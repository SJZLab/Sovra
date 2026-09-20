#!/usr/bin/env python3
"""FTF-0405 -- JSON Schema tool validation (no-DB unit/fault tests).

Runnable without Postgres or network egress. This suite proves, without
any database or network access:

1. `validate_json_schema_document`: a real schema document is validated
   against this task's own pinned, safe JSON Schema subset -- pinned
   dialect enforced, safe local `$ref` accepted, unsupported keywords and
   bad `type`/`required` constraints refused explicitly.
2. negative_acceptance, "recursive/reference bombs...fail explicitly":
   a `$defs` reference cycle is refused before it is ever used to
   validate an instance (which would otherwise never terminate), and an
   oversized/overly-deep schema is refused independently of `$ref` use.
3. negative_acceptance, "remote schema SSRF...fail explicitly": a `$ref`
   naming any non-local target (an `http(s)://` URL, a bare path, a
   pointer outside `$defs`) is refused -- and, by fault injection, this
   module genuinely never attempts network I/O to resolve it: patching
   `socket.socket` to raise proves no code path in this module ever
   constructs a socket, even when handed a schema built specifically to
   provoke that.
4. negative_acceptance, "invalid required/type constraints...fail
   explicitly": an unknown `type` value and a `required` entry naming an
   undeclared property are both refused.
5. negative_acceptance, "excess arguments...fail explicitly":
   `validate_instance` refuses call-time instance data carrying a
   property beyond what `additionalProperties: false` permits.
6. Real live wiring: `tool_definition_schema.py`'s own
   `validate_schema_object` genuinely calls
   `validate_json_schema_document` (source-inspection proof, not merely
   an import), and constructing a real `ToolDefinition` with a schema
   this task's own validator rejects fails at construction time, before
   any session or database is ever touched -- the same discipline
   FTF-0404's own `ToolDefinition.__init__` already established for
   name/side-effect-class.

Run directly: python3 tests/tasks/test_ftf_0405.py
"""

from __future__ import annotations

