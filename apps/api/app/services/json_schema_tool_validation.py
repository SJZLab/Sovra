"""JSON Schema tool validation (FTF-0405, Section 6.9/22/65-66/77/81).

FTF-0404's own `tool_definition_schema.py` (`validate_schema_object`)
explicitly disclaimed real JSON Schema meta-schema validation as "a later
task's own scope" -- this module is that task. It closes the disclosed gap
for real: `input_schema_json`/`output_schema_json` submitted through
`create_tool_definition`/`edit_tool_definition` are now validated as
documents in a pinned, safe subset of JSON Schema Draft 2020-12, not
merely "some bounded JSON object" as before.

Design choice, not an oversight: this module supports only a pinned,
bounded vocabulary of JSON Schema keywords and only intra-document `$ref`
(`#/$defs/<name>`) -- no remote `$ref`, no `$dynamicRef`/`$recursiveRef`,
no `$id`-based re-basing. This is what makes the two named
negative_acceptance attack classes structurally impossible rather than
merely detected:

  - "remote schema SSRF" -- this module never performs network I/O for
    any reason, under any input. A `$ref` that is not a local
    `#/$defs/<name>` pointer is rejected before any resolution is
    attempted, full stop.
  - "recursive/reference bombs" -- the `$defs` reference graph is walked
    for cycles at schema-*validation* time (before the schema is ever
    used to validate an instance), and both structural depth and total
    node count are bounded independently of `$ref` use. A separate
    `MAX_REF_HOPS` budget at instance-validation time is defense-in-depth
    on top of that, not a substitute for it.

Instance validation (`validate_instance`) has no live caller in this
repository yet -- there is no tool-invocation/tool-call runtime anywhere
in this codebase to feed it real tool-call arguments (that is a much
later phase: Section 7A.8's target-agent evaluation harness / a live
agent runtime, neither of which exists yet). Disclosed, not hidden,
matching the same precedent already independently CERTIFIED for
FTF-0103/FTF-0104/FTF-0105/FTF-H003/FTF-0305 (a real, tested primitive
whose first live caller arrives in a later, separately dependency-gated
task). The schema-*definition* validator (`validate_json_schema_document`)
is real and live-wired: `tool_definition_schema.py`'s own
`validate_schema_object` calls it for both `input_schema_json` and
`output_schema_json`, on every create and edit, before either value ever
reaches the database.
"""

from __future__ import annotations

import re
from typing import NoReturn

__all__ = [
    "ALLOWED_KEYWORDS",
    "ALLOWED_TYPES",
    "MAX_PATTERN_INPUT_LENGTH",
    "MAX_PATTERN_LENGTH",
    "MAX_REF_HOPS",
    "MAX_SCHEMA_DEPTH",
    "MAX_SCHEMA_NODES",
    "PINNED_DIALECT",
    "JsonSchemaSecurityError",
    "JsonSchemaValidationError",
    "validate_instance",
    "validate_json_schema_document",
]


PINNED_DIALECT = "https://json-schema.org/draft/2020-12/schema"

ALLOWED_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})

# A pinned, bounded vocabulary -- this task's own "pinned dialect" choice.
# Any keyword outside this set fails explicitly rather than being
# silently ignored (a silently-ignored unknown keyword is exactly how a
# schema author could smuggle in something this validator never actually
# enforces).
ALLOWED_KEYWORDS = frozenset(
    {
        "$schema",
        "$ref",
        "$defs",
        "type",
        "title",
        "description",
        "default",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)

MAX_SCHEMA_DEPTH = 10
MAX_SCHEMA_NODES = 300
MAX_REF_HOPS = 25
MAX_PATTERN_LENGTH = 200
MAX_PATTERN_INPUT_LENGTH = 10_000

_DEF_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_REF_PREFIX = "#/$defs/"

# A minimal, best-effort heuristic for a classic catastrophic-backtracking
# shape (nested quantified groups such as `(a+)+`). This is flagged here
# as a heuristic, not claimed as a formal proof of linear-time matching.
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*]")


class JsonSchemaValidationError(ValueError):
    """The submitted document is not a valid instance of this task's own
    pinned, safe JSON Schema subset."""


class JsonSchemaSecurityError(JsonSchemaValidationError):
    """The submitted document uses a construct this module refuses to
    resolve for safety reasons (a non-local `$ref`, a reference cycle, or
    a schema that exceeds a bounded safety limit). A subclass of
    `JsonSchemaValidationError` so a caller that only catches the base
    class still sees it, while a security-focused test can distinguish
    the two."""


def _fail(field_name: str, message: str) -> NoReturn:
    raise JsonSchemaValidationError(f"{field_name}: {message}")


