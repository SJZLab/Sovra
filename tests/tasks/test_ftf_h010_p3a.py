#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,var-annotated,misc"
# ruff: noqa: E501
"""FTF-H010-P3a -- pure import-artifact validators (no DB, no network, no weights).

Run directly: python3 tests/tasks/test_ftf_h010_p3a.py

Criteria (QUEUE Backlog, FTF-H010-P3a; P3 steps 4, 6, 7 and the pure half of 8):
  A1  file allow-list: the exact weights / adapter file sets are accepted
  A2  file allow-list: anything else (case, double extension, hidden, nested,
      separators, look-alikes, duplicates) is `unexpected_file`
  A3  pickle-capable formats (any case, any extension position) are
      `unsafe_weight_format`, and win over `unexpected_file`
  A4  required files missing -> `required_file_missing`
  A5  exact input types (no str subclass, no generator, no caller object)
  B1  strict JSON: duplicate keys, NaN/Infinity, depth, size, UTF-8, BOM,
      surrogates, non-object, trailing garbage, exact `bytes` input
  B2  config.json field rules and `quantization_config` -> `quantization_unverified`
  C1  tokenizer_config.json: chat_template_sha256 over the exact UTF-8 bytes,
      special-token shapes
  D1  safetensors header: N bounds, JSON, dtype / shape / offsets, size equation,
      contiguous exact coverage, `__metadata__`
  E1  adapter_config.json rules (`peft_type == "LORA"`, r, target_modules)
  E2  adapter <-> base shape compatibility (lora_A/lora_B vs `{module}.weight`)
  F1  META-LEAK: error text, args, chain and traceback carry codes only
  G1  purity: stdlib only, no torch/safetensors/transformers, no file/pickle/exec
  H1  every fault in FAULTS turns the suite RED (fault-injected module copies)
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import itertools
import json
import struct
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

MODULE_PATH = ROOT / "apps" / "api" / "app" / "model_versions" / "import_artifact_validators.py"
CID = "FTF-H010-P3a"

_failures: list[str] = []


CHECK_COUNT = [0]


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECK_COUNT[0] += 1
    if not condition:
        _failures.append(f"FAIL [{criterion_id}] {description}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# -- helpers --------------------------------------------------------------------


def outcome(m: Any, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """'ok' | the module error's code | 'LEAK:<ExceptionType>' for any other exception.
    A module error that carries an exception context/cause is also a LEAK."""
    try:
        fn(*args, **kwargs)
    except m.ImportArtifactValidationError as exc:
        if exc.__context__ is not None or exc.__cause__ is not None:
            return "LEAK:chained"
        return str(exc.code)
    except BaseException as exc:  # noqa: BLE001 - the point is to catch non-module errors
        return f"LEAK:{type(exc).__name__}"
    return "ok"


def reason_of(m: Any, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """The module error's static reason, or '' when it did not refuse."""
    try:
        fn(*args, **kwargs)
    except m.ImportArtifactValidationError as exc:
        return str(exc.reason)
    except BaseException:  # noqa: BLE001
        return "LEAK"
    return ""


def refuses(m: Any, code: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    return outcome(m, fn, *args, **kwargs) == code


DTYPE_SIZE = {
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


def st_file(
    tensors: dict[str, tuple[str, list[int]]],
    metadata: dict[str, Any] | None = None,
    pad: int = 0,
) -> tuple[bytes, int]:
    """A valid safetensors file (header + zero buffer); returns (bytes, file_size)."""
    header: dict[str, Any] = {}
    if metadata is not None:
        header["__metadata__"] = metadata
    offset = 0
    for name, (dtype, shape) in tensors.items():
        n = DTYPE_SIZE[dtype]
        for dim in shape:
            n *= dim
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + n]}
        offset += n
    raw = json.dumps(header, separators=(",", ":")).encode() + b" " * pad
    blob = struct.pack("<Q", len(raw)) + raw + b"\x00" * offset
    return blob, len(blob)


def st_raw(header_bytes: bytes, buffer_len: int) -> tuple[bytes, int]:
    blob = struct.pack("<Q", len(header_bytes)) + header_bytes + b"\x00" * buffer_len
    return blob, len(blob)


def st_tensor_header(entries: dict[str, Any], buffer_len: int) -> tuple[bytes, int]:
    return st_raw(json.dumps(entries, separators=(",", ":")).encode(), buffer_len)


def parse(m: Any, blob_and_size: tuple[bytes, int]) -> Any:
    blob, size = blob_and_size
    return m.parse_safetensors_header(blob, file_size=size)


GOOD_CONFIG: dict[str, Any] = {
    "model_type": "llama",
    "architectures": ["LlamaForCausalLM"],
    "hidden_size": 64,
    "num_hidden_layers": 2,
    "vocab_size": 128,
    "torch_dtype": "bfloat16",
}


def jb(value: Any) -> bytes:
    return json.dumps(value).encode()


WEIGHTS_FILES = [
    "config.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    "model.safetensors.index.json",
    "generation_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.json",
    "merges.txt",
    "chat_template.jinja",
    "README.md",
    "LICENSE",
]
ADAPTER_FILES = ["adapter_config.json", "adapter_model.safetensors"]
PICKLE_EXTS = ["bin", "pt", "pth", "ckpt", "pkl", "pickle", "joblib", "npy"]


class StrSub(str):
    pass


class BytesSub(bytes):
    pass


# -- A: file allow-list -----------------------------------------------------------


def group_a(m: Any) -> None:
    f = m.check_file_names
    check("A1", "full weights set accepted", outcome(m, f, WEIGHTS_FILES, kind="weights") == "ok")
    check("A1", "tuple input accepted", outcome(m, f, tuple(WEIGHTS_FILES), kind="weights") == "ok")
    minimal = ["config.json", "tokenizer_config.json", "tokenizer.model", "model.safetensors"]
    check(
        "A1",
        "minimal weights set (tokenizer.model, single shard)",
        outcome(m, f, minimal, kind="weights") == "ok",
    )
    check("A1", "adapter set accepted", outcome(m, f, ADAPTER_FILES, kind="adapter") == "ok")
    adapter_full = [
        *ADAPTER_FILES,
        "tokenizer_config.json",
        "tokenizer.json",
        "README.md",
        "LICENSE",
    ]
    check(
        "A1",
        "adapter with tokenizer files and docs accepted",
        outcome(m, f, adapter_full, kind="adapter") == "ok",
    )
    result = f(minimal, kind="weights")
    check(
        "A1",
        "returns the validated names as a tuple",
        result == tuple(minimal) and type(result) is tuple,
    )

    # A2: generated look-alikes of every allowed name
    allowed_all = [*WEIGHTS_FILES, *ADAPTER_FILES]
    bad: list[str] = []
    for name in allowed_all:
        bad += [
            name.upper(),
            name.swapcase(),
            name.title(),
            name + "\n",
            name + " ",
            " " + name,
            name + "\x00",
        ]
        bad += [
            "./" + name,
            "sub/" + name,
            "../" + name,
            "/" + name,
            name + "/",
            "." + name,
            name.replace(".", "․"),
        ]
        bad += [
            name + ".json",
            name + ".bak",
            name + ".tmp",
            name.replace(".", "..", 1),
            name + "\\x",
            "a\\" + name,
        ]
        bad += [name.replace("o", "ο"), name.replace("i", "ı")]
    bad += [
        "",
        ".",
        "..",
        ".hidden",
        ".git/config",
        "evil.sh",
        "run.py",
        "model",
        "safetensors",
        ".safetensors",
    ]
    bad += [
        "a.b.safetensors",
        "x" * 129 + ".safetensors",
        "model.safetensors.index.json.bak",
        "tokenizer.model.v3",
    ]
    # a confusable name must not be accepted as a different file in the same kind
    for name in bad:
        if name in WEIGHTS_FILES or name in ADAPTER_FILES:
            continue
        got = outcome(m, f, [*minimal, name], kind="weights")
        check(
            "A2",
            f"weights set + look-alike {name!r:.40} -> unexpected_file/unsafe_weight_format (got {got})",
            got in ("unexpected_file", "unsafe_weight_format"),
        )
        got_a = outcome(m, f, [*ADAPTER_FILES, name], kind="adapter")
        check(
            "A2",
            f"adapter set + look-alike {name!r:.40} refused (got {got_a})",
            got_a in ("unexpected_file", "unsafe_weight_format"),
        )
    plain_bad = [
        "evil.sh",
        "run.py",
        ".hidden",
        "sub/config.json",
        "CONFIG.JSON",
        "config.json\n",
        "a.b.safetensors",
    ]
    for name in plain_bad:
        check(
            "A2",
            f"{name!r} is exactly unexpected_file",
            refuses(m, "unexpected_file", f, [*minimal, name], kind="weights"),
        )
    check(
        "A2",
        "duplicate name is unexpected_file",
        refuses(m, "unexpected_file", f, [*minimal, "config.json"], kind="weights"),
    )
    check(
        "A2",
        "adapter_model.safetensors is not a weights-kind shard",
        refuses(m, "unexpected_file", f, [*minimal, "adapter_model.safetensors"], kind="weights"),
    )
    check(
        "A2",
        "config.json is not allowed in an adapter set",
        refuses(m, "unexpected_file", f, [*ADAPTER_FILES, "config.json"], kind="adapter"),
    )
    check(
        "A2",
        "an adapter shard other than adapter_model is refused",
        refuses(m, "unexpected_file", f, [*ADAPTER_FILES, "model.safetensors"], kind="adapter"),
    )
    check(
        "A2",
        "adapter_config.json is not allowed in a weights set",
        refuses(m, "unexpected_file", f, [*minimal, "adapter_config.json"], kind="weights"),
    )
    check(
        "A2",
        "index file is not allowed in an adapter set",
        refuses(
            m,
            "unexpected_file",
            f,
            [*ADAPTER_FILES, "model.safetensors.index.json"],
            kind="adapter",
        ),
    )
    check(
        "A2",
        "generation_config.json is not allowed in an adapter set",
        refuses(
            m, "unexpected_file", f, [*ADAPTER_FILES, "generation_config.json"], kind="adapter"
        ),
    )

    # A3: pickle-capable formats
    pickle_names: list[str] = []
    for ext in [*PICKLE_EXTS, "npz"]:
        for variant in (ext, ext.upper(), ext.title()):
            pickle_names += [
                f"pytorch_model.{variant}",
                f"model.{variant}",
                f"a.{variant}.safetensors",
                f"model.safetensors.{variant}",
                f"x.{variant}.json",
            ]
    for name in pickle_names:
        check(
            "A3",
            f"{name!r} is unsafe_weight_format",
            refuses(m, "unsafe_weight_format", f, [*minimal, name], kind="weights"),
        )
        check(
            "A3",
            f"{name!r} is unsafe_weight_format in an adapter set",
            refuses(m, "unsafe_weight_format", f, [*ADAPTER_FILES, name], kind="adapter"),
        )
    check(
        "A3",
        "unsafe wins over unexpected regardless of order",
        refuses(
            m, "unsafe_weight_format", f, ["evil.sh", "pytorch_model.bin", *minimal], kind="weights"
        )
        and refuses(
            m, "unsafe_weight_format", f, ["pytorch_model.bin", "evil.sh", *minimal], kind="weights"
        ),
    )
    check(
        "A3",
        "unsafe wins over a missing required file",
        refuses(m, "unsafe_weight_format", f, ["model.bin"], kind="weights"),
    )
    check(
        "A3",
        "unexpected wins over a missing required file",
        refuses(m, "unexpected_file", f, ["evil.sh"], kind="weights"),
    )

    # A4: required files
    for drop in ("config.json", "tokenizer_config.json"):
        rest = [n for n in minimal if n != drop]
        check(
            "A4",
            f"missing {drop} -> required_file_missing",
            refuses(m, "required_file_missing", f, rest, kind="weights"),
        )
    no_tok = ["config.json", "tokenizer_config.json", "model.safetensors"]
    check(
        "A4",
        "neither tokenizer.json nor tokenizer.model -> required_file_missing",
        refuses(m, "required_file_missing", f, no_tok, kind="weights"),
    )
    check(
        "A4",
        "tokenizer.json alone satisfies the tokenizer requirement",
        outcome(m, f, [*no_tok, "tokenizer.json"], kind="weights") == "ok",
    )
    check(
        "A4",
        "no safetensors shard -> required_file_missing",
        refuses(
            m,
            "required_file_missing",
            f,
            [
                "config.json",
                "tokenizer_config.json",
                "tokenizer.json",
                "model.safetensors.index.json",
            ],
            kind="weights",
        ),
    )
    check(
        "A4",
        "empty list -> required_file_missing",
        refuses(m, "required_file_missing", f, [], kind="weights"),
    )
    check(
        "A4",
        "adapter without adapter_config.json",
        refuses(m, "required_file_missing", f, ["adapter_model.safetensors"], kind="adapter"),
    )
    check(
        "A4",
        "adapter without adapter_model.safetensors",
        refuses(m, "required_file_missing", f, ["adapter_config.json"], kind="adapter"),
    )

    # A5: exact input types
    for label, value in [
        ("str", "config.json"),
        ("bytes", b"config.json"),
        ("None", None),
        ("int", 5),
        ("set", set(minimal)),
        ("dict", dict.fromkeys(minimal)),
        ("generator", (n for n in minimal)),
        ("list-of-bytes", [b"config.json"]),
        ("list-with-None", [*minimal, None]),
        ("list-with-int", [*minimal, 7]),
        ("list-with-list", [*minimal, ["x"]]),
        ("str-subclass-name", [*minimal[:-1], StrSub("model.safetensors")]),
        ("unhashable-name", [*minimal, {}]),
    ]:
        got = outcome(m, f, value, kind="weights")
        check(
            "A5",
            f"{label} input is refused by the module's own error (got {got})",
            got == "unexpected_file",
        )
    for kind in (
        " weights",
        "weights ",
        "Weights",
        "WEIGHTS",
        "weights\n",
        " adapter",
        "ADAPTER",
        "Adapter\n",
        "",
        "weight",
    ):
        check(
            "A5",
            f"kind {kind!r} is not normalised",
            refuses(m, "validator_input_invalid", f, minimal, kind=kind),
        )
    check(
        "A5",
        "an unknown kind is refused by the module's own error",
        refuses(m, "validator_input_invalid", f, minimal, kind="other"),
    )
    check(
        "A5",
        "a non-str kind is refused by the module's own error",
        refuses(m, "validator_input_invalid", f, minimal, kind=["weights"]),
    )
    check(
        "A5",
        "a str-subclass kind is refused",
        refuses(m, "validator_input_invalid", f, minimal, kind=StrSub("weights")),
    )