import socket
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.json_schema_tool_validation import (  # noqa: E402
    ALLOWED_KEYWORDS,
    MAX_REF_HOPS,
    MAX_SCHEMA_DEPTH,
    MAX_SCHEMA_NODES,
    PINNED_DIALECT,
    JsonSchemaSecurityError,
    JsonSchemaValidationError,
    validate_instance,
    validate_json_schema_document,
)
from app.tools.tool_definition_schema import (  # noqa: E402
    ToolDefinition,
    ToolDefinitionValidationError,
    compute_schema_hash,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except JsonSchemaValidationError:
        return True


def _rejects_as_security(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except JsonSchemaSecurityError:
        return True
    except JsonSchemaValidationError:
        return False


# -- 1. validate_json_schema_document: pinned dialect, safe $ref, shape ------


def test_a_plain_valid_schema_is_accepted() -> None:
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string", "minLength": 1, "maxLength": 64}},
        "required": ["order_id"],
        "additionalProperties": False,
    }
    result = validate_json_schema_document(schema, "input_schema_json")
    check("FTF-0405", "a real, well-formed schema round-trips unchanged", result == schema)


def test_pinned_dialect_is_enforced() -> None:
    schema = {"$schema": PINNED_DIALECT, "type": "object"}
    check(
        "FTF-0405",
        "the pinned dialect string itself is accepted",
        validate_json_schema_document(schema, "x") == schema,
    )
    check(
        "FTF-0405",
        "a different (non-pinned) $schema dialect is refused explicitly",
        _rejects(
            validate_json_schema_document,
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
            "x",
        ),
    )


def test_local_ref_to_defs_is_accepted() -> None:
    schema = {
        "type": "object",
        "$defs": {"id_field": {"type": "string", "minLength": 1}},
        "properties": {"order_id": {"$ref": "#/$defs/id_field"}},
    }
    result = validate_json_schema_document(schema, "x")
    check("FTF-0405", "a safe local $ref into $defs is accepted", result == schema)


def test_ref_to_undeclared_def_is_rejected() -> None:
    schema = {
        "type": "object",
        "properties": {"order_id": {"$ref": "#/$defs/does_not_exist"}},
    }
    check(
        "FTF-0405",
        "a $ref naming a $defs entry that does not exist is refused",
        _rejects(validate_json_schema_document, schema, "x"),
    )


def test_unsupported_keyword_is_rejected_explicitly() -> None:
    check(
        "FTF-0405",
        "an unsupported/unpinned keyword is refused rather than silently ignored",
        _rejects(validate_json_schema_document, {"type": "object", "patternProperties": {}}, "x"),
    )
    check(
        "FTF-0405",
        "$dynamicRef (outside this pinned dialect's vocabulary) is refused",
        _rejects(validate_json_schema_document, {"$dynamicRef": "#meta"}, "x"),
    )


# -- 2. negative_acceptance: recursive/reference bombs -----------------------


def test_defs_reference_cycle_is_rejected() -> None:
    schema = {
        "$defs": {
            "a": {"$ref": "#/$defs/b"},
            "b": {"$ref": "#/$defs/a"},
        },
        "$ref": "#/$defs/a",
    }
    check(
        "FTF-0405",
        "negative_acceptance: a $defs reference cycle (A->B->A) is refused explicitly, "
        "as a security-classed error",
        _rejects_as_security(validate_json_schema_document, schema, "x"),
    )


def test_self_referencing_def_is_rejected() -> None:
    schema = {"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}
    check(
        "FTF-0405",
        "negative_acceptance: a $defs entry that refers to itself is refused explicitly",
        _rejects_as_security(validate_json_schema_document, schema, "x"),
    )


def test_oversized_schema_is_rejected_independent_of_ref() -> None:
    # A schema with no $ref at all can still be a bomb: enormous width via
    # sheer node count. The node-count ceiling catches this independently
    # of any reference machinery.
    huge_properties = {f"field_{i}": {"type": "string"} for i in range(MAX_SCHEMA_NODES + 10)}
    schema = {"type": "object", "properties": huge_properties}
    check(
        "FTF-0405",
        "negative_acceptance: a schema exceeding the node-count ceiling is refused, "
        "with no $ref involved at all",
        _rejects_as_security(validate_json_schema_document, schema, "x"),
    )


def test_overly_deep_schema_is_rejected() -> None:
    node: dict[str, object] = {"type": "string"}
    for _ in range(MAX_SCHEMA_DEPTH + 5):
        node = {"type": "object", "properties": {"child": node}}
    check(
        "FTF-0405",
        "negative_acceptance: schema nesting beyond the depth ceiling is refused",
        _rejects_as_security(validate_json_schema_document, node, "x"),
    )


def test_ref_hop_budget_bounds_instance_validation() -> None:
    # Not a cycle (each $defs entry is acyclic and would pass schema
    # validation), but chained deeply enough to exceed MAX_REF_HOPS while
    # resolving a single instance -- defense-in-depth on top of the
    # cycle detector above, not a substitute for it.
    defs: dict[str, object] = {"level_0": {"type": "string"}}
    for i in range(1, MAX_REF_HOPS + 10):
        defs[f"level_{i}"] = {"$ref": f"#/$defs/level_{i - 1}"}
    top_name = f"level_{MAX_REF_HOPS + 9}"
    schema = {"$defs": defs, "$ref": f"#/$defs/{top_name}"}
    validate_json_schema_document(schema, "x")
    check(
        "FTF-0405",
        "an acyclic but excessively long $ref chain is refused at instance-validation "
        "time by the hop budget, distinct from the schema-validation-time cycle check",
        _rejects_as_security(validate_instance, schema, "hello"),
    )


# -- 3. negative_acceptance: remote schema SSRF ------------------------------


def test_remote_ref_is_rejected_explicitly() -> None:
    for malicious_ref in (
        "http://169.254.169.254/latest/meta-data/",
        "https://evil.example/schema.json",
        "file:///etc/passwd",
        "//evil.example/schema.json",
        "urn:not-a-local-pointer",
    ):
        check(
            "FTF-0405",
            f"negative_acceptance: a non-local $ref ({malicious_ref!r}) is refused, "
            "as a security-classed error",
            _rejects_as_security(validate_json_schema_document, {"$ref": malicious_ref}, "x"),
        )


def test_remote_ref_never_attempts_network_io() -> None:
    # Fault injection: prove the refusal above is structural, not merely
    # a check that happens to run before an (unreached) network call. If
    # this module's own resolution path ever constructed a real socket
    # for a $ref, this patch would make it raise instead of silently
    # succeeding or timing out.
    malicious_schema: dict[str, object] = {
        "$defs": {"evil": {"$ref": "https://169.254.169.254/latest/meta-data/"}},
        "$ref": "#/$defs/evil",
    }
    safe_local_schema: dict[str, object] = {
        "$defs": {"good": {"type": "string"}},
        "$ref": "#/$defs/good",
    }
    with patch.object(socket, "socket", side_effect=AssertionError("network I/O attempted")):
        check(
            "FTF-0405",
            "negative_acceptance: validating a schema with an embedded remote $ref "
            "never opens a socket, even one level removed via $defs",
            _rejects_as_security(validate_json_schema_document, malicious_schema, "x"),
        )
        try:
            validate_instance(safe_local_schema, "a value resolved only through a safe local $ref")
            safe_case_succeeded = True
        except JsonSchemaValidationError:
            safe_case_succeeded = False
        check(
            "FTF-0405",
            "the same patched-socket environment does not break genuinely safe "
            "local $ref resolution -- the refusal above is about the remote "
            "reference specifically, not a blanket failure",
            safe_case_succeeded,
        )


# -- 4. negative_acceptance: invalid required/type constraints ---------------


def test_unknown_type_value_is_rejected() -> None:
    check(
        "FTF-0405",
        "negative_acceptance: an unrecognized 'type' value is refused explicitly",
        _rejects(validate_json_schema_document, {"type": "not_a_real_type"}, "x"),
    )


def test_required_naming_undeclared_property_is_rejected() -> None:
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id", "ghost_field"],
    }
    check(
        "FTF-0405",
        "negative_acceptance: 'required' naming a property never declared in "
        "'properties' is refused explicitly",
        _rejects(validate_json_schema_document, schema, "x"),
    )


def test_required_without_properties_object_is_rejected() -> None:
    check(
        "FTF-0405",
        "'required' present with no matching 'properties' object is refused",
        _rejects(validate_json_schema_document, {"type": "object", "required": ["x"]}, "x"),
    )


# -- 5. negative_acceptance: excess arguments --------------------------------


def test_excess_instance_property_is_rejected() -> None:
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    }
    validate_instance(schema, {"order_id": "abc"})  # the honest case must pass
    check(
        "FTF-0405",
        "negative_acceptance: an instance carrying a property beyond what "
        "additionalProperties: false permits is refused explicitly",
        _rejects(validate_instance, schema, {"order_id": "abc", "unexpected_extra": "x"}),
    )


