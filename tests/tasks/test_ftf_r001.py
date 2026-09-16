#!/usr/bin/env python3
"""
FTF-R001 -- Freeze seven-component architecture, ownership and version-lock
schemas.

Proves the frozen contracts in
`packages/component-contracts/architecture.schema.json` and
`spec/component-lock.schema.json` actually reject the four
negative_acceptance cases named in the task's own resolved contract
(`tools/r1_handoff.py packet FTF-R001`):

  1. missing/extra substituted component
  2. an invented always-on Axolotl/Inspect HTTP endpoint
  3. duplicate registry approval authority
  4. a mutable latest-version release lock

and that a genuinely correct instance -- exactly the seven canonical
component keys, Axolotl/Inspect AI as worker packages with no endpoint,
LiteLLM as the existing gateway, one Factory-owned release approval
authority, and pinned (non-'latest') version locks -- passes with zero
errors.

Run directly: python3 tests/tasks/test_ftf_r001.py
"""

import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.architecture_lock import (  # noqa: E402
    ARCHITECTURE_SCHEMA_PATH as ARCH_SCHEMA_PATH,
)
from scripts.architecture_lock import (  # noqa: E402
    CANONICAL_COMPONENT_KEYS,
    load_json,
    validate_component_binding_document,
    validate_component_lock_document,
    validate_instance,
)
from scripts.architecture_lock import (  # noqa: E402
    COMPONENT_LOCK_SCHEMA_PATH as LOCK_SCHEMA_PATH,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _binding(component_key, deployment_mode, approved_endpoint):
    return {
        "component_key": component_key,
        "security_zone_scope": "default",
        "project_scope": None,
        "deployment_mode": deployment_mode,
        "approved_endpoint": approved_endpoint,
        "secret_reference": None,
        "network_policy_identity": f"{component_key}-net-policy",
        "health_result": "unknown",
        "compatibility_lock_reference": component_key,
    }


def valid_bindings():
    return [
        _binding("axolotl", "worker_package", None),
        _binding("inspect_ai", "worker_package", None),
        _binding("mlflow", "internal_service", "https://mlflow.internal.sovra.local"),
        _binding("vllm", "internal_service", "https://vllm.internal.sovra.local"),
        _binding("temporal", "internal_service", "temporal.internal.sovra.local:7233"),
        _binding("litellm", "existing_gateway", "https://litellm.internal.sovra.local"),
        _binding("argilla", "internal_service", "https://argilla.internal.sovra.local"),
    ]


def valid_authority():
    return [{"authority_id": "factory_approval_envelope", "component_key": None}]


def valid_binding_document():
    return {
        "component_bindings": valid_bindings(),
        "release_approval_authorities": valid_authority(),
    }


def _lock(component_key, package_version="1.2.3", image_digest=None):
    return {
        "component_key": component_key,
        "package_version": package_version,
        "image_digest": image_digest or ("sha256:" + ("a" * 64)),
        "dependencies": [],
        "configuration_digest": "sha256:" + ("b" * 64),
        "supported_api_versions": ["v1"],
        "source_license_evidence": f"{component_key}-license-notice",
        "qualification_status": "pending",
    }


def valid_lock_document():
    return {"component_version_locks": [_lock(key) for key in sorted(CANONICAL_COMPONENT_KEYS)]}


def test_frozen_schemas_enumerate_exactly_the_seven_canonical_keys():
    architecture_schema = load_json(ARCH_SCHEMA_PATH)
    lock_schema = load_json(LOCK_SCHEMA_PATH)
    arch_keys = set(architecture_schema["$defs"]["componentKey"]["enum"])
    lock_keys = set(lock_schema["$defs"]["componentKey"]["enum"])
    check(
        "FTF-R001",
        "architecture.schema.json enumerates exactly the seven canonical component keys",
        arch_keys == CANONICAL_COMPONENT_KEYS,
    )
    check(
        "FTF-R001",
        "component-lock.schema.json enumerates exactly the seven canonical component keys "
        "(the two frozen contracts do not drift apart)",
        lock_keys == CANONICAL_COMPONENT_KEYS,
    )


def test_valid_binding_document_passes_clean():
    errors = validate_component_binding_document(valid_binding_document())
    check("FTF-R001", f"a correct binding document has zero errors, got {errors}", not errors)


def test_valid_lock_document_passes_clean():
    errors = validate_component_lock_document(valid_lock_document())
    check("FTF-R001", f"a correct lock document has zero errors, got {errors}", not errors)


def test_missing_component_rejected():
    document = valid_binding_document()
    document["component_bindings"] = [
        b for b in document["component_bindings"] if b["component_key"] != "argilla"
    ]
    errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "a binding set missing one of the seven components is rejected",
        any("missing_component" in e and "argilla" in e for e in errors),
    )


