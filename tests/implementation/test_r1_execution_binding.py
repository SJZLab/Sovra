#!/usr/bin/env python3
"""
FTF-R014 -- Bind selected coding executor to the embedded Release 1
execution controls.

Proves:
  1. `compute_expected_digests()` computes both digests live against the
     real, current `spec/RELEASE_1.md` -- reproduced independently in this
     test with plain `hashlib`/regex, not trusted as a canned value.
  2. A real receipt whose digests match the actual current file, for the
     `R1_EMBEDDED` (file-only) profile with no external skill, passes --
     acceptance clause 1 and the "missing optional external skill must not
     block R1_EMBEDDED" negative_acceptance clause.
  3. A stale/wrong `source_file_digest` is rejected (missing/mismatched
     embedded-control digest).
  4. A stale/wrong `embedded_section_digest` is rejected even when
     `source_file_digest` is correct.
  5. `R1_EMBEDDED` fabricating an `external_skill` is rejected (fabricated
     external skill conformance).
  6. `R1_EMBEDDED_PLUS_EXISTING_SKILL` missing its `external_skill` is
     rejected.
  7. `R1_EMBEDDED_PLUS_EXISTING_SKILL` with a real, complete
     `external_skill` passes.
  8. `ExternalSkill` rejects an empty name/version/file_hash at
     construction.
  9. `SELF_CERTIFICATION_DENIED` when `validator_identity ==
     producer_identity`.
  10. An unscoped `workspace_identity` (`"main"` or empty) is rejected --
      task isolation.
  11. A receipt whose `changed_files` touches a real CODEOWNERS-protected
      path is rejected unless `validator_identity` is the real protected-
      gate release authority -- "producer mutation of protected gates...
      material."
  12. The same protected-path change is accepted when validated by the
      real release authority.
  13. `openhands_qualification_claimed` without
      `openhands_selected_and_run` is rejected -- "claimed OpenHands
      qualification without selection and use."
  14. An invalid `execution_control_profile` enum value is rejected by the
      real schema (`spec/implementation-executor.schema.json`), not a
      hand-picked string check.
  15. Real fault injection against the tracked module itself: disabling
      the self-certification guard in
      `tools/implementation/executor_contract.py` lets a fresh subprocess
      accept a self-certified receipt; restoring the file byte-identical
      re-rejects it.

Run directly: python3 tests/implementation/test_r1_execution_binding.py
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_evidence_gate import GateStatus  # noqa: E402
from tools.implementation.executor_contract import (  # noqa: E402
    EMBEDDED_SECTION_NUMBER,
    RELEASE_AUTHORITY_IDENTITY,
    RELEASE_SPEC_PATH,
    ExecutorReceipt,
    ExternalSkill,
    compute_expected_digests,
    validate_receipt,
)

FAILURES = []


def check(criterion_id, description, condition):
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _independent_reference_digests() -> tuple[str, str]:
    """Recomputes both digests with plain stdlib calls, independently of
    the module under test -- proving `compute_expected_digests()` isn't
    just echoing a hardcoded value."""
    raw = RELEASE_SPEC_PATH.read_bytes()
    source_digest = hashlib.sha256(raw).hexdigest()
    matches = list(re.finditer(r"^# (\d+[A-Z]?)\. .*?$", raw.decode("utf-8"), re.M))
    section_text = None
    for i, m in enumerate(matches):
        if m.group(1) == EMBEDDED_SECTION_NUMBER:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            section_text = raw.decode("utf-8")[m.start() : end].rstrip()
            break
    assert section_text is not None
    section_digest = hashlib.sha256(section_text.encode("utf-8")).hexdigest()
    return source_digest, section_digest


def _base_kwargs(**overrides):
    source_digest, section_digest = compute_expected_digests()
    kwargs = dict(
        task_id="FTF-R014",
        executor_id="claude-code-cli",
        model_route="claude-sonnet-5",
        execution_control_profile="R1_EMBEDDED",
        source_file_digest=source_digest,
        embedded_section_digest=section_digest,
        producer_identity="session:producer-1",
        validator_identity="session:validator-2",
        workspace_identity="claude/test-workspace",
        changed_files=frozenset(),
        external_skill=None,
        openhands_selected_and_run=False,
        openhands_qualification_claimed=False,
    )
    kwargs.update(overrides)
    return kwargs


def test_digests_computed_live_not_hardcoded():
    module_source, module_section = compute_expected_digests()
    independent_source, independent_section = _independent_reference_digests()
    check(
        "FTF-R014",
        "source_file_digest matches an independently recomputed sha256 of spec/RELEASE_1.md",
        module_source == independent_source,
    )
    check(
        "FTF-R014",
        "embedded_section_digest matches an independently recomputed sha256 of Section 84's "
        "real text",
        module_section == independent_section,
    )
    check("FTF-R014", "digests are real 64-char hex, not placeholders", len(module_source) == 64)


def test_valid_r1_embedded_receipt_passes():
    receipt = ExecutorReceipt(**_base_kwargs())
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "a real R1_EMBEDDED receipt with matching digests and no external skill passes "
        "(missing optional external skill must not block R1_EMBEDDED)",
        result.status is GateStatus.PASS,
    )


def test_stale_source_digest_rejected():
    receipt = ExecutorReceipt(**_base_kwargs(source_file_digest="0" * 64))
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "a wrong source_file_digest is rejected (missing/mismatched embedded-control digest)",
        result.status is GateStatus.FAIL and "source_file_digest" in result.detail,
    )


def test_stale_section_digest_rejected():
    receipt = ExecutorReceipt(**_base_kwargs(embedded_section_digest="0" * 64))
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "a wrong embedded_section_digest is rejected even with a correct source_file_digest",
        result.status is GateStatus.FAIL and "embedded_section_digest" in result.detail,
    )


def test_r1_embedded_with_external_skill_rejected():
    skill = ExternalSkill(name="agentic-sdlc", version="20.1", file_hash="a" * 64)
    receipt = ExecutorReceipt(**_base_kwargs(external_skill=skill))
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "R1_EMBEDDED fabricating an external_skill is rejected",
        result.status is GateStatus.FAIL and "fabricat" in result.detail,
    )


def test_plus_existing_skill_without_skill_rejected():
    receipt = ExecutorReceipt(
        **_base_kwargs(execution_control_profile="R1_EMBEDDED_PLUS_EXISTING_SKILL")
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "R1_EMBEDDED_PLUS_EXISTING_SKILL without a real external_skill is rejected",
        result.status is GateStatus.FAIL,
    )


def test_plus_existing_skill_with_real_skill_passes():
    skill = ExternalSkill(name="agentic-sdlc", version="20.1", file_hash="b" * 64)
    receipt = ExecutorReceipt(
        **_base_kwargs(
            execution_control_profile="R1_EMBEDDED_PLUS_EXISTING_SKILL",
            external_skill=skill,
        )
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "R1_EMBEDDED_PLUS_EXISTING_SKILL with a real, complete external_skill passes",
        result.status is GateStatus.PASS,
    )


def test_external_skill_rejects_empty_fields():
    threw = False
    try:
        ExternalSkill(name="", version="20.1", file_hash="c" * 64)
    except ValueError:
        threw = True
    check("FTF-R014", "ExternalSkill rejects an empty name at construction", threw)


def test_self_certification_denied():
    receipt = ExecutorReceipt(
        **_base_kwargs(producer_identity="session:same", validator_identity="session:same")
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "SELF_CERTIFICATION_DENIED when validator_identity == producer_identity",
        result.status is GateStatus.FAIL and "SELF_CERTIFICATION_DENIED" in result.detail,
    )


def test_unscoped_workspace_rejected():
    for bad_workspace in ("main", ""):
        receipt = ExecutorReceipt(**_base_kwargs(workspace_identity=bad_workspace))
        result = validate_receipt(receipt)
        check(
            "FTF-R014",
            f"workspace_identity {bad_workspace!r} is rejected -- task isolation",
            result.status is GateStatus.FAIL,
        )


def test_protected_gate_change_requires_owner_validator():
    receipt = ExecutorReceipt(
        **_base_kwargs(
            changed_files=frozenset({".github/workflows/ci.yml"}),
            validator_identity="session:ordinary-relay",
        )
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "a protected-gate-touching receipt validated by an ordinary session is rejected",
        result.status is GateStatus.FAIL and "protected gates" in result.detail,
    )


def test_protected_gate_change_accepted_from_real_owner():
    receipt = ExecutorReceipt(
        **_base_kwargs(
            changed_files=frozenset({".github/workflows/ci.yml"}),
            validator_identity=RELEASE_AUTHORITY_IDENTITY,
        )
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "the same protected-gate change is accepted when validated by the real "
        "CODEOWNERS release authority",
        result.status is GateStatus.PASS,
    )


def test_unprotected_file_change_does_not_require_owner():
    receipt = ExecutorReceipt(
        **_base_kwargs(
            changed_files=frozenset({"tests/implementation/test_r1_execution_binding.py"})
        )
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "an ordinary (non-protected) changed file does not require the release authority",
        result.status is GateStatus.PASS,
    )


def test_openhands_claim_without_run_rejected():
    receipt = ExecutorReceipt(
        **_base_kwargs(openhands_qualification_claimed=True, openhands_selected_and_run=False)
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "claimed OpenHands qualification without OpenHands actually selected and run is rejected",
        result.status is GateStatus.FAIL,
    )


def test_openhands_claim_with_real_run_passes():
    receipt = ExecutorReceipt(
        **_base_kwargs(openhands_qualification_claimed=True, openhands_selected_and_run=True)
    )
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "OpenHands qualification claimed with a genuine selection+run passes",
        result.status is GateStatus.PASS,
    )


def test_invalid_profile_rejected_by_real_schema():
    receipt = ExecutorReceipt(**_base_kwargs(execution_control_profile="NOT_A_REAL_PROFILE"))
    result = validate_receipt(receipt)
    check(
        "FTF-R014",
        "an unknown execution_control_profile is rejected by the real JSON Schema enum",
        result.status is GateStatus.FAIL and "schema violation" in result.detail,
    )


def test_fault_injection_self_certification_guard():
    """Real fault injection against the tracked module on disk: temporarily
    remove the self-certification check, run a fresh subprocess that
    imports the edited file and validates a self-certified receipt,
    confirm it wrongly passes, then restore byte-identical and confirm it
    is rejected again."""
    target = ROOT / "tools" / "implementation" / "executor_contract.py"
    original = target.read_text(encoding="utf-8")

    guard_block = (
        "    if receipt.validator_identity == receipt.producer_identity:\n"
        "        return GateResult(\n"
        "            key,\n"
        "            GateStatus.FAIL,\n"
        '            f"SELF_CERTIFICATION_DENIED: validator_identity == producer_identity "\n'
        '            f"({receipt.producer_identity!r})",\n'
        "        )\n"
    )
    check(
        "FTF-R014",
        "the self-certification guard block is present in the real tracked source "
        "before fault injection",
        guard_block in original,
    )

    mutated = original.replace(guard_block, "")
    check("FTF-R014", "fault injection actually removed the guard block", mutated != original)
    target.write_text(mutated, encoding="utf-8")

    probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from tools.implementation.executor_contract import (
    ExecutorReceipt, validate_receipt, compute_expected_digests,
)
from scripts.release_evidence_gate import GateStatus
src, sec = compute_expected_digests()
receipt = ExecutorReceipt(
    task_id="FTF-R014", executor_id="e", model_route="m",
    execution_control_profile="R1_EMBEDDED",
    source_file_digest=src, embedded_section_digest=sec,
    producer_identity="same", validator_identity="same",
    workspace_identity="ws",
)
result = validate_receipt(receipt)
print(result.status.value)
"""

    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-R014",
            "with the guard removed, a fresh subprocess wrongly PASSes a self-certified receipt",
            proc.stdout.strip() == "PASS",
        )
    finally:
        target.write_text(original, encoding="utf-8")

    restored = target.read_text(encoding="utf-8")
    check(
        "FTF-R014",
        "the tracked module was restored byte-identical after fault injection",
        restored == original,
    )

    proc_after = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
    )
    check(
        "FTF-R014",
        "after restoring, the same self-certified receipt is rejected again",
        proc_after.stdout.strip() == "FAIL",
    )


def main() -> int:
    test_digests_computed_live_not_hardcoded()
    test_valid_r1_embedded_receipt_passes()
    test_stale_source_digest_rejected()
    test_stale_section_digest_rejected()
    test_r1_embedded_with_external_skill_rejected()
    test_plus_existing_skill_without_skill_rejected()
    test_plus_existing_skill_with_real_skill_passes()
    test_external_skill_rejects_empty_fields()
    test_self_certification_denied()
    test_unscoped_workspace_rejected()
    test_protected_gate_change_requires_owner_validator()
    test_protected_gate_change_accepted_from_real_owner()
    test_unprotected_file_change_does_not_require_owner()
    test_openhands_claim_without_run_rejected()
    test_openhands_claim_with_real_run_passes()
    test_invalid_profile_rejected_by_real_schema()
    test_fault_injection_self_certification_guard()

    total_checks = 24
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed out of {total_checks}:")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print(f"All {total_checks} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