# -- B/C: strict JSON, config, tokenizer config --------------------------------------


JSON_REASONS = {
    "dup-key top": "duplicate_key",
    "dup-key same value": "duplicate_key",
    "dup-key nested": "duplicate_key",
    "NaN": "non_finite_number",
    "Infinity": "non_finite_number",
    "-Infinity": "non_finite_number",
    "1e999": "non_finite_number",
    "-1e999": "non_finite_number",
    "trailing garbage": "malformed_json",
    "trailing comma": "malformed_json",
    "two documents": "malformed_json",
    "empty": "malformed_json",
    "whitespace": "malformed_json",
    "bad utf8": "malformed_json",
    "overlong utf8": "malformed_json",
    "BOM": "malformed_json",
    "lone surrogate escape": "invalid_text",
    "lone low surrogate escape in key": "invalid_text",
    "list top": "not_an_object",
    "string top": "not_an_object",
    "number top": "not_an_object",
    "null top": "not_an_object",
    "depth 33": "too_deep",
    "depth 100000": "too_deep",
    "huge int": "integer_too_large",
    "single quotes": "malformed_json",
    "comment": "malformed_json",
}


def nest(depth: int) -> bytes:
    return b'{"a":' * (depth - 1) + b"{}" + b"}" * (depth - 1)


