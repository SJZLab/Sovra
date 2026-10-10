#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index"
# ruff: noqa: E501
"""FTF-0310 -- capability contract tests: checker, generated negative table, matrix.

The real-Postgres, real-router evidence is
tests/integration/test_ftf_0310_capability_contract.py.

Criteria (CAP-06, Q07, Q10), one behavioural check group each:
  K1  the checker enforces every keyword the frozen 03 file uses (exact JSON
      typing, enum by type+value, bounds, formats, closed objects, allOf/oneOf/$ref).
  K2  fail closed: an unknown keyword, unsupported $ref or unsupported type raises.
  K3  the contract loads from the one frozen path; any copy is refused.
  K4  a generated negative table (missing required, enum variants, wrong type,
      bounds, extra property) is derived from valid seeds for every response schema
      and every case is rejected.
  K5  the matrix covers every FROZEN source type x status x license x mapping cell,
      and the frozen enums equal the code's shipped sets.
  K6  expected_trainable is true only for mapped + ready + approved.
  K7  the generated table catches a checker that accepts an extra property, casefolds
      enums, treats bool as integer, or skips `required`.

Run directly: python3 tests/tasks/test_ftf_0310.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.model_catalog import capability_contract_tests as cct  # noqa: E402

from app.artifacts.artifact_schema import ALLOWED_SOURCE_TYPES, ALLOWED_STATUSES  # noqa: E402

CID = "FTF-0310"
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(exc: type[BaseException], fn: Any) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001 - a different error type is a failed check
        return False
    return False


CONTRACT = cct.load_frozen_contract(ROOT)
ROOT_DOC = CONTRACT.document

_UUID_A = "11111111-1111-4111-8111-111111111111"
_UUID_B = "22222222-2222-4222-8222-222222222222"
_TS = "2026-10-07T12:00:00+00:00"

VALID_ARTIFACT = {
    "id": _UUID_B,
    "organization_id": _UUID_A,
    "display_name": "artifact-a",
    "source_type": "huggingface",
    "source_uri": "acme/model",
    "model_family": "llama",
    "base_model_revision": "a" * 40,
    "weights_digest": "sha256:" + "b" * 64,
    "license_id": "mit",
    "license_accepted_by": "seed",
    "license_accepted_at": _TS,
    "trust_remote_code_allowed": False,
    "required_python_packages": ["torch"],
    "minimum_gpu_profile": {"gpu": "a100"},
    "status": "ready",
    "created_at": _TS,
    "updated_at": _TS,
    "archived_at": None,
}
VALID_MAPPING = {
    "mapping_state": "mapped",
    "model_catalog_entry_id": _UUID_A,
    "litellm_connection_id": _UUID_B,
    "public_model_name": "trainable-alias",
    "trainable_artifact_id": _UUID_B,
    "catalog_version": 1,
    "last_verified_accessible_at": _TS,
    "last_changed_at": _TS,
    "artifact": VALID_ARTIFACT,
}
VALID_UNMAPPED = {
    **VALID_MAPPING,
    "mapping_state": "unmapped",
    "model_catalog_entry_id": None,
    "trainable_artifact_id": None,
    "catalog_version": 0,
    "last_verified_accessible_at": None,
    "last_changed_at": None,
    "artifact": None,
}
VALID_CHANGE = {
    "id": _UUID_A,
    "model_catalog_entry_id": _UUID_B,
    "previous_trainable_artifact_id": None,
    "new_trainable_artifact_id": _UUID_B,
    "previous_catalog_version": 0,
    "new_catalog_version": 1,
    "actor": "user:jane",
    "request_id": "req-1",
    "created_at": _TS,
}
VALID_CAPABILITIES = {
    "litellm_connection_id": _UUID_A,
    "project_id": _UUID_B,
    "public_model_name": "trainable-alias",
    "connection_status": "active",
    "access": "granted",
    "capability": "trainable_open_weight",
    "trainable": True,
    "capability_detail": "backed by a ready TrainableArtifact",
    "capability_reason": None,
    "role": None,
    "artifact": {
        "artifact_id": _UUID_B,
        "availability": "ready",
        "license_approved": True,
        "weights_digest": "sha256:" + "b" * 64,
    },
    "catalog_version": 1,
    "method_support": {"state": "unknown", "reason": "no_backing_evidence"},
    "min_gpu_vram_gb": {
        "state": "unknown",
        "reason": "not_tested",
        "declared_minimum_gpu_profile": {"gpu": "a100"},
        "declared_evidence": "declared",
    },
    "chat_template": {"state": "unknown", "reason": "no_backing_evidence"},
    "evidence": {
        "discovery_checked_at": _TS,
        "tool_call_probe_checked_at": None,
        "tool_call_probe_present": False,
    },
}

SEEDS: list[tuple[str, dict[str, Any], Any]] = [
    ("ModelCapabilities", CONTRACT.schema("ModelCapabilities"), VALID_CAPABILITIES),
    (
        "ModelTrainingArtifactMapping",
        CONTRACT.schema("ModelTrainingArtifactMapping"),
        VALID_MAPPING,
    ),
    (
        "ModelTrainingArtifactMapping/unmapped",
        CONTRACT.schema("ModelTrainingArtifactMapping"),
        VALID_UNMAPPED,
    ),
    (
        "ModelTrainingArtifactMappingChange",
        CONTRACT.schema("ModelTrainingArtifactMappingChange"),
        VALID_CHANGE,
    ),
    ("TrainableArtifact", CONTRACT.schema("TrainableArtifact"), VALID_ARTIFACT),
]


def errors_for(instance: Any, schema: dict[str, Any]) -> list[str]:
    return cct.validate(instance, schema, ROOT_DOC)


def test_k1_checker_keywords() -> None:
    def s(**kw: Any) -> dict[str, Any]:
        return kw

    check(
        "K1", "valid seeds validate", all(not errors_for(seed, schema) for _, schema, seed in SEEDS)
    )
    check("K1", "bool is not an integer", bool(errors_for(True, s(type="integer"))))
    check("K1", "bool is not a number", bool(errors_for(False, s(type="number"))))
    check("K1", "int is an integer", not errors_for(3, s(type="integer")))
    check("K1", "null in a type list", not errors_for(None, s(type=["string", "null"])))
    check("K1", "enum compares type: True is not 1", bool(errors_for(True, s(enum=[1]))))
    check("K1", "enum compares type: 1 is not True", bool(errors_for(1, s(enum=[True]))))
    check("K1", "enum accepts exact", not errors_for("a", s(enum=["a"])))
    check(
        "K1",
        "unhashable value against enum is a violation, not a crash",
        bool(errors_for([], s(enum=["a"]))),
    )
    check(
        "K1",
        "unhashable dict against enum is a violation, not a crash",
        bool(errors_for({}, s(enum=["a"]))),
    )
    check("K1", "minLength", bool(errors_for("", s(type="string", minLength=1))))
    check("K1", "maxLength", bool(errors_for("abc", s(type="string", maxLength=2))))
    check("K1", "maxLength boundary accepted", not errors_for("ab", s(type="string", maxLength=2)))
    check("K1", "pattern", bool(errors_for("x", s(type="string", pattern="^sha256:"))))
    check("K1", "minimum", bool(errors_for(-1, s(type="integer", minimum=0))))
    check("K1", "maximum", bool(errors_for(101, s(type="integer", maximum=100))))
    check("K1", "uuid format", bool(errors_for("nope", s(type="string", format="uuid"))))
    check("K1", "uuid format accepts", not errors_for(_UUID_A, s(type="string", format="uuid")))
    check(
        "K1",
        "date-time format",
        bool(errors_for("2026-10-07", s(type="string", format="date-time"))),
    )
    check("K1", "required", bool(errors_for({}, s(type="object", required=["a"]))))
    check(
        "K1",
        "closed object",
        bool(errors_for({"a": 1}, s(type="object", additionalProperties=False))),
    )
    check("K1", "open object", not errors_for({"a": 1}, s(type="object")))
    check("K1", "items", bool(errors_for([1], s(type="array", items=s(type="string")))))
    check("K1", "allOf", bool(errors_for(1, s(allOf=[s(type="integer"), s(minimum=5)]))))
    check(
        "K1", "oneOf exactly one", not errors_for(None, s(oneOf=[s(type="string"), s(type="null")]))
    )
    check("K1", "oneOf none", bool(errors_for(3, s(oneOf=[s(type="string"), s(type="null")]))))
    check("K1", "oneOf two", bool(errors_for(3, s(oneOf=[s(type="integer"), s(minimum=0)]))))


def test_k2_fail_closed() -> None:
    check(
        "K2",
        "unknown keyword raises",
        raises(cct.UnsupportedSchemaError, lambda: errors_for(1, {"exclusiveMinimum": 0})),
    )
    check(
        "K2",
        "unknown type raises",
        raises(cct.UnsupportedSchemaError, lambda: errors_for(1, {"type": "decimal"})),
    )
    check(
        "K2",
        "unresolvable $ref raises",
        raises(
            cct.UnsupportedSchemaError, lambda: errors_for(1, {"$ref": "#/components/schemas/Nope"})
        ),
    )
    check(
        "K2",
        "external $ref raises",
        raises(cct.UnsupportedSchemaError, lambda: errors_for(1, {"$ref": "http://x/y"})),
    )
    check(
        "K2",
        "unknown format raises",
        raises(
            cct.UnsupportedSchemaError,
            lambda: errors_for("x", {"type": "string", "format": "email"}),
        ),
    )
    check(
        "K2",
        "annotation keywords are allowed",
        not errors_for(1, {"type": "integer", "description": "d", "default": 0}),
    )


def test_k3_frozen_path() -> None:
    check(
        "K3",
        "default load is the frozen file",
        CONTRACT.path == (ROOT / cct.FROZEN_CONTRACT_RELATIVE_PATH).resolve(),
    )
    check(
        "K3",
        "sha256 is the hash of the frozen bytes",
        CONTRACT.sha256
        == hashlib.sha256((ROOT / cct.FROZEN_CONTRACT_RELATIVE_PATH).read_bytes()).hexdigest(),
    )
    tmp = Path(tempfile.mkdtemp())
    try:
        copy = tmp / "03-interface.yaml"
        shutil.copy(ROOT / cct.FROZEN_CONTRACT_RELATIVE_PATH, copy)
        check(
            "K3",
            "a byte-identical copy is refused",
            raises(cct.ContractError, lambda: cct.load_frozen_contract(ROOT, copy)),
        )
        check(
            "K3",
            "a copy under another root is refused",
            raises(cct.ContractError, lambda: cct.load_frozen_contract(tmp)),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check(
        "K3",
        "an explicit frozen path is accepted",
        cct.load_frozen_contract(ROOT, ROOT / cct.FROZEN_CONTRACT_RELATIVE_PATH).sha256
        == CONTRACT.sha256,
    )


def table() -> list[tuple[str, dict[str, Any], list[cct.NegativeCase]]]:
    return [
        (name, schema, cct.generate_negative_cases(seed, schema, ROOT_DOC))
        for name, schema, seed in SEEDS
    ]


def test_k4_negative_table() -> None:
    for name, schema, cases in table():
        categories = {c.category for c in cases}
        check("K4", f"{name}: table is non-trivial ({len(cases)})", len(cases) >= 10)
        for needed in ("missing_required", "wrong_type"):
            check("K4", f"{name}: has {needed} cases", needed in categories)
        check(
            "K4",
            f"{name}: every case rejected",
            cct.assert_negative_cases_rejected(cases, schema, ROOT_DOC) == [],
        )
    caps = next(c for n, _, c in table() if n == "ModelCapabilities")
    categories = {c.category for c in caps}
    for needed in ("enum", "max_length", "minimum", "format", "extra_property"):
        # ModelCapabilities has enums, uuid/date-time formats and a minimum; extra_property
        # applies to its closed root.
        if needed == "max_length":
            continue
        check("K4", f"ModelCapabilities has {needed} cases", needed in categories)
    enum_paths = {c.path for c in caps if c.category == "enum"}
    check(
        "K4",
        "access enum gets an uppercase variant",
        any(".access:'GRANTED'" in p for p in enum_paths),
    )
    check(
        "K4",
        "capability enum gets a trailing-space variant",
        any(".capability:'trainable_open_weight '" in p for p in enum_paths),
    )
    check("K4", "availability enum is exercised", any("availability" in p for p in enum_paths))
    check(
        "K4",
        "required fields are removed one at a time",
        sum(1 for c in caps if c.category == "missing_required") >= 13,
    )
    mapping = next(c for n, _, c in table() if n == "ModelTrainingArtifactMapping")
    check(
        "K4",
        "nested artifact object is mutated too",
        any(".artifact.status" in c.path for c in mapping),
    )
    check(
        "K4",
        "the seed must itself be valid",
        raises(
            cct.ContractError,
            lambda: cct.generate_negative_cases({}, CONTRACT.schema("ModelCapabilities"), ROOT_DOC),
        ),
    )


def test_k5_matrix() -> None:
    sources = CONTRACT.enum_values("SourceType")
    statuses = CONTRACT.enum_values("ArtifactStatus")
    check(
        "K5",
        "frozen SourceType equals the shipped set",
        set(sources) == set(ALLOWED_SOURCE_TYPES) and len(sources) == len(ALLOWED_SOURCE_TYPES),
    )
    check(
        "K5",
        "frozen ArtifactStatus equals the shipped set",
        set(statuses) == set(ALLOWED_STATUSES) and len(statuses) == len(ALLOWED_STATUSES),
    )
    matrix = cct.capability_matrix(sources, statuses)
    check(
        "K5",
        f"matrix has every cell ({len(matrix)})",
        len(matrix) == len(sources) * len(statuses) * 3 * 2,
    )
    seen = {(c["source_type"], c["status"], c["license"], c["mapped"]) for c in matrix}
    check("K5", "no duplicate cell", len(seen) == len(matrix))
    for source in sources:
        check(
            "K5",
            f"source {source} appears with every status",
            {c["status"] for c in matrix if c["source_type"] == source} == set(statuses),
        )
    check(
        "K5",
        "dropping a source variant shrinks the matrix",
        len(cct.capability_matrix(sources[:-1], statuses)) < len(matrix),
    )


def test_k6_expected_trainable() -> None:
    trainable = [
        c
        for c in cct.capability_matrix(
            CONTRACT.enum_values("SourceType"), CONTRACT.enum_values("ArtifactStatus")
        )
        if cct.expected_trainable(c)
    ]
    check(
        "K6",
        "only mapped+ready+approved cells are trainable",
        all(
            c["mapped"] and c["status"] == "ready" and c["license"] == "approved" for c in trainable
        ),
    )
    check(
        "K6",
        "one trainable cell per source type",
        len(trainable) == len(CONTRACT.enum_values("SourceType")),
    )
    for license_state in ("pending", "revoked"):
        check(
            "K6",
            f"{license_state} license is never trainable",
            not cct.expected_trainable(
                {"mapped": True, "status": "ready", "license": license_state}
            ),
        )
    check(
        "K6",
        "unmapped is never trainable",
        not cct.expected_trainable({"mapped": False, "status": "ready", "license": "approved"}),
    )
    check(
        "K6",
        "archived is never trainable",
        not cct.expected_trainable({"mapped": True, "status": "archived", "license": "approved"}),
    )
    cells = cct.capability_matrix(
        CONTRACT.enum_values("SourceType"), CONTRACT.enum_values("ArtifactStatus")
    )
    check(
        "K6",
        "exactly the ready cells without an approved license are API-unreachable",
        {(c["status"], c["license"]) for c in cells if not cct.api_reachable(c)}
        == {("ready", "pending"), ("ready", "revoked")},
    )
    check(
        "K6",
        "every trainable cell is API-reachable",
        all(cct.api_reachable(c) for c in cells if cct.expected_trainable(c)),
    )
    check(
        "K6",
        "registered is never trainable",
        not cct.expected_trainable({"mapped": True, "status": "registered", "license": "approved"}),
    )


def raises_with(exc: type[BaseException], needle: str, fn: Any) -> bool:
    try:
        fn()
    except exc as caught:
        return needle in str(caught)
    except Exception:  # noqa: BLE001 - a different error type is a failed check
        return False
    return False


def test_k8_edges() -> None:
    def v(instance: Any, schema: dict[str, Any]) -> list[str]:
        return cct.validate(instance, schema, ROOT_DOC)

    # boundaries are inclusive
    check("K8", "minLength boundary accepted", not v("a", {"type": "string", "minLength": 1}))
    check("K8", "maximum boundary accepted", not v(100, {"type": "integer", "maximum": 100}))
    check("K8", "minimum boundary accepted", not v(0, {"type": "integer", "minimum": 0}))
    check("K8", "maximum exceeded", bool(v(101, {"type": "integer", "maximum": 100})))
    check(
        "K8",
        "number accepts int and float, refuses str and bool",
        not v(1, {"type": "number"})
        and not v(1.5, {"type": "number"})
        and bool(v("1", {"type": "number"}))
        and bool(v(True, {"type": "number"})),
    )
    check("K8", "float is not an integer", bool(v(1.5, {"type": "integer"})))
    # enum equality is exact and deep
    check(
        "K8",
        "enum compares dict values",
        not v({"a": 1}, {"enum": [{"a": 1}]})
        and bool(v({"a": 2}, {"enum": [{"a": 1}]}))
        and bool(v({"a": 1, "b": 2}, {"enum": [{"a": 1}]}))
        and bool(v({}, {"enum": [{"a": 1}]})),
    )
    check(
        "K8",
        "enum compares list values",
        not v([1, 2], {"enum": [[1, 2]]})
        and bool(v([1], {"enum": [[1, 2]]}))
        and bool(v([1, 3], {"enum": [[1, 2]]}))
        and bool(v([2, 1], {"enum": [[1, 2]]})),
    )
    check(
        "K8",
        "enum compares nested types: {a: True} is not {a: 1}",
        bool(v({"a": True}, {"enum": [{"a": 1}]})) and bool(v({"a": 1}, {"enum": [{"a": True}]})),
    )
    check(
        "K8",
        "enum compares nested types: [True] is not [1]",
        bool(v([True], {"enum": [[1]]})) and bool(v([1], {"enum": [[True]]})),
    )
    check(
        "K8",
        "enum is not case/space insensitive",
        bool(v(" A ", {"enum": ["a"]})) and bool(v("A", {"enum": ["a"]})),
    )
    # oneOf: exactly one valid branch among several
    check(
        "K8",
        "oneOf with one valid among three",
        not v(1, {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}),
    )
    # required / properties are exact-name
    check(
        "K8",
        "required name is case-exact",
        bool(v({"a": 1}, {"type": "object", "required": ["A"]})),
    )
    check(
        "K8",
        "property name is case/space-exact on a closed object",
        bool(
            v({" A": 1}, {"type": "object", "properties": {"a": {}}, "additionalProperties": False})
        ),
    )
    # formats are strict
    check(
        "K8",
        "uuid without dashes is refused",
        bool(v("1" * 32, {"type": "string", "format": "uuid"})),
    )
    check(
        "K8",
        "uuid with braces is refused",
        bool(v("{" + _UUID_A + "}", {"type": "string", "format": "uuid"})),
    )
    check(
        "K8",
        "uuid with Z-less date-time naive accepted shape",
        not v("2026-10-07T12:00:00", {"type": "string", "format": "date-time"}),
    )
    check(
        "K8",
        "date-time Z suffix accepted",
        not v("2026-10-07T12:00:00Z", {"type": "string", "format": "date-time"}),
    )
    check(
        "K8",
        "date-time garbage refused",
        bool(v("2026-13-45T99:00:00", {"type": "string", "format": "date-time"})),
    )
    # malformed schemas / contracts fail closed with a specific message
    check(
        "K8",
        "non-object schema raises",
        raises_with(cct.UnsupportedSchemaError, "not an object", lambda: v(1, [])),
    )
    check(
        "K8",
        "non-str $ref raises 'unsupported'",
        raises_with(cct.UnsupportedSchemaError, "unsupported $ref", lambda: v(1, {"$ref": 5})),
    )
    check(
        "K8",
        "external $ref raises 'unsupported'",
        raises_with(
            cct.UnsupportedSchemaError, "unsupported $ref", lambda: v(1, {"$ref": "http://x/y"})
        ),
    )
    check(
        "K8",
        "missing component raises 'unresolvable'",
        raises_with(
            cct.UnsupportedSchemaError,
            "unresolvable",
            lambda: v(1, {"$ref": "#/components/schemas/Nope"}),
        ),
    )
    check(
        "K8",
        "unhashable type name raises",
        raises_with(
            cct.UnsupportedSchemaError, "unsupported type", lambda: v(1, {"type": [["a"]]})
        ),
    )
    check(
        "K8",
        "type name is exact",
        raises_with(
            cct.UnsupportedSchemaError, "unsupported type", lambda: v("x", {"type": "String"})
        ),
    )
    check(
        "K8",
        "format name is exact",
        raises_with(
            cct.UnsupportedSchemaError,
            "unsupported format",
            lambda: v("x", {"type": "string", "format": "UUID"}),
        ),
    )
    check(
        "K8",
        "unknown frozen schema name raises",
        raises_with(cct.ContractError, "unknown frozen schema", lambda: CONTRACT.schema("Nope")),
    )
    check(
        "K8",
        "non-str frozen schema name raises",
        raises_with(
            cct.ContractError,
            "unknown frozen schema",
            lambda: CONTRACT.schema(["ModelCapabilities"]),
        ),
    )
    for body, label in (("[]", "a list"), ("a: 1\n", "no components"), ("- 1\n", "a sequence")):
        tmp = Path(tempfile.mkdtemp())
        try:
            target = tmp / cct.FROZEN_CONTRACT_RELATIVE_PATH
            target.parent.mkdir(parents=True)
            target.write_text(body)
            check(
                "K8",
                f"a frozen file that is {label} is refused",
                raises_with(
                    cct.ContractError,
                    "not an OpenAPI document",
                    lambda tmp=tmp: cct.load_frozen_contract(tmp),
                ),
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp())
    try:
        check(
            "K8",
            "a missing frozen file is refused",
            raises_with(
                cct.ContractError, "frozen contract missing", lambda: cct.load_frozen_contract(tmp)
            ),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_k9_generator_on_custom_schemas() -> None:
    doc: dict[str, Any] = {
        "components": {
            "schemas": {
                "Str": {"type": "string"},
                "Obj": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["Id", "n", "name", "tag", "tags", "when", "ref", "opt"],
                    "properties": {
                        "Id": {"type": "string", "format": "uuid"},
                        "n": {"type": "integer"},
                        "m": {"type": "integer", "minimum": 5},
                        "name": {"type": "string", "minLength": 1, "maxLength": 3},
                        "tag": {"type": "string", "enum": ["Ab", "cd"]},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "bag": {"type": "array"},
                        "when": {"type": "number"},
                        "ref": {"oneOf": [{"$ref": "#/components/schemas/Str"}, {"type": "null"}]},
                        "opt": {
                            "allOf": [
                                {"type": "object"},
                                {"properties": {"k": {"type": "integer"}}, "required": ["k"]},
                            ]
                        },
                        "free": {},
                    },
                },
            }
        }
    }
    schema = {"$ref": "#/components/schemas/Obj"}
    seed = {
        "Id": _UUID_A,
        "n": 1,
        "m": 7,
        "name": "ab",
        "tag": "Ab",
        "tags": ["t"],
        "bag": [1],
        "when": 2.5,
        "ref": None,
        "opt": {"k": 1},
        "free": 1,
    }
    check("K9", "the custom seed is valid", not cct.validate(seed, schema, doc))
    cases = cct.generate_negative_cases(seed, schema, doc)
    check(
        "K9",
        "every custom case is rejected",
        cct.assert_negative_cases_rejected(cases, schema, doc) == [],
    )
    by_cat: dict[str, list[str]] = {}
    for case in cases:
        by_cat.setdefault(case.category, []).append(case.path)
    for needed in (
        "missing_required",
        "wrong_type",
        "enum",
        "max_length",
        "min_length",
        "format",
        "minimum",
        "extra_property",
    ):
        check("K9", f"custom table has {needed} cases", needed in by_cat)
    check(
        "K9",
        "a required name with a capital letter is removed",
        any(p == "$.Id" for p in by_cat.get("missing_required", [])),
    )
    check(
        "K9",
        "a property name with a capital letter is walked",
        any(p.startswith("$.Id:") for p in by_cat.get("wrong_type", [])),
    )
    check(
        "K9",
        "array items are walked",
        any(p.startswith("$.tags[0]") for p in by_cat.get("wrong_type", [])),
    )
    check(
        "K9",
        "nested allOf object requirement is removed",
        "$.opt.k" in by_cat.get("missing_required", []),
    )
    check(
        "K9",
        "an array without `items` does not crash and is not walked",
        not any(p.startswith("$.bag[") for p in by_cat.get("wrong_type", [])),
    )
    check(
        "K9",
        "an integer without `minimum` does not crash",
        not any(p.startswith("$.n") for p in by_cat.get("minimum", [])),
    )
    check("K9", "minimum below the bound is generated", "$.m" in by_cat.get("minimum", []))
    check(
        "K9",
        "number: an integer sample is not a wrong type",
        not any(p == "$.when:integer" for p in by_cat.get("wrong_type", [])),
    )
    check(
        "K9",
        "number: a string sample is a wrong type",
        "$.when:string" in by_cat.get("wrong_type", []),
    )
    check(
        "K9",
        "oneOf of [$ref string, null]: string and null are NOT wrong types",
        not any(p in ("$.ref:string", "$.ref:null") for p in by_cat.get("wrong_type", [])),
    )
    check(
        "K9",
        "oneOf of [$ref string, null]: object is a wrong type",
        "$.ref:object" in by_cat.get("wrong_type", []),
    )
    check(
        "K9",
        "an unconstrained property gets no wrong-type case",
        not any(p.startswith("$.free") for p in by_cat.get("wrong_type", [])),
    )
    enum_paths = set(by_cat.get("enum", []))
    check("K9", "enum: uppercase variant of 'Ab' is generated", "$.tag:'AB'" in enum_paths)
    check(
        "K9",
        "enum: a variant equal to another enum member is not generated",
        not any(p == "$.tag:'cd'" for p in enum_paths),
    )
    check("K9", "enum: no duplicate candidates", len(enum_paths) == len(by_cat.get("enum", [])))
    check(
        "K9",
        "enum: the valid value itself is never a negative case",
        "$.tag:'Ab'" not in enum_paths,
    )
    check(
        "K9",
        "min_length case is generated for minLength 1",
        "$.name" in by_cat.get("min_length", []),
    )
    check(
        "K9",
        "no min_length case for a property without minLength",
        "$.tag" not in by_cat.get("min_length", []),
    )
    check("K9", "max_length case is generated", "$.name" in by_cat.get("max_length", []))
    check(
        "K9",
        "a string property gets no minimum case",
        not any(p == "$.name" for p in by_cat.get("minimum", [])),
    )
    check(
        "K9",
        "a non-seed-valid instance is refused",
        raises_with(
            cct.ContractError,
            "seed instance must be valid",
            lambda: cct.generate_negative_cases({}, schema, doc),
        ),
    )


def survivors_with(patched: dict[str, Any]) -> int:
    """Run the whole table with module attributes patched; count wrongly accepted cases."""
    originals = {k: getattr(cct, k) for k in patched}
    for k, v in patched.items():
        setattr(cct, k, v)
    try:
        return sum(
            len(cct.assert_negative_cases_rejected(cases, schema, ROOT_DOC))
            for _, schema, cases in cases_cache
        )
    finally:
        for k, v in originals.items():
            setattr(cct, k, v)


cases_cache = table()


def test_k7_faults() -> None:
    check("K7", "real checker rejects the whole table", survivors_with({}) == 0)

    real_validate_inner = cct._validate

    def accept_extra(
        instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str, errors: list[str]
    ) -> None:
        if type(schema) is dict and "additionalProperties" in schema:
            schema = {k: v for k, v in schema.items() if k != "additionalProperties"}
        real_validate_inner(instance, schema, root, path, errors)

    # the recursive call inside cct._validate looks up the module global, so patching it patches every level
    check(
        "K7",
        "a checker that accepts extra properties is caught",
        survivors_with({"_validate": accept_extra}) > 0,
    )

    def casefold_enum(left: Any, right: Any) -> bool:
        if type(left) is str and type(right) is str:
            return left.strip().casefold() == right.strip().casefold()
        return type(left) is type(right) and bool(left == right)

    check(
        "K7",
        "a casefolding / stripping enum compare is caught",
        survivors_with({"_json_equal": casefold_enum}) > 0,
    )

    def bool_is_int(value: Any, name: str) -> bool:
        if name == "integer":
            return isinstance(value, int)
        return real_is_type(value, name)

    real_is_type = cct._is_type
    check(
        "K7",
        "a checker that treats bool as integer is caught",
        survivors_with({"_is_type": bool_is_int}) > 0,
    )

    def skip_required(
        instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str, errors: list[str]
    ) -> None:
        if type(schema) is dict and "required" in schema:
            schema = {k: v for k, v in schema.items() if k != "required"}
        real_validate_inner(instance, schema, root, path, errors)

    check(
        "K7",
        "a checker that skips `required` is caught",
        survivors_with({"_validate": skip_required}) > 0,
    )

    def no_format(value: str, name: str) -> bool:
        return True

    check(
        "K7", "a checker that skips format is caught", survivors_with({"_format_ok": no_format}) > 0
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        print(f"FTF-0310 capability contract unit tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0310 capability contract unit tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
