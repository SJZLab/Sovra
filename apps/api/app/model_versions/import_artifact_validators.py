"""Pure import-artifact validators (FTF-H010-P3a, Section 6.7/6.17/17/34/58A/68.1).

The file-level half of FTF-H010-P3's weights / adapter import validation,
split out so it can be proven without a database, a migration, a GPU or
any artifact bytes beyond small fixtures. Every function takes exact
types (`bytes`, `str`, `list`/`tuple`, the module's own frozen results)
and either returns a typed result or raises
`ImportArtifactValidationError` carrying the P3 failure code. Nothing
here reads a file, imports an artifact, or deserializes anything but
JSON: the stdlib `json` module parses strict, size- and depth-bounded
text, and `struct` reads the 8-byte safetensors length prefix. It never
imports torch, safetensors or transformers, and never executes or
imports an artifact file (Section 17: "do not deserialize arbitrary
uploaded pickle").

Steps implemented (numbers follow FTF-H010-P3 in QUEUE.md):

* 4  `check_file_names` -- an exact allow-list (never a deny-list) per
  kind. Pickle-capable formats get their own code, `unsafe_weight_format`.
* 6  `validate_config_json`, `validate_tokenizer_config_json` -- strict
  JSON (duplicate keys, NaN/Infinity, depth, size, UTF-8 and surrogates
  all refused), field rules, `chat_template_sha256`, and a present
  `quantization_config` is `quantization_unverified`.
* 7  `parse_safetensors_header` -- the 8-byte length bound, a strict JSON
  object, per-tensor dtype/shape/offset rules, the size equation, and
  contiguous exact coverage of the data buffer.
* 8 (pure half)  `validate_adapter_config_json`,
  `check_adapter_base_compatibility` -- `peft_type == "LORA"`, and the
  lora_A/lora_B <-> base `{module}.weight` shape check. The adapter's
  `base_model_name_or_path` is informational: it is parsed and returned
  but never used to find or judge the base.

Failure codes (the module's own error carries one of these):
`unexpected_file`, `unsafe_weight_format`, `required_file_missing`,
`config_invalid`, `tokenizer_config_invalid`, `adapter_config_invalid`,
`quantization_unverified`, `weights_header_invalid`,
`adapter_base_incompatible`, and `validator_input_invalid` for a caller
that passes an unknown `kind`. The step-6 and adapter-config codes are
this slice's own naming (the P3 text names only `quantization_unverified`
for step 6); P3 reuses them unchanged.

Error text is a code plus a static reason chosen from this module's own
literals -- never a file name, a field value or a parsed fragment -- and
every raise happens outside the `except` block that caught the parser's
exception, so no exception chain can carry artifact content (META-LEAK).

UNWIRED: nothing calls this module yet; FTF-H010-P3 wires it into the P2
activity (disclosed in the submission).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

__all__ = [
    "AdapterConfig",
    "ImportArtifactValidationError",
    "KIND_ADAPTER",
    "KIND_WEIGHTS",
    "MAX_SAFETENSORS_HEADER_BYTES",
    "ModelConfig",
    "SafetensorsHeader",
    "TensorInfo",
    "TokenizerConfig",
    "base_shapes_from_headers",
    "check_adapter_base_compatibility",
    "check_file_names",
    "parse_safetensors_header",
    "validate_adapter_config_json",
    "validate_config_json",
    "validate_tokenizer_config_json",
]

KIND_WEIGHTS = "weights"
KIND_ADAPTER = "adapter"

CODE_UNEXPECTED_FILE = "unexpected_file"
CODE_UNSAFE_WEIGHT_FORMAT = "unsafe_weight_format"
CODE_REQUIRED_FILE_MISSING = "required_file_missing"
CODE_CONFIG_INVALID = "config_invalid"
CODE_TOKENIZER_CONFIG_INVALID = "tokenizer_config_invalid"
CODE_ADAPTER_CONFIG_INVALID = "adapter_config_invalid"
CODE_QUANTIZATION_UNVERIFIED = "quantization_unverified"
CODE_WEIGHTS_HEADER_INVALID = "weights_header_invalid"
CODE_ADAPTER_BASE_INCOMPATIBLE = "adapter_base_incompatible"
CODE_VALIDATOR_INPUT_INVALID = "validator_input_invalid"

# The safetensors format's own limit on the JSON header.
MAX_SAFETENSORS_HEADER_BYTES = 100_000_000
CONFIG_MAX_BYTES = 1 << 20  # config.json, adapter_config.json: 1 MiB
TOKENIZER_CONFIG_MAX_BYTES = 4 << 20  # tokenizer_config.json: 4 MiB
JSON_MAX_DEPTH = 32
SAFETENSORS_HEADER_MAX_DEPTH = 3  # {tensor: {shape: [dim]}} is the deepest valid header

_PICKLE_EXTENSIONS = frozenset(
    {"bin", "pt", "pth", "ckpt", "pkl", "pickle", "joblib", "npy", "npz"}
)

_TOKENIZER_FILES = frozenset(
    {
        "tokenizer_config.json",
        "tokenizer.json",
        "tokenizer.model",
        "special_tokens_map.json",
        "added_tokens.json",
        "vocab.json",
        "merges.txt",
        "chat_template.jinja",
    }
)
_DOC_FILES = frozenset({"README.md", "LICENSE"})
_WEIGHTS_NAMED_FILES = (
    _TOKENIZER_FILES
    | _DOC_FILES
    | frozenset({"config.json", "model.safetensors.index.json", "generation_config.json"})
)
_ADAPTER_NAMED_FILES = _TOKENIZER_FILES | _DOC_FILES | frozenset({"adapter_config.json"})
_ADAPTER_WEIGHTS_FILE = "adapter_model.safetensors"
# `*.safetensors` shards for a weights set: a single plain stem, no dots (so no
# double extension), and never the adapter file's own name.
_WEIGHTS_SHARD = re.compile(r"[A-Za-z0-9_-]{1,128}\.safetensors")
_WEIGHTS_REQUIRED = frozenset({"config.json", "tokenizer_config.json"})
_TOKENIZER_REQUIRED_ONE_OF = frozenset({"tokenizer.json", "tokenizer.model"})
_ADAPTER_REQUIRED = frozenset({"adapter_config.json", _ADAPTER_WEIGHTS_FILE})

_TORCH_DTYPES = frozenset({"float32", "float16", "bfloat16", "float64"})

_SPECIAL_TOKEN_KEYS = (
    "bos_token",
    "cls_token",
    "eos_token",
    "mask_token",
    "pad_token",
    "sep_token",
    "unk_token",
)

_SAFETENSORS_DTYPE_SIZES: Mapping[str, int] = {
    "F64": 8,
    "F32": 4,
    "F16": 2,
    "BF16": 2,
    "I64": 8,
    "I32": 4,
    "I16": 2,
    "I8": 1,
    "U8": 1,
    "BOOL": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
}
_TENSOR_KEYS = frozenset({"dtype", "shape", "data_offsets"})

_LORA_KEY = re.compile(r"(.+)\.lora_([AB])\.weight")
_PEFT_PREFIX = "base_model.model."
_SURROGATES = re.compile("[\ud800-\udfff]")


class ImportArtifactValidationError(ValueError):
    """A validator refused its input. `code` is the P3 failure code; the
    message is `<code>: <static reason>` and never contains input."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class ModelConfig:
    model_type: str
    architectures: tuple[str, ...]
    hidden_size: int
    num_hidden_layers: int
    vocab_size: int
    torch_dtype: str | None