def group_b(m: Any) -> None:
    cfg = m.validate_config_json
    good = cfg(jb(GOOD_CONFIG))
    check(
        "B2",
        "valid config parsed",
        good.model_type == "llama"
        and good.architectures == ("LlamaForCausalLM",)
        and good.hidden_size == 64
        and good.num_hidden_layers == 2
        and good.vocab_size == 128
        and good.torch_dtype == "bfloat16",
    )
    without_dtype = {k: v for k, v in GOOD_CONFIG.items() if k != "torch_dtype"}
    check("B2", "absent torch_dtype is None", cfg(jb(without_dtype)).torch_dtype is None)
    for dtype in ("float32", "float16", "bfloat16", "float64"):
        check(
            "B2",
            f"torch_dtype {dtype} accepted",
            outcome(m, cfg, jb({**GOOD_CONFIG, "torch_dtype": dtype})) == "ok",
        )
    for dtype in (
        "float8",
        "Float16",
        "FLOAT32",
        "float32 ",
        "float32\n",
        "int8",
        "",
        5,
        None,
        ["float32"],
        True,
    ):
        check(
            "B2",
            f"torch_dtype {dtype!r} refused",
            refuses(m, "config_invalid", cfg, jb({**GOOD_CONFIG, "torch_dtype": dtype})),
        )

    bad_ints = [0, -1, True, False, 1.5, 2.0, "7", None, [], {}]
    for field in ("hidden_size", "num_hidden_layers", "vocab_size"):
        check(
            "B2",
            f"{field} missing refused",
            refuses(
                m, "config_invalid", cfg, jb({k: v for k, v in GOOD_CONFIG.items() if k != field})
            ),
        )
        check("B2", f"{field}=1 accepted", outcome(m, cfg, jb({**GOOD_CONFIG, field: 1})) == "ok")
        for bad in bad_ints:
            check(
                "B2",
                f"{field}={str(bad)[:12]!r} refused",
                refuses(m, "config_invalid", cfg, jb({**GOOD_CONFIG, field: bad})),
            )
        huge = jb({**GOOD_CONFIG, field: 1}).replace(
            b'"%s": 1' % field.encode(), b'"%s": ' % field.encode() + b"9" * 5000
        )
        check(
            "B2",
            f"{field} with a 5000-digit integer is refused by the module's own error",
            refuses(m, "config_invalid", cfg, huge),
        )
    for bad in (None, "", 5, True, [], {}, ["x"]):
        check(
            "B2",
            f"model_type={bad!r} refused",
            refuses(m, "config_invalid", cfg, jb({**GOOD_CONFIG, "model_type": bad})),
        )
    check(
        "B2",
        "model_type missing refused",
        refuses(
            m,
            "config_invalid",
            cfg,
            jb({k: v for k, v in GOOD_CONFIG.items() if k != "model_type"}),
        ),
    )
    for bad in ([], [1], ["a", 1], "LlamaForCausalLM", [""], [None], {}, None, [["a"]], 5):
        check(
            "B2",
            f"architectures={bad!r} refused",
            refuses(m, "config_invalid", cfg, jb({**GOOD_CONFIG, "architectures": bad})),
        )
    check(
        "B2",
        "architectures missing refused",
        refuses(
            m,
            "config_invalid",
            cfg,
            jb({k: v for k, v in GOOD_CONFIG.items() if k != "architectures"}),
        ),
    )
    check(
        "B2",
        "several architectures accepted",
        cfg(jb({**GOOD_CONFIG, "architectures": ["A", "B"]})).architectures == ("A", "B"),
    )

    # quantization
    for q in ({"bits": 4}, {}, None, "gptq", []):
        check(
            "B2",
            f"quantization_config={q!r} -> quantization_unverified",
            refuses(
                m, "quantization_unverified", cfg, jb({**GOOD_CONFIG, "quantization_config": q})
            ),
        )
    check(
        "B2",
        "invalid config + quantization_config is config_invalid (parse first)",
        refuses(
            m,
            "config_invalid",
            cfg,
            jb({**GOOD_CONFIG, "hidden_size": 0, "quantization_config": {}}),
        ),
    )

    # B1: strict JSON, exercised through every JSON entry point
    entries: list[tuple[str, Callable[[bytes], Any], str, int]] = [
        ("config", cfg, "config_invalid", 1 << 20),
        ("tokenizer_config", m.validate_tokenizer_config_json, "tokenizer_config_invalid", 4 << 20),
        ("adapter_config", m.validate_adapter_config_json, "adapter_config_invalid", 1 << 20),
    ]
    good_for = {
        "config": jb(GOOD_CONFIG),
        "tokenizer_config": jb({"chat_template": "x"}),
        "adapter_config": jb({"peft_type": "LORA", "r": 4, "target_modules": ["q_proj"]}),
    }
    for label, fn, code, limit in entries:
        base = good_for[label]
        check("B1", f"{label}: good payload accepted", outcome(m, fn, base) == "ok")
        # size boundary (whitespace padding keeps it valid JSON)
        at_limit = base + b" " * (limit - len(base))
        over_limit = at_limit + b" "
        check("B1", f"{label}: exactly {limit} bytes accepted", outcome(m, fn, at_limit) == "ok")
        check("B1", f"{label}: {limit}+1 bytes refused", refuses(m, code, fn, over_limit))
        check(
            "B1",
            f"{label}: {limit}+1 bytes refused as too_large",
            reason_of(m, fn, over_limit) == "too_large",
        )
        for tag, raw in [
            ("dup-key top", b'{"model_type":"a","model_type":"b"}'),
            ("dup-key same value", b'{"r":1,"r":1}'),
            ("dup-key nested", b'{"x":{"a":1,"a":2}}'),
            ("NaN", b'{"x":NaN}'),
            ("Infinity", b'{"x":Infinity}'),
            ("-Infinity", b'{"x":-Infinity}'),
            ("1e999", b'{"x":1e999}'),
            ("-1e999", b'{"x":-1e999}'),
            ("trailing garbage", base + b"x"),
            ("trailing comma", b'{"a":1,}'),
            ("two documents", base + base),
            ("empty", b""),
            ("whitespace", b"  "),
            ("bad utf8", b'{"a":"\xff"}'),
            ("overlong utf8", b'{"a":"\xc0\xaf"}'),
            ("BOM", b"\xef\xbb\xbf" + base),
            ("lone surrogate escape", b'{"a":"\\ud800"}'),
            ("lone low surrogate escape in key", b'{"\\udc00":1}'),
            ("list top", b"[]"),
            ("string top", b'"x"'),
            ("number top", b"5"),
            ("null top", b"null"),
            ("depth 33", nest(33)),
            ("depth 100000", b"[" * 100000 + b"]" * 100000),
            ("huge int", b'{"x":' + b"9" * 5000 + b"}"),
            ("single quotes", b"{'a':1}"),
            ("comment", b'{"a":1}//x'),
        ]:
            got = outcome(m, fn, raw)
            check("B1", f"{label}: {tag} refused with its own code (got {got})", got == code)
            want = JSON_REASONS[tag]
            check("B1", f"{label}: {tag} refused for reason {want}", reason_of(m, fn, raw) == want)
        # depth 32 accepted by the JSON layer itself (field rules may still refuse): must not be a depth refusal
        for tag, bad in [
            ("bytearray", bytearray(base)),
            ("memoryview", memoryview(base)),
            ("str", base.decode()),
            ("None", None),
            ("int", 5),
            ("bytes subclass", BytesSub(base)),
            ("list", [base]),
        ]:
            got = outcome(m, fn, bad)
            check(
                "B1",
                f"{label}: {tag} input refused by the module's own error (got {got})",
                got == code,
            )
            check(
                "B1",
                f"{label}: {tag} input refused as input_type",
                reason_of(m, fn, bad) == "input_type",
            )
    # integer text bound: 20 digits accepted, 21 refused (host-independent)
    twenty = b"1" + b"0" * 19
    check(
        "B1",
        "a 20-digit integer in an ignored field is accepted",
        outcome(
            m, cfg, jb({**GOOD_CONFIG, "extra": 1}).replace(b'"extra": 1', b'"extra": ' + twenty)
        )
        == "ok",
    )
    check(
        "B1",
        "a 21-digit integer is refused",
        refuses(
            m,
            "config_invalid",
            cfg,
            jb({**GOOD_CONFIG, "extra": 1}).replace(b'"extra": 1', b'"extra": ' + twenty + b"0"),
        ),
    )
    # exact depth boundary through an entry point that ignores unknown keys
    deep_ok = json.dumps(
        {**GOOD_CONFIG, "extra": json.loads(nest(31))}
    ).encode()  # container depth 1 + 31 = 32
    deep_bad = json.dumps({**GOOD_CONFIG, "extra": json.loads(nest(32))}).encode()  # 33
    check("B1", "config: depth 32 accepted", outcome(m, cfg, deep_ok) == "ok")
    check("B1", "config: depth 33 refused", refuses(m, "config_invalid", cfg, deep_bad))
    check(
        "B1",
        "unknown extra keys are ignored",
        outcome(m, cfg, jb({**GOOD_CONFIG, "rope_scaling": {"a": [1, 2]}, "extra": None})) == "ok",
    )
    check(
        "B1",
        "unicode content accepted",
        outcome(m, cfg, jb({**GOOD_CONFIG, "model_type": "lläma-中"})) == "ok",
    )

    # C1: tokenizer_config
    tk = m.validate_tokenizer_config_json
    t0 = tk(jb({}))
    check(
        "C1",
        "empty tokenizer config: no template, no tokens",
        t0.chat_template_sha256 is None
        and t0.special_tokens == ()
        and t0.additional_special_tokens == (),
    )
    check(
        "C1",
        "null chat_template is absent",
        tk(jb({"chat_template": None})).chat_template_sha256 is None,
    )
    for template in ("{{ x }}", " x \n", "\tx", "", "café", "café", "a\r\nb", "中文", "x" * 5000):
        expected = hashlib.sha256(template.encode("utf-8")).hexdigest()
        check(
            "C1",
            f"chat_template {template[:10]!r} hashed over its exact UTF-8 bytes",
            tk(jb({"chat_template": template})).chat_template_sha256 == expected,
        )
    check(
        "C1",
        "NFC and NFD templates hash differently (no normalisation)",
        tk(jb({"chat_template": "café"})).chat_template_sha256
        != tk(jb({"chat_template": "café"})).chat_template_sha256,
    )
    for bad in (5, True, [], {}, [{"name": "a", "template": "b"}], 1.5):
        check(
            "C1",
            f"chat_template={bad!r} refused",
            refuses(m, "tokenizer_config_invalid", tk, jb({"chat_template": bad})),
        )
    check(
        "C1",
        "lone-surrogate chat_template refused",
        refuses(m, "tokenizer_config_invalid", tk, b'{"chat_template":"\\ud83d"}'),
    )
    check(
        "C1",
        "paired-surrogate (emoji) chat_template accepted",
        outcome(m, tk, b'{"chat_template":"\\ud83d\\ude00"}') == "ok",
    )
    for name in (
        "bos_token",
        "eos_token",
        "unk_token",
        "pad_token",
        "sep_token",
        "cls_token",
        "mask_token",
    ):
        check(
            "C1",
            f"{name} as str accepted",
            tk(jb({name: "<s>"})).special_tokens == ((name, "<s>"),),
        )
        check(
            "C1",
            f"{name} as {{content}} accepted",
            tk(jb({name: {"content": "<s>", "lstrip": False}})).special_tokens == ((name, "<s>"),),
        )
        check("C1", f"{name} null is absent", tk(jb({name: None})).special_tokens == ())
        for bad in (
            5,
            True,
            [],
            {},
            {"content": 1},
            {"content": None},
            {"lstrip": False},
            {"content": ["x"]},
            [{"content": "x"}],
            1.5,
        ):
            check(
                "C1",
                f"{name}={bad!r} refused",
                refuses(m, "tokenizer_config_invalid", tk, jb({name: bad})),
            )
    two = tk(jb({"eos_token": "e", "bos_token": {"content": "b"}}))
    check(
        "C1",
        "special tokens reported in sorted name order",
        two.special_tokens == (("bos_token", "b"), ("eos_token", "e")),
    )
    check(
        "C1",
        "additional_special_tokens mix of str and {content}",
        tk(jb({"additional_special_tokens": ["a", {"content": "b"}]})).additional_special_tokens
        == ("a", "b"),
    )
    for bad in ("a", 5, {}, [1], [None], [{}], [{"content": 1}], [["a"]], None):
        if bad is None:
            check(
                "C1",
                "additional_special_tokens null is absent",
                tk(jb({"additional_special_tokens": None})).additional_special_tokens == (),
            )
            continue
        check(
            "C1",
            f"additional_special_tokens={bad!r} refused",
            refuses(m, "tokenizer_config_invalid", tk, jb({"additional_special_tokens": bad})),
        )
    check(
        "C1",
        "other unknown keys are ignored",
        outcome(
            m,
            tk,
            jb(
                {
                    "model_max_length": 10,
                    "tokenizer_class": "X",
                    "added_tokens_decoder": {"0": {"content": "x"}},
                }
            ),
        )
        == "ok",
    )


# -- D: safetensors header ----------------------------------------------------------


