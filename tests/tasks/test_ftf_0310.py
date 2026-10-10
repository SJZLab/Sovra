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