@dataclass(frozen=True)
class TokenizerConfig:
    chat_template_sha256: str | None
    special_tokens: tuple[tuple[str, str], ...]
    additional_special_tokens: tuple[str, ...]


@dataclass(frozen=True)
class AdapterConfig:
    r: int
    target_modules: tuple[str, ...]
    base_model_name_or_path: str | None


@dataclass(frozen=True)
class TensorInfo:
    dtype: str
    shape: tuple[int, ...]
    begin: int
    end: int


@dataclass(frozen=True)
class SafetensorsHeader:
    tensors: tuple[tuple[str, TensorInfo], ...]
    metadata: tuple[tuple[str, str], ...]
    header_size: int
    data_size: int

    def shapes(self) -> dict[str, tuple[int, ...]]:
        return {name: info.shape for name, info in self.tensors}


# -- strict JSON -----------------------------------------------------------------------


class _Reject(Exception):
    """Raised by the JSON hooks; carries only a static reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise _Reject("duplicate_key")
        out[key] = value
    return out


def _reject_constant(_name: str) -> object:
    raise _Reject("non_finite_number")


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _Reject("non_finite_number")
    return value


def _walk_limits(value: object, max_depth: int) -> str | None:
    """None when every container is within `max_depth` and no string holds a
    lone surrogate; otherwise a static reason. Iterative (no recursion)."""
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            if depth + 1 > max_depth:
                return "too_deep"
            for key, child in item.items():
                if _SURROGATES.search(key) is not None:
                    return "invalid_text"
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if depth + 1 > max_depth:
                return "too_deep"
            for child in item:
                stack.append((child, depth + 1))
        elif isinstance(item, str) and _SURROGATES.search(item) is not None:
            return "invalid_text"
    return None


def _strict_loads(data: object, max_bytes: int, max_depth: int) -> tuple[object, str | None]:
    """(value, None) on success, (None, static reason) on refusal. Never raises."""
    if type(data) is not bytes:
        return None, "input_type"
    if len(data) > max_bytes:
        return None, "too_large"
    reason: str | None = None
    value: object = None
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
        )
    except _Reject as exc:
        reason = exc.reason
    except RecursionError:
        reason = "too_deep"
    except ValueError:
        reason = "malformed_json"
    if reason is not None:
        return None, reason
    limit_reason = _walk_limits(value, max_depth)
    if limit_reason is not None:
        return None, limit_reason
    return value, None


def _load_object(data: object, max_bytes: int, max_depth: int, code: str) -> dict[str, object]:
    value, reason = _strict_loads(data, max_bytes, max_depth)
    if reason is not None:
        raise ImportArtifactValidationError(code, reason)
    if type(value) is not dict:
        raise ImportArtifactValidationError(code, "not_an_object")
    return value


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _nonempty_str(value: object) -> bool:
    return type(value) is str and len(value) > 0


# -- step 4: file allow-list ----------------------------------------------------------------


def _is_pickle_capable(name: str) -> bool:
    return any(part in _PICKLE_EXTENSIONS for part in name.lower().split(".")[1:])


def _exact_str_list(value: object) -> list[str] | None:
    """`value` as a list when it is exactly a list/tuple of exact `str`."""
    if type(value) not in (list, tuple):
        return None
    items = list(cast("list[object]", value))
    if any(type(item) is not str for item in items):
        return None
    return cast("list[str]", items)


def check_file_names(names: object, *, kind: object) -> tuple[str, ...]:
    """Exact allow-list over a manifest's file names for `kind`
    ('weights' | 'adapter'). Order of precedence when several problems exist:
    `unsafe_weight_format`, then `unexpected_file`, then `required_file_missing`.
    Returns the validated names."""
    if type(kind) is not str or kind not in (KIND_WEIGHTS, KIND_ADAPTER):
        raise ImportArtifactValidationError(CODE_VALIDATOR_INPUT_INVALID, "unknown_kind")
    items = _exact_str_list(names)
    if items is None:
        raise ImportArtifactValidationError(CODE_UNEXPECTED_FILE, "names_not_a_list_of_str")
    if kind == KIND_WEIGHTS:
        named = _WEIGHTS_NAMED_FILES
    else:
        named = _ADAPTER_NAMED_FILES | {_ADAPTER_WEIGHTS_FILE}

    unsafe = False
    unexpected = False
    seen: set[str] = set()
    for name in items:
        if name in seen:
            unexpected = True
            continue
        seen.add(name)
        if name in named:
            continue
        if (
            kind == KIND_WEIGHTS
            and _WEIGHTS_SHARD.fullmatch(name) is not None
            and name != _ADAPTER_WEIGHTS_FILE
        ):
            continue
        if _is_pickle_capable(name):
            unsafe = True
        else:
            unexpected = True
    if unsafe:
        raise ImportArtifactValidationError(CODE_UNSAFE_WEIGHT_FORMAT, "pickle_capable_file")
    if unexpected:
        raise ImportArtifactValidationError(CODE_UNEXPECTED_FILE, "not_on_the_allow_list")

    if kind == KIND_WEIGHTS:
        has_shard = any(
            _WEIGHTS_SHARD.fullmatch(name) is not None and name != _ADAPTER_WEIGHTS_FILE
            for name in seen
        )
        complete = (
            _WEIGHTS_REQUIRED <= seen and bool(_TOKENIZER_REQUIRED_ONE_OF & seen) and has_shard
        )
    else:
        complete = _ADAPTER_REQUIRED <= seen
    if not complete:
        raise ImportArtifactValidationError(CODE_REQUIRED_FILE_MISSING, "required_file_absent")
    return tuple(items)


# -- step 6: config.json / tokenizer_config.json ----------------------------------------------


def validate_config_json(data: object) -> ModelConfig:
    obj = _load_object(data, CONFIG_MAX_BYTES, JSON_MAX_DEPTH, CODE_CONFIG_INVALID)
    model_type = obj.get("model_type")
    architectures = obj.get("architectures")
    torch_dtype = obj.get("torch_dtype")
    if not _nonempty_str(model_type):
        raise ImportArtifactValidationError(CODE_CONFIG_INVALID, "model_type")
    if (
        type(architectures) is not list
        or len(architectures) == 0
        or not all(_nonempty_str(a) for a in architectures)
    ):
        raise ImportArtifactValidationError(CODE_CONFIG_INVALID, "architectures")
    for field in ("hidden_size", "num_hidden_layers", "vocab_size"):
        if not _positive_int(obj.get(field)):
            raise ImportArtifactValidationError(CODE_CONFIG_INVALID, field)
    if "torch_dtype" in obj and (type(torch_dtype) is not str or torch_dtype not in _TORCH_DTYPES):
        raise ImportArtifactValidationError(CODE_CONFIG_INVALID, "torch_dtype")
    if "quantization_config" in obj:
        raise ImportArtifactValidationError(
            CODE_QUANTIZATION_UNVERIFIED, "quantization_config_present"
        )
    return ModelConfig(
        model_type=str(model_type),
        architectures=tuple(str(a) for a in architectures),
        hidden_size=int(obj["hidden_size"]),  # type: ignore[call-overload]
        num_hidden_layers=int(obj["num_hidden_layers"]),  # type: ignore[call-overload]
        vocab_size=int(obj["vocab_size"]),  # type: ignore[call-overload]
        torch_dtype=None if "torch_dtype" not in obj else str(torch_dtype),
    )


def _token_content(value: object) -> str | None:
    """A special token is a str or `{content: str, ...}`; anything else is None."""
    if type(value) is str:
        return value
    if type(value) is dict:
        content = value.get("content")
        if type(content) is str:
            return content
    return None


def validate_tokenizer_config_json(data: object) -> TokenizerConfig:
    obj = _load_object(
        data, TOKENIZER_CONFIG_MAX_BYTES, JSON_MAX_DEPTH, CODE_TOKENIZER_CONFIG_INVALID
    )
    template = obj.get("chat_template")
    template_sha256: str | None = None
    if template is not None:
        if type(template) is not str:
            raise ImportArtifactValidationError(CODE_TOKENIZER_CONFIG_INVALID, "chat_template")
        template_sha256 = hashlib.sha256(template.encode("utf-8")).hexdigest()
    tokens: list[tuple[str, str]] = []
    for key in _SPECIAL_TOKEN_KEYS:
        raw = obj.get(key)
        if raw is None:
            continue
        content = _token_content(raw)
        if content is None:
            raise ImportArtifactValidationError(CODE_TOKENIZER_CONFIG_INVALID, "special_token")
        tokens.append((key, content))
    additional: list[str] = []
    raw_additional = obj.get("additional_special_tokens")
    if raw_additional is not None:
        if type(raw_additional) is not list:
            raise ImportArtifactValidationError(
                CODE_TOKENIZER_CONFIG_INVALID, "additional_special_tokens"
            )
        for item in raw_additional:
            content = _token_content(item)
            if content is None:
                raise ImportArtifactValidationError(
                    CODE_TOKENIZER_CONFIG_INVALID, "additional_special_tokens"
                )
            additional.append(content)
    return TokenizerConfig(
        chat_template_sha256=template_sha256,
        special_tokens=tuple(tokens),
        additional_special_tokens=tuple(additional),
    )


# -- step 7: safetensors header ----------------------------------------------------------------


def _header_error(reason: str) -> ImportArtifactValidationError:
    return ImportArtifactValidationError(CODE_WEIGHTS_HEADER_INVALID, reason)


def parse_safetensors_header(prefix: object, *, file_size: object) -> SafetensorsHeader:
    """`prefix` is the file's leading bytes: at least the 8-byte length and the
    N header bytes (the data buffer is not needed); `file_size` is the file's
    real size, checked against N and against the offsets' exact coverage."""
    if type(prefix) is not bytes:
        raise _header_error("prefix_type")
    if type(file_size) is not int or file_size < 8:
        raise _header_error("file_size")
    if len(prefix) < 8:
        raise _header_error("prefix_too_short")
    (n,) = struct.unpack("<Q", prefix[:8])
    if n > MAX_SAFETENSORS_HEADER_BYTES:
        raise _header_error("header_size_out_of_bounds")
    if n > file_size - 8:
        raise _header_error("header_larger_than_file")
    if len(prefix) < 8 + n:
        raise _header_error("prefix_too_short")
    raw = prefix[8 : 8 + n]
    if raw[:1] != b"{":
        raise _header_error("header_not_an_object")
    header, reason = _strict_loads(raw, n, SAFETENSORS_HEADER_MAX_DEPTH)
    if reason is not None:
        raise _header_error(reason)
    if type(header) is not dict:
        raise _header_error("header_not_an_object")

    data_size = file_size - 8 - n
    metadata: tuple[tuple[str, str], ...] = ()
    tensors: list[tuple[str, TensorInfo]] = []
    for name, entry in header.items():
        if name == "__metadata__":
            metadata = _metadata_pairs(entry)
            continue
        tensors.append((name, _tensor_info(name, entry)))
    if not tensors:
        raise _header_error("no_tensors")
    tensors.sort(key=lambda item: (item[1].begin, item[1].end, item[0]))
    cursor = 0
    for _name, info in tensors:
        if info.begin != cursor:
            raise _header_error("offsets_not_contiguous")
        cursor = info.end
    if cursor != data_size:
        raise _header_error("offsets_do_not_cover_the_buffer")
    return SafetensorsHeader(
        tensors=tuple(tensors),
        metadata=metadata,
        header_size=n,
        data_size=data_size,
    )