def group_d(m: Any, big: bool) -> None:
    p = m.parse_safetensors_header
    h = parse(m, st_file({"a": ("F32", [2, 3]), "b": ("BF16", [4])}, metadata={"format": "pt"}))
    check(
        "D1",
        "valid header parsed",
        dict(h.tensors).keys() == {"a", "b"} and h.metadata == (("format", "pt"),),
    )
    ta = dict(h.tensors)["a"]
    check(
        "D1",
        "tensor info carried",
        ta.dtype == "F32" and ta.shape == (2, 3) and (ta.begin, ta.end) == (0, 24),
    )
    check("D1", "buffer size reported", h.data_size == 32 and h.header_size > 0)
    check("D1", "shapes() helper", h.shapes() == {"a": (2, 3), "b": (4,)})
    check(
        "D1",
        "no __metadata__ -> empty metadata",
        parse(m, st_file({"a": ("U8", [1])})).metadata == (),
    )

    # dtype table: every allowed dtype with the exactly right size, +-1 byte wrong
    for dtype, size in DTYPE_SIZE.items():
        ok = st_tensor_header(
            {"t": {"dtype": dtype, "shape": [3], "data_offsets": [0, 3 * size]}}, 3 * size
        )
        check("D1", f"dtype {dtype} with exact size accepted", outcome(m, parse, m, ok) == "ok")
        for delta in (-1, 1):
            n = 3 * size + delta
            bad = st_tensor_header({"t": {"dtype": dtype, "shape": [3], "data_offsets": [0, n]}}, n)
            if size == 1 and delta == -1 and n < 0:
                continue
            check(
                "D1",
                f"dtype {dtype} size off by {delta} refused",
                refuses(m, "weights_header_invalid", parse, m, bad),
            )
    for dtype in (
        "f32",
        "F32 ",
        "float32",
        "F8",
        "I4",
        "U16",
        "U32",
        "U64",
        "C64",
        "bf16",
        "Bool",
        "",
        5,
        None,
        ["F32"],
        True,
    ):
        bad = st_tensor_header({"t": {"dtype": dtype, "shape": [1], "data_offsets": [0, 4]}}, 4)
        check("D1", f"dtype {dtype!r} refused", refuses(m, "weights_header_invalid", parse, m, bad))

    def one(
        shape: Any = None,
        offsets: Any = None,
        dtype: str = "F32",
        buffer_len: int = 4,
        **extra: Any,
    ) -> tuple[bytes, int]:
        entry: dict[str, Any] = {
            "dtype": dtype,
            "shape": [1] if shape is None else shape,
            "data_offsets": [0, 4] if offsets is None else offsets,
        }
        entry.update(extra)
        return st_tensor_header({"t": entry}, buffer_len)

    check("D1", "baseline one-tensor header accepted", outcome(m, parse, m, one()) == "ok")
    check(
        "D1",
        "scalar shape [] has dtype-size bytes",
        outcome(m, parse, m, one(shape=[], offsets=[0, 4])) == "ok",
    )
    check(
        "D1",
        "zero-size tensor accepted",
        outcome(m, parse, m, one(shape=[0], offsets=[0, 0], buffer_len=0)) == "ok",
    )
    for shape in ([-1], [True], [1.5], ["2"], "3", None, [[2]], [None], {}, [2**70], [1, -1], 5):
        raw = st_tensor_header({"t": {"dtype": "F32", "shape": shape, "data_offsets": [0, 4]}}, 4)
        check("D1", f"shape {shape!r} refused", refuses(m, "weights_header_invalid", parse, m, raw))
    for offs in (
        [0],
        [0, 4, 8],
        [0.0, 4],
        [0, 4.0],
        [True, 4],
        [0, True],
        [-1, 3],
        [4, 0],
        "ab",
        None,
        {},
        [None, 4],
        ["0", "4"],
        [[0], [4]],
        [0, 2**70],
    ):
        raw = st_tensor_header({"t": {"dtype": "F32", "shape": [1], "data_offsets": offs}}, 4)
        check(
            "D1",
            f"data_offsets {offs!r} refused",
            refuses(m, "weights_header_invalid", parse, m, raw),
        )
    for tag, raw in [
        ("extra tensor key", one(extra_key=1)),
        ("tensor not a dict", st_tensor_header({"t": [0, 4]}, 4)),
        ("tensor null", st_tensor_header({"t": None}, 4)),
        ("missing dtype", st_tensor_header({"t": {"shape": [1], "data_offsets": [0, 4]}}, 4)),
        ("missing shape", st_tensor_header({"t": {"dtype": "F32", "data_offsets": [0, 4]}}, 4)),
        ("missing offsets", st_tensor_header({"t": {"dtype": "F32", "shape": [1]}}, 4)),
        (
            "empty tensor name",
            st_tensor_header({"": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}, 4),
        ),
        ("zero tensors", st_tensor_header({}, 0)),
        ("only metadata", st_tensor_header({"__metadata__": {"a": "b"}}, 0)),
        ("buffer 1 byte longer than coverage", one(buffer_len=5)),
        ("buffer 1 byte shorter than coverage", one(buffer_len=3)),
        (
            "offsets start at 1",
            st_tensor_header({"t": {"dtype": "U8", "shape": [1], "data_offsets": [1, 2]}}, 2),
        ),
        (
            "offset end beyond buffer",
            st_tensor_header({"t": {"dtype": "U8", "shape": [2], "data_offsets": [0, 2]}}, 1),
        ),
        ("header is a list", st_raw(b"[]", 0)),
        ("header is a string", st_raw(b'"x"', 0)),
        ("header is null", st_raw(b"null", 0)),
        (
            "NaN in header",
            st_raw(
                b'{"__metadata__":{"a":"b"},"t":{"dtype":"U8","shape":[NaN],"data_offsets":[0,1]}}',
                1,
            ),
        ),
        (
            "duplicate tensor name",
            st_raw(
                b'{"t":{"dtype":"U8","shape":[1],"data_offsets":[0,1]},"t":{"dtype":"U8","shape":[1],"data_offsets":[1,2]}}',
                2,
            ),
        ),
        (
            "duplicate dtype key inside a tensor",
            st_raw(b'{"t":{"dtype":"U8","dtype":"F32","shape":[1],"data_offsets":[0,1]}}', 1),
        ),
        (
            "duplicate __metadata__",
            st_raw(
                b'{"__metadata__":{},"__metadata__":{},"t":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}',
                1,
            ),
        ),
        (
            "leading space before {",
            st_raw(b' {"t":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}', 1),
        ),
        ("not json", st_raw(b"{not json}", 0)),
        ("invalid utf8", st_raw(b'{"\xff":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}', 1)),
        (
            "lone surrogate name",
            st_raw(b'{"\\ud800":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}', 1),
        ),
        (
            "trailing garbage in header",
            st_raw(b'{"t":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}x', 1),
        ),
        (
            "header nested too deep",
            st_raw(b'{"t":{"dtype":"U8","shape":[[1]],"data_offsets":[0,1]}}', 1),
        ),
    ]:
        check("D1", f"{tag} refused", refuses(m, "weights_header_invalid", parse, m, raw))
    # __metadata__ rules
    for md in (
        {"a": 1},
        {"a": None},
        {"a": ["x"]},
        {"a": {"b": "c"}},
        [],
        "x",
        5,
        None,
        {"a": True},
    ):
        raw = st_tensor_header(
            {"__metadata__": md, "t": {"dtype": "U8", "shape": [1], "data_offsets": [0, 1]}}, 1
        )
        check(
            "D1",
            f"__metadata__ {md!r} refused",
            refuses(m, "weights_header_invalid", parse, m, raw),
        )
    for lookalike in (
        "__METADATA__",
        " __metadata__",
        "__metadata__ ",
        "__Metadata__",
        "__metadata__\n",
    ):
        got = parse(
            m,
            st_tensor_header({lookalike: {"dtype": "U8", "shape": [1], "data_offsets": [0, 1]}}, 1),
        )
        check(
            "D1",
            f"{lookalike!r} is an ordinary tensor name, not the metadata key",
            dict(got.tensors).keys() == {lookalike} and got.metadata == (),
        )
    check(
        "D1",
        "__metadata__ str->str accepted",
        outcome(
            m,
            parse,
            m,
            st_tensor_header(
                {
                    "__metadata__": {"a": "b", "c": ""},
                    "t": {"dtype": "U8", "shape": [1], "data_offsets": [0, 1]},
                },
                1,
            ),
        )
        == "ok",
    )
    check(
        "D1",
        "__metadata__ is not reported as a tensor",
        "__metadata__"
        not in dict(parse(m, st_file({"a": ("U8", [1])}, metadata={"k": "v"})).tensors),
    )

    # header-size N boundaries
    blob, size = st_file({"a": ("U8", [1])})
    check(
        "D1",
        "file shorter than 8 bytes refused",
        refuses(m, "weights_header_invalid", p, b"\x01\x00\x00", file_size=3),
    )
    check("D1", "empty prefix refused", refuses(m, "weights_header_invalid", p, b"", file_size=0))
    for cut in (0, 1, 3, 7, 8):
        cut_prefix = struct.pack("<Q", 2)[:cut]
        check(
            "D1",
            f"{cut}-byte prefix with a large file refused as prefix_too_short",
            reason_of(m, p, cut_prefix, file_size=100) == "prefix_too_short",
        )
    check(
        "D1",
        "N=0 refused",
        refuses(m, "weights_header_invalid", p, struct.pack("<Q", 0) + b"{}", file_size=10),
    )
    check(
        "D1",
        "N=1 refused",
        refuses(
            m, "weights_header_invalid", p, struct.pack("<Q", 1) + b"{" + b"\x00" * 4, file_size=13
        ),
    )
    check(
        "D1",
        "N larger than file_size-8 refused",
        refuses(m, "weights_header_invalid", p, blob, file_size=size - 1),
    )
    check(
        "D1",
        "N == file_size-8 with an empty buffer (zero-size tensor) accepted",
        outcome(
            m,
            parse,
            m,
            st_tensor_header({"t": {"dtype": "U8", "shape": [0], "data_offsets": [0, 0]}}, 0),
        )
        == "ok",
    )
    raw0, size0 = st_tensor_header({"t": {"dtype": "U8", "shape": [0], "data_offsets": [0, 0]}}, 0)
    check(
        "D1",
        "N == file_size-7 refused (header would overrun the file)",
        refuses(m, "weights_header_invalid", p, raw0, file_size=size0 - 1),
    )
    pblob, psize = st_file({"a": ("U8", [1])}, pad=5)
    (pn,) = struct.unpack("<Q", pblob[:8])
    check(
        "D1",
        "padded header with the prefix cut one byte short refused (cut lands in padding)",
        refuses(m, "weights_header_invalid", p, pblob[: 8 + pn - 1], file_size=psize),
    )
    check(
        "D1",
        "padded header with the exact prefix accepted",
        outcome(m, p, pblob[: 8 + pn], file_size=psize) == "ok",
    )
    short_prefix = blob[: 8 + 3]
    check(
        "D1",
        "prefix cut inside the header refused",
        refuses(m, "weights_header_invalid", p, short_prefix, file_size=size),
    )
    check(
        "D1",
        "prefix may be just header (buffer not needed)",
        outcome(m, p, blob[: 8 + struct.unpack("<Q", blob[:8])[0]], file_size=size) == "ok",
    )
    for bad_size in (True, 1.5, "100", None, -1, 2**70):
        out = outcome(m, p, blob, file_size=bad_size)
        check(
            "D1",
            f"file_size={bad_size!r} refused by the module's own error (got {out})",
            out == "weights_header_invalid",
        )
    for tag, bad in [
        ("bytearray", bytearray(blob)),
        ("memoryview", memoryview(blob)),
        ("str", "x" * 20),
        ("None", None),
        ("bytes subclass", BytesSub(blob)),
    ]:
        out = outcome(m, p, bad, file_size=size)
        check(
            "D1",
            f"{tag} prefix refused by the module's own error (got {out})",
            out == "weights_header_invalid",
        )
    check(
        "D1",
        "header padded with spaces (as the reference writer does) accepted",
        outcome(m, parse, m, st_file({"a": ("U8", [1])}, pad=7)) == "ok",
    )
    check(
        "D1",
        "huge N claiming more than 2**63 refused",
        refuses(
            m, "weights_header_invalid", p, struct.pack("<Q", 2**63 + 5) + b"{}", file_size=2**64
        ),
    )
    check("D1", "max header constant is 100,000,000", m.MAX_SAFETENSORS_HEADER_BYTES == 100_000_000)
    if big:
        limit = m.MAX_SAFETENSORS_HEADER_BYTES
        body = b'{"t":{"dtype":"U8","shape":[1],"data_offsets":[0,1]}}'
        at = body + b" " * (limit - len(body))
        blob_at = struct.pack("<Q", len(at)) + at + b"\x00"
        check(
            "D1",
            "N == 100,000,000 accepted",
            outcome(m, p, blob_at, file_size=len(blob_at)) == "ok",
        )
        del blob_at
        over = body + b" " * (limit + 1 - len(body))
        blob_over = struct.pack("<Q", len(over)) + over + b"\x00"
        check(
            "D1",
            "N == 100,000,001 refused",
            refuses(m, "weights_header_invalid", p, blob_over, file_size=len(blob_over)),
        )
        del blob_over

    # contiguity, in every key order, plus generated perturbations
    spans = [("x", "F32", [2], 8), ("y", "U8", [3], 3), ("z", "F16", [2, 2], 8)]

    def build(
        offs: list[tuple[int, int]], buffer_len: int, order: tuple[int, ...]
    ) -> tuple[bytes, int]:
        entries = {
            spans[i][0]: {"dtype": spans[i][1], "shape": spans[i][2], "data_offsets": list(offs[i])}
            for i in order
        }
        return st_tensor_header(entries, buffer_len)

    good_offs = [(0, 8), (8, 11), (11, 19)]
    for order in itertools.permutations(range(3)):
        check(
            "D1",
            f"contiguous offsets accepted in key order {order}",
            outcome(m, parse, m, build(good_offs, 19, order)) == "ok",
        )
    bad_variants = {
        "gap": [(0, 8), (9, 12), (12, 20)],
        "overlap": [(0, 8), (7, 10), (10, 18)],
        "starts late": [(1, 9), (9, 12), (12, 20)],
        "duplicate span": [(0, 8), (8, 11), (0, 8)],
    }
    for tag, offs in bad_variants.items():
        for order in itertools.permutations(range(3)):
            check(
                "D1",
                f"{tag} refused in key order {order}",
                refuses(
                    m,
                    "weights_header_invalid",
                    parse,
                    m,
                    build(offs, max(e for _, e in offs), order),
                ),
            )
    for extra in (-1, 1):
        check(
            "D1",
            f"buffer {extra:+d} bytes vs full coverage refused",
            refuses(m, "weights_header_invalid", parse, m, build(good_offs, 19 + extra, (0, 1, 2))),
        )
    check(
        "D1",
        "tensors reported in offset order",
        [n for n, _ in parse(m, build(good_offs, 19, (2, 0, 1))).tensors] == ["x", "y", "z"],
    )