def test_extra_substituted_component_rejected():
    document = valid_binding_document()
    document["component_bindings"][-1] = _binding(
        "some_invented_trainer", "internal_service", "https://invented.internal.sovra.local"
    )
    errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "a binding set with an invented component key substituted for a canonical one is rejected",
        any("extra_component" in e or "missing_component" in e for e in errors) or len(errors) > 0,
    )
    # The substituted binding's component_key also fails the enum check directly --
    # the schema itself, not only the whole-set rule, rejects the invented key.
    check(
        "FTF-R001",
        "the invented component key fails the componentKey enum on the schema itself",
        any("not in enum" in e for e in errors),
    )


def test_duplicate_component_rejected():
    document = valid_binding_document()
    document["component_bindings"][-1] = copy.deepcopy(document["component_bindings"][0])
    errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "binding the same component key twice (crowding out a canonical component) is rejected",
        any("duplicate_component" in e for e in errors)
        or any("missing_component" in e for e in errors),
    )


def test_invented_axolotl_http_endpoint_rejected():
    document = valid_binding_document()
    for binding in document["component_bindings"]:
        if binding["component_key"] == "axolotl":
            binding["deployment_mode"] = "internal_service"
            binding["approved_endpoint"] = "https://axolotl.internal.sovra.local:9000"
    errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "an Axolotl binding declaring an always-on internal_service HTTP endpoint is rejected "
        "(Axolotl is worker-executed software, never an assumed always-on HTTP service)",
        any("deployment_mode" in e for e in errors)
        and any("approved_endpoint" in e for e in errors),
    )


def test_invented_inspect_ai_http_endpoint_rejected():
    document = valid_binding_document()
    for binding in document["component_bindings"]:
        if binding["component_key"] == "inspect_ai":
            binding["deployment_mode"] = "internal_service"
            binding["approved_endpoint"] = "https://inspect.internal.sovra.local:9001"
    errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "an Inspect AI binding declaring an always-on internal_service HTTP endpoint is rejected",
        any("deployment_mode" in e for e in errors)
        and any("approved_endpoint" in e for e in errors),
    )


def test_second_release_approval_authority_rejected():
    document = valid_binding_document()
    document["release_approval_authorities"].append(
        {"authority_id": "factory_approval_envelope", "component_key": None}
    )
    errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "a second release approval authority record is rejected (exactly one is frozen)",
        any("maxItems" in e for e in errors),
    )


def test_vendor_component_claiming_release_authority_rejected():
    document = valid_binding_document()
    document["release_approval_authorities"] = [
        {"authority_id": "factory_approval_envelope", "component_key": "mlflow"}
    ]
    errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "a vendor component (mlflow) claiming release approval authority is rejected "
        "-- a manual registry tag can never authorize release",
        any("component_key" in e for e in errors),
    )


def test_mutable_latest_package_version_rejected():
    document = valid_lock_document()
    for lock in document["component_version_locks"]:
        if lock["component_key"] == "mlflow":
            lock["package_version"] = "latest"
    errors = validate_component_lock_document(document)
    check(
        "FTF-R001",
        "a component_version_lock pinned to the mutable tag 'latest' is rejected",
        any("package_version" in e for e in errors),
    )


def test_mutable_latest_image_digest_rejected():
    document = valid_lock_document()
    for lock in document["component_version_locks"]:
        if lock["component_key"] == "vllm":
            lock["image_digest"] = "latest"
    errors = validate_component_lock_document(document)
    check(
        "FTF-R001",
        "a component_version_lock whose image_digest is the mutable tag 'latest' "
        "(not a real content digest) is rejected",
        any("image_digest" in e for e in errors),
    )


def test_law1_evidence_key_set_check_catches_what_schema_alone_cannot():
    """Law 1: `component_bindings` is schema-pinned to exactly 7 items
    (minItems/maxItems=7), so a *missing* component always also trips item
    count. But a *duplicate* substituted for a missing one keeps the count
    at 7, with every individual binding still schema-valid (each has some
    real enum-valid component_key) -- only the whole-set business rule in
    `_check_exact_key_set` can catch that. Confirms schema-only validation
    genuinely passes this fixture (proving the business-rule check is
    load-bearing, not decorative, rather than merely re-asserting it)."""
    document = valid_binding_document()
    document["component_bindings"][-1] = copy.deepcopy(document["component_bindings"][0])
    # Now 7 items, "axolotl" bound twice, "argilla" missing -- item count and
    # every individual binding's own schema are still satisfied.
    schema_only_errors = validate_instance(document, load_json(ARCH_SCHEMA_PATH))
    check(
        "FTF-R001",
        "schema-only validation (no whole-set key check) passes a document where 'axolotl' is "
        "bound twice and 'argilla' is missing, since item count (7) and every individual "
        "binding's own schema are satisfied",
        schema_only_errors == [],
    )
    full_errors = validate_component_binding_document(document)
    check(
        "FTF-R001",
        "the real validator (schema + whole-set key check) rejects the same fixture",
        any("duplicate_component" in e for e in full_errors)
        and any("missing_component" in e and "argilla" in e for e in full_errors),
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-R001 reuse architecture / version-lock schema freeze: {len(FAILURES)} failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-R001 reuse architecture / version-lock schema freeze: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
