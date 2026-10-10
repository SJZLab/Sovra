"""FTF-0310 -- capability contract conformance checker (CAP-06, Q07, Q10).

A contract-conformance suite, not new behaviour. It validates a real response
of the live training-artifact mapping routes (FTF-0308-P1) and the capability
route (FTF-0309-P1) against the FROZEN phase-03 contract
(`packages/contracts/openapi/03-interface.yaml`), using a stdlib-only checker
over exactly the JSON Schema / OpenAPI keywords the frozen file uses. The
`jsonschema` package is not a dependency, and adding one is ask-first.

Rules the checker enforces:

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
import re
import uuid
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
    "api_reachable",
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
_SUPPORTED_FORMATS = frozenset({"uuid", "date-time"})
_TYPE_NAMES = frozenset({"string", "integer", "number", "boolean", "object", "array", "null"})
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


def _is_type(value: Any, name: str) -> bool:
    if name not in _TYPE_NAMES:
        raise UnsupportedSchemaError(f"unsupported type {name!r}")
    if name == "null":
        return value is None
    if name == "boolean":
        return type(value) is bool
    if name == "integer":
        return type(value) is int
    if name == "number":
        return type(value) in (int, float)
    if name == "string":
        return type(value) is str
    if name == "object":
        return type(value) is dict
    return type(value) is list


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


def _format_ok(value: str, name: str) -> bool:
    if name == "uuid":
        if not _UUID_RE.match(value):
            return False
        try:
            uuid.UUID(value)
        except ValueError:
            return False
        return True
    if name == "date-time":
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        if "T" not in text:
            return False
        try:
            datetime.fromisoformat(text)
        except ValueError:
            return False
        return True
    raise UnsupportedSchemaError(f"unsupported format {name!r}")


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any]) -> list[str]:
    """Return the list of violations ([] when valid). `root` is the OpenAPI
    document used to resolve `$ref`."""
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


def _deepcopy(value: Any) -> Any:
    if type(value) is dict:
        return {k: _deepcopy(v) for k, v in value.items()}
    if type(value) is list:
        return [_deepcopy(v) for v in value]
    return value


def _types_of(node: dict[str, Any], root: dict[str, Any]) -> set[str]:
    if "$ref" in node:
        return _types_of(_resolve(node["$ref"], root), root)
    if "type" in node:
        return set(node["type"]) if type(node["type"]) is list else {node["type"]}
    return set()


def _flatten(schema: dict[str, Any], instance: Any, root: dict[str, Any]) -> dict[str, Any]:
    """Merge $ref/allOf and the one matching oneOf branch into a single view
    of the constraints that apply to `instance`."""
    merged: dict[str, Any] = {
        "properties": {},
        "required": [],
        "closed": False,
        "types": None,
        "union_types": set(),
    }

    def absorb(node: dict[str, Any]) -> None:
        if "$ref" in node:
            absorb(_resolve(node["$ref"], root))
        for sub in node.get("allOf", ()):
            absorb(sub)
        if "oneOf" in node:
            for sub in node["oneOf"]:
                merged["union_types"].update(_types_of(sub, root))
            for sub in node["oneOf"]:
                if not validate(instance, sub, root):
                    absorb(sub)
                    break
        for key, value in node.get("properties", {}).items():
            merged["properties"].setdefault(key, value)
        merged["required"].extend(node.get("required", ()))
        if node.get("additionalProperties") is False:
            merged["closed"] = True
        for key in ("enum", "minLength", "maxLength", "minimum", "maximum", "items", "pattern"):
            if key in node:
                merged[key] = node[key]
        if "format" in node:
            merged["format"] = node["format"]
        if "type" in node:
            names = node["type"] if type(node["type"]) is list else [node["type"]]
            merged["types"] = names if merged["types"] is None else merged["types"]

    absorb(schema)
    if merged["union_types"]:
        # every oneOf branch's type is valid for this value, not just the matching branch's
        merged["types"] = sorted(merged["union_types"] | set(merged["types"] or ()))
    return merged


_WRONG_TYPE_SAMPLES: tuple[tuple[str, Any], ...] = (
    ("string", "x"),
    ("integer", 12345),
    ("boolean", True),
    ("null", None),
    ("array", ["x"]),
    ("object", {"x": 1}),
)


def _variants(value: str) -> list[str]:
    options = [
        value.upper(),
        value.title(),
        " " + value,
        value + " ",
        value + "\n",
        value + "​",
        "",
        value + "x",
        value[:-1],
    ]
    out: list[str] = []
    for option in options:
        if option != value and option not in out:
            out.append(option)
    return out


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
            types = view["types"]
            for type_name, sample in _WRONG_TYPE_SAMPLES:
                if types is not None and type_name in types:
                    continue
                if types is None and not view["properties"] and "enum" not in view:
                    continue  # unconstrained value (`{}`): any type is valid
                if type_name == "integer" and "number" in (types or ()):
                    continue
                cases.append(
                    NegativeCase("wrong_type", f"{label}:{type_name}", set_at(path, sample))
                )
            if "enum" in view:
                options = view["enum"]
                candidates: list[Any] = []
                if type(value) is str:
                    candidates.extend(_variants(value))
                candidates.extend(["not-in-enum", None, 0, 1, True, False])
                for candidate in candidates:
                    if any(_json_equal(candidate, option) for option in options):
                        continue
                    cases.append(
                        NegativeCase("enum", f"{label}:{candidate!r}", set_at(path, candidate))
                    )
            if type(value) is str:
                if "maxLength" in view:
                    cases.append(
                        NegativeCase(
                            "max_length", label, set_at(path, "a" * (view["maxLength"] + 1))
                        )
                    )
                if view.get("minLength", 0) >= 1:
                    cases.append(NegativeCase("min_length", label, set_at(path, "")))
                if "format" in view:
                    cases.append(
                        NegativeCase("format", label, set_at(path, "not-a-" + view["format"]))
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
                cases.append(
                    NegativeCase(
                        "extra_property", label, set_at((*path, "unexpected_extra_field"), 1)
                    )
                )
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


def api_reachable(cell: dict[str, Any]) -> bool:
    """False for a cell the public API can never produce: `mark-ready` requires
    an accepted license and `revoke-license` demotes a ready artifact to
    `registered`, so a `ready` artifact without an approved license exists only
    through a direct database write. Such a cell is still schema-checked, but its
    trainability is reported as a finding, not asserted."""
    return not (cell["status"] == "ready" and cell["license"] != "approved")