def _metadata_pairs(entry: object) -> tuple[tuple[str, str], ...]:
    if type(entry) is not dict:
        raise _header_error("metadata_not_an_object")
    for value in entry.values():
        if type(value) is not str:
            raise _header_error("metadata_value_not_a_string")
    return tuple((str(k), str(v)) for k, v in entry.items())


def _tensor_info(name: str, entry: object) -> TensorInfo:
    if len(name) == 0:
        raise _header_error("tensor_name")
    if type(entry) is not dict or set(entry) != _TENSOR_KEYS:
        raise _header_error("tensor_entry")
    dtype = entry["dtype"]
    shape = entry["shape"]
    offsets = entry["data_offsets"]
    if type(dtype) is not str or dtype not in _SAFETENSORS_DTYPE_SIZES:
        raise _header_error("dtype")
    if type(shape) is not list or any(type(d) is not int or d < 0 for d in shape):
        raise _header_error("shape")
    if type(offsets) is not list or len(offsets) != 2 or any(type(o) is not int for o in offsets):
        raise _header_error("data_offsets")
    expected = _SAFETENSORS_DTYPE_SIZES[dtype]
    for dim in shape:
        expected *= dim
    if offsets[1] - offsets[0] != expected:
        raise _header_error("size_mismatch")
    return TensorInfo(dtype=dtype, shape=tuple(shape), begin=offsets[0], end=offsets[1])


