"""FTF-R001 -- reuse-architecture / version-lock schema validator.

Validates instance data (a ComponentBindingSet + ReleaseApprovalAuthority
list, or a ComponentVersionLock set) against the frozen JSON Schema
contracts in `packages/component-contracts/architecture.schema.json` and
`spec/component-lock.schema.json`.

`validate_instance` is a bounded JSON Schema validator matched to exactly
the keywords those two frozen schemas use (`type`, `enum`, `const`,
`pattern`, `minLength`, `required`, `properties`, `additionalProperties`,
`items`, `minItems`/`maxItems`, `allOf`/`if`/`then`, local `$ref`) -- not a
general-purpose JSON Schema engine.

`validate_component_binding_document` and `validate_component_lock_document`
add the one whole-set business rule no single-record schema keyword can
express: the seven bindings/locks must be exactly the seven canonical
component keys, each exactly once -- never missing, duplicated or
substituted for an invented key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE_SCHEMA_PATH = (
    REPO_ROOT / "packages" / "component-contracts" / "architecture.schema.json"
)
COMPONENT_LOCK_SCHEMA_PATH = REPO_ROOT / "spec" / "component-lock.schema.json"

CANONICAL_COMPONENT_KEYS = frozenset(
    {"axolotl", "inspect_ai", "mlflow", "vllm", "temporal", "litellm", "argilla"}
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref pointers are supported, got {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValueError(f"unsupported schema type {expected!r}")


def validate_instance(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    root = schema if root is None else root
    if "$ref" in schema:
        schema = _resolve_ref(schema["$ref"], root)

    errors: list[str] = []

    if "type" in schema:
        expected_types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(instance, t) for t in expected_types):
            errors.append(f"{path}: expected type {schema['type']}, got {instance!r}")
            return errors  # further structural checks would be meaningless

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, str):
        if "pattern" in schema and re.fullmatch(schema["pattern"], instance) is None:
            errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            min_length = schema["minLength"]
            errors.append(f"{path}: length {len(instance)} is below minLength {min_length}")

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for required_key in schema.get("required", []):
            if required_key not in instance:
                errors.append(f"{path}: missing required property {required_key!r}")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, value in instance.items():
            if key in props:
                errors.extend(validate_instance(value, props[key], root, f"{path}.{key}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            min_items = schema["minItems"]
            errors.append(f"{path}: has {len(instance)} item(s), fewer than minItems {min_items}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            max_items = schema["maxItems"]
            errors.append(f"{path}: has {len(instance)} item(s), more than maxItems {max_items}")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                errors.extend(validate_instance(item, item_schema, root, f"{path}[{index}]"))

    for sub in schema.get("allOf", []):
        if "if" in sub:
            if not validate_instance(instance, sub["if"], root, path):
                errors.extend(validate_instance(instance, sub.get("then", {}), root, path))
        else:
            errors.extend(validate_instance(instance, sub, root, path))

    return errors


def _check_exact_key_set(keys: list[Any], record_noun: str, verb_past: str) -> list[str]:
    """negative_acceptance: 'missing/extra substituted component' -- the one
    whole-set rule no per-record schema keyword can express."""
    errors: list[str] = []
    seen: set[Any] = set()
    for key in keys:
        if key is None:
            continue
        if key in seen:
            errors.append(f"duplicate_component: {key!r} is {verb_past} more than once")
        seen.add(key)
    for key in sorted(CANONICAL_COMPONENT_KEYS - seen):
        errors.append(f"missing_component: {key!r} has no {record_noun}")
    for key in sorted((seen - CANONICAL_COMPONENT_KEYS), key=str):
        errors.append(f"extra_component: {key!r} is not one of the seven canonical components")
    return errors


def validate_component_binding_document(document: dict[str, Any]) -> list[str]:
    schema = load_json(ARCHITECTURE_SCHEMA_PATH)
    errors = validate_instance(document, schema)
    bindings = document.get("component_bindings", [])
    keys = [b.get("component_key") for b in bindings if isinstance(b, dict)]
    errors.extend(_check_exact_key_set(keys, "binding", "bound"))
    return errors


def validate_component_lock_document(document: dict[str, Any]) -> list[str]:
    schema = load_json(COMPONENT_LOCK_SCHEMA_PATH)
    errors = validate_instance(document, schema)
    locks = document.get("component_version_locks", [])
    keys = [entry.get("component_key") for entry in locks if isinstance(entry, dict)]
    errors.extend(_check_exact_key_set(keys, "version lock", "locked"))
    return errors