# -- E: adapter ------------------------------------------------------------------------


def lora(
    m: Any, module: str, r: int, in_f: int, out_f: int, prefix: str = "base_model.model."
) -> dict[str, tuple[str, list[int]]]:
    return {
        f"{prefix}{module}.lora_A.weight": ("F32", [r, in_f]),
        f"{prefix}{module}.lora_B.weight": ("F32", [out_f, r]),
    }


GOOD_ADAPTER_CFG = {
    "peft_type": "LORA",
    "r": 4,
    "target_modules": ["q_proj", "v_proj"],
    "base_model_name_or_path": "meta/llama",
}
MOD_Q = "model.layers.0.self_attn.q_proj"
MOD_V = "model.layers.0.self_attn.v_proj"
BASE_SHAPES = {
    f"{MOD_Q}.weight": (16, 32),
    f"{MOD_V}.weight": (8, 32),
    "model.embed_tokens.weight": (128, 32),
}


def group_e(m: Any) -> None:
    ac = m.validate_adapter_config_json
    cfg = ac(jb(GOOD_ADAPTER_CFG))
    check(
        "E1",
        "valid adapter_config parsed",
        cfg.r == 4
        and cfg.target_modules == ("q_proj", "v_proj")
        and cfg.base_model_name_or_path == "meta/llama",
    )
    check(
        "E1",
        "base_model_name_or_path absent/null is None",
        ac(
            jb({k: v for k, v in GOOD_ADAPTER_CFG.items() if k != "base_model_name_or_path"})
        ).base_model_name_or_path
        is None
        and ac(jb({**GOOD_ADAPTER_CFG, "base_model_name_or_path": None})).base_model_name_or_path
        is None,
    )
    for bad in (5, True, [], {}):
        check(
            "E1",
            f"base_model_name_or_path={bad!r} refused",
            refuses(
                m,
                "adapter_config_invalid",
                ac,
                jb({**GOOD_ADAPTER_CFG, "base_model_name_or_path": bad}),
            ),
        )
    for peft in (
        "lora",
        "Lora",
        "LORA ",
        " LORA",
        "LORA\n",
        "IA3",
        "PREFIX_TUNING",
        "",
        None,
        5,
        True,
        ["LORA"],
        {},
    ):
        check(
            "E1",
            f"peft_type={peft!r} refused",
            refuses(m, "adapter_config_invalid", ac, jb({**GOOD_ADAPTER_CFG, "peft_type": peft})),
        )
    check(
        "E1",
        "peft_type missing refused",
        refuses(
            m,
            "adapter_config_invalid",
            ac,
            jb({k: v for k, v in GOOD_ADAPTER_CFG.items() if k != "peft_type"}),
        ),
    )
    for r in (0, -1, True, False, 1.5, 4.0, "8", None, [], {}):
        check(
            "E1",
            f"r={str(r)[:10]!r} refused",
            refuses(m, "adapter_config_invalid", ac, jb({**GOOD_ADAPTER_CFG, "r": r})),
        )
    huge_r = jb({**GOOD_ADAPTER_CFG, "r": 1}).replace(b'"r": 1', b'"r": ' + b"9" * 5000)
    check(
        "E1",
        "r as a 5000-digit integer is refused by the module's own error",
        refuses(m, "adapter_config_invalid", ac, huge_r),
    )
    check(
        "E1",
        "r missing refused",
        refuses(
            m,
            "adapter_config_invalid",
            ac,
            jb({k: v for k, v in GOOD_ADAPTER_CFG.items() if k != "r"}),
        ),
    )
    check("E1", "r=1 accepted", ac(jb({**GOOD_ADAPTER_CFG, "r": 1})).r == 1)
    for tm in ([], "q_proj", [1], [""], [None], ["q", 1], None, {}, [["q"]], 5):
        check(
            "E1",
            f"target_modules={tm!r} refused",
            refuses(
                m, "adapter_config_invalid", ac, jb({**GOOD_ADAPTER_CFG, "target_modules": tm})
            ),
        )
    check(
        "E1",
        "target_modules missing refused",
        refuses(
            m,
            "adapter_config_invalid",
            ac,
            jb({k: v for k, v in GOOD_ADAPTER_CFG.items() if k != "target_modules"}),
        ),
    )

    cc = m.check_adapter_base_compatibility

    def header(tensors: dict[str, tuple[str, list[int]]]) -> Any:
        return parse(m, st_file(tensors))

    good = {**lora(m, MOD_Q, 4, 32, 16), **lora(m, MOD_V, 4, 32, 8)}
    check(
        "E2", "compatible adapter accepted", outcome(m, cc, header(good), cfg, BASE_SHAPES) == "ok"
    )
    check(
        "E2",
        "adapter keys without the base_model.model. prefix accepted",
        outcome(
            m,
            cc,
            header({**lora(m, MOD_Q, 4, 32, 16, prefix=""), **lora(m, MOD_V, 4, 32, 8, prefix="")}),
            cfg,
            BASE_SHAPES,
        )
        == "ok",
    )
    cfg_q = ac(jb({**GOOD_ADAPTER_CFG, "target_modules": ["q_proj"]}))
    check(
        "E2",
        "single targeted module accepted",
        outcome(m, cc, header(lora(m, MOD_Q, 4, 32, 16)), cfg_q, BASE_SHAPES) == "ok",
    )
    check(
        "E2",
        "full-path target_modules entry accepted",
        outcome(
            m,
            cc,
            header(lora(m, MOD_Q, 4, 32, 16)),
            ac(jb({**GOOD_ADAPTER_CFG, "target_modules": [MOD_Q]})),
            BASE_SHAPES,
        )
        == "ok",
    )
    bad_cases = {
        "r in tensors differs from config r": {
            **lora(m, MOD_Q, 8, 32, 16),
            **lora(m, MOD_V, 8, 32, 8),
        },
        "lora_A/lora_B r differ": {
            f"base_model.model.{MOD_Q}.lora_A.weight": ("F32", [4, 32]),
            f"base_model.model.{MOD_Q}.lora_B.weight": ("F32", [16, 5]),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "in/out swapped": {**lora(m, MOD_Q, 4, 16, 32), **lora(m, MOD_V, 4, 32, 8)},
        "A and B shapes swapped": {
            f"base_model.model.{MOD_Q}.lora_A.weight": ("F32", [16, 4]),
            f"base_model.model.{MOD_Q}.lora_B.weight": ("F32", [4, 32]),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "in dim off by one": {**lora(m, MOD_Q, 4, 33, 16), **lora(m, MOD_V, 4, 32, 8)},
        "out dim off by one": {**lora(m, MOD_Q, 4, 32, 17), **lora(m, MOD_V, 4, 32, 8)},
        "missing base tensor": {
            **lora(m, "model.layers.1.self_attn.q_proj", 4, 32, 16),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "module not targeted (base has it, config does not)": {
            **lora(m, MOD_Q, 4, 32, 16),
            **lora(m, MOD_V, 4, 32, 8),
            **lora(m, "model.layers.0.self_attn.k_proj", 4, 32, 8),
        },
        "module matches only without the dot boundary": {
            **lora(m, MOD_Q, 4, 32, 16),
            **lora(m, MOD_V, 4, 32, 8),
            **lora(m, "model.layers.0.self_attn.xq_proj", 4, 32, 8),
        },
        "unpaired lora_A": {
            f"base_model.model.{MOD_Q}.lora_A.weight": ("F32", [4, 32]),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "unpaired lora_B": {
            f"base_model.model.{MOD_Q}.lora_B.weight": ("F32", [16, 4]),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "non-lora tensor": {**good, "base_model.model.model.norm.weight": ("F32", [32])},
        "lora tensor without .weight": {
            **good,
            f"base_model.model.{MOD_Q}.lora_A": ("F32", [4, 32]),
        },
        "lora_embedding tensor": {
            **good,
            "base_model.model.model.embed_tokens.lora_embedding_A": ("F32", [4, 128]),
        },
        "modules_to_save tensor": {
            **good,
            "base_model.model.model.embed_tokens.modules_to_save.default.weight": (
                "F32",
                [128, 32],
            ),
        },
        "rank-1 A": {
            f"base_model.model.{MOD_Q}.lora_A.weight": ("F32", [4]),
            f"base_model.model.{MOD_Q}.lora_B.weight": ("F32", [16, 4]),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "rank-3 B": {
            f"base_model.model.{MOD_Q}.lora_A.weight": ("F32", [4, 32]),
            f"base_model.model.{MOD_Q}.lora_B.weight": ("F32", [16, 4, 1]),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "colliding names after prefix strip": {
            **lora(m, MOD_Q, 4, 32, 16),
            **lora(m, MOD_Q, 4, 32, 16, prefix=""),
            **lora(m, MOD_V, 4, 32, 8),
        },
        "empty module name": {
            "base_model.model..lora_A.weight": ("F32", [4, 32]),
            "base_model.model..lora_B.weight": ("F32", [16, 4]),
        },
        "a-name-ending-newline": {
            f"base_model.model.{MOD_Q}.lora_A.weight\n": ("F32", [4, 32]),
            f"base_model.model.{MOD_Q}.lora_B.weight": ("F32", [16, 4]),
            **lora(m, MOD_V, 4, 32, 8),
        },
    }
    base_with_extras = {
        **BASE_SHAPES,
        "model.layers.0.self_attn.k_proj.weight": (8, 32),
        "model.layers.0.self_attn.xq_proj.weight": (8, 32),
    }
    for tag, tensors in bad_cases.items():
        check(
            "E2",
            f"{tag} -> adapter_base_incompatible",
            refuses(m, "adapter_base_incompatible", cc, header(tensors), cfg, base_with_extras),
        )
    check(
        "E2",
        "config targets a module that has no adapter tensors (extra target)",
        refuses(
            m,
            "adapter_base_incompatible",
            cc,
            header(good),
            ac(jb({**GOOD_ADAPTER_CFG, "target_modules": ["q_proj", "v_proj", "o_proj"]})),
            BASE_SHAPES,
        ),
    )
    check(
        "E2",
        "config r differs from the tensors",
        refuses(
            m,
            "adapter_base_incompatible",
            cc,
            header(good),
            ac(jb({**GOOD_ADAPTER_CFG, "r": 8})),
            BASE_SHAPES,
        ),
    )
    check(
        "E2",
        "suffix match is on a dot boundary (proj does not target q_proj)",
        refuses(
            m,
            "adapter_base_incompatible",
            cc,
            header(lora(m, MOD_Q, 4, 32, 16)),
            ac(jb({**GOOD_ADAPTER_CFG, "target_modules": ["proj"]})),
            BASE_SHAPES,
        ),
    )
    check(
        "E2",
        "base missing every tensor",
        refuses(m, "adapter_base_incompatible", cc, header(good), cfg, {}),
    )
    wrong_rank_base = {**BASE_SHAPES, f"{MOD_Q}.weight": (16,)}
    check(
        "E2",
        "a zero-size base tensor elsewhere in the map is accepted",
        outcome(m, cc, header(good), cfg, {**BASE_SHAPES, "model.empty.weight": (0, 4)}) == "ok",
    )
    empty_adapter = m.SafetensorsHeader(tensors=(), metadata=(), header_size=2, data_size=0)
    check(
        "E2",
        "an adapter header with no tensors is refused as no_lora_tensors",
        reason_of(m, cc, empty_adapter, cfg, BASE_SHAPES) == "no_lora_tensors",
    )
    check(
        "E2",
        "base tensor of rank 1 refused",
        refuses(m, "adapter_base_incompatible", cc, header(good), cfg, wrong_rank_base),
    )
    # the name never resolves the base (P3a/P3: base_model_name_or_path is informational only)
    other_name = ac(jb({**GOOD_ADAPTER_CFG, "base_model_name_or_path": "totally/different-model"}))
    check(
        "E2",
        "different base_model_name_or_path but compatible shapes -> accepted",
        outcome(m, cc, header(good), other_name, BASE_SHAPES) == "ok",
    )
    check(
        "E2",
        "right base_model_name_or_path but incompatible shapes -> refused",
        refuses(
            m,
            "adapter_base_incompatible",
            cc,
            header({**lora(m, MOD_Q, 4, 99, 16), **lora(m, MOD_V, 4, 32, 8)}),
            cfg,
            BASE_SHAPES,
        ),
    )
    # exact input types
    for tag, args in [
        ("adapter not a header", ("x", cfg, BASE_SHAPES)),
        ("config not an AdapterConfig", (header(good), {"r": 4}, BASE_SHAPES)),
        ("base_shapes a list", (header(good), cfg, list(BASE_SHAPES.items()))),
        ("base_shapes None", (header(good), cfg, None)),
        ("base shape a list", (header(good), cfg, {**BASE_SHAPES, f"{MOD_Q}.weight": [16, 32]})),
        (
            "base shape with bool",
            (header(good), cfg, {**BASE_SHAPES, f"{MOD_Q}.weight": (True, 32)}),
        ),
        (
            "base shape with negative",
            (header(good), cfg, {**BASE_SHAPES, f"{MOD_Q}.weight": (-16, 32)}),
        ),
        (
            "base shape with float",
            (header(good), cfg, {**BASE_SHAPES, f"{MOD_Q}.weight": (16.0, 32)}),
        ),
        ("base key not a str", (header(good), cfg, {**BASE_SHAPES, 5: (1, 1)})),
        (
            "base key a str subclass",
            (header(good), cfg, {StrSub(f"{MOD_Q}.weight"): (16, 32), f"{MOD_V}.weight": (8, 32)}),
        ),
    ]:
        got = outcome(m, cc, *args)
        check(
            "E2",
            f"{tag} refused by the module's own error (got {got})",
            got == "adapter_base_incompatible",
        )
    # helper: merge shard headers into the base shape map
    sh1 = header({"a.weight": ("F32", [2, 2])})
    sh2 = header({"b.weight": ("F32", [3])})
    check(
        "E2",
        "base_shapes_from_headers merges shards",
        m.base_shapes_from_headers([sh1, sh2]) == {"a.weight": (2, 2), "b.weight": (3,)},
    )
    check(
        "E2",
        "a tensor present in two shards is refused",
        refuses(m, "weights_header_invalid", m.base_shapes_from_headers, [sh1, sh1]),
    )
    check(
        "E2",
        "non-header shard refused by the module's own error",
        refuses(m, "weights_header_invalid", m.base_shapes_from_headers, [sh1, "x"]),
    )
    check(
        "E2",
        "non-list shards refused",
        refuses(m, "weights_header_invalid", m.base_shapes_from_headers, sh1),
    )


# -- F: META-LEAK ------------------------------------------------------------------------

MARK = "ZZLEAKMARKZZ-7f3a"


def leaked(exc: BaseException) -> bool:
    blob = " ".join(
        [
            str(exc),
            repr(exc),
            " ".join(map(repr, exc.args)),
            "".join(traceback.format_exception(exc)),
        ]
    )
    cur: BaseException | None = exc
    while cur is not None:
        blob += " " + str(cur) + repr(getattr(cur, "doc", ""))
        cur = cur.__cause__ or cur.__context__
    return MARK in blob


def group_f(m: Any) -> None:
    cases: list[tuple[str, Callable[[], Any]]] = [
        ("file name unexpected", lambda: m.check_file_names([f"{MARK}.sh"], kind="weights")),
        ("file name pickle", lambda: m.check_file_names([f"{MARK}.bin"], kind="weights")),
        (
            "file name missing-required context",
            lambda: m.check_file_names([f"{MARK}"], kind="adapter"),
        ),
        (
            "config dup key",
            lambda: m.validate_config_json((f'{{"{MARK}":1,"{MARK}":2}}').encode()),
        ),
        (
            "config bad type value",
            lambda: m.validate_config_json(jb({**GOOD_CONFIG, "model_type": [MARK]})),
        ),
        (
            "config architectures content",
            lambda: m.validate_config_json(jb({**GOOD_CONFIG, "architectures": [MARK, 1]})),
        ),
        (
            "config dtype content",
            lambda: m.validate_config_json(jb({**GOOD_CONFIG, "torch_dtype": MARK})),
        ),
        (
            "config quantization content",
            lambda: m.validate_config_json(jb({**GOOD_CONFIG, "quantization_config": {"x": MARK}})),
        ),
        (
            "config malformed with marker",
            lambda: m.validate_config_json((f'{{"{MARK}": NaN}}').encode()),
        ),
        (
            "config invalid utf8 near marker",
            lambda: m.validate_config_json(MARK.encode() + b"\xff"),
        ),
        (
            "tokenizer special token content",
            lambda: m.validate_tokenizer_config_json(jb({"bos_token": {"content": [MARK]}})),
        ),
        (
            "tokenizer template wrong type",
            lambda: m.validate_tokenizer_config_json(jb({"chat_template": {MARK: 1}})),
        ),
        (
            "adapter peft_type content",
            lambda: m.validate_adapter_config_json(jb({**GOOD_ADAPTER_CFG, "peft_type": MARK})),
        ),
        (
            "adapter target content",
            lambda: m.validate_adapter_config_json(
                jb({**GOOD_ADAPTER_CFG, "target_modules": [MARK, 1]})
            ),
        ),
        (
            "safetensors tensor name",
            lambda: parse(
                m,
                st_tensor_header({MARK: {"dtype": "F32", "shape": [1], "data_offsets": [0, 3]}}, 3),
            ),
        ),
        (
            "safetensors dtype content",
            lambda: parse(
                m, st_tensor_header({"t": {"dtype": MARK, "shape": [1], "data_offsets": [0, 4]}}, 4)
            ),
        ),
        (
            "safetensors metadata content",
            lambda: parse(
                m,
                st_tensor_header(
                    {
                        "__metadata__": {"a": [MARK]},
                        "t": {"dtype": "U8", "shape": [1], "data_offsets": [0, 1]},
                    },
                    1,
                ),
            ),
        ),
        (
            "safetensors dup tensor",
            lambda: parse(
                m,
                st_raw(
                    (
                        f'{{"{MARK}":{{"dtype":"U8","shape":[1],"data_offsets":[0,1]}},'
                        f'"{MARK}":{{"dtype":"U8","shape":[1],"data_offsets":[1,2]}}}}'
                    ).encode(),
                    2,
                ),
            ),
        ),
        (
            "adapter/base tensor names",
            lambda: m.check_adapter_base_compatibility(
                parse(
                    m,
                    st_file(
                        {
                            f"{MARK}.lora_A.weight": ("F32", [4, 8]),
                            f"{MARK}.lora_B.weight": ("F32", [8, 4]),
                        }
                    ),
                ),
                m.validate_adapter_config_json(jb(GOOD_ADAPTER_CFG)),
                {f"{MARK}.weight": (1, 1)},
            ),
        ),
        (
            "adapter bad tensor name",
            lambda: m.check_adapter_base_compatibility(
                parse(m, st_file({f"{MARK}": ("F32", [4, 8])})),
                m.validate_adapter_config_json(jb(GOOD_ADAPTER_CFG)),
                {},
            ),
        ),
    ]
    for label, fn in cases:
        try:
            fn()
        except m.ImportArtifactValidationError as exc:
            check("F1", f"{label}: refused", True)
            check("F1", f"{label}: no marker in text, args, chain or traceback", not leaked(exc))
            check(
                "F1",
                f"{label}: the message is the code plus a static reason",
                str(exc).startswith(str(exc.code)),
            )
        except BaseException as exc:  # noqa: BLE001
            check("F1", f"{label}: raised {type(exc).__name__} instead of the module error", False)
        else:
            check("F1", f"{label}: expected a refusal", False)


# -- G: purity -------------------------------------------------------------------------------

ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "math",
    "re",
    "struct",
    "dataclasses",
    "collections",
    "typing",
}
FORBIDDEN_NAMES = {
    "torch",
    "safetensors",
    "transformers",
    "numpy",
    "pickle",
    "marshal",
    "shelve",
    "importlib",
    "subprocess",
    "os",
    "sys",
    "socket",
    "pathlib",
    "ctypes",
    "builtins",
    "joblib",
    "dill",
    "cloudpickle",
    "yaml",
    "tempfile",
    "shutil",
    "io",
}
FORBIDDEN_CALLS = {
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
    "getattr",
    "setattr",
    "globals",
    "locals",
    "input",
    "print",
}


def group_g(m: Any, source: str) -> None:
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    check(
        "G1",
        f"imports are stdlib-only and from the allowed set (got {sorted(imported)})",
        imported <= ALLOWED_IMPORTS,
    )
    check("G1", "no forbidden module is imported", not (imported & FORBIDDEN_NAMES))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    check(
        "G1",
        f"no file/exec/introspection builtin is called (got {sorted(called & FORBIDDEN_CALLS)})",
        not (called & FORBIDDEN_CALLS),
    )
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    check("G1", "no forbidden module name is referenced", not (names & FORBIDDEN_NAMES))


# -- H: faults ----------------------------------------------------------------------------------

# (name, old text, new text, occurrences of `old`, needs the 100 MB boundary cases)
# Each patch is applied to a copy of the module; the whole suite must go RED on that copy.
FAULTS: list[tuple[str, str, str, int, bool]] = [
    (
        "pickle-formats-allowed",
        "if _is_pickle_capable(name):\n            unsafe = True",
        "if _is_pickle_capable(name):\n            continue",
        1,
        False,
    ),
    (
        "allow-list-becomes-deny-list",
        "        else:\n            unexpected = True",
        "        else:\n            continue",
        1,
        False,
    ),
    (
        "pickle-extension-case-sensitive",
        'name.lower().split(".")[1:]',
        'name.split(".")[1:]',
        1,
        False,
    ),
    (
        "pickle-only-last-extension",
        'name.lower().split(".")[1:]',
        'name.lower().rsplit(".", 1)[1:]',
        1,
        False,
    ),
    ("npz-not-pickle", '"joblib", "npy", "npz"}', '"joblib", "npy"}', 1, False),
    (
        "str-subclass-name-accepted",
        "any(type(item) is not str for item in items)",
        "any(not isinstance(item, str) for item in items)",
        1,
        False,
    ),
    (
        "shard-match-not-fullmatch",
        "_WEIGHTS_SHARD.fullmatch(name)",
        "_WEIGHTS_SHARD.match(name)",
        1,
        False,
    ),
    ("adapter-file-allowed-as-weights-shard", " and name != _ADAPTER_WEIGHTS_FILE", "", 1, False),
    ("required-files-not-checked", "    if spec.required - seen:", "    if False:", 1, False),
    (
        "tokenizer-or-shard-requirement-dropped",
        "    if spec.shards and not (",
        "    if False and not (",
        1,
        False,
    ),
    (
        "duplicate-names-accepted",
        "        if name in seen:\n            unexpected = True\n            continue",
        "        if name in seen:\n            continue",
        1,
        False,
    ),
    (
        "integer-digit-bound-dropped",
        "    if len(text) > _MAX_INT_TEXT_LENGTH:",
        "    if False:",
        1,
        False,
    ),
    (
        "integer-digit-bound-off-by-one",
        "    if len(text) > _MAX_INT_TEXT_LENGTH:",
        "    if len(text) >= _MAX_INT_TEXT_LENGTH:",
        1,
        False,
    ),
    (
        "duplicate-json-keys-accepted",
        '        if key in out:\n            raise _Reject("duplicate_key")',
        "        if key in out:\n            pass",
        1,
        False,
    ),
    (
        "nan-infinity-constants-accepted",
        'def _reject_constant(_name: str) -> object:\n    raise _Reject("non_finite_number")',
        "def _reject_constant(_name: str) -> object:\n    return 0",
        1,
        False,
    ),
    ("overflowing-float-accepted", "    if not math.isfinite(value):", "    if False:", 1, False),
    ("depth-bound-dropped", "if depth + 1 > max_depth:", "if False:", 2, False),
    (
        "surrogates-accepted",
        '_SURROGATES = re.compile("[\\ud800-\\udfff]")',
        '_SURROGATES = re.compile("(?!)")',
        1,
        False,
    ),
    ("size-bound-dropped", "    if len(data) > max_bytes:", "    if False:", 1, False),
    (
        "size-bound-off-by-one",
        "    if len(data) > max_bytes:",
        "    if len(data) >= max_bytes:",
        1,
        False,
    ),
    (
        "bytearray-input-accepted",
        '    if type(data) is not bytes:\n        return None, "input_type"',
        '    if not isinstance(data, (bytes, bytearray)):\n        return None, "input_type"',
        1,
        False,
    ),
    (
        "non-object-top-level-accepted",
        '    if type(value) is not dict:\n        raise ImportArtifactValidationError(code, "not_an_object")\n    return value',
        "    return value if type(value) is dict else {}",
        1,
        False,
    ),
    (
        "json-error-chained",
        '    except ValueError:\n        reason = "malformed_json"',
        '    except ValueError as exc:\n        raise ImportArtifactValidationError("config_invalid", "malformed_json") from exc',
        1,
        False,
    ),
    (
        "error-text-carries-dtype",
        '        raise _header_error("dtype")',
        '        raise _header_error(f"dtype {dtype}")',
        1,
        False,
    ),
    (
        "quantization-config-accepted",
        '    if "quantization_config" in obj:',
        "    if False:",
        1,
        False,
    ),
    (
        "bool-accepted-as-int",
        "return type(value) is int and value > 0",
        "return isinstance(value, int) and value > 0",
        1,
        False,
    ),
    ("empty-architectures-accepted", "        or len(architectures) == 0\n", "", 1, False),
    (
        "torch-dtype-unchecked",
        '    if "torch_dtype" in obj and (type(torch_dtype) is not str or torch_dtype not in _TORCH_DTYPES):',
        "    if False:",
        1,
        False,
    ),
    (
        "chat-template-hash-over-stripped-text",
        'template.encode("utf-8")',
        'template.strip().encode("utf-8")',
        1,
        False,
    ),
    (
        "special-token-content-unchecked",
        "        if type(content) is str:\n            return content",
        "        if True:\n            return content",
        1,
        False,
    ),
    (
        "safetensors-header-size-max-off-by-one",
        "n > MAX_SAFETENSORS_HEADER_BYTES",
        "n >= MAX_SAFETENSORS_HEADER_BYTES",
        1,
        True,
    ),
    (
        "safetensors-header-size-max-too-large",
        "n > MAX_SAFETENSORS_HEADER_BYTES",
        "n > MAX_SAFETENSORS_HEADER_BYTES + 1",
        1,
        True,
    ),
    (
        "safetensors-header-vs-file-too-strict",
        "    if n > file_size - 8:",
        "    if n >= file_size - 8:",
        1,
        False,
    ),
    (
        "safetensors-prefix-length-off-by-one",
        "    if len(prefix) < 8 + n:",
        "    if len(prefix) < 7 + n:",
        1,
        False,
    ),
    ("safetensors-big-endian-length", '(prefix[:8], "little")', '(prefix[:8], "big")', 1, False),
    ("safetensors-header-not-brace-checked", '    if raw[:1] != b"{":', "    if False:", 1, False),
    ("safetensors-no-tensors-accepted", "    if not tensors:", "    if False:", 1, False),
    (
        "safetensors-metadata-values-unchecked",
        '        if type(value) is not str:\n            raise _header_error("metadata_value_not_a_string")',
        '        if False:\n            raise _header_error("metadata_value_not_a_string")',
        1,
        False,
    ),
    (
        "safetensors-extra-tensor-keys-accepted",
        "set(entry) != _TENSOR_KEYS",
        "not _TENSOR_KEYS <= set(entry)",
        1,
        False,
    ),
    (
        "safetensors-dtype-unchecked",
        "dtype not in _SAFETENSORS_DTYPE_SIZES",
        "dtype.upper() not in _SAFETENSORS_DTYPE_SIZES",
        1,
        False,
    ),
    (
        "safetensors-shape-check-dropped",
        "type(shape) is not list or any(\n        type(d) is not int or d < 0 or d > _MAX_SHAPE_DIM for d in shape\n    ):",
        "type(shape) is not list:",
        1,
        False,
    ),
    (
        "safetensors-dimension-above-u64-accepted",
        "d < 0 or d > _MAX_SHAPE_DIM",
        "d < 0",
        1,
        False,
    ),
    ("safetensors-negative-dimension-accepted", "d < 0 or d >", "d < -1 or d >", 1, False),
    (
        "safetensors-shape-product-overflow-accepted",
        "        if elements > _MAX_SHAPE_DIM:",
        "        if elements > _MAX_SHAPE_DIM**2:",
        1,
        False,
    ),
    (
        "adapter-lora-A-rank-unchecked",
        "if shape_a[0] != config.r or shape_b[1] != config.r:",
        "if shape_b[1] != config.r:",
        1,
        False,
    ),
    (
        "adapter-lora-B-rank-unchecked",
        "if shape_a[0] != config.r or shape_b[1] != config.r:",
        "if shape_a[0] != config.r:",
        1,
        False,
    ),
    (
        "peft-prefix-stripped-anywhere",
        "name[len(_PEFT_PREFIX) :] if name.startswith(_PEFT_PREFIX) else name",
        'name.replace(_PEFT_PREFIX, "")',
        1,
        False,
    ),
    (
        "safetensors-offset-type-check-dropped",
        " or any(type(o) is not int for o in offsets)",
        "",
        1,
        False,
    ),
    (
        "safetensors-size-equation-dropped",
        "    if offsets[1] - offsets[0] != expected:",
        "    if False:",
        1,
        False,
    ),
    (
        "safetensors-contiguity-dropped",
        "        if info.begin != cursor:",
        "        if False:",
        1,
        False,
    ),
    (
        "safetensors-gaps-accepted",
        "        if info.begin != cursor:",
        "        if info.begin < cursor:",
        1,
        False,
    ),
    (
        "safetensors-offset-coverage-dropped",
        "    if cursor != data_size:",
        "    if False:",
        1,
        False,
    ),
    ("safetensors-empty-tensor-name-accepted", "    if len(name) == 0:", "    if False:", 1, False),
    (
        "adapter-peft-type-case-insensitive",
        'peft_type != "LORA"',
        'peft_type.upper() != "LORA"',
        1,
        False,
    ),
    (
        "adapter-target-modules-string-accepted",
        "        type(target_modules) is not list\n        or len(target_modules) == 0\n",
        "        len(target_modules or []) == 0\n",
        1,
        False,
    ),
    (
        "adapter-r-mismatch-allowed",
        "if shape_a[0] != config.r or shape_b[1] != config.r:",
        "if shape_a[0] != shape_b[1]:",
        1,
        False,
    ),
    (
        "adapter-shape-check-dropped",
        "        if base_shape != (shape_b[0], shape_a[1]):",
        "        if False:",
        1,
        False,
    ),
    (
        "adapter-in-out-swapped-accepted",
        "(shape_b[0], shape_a[1])",
        "(shape_a[1], shape_b[0])",
        1,
        False,
    ),
    (
        "adapter-base-tensor-missing-accepted",
        '        if base_shape is None:\n            raise _incompatible("base_tensor_missing")\n        if base_shape != (shape_b[0], shape_a[1]):\n            raise _incompatible("shape_mismatch")',
        '        if base_shape is not None and base_shape != (shape_b[0], shape_a[1]):\n            raise _incompatible("shape_mismatch")',
        1,
        False,
    ),
    (
        "adapter-untargeted-module-accepted",
        "        if not any(_target_matches(module, t) for t in config.target_modules):",
        "        if False:",
        1,
        False,
    ),
    (
        "adapter-target-suffix-without-dot",
        'return module == target or module.endswith("." + target)',
        "return module == target or module.endswith(target)",
        1,
        False,
    ),
    (
        "adapter-extra-target-accepted",
        "        if not any(_target_matches(module, target) for module in pairs):",
        "        if False:",
        1,
        False,
    ),
    (
        "adapter-unpaired-lora-accepted",
        '        if "A" not in slot or "B" not in slot:',
        "        if False:",
        1,
        False,
    ),
    (
        "adapter-duplicate-lora-key-accepted",
        "        if side in slot:",
        "        if False:",
        1,
        False,
    ),
    (
        "adapter-non-lora-tensor-ignored",
        '        if match is None:\n            raise _incompatible("not_a_lora_tensor")',
        "        if match is None:\n            continue",
        1,
        False,
    ),
    ("adapter-base-shapes-unchecked", "    if shapes is None:", "    if False:", 1, False),
    (
        "shard-in-two-files-merged",
        '        for name, info in header.tensors:\n            if name in merged:\n                raise _header_error("tensor_in_two_shards")',
        "        for name, info in header.tensors:\n            if name in merged:\n                pass",
        1,
        False,
    ),
]


def group_round2(m: Any) -> None:
    """Round-2 return: bounded shape product, negative dimension, lora_A rank, PEFT prefix."""
    import time

    shape_u64 = 2**64 - 1
    # R1: dimensions and the running product stay within u64, in order (like the format's reader)
    for shape in ([0, 2**64], [2**64], [shape_u64 + 1, 0], [2**32, 2**32, 0], [2**40, 2**40, 1]):
        raw = st_tensor_header({"t": {"dtype": "U8", "shape": shape, "data_offsets": [0, 0]}}, 0)
        check(
            "R1",
            f"shape {shape!r} refused as `shape`",
            reason_of(m, parse, m, raw) == "shape"
            and refuses(m, "weights_header_invalid", parse, m, raw),
        )
    ok_raw = st_tensor_header(
        {"t": {"dtype": "U8", "shape": [0, shape_u64], "data_offsets": [0, 0]}}, 0
    )
    check(
        "R1",
        "a zero first dimension then a u64-max one is still accepted",
        outcome(m, parse, m, ok_raw) == "ok",
    )
    # a product that fits u64 but not the buffer is a size mismatch, never a crash
    raw = st_tensor_header(
        {"t": {"dtype": "F64", "shape": [2**40, 2**20], "data_offsets": [0, 8]}}, 8
    )
    check(
        "R1",
        "huge in-range product vs 8 bytes -> size_mismatch",
        reason_of(m, parse, m, raw) == "size_mismatch",
    )
    # the shape product is bounded in time: 200k+ dimensions of a huge value / of one
    for label, dims in (
        ("huge", [2**60] * 300_000),
        ("huge-after-ones", [1] * 100_000 + [2**33] * 200_000),
        ("ones", [1] * 400_000),
    ):
        raw = st_tensor_header({"t": {"dtype": "U8", "shape": dims, "data_offsets": [0, 1]}}, 1)
        start = time.perf_counter()
        got = outcome(m, parse, m, raw)
        took = time.perf_counter() - start
        check("R1", f"{label}: {len(dims)} dims took {took:.2f}s (limit 5s)", took < 5.0)
        check("R1", f"{label}: outcome {got}", got in ("ok", "weights_header_invalid"))
        if label != "ones":
            check("R1", f"{label}: refused", got == "weights_header_invalid")
    # R2: negative dimensions are refused as `shape` even when the size equation would hold
    for shape in ([-1, -1], [-2, -2], [-1, 1, -1]):
        n = 1
        for d in shape:
            n *= d
        raw = st_tensor_header(
            {"t": {"dtype": "U8", "shape": shape, "data_offsets": [0, n]}}, max(n, 0)
        )
        check(
            "R2", f"negative shape {shape!r}: reason shape", reason_of(m, parse, m, raw) == "shape"
        )
    raw = st_tensor_header({"t": {"dtype": "F32", "shape": [-1, -1], "data_offsets": [0, 4]}}, 4)
    check("R2", "[-1,-1] F32 with offsets [0,4] -> shape", reason_of(m, parse, m, raw) == "shape")

    cc = m.check_adapter_base_compatibility

    def header(tensors: dict[str, tuple[str, list[int]]]) -> Any:
        return parse(m, st_file(tensors))

    # R3: lora_A's rank is checked on its own (the base matches A [5,8] / B [16,4], so nothing
    # else can refuse it)
    base = {f"{MOD_Q}.weight": (16, 8)}
    cfg_q = m.validate_adapter_config_json(jb({**GOOD_ADAPTER_CFG, "target_modules": ["q_proj"]}))
    for label, a_shape, b_shape in (
        ("A rank 5, B rank 4", [5, 8], [16, 4]),
        ("A rank 3, B rank 4", [3, 8], [16, 4]),
        ("A rank 4, B rank 5", [4, 8], [16, 5]),
        ("A rank 4, B rank 3", [4, 8], [16, 3]),
    ):
        adapter = header(
            {
                f"base_model.model.{MOD_Q}.lora_A.weight": ("F32", a_shape),
                f"base_model.model.{MOD_Q}.lora_B.weight": ("F32", b_shape),
            }
        )
        check("R3", f"{label}: r_mismatch", reason_of(m, cc, adapter, cfg_q, base) == "r_mismatch")
    good_adapter = header(lora(m, MOD_Q, 4, 8, 16))
    check("R3", "A and B both rank r accepted", outcome(m, cc, good_adapter, cfg_q, base) == "ok")
    # R4: only ONE leading `base_model.model.` is stripped, never one found elsewhere in the name
    base_q = {f"{MOD_Q}.weight": (16, 8), "model.layers.0.q_proj.weight": (16, 8)}
    doubled = header(lora(m, MOD_Q, 4, 8, 16, prefix="base_model.model.base_model.model."))
    check(
        "R4",
        "doubled prefix -> base_tensor_missing",
        reason_of(m, cc, doubled, cfg_q, base_q) == "base_tensor_missing",
    )
    inner = header(lora(m, "model.layers.0.base_model.model.q_proj", 4, 8, 16, prefix=""))
    check(
        "R4",
        "inner prefix not stripped -> base_tensor_missing",
        reason_of(m, cc, inner, cfg_q, base_q) == "base_tensor_missing",
    )
    inner2 = header(lora(m, "model.layers.0.base_model.model.q_proj", 4, 8, 16))
    check(
        "R4",
        "single leading + inner prefix -> base_tensor_missing",
        reason_of(m, cc, inner2, cfg_q, base_q) == "base_tensor_missing",
    )


def suite(m: Any, source: str, big: bool) -> list[str]:
    _failures.clear()
    group_a(m)
    group_b(m)
    group_d(m, big)
    group_e(m)
    group_round2(m)
    group_f(m)
    group_g(m, source)
    return list(_failures)


def run_fault(
    source: str, name: str, old: str, new: str, count: int, big: bool
) -> tuple[bool, str]:
    if source.count(old) != count:
        return False, f"patch text occurs {source.count(old)} time(s), expected {count}"
    mutated = source.replace(old, new)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mutant.py"
        path.write_text(mutated)
        try:
            mm = load_module(path, f"import_artifact_validators_fault_{abs(hash(name))}")
        except BaseException as exc:  # noqa: BLE001
            return False, f"mutant does not import ({type(exc).__name__})"
        try:
            red = suite(mm, mutated, big)
        except BaseException as exc:  # noqa: BLE001 - a crashing suite is also RED
            red = [f"crash {type(exc).__name__}"]
    if not red:
        return False, "suite stayed GREEN"
    return True, f"{len(red)} failing check(s), e.g. {red[0][:100]}"


def main() -> int:
    # Neutral host: the interpreter's int-string limit switched off, so the huge-integer
    # refusals prove the module's own bound and not the host's setting.
    sys.set_int_max_str_digits(0)
    if not MODULE_PATH.exists():
        print(f"FAIL [{CID}] module missing: {MODULE_PATH}")
        return 1
    source = MODULE_PATH.read_text()
    m = load_module(MODULE_PATH, "import_artifact_validators_real")
    failures = suite(m, source, big=True)
    names = [f[0] for f in FAULTS]
    if len(set(names)) != len(names):
        failures.append("FAIL [H1] duplicate fault names")
    for name, old, new, count, big in FAULTS:
        ok, detail = run_fault(source, name, old, new, count, big)
        if ok:
            print(f"  fault {name}: RED ({detail})")
        else:
            failures.append(f"FAIL [H1] fault {name}: {detail}")
    if failures:
        print(f"{len(failures)} failing check(s):")
        for failure in failures[:80]:
            print(f"  {failure}")
        return 1
    print(
        f"OK -- {CHECK_COUNT[0]} checks in {len(FAULTS) + 1} suite runs, {len(FAULTS)} faults RED, 0 failing ({CID})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
