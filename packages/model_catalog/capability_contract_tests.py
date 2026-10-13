"""FTF-0310 -- capability contract conformance checker (CAP-06, Q07, Q10).

A contract-conformance suite, not new behaviour. It validates a real response
of the live training-artifact mapping routes (FTF-0308-P1) and the capability
route (FTF-0309-P1) against the FROZEN phase-03 contract
(`packages/contracts/openapi/03-interface.yaml`), using a stdlib-only checker
over exactly the JSON Schema / OpenAPI keywords the frozen file uses. The
`jsonschema` package is not a dependency, and adding one is ask-first.

Rules the checker enforces:

- The checker scans the WHOLE reachable schema before validating, so an
  unsupported keyword, type or format, or a non-`false` `additionalProperties`,
  raises even in a branch the instance never walks.
- `date-time` is RFC 3339 only (offset mandatory, seconds mandatory).
- The contract is loaded from the one frozen path only, `load_frozen_contract`
  refuses any other file (a copy could silently drift) and returns its sha256.
- Fail closed: a schema keyword outside `SUPPORTED_KEYWORDS` /
  `ANNOTATION_KEYWORDS` raises `UnsupportedSchemaError` rather than being
  ignored, so a frozen-file change that adds a constraint is never skipped.
- Exact JSON typing: a `bool` is never an integer or a number, `enum` compares
  type and value (`True` does not match `1`), and an unhashable value never
  reaches a set or dict membership test.
- `generate_negative_cases` derives, from one VALID instance and the schema,
  a deterministic table of invalid instances: every required field removed,
  every enum field set to values outside the enum (case, whitespace, empty,
  null), every typed field set to each wrong JSON type, every bounded field
  pushed past its bound, and an extra property on every closed object.
  `assert_negative_cases_rejected` fails if the checker accepts any of them.

Path refinement (recorded): the task packet names
`packages/model-catalog/capability_contract_tests.py`; the real importable
package directory is `packages/model_catalog/`.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ANNOTATION_KEYWORDS",
    "FROZEN_CONTRACT_RELATIVE_PATH",
    "SUPPORTED_KEYWORDS",
    "ContractError",
    "FrozenContract",
    "NegativeCase",
    "UnsupportedSchemaError",
    "assert_negative_cases_rejected",
    "capability_matrix",
    "expected_trainable",
    "generate_negative_cases",
    "load_frozen_contract",
    "validate",
]

FROZEN_CONTRACT_RELATIVE_PATH = Path("packages/contracts/openapi/03-interface.yaml")

# Keywords the checker enforces.
SUPPORTED_KEYWORDS = frozenset(
    {
        "$ref",
        "allOf",
        "oneOf",
        "type",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "pattern",
        "format",
    }
)
# Keywords that carry no constraint.
ANNOTATION_KEYWORDS = frozenset(
    {"description", "default", "summary", "title", "example", "allowEmptyValue"}
)
_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "null": lambda v: v is None,
    "boolean": lambda v: type(v) is bool,
    "integer": lambda v: type(v) is int,
    "number": lambda v: type(v) in (int, float),
    "string": lambda v: type(v) is str,
    "object": lambda v: type(v) is dict,
    "array": lambda v: type(v) is list,
}
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ContractError(ValueError):
    """The frozen contract could not be loaded or is not the frozen file."""


class UnsupportedSchemaError(ValueError):
    """A schema uses a keyword or form the checker does not enforce."""


class FrozenContract:
    """The parsed frozen OpenAPI document plus the sha256 of its bytes."""

    def __init__(self, document: dict[str, Any], sha256: str, path: Path) -> None:
        self.document = document
        self.sha256 = sha256
        self.path = path

    def schema(self, name: str) -> dict[str, Any]:
        schemas = self.document["components"]["schemas"]
        if type(name) is not str or name not in schemas:
            raise ContractError(f"unknown frozen schema {name!r}")
        return {"$ref": f"#/components/schemas/{name}"}

    def response_schema(self, path: str, method: str, status: str) -> dict[str, Any]:
        """The inline response schema the frozen file declares for one operation
        (read from the frozen document, never an inline copy)."""
        try:
            schema = self.document["paths"][path][method]["responses"][status]["content"][
                "application/json"
            ]["schema"]
        except (KeyError, TypeError) as exc:
            raise ContractError(f"no frozen response schema for {method} {path} {status}") from exc
        if type(schema) is not dict:
            raise ContractError(f"frozen response schema for {method} {path} {status} is invalid")
        return schema

    def enum_values(self, name: str) -> tuple[str, ...]:
        values = self.document["components"]["schemas"][name]["enum"]
        return tuple(values)


def load_frozen_contract(repo_root: Path, path: Path | None = None) -> FrozenContract:
    """Load the one frozen file. A caller-supplied `path` is accepted only if
    it resolves to exactly the frozen file under `repo_root`."""
    frozen = (repo_root / FROZEN_CONTRACT_RELATIVE_PATH).resolve()
    chosen = frozen if path is None else Path(path).resolve()
    if chosen != frozen:
        raise ContractError(f"{chosen} is not the frozen contract {frozen}")
    if not frozen.is_file():
        raise ContractError(f"frozen contract missing: {frozen}")
    raw = frozen.read_bytes()
    document = yaml.safe_load(raw)
    if type(document) is not dict or "components" not in document:
        raise ContractError("frozen contract is not an OpenAPI document")
    return FrozenContract(document, hashlib.sha256(raw).hexdigest(), frozen)


# ---------------------------------------------------------------- validation


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/components/schemas/"
    if type(ref) is not str or not ref.startswith(prefix):
        raise UnsupportedSchemaError(f"unsupported $ref {ref!r}")
    schemas = root["components"]["schemas"]
    name = ref[len(prefix) :]
    if name not in schemas:
        raise UnsupportedSchemaError(f"unresolvable $ref {ref!r}")
    resolved: dict[str, Any] = schemas[name]
    return resolved


def _is_type(value: Any, name: Any) -> bool:
    check = _TYPE_CHECKS.get(name) if type(name) is str else None
    if check is None:
        raise UnsupportedSchemaError(f"unsupported type {name!r}")
    return check(value)


def _json_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


def _uuid_ok(value: str) -> bool:
    if not _UUID_RE.match(value):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


_RFC3339_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(\.[0-9]+)?([Zz]|[+-]([0-9]{2}):([0-9]{2}))"
)


def _datetime_ok(value: str) -> bool:
    """RFC 3339 `date-time`: full date, `T`, HH:MM:SS, optional fraction and a
    mandatory `Z` or +/-HH:MM offset. Naive, hour-only, no-seconds, `+0000`,
    ISO week/ordinal and basic (no separator) forms are all refused."""
    match = _RFC3339_RE.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    if second > 60:  # 60 is the RFC 3339 leap second
        return False
    try:
        datetime(year, month, day, hour, minute, min(second, 59))
    except ValueError:
        return False
    if match.group(9) is not None and (int(match.group(9)) > 23 or int(match.group(10)) > 59):
        return False
    return True


_FORMAT_CHECKS: dict[str, Callable[[str], bool]] = {"uuid": _uuid_ok, "date-time": _datetime_ok}


def _format_ok(value: str, name: Any) -> bool:
    check = _FORMAT_CHECKS.get(name) if type(name) is str else None
    if check is None:
        raise UnsupportedSchemaError(f"unsupported format {name!r}")
    return check(value)


def _scan_schema(node: Any, root: dict[str, Any], path: str, seen: set[str]) -> None:
    """Fail closed on the WHOLE reachable schema, not only the branches a given
    instance happens to walk: an unsupported keyword, type or format, a
    non-`false` `additionalProperties`, or a malformed container raises."""
    if type(node) is not dict:
        raise UnsupportedSchemaError(f"schema at {path} is not an object")
    unknown = set(node) - SUPPORTED_KEYWORDS - ANNOTATION_KEYWORDS
    if unknown:
        raise UnsupportedSchemaError(f"unsupported keyword(s) at {path}: {sorted(unknown)}")
    if "additionalProperties" in node and node["additionalProperties"] is not False:
        raise UnsupportedSchemaError(
            f"unsupported additionalProperties at {path}: only `false` is enforced"
        )
    if "type" in node:
        names = node["type"] if type(node["type"]) is list else [node["type"]]
        for name in names:
            if type(name) is not str or name not in _TYPE_CHECKS:
                raise UnsupportedSchemaError(f"unsupported type {name!r} at {path}")
    if "format" in node and (
        type(node["format"]) is not str or node["format"] not in _FORMAT_CHECKS
    ):
        raise UnsupportedSchemaError(f"unsupported format {node['format']!r} at {path}")
    if "$ref" in node:
        ref = node["$ref"]
        target = _resolve(ref, root)
        if ref not in seen:
            seen.add(ref)
            _scan_schema(target, root, ref, seen)
    for keyword in ("allOf", "oneOf"):
        if keyword in node:
            if type(node[keyword]) is not list:
                raise UnsupportedSchemaError(f"{keyword} at {path} is not a list")
            for index, sub in enumerate(node[keyword]):
                _scan_schema(sub, root, f"{path}.{keyword}[{index}]", seen)
    if "properties" in node:
        if type(node["properties"]) is not dict:
            raise UnsupportedSchemaError(f"properties at {path} is not an object")
        for key, sub in node["properties"].items():
            _scan_schema(sub, root, f"{path}.{key}", seen)
    if "items" in node:
        _scan_schema(node["items"], root, f"{path}[]", seen)


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any]) -> list[str]:
    """Return the list of violations ([] when valid). `root` is the OpenAPI
    document used to resolve `$ref`."""
    _scan_schema(schema, root, "$", set())
    errors: list[str] = []
    _validate(instance, schema, root, "$", errors)
    return errors


def _validate(
    instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str, errors: list[str]
) -> None:
    if type(schema) is not dict:
        raise UnsupportedSchemaError(f"schema at {path} is not an object")
    unknown = set(schema) - SUPPORTED_KEYWORDS - ANNOTATION_KEYWORDS
    if unknown:
        raise UnsupportedSchemaError(f"unsupported keyword(s) at {path}: {sorted(unknown)}")
    if "$ref" in schema:
        _validate(instance, _resolve(schema["$ref"], root), root, path, errors)
    for sub in schema.get("allOf", ()):
        _validate(instance, sub, root, path, errors)
    if "oneOf" in schema:
        matches = 0
        for sub in schema["oneOf"]:
            branch: list[str] = []
            _validate(instance, sub, root, path, branch)
            if not branch:
                matches += 1
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches, need exactly 1")
    if "type" in schema:
        names = schema["type"] if type(schema["type"]) is list else [schema["type"]]
        if not any(_is_type(instance, n) for n in names):
            errors.append(f"{path}: wrong type, expected {names}")
            return
    if "enum" in schema and not any(_json_equal(instance, option) for option in schema["enum"]):
        errors.append(f"{path}: value outside enum")
    if type(instance) is str:
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match pattern")
        if "format" in schema and not _format_ok(instance, schema["format"]):
            errors.append(f"{path}: not a valid {schema['format']}")
    if type(instance) in (int, float):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if type(instance) is dict:
        for name in schema.get("required", ()):
            if name not in instance:
                errors.append(f"{path}: missing required {name!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                _validate(value, properties[key], root, f"{path}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {key!r}")
    if type(instance) is list and "items" in schema:
        for index, item in enumerate(instance):
            _validate(item, schema["items"], root, f"{path}[{index}]", errors)


# ------------------------------------------------------- negative case table


class NegativeCase:
    """One invalid instance and the reason it is invalid."""

    def __init__(self, category: str, path: str, instance: Any) -> None:
        self.category = category
        self.path = path
        self.instance = instance

    def __repr__(self) -> str:
        return f"NegativeCase({self.category}, {self.path})"


def _same_exactly(left: Any, right: Any) -> bool:
    """Exact JSON identity for the generator's own dedupe. Deliberately NOT
    `_json_equal`: a checker whose equality is lax must not also make the
    generator skip the very candidates that would expose it."""
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def _datetime_variants(value: str) -> list[str]:
    """Non-RFC 3339 renderings of a valid date-time (naive, hour-only, no
    seconds, offset without colon, basic format, space separator, date only,
    week date, ordinal date)."""
    match = _RFC3339_RE.fullmatch(value)
    if match is None:
        return []
    date, clock = value[:10], value[11:]
    offset = match.group(8)
    body = clock[: len(clock) - len(offset)]
    hh, mm = body[:2], body[3:5]
    year, month, day = date[:4], date[5:7], date[8:10]
    compact_offset = offset.replace(":", "")
    return [
        f"{date}T{body}",
        f"{date}T{hh}{offset}",
        f"{date}T{hh}:{mm}{offset}",
        f"{date}T{body}{compact_offset}",
        f"{date}T{body}{offset[:3]}" if offset[0] in "+-" else f"{date}T{body}+00",
        f"{year}{month}{day}T{hh}{mm}{body[6:]}{offset}",
        f"{date} {body}{offset}",
        f"{date}{offset}",
        date,
        f"{year}-W41-3T{body}{offset}",
        f"{year}-280T{body}{offset}",
    ]


def _deepcopy(value: Any) -> Any:
    if type(value) is dict:
        return {k: _deepcopy(v) for k, v in value.items()}
    if type(value) is list:
        return [_deepcopy(v) for v in value]
    return value


def _allowed_types(node: dict[str, Any], root: dict[str, Any]) -> set[str] | None:
    """The JSON types `node` accepts, or None when it does not constrain the type."""
    if "$ref" in node:
        return _allowed_types(_resolve(node["$ref"], root), root)
    found: list[set[str]] = []
    if "type" in node:
        names = node["type"] if type(node["type"]) is list else [node["type"]]
        found.append(set(names))
    for sub in node.get("allOf", ()):
        sub_types = _allowed_types(sub, root)
        if sub_types is not None:
            found.append(sub_types)
    if "oneOf" in node:
        branches = [_allowed_types(sub, root) for sub in node["oneOf"]]
        if None not in branches:
            found.append(set().union(*[b for b in branches if b is not None]))
    if not found:
        return None
    allowed = found[0].intersection(*found[1:])
    if "number" in allowed:
        allowed.add("integer")
    return allowed


_BOUND_KEYWORDS = ("enum", "minLength", "maxLength", "minimum", "maximum", "items", "format")


def _flatten(schema: dict[str, Any], instance: Any, root: dict[str, Any]) -> dict[str, Any]:
    """Merge $ref/allOf and the one matching oneOf branch into a single view
    of the constraints that apply to `instance`."""
    merged: dict[str, Any] = {"properties": {}, "required": [], "closed": False}

    def absorb(node: dict[str, Any]) -> None:
        if "$ref" in node:
            absorb(_resolve(node["$ref"], root))
        for sub in node.get("allOf", ()):
            absorb(sub)
        for sub in node.get("oneOf", ()):
            if not validate(instance, sub, root):
                absorb(sub)
                break
        for key, value in node.get("properties", {}).items():
            merged["properties"].setdefault(key, value)
        merged["required"].extend(node.get("required", ()))
        if node.get("additionalProperties") is False:
            merged["closed"] = True
        for keyword in _BOUND_KEYWORDS:
            if keyword in node:
                merged[keyword] = node[keyword]

    absorb(schema)
    return merged


_WRONG_TYPE_SAMPLES: dict[str, Any] = {
    "string": "x",
    "integer": 12345,
    "boolean": True,
    "null": None,
    "array": ["x"],
    "object": {"x": 1},
}


def _variants(value: str) -> list[str]:
    return [
        value.upper(),
        value.title(),
        " " + value,
        value + " ",
        value + "\n",
        value + "\u200b",
        "",
        value + "x",
        value[:-1],
    ]


def generate_negative_cases(
    valid_instance: Any, schema: dict[str, Any], root: dict[str, Any]
) -> list[NegativeCase]:
    """Deterministic table of invalid instances derived from one valid one."""
    base = valid_instance
    if validate(base, schema, root):
        raise ContractError("the seed instance must be valid against the schema")
    cases: list[NegativeCase] = []

    def set_at(path: tuple[Any, ...], value: Any, *, delete: bool = False) -> Any:
        clone = _deepcopy(base)
        node = clone
        for step in path[:-1]:
            node = node[step]
        if delete:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        return clone

    def walk(value: Any, sub_schema: dict[str, Any], path: tuple[Any, ...]) -> None:
        view = _flatten(sub_schema, value, root)
        label = "$" + "".join(f"[{p!r}]" if type(p) is int else f".{p}" for p in path)
        if path:
            allowed = _allowed_types(sub_schema, root)
            if allowed is not None:
                for type_name in sorted(set(_WRONG_TYPE_SAMPLES) - allowed):
                    sample = _WRONG_TYPE_SAMPLES[type_name]
                    cases.append(
                        NegativeCase("wrong_type", f"{label}:{type_name}", set_at(path, sample))
                    )
            if "enum" in view:
                candidates: list[Any] = _variants(value) if type(value) is str else []
                candidates.extend(["not-in-enum", None, 0, 1, True, False])
                tried: list[Any] = list(view["enum"])
                for candidate in candidates:
                    if any(_same_exactly(candidate, other) for other in tried):
                        continue
                    tried.append(candidate)
                    cases.append(
                        NegativeCase("enum", f"{label}:{candidate!r}", set_at(path, candidate))
                    )
            if type(value) is str:
                if "maxLength" in view:
                    too_long = "a" * (view["maxLength"] + 1)
                    cases.append(NegativeCase("max_length", label, set_at(path, too_long)))
                if view.get("minLength", 0) >= 1:
                    cases.append(NegativeCase("min_length", label, set_at(path, "")))
                if "format" in view:
                    bad = "not-a-" + view["format"]
                    cases.append(NegativeCase("format", label, set_at(path, bad)))
                    if view["format"] == "date-time":
                        for variant in _datetime_variants(value):
                            cases.append(
                                NegativeCase(
                                    "format_datetime", f"{label}:{variant!r}", set_at(path, variant)
                                )
                            )
            if type(value) is int and "minimum" in view:
                cases.append(NegativeCase("minimum", label, set_at(path, view["minimum"] - 1)))
        if type(value) is dict:
            for name in sorted(set(view["required"])):
                if name in value:
                    cases.append(
                        NegativeCase(
                            "missing_required",
                            f"{label}.{name}",
                            set_at((*path, name), None, delete=True),
                        )
                    )
            if view["closed"]:
                extra = set_at((*path, "unexpected_extra_field"), 1)
                cases.append(NegativeCase("extra_property", label, extra))
            for key, child in value.items():
                if key in view["properties"]:
                    walk(child, view["properties"][key], (*path, key))
        if type(value) is list and "items" in view:
            for index, child in enumerate(value):
                walk(child, view["items"], (*path, index))

    walk(base, schema, ())
    return cases


def assert_negative_cases_rejected(
    cases: list[NegativeCase], schema: dict[str, Any], root: dict[str, Any]
) -> list[str]:
    """Return one message per case the checker wrongly accepted."""
    return [
        f"accepted invalid case {case!r}"
        for case in cases
        if not validate(case.instance, schema, root)
    ]


# ------------------------------------------------------------ capability matrix


def capability_matrix(
    source_types: tuple[str, ...], statuses: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Every (source type, artifact status, license state, mapping) cell. The
    caller passes the FROZEN enums, so a dropped variant shrinks the matrix and
    the coverage assertion fails."""
    cells: list[dict[str, Any]] = []
    for source_type in source_types:
        for status in statuses:
            for license_state in ("approved", "pending", "revoked"):
                for mapped in (True, False):
                    cells.append(
                        {
                            "source_type": source_type,
                            "status": status,
                            "license": license_state,
                            "mapped": mapped,
                        }
                    )
    return cells


def expected_trainable(cell: dict[str, Any]) -> bool:
    """The frozen rule: trainable only for a mapped, ready artifact whose
    license is approved."""
    return bool(cell["mapped"] and cell["status"] == "ready" and cell["license"] == "approved")