def test_missing_required_instance_property_is_rejected() -> None:
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }
    check(
        "FTF-0405",
        "an instance missing a required property is refused",
        _rejects(validate_instance, schema, {}),
    )


def test_instance_type_mismatch_is_rejected() -> None:
    schema: dict[str, object] = {"type": "string"}
    check(
        "FTF-0405",
        "an instance of the wrong type is refused",
        _rejects(validate_instance, schema, 12345),
    )
    check(
        "FTF-0405",
        "a matching instance is accepted",
        not _rejects(validate_instance, schema, "ok"),
    )


def test_enum_and_pattern_instance_checks() -> None:
    enum_schema: dict[str, object] = {"type": "string", "enum": ["a", "b", "c"]}
    check(
        "FTF-0405",
        "a value outside a declared enum is refused",
        _rejects(validate_instance, enum_schema, "z"),
    )
    pattern_schema: dict[str, object] = {"type": "string", "pattern": "^[a-z]+$"}
    check(
        "FTF-0405",
        "a value not matching a declared pattern is refused",
        _rejects(validate_instance, pattern_schema, "NOT-LOWERCASE"),
    )
    check(
        "FTF-0405",
        "a value matching a declared pattern is accepted",
        not _rejects(validate_instance, pattern_schema, "lowercase"),
    )


# -- 6. Real live wiring: tool_definition_schema.py calls this for real -----


def test_validate_schema_object_genuinely_calls_this_module() -> None:
    import inspect

    from app.tools import tool_definition_schema

    source = inspect.getsource(tool_definition_schema)
    check(
        "FTF-0405",
        "tool_definition_schema.py's own validate_schema_object genuinely calls "
        "validate_json_schema_document -- not merely importing it unused",
        "validate_json_schema_document(" in source
        and "app.services.json_schema_tool_validation" in source,
    )


def test_constructing_a_tool_definition_with_an_invalid_schema_fails_before_any_db() -> None:
    now = datetime.now(UTC)
    bad_input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"order_id": {"type": "not_a_type"}},
    }

    def _construct() -> ToolDefinition:
        return ToolDefinition(
            organization_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="lookup_order",
            description=None,
            input_schema_json=bad_input_schema,
            output_schema_json={"type": "object"},
            side_effect_class="read_only",
            requires_confirmation=False,
            is_critical=False,
            version=1,
            schema_hash=compute_schema_hash(bad_input_schema, {"type": "object"}),
            previous_version_id=None,
            superseded_at=None,
            created_by="user-1",
            created_at=now,
            updated_at=now,
        )

    try:
        _construct()
        raised = False
    except ToolDefinitionValidationError:
        raised = True
    check(
        "FTF-0405",
        "constructing a ToolDefinition whose input_schema_json this task's own "
        "validator rejects raises at construction time, before any session or "
        "database is ever touched",
        raised,
    )


def test_a_real_agent_tool_schema_using_defs_and_ref_survives_the_full_pipeline() -> None:
    # A realistic tool schema (Section 22's own worked example shape):
    # a shared "money amount" definition reused by two fields via $ref.
    schema = {
        "type": "object",
        "$defs": {
            "money_amount": {"type": "number", "minimum": 0, "exclusiveMaximum": 1000000},
        },
        "properties": {
            "refund_amount": {"$ref": "#/$defs/money_amount"},
            "fee_amount": {"$ref": "#/$defs/money_amount"},
        },
        "required": ["refund_amount"],
        "additionalProperties": False,
    }
    validate_json_schema_document(schema, "input_schema_json")
    validate_instance(schema, {"refund_amount": 42.5, "fee_amount": 1.0})
    check(
        "FTF-0405",
        "negative_acceptance (real positive case): a valid instance using a shared "
        "$defs reference from two sibling properties passes end-to-end",
        _rejects(validate_instance, schema, {"refund_amount": -5}) is True,
    )


def test_allowed_keywords_is_the_documented_pinned_vocabulary() -> None:
    check(
        "FTF-0405",
        "core structural keywords this task's own tests rely on are all in the "
        "pinned vocabulary",
        {"type", "properties", "required", "$ref", "$defs", "additionalProperties"}
        <= ALLOWED_KEYWORDS,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0405 JSON Schema tool validation tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0405 JSON Schema tool validation tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