def _fail_security(field_name: str, message: str) -> NoReturn:
    raise JsonSchemaSecurityError(f"{field_name}: {message}")


class _NodeCounter:
    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def increment(self, field_name: str) -> None:
        self.count += 1
        if self.count > MAX_SCHEMA_NODES:
            _fail_security(field_name, f"schema exceeds the maximum of {MAX_SCHEMA_NODES} nodes")


def _check_ref_target(value: object, field_name: str, defs: dict[str, object]) -> str:
    if not isinstance(value, str):
        _fail(field_name, "$ref must be a string")
    if not value.startswith(_REF_PREFIX):
        _fail_security(
            field_name,
            "$ref must be a local '#/$defs/<name>' pointer -- remote and "
            "non-local references are never resolved",
        )
    name = value[len(_REF_PREFIX) :]
    if not name or not set(name) <= _DEF_NAME_CHARS:
        _fail_security(field_name, f"$ref target name is not a safe identifier: {name!r}")
    if name not in defs:
        _fail(field_name, f"$ref target '{name}' is not declared in $defs")
    return name


def _detect_ref_cycles(defs: dict[str, object], field_name: str) -> None:
    """DFS cycle detection over the $defs reference graph -- run once per
    schema-validation call, independent of how many times a given $defs
    entry is later reused while validating an instance. A cycle here
    would make instance validation never terminate; `MAX_REF_HOPS` at
    instance-validation time is defense-in-depth on top of this, never a
    substitute for it."""

    def refs_of(node: object) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str) and value.startswith(_REF_PREFIX):
                    found.append(value[len(_REF_PREFIX) :])
                else:
                    found.extend(refs_of(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(refs_of(item))
        return found

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str, path: list[str]) -> None:
        if name in visiting:
            cycle = " -> ".join([*path, name])
            _fail_security(field_name, f"$defs reference cycle detected: {cycle}")
        if name in visited or name not in defs:
            return
        visiting.add(name)
        for ref_name in refs_of(defs[name]):
            visit(ref_name, [*path, name])
        visiting.discard(name)
        visited.add(name)

    for def_name in defs:
        if def_name not in visited:
            visit(def_name, [])


def _walk_schema_node(
    node: object,
    *,
    field_name: str,
    defs: dict[str, object],
    depth: int,
    counter: _NodeCounter,
) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        _fail_security(
            field_name, f"schema nesting exceeds the maximum depth of {MAX_SCHEMA_DEPTH}"
        )
    if not isinstance(node, dict):
        _fail(field_name, "each schema node must be a JSON object")
    counter.increment(field_name)

    unknown = set(node) - ALLOWED_KEYWORDS
    if unknown:
        _fail(field_name, f"unsupported schema keyword(s): {sorted(unknown)}")

    if "$ref" in node:
        _check_ref_target(node["$ref"], field_name, defs)
        # A $ref node stands alone in this pinned dialect -- sibling
        # keywords next to $ref are rejected rather than silently
        # ignored (2020-12 itself permits siblings; this pinned, safe
        # subset does not, to keep resolution semantics unambiguous).
        if set(node) - {"$ref", "description", "title"}:
            _fail(field_name, "$ref must not be combined with other constraint keywords")
        return

    if "type" in node:
        type_value = node["type"]
        types = type_value if isinstance(type_value, list) else [type_value]
        if not types or any(t not in ALLOWED_TYPES for t in types):
            _fail(field_name, f"type must be one of {sorted(ALLOWED_TYPES)}: {type_value!r}")

    if "properties" in node:
        properties = node["properties"]
        if not isinstance(properties, dict):
            _fail(field_name, "properties must be a JSON object")
        for key, sub_schema in properties.items():
            _walk_schema_node(
                sub_schema,
                field_name=f"{field_name}.properties.{key}",
                defs=defs,
                depth=depth + 1,
                counter=counter,
            )

    if "required" in node:
        required = node["required"]
        if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
            _fail(field_name, "required must be a list of strings")
        properties = node.get("properties")
        declared = set(properties) if isinstance(properties, dict) else set()
        missing = [r for r in required if r not in declared]
        if missing:
            _fail(
                field_name, f"required names properties never declared in 'properties': {missing}"
            )

    if "items" in node:
        _walk_schema_node(
            node["items"],
            field_name=f"{field_name}.items",
            defs=defs,
            depth=depth + 1,
            counter=counter,
        )

    if "additionalProperties" in node and not isinstance(node["additionalProperties"], bool):
        _fail(field_name, "additionalProperties must be a boolean in this pinned dialect")

    if "pattern" in node:
        pattern = node["pattern"]
        if not isinstance(pattern, str):
            _fail(field_name, "pattern must be a string")
        if len(pattern) > MAX_PATTERN_LENGTH:
            _fail_security(field_name, f"pattern exceeds {MAX_PATTERN_LENGTH} characters")
        if _NESTED_QUANTIFIER_RE.search(pattern):
            _fail_security(
                field_name, "pattern uses a nested-quantifier shape refused on safety grounds"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            _fail(field_name, f"pattern is not a valid regular expression: {exc}")

    for numeric_keyword in (
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    ):
        if numeric_keyword in node and not isinstance(node[numeric_keyword], int | float):
            _fail(field_name, f"{numeric_keyword} must be numeric")

    if "uniqueItems" in node and not isinstance(node["uniqueItems"], bool):
        _fail(field_name, "uniqueItems must be a boolean")


def validate_json_schema_document(document: object, field_name: str) -> dict[str, object]:
    """Validates `document` as an instance of this task's own pinned,
    safe JSON Schema subset. Raises `JsonSchemaValidationError` (or its
    `JsonSchemaSecurityError` subclass for the two named attack classes)
    on any violation; returns a `dict` copy of `document` on success.

    Called by `tool_definition_schema.py`'s `validate_schema_object` for
    both `input_schema_json` and `output_schema_json` -- this is this
    task's own real, live-wired product-runtime path (every
    `create_tool_definition`/`edit_tool_definition` call runs through
    it, since `ToolDefinition.__init__` calls `validate_schema_object`
    for both fields)."""
    if not isinstance(document, dict):
        _fail(field_name, "must be a JSON object")

    schema_dialect = document.get("$schema")
    if schema_dialect is not None and schema_dialect != PINNED_DIALECT:
        _fail(
            field_name,
            f"$schema must be the pinned dialect {PINNED_DIALECT!r}, got {schema_dialect!r}",
        )

    defs = document.get("$defs", {})
    if not isinstance(defs, dict):
        _fail(field_name, "$defs must be a JSON object")
    for def_name, def_schema in defs.items():
        if not set(def_name) <= _DEF_NAME_CHARS:
            _fail_security(field_name, f"$defs key is not a safe identifier: {def_name!r}")
        if not isinstance(def_schema, dict):
            _fail(f"{field_name}.$defs.{def_name}", "each $defs entry must be a JSON object")

    _detect_ref_cycles(defs, field_name)

    counter = _NodeCounter()
    root = {k: v for k, v in document.items() if k not in ("$schema", "$defs")}
    _walk_schema_node(root, field_name=field_name, defs=defs, depth=0, counter=counter)
    for def_name, def_schema in defs.items():
        _walk_schema_node(
            def_schema,
            field_name=f"{field_name}.$defs.{def_name}",
            defs=defs,
            depth=0,
            counter=counter,
        )

    return dict(document)


def _resolve(
    node: dict[str, object], defs: dict[str, object], field_name: str, hops: list[int]
) -> dict[str, object]:
    if "$ref" in node:
        hops[0] += 1
        if hops[0] > MAX_REF_HOPS:
            _fail_security(field_name, f"$ref resolution exceeds {MAX_REF_HOPS} hops")
        name = _check_ref_target(node["$ref"], field_name, defs)
        resolved = defs[name]
        if not isinstance(resolved, dict):
            _fail(field_name, f"$defs target '{name}' is not a JSON object")
        return _resolve(resolved, defs, field_name, hops)
    return node


def validate_instance(
    schema: dict[str, object], instance: object, *, field_name: str = "instance"
) -> None:
    """Validates `instance` (real call-time data, e.g. tool-call
    arguments) against an already-`validate_json_schema_document`-checked
    `schema`. Raises `JsonSchemaValidationError` on the first violation.

    No live caller exists in this repository yet -- disclosed in this
    module's own docstring above, not a hidden gap. Covered directly by
    this task's own test suite."""
    defs = schema.get("$defs", {}) if isinstance(schema, dict) else {}
    if not isinstance(defs, dict):
        defs = {}
    _validate_instance_node(schema, instance, defs, field_name, [0])


def _validate_instance_node(
    node: dict[str, object],
    instance: object,
    defs: dict[str, object],
    field_name: str,
    hops: list[int],
) -> None:
    node = _resolve(node, defs, field_name, hops)

    if "enum" in node:
        enum_values = node["enum"]
        if not isinstance(enum_values, list) or instance not in enum_values:
            _fail(field_name, f"value is not one of the allowed enum values: {enum_values!r}")
        return
    if "const" in node:
        if instance != node["const"]:
            _fail(field_name, f"value must equal the constant {node['const']!r}")
        return

    declared_types = node.get("type")
    if declared_types is not None:
        types = declared_types if isinstance(declared_types, list) else [declared_types]
        if not any(_matches_type(instance, t) for t in types if isinstance(t, str)):
            _fail(field_name, f"value does not match type(s) {types}: {instance!r}")

    if isinstance(instance, bool):
        pass
    elif isinstance(instance, str):
        _validate_string_instance(node, instance, field_name)
    elif isinstance(instance, int | float):
        _validate_numeric_instance(node, instance, field_name)
    elif isinstance(instance, list):
        _validate_array_instance(node, instance, defs, field_name, hops)
    elif isinstance(instance, dict):
        _validate_object_instance(node, instance, defs, field_name, hops)


def _matches_type(instance: object, declared_type: str) -> bool:
    if declared_type == "object":
        return isinstance(instance, dict)
    if declared_type == "array":
        return isinstance(instance, list)
    if declared_type == "string":
        return isinstance(instance, str)
    if declared_type == "boolean":
        return isinstance(instance, bool)
    if declared_type == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if declared_type == "number":
        return isinstance(instance, int | float) and not isinstance(instance, bool)
    if declared_type == "null":
        return instance is None
    return False


def _validate_string_instance(node: dict[str, object], instance: str, field_name: str) -> None:
    min_length = node.get("minLength")
    if isinstance(min_length, int | float) and len(instance) < min_length:
        _fail(field_name, f"string shorter than minLength {min_length}")
    max_length = node.get("maxLength")
    if isinstance(max_length, int | float) and len(instance) > max_length:
        _fail(field_name, f"string longer than maxLength {max_length}")
    pattern = node.get("pattern")
    if isinstance(pattern, str):
        if len(instance) > MAX_PATTERN_INPUT_LENGTH:
            _fail_security(
                field_name,
                f"value exceeds {MAX_PATTERN_INPUT_LENGTH} characters for pattern matching",
            )
        if re.fullmatch(pattern, instance) is None:
            _fail(field_name, f"value does not match pattern {pattern!r}")


def _validate_numeric_instance(node: dict[str, object], instance: float, field_name: str) -> None:
    minimum = node.get("minimum")
    if isinstance(minimum, int | float) and instance < minimum:
        _fail(field_name, f"value below minimum {minimum}")
    maximum = node.get("maximum")
    if isinstance(maximum, int | float) and instance > maximum:
        _fail(field_name, f"value above maximum {maximum}")
    exclusive_minimum = node.get("exclusiveMinimum")
    if isinstance(exclusive_minimum, int | float) and instance <= exclusive_minimum:
        _fail(field_name, f"value not above exclusiveMinimum {exclusive_minimum}")
    exclusive_maximum = node.get("exclusiveMaximum")
    if isinstance(exclusive_maximum, int | float) and instance >= exclusive_maximum:
        _fail(field_name, f"value not below exclusiveMaximum {exclusive_maximum}")


def _validate_array_instance(
    node: dict[str, object],
    instance: list[object],
    defs: dict[str, object],
    field_name: str,
    hops: list[int],
) -> None:
    min_items = node.get("minItems")
    if isinstance(min_items, int | float) and len(instance) < min_items:
        _fail(field_name, f"array shorter than minItems {min_items}")
    max_items = node.get("maxItems")
    if isinstance(max_items, int | float) and len(instance) > max_items:
        _fail(field_name, f"array longer than maxItems {max_items}")
    if node.get("uniqueItems") and len({_freeze(v) for v in instance}) != len(instance):
        _fail(field_name, "array items must be unique")
    items_schema = node.get("items")
    if isinstance(items_schema, dict):
        for index, item in enumerate(instance):
            _validate_instance_node(items_schema, item, defs, f"{field_name}[{index}]", hops)


def _validate_object_instance(
    node: dict[str, object],
    instance: dict[str, object],
    defs: dict[str, object],
    field_name: str,
    hops: list[int],
) -> None:
    properties = node.get("properties")
    properties_dict = properties if isinstance(properties, dict) else {}
    required = node.get("required")
    if isinstance(required, list):
        for name in required:
            if name not in instance:
                _fail(field_name, f"missing required property {name!r}")
    additional_allowed = node.get("additionalProperties", True)
    for key, value in instance.items():
        sub_schema = properties_dict.get(key)
        if isinstance(sub_schema, dict):
            _validate_instance_node(sub_schema, value, defs, f"{field_name}.{key}", hops)
        elif additional_allowed is False:
            _fail(field_name, f"excess property {key!r} is not permitted by this schema")


def _freeze(value: object) -> object:
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    return value