# -- step 8 (pure half): adapter ------------------------------------------------------------------


def validate_adapter_config_json(data: object) -> AdapterConfig:
    obj = _load_object(data, CONFIG_MAX_BYTES, JSON_MAX_DEPTH, CODE_ADAPTER_CONFIG_INVALID)
    peft_type = obj.get("peft_type")
    target_modules = obj.get("target_modules")
    base_name = obj.get("base_model_name_or_path")
    if type(peft_type) is not str or peft_type != "LORA":
        raise ImportArtifactValidationError(CODE_ADAPTER_CONFIG_INVALID, "peft_type")
    if not _positive_int(obj.get("r")):
        raise ImportArtifactValidationError(CODE_ADAPTER_CONFIG_INVALID, "r")
    if (
        type(target_modules) is not list
        or len(target_modules) == 0
        or not all(_nonempty_str(t) for t in target_modules)
    ):
        raise ImportArtifactValidationError(CODE_ADAPTER_CONFIG_INVALID, "target_modules")
    if base_name is not None and type(base_name) is not str:
        raise ImportArtifactValidationError(CODE_ADAPTER_CONFIG_INVALID, "base_model_name_or_path")
    return AdapterConfig(
        r=int(obj["r"]),  # type: ignore[call-overload]
        target_modules=tuple(str(t) for t in target_modules),
        base_model_name_or_path=None if base_name is None else str(base_name),
    )


def base_shapes_from_headers(headers: object) -> dict[str, tuple[int, ...]]:
    """Merge the base's shard headers into one tensor-name -> shape map."""
    if type(headers) not in (list, tuple):
        raise _header_error("shards_not_a_list")
    merged: dict[str, tuple[int, ...]] = {}
    for header in cast("list[object]", headers):
        if type(header) is not SafetensorsHeader:
            raise _header_error("shard_type")
        for name, info in header.tensors:
            if name in merged:
                raise _header_error("tensor_in_two_shards")
            merged[name] = info.shape
    return merged


def _incompatible(reason: str) -> ImportArtifactValidationError:
    return ImportArtifactValidationError(CODE_ADAPTER_BASE_INCOMPATIBLE, reason)


def _as_shape_map(value: object) -> dict[str, tuple[int, ...]] | None:
    if type(value) is not dict:
        return None
    for key, shape in value.items():
        if type(key) is not str or type(shape) is not tuple:
            return None
        if any(type(d) is not int or d < 0 for d in shape):
            return None
    return value


def _target_matches(module: str, target: str) -> bool:
    # PEFT's own rule: the full module path, or a dot-delimited suffix of it.
    return module == target or module.endswith("." + target)


def check_adapter_base_compatibility(adapter: object, config: object, base_shapes: object) -> None:
    """Every adapter tensor must be a `<module>.lora_A.weight` /
    `<module>.lora_B.weight` pair for a module the config targets, with
    A = [r, in], B = [out, r], r equal to the config's r, and the base must
    hold `<module>.weight` with shape [out, in]. The base is identified only
    by `base_shapes`, never by any name in the adapter's config."""
    if type(adapter) is not SafetensorsHeader or type(config) is not AdapterConfig:
        raise _incompatible("input_type")
    shapes = _as_shape_map(base_shapes)
    if shapes is None:
        raise _incompatible("base_shapes_type")

    pairs: dict[str, dict[str, tuple[int, ...]]] = {}
    for name, info in adapter.tensors:
        key = name[len(_PEFT_PREFIX) :] if name.startswith(_PEFT_PREFIX) else name
        match = _LORA_KEY.fullmatch(key)
        if match is None:
            raise _incompatible("not_a_lora_tensor")
        module, side = match.group(1), match.group(2)
        slot = pairs.setdefault(module, {})
        if side in slot:
            raise _incompatible("duplicate_lora_tensor")
        slot[side] = info.shape
    if not pairs:
        raise _incompatible("no_lora_tensors")

    for module, slot in pairs.items():
        if "A" not in slot or "B" not in slot:
            raise _incompatible("unpaired_lora_tensor")
        shape_a, shape_b = slot["A"], slot["B"]
        if len(shape_a) != 2 or len(shape_b) != 2:
            raise _incompatible("lora_rank")
        if shape_a[0] != config.r or shape_b[1] != config.r:
            raise _incompatible("r_mismatch")
        if not any(_target_matches(module, t) for t in config.target_modules):
            raise _incompatible("module_not_targeted")
        base_shape = shapes.get(module + ".weight")
        if base_shape is None:
            raise _incompatible("base_tensor_missing")
        if base_shape != (shape_b[0], shape_a[1]):
            raise _incompatible("shape_mismatch")
    for target in config.target_modules:
        if not any(_target_matches(module, target) for module in pairs):
            raise _incompatible("target_without_tensors")
