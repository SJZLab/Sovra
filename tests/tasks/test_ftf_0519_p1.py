#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,list-item,unused-ignore,func-returns-value,attr-defined,misc,assignment,index,var-annotated,union-attr,operator,return-value,no-any-return"
# ruff: noqa: E501
"""FTF-0519-P1 -- immutable split manifest: pure domain, migration 0030 and store.

Runnable without a database or network (the live Postgres evidence is
`tests/integration/test_ftf_0519_p1_split_manifests.py`, run by CI's
`dataset-split-manifest-integration` job). Three parts:

P  `workers.data.immutable_split_manifest`: the fixed role <-> purpose map and the pinned purposes,
   role hashes (golden value, order independence, `PYTHONHASHSEED` independence, one field at a
   time), role rows, the frozen content and its two hashes (every field moves them), the leakage
   report round trip and its malformed-body matrix, the explicit legacy three-split migration, the
   training / development row guards.
M  migration `0030_dataset_split_manifests.py`: chained onto 0029 as the single head, ENABLE + FORCE
   RLS and a non-allow-all `tenant_isolation` policy on all four tables, no DELETE grant, the exact
   column-limited UPDATE, the CHECK / foreign-key / trigger names the contract names -- each shown
   able to fail by editing the file on disk.
S  `dataset_split_manifest_store` over a real SQLite session (only the Postgres tenant guard, the
   audit writer and the connection fetch are replaced; the quality ledger, FTF-0517's allocator and
   FTF-0518's detector are the real ones): propose, version on change, every refusal with its stable
   code writing nothing, approve / reject, and the training / development readers.

Acceptance ("Freeze hashes/counts/roles/grants for all four dataset purposes and version on
change"): ACC-FREEZE-*, ACC-VERSION-*. Negative acceptance ("Old three-split manifests are migrated
explicitly; final set cannot be returned under training access"): NEG-LEGACY-*, NEG-FINAL-*.

Run directly: python3 tests/tasks/test_ftf_0519_p1.py
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.security_profile_enforcement import Destination  # noqa: E402
from sqlalchemy import create_engine, func  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.data import immutable_split_manifest as ism  # noqa: E402
from workers.data.group_aware_split_engine import (  # noqa: E402
    ROLES,
    SplitRecord,
    SplitSpec,
    allocate_splits,
)
from workers.data.quality_issue_engine import REQUIRED_DETECTORS  # noqa: E402
from workers.data.split_leakage_detector import (  # noqa: E402
    EmbeddingRoute,
    LeakageRecord,
    PriorQualificationSource,
    compute_report_sha256,
    detect_split_leakage,
    report_body,
)

from app.datasets import dataset_quality_store as qstore  # noqa: E402
from app.datasets import dataset_source_connection_crud as crud  # noqa: E402
from app.datasets import dataset_split_manifest_store as store  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


class StrSub(str):
    """A str subclass: every exact-type check must refuse it."""


def raises(fn: Any, exc: type[BaseException] = ism.ManifestDomainError) -> bool:
    """True only if `fn` raises exactly the module's own error (never a raw TypeError)."""
    try:
        fn()
    except exc:
        return True
    except BaseException:  # noqa: BLE001 -- a foreign exception type is a failure
        return False
    return False


DS_A = "11111111-1111-4111-8111-111111111111"
DS_B = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
CONN = "44444444-4444-4444-8444-444444444444"
REPORT = "55555555-5555-4555-8555-555555555555"


def row(
    dataset: str = DS_A,
    position: int = 0,
    role: str = "train",
    group: str = "g0",
    raw: str | None = None,
    norm: str | None = None,
) -> ism.AssignmentRow:
    return ism.AssignmentRow(
        f"{dataset}:{position}",
        dataset,
        position,
        role,
        group,
        raw or sha(f"raw{dataset}{position}"),
        norm or sha(f"norm{dataset}{position}"),
    )


def part_p() -> None:
    # ---- P1 role / purpose map ------------------------------------------------------
    check(
        "ACC-FREEZE-ROLES",
        "the role <-> purpose map is exactly Section 9's",
        dict(ism.ROLE_PURPOSE)
        == {
            "train": "training",
            "validation": "validation",
            "development_eval": "candidate_development",
            "final_qualification": "held_out_evaluation",
        }
        and tuple(ism.ROLE_PURPOSE) == ROLES,
    )
    check(
        "NEG-FINAL-PIN",
        "held-out and development purposes pin their evaluation role; training/validation are free",
        ism.pinned_role_for_purpose("held_out_evaluation") == "final_qualification"
        and ism.pinned_role_for_purpose("candidate_development") == "development_eval"
        and ism.pinned_role_for_purpose("training") is None
        and ism.pinned_role_for_purpose("validation") is None
        and dict(ism.PINNED_PURPOSE_ROLE)
        == {
            "candidate_development": "development_eval",
            "held_out_evaluation": "final_qualification",
        },
    )
    for bad in (
        "baseline_only",
        "heldout_test",
        "",
        "Training",
        "training ",
        None,
        3,
        [],
        {},
        StrSub("training"),
    ):
        check(
            "NEG-FINAL-PIN",
            f"{bad!r} is not a split purpose",
            raises(lambda bad=bad: ism.pinned_role_for_purpose(bad)),
        )
    check(
        "NEG-FINAL-PIN",
        "training roles are train/validation, development is development_eval, the legacy roles are fixed",
        ism.TRAINING_ROLES == ("train", "validation")
        and ism.DEVELOPMENT_ROLES == ("development_eval",)
        and ism.LEGACY_ROLES == ("train", "validation", "heldout_test")
        and ism.LEGACY_STRATEGY == "legacy_three_split_v1"
        and ism.MAX_MANIFEST_RECORDS == 1_000_000,
    )

    # ---- P2 AssignmentRow ------------------------------------------------------------
    good = row()
    check("ACC-FREEZE-ROWS", "a valid assignment row is accepted", good.role == "train")
    bad_rows: list[tuple[str, dict[str, Any]]] = [
        ("record id not '<dataset>:<position>'", {"record_id": f"{DS_A}:1"}),
        ("blank record id", {"record_id": ""}),
        ("padded record id", {"record_id": f" {DS_A}:0"}),
        ("record id str subclass", {"record_id": StrSub(f"{DS_A}:0")}),
        (
            "non-canonical dataset uuid",
            {"source_dataset_id": DS_A.replace("-", ""), "record_id": f"{DS_A.replace('-', '')}:0"},
        ),
        ("dataset id not a string", {"source_dataset_id": uuid.UUID(DS_A)}),
        ("dataset id garbage", {"source_dataset_id": "nope"}),
        ("bool position", {"batch_position": True}),
        ("negative position", {"batch_position": -1}),
        ("huge position", {"batch_position": 2**31}),
        ("float position", {"batch_position": 0.0}),
        ("unknown role", {"role": "heldout_test"}),
        ("unhashable role", {"role": []}),
        ("role subclass", {"role": StrSub("train")}),
        ("blank group", {"group_id": ""}),
        ("bad raw hash", {"record_sha256": "A" * 64}),
        ("short normalized hash", {"normalized_sha256": "a" * 63}),
        ("hash subclass", {"record_sha256": StrSub("a" * 64)}),
    ]
    for label, over in bad_rows:
        values = {
            "record_id": f"{DS_A}:0",
            "source_dataset_id": DS_A,
            "batch_position": 0,
            "role": "train",
            "group_id": "g0",
            "record_sha256": "a" * 64,
            "normalized_sha256": "b" * 64,
            **over,
        }
        check(
            "ACC-FREEZE-ROWS",
            f"assignment row refuses {label}",
            raises(lambda v=values: ism.AssignmentRow(**v)),
        )
    check(
        "ACC-FREEZE-ROWS",
        "the longest accepted ids are 400 characters",
        ism.AssignmentRow(f"{DS_A}:0", DS_A, 0, "train", "g" * 400, "a" * 64, "b" * 64).group_id
        == "g" * 400
        and raises(lambda: row(group="g" * 401)),
    )

    # ---- P3 role hash ----------------------------------------------------------------
    pinned = [
        row(DS_A, 0, "train", "g0", "a" * 64, "b" * 64),
        row(DS_A, 1, "train", "g1", "c" * 64, "d" * 64),
    ]
    golden = ism.role_sha256(pinned)
    expected_body = json.dumps(
        [
            [f"{DS_A}:0", "a" * 64, "b" * 64, "g0"],
            [f"{DS_A}:1", "c" * 64, "d" * 64, "g1"],
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    check(
        "ACC-FREEZE-HASH",
        "role_sha256 is sha256 of the sorted (record_id, raw, normalized, group) rows as canonical JSON",
        golden == hashlib.sha256(expected_body.encode()).hexdigest(),
    )
    check(
        "ACC-FREEZE-HASH",
        "role_sha256 golden value is pinned",
        golden == "096de354cab04cd07184c327d5e4e99a85ced1b4d63d1b5ead1d5bb0701316c6",
    )
    check(
        "ACC-FREEZE-HASH", "role_sha256 ignores row order", ism.role_sha256(pinned[::-1]) == golden
    )
    for label, changed in (
        ("record id", replace(pinned[1], record_id=f"{DS_A}:5", batch_position=5)),
        ("raw hash", replace(pinned[1], record_sha256="e" * 64)),
        ("normalized hash", replace(pinned[1], normalized_sha256="e" * 64)),
        ("group", replace(pinned[1], group_id="g9")),
    ):
        check(
            "ACC-FREEZE-HASH",
            f"role_sha256 changes with the {label}",
            ism.role_sha256([pinned[0], changed]) != golden,
        )
    check(
        "ACC-FREEZE-HASH",
        "role_sha256 does not depend on the role label (the role rows carry it)",
        ism.role_sha256([replace(r, role="validation") for r in pinned]) == golden,
    )
    check("ACC-FREEZE-HASH", "role_sha256 of no rows is defined", len(ism.role_sha256([])) == 64)
    script = (
        f"import sys; sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'apps' / 'api')!r}]\n"
        "import hashlib\n"
        "from workers.data import immutable_split_manifest as m\n"
        f"rows=[m.AssignmentRow('{DS_A}:'+str(i),'{DS_A}',i,'train','g'+str(i%7),"
        "hashlib.sha256(str(i).encode()).hexdigest(),hashlib.sha256(str(-i).encode()).hexdigest())"
        " for i in range(60)]\n"
        "print(m.role_sha256(rows))\n"
    )
    outputs = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env
        )
        outputs.add(done.stdout.strip())
    check(
        "ACC-FREEZE-HASH",
        f"role_sha256 is PYTHONHASHSEED independent: {outputs}",
        len(outputs) == 1 and len(next(iter(outputs))) == 64,
    )

    # ---- P4 role rows ----------------------------------------------------------------
    rows = [
        row(DS_A, 0, "train", "g0"),
        row(DS_A, 1, "train", "g0"),
        row(DS_A, 2, "validation", "g1"),
        row(DS_B, 0, "development_eval", "g2"),
        row(DS_B, 1, "final_qualification", "g3"),
        row(DS_B, 2, "final_qualification", "g4"),
    ]
    roles = ism.build_role_rows(rows)
    check(
        "ACC-FREEZE-ROLES",
        "four role rows in ROLES order with exact counts and the fixed purposes",
        [r.role for r in roles] == list(ROLES)
        and [(r.record_count, r.group_count) for r in roles] == [(2, 1), (1, 1), (1, 1), (2, 2)]
        and [r.purpose for r in roles] == [ism.ROLE_PURPOSE[r] for r in ROLES]
        and roles[0].role_sha256 == ism.role_sha256(rows[:2])
        and roles[3].role_sha256 == ism.role_sha256(rows[4:]),
    )
    empty_roles = ism.build_role_rows([row(DS_A, 0, "train", "g0")])
    check(
        "ACC-FREEZE-ROLES",
        "an empty role is a row with zero counts, never missing",
        [(r.record_count, r.group_count) for r in empty_roles] == [(1, 1), (0, 0), (0, 0), (0, 0)],
    )
    check(
        "ACC-FREEZE-ROLES",
        "role rows are order independent",
        ism.build_role_rows(rows[::-1]) == roles,
    )
    check(
        "ACC-FREEZE-ROLES",
        "a duplicate record id is refused",
        raises(lambda: ism.build_role_rows([rows[0], rows[0]])),
    )
    check("ACC-FREEZE-ROLES", "non-rows are refused", raises(lambda: ism.build_role_rows([("a",)])))
    check("ACC-FREEZE-ROLES", "a non-list is refused", raises(lambda: ism.build_role_rows("abc")))
    check(
        "ACC-FREEZE-ROLES",
        "a set is refused (order is not defined)",
        raises(lambda: ism.build_role_rows({rows[0]})),
    )
    for label, args in (
        ("a purpose that does not match", ("train", "validation", 1, 1, "a" * 64)),
        ("unknown role", ("heldout_test", "held_out_evaluation", 1, 1, "a" * 64)),
        ("more groups than records", ("train", "training", 1, 2, "a" * 64)),
        ("records without groups", ("train", "training", 1, 0, "a" * 64)),
        ("groups without records", ("train", "training", 0, 1, "a" * 64)),
        ("negative count", ("train", "training", -1, 0, "a" * 64)),
        ("bool count", ("train", "training", True, True, "a" * 64)),
        ("over the cap", ("train", "training", 1_000_001, 1, "a" * 64)),
        ("bad hash", ("train", "training", 1, 1, "z" * 64)),
    ):
        check(
            "ACC-FREEZE-ROLES", f"RoleRow refuses {label}", raises(lambda a=args: ism.RoleRow(*a))
        )
    check(
        "ACC-FREEZE-ROLES",
        "RoleRow accepts the cap",
        ism.RoleRow("train", "training", 1_000_000, 1_000_000, "a" * 64).record_count == 1_000_000,
    )

    # ---- P5 build_assignment_rows ----------------------------------------------------
    records = [SplitRecord(f"{DS_A}:{i}", (f"{DS_A}:{i}",), None, None) for i in range(30)]
    spec = SplitSpec("sha256_group_hash_v1", "seed", (70, 10, 10, 10))
    manifest = allocate_splits(spec, records)
    origins = {f"{DS_A}:{i}": (DS_A, i, sha(f"r{i}"), sha(f"n{i}")) for i in range(30)}
    built = ism.build_assignment_rows(manifest, origins)
    check(
        "ACC-FREEZE-ROWS",
        "assignment rows carry the manifest's role and group and the origin's hashes",
        len(built) == 30
        and all(
            (r.record_id, r.role, r.group_id) == a
            for r, a in zip(built, manifest.assignments, strict=True)
        )
        and built[3].record_sha256 == sha(f"r{int(built[3].record_id.split(':')[1])}"),
    )
    check(
        "ACC-FREEZE-ROWS",
        "an origin missing a record is refused",
        raises(lambda: ism.build_assignment_rows(manifest, dict(list(origins.items())[1:]))),
    )
    extra = {**origins, f"{DS_A}:99": (DS_A, 99, sha("x"), sha("y"))}
    check(
        "ACC-FREEZE-ROWS",
        "an origin for a record that is not in the manifest is refused",
        raises(lambda: ism.build_assignment_rows(manifest, extra)),
    )
    tampered = replace(manifest, seed="other")
    check(
        "ACC-FREEZE-ROWS",
        "a tampered split manifest is refused",
        raises(lambda: ism.build_assignment_rows(tampered, origins)),
    )

    # ---- P6 excluded positions -------------------------------------------------------
    check(
        "ACC-FREEZE-GRANT",
        "excluded_positions_sha256 is the hash of the sorted positions",
        ism.excluded_positions_sha256([0, 1, 2])
        == hashlib.sha256(b'{"excluded_batch_positions":[0,1,2]}').hexdigest()
        and ism.excluded_positions_sha256(())
        == hashlib.sha256(b'{"excluded_batch_positions":[]}').hexdigest()
        and ism.excluded_positions_sha256((0, 1)) != ism.excluded_positions_sha256((0, 2)),
    )
    for bad in ([2, 1], [1, 1], [-1], [True], ["1"], [1.0], [2**31], "ab", {1}, None):
        check(
            "ACC-FREEZE-GRANT",
            f"excluded positions {bad!r} are refused",
            raises(lambda bad=bad: ism.excluded_positions_sha256(bad)),
        )

    # ---- P7 SourceGrant --------------------------------------------------------------
    def grant(**over: Any) -> ism.SourceGrant:
        values = {
            "dataset_id": DS_A,
            "dataset_version": 1,
            "dataset_purpose": "training",
            "dataset_sha256": "a" * 64,
            "dataset_source_connection_id": CONN,
            "allowed_purposes_snapshot": ("held_out_evaluation", "training"),
            "quality_report_id": REPORT,
            "quality_report_sha256": "b" * 64,
            "excluded_positions_sha256": "c" * 64,
            **over,
        }
        return ism.SourceGrant(**values)

    check("ACC-FREEZE-GRANT", "a valid grant is accepted", grant().dataset_purpose == "training")
    for label, over in (
        ("a version of zero", {"dataset_version": 0}),
        ("bool version", {"dataset_version": True}),
        ("baseline_only purpose", {"dataset_purpose": "baseline_only"}),
        ("bad dataset hash", {"dataset_sha256": "A" * 64}),
        ("bad connection id", {"dataset_source_connection_id": "x"}),
        ("list snapshot", {"allowed_purposes_snapshot": ["training"]}),
        ("empty snapshot", {"allowed_purposes_snapshot": ()}),
        ("unsorted snapshot", {"allowed_purposes_snapshot": ("training", "held_out_evaluation")}),
        ("duplicate snapshot", {"allowed_purposes_snapshot": ("training", "training")}),
        ("blank purpose in snapshot", {"allowed_purposes_snapshot": ("",)}),
        ("bad report id", {"quality_report_id": REPORT.replace("-", "")}),
        ("bad report hash", {"quality_report_sha256": "b" * 65}),
        ("bad exclusion hash", {"excluded_positions_sha256": "c" * 63}),
    ):
        check(
            "ACC-FREEZE-GRANT",
            f"SourceGrant refuses {label}",
            raises(lambda over=over: grant(**over)),
        )

    part_p2(roles, manifest, origins, built, grant)


def make_content(
    base_roles: tuple[ism.RoleRow, ...],
    grant_factory: Any,
    *,
    reviewed: bool = False,
    **over: Any,
) -> ism.ManifestContent:
    body = {
        "status": "clear",
        "reason_code": None,
        "findings": [],
        "issues": [],
        "methods_run": list(
            (
                "exact_raw",
                "canonical_normalized",
                "text_normalized",
                "group",
                "char_ngram",
                "token_jaccard",
                "embedding",
                "agent_trace_structural",
            )
        ),
        "methods_not_run": [],
        "reviews": [],
    }
    report = ism.report_from_body(body)
    values: dict[str, Any] = {
        "project_id": PROJECT,
        "name": "campaign",
        "version": 1,
        "parent_version": None,
        "strategy": "sha256_group_hash_v1",
        "seed": "seed",
        "ratios": (70, 10, 10, 10),
        "cutoff": None,
        "normalization_version": "norm-v1",
        "tokenizer_identity": None,
        "split_manifest_sha256": sha("manifest"),
        "leakage_report": body,
        "leakage_report_sha256": report.report_sha256,
        "leakage_status": "clear",
        "roles": base_roles,
        "sources": (grant_factory(),),
    }
    if reviewed:
        values.update(
            reviewed_leakage_report_sha256=report.report_sha256,
            approved_by="approver",
        )
    values.update(over)
    return ism.ManifestContent(**values)


def part_p2(roles: Any, manifest: Any, origins: Any, built: Any, grant: Any) -> None:
    content = make_content(roles, grant)
    base = ism.compute_proposal_sha256(content)
    check(
        "ACC-FREEZE-HASH",
        "a proposal hash is 64 hex characters",
        re.fullmatch(r"[0-9a-f]{64}", base) is not None,
    )
    # Every field of the proposal moves its hash.
    variants: dict[str, Any] = {
        "project_id": "66666666-6666-4666-8666-666666666666",
        "name": "other",
        "version": 2,
        "strategy": "other_strategy",
        "seed": "another-seed",
        "ratios": (60, 20, 10, 10),
        "cutoff": 5,
        "normalization_version": "norm-v2",
        "tokenizer_identity": "tok",
        "split_manifest_sha256": sha("other manifest"),
        "leakage_report_sha256": sha("other report"),
        "roles": (replace(roles[0], role_sha256=sha("x")), *roles[1:]),
        "sources": (grant(dataset_version=2),),
    }
    for field, value in variants.items():
        extra: dict[str, Any] = {}
        if field == "version":
            extra["parent_version"] = 1
        if field == "strategy":
            extra["migrated_from_legacy_sha256"] = None
        try:
            other = replace(content, **{field: value}, **extra)
        except ism.ManifestDomainError:
            other = None
        check(
            "ACC-FREEZE-HASH",
            f"the proposal hash covers {field}",
            other is not None and ism.compute_proposal_sha256(other) != base,
        )
    check(
        "ACC-FREEZE-HASH",
        "the proposal hash covers the leakage status, the legacy lineage and the parent",
        ism.compute_proposal_sha256(replace(content, leakage_status="blocked")) != base
        and ism.compute_proposal_sha256(
            replace(content, strategy="legacy_three_split_v1", migrated_from_legacy_sha256=sha("l"))
        )
        != base
        and ism.compute_proposal_sha256(replace(content, version=2, parent_version=1)) != base,
    )
    check(
        "ACC-FREEZE-HASH",
        "the proposal hash ignores reviews and approver (they are added at the lock)",
        ism.compute_proposal_sha256(
            make_content(roles, grant, reviewed=True, leakage_reviews=({"finding_id": "f"},))
        )
        == base,
    )
    check(
        "ACC-FREEZE-HASH",
        "an unreviewed manifest has no frozen hash",
        raises(lambda: ism.compute_frozen_manifest_sha256(content)),
    )
    check(
        "ACC-FREEZE-HASH",
        "an unapproved manifest has no frozen hash",
        raises(
            lambda: ism.compute_frozen_manifest_sha256(
                replace(content, reviewed_leakage_report_sha256=sha("r"))
            )
        ),
    )
    reviewed = make_content(roles, grant, reviewed=True)
    frozen = ism.compute_frozen_manifest_sha256(reviewed)
    check(
        "ACC-FREEZE-HASH",
        "the frozen hash is 64 hex characters",
        re.fullmatch(r"[0-9a-f]{64}", frozen) is not None,
    )
    ism.verify_frozen_content(reviewed, frozen)
    check("ACC-FREEZE-HASH", "verify_frozen_content accepts the content it was computed from", True)
    frozen_variants: dict[str, Any] = {
        **{f: v for f, v in variants.items() if f not in ("strategy", "version")},
        "approved_by": "someone-else",
        "reviewed_leakage_report_sha256": sha("other reviewed"),
        "leakage_reviews": (
            {
                "finding_id": "f" * 64,
                "reviewer_id": "r",
                "decision": "not_leakage",
                "rationale": "x",
            },
        ),
        "version": 2,
    }
    for field, value in frozen_variants.items():
        extra = {"parent_version": 1} if field == "version" else {}
        other = replace(reviewed, **{field: value}, **extra)
        check(
            "ACC-FREEZE-HASH",
            f"the frozen hash covers {field}",
            raises(lambda other=other: ism.verify_frozen_content(other, frozen)),
        )
    check(
        "ACC-FREEZE-HASH",
        "the frozen hash covers the leakage report body",
        raises(
            lambda: ism.verify_frozen_content(
                replace(
                    reviewed,
                    leakage_report={**reviewed.leakage_report, "methods_not_run": ["embedding"]},
                ),
                frozen,
            )
        ),
    )
    check(
        "ACC-FREEZE-HASH",
        "the frozen hash covers the grant snapshot, the quality report and the exclusion set",
        all(
            raises(lambda g=g: ism.verify_frozen_content(replace(reviewed, sources=(g,)), frozen))
            for g in (
                grant(allowed_purposes_snapshot=("training",)),
                grant(quality_report_id="77777777-7777-4777-8777-777777777777"),
                grant(quality_report_sha256="d" * 64),
                grant(excluded_positions_sha256="d" * 64),
                grant(dataset_source_connection_id="88888888-8888-4888-8888-888888888888"),
                grant(dataset_purpose="validation"),
                grant(dataset_sha256="d" * 64),
            )
        ),
    )
    check(
        "ACC-FREEZE-HASH",
        "a malformed frozen hash is refused",
        raises(lambda: ism.verify_frozen_content(reviewed, "nope")),
    )
    check(
        "ACC-FREEZE-HASH",
        "a wrong frozen hash is refused",
        raises(lambda: ism.verify_frozen_content(reviewed, "0" * 64)),
    )
    wrong_report_hash = replace(reviewed, leakage_report_sha256=sha("lie"))
    check(
        "ACC-FREEZE-HASH",
        "a leakage report that does not hash to the recorded hash is refused even with a matching frozen hash",
        raises(
            lambda: ism.verify_frozen_content(
                wrong_report_hash, ism.compute_frozen_manifest_sha256(wrong_report_hash)
            )
        ),
    )

    # ---- ManifestContent validation matrix -------------------------------------------
    bad_values: dict[str, list[Any]] = {
        "project_id": ["x", PROJECT.replace("-", ""), 1, None],
        "name": ["", " x", "x ", StrSub("x"), 1, "n" * 201],
        "version": [0, True, "1", 2**31, 1.0],
        "strategy": ["", StrSub("s"), 1],
        "seed": ["", "s" * 129, StrSub("s")],
        "ratios": [
            [70, 10, 10, 10],
            (70, 10, 10),
            (70, 10, 10, True),
            (70, 10, 10, 101),
            (70, 10, 10, -1),
        ],
        "cutoff": [-1, True, "1", 1.5, 10**13 + 1],
        "normalization_version": ["", " v", "v" * 65, 1],
        "tokenizer_identity": ["", " t", 1, "t" * 201],
        "split_manifest_sha256": ["", "A" * 64, 1],
        "leakage_report": ["x", None, [], 1],
        "leakage_report_sha256": ["", "g" * 64],
        "leakage_status": ["maybe", "", None, ["clear"], StrSub("clear")],
        "roles": [(), roles[:3], (*roles[:3], "x"), roles[::-1], list(roles)],
        "sources": [(), [], ("x",), None],
        "leakage_reviews": [[], "x", None],
        "reviewed_leakage_report_sha256": ["", "A" * 64, 1],
        "migrated_from_legacy_sha256": ["", "A" * 64, 1],
        "approved_by": ["", " a", 1, "a" * 201],
    }
    for field, values in bad_values.items():
        for value in values:
            check(
                "ACC-FREEZE-ROWS",
                f"ManifestContent refuses {field}={value!r}"[:140],
                raises(
                    lambda field=field, value=value: make_content(roles, grant, **{field: value})
                ),
            )
    check(
        "ACC-FREEZE-ROWS",
        "version 1 has no parent; version n > 1 has parent n - 1",
        raises(lambda: make_content(roles, grant, version=1, parent_version=1))
        and raises(lambda: make_content(roles, grant, version=2, parent_version=None))
        and raises(lambda: make_content(roles, grant, version=3, parent_version=1))
        and raises(lambda: make_content(roles, grant, version=2, parent_version=True))
        and make_content(roles, grant, version=3, parent_version=2).parent_version == 2,
    )
    check(
        "NEG-LEGACY-LINEAGE",
        "the legacy strategy and its lineage hash go together",
        raises(lambda: make_content(roles, grant, strategy="legacy_three_split_v1"))
        and raises(lambda: make_content(roles, grant, migrated_from_legacy_sha256=sha("l")))
        and make_content(
            roles, grant, strategy="legacy_three_split_v1", migrated_from_legacy_sha256=sha("l")
        ).strategy
        == "legacy_three_split_v1",
    )
    check(
        "ACC-FREEZE-ROWS",
        "sources must be distinct and ordered by dataset id",
        raises(lambda: make_content(roles, grant, sources=(grant(), grant())))
        and raises(
            lambda: make_content(
                roles, grant, sources=(grant(dataset_id=DS_B), grant(dataset_id=DS_A))
            )
        )
        and make_content(roles, grant, sources=(grant(dataset_id=DS_A), grant(dataset_id=DS_B)))
        .sources[1]
        .dataset_id
        == DS_B,
    )
    check(
        "ACC-FREEZE-ROWS",
        "a cutoff of zero and a four-digit seed are accepted",
        make_content(roles, grant, cutoff=0).cutoff == 0
        and make_content(roles, grant, seed="s" * 128).seed == "s" * 128,
    )
    part_report(roles, grant, manifest, origins)
    part_legacy()
    part_views()


def real_report() -> tuple[Any, Any]:
    """A real FTF-0518 report over a real FTF-0517 manifest, with a prior campaign."""
    records = [SplitRecord(f"{DS_A}:{i}", (f"{DS_A}:{i}",), None, None) for i in range(40)]
    manifest = allocate_splits(SplitSpec("sha256_group_hash_v1", "rp", (50, 20, 20, 10)), records)
    leak = [
        LeakageRecord(
            record_id=f"{DS_A}:{i}",
            content={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": sha(f"t{i}")[:30] + " " + sha(f"u{i}")[:30]}
                        ],
                    }
                ],
                "metadata": {},
            },
            record_sha256=sha(f"raw{i}"),
            normalized_sha256=sha(f"norm{i}"),
        )
        for i in range(40)
    ]
    route = EmbeddingRoute(Destination(kind="internal"), "standard", "standard")
    return detect_split_leakage(manifest, leak, route=route), manifest


def part_report(roles: Any, grant: Any, manifest: Any, origins: Any) -> None:
    report, _ = real_report()
    body = report_body(report)
    rebuilt = ism.report_from_body(json.loads(json.dumps(body)))
    check(
        "ACC-FREEZE-REPORT",
        "a stored report body rebuilds to the same hash after a JSON round trip",
        rebuilt.report_sha256 == report.report_sha256 == compute_report_sha256(rebuilt)
        and rebuilt.status == report.status,
    )
    check(
        "ACC-FREEZE-REPORT",
        "a clear report has every method run",
        report.status == "clear" and not report.methods_not_run,
    )
    bad_bodies: list[tuple[str, Any]] = [
        ("not a mapping", []),
        ("a missing key", {k: v for k, v in body.items() if k != "issues"}),
        ("an extra key", {**body, "extra": 1}),
        ("status not a string", {**body, "status": 1}),
        ("unknown status", {**body, "status": "fine"}),
        ("findings not a list", {**body, "findings": ()}),
        ("a finding that is not a mapping", {**body, "findings": ["x"]}),
        ("a finding with a missing key", {**body, "findings": [{"finding_id": "a" * 64}]}),
        ("methods_run not a list", {**body, "methods_run": "abc"}),
        ("an unknown method", {**body, "methods_run": ["nope"]}),
        (
            "a clear report that skipped a method",
            {
                **body,
                "methods_run": body["methods_run"][:-1],
                "methods_not_run": [body["methods_run"][-1]],
            },
        ),
        ("reviews not a list", {**body, "reviews": {}}),
        ("a review with the wrong keys", {**body, "reviews": [{"finding_id": "a" * 64}]}),
        ("an unhashable status", {**body, "status": []}),
        ("an issue with the wrong keys", {**body, "issues": [{"code": "invalid_content"}]}),
        (
            "an issue with an unknown code",
            {**body, "issues": [{"code": "x", "record_id": None, "source_label": None}]},
        ),
    ]
    for label, bad in bad_bodies:
        check(
            "ACC-FREEZE-REPORT",
            f"report_from_body refuses {label} with the module's own error",
            raises(lambda bad=bad: ism.report_from_body(bad)),
        )
    finding = {
        "finding_id": "a" * 64,
        "method": "exact_raw",
        "reason": "cross_role",
        "disposition": "blocking",
        "record_id_a": "a",
        "role_a": "train",
        "record_id_b": "b",
        "role_b": "final_qualification",
        "prior_source_label": None,
        "scores": [["exact", 1]],
    }
    check(
        "ACC-FREEZE-REPORT",
        "a finding whose id does not follow from its fields is refused",
        raises(
            lambda: ism.report_from_body({**body, "status": "leakage_found", "findings": [finding]})
        ),
    )
    for label, scores in (
        ("a score pair of the wrong length", [["exact"]]),
        ("a score name that is not a string", [[1, 1.0]]),
        ("a score value that is not a number", [["exact", "1"]]),
        ("a bool score", [["exact", True]]),
        ("scores not a list", "x"),
    ):
        check(
            "ACC-FREEZE-REPORT",
            f"report_from_body refuses {label}",
            raises(
                lambda scores=scores: ism.report_from_body(
                    {**body, "findings": [{**finding, "scores": scores}]}
                )
            ),
        )


def part_legacy() -> None:
    def legacy_manifest(seed: str, assignments: tuple[Any, ...]) -> ism.LegacyThreeSplitManifest:
        return ism.LegacyThreeSplitManifest(
            seed, assignments, ism.compute_legacy_sha256(seed, assignments)
        )

    assignments = (
        ("r1", "train", "g1"),
        ("r2", "train", "g2"),
        ("r3", "validation", "g3"),
        ("r4", "heldout_test", "g4"),
        ("r5", "heldout_test", "g5"),
    )
    legacy = legacy_manifest("seed", assignments)
    migrated = ism.migrate_legacy_three_split(legacy)
    roles = {rid: role for rid, role, _ in migrated.assignments}
    check(
        "NEG-LEGACY-MAP",
        "heldout_test becomes final_qualification only; train and validation keep their roles",
        roles
        == {
            "r1": "train",
            "r2": "train",
            "r3": "validation",
            "r4": "final_qualification",
            "r5": "final_qualification",
        },
    )
    check(
        "NEG-LEGACY-MAP",
        "heldout_test never becomes train, validation or development",
        all(roles[r] == "final_qualification" for r in ("r4", "r5")),
    )
    check(
        "NEG-LEGACY-INSUFFICIENT",
        "without a development source the migrated manifest is insufficient and cannot lock",
        migrated.status == "insufficient"
        and dict((r, c) for r, c, _ in migrated.counts)["development_eval"] == 0,
    )
    check(
        "NEG-LEGACY-MAP",
        "the migrated manifest keeps the legacy strategy, seed, zero ratios and a verified hash",
        migrated.strategy == "legacy_three_split_v1"
        and migrated.seed == "seed"
        and migrated.ratios == (0, 0, 0, 0)
        and migrated.cutoff is None
        and migrated.manifest_sha256 == compute_manifest_sha256_of(migrated),
    )
    full = ism.migrate_legacy_three_split(legacy, [("d1", "gd1"), ("d2", "gd2")])
    check(
        "NEG-LEGACY-MAP",
        "an explicit development source gives development_eval rows and a ready manifest",
        full.status == "ready"
        and {rid for rid, role, _ in full.assignments if role == "development_eval"} == {"d1", "d2"}
        and dict((r, c) for r, c, _ in full.counts)
        == {"train": 2, "validation": 1, "development_eval": 2, "final_qualification": 2},
    )
    check(
        "NEG-LEGACY-MAP",
        "migration is deterministic and independent of input order",
        ism.migrate_legacy_three_split(legacy_manifest("seed", assignments[::-1])) == migrated
        and ism.migrate_legacy_three_split(legacy, [("d2", "gd2"), ("d1", "gd1")])
        == ism.migrate_legacy_three_split(legacy, [("d1", "gd1"), ("d2", "gd2")]),
    )
    check(
        "NEG-LEGACY-MAP",
        "a development record whose group is a legacy group would cross a boundary and is refused",
        raises(lambda: ism.migrate_legacy_three_split(legacy, [("d1", "g4")])),
    )
    check(
        "NEG-LEGACY-MAP",
        "a development id that collides with a legacy id is refused",
        raises(lambda: ism.migrate_legacy_three_split(legacy, [("r1", "gx")])),
    )
    check(
        "NEG-LEGACY-MAP",
        "a malformed development entry is refused",
        raises(lambda: ism.migrate_legacy_three_split(legacy, [("d1",)])),
    )
    check(
        "NEG-LEGACY-MAP",
        "development must be a list or tuple",
        raises(lambda: ism.migrate_legacy_three_split(legacy, "d1")),
    )
    check(
        "NEG-LEGACY-MAP",
        "only a LegacyThreeSplitManifest migrates",
        raises(lambda: ism.migrate_legacy_three_split({"x": 1})),
    )
    check(
        "NEG-LEGACY-MAP",
        "a group shared by a train and a heldout_test record is refused",
        raises(
            lambda: ism.migrate_legacy_three_split(
                legacy_manifest("s", (("a", "train", "g"), ("b", "heldout_test", "g")))
            )
        ),
    )
    check(
        "NEG-LEGACY-MAP",
        "a duplicate legacy record id is refused",
        raises(
            lambda: ism.migrate_legacy_three_split(
                legacy_manifest("s", (("a", "train", "g"), ("a", "validation", "h")))
            )
        ),
    )
    for label, make in (
        (
            "a hash that does not cover the content",
            lambda: ism.LegacyThreeSplitManifest("seed", assignments, sha("lie")),
        ),
        ("a malformed hash", lambda: ism.LegacyThreeSplitManifest("seed", assignments, "x")),
        (
            "a four-role assignment",
            lambda: legacy_manifest("seed", (("a", "development_eval", "g"),)),
        ),
        ("an unknown role", lambda: legacy_manifest("seed", (("a", "test", "g"),))),
        ("an unhashable role", lambda: legacy_manifest("seed", (("a", [], "g"),))),
        (
            "a list of assignments",
            lambda: ism.LegacyThreeSplitManifest("seed", list(assignments), sha("x")),
        ),
        ("no assignments", lambda: legacy_manifest("seed", ())),
        ("a malformed assignment", lambda: legacy_manifest("seed", (("a", "train"),))),
        ("a blank record id", lambda: legacy_manifest("seed", (("", "train", "g"),))),
        ("a blank group", lambda: legacy_manifest("seed", (("a", "train", ""),))),
        ("a blank seed", lambda: legacy_manifest("", assignments)),
        ("an over-long seed", lambda: legacy_manifest("s" * 129, assignments)),
    ):
        check("NEG-LEGACY-INPUT", f"LegacyThreeSplitManifest refuses {label}", raises(make))
    check(
        "NEG-LEGACY-INPUT",
        "a legacy seed of exactly 128 characters is accepted",
        legacy_manifest("s" * 128, assignments).seed == "s" * 128,
    )
    check(
        "NEG-LEGACY-INPUT",
        "the legacy hash covers the strategy name, the seed and every assignment",
        len(
            {
                ism.compute_legacy_sha256("s", assignments),
                ism.compute_legacy_sha256("t", assignments),
                ism.compute_legacy_sha256("s", assignments[:-1]),
            }
        )
        == 3
        and ism.compute_legacy_sha256("s", ())
        == hashlib.sha256(
            b'{"assignments":[],"seed":"s","strategy":"legacy_three_split_v1"}'
        ).hexdigest(),
    )
    for value, expected in (
        (legacy, True),
        ("legacy_three_split_v1", True),
        ("heldout_test", True),
        ("sha256_group_hash_v1", False),
        ((60, 20, 20), True),
        ([60, 20, 20], True),
        ((70, 10, 10, 10), False),
        ((60, 20, 20.0), False),
        (("train", "validation", "heldout_test"), True),
        (("train", "validation"), False),
        (["heldout_test"], True),
        (SplitSpec("sha256_group_hash_v1", "s", (70, 10, 10, 10)), False),
        (None, False),
        (3, False),
        ({"strategy": "legacy_three_split_v1"}, False),
    ):
        check(
            "NEG-LEGACY-REFUSE",
            f"is_legacy_three_split({value!r}) is {expected}"[:120],
            ism.is_legacy_three_split(value) is expected,
        )


def compute_manifest_sha256_of(manifest: Any) -> str:
    from workers.data.group_aware_split_engine import compute_manifest_sha256

    return compute_manifest_sha256(manifest)


def part_views() -> None:
    train = row(DS_A, 0, "train")
    validation = row(DS_A, 1, "validation")
    dev = row(DS_B, 0, "development_eval")
    final = row(DS_B, 1, "final_qualification")
    check(
        "NEG-FINAL-VIEW",
        "a training view keeps train and validation rows",
        ism.assert_training_rows([train, validation]) == (train, validation),
    )
    for label, rows in (
        ("a final record", [train, final]),
        ("a development record", [dev, validation]),
        ("a non-row", [train, ("x",)]),
    ):
        check(
            "NEG-FINAL-VIEW",
            f"a training view refuses {label}",
            raises(lambda rows=rows: ism.assert_training_rows(rows)),
        )
    check("NEG-FINAL-VIEW", "an empty training view is empty", ism.assert_training_rows([]) == ())
    check(
        "NEG-FINAL-VIEW",
        "a development view keeps only development rows",
        ism.assert_development_rows([dev]) == (dev,)
        and raises(lambda: ism.assert_development_rows([dev, final]))
        and raises(lambda: ism.assert_development_rows([train]))
        and raises(lambda: ism.assert_development_rows([("x",)])),
    )


# -- M: migration shape --------------------------------------------------------

MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0030_dataset_split_manifests.py"
TABLES = (
    "dataset_split_manifests",
    "dataset_split_manifest_roles",
    "dataset_split_manifest_sources",
    "dataset_split_assignments",
)


def migration_findings(src: str) -> list[str]:
    """Every shape rule the migration must satisfy; returns the broken ones."""
    broken: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            broken.append(label)

    need("revision id", 'revision = "0030_dataset_split_manifests"' in src)
    need("chained onto 0029", 'down_revision = "0029_artifact_ready_license"' in src)
    need("revision id fits alembic_version (<=32)", len("0030_dataset_split_manifests") <= 32)
    need("ENABLE rls", 'f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"' in src)
    need("FORCE rls", 'f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"' in src)
    rls_section = src.split("tenant_setting =")[1].split("def downgrade")[0]
    for table in TABLES:
        need(f"rls loop names {table}", f'"{table}",' in rls_section)
    need("policy name", "CREATE POLICY tenant_isolation ON {table} FOR ALL" in src)
    need("policy using", "USING (organization_id = {tenant_setting})" in src)
    need("policy with check", "WITH CHECK (organization_id = {tenant_setting})" in src)
    need("not allow-all", "USING (true)" not in src and "WITH CHECK (true)" not in src)
    need(
        "manifest SELECT/INSERT",
        'GRANT SELECT, INSERT ON dataset_split_manifests TO "{APP_ROLE_NAME}"' in src,
    )
    need(
        "child SELECT/INSERT",
        "f'GRANT SELECT, INSERT ON {table} TO \"{APP_ROLE_NAME}\"'" in src
        and "for table in _TABLES[:3]:" in src,
    )
    need(
        "manifest column-limited UPDATE",
        "f\"GRANT UPDATE ({', '.join(_MANIFEST_UPDATABLE)}) ON dataset_split_manifests \"" in src,
    )
    updatable = re.search(r"_MANIFEST_UPDATABLE = \(([^)]*)\)", src)
    need(
        "the updatable columns are exactly the lock columns",
        updatable is not None
        and re.findall(r'"(\w+)"', updatable.group(1))
        == [
            "status",
            "leakage_reviews",
            "reviewed_leakage_report_sha256",
            "frozen_manifest_sha256",
            "approved_by",
            "locked_at",
            "updated_at",
        ],
    )
    upgrade_body = src.split("def upgrade")[1].split("def downgrade")[0]
    need("no DELETE grant", re.search(r"GRANT[^\n]*DELETE", upgrade_body) is None)
    need(
        "exactly one UPDATE grant, column-limited, on the manifests",
        len(re.findall(r"GRANT[^\n]*UPDATE", upgrade_body)) == 1,
    )
    for name in (
        "uq_dataset_quality_reports_hash_identity",
        "uq_dataset_split_manifests_project_name_version",
        "uq_dataset_split_manifests_lineage",
        "uq_dataset_split_manifest_roles_role",
        "uq_dataset_split_manifest_sources_dataset",
        "fk_dataset_split_manifests_parent",
        "fk_dataset_split_manifest_sources_dataset_lineage",
        "fk_dataset_split_manifest_sources_report_identity",
        "fk_dataset_split_manifest_sources_report_hash",
        "fk_dataset_split_manifest_sources_connection",
        "fk_dataset_split_assignments_manifest",
        "fk_dataset_split_assignments_source",
        "ck_dataset_split_manifests_first_has_no_parent",
        "ck_dataset_split_manifests_parent_is_previous",
        "ck_dataset_split_manifests_strategy",
        "ck_dataset_split_manifests_ratios",
        "ck_dataset_split_manifests_hashes",
        "ck_dataset_split_manifests_legacy_lineage",
        "ck_dataset_split_manifests_lock_columns",
        "ck_dataset_split_manifests_locked_leakage",
        "ck_dataset_split_manifests_leakage_status",
        "ck_dataset_split_manifest_roles_pair",
        "ck_dataset_split_manifest_roles_counts",
        "ck_dataset_split_manifest_sources_purpose",
        "ck_dataset_split_manifest_sources_snapshot",
        "ck_dataset_split_assignments_role",
        "ck_dataset_split_assignments_record_id",
        "tr_dataset_split_manifests_insert_guard",
        "tr_dataset_split_manifests_update_guard",
        "tr_dataset_split_manifests_update_guard_final",
        "tr_dataset_split_manifests_update_guard_frozen",
        "tr_dataset_split_manifests_update_guard_roles",
        "tr_dataset_split_manifests_update_guard_counts",
        "tr_dataset_split_manifests_update_guard_groups",
        "tr_dataset_split_manifests_update_guard_sources",
        "tr_dataset_split_assignments_insert_guard_pinned",
        "tr_dataset_split_assignments_insert_guard_grant",
    ):
        need(f"names {name}", name in src)
    for kind, table in (
        ("manifest_roles", "dataset_split_manifest_roles"),
        ("manifest_sources", "dataset_split_manifest_sources"),
        ("assignments", "dataset_split_assignments"),
    ):
        need(f"insert trigger for {table}", f"tr_{table}_insert_guard" in src or kind in src)
    need(
        "update guard checks the frozen columns",
        "(to_jsonb(NEW) - 'status' - 'leakage_reviews' - 'reviewed_leakage_report_sha256'" in src,
    )
    need("update guard allows only the one transition", "IF OLD.status <> 'proposed' THEN" in src)
    need("lock needs four roles", "AND record_count >= 1) <> 4 THEN" in src)
    need("lock checks group isolation", "HAVING count(DISTINCT a.role) > 1" in src)
    need(
        "lock needs a source", "IF NOT EXISTS (SELECT 1 FROM dataset_split_manifest_sources" in src
    )
    need(
        "pinned held-out",
        "source_purpose = 'held_out_evaluation' AND NEW.role <> 'final_qualification' THEN" in src,
    )
    need(
        "pinned development",
        "source_purpose = 'candidate_development' AND NEW.role <> 'development_eval' THEN" in src,
    )
    need("grant snapshot is checked", "= ANY (snapshot)" in src)
    need(
        "blocked leakage cannot lock",
        "status <> 'locked' OR leakage_status IN ('clear', 'leakage_found')" in src,
    )
    need(
        "children refuse a non-proposed manifest",
        "IF manifest_status IS DISTINCT FROM 'proposed' THEN" in src,
    )
    need(
        "ondelete RESTRICT on the dataset lineage fk",
        'ondelete="RESTRICT",\n            name="fk_dataset_split_manifest_sources_dataset_lineage"'
        in src,
    )
    return broken


def part_m() -> None:
    src = MIGRATION_PATH.read_text()
    broken = migration_findings(src)
    check(
        "LAW18-MIGRATION",
        f"the migration satisfies every shape rule (broken: {broken})",
        not broken,
    )
    faults = {
        "drop FORCE RLS": ('f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"', '"SELECT 1"'),
        "drop ENABLE RLS": ('f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"', '"SELECT 1"'),
        "allow-all policy": ("USING (organization_id = {tenant_setting})", "USING (true)"),
        "allow-all check": ("WITH CHECK (organization_id = {tenant_setting})", "WITH CHECK (true)"),
        "RLS skips the assignments table": (
            '        "dataset_split_assignments",\n    ):\n        op.execute(f"ALTER TABLE {table} ENABLE',
            '    ):\n        op.execute(f"ALTER TABLE {table} ENABLE',
        ),
        "grant DELETE on manifests": (
            'GRANT SELECT, INSERT ON dataset_split_manifests TO "{APP_ROLE_NAME}"',
            'GRANT SELECT, INSERT, DELETE ON dataset_split_manifests TO "{APP_ROLE_NAME}"',
        ),
        "grant UPDATE on the children": (
            "f'GRANT SELECT, INSERT ON {table} TO \"{APP_ROLE_NAME}\"'",
            "f'GRANT SELECT, INSERT, UPDATE ON {table} TO \"{APP_ROLE_NAME}\"'",
        ),
        "widen the manifest UPDATE columns": (
            '    "status",\n    "leakage_reviews",',
            '    "status",\n    "seed",\n    "leakage_reviews",',
        ),
        "drop the update guard's one-transition rule": (
            "IF OLD.status <> 'proposed' THEN",
            "IF false THEN",
        ),
        "drop the frozen-column guard": (
            "(to_jsonb(NEW) - 'status' - 'leakage_reviews' - 'reviewed_leakage_report_sha256'",
            "(to_jsonb(OLD) - 'status' - 'leakage_reviews' - 'reviewed_leakage_report_sha256'",
        ),
        "lock without four roles": (
            "AND record_count >= 1) <> 4 THEN",
            "AND record_count >= 1) <> 0 THEN",
        ),
        "lock without group isolation": (
            "HAVING count(DISTINCT a.role) > 1",
            "HAVING count(DISTINCT a.role) > 99",
        ),
        "lock without a source": (
            "IF NOT EXISTS (SELECT 1 FROM dataset_split_manifest_sources",
            "IF EXISTS (SELECT 1 FROM dataset_split_manifest_sources",
        ),
        "unpin the held-out dataset": (
            "source_purpose = 'held_out_evaluation' AND NEW.role <> 'final_qualification'",
            "source_purpose = 'held_out_evaluation' AND NEW.role <> 'final_qualification' AND false",
        ),
        "unpin the development dataset": (
            "source_purpose = 'candidate_development' AND NEW.role <> 'development_eval'",
            "source_purpose = 'candidate_development' AND NEW.role <> 'development_eval' AND false",
        ),
        "ignore the frozen grant": ("= ANY (snapshot)", "= ANY (ARRAY[NEW.role])"),
        "let a blocked report lock": (
            "status <> 'locked' OR leakage_status IN ('clear', 'leakage_found')",
            "status <> 'locked' OR leakage_status IN ('clear', 'leakage_found', 'blocked')",
        ),
        "children accept any manifest": (
            "IF manifest_status IS DISTINCT FROM 'proposed' THEN",
            "IF false THEN",
        ),
        "cascade the dataset lineage fk": (
            'ondelete="RESTRICT",\n            name="fk_dataset_split_manifest_sources_dataset_lineage"',
            'ondelete="CASCADE",\n            name="fk_dataset_split_manifest_sources_dataset_lineage"',
        ),
        "wrong parent revision": (
            'down_revision = "0029_artifact_ready_license"',
            'down_revision = "0028_model_import_state_machine"',
        ),
    }
    for label, (old, new) in faults.items():
        check("LAW18-MIGRATION", f"fault {label!r}: the pattern exists in the file", old in src)
        check(
            "LAW18-MIGRATION",
            f"fault {label!r} is caught by the shape rules",
            bool(migration_findings(src.replace(old, new, 1))),
        )
    part_m_sql()


def offline_sql(action: str, revision_range: str) -> str:
    env = {**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision_range, "--sql"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env=env,
    )
    check(
        "LAW18-MIGRATION",
        f"alembic {action} {revision_range} --sql exits 0 ({result.stderr[-200:]})",
        result.returncode == 0,
    )
    return result.stdout


def part_m_sql() -> None:
    up = offline_sql("upgrade", "0029_artifact_ready_license:0030_dataset_split_manifests")
    for needle in (
        "CREATE TABLE dataset_split_manifests",
        "CREATE TABLE dataset_split_manifest_roles",
        "CREATE TABLE dataset_split_manifest_sources",
        "CREATE TABLE dataset_split_assignments",
        "ADD CONSTRAINT uq_dataset_quality_reports_hash_identity UNIQUE (organization_id, id, report_sha256)",
        'GRANT SELECT, INSERT ON dataset_split_manifests TO "sovra_app"',
        'GRANT UPDATE (status, leakage_reviews, reviewed_leakage_report_sha256, frozen_manifest_sha256, approved_by, locked_at, updated_at) ON dataset_split_manifests TO "sovra_app"',
        'GRANT SELECT, INSERT ON dataset_split_manifest_roles TO "sovra_app"',
        'GRANT SELECT, INSERT ON dataset_split_manifest_sources TO "sovra_app"',
        'GRANT SELECT, INSERT ON dataset_split_assignments TO "sovra_app"',
        "CREATE TRIGGER tr_dataset_split_manifests_insert_guard BEFORE INSERT ON dataset_split_manifests",
        "CREATE TRIGGER tr_dataset_split_manifests_update_guard BEFORE UPDATE ON dataset_split_manifests",
        "CREATE TRIGGER tr_dataset_split_manifest_roles_insert_guard BEFORE INSERT ON dataset_split_manifest_roles",
        "CREATE TRIGGER tr_dataset_split_manifest_sources_insert_guard BEFORE INSERT ON dataset_split_manifest_sources",
        "CREATE TRIGGER tr_dataset_split_assignments_insert_guard BEFORE INSERT ON dataset_split_assignments",
        "UPDATE alembic_version SET version_num='0030_dataset_split_manifests'",
        "CONSTRAINT fk_dataset_split_manifests_parent FOREIGN KEY(organization_id, project_id, name, parent_version)",
        "CHECK (role IN ('train','validation','development_eval','final_qualification'))",
        "CHECK (strategy IN ('sha256_group_hash_v1','legacy_three_split_v1'))",
        "CHECK (leakage_status IN ('clear','leakage_found','blocked','failed'))",
        "CHECK (dataset_purpose IN ('training','validation','candidate_development','held_out_evaluation'))",
        "allowed_purposes_snapshot <@ ARRAY['training','validation','candidate_development','held_out_evaluation','baseline_only']::text[]",
        "server_default",
    ):
        if needle == "server_default":
            continue
        check("LAW18-MIGRATION", f"the upgrade SQL contains: {needle[:90]}", needle in up)
    check(
        "LAW18-MIGRATION",
        "the upgrade creates exactly four tenant_isolation policies, four ENABLE and four FORCE, and never grants DELETE",
        up.count("CREATE POLICY tenant_isolation ON") == 4
        and up.count("ENABLE ROW LEVEL SECURITY") == 4
        and up.count("FORCE ROW LEVEL SECURITY") == 4
        and "GRANT DELETE" not in up
        and "DELETE ON" not in up
        and "USING (true)" not in up,
    )
    check(
        "LAW18-MIGRATION",
        "every table gets the same non-allow-all tenant policy",
        all(
            f"CREATE POLICY tenant_isolation ON {t} FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid)"
            in up
            for t in TABLES
        ),
    )
    down = offline_sql("downgrade", "0030_dataset_split_manifests:0029_artifact_ready_license")
    for needle in (
        "DROP TRIGGER IF EXISTS tr_dataset_split_assignments_insert_guard ON dataset_split_assignments",
        "DROP TRIGGER IF EXISTS tr_dataset_split_manifest_sources_insert_guard ON dataset_split_manifest_sources",
        "DROP TRIGGER IF EXISTS tr_dataset_split_manifest_roles_insert_guard ON dataset_split_manifest_roles",
        "DROP TRIGGER IF EXISTS tr_dataset_split_manifests_update_guard ON dataset_split_manifests",
        "DROP TRIGGER IF EXISTS tr_dataset_split_manifests_insert_guard ON dataset_split_manifests",
        "DROP FUNCTION IF EXISTS dataset_split_assignments_insert_guard()",
        "DROP FUNCTION IF EXISTS dataset_split_manifests_update_guard()",
        "DROP TABLE dataset_split_assignments",
        "DROP TABLE dataset_split_manifest_sources",
        "DROP TABLE dataset_split_manifest_roles",
        "DROP TABLE dataset_split_manifests",
        "DROP CONSTRAINT uq_dataset_quality_reports_hash_identity",
        'REVOKE ALL ON dataset_split_manifests FROM "sovra_app"',
        "UPDATE alembic_version SET version_num='0029_artifact_ready_license'",
    ):
        check("LAW18-MIGRATION", f"the downgrade SQL contains: {needle[:90]}", needle in down)
    check(
        "LAW18-MIGRATION",
        "the downgrade drops the four tenant policies",
        down.count("DROP POLICY IF EXISTS tenant_isolation ON") == 4,
    )
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("m0030", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check(
        "LAW18-MIGRATION",
        "helper quoting",
        module._in(("a", "b")) == "('a','b')"
        and module._array(("a", "b")) == "ARRAY['a','b']::text[]"
        and module.APP_ROLE_NAME == "sovra_app",
    )
    raised = module._raise("m", "c")
    check(
        "LAW18-MIGRATION",
        "_raise builds a check_violation with the named constraint",
        raised == "RAISE EXCEPTION 'm' USING ERRCODE = 'check_violation', CONSTRAINT = 'c'; ",
    )
    required = module._uuid("project_id")
    optional = module._uuid("x", nullable=True)
    stamp = module._now_col("created_at")
    nullable_stamp = module._now_col("locked_at", nullable=True)
    pk = module._uuid_pk()
    org = module._org_fk("t")
    check(
        "LAW18-MIGRATION",
        "column helpers: required/optional uuid, server-default timestamp, nullable timestamp, pk, org fk",
        required.nullable is False
        and optional.nullable is True
        and stamp.nullable is False
        and stamp.server_default is not None
        and nullable_stamp.nullable is True
        and nullable_stamp.server_default is None
        and pk.primary_key is True
        and pk.server_default is not None
        and org.nullable is False
        and org.name == "organization_id",
    )


# -- S: store over SQLite ------------------------------------------------------

_NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
_ORG = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
_PROJECT = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")
_CTX: Any = SimpleNamespace(organization_id=_ORG)
ADMIN = store.SplitActor("user", "user-admin", "workspace_admin", "req-admin")
ADMIN2 = store.SplitActor("user", "user-admin-2", "organization_admin", "req-admin-2")
ML = store.SplitActor("user", "user-ml", "ml_engineer", "req-ml")
ROUTE = EmbeddingRoute(Destination(kind="internal"), "standard", "standard")
BLOCKED_ROUTE = EmbeddingRoute(
    Destination(kind="external", provider="acme-embed"), "zero_egress", "standard"
)
SPEC = SplitSpec("sha256_group_hash_v1", "seed-0519", (70, 10, 10, 10))
ALL_PURPOSES = ["training", "validation", "candidate_development", "held_out_evaluation"]
VERSIONS = {name: "1.0.0" for name in sorted(REQUIRED_DETECTORS)}


def det_uuid(label: str) -> uuid.UUID:
    """Deterministic ids, so the hash allocation (and with it every count here) is stable."""
    raw = bytearray(hashlib.sha256(label.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def actions(self) -> list[str]:
        return [e["action"] for e in self.events]


QUERIES = [0]


def passthrough(
    session: Session, organization_id: uuid.UUID, statement: Any, params: Any = None
) -> Any:
    QUERIES[0] += 1
    return session.execute(statement) if params is None else session.execute(statement, params)


class World:
    """A SQLite session wired like the Postgres one, with the same real stores on top."""

    def __init__(self, session: Session, audit: Audit) -> None:
        self.session = session
        self.audit = audit
        self.connections: dict[uuid.UUID, Any] = {}
        self.records: dict[uuid.UUID, tuple[store.SourceRecord, ...]] = {}

    # -- seeding --
    def connection(self, key: str, purposes: list[str], status: str = "active") -> uuid.UUID:
        conn_id = det_uuid(f"conn-{key}")
        self.connections[conn_id] = SimpleNamespace(
            id=conn_id,
            project_id=_PROJECT,
            connection_status=status,
            allowed_purposes_json=list(purposes),
        )
        return conn_id

    def dataset(
        self,
        name: str,
        purpose: str,
        records: int,
        conn: uuid.UUID,
        *,
        report: bool = True,
        blocking: bool = False,
        version: int = 1,
    ) -> ustore.Dataset:
        dataset = ustore.Dataset(
            id=det_uuid(f"dataset-{name}-{version}"),
            organization_id=_ORG,
            project_id=_PROJECT,
            name=name,
            version=version,
            purpose=purpose,
            upload_session_id=det_uuid(f"upload-{name}-{version}"),
            upload_status="verified",
            verified_sha256=sha(f"dataset-{name}-{version}"),
            verified_size_bytes=100,
            created_by="seed",
            created_at=_NOW,
        )
        self.session.add(dataset)
        self.session.add(
            ustore.DatasetUploadSession(
                id=dataset.upload_session_id,
                organization_id=_ORG,
                project_id=_PROJECT,
                dataset_source_connection_id=conn,
                purpose=purpose,
                object_key=f"k/{name}",
                declared_size_bytes=100,
                max_bytes=100,
                declared_sha256=dataset.verified_sha256,
                declared_content_type="application/jsonl",
                status="verified",
                created_by="seed",
                created_at=_NOW,
                expires_at=_NOW,
                updated_at=_NOW,
            )
        )
        self.session.flush()
        self.records[dataset.id] = tuple(self.record(str(dataset.id), i) for i in range(records))
        if report:
            self.report(dataset, blocking=blocking)
        return dataset

    @staticmethod
    def record(prefix: str, position: int, text_value: str | None = None) -> store.SourceRecord:
        body = text_value or " ".join(sha(f"{prefix}-{position}-{i}")[:6] for i in range(10))
        return store.SourceRecord(
            batch_position=position,
            content={
                "messages": [{"role": "user", "content": [{"type": "text", "text": body}]}],
                "metadata": {},
            },
            record_sha256=sha(f"raw-{prefix}-{position}"),
            normalized_sha256=sha(f"norm-{prefix}-{position}"),
        )

    def report(
        self, dataset: ustore.Dataset, *, blocking: bool = False, seq: int | None = None
    ) -> qstore.DatasetQualityReport:
        latest = (
            self.session.query(qstore.DatasetQualityReport).filter_by(dataset_id=dataset.id).count()
        )
        number = seq or latest + 1
        report = qstore.DatasetQualityReport(
            id=det_uuid(f"report-{dataset.id}-{number}"),
            organization_id=_ORG,
            project_id=_PROJECT,
            dataset_id=dataset.id,
            dataset_name=dataset.name,
            dataset_purpose=dataset.purpose,
            dataset_version=dataset.version,
            dataset_sha256=dataset.verified_sha256,
            report_seq=number,
            report_sha256=sha(f"report-{dataset.id}-{number}"),
            detector_versions=VERSIONS,
            detectors_reported=sorted(REQUIRED_DETECTORS),
            issue_count=1 if blocking else 0,
            blocking_count=1 if blocking else 0,
            warning_count=0,
            resolution_version=0,
            created_by="seed",
            created_at=_NOW,
            updated_at=_NOW,
        )
        self.session.add(report)
        self.session.flush()
        if blocking:
            self.issue(report, dataset, (2,))
        return report

    def issue(
        self,
        report: qstore.DatasetQualityReport,
        dataset: ustore.Dataset,
        positions: tuple[int, ...],
    ) -> qstore.DatasetQualityIssue:
        issue_id = sha(f"issue-{report.id}-{positions}")
        row = qstore.DatasetQualityIssue(
            id=det_uuid(f"issue-row-{report.id}-{positions}"),
            organization_id=_ORG,
            report_id=report.id,
            dataset_sha256=dataset.verified_sha256,
            issue_id=issue_id,
            detector="pii_secrets",
            category="pii_secrets",
            severity="blocking",
            reason_code="pii_secret_email",
            message_sha256=sha("message"),
            batch_positions=list(positions),
            record_indices=list(positions),
            evidence_digest=None,
            is_detector_failure=False,
            has_positions=True,
            evidence_sha256=sha(f"{issue_id}:{dataset.verified_sha256}"),
            created_at=_NOW,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def exclude(
        self,
        report: qstore.DatasetQualityReport,
        dataset: ustore.Dataset,
        positions: tuple[int, ...],
    ) -> None:
        issue = self.issue(report, dataset, positions)
        self.session.add(
            qstore.DatasetIssueResolution(
                id=det_uuid(f"resolution-{issue.id}"),
                organization_id=_ORG,
                quality_report_id=report.id,
                issue_id=issue.issue_id,
                project_id=_PROJECT,
                dataset_id=dataset.id,
                dataset_name=dataset.name,
                dataset_purpose=dataset.purpose,
                dataset_version=dataset.version,
                dataset_sha256=dataset.verified_sha256,
                severity="blocking",
                is_detector_failure=False,
                has_positions=True,
                before_evidence_sha256=issue.evidence_sha256,
                action="exclude",
                engine_decision="resolved",
                excluded_batch_positions=list(positions),
                successor_dataset_id=None,
                successor_version=None,
                successor_sha256=None,
                after_evidence_sha256=sha("after"),
                reason="reviewed",
                actor_id="reviewer",
                actor_role="ml_engineer",
                request_id="r",
                idempotency_key=uuid.uuid4().hex,
                request_sha256=sha("request"),
                resolution_version_after=1,
                created_at=_NOW,
            )
        )
        self.session.flush()

    # -- calls --
    def source(self, dataset: ustore.Dataset) -> store.SplitSourceInput:
        return store.SplitSourceInput(dataset.id, self.records[dataset.id])

    def propose(
        self,
        name: str,
        datasets: list[ustore.Dataset],
        **over: Any,
    ) -> store.DatasetSplitManifest:
        kwargs: dict[str, Any] = {
            "project_id": _PROJECT,
            "name": name,
            "spec": SPEC,
            "sources": [self.source(d) for d in datasets],
            "prior_sources": (),
            "route": ROUTE,
            "normalization_version": "text-normalization-v1",
            "tokenizer_identity": None,
            "actor": ADMIN,
            "now": _NOW,
        }
        if "force_name" in over:
            kwargs["name"] = over.pop("force_name")
        kwargs.update(over)
        return store.propose_split_manifest(self.session, _CTX, **kwargs)

    def approve(self, manifest: Any, **over: Any) -> store.DatasetSplitManifest:
        kwargs: dict[str, Any] = {
            "project_id": _PROJECT,
            "manifest_id": manifest.id,
            "expected_proposal_sha256": manifest.proposal_sha256,
            "reviews": (),
            "actor": ADMIN,
            "now": _NOW,
        }
        kwargs.update(over)
        return store.approve_split_manifest(self.session, _CTX, **kwargs)

    def counts(self) -> tuple[int, ...]:
        return tuple(
            self.session.query(model).count()
            for model in (
                store.DatasetSplitManifest,
                store.DatasetSplitManifestRole,
                store.DatasetSplitManifestSource,
                store.DatasetSplitAssignment,
            )
        ) + (len(self.audit.events),)

    def fingerprint(self, manifest_id: uuid.UUID) -> str:
        parts: list[str] = []
        for model, column in (
            (store.DatasetSplitManifest, store.DatasetSplitManifest.id),
            (store.DatasetSplitManifestRole, store.DatasetSplitManifestRole.manifest_id),
            (store.DatasetSplitManifestSource, store.DatasetSplitManifestSource.manifest_id),
            (store.DatasetSplitAssignment, store.DatasetSplitAssignment.manifest_id),
        ):
            for row_ in self.session.query(model).filter(column == manifest_id):
                parts.append(
                    json.dumps(
                        {c.name: str(getattr(row_, c.name)) for c in row_.__table__.columns},
                        sort_keys=True,
                    )
                )
        return sha("|".join(sorted(parts)))


@contextlib.contextmanager
def sqlite_world() -> Any:
    engine = create_engine("sqlite://")
    for model in (
        ustore.Dataset,
        ustore.DatasetUploadSession,
        qstore.DatasetQualityReport,
        qstore.DatasetQualityIssue,
        qstore.DatasetIssueResolution,
        store.DatasetSplitManifest,
        store.DatasetSplitManifestRole,
        store.DatasetSplitManifestSource,
        store.DatasetSplitAssignment,
    ):
        model.__table__.create(engine)
    audit = Audit()
    saved = (
        store.execute_tenant_scoped,
        qstore.execute_tenant_scoped,
        ustore.execute_tenant_scoped,
        store.write_audit_event,
        store.authorize_dataset_source_purpose,
    )
    with Session(engine) as session:
        world = World(session, audit)

        def authorize(
            _session: Session,
            _context: Any,
            project_id: uuid.UUID,
            connection_id: uuid.UUID,
            purpose: str,
        ) -> Any:
            connection = world.connections.get(connection_id)
            if connection is None or connection.project_id != project_id:
                raise crud.DatasetSourceConnectionNotFoundError(str(connection_id))
            crud.authorize_purpose_use(connection, purpose)
            return connection

        store.execute_tenant_scoped = passthrough
        qstore.execute_tenant_scoped = passthrough
        ustore.execute_tenant_scoped = passthrough
        store.write_audit_event = audit
        store.authorize_dataset_source_purpose = authorize
        try:
            yield world
        finally:
            (
                store.execute_tenant_scoped,
                qstore.execute_tenant_scoped,
                ustore.execute_tenant_scoped,
                store.write_audit_event,
                store.authorize_dataset_source_purpose,
            ) = saved
    engine.dispose()


def code_of(fn: Any) -> str | None:
    """The stable code a store call refuses with; None if it did not refuse."""
    try:
        fn()
    except store.SplitManifestError as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return None


def refuses(
    world: World,
    label: str,
    code: str,
    fn: Any,
    criterion: str = "ACC-FREEZE-REFUSE",
    *,
    early: bool = False,
) -> None:
    """`early=True`: the refusal needs no database, so it must come before the first query."""
    before = world.counts()
    queries = QUERIES[0]
    got = code_of(fn)
    check(criterion, f"{label}: code {code!r}, got {got!r}", got == code)
    check(criterion, f"{label}: nothing was written", world.counts() == before)
    if early:
        check(criterion, f"{label}: refused before any query", QUERIES[0] == queries)


def part_s() -> None:
    with sqlite_world() as world:
        s_golden(world)
    with sqlite_world() as world:
        s_refusals(world)
    with sqlite_world() as world:
        s_approval(world)
    with sqlite_world() as world:
        s_review(world)
    with sqlite_world() as world:
        s_access(world)
    with sqlite_world() as world:
        s_legacy(world)
    with sqlite_world() as world:
        s_reads(world)
    with sqlite_world() as world:
        s_hardening(world)
    with sqlite_world() as world:
        s_tiny(world)
    with sqlite_world() as world:
        s_prior_groups(world)
    with sqlite_world() as world:
        s_tamper(world)


def standard_world(world: World) -> dict[str, Any]:
    conn = world.connection("all", ALL_PURPOSES)
    conn_a = world.connection("a", ALL_PURPOSES)
    train_a = world.dataset("train-a", "training", 40, conn_a)
    return {
        "conn": conn,
        "conn_a": conn_a,
        "a": train_a,
        "b": world.dataset("val-b", "validation", 10, conn),
        "d": world.dataset("dev-set", "candidate_development", 4, conn),
        "h": world.dataset("held-out", "held_out_evaluation", 4, conn),
    }


def s_golden(world: World) -> None:
    ds = standard_world(world)
    a, b, d, h = ds["a"], ds["b"], ds["d"], ds["h"]
    before = world.counts()
    m1 = world.propose("campaign", [a, b, d, h])
    after = world.counts()
    check(
        "ACC-FREEZE-ROWS",
        f"propose writes one manifest, four roles, four sources, 58 assignments and one audit row: {before} -> {after}",
        after == (1, 4, 4, 58, 1),
    )
    check(
        "ACC-FREEZE-ROWS",
        "the manifest row carries the stated spec, the project, the creator and the clock",
        (m1.project_id, m1.organization_id, m1.name, m1.version, m1.parent_version)
        == (_PROJECT, _ORG, "campaign", 1, None)
        and (m1.strategy, m1.seed, list(m1.ratios), m1.cutoff)
        == ("sha256_group_hash_v1", "seed-0519", [70, 10, 10, 10], None)
        and (m1.normalization_version, m1.tokenizer_identity) == ("text-normalization-v1", None)
        and (m1.status, m1.created_by) == ("proposed", ADMIN.actor_id)
        and (m1.created_at, m1.updated_at) == (_NOW, _NOW)
        and (m1.frozen_manifest_sha256, m1.approved_by, m1.locked_at) == (None, None, None)
        and m1.leakage_reviews == []
        and m1.reviewed_leakage_report_sha256 is None
        and m1.migrated_from_legacy_sha256 is None,
    )
    assignments = (
        world.session.query(store.DatasetSplitAssignment).filter_by(manifest_id=m1.id).all()
    )
    by_role: dict[str, list[Any]] = {r: [] for r in ROLES}
    for row_ in assignments:
        by_role[row_.role].append(row_)
    check(
        "NEG-FINAL-PIN",
        "the held-out source is wholly final, the development source wholly development",
        {r.role for r in assignments if r.source_dataset_id == h.id} == {"final_qualification"}
        and {r.role for r in assignments if r.source_dataset_id == d.id} == {"development_eval"}
        and len([r for r in assignments if r.source_dataset_id == h.id]) == 4
        and len([r for r in assignments if r.source_dataset_id == d.id]) == 4,
    )
    check(
        "ACC-FREEZE-ROLES",
        f"every role is non-empty: {[(r, len(v)) for r, v in by_role.items()]}",
        all(len(v) >= 1 for v in by_role.values()),
    )
    roles = {
        r.role: r
        for r in world.session.query(store.DatasetSplitManifestRole).filter_by(manifest_id=m1.id)
    }
    for role in ROLES:
        mine = by_role[role]
        row_hash = hashlib.sha256(
            json.dumps(
                sorted(
                    [x.record_id, x.record_sha256, x.normalized_sha256, x.group_id] for x in mine
                ),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        check(
            "ACC-FREEZE-ROLES",
            f"{role}: purpose, counts and hash are recomputed independently",
            roles[role].purpose == ism.ROLE_PURPOSE[role]
            and roles[role].record_count == len(mine)
            and roles[role].group_count == len({x.group_id for x in mine})
            and roles[role].role_sha256 == row_hash,
        )
    check(
        "ACC-FREEZE-ROLES",
        "assignments carry the origin: dataset, position, hashes and a '<dataset>:<position>' id",
        all(
            r.record_id == f"{r.source_dataset_id}:{r.batch_position}"
            and r.record_sha256 == sha(f"raw-{r.source_dataset_id}-{r.batch_position}")
            and r.normalized_sha256 == sha(f"norm-{r.source_dataset_id}-{r.batch_position}")
            and r.organization_id == _ORG
            for r in assignments
        ),
    )
    sources = {
        s.dataset_id: s
        for s in world.session.query(store.DatasetSplitManifestSource).filter_by(manifest_id=m1.id)
    }
    check(
        "ACC-FREEZE-GRANT",
        "each source freezes its identity, grant snapshot, connection, quality report and exclusion hash",
        set(sources) == {a.id, b.id, d.id, h.id}
        and all(
            s.dataset_name == ds_.name
            and s.dataset_purpose == ds_.purpose
            and s.dataset_version == 1
            and s.dataset_sha256 == ds_.verified_sha256
            and s.dataset_source_connection_id == (ds["conn_a"] if ds_ is a else ds["conn"])
            and sorted(s.allowed_purposes_snapshot) == sorted(ALL_PURPOSES)
            and s.quality_report_id == det_uuid(f"report-{ds_.id}-1")
            and s.quality_report_sha256 == sha(f"report-{ds_.id}-1")
            and s.excluded_positions_sha256 == ism.excluded_positions_sha256(())
            and s.project_id == _PROJECT
            and s.created_at.replace(tzinfo=None) == _NOW.replace(tzinfo=None)
            for ds_, s in ((x, sources[x.id]) for x in (a, b, d, h))
        ),
    )
    # The store ran the real allocator and the real detector on this exact manifest.
    records = [
        SplitRecord(
            f"{x.id}:{r.batch_position}",
            (f"{x.id}:{r.batch_position}",),
            {
                "candidate_development": "development_eval",
                "held_out_evaluation": "final_qualification",
            }.get(x.purpose),
            None,
        )
        for x in (a, b, d, h)
        for r in world.records[x.id]
    ]
    expected = allocate_splits(SPEC, records)
    check(
        "ACC-FREEZE-ROWS",
        "split_manifest_sha256 is FTF-0517's own hash of the allocation",
        m1.split_manifest_sha256 == expected.manifest_sha256
        and [
            (x.record_id, x.role, x.group_id)
            for x in sorted(assignments, key=lambda r: r.record_id)
        ]
        == list(expected.assignments),
    )
    leak = [
        LeakageRecord(
            f"{x.id}:{r.batch_position}",
            r.content,
            r.record_sha256,
            r.normalized_sha256,
            r.group_keys,
        )
        for x in (a, b, d, h)
        for r in world.records[x.id]
    ]
    independent = detect_split_leakage(expected, leak, route=ROUTE)
    check(
        "ACC-FREEZE-REPORT",
        "the stored leakage report is the detector's own report over this manifest (the store ran it)",
        m1.leakage_report_sha256 == independent.report_sha256
        and m1.leakage_report == report_body(independent)
        and m1.leakage_status == independent.status == "clear",
    )
    check(
        "ACC-FREEZE-REPORT",
        "no record content is stored in the leakage report or the audit row",
        all(
            r.content["messages"][0]["content"][0]["text"] not in json.dumps(m1.leakage_report)
            for r in world.records[a.id]
        )
        and "record_id" not in json.dumps(world.audit.events[0]["details"]),
    )
    event = world.audit.events[0]
    check(
        "ACC-FREEZE-AUDIT",
        "one audit row names the actor, request, resource and counts, never content",
        world.audit.actions() == ["dataset_split_manifest.proposed"]
        and event["organization_id"] == _ORG
        and (event["actor_type"], event["actor_id"], event["request_id"])
        == ("user", ADMIN.actor_id, ADMIN.request_id)
        and (event["resource_type"], event["resource_id"]) == ("dataset_split_manifest", str(m1.id))
        and event["details"]["project_id"] == str(_PROJECT)
        and event["details"]["name"] == "campaign"
        and event["details"]["version"] == 1
        and event["details"]["split_manifest_sha256"] == m1.split_manifest_sha256
        and event["details"]["proposal_sha256"] == m1.proposal_sha256
        and event["details"]["leakage_status"] == "clear"
        and event["details"]["source_count"] == 4
        and event["details"]["finding_count"] == 0
        and event["details"]["role_counts"] == {r: len(by_role[r]) for r in ROLES},
    )
    content = store._content_from_rows(
        m1,
        store._fetch_roles(world.session, _CTX, m1.id),
        store._fetch_sources(world.session, _CTX, m1.id),
    )
    check(
        "ACC-FREEZE-HASH",
        "the stored proposal hash is the pure module's hash of the stored content",
        ism.compute_proposal_sha256(content) == m1.proposal_sha256,
    )

    # An unchanged re-proposal is the same row and writes nothing.
    again_before = world.counts()
    again = world.propose("campaign", [a, b, d, h])
    check(
        "ACC-VERSION-IDEMPOTENT",
        "re-proposing an unchanged manifest returns version 1 and writes nothing",
        again.id == m1.id and world.counts() == again_before,
    )
    # Source order and record order do not matter.
    reordered = world.propose(
        "campaign",
        [],
        sources=[
            store.SplitSourceInput(x.id, tuple(reversed(world.records[x.id]))) for x in (h, d, b, a)
        ],
    )
    check(
        "ACC-VERSION-IDEMPOTENT",
        "source order and record order do not change the proposal",
        reordered.id == m1.id and world.counts() == again_before,
    )

    # Version on change: each change is version 2 with parent 1; version 1 is untouched.
    fingerprint = world.fingerprint(m1.id)
    for label, over in (
        ("a changed seed", {"spec": SplitSpec("sha256_group_hash_v1", "seed-b", (70, 10, 10, 10))}),
        (
            "changed ratios",
            {"spec": SplitSpec("sha256_group_hash_v1", "seed-0519", (60, 20, 10, 10))},
        ),
        ("a cutoff", None),
        ("a changed normalization version", {"normalization_version": "text-normalization-v2"}),
        ("a tokenizer identity", {"tokenizer_identity": "tokenizer-sha"}),
    ):
        if over is None:
            continue
        latest_version = world.session.query(func.max(store.DatasetSplitManifest.version)).scalar()
        changed = world.propose("campaign", [a, b, d, h], **over)
        check(
            "ACC-VERSION-CHANGE",
            f"{label} is a new version whose parent is the previous one",
            changed.version == latest_version + 1
            and changed.parent_version == latest_version
            and changed.id != m1.id
            and changed.status == "proposed",
        )
    check(
        "ACC-VERSION-CHANGE",
        "version 1 is byte-identical after later versions were written",
        world.fingerprint(m1.id) == fingerprint,
    )
    chain = [
        (r.version, r.parent_version)
        for r in world.session.query(store.DatasetSplitManifest)
        .filter_by(name="campaign")
        .order_by(store.DatasetSplitManifest.version)
    ]
    check(
        "ACC-VERSION-CHANGE",
        f"the chain is 1..{len(chain)}: {chain}",
        chain == [(1, None), (2, 1), (3, 2), (4, 3), (5, 4)],
    )
    cutoff_records = [replace(r, event_time=1000 + i) for i, r in enumerate(world.records[b.id])]
    cutoff_sources = [
        world.source(a),
        store.SplitSourceInput(b.id, tuple(cutoff_records)),
        world.source(d),
        world.source(h),
    ]
    refusals_for_cutoff = code_of(
        lambda: world.propose(
            "campaign",
            [],
            spec=SplitSpec("sha256_group_hash_v1", "c", (70, 10, 10, 10), cutoff=1005),
            sources=cutoff_sources,
        )
    )
    check(
        "ACC-VERSION-CHANGE",
        f"a cutoff needs an event time on every record: {refusals_for_cutoff!r}",
        refusals_for_cutoff == "invalid_request",
    )
    other = world.propose("other-campaign", [a, b, d, h])
    check(
        "ACC-VERSION-CHANGE",
        "a different name starts its own chain at version 1",
        other.version == 1 and other.parent_version is None and other.id != m1.id,
    )

    # A changed source version / quality report / grant is a change too.
    b2 = world.dataset("val-b", "validation", 10, ds["conn"], version=2)
    v_src = world.propose("campaign", [a, b2, d, h])
    check(
        "ACC-VERSION-CHANGE",
        "a new dataset version is a new manifest version",
        v_src.version == 6
        and {
            s.dataset_version
            for s in world.session.query(store.DatasetSplitManifestSource).filter_by(
                manifest_id=v_src.id
            )
        }
        == {1, 2},
    )
    world.report(a)
    v_report = world.propose("campaign", [a, b2, d, h])
    check(
        "ACC-VERSION-CHANGE",
        "a new quality report is a new manifest version",
        v_report.version == 7
        and world.session.query(store.DatasetSplitManifestSource)
        .filter_by(manifest_id=v_report.id, dataset_id=a.id)
        .one()
        .quality_report_id
        == det_uuid(f"report-{a.id}-2"),
    )
    world.connections[ds["conn"]].allowed_purposes_json = [*ALL_PURPOSES, "baseline_only"]
    v_grant = world.propose("campaign", [a, b2, d, h])
    check(
        "ACC-VERSION-CHANGE",
        "a changed connection grant is a new manifest version with the new snapshot",
        v_grant.version == 8
        and {
            tuple(s.allowed_purposes_snapshot)
            for s in world.session.query(store.DatasetSplitManifestSource).filter_by(
                manifest_id=v_grant.id
            )
        }
        == {tuple(sorted([*ALL_PURPOSES, "baseline_only"])), tuple(sorted(ALL_PURPOSES))},
    )
    world.connections[ds["conn"]].allowed_purposes_json = list(ALL_PURPOSES)

    # A concurrent version collision is a stable refusal that writes nothing.
    original_flush = world.session.flush

    def exploding_flush(*args: Any, **kwargs: Any) -> None:
        if world.session.in_nested_transaction():
            raise IntegrityError("insert", {}, Exception("duplicate version"))
        original_flush(*args, **kwargs)

    counts_before = world.counts()
    world.session.flush = exploding_flush  # type: ignore[method-assign]
    try:
        got = code_of(
            lambda: world.propose("campaign", [a, b2, d, h], normalization_version="collide")
        )
    finally:
        world.session.flush = original_flush  # type: ignore[method-assign]
    check(
        "ACC-VERSION-CONFLICT",
        f"a version collision is version_conflict: {got!r}",
        got == "version_conflict",
    )
    check("ACC-VERSION-CONFLICT", "and nothing was written", world.counts() == counts_before)


def s_refusals(world: World) -> None:
    ds = standard_world(world)
    b, d, h, conn = ds["b"], ds["d"], ds["h"], ds["conn"]
    base_args = [b, d, h]

    dirty = world.dataset("dirty", "training", 10, conn, blocking=True)
    refuses(
        world,
        "an unresolved blocking quality issue",
        "quality_not_clear",
        lambda: world.propose("c1", [dirty, d, h]),
    )
    unreported = world.dataset("unreported", "training", 10, conn, report=False)
    refuses(
        world,
        "no quality report",
        "quality_not_clear",
        lambda: world.propose("c2", [unreported, d, h]),
    )
    narrow = world.dataset("narrow", "training", 40, world.connection("train-only", ["training"]))
    refuses(
        world,
        "roles the connection does not grant",
        "purpose_not_authorized",
        lambda: world.propose("c3", [narrow, d, h]),
    )
    revoked_conn = world.connection("revoked", ALL_PURPOSES, status="revoked")
    revoked = world.dataset("revoked", "training", 40, revoked_conn)
    refuses(
        world,
        "a revoked connection",
        "purpose_not_authorized",
        lambda: world.propose("c4", [revoked, d, h]),
    )
    baseline = world.dataset(
        "baseline", "baseline_only", 4, world.connection("baseline", ["baseline_only"])
    )
    refuses(
        world,
        "a baseline_only source",
        "source_not_splittable",
        lambda: world.propose("c5", [b, baseline]),
    )
    refuses(
        world,
        "an actor that may not see held-out data",
        "actor_not_permitted",
        lambda: world.propose("c6", base_args, actor=ML),
    )
    ok_before = world.counts()
    mine = world.propose("control", base_args)
    check(
        "ACC-FREEZE-REFUSE",
        "control: the same sources with a permitted actor are accepted",
        mine.version == 1 and world.counts() != ok_before,
    )

    legacy = ism.LegacyThreeSplitManifest(
        "s", (("a", "train", "g"),), ism.compute_legacy_sha256("s", (("a", "train", "g"),))
    )
    for label, spec in (
        ("a LegacyThreeSplitManifest", legacy),
        ("three ratios as a tuple", (60, 20, 20)),
        ("three ratios as a list", [60, 20, 20]),
        ("the legacy strategy name", "legacy_three_split_v1"),
        ("a heldout_test role", "heldout_test"),
    ):
        refuses(
            world,
            f"propose refuses {label}",
            "legacy_manifest_requires_migration",
            lambda spec=spec: world.propose("c7", base_args, spec=spec),
            "NEG-LEGACY-REFUSE",
        )

    wrong_order = [
        ("a spec that is not a SplitSpec", {"spec": {"strategy": "x"}}),
        ("a project id that is not a UUID", {"project_id": str(_PROJECT)}),
        ("a blank name", {"force_name": ""}),
        ("a padded name", {"force_name": " x"}),
        ("an over-long name", {"force_name": "n" * 201}),
        ("a name that is not a string", {"force_name": 5}),
        ("a blank normalization version", {"normalization_version": ""}),
        ("an over-long normalization version", {"normalization_version": "v" * 65}),
        ("a blank tokenizer identity", {"tokenizer_identity": ""}),
        ("an over-long tokenizer identity", {"tokenizer_identity": "t" * 201}),
        ("a tokenizer identity that is not a string", {"tokenizer_identity": 4}),
        ("no sources", {"sources": []}),
        ("sources as a string", {"sources": "abc"}),
        ("a source that is not a SplitSourceInput", {"sources": ["x"]}),
        ("the same dataset twice", {"sources": [world.source(b), world.source(b)]}),
        (
            "a dataset id that is not a UUID",
            {"sources": [store.SplitSourceInput(str(b.id), world.records[b.id])]},
        ),
        (
            "records as a list",
            {"sources": [store.SplitSourceInput(b.id, list(world.records[b.id]))]},
        ),
        (
            "a record that is not a SourceRecord",
            {"sources": [store.SplitSourceInput(b.id, ("x",))]},
        ),
        (
            "a duplicated batch position",
            {
                "sources": [
                    store.SplitSourceInput(b.id, (world.records[b.id][0], world.records[b.id][0]))
                ]
            },
        ),
        (
            "a negative batch position",
            {
                "sources": [
                    store.SplitSourceInput(
                        b.id, (replace(world.records[b.id][0], batch_position=-1),)
                    )
                ]
            },
        ),
        (
            "a bool batch position",
            {
                "sources": [
                    store.SplitSourceInput(
                        b.id, (replace(world.records[b.id][0], batch_position=True),)
                    )
                ]
            },
        ),
        (
            "record content that is not a mapping",
            {
                "sources": [
                    store.SplitSourceInput(b.id, (replace(world.records[b.id][0], content=["x"]),))
                ]
            },
        ),
        (
            "a group key that is blank",
            {
                "sources": [
                    store.SplitSourceInput(
                        b.id, (replace(world.records[b.id][0], group_keys=("",)),)
                    )
                ]
            },
        ),
        (
            "a record hash that is not hex",
            {
                "sources": [
                    store.SplitSourceInput(
                        b.id, (replace(world.records[b.id][0], record_sha256="nope"),)
                    )
                ]
            },
        ),
        (
            "a cutoff without event times",
            {"spec": SplitSpec("sha256_group_hash_v1", "s", (70, 10, 10, 10), cutoff=5)},
        ),
    ]
    for label, over in wrong_order:
        refuses(
            world,
            f"invalid request: {label}",
            "invalid_request",
            lambda over=over: world.propose("c8", base_args, **over),
        )
    for label, bad_actor in (
        ("an actor that is not a SplitActor", "admin"),
        ("a blank actor id", store.SplitActor("user", "", "workspace_admin", "r")),
        ("a padded actor id", store.SplitActor("user", " a", "workspace_admin", "r")),
        ("an over-long actor id", store.SplitActor("user", "a" * 201, "workspace_admin", "r")),
        ("an over-long request id", store.SplitActor("user", "a", "workspace_admin", "r" * 101)),
        ("an actor type that is not a string", store.SplitActor(1, "a", "workspace_admin", "r")),
        ("a role that is not a string", store.SplitActor("user", "a", None, "r")),
    ):
        refuses(
            world,
            f"invalid actor: {label}",
            "invalid_request",
            lambda bad_actor=bad_actor: world.propose("c9", base_args, actor=bad_actor),
        )
    check(
        "ACC-FREEZE-REFUSE",
        "an actor id of exactly 200 characters and a request id of exactly 100 are accepted",
        world.propose(
            "c10",
            base_args,
            actor=store.SplitActor("user", "a" * 200, "workspace_admin", "r" * 100),
        ).created_by
        == "a" * 200,
    )
    refuses(
        world,
        "a dataset that does not exist",
        "dataset_not_found",
        lambda: world.propose(
            "c11", [], sources=[store.SplitSourceInput(det_uuid("ghost"), world.records[b.id])]
        ),
    )
    refuses(
        world,
        "a dataset of another project",
        "dataset_not_found",
        lambda: world.propose("c12", base_args, project_id=det_uuid("elsewhere")),
    )
    all_excluded = world.dataset("all-excluded", "training", 3, conn, report=False)
    report = world.report(all_excluded)
    world.exclude(report, all_excluded, (0, 1, 2))
    refuses(
        world,
        "a source whose every record is excluded",
        "no_records",
        lambda: world.propose("c13", [all_excluded, d, h]),
    )
    original = store.MAX_MANIFEST_RECORDS
    store.MAX_MANIFEST_RECORDS = 15
    try:
        refuses(
            world,
            "more records than the limit (never truncated)",
            "manifest_too_large",
            lambda: world.propose("c14", [b, d, h]),
        )
        within = world.propose("c15", [b])
        check(
            "ACC-FREEZE-REFUSE",
            "control: exactly the limit's worth is accepted",
            within.version == 1,
        )
        store.MAX_MANIFEST_RECORDS = 10
        within_ten = world.propose("c16", [b])
        check(
            "ACC-FREEZE-REFUSE",
            "control: a count equal to the limit is accepted",
            within_ten.version == 1,
        )
    finally:
        store.MAX_MANIFEST_RECORDS = original

    # Exclusions: dropped, frozen, and the hash is of the sorted positions.
    excluding = world.dataset("excluding", "training", 12, conn, report=False)
    excluding_report = world.report(excluding)
    world.exclude(excluding_report, excluding, (1, 3))
    kept = world.propose("c-excl", [excluding, d, h])
    placed = {
        r.batch_position
        for r in world.session.query(store.DatasetSplitAssignment).filter_by(
            manifest_id=kept.id, source_dataset_id=excluding.id
        )
    }
    check(
        "ACC-FREEZE-EXCLUDE",
        f"excluded positions never enter the manifest: {sorted(placed)}",
        placed == set(range(12)) - {1, 3},
    )
    frozen_source = (
        world.session.query(store.DatasetSplitManifestSource)
        .filter_by(manifest_id=kept.id, dataset_id=excluding.id)
        .one()
    )
    check(
        "ACC-FREEZE-EXCLUDE",
        "the exclusion set is frozen as the hash of the sorted positions",
        frozen_source.excluded_positions_sha256 == ism.excluded_positions_sha256((1, 3)),
    )


def s_approval(world: World) -> None:
    ds = standard_world(world)
    a, b, d, h, conn = ds["a"], ds["b"], ds["d"], ds["h"], ds["conn"]
    m1 = world.propose("campaign", [a, b, d, h])
    sibling = world.propose("sibling", [a, b, d, h])
    check("ACC-FREEZE-APPROVE", "the proposals are clear", m1.leakage_status == "clear")

    refuses(
        world,
        "approval by an actor that may not see held-out data",
        "actor_not_permitted",
        lambda: world.approve(m1, actor=ML),
    )
    refuses(
        world,
        "a stale expected hash",
        "manifest_changed",
        lambda: world.approve(m1, expected_proposal_sha256="0" * 64),
    )
    refuses(
        world,
        "an expected hash of the wrong type",
        "manifest_changed",
        lambda: world.approve(m1, expected_proposal_sha256=None),
    )
    refuses(
        world,
        "an expected hash that is a str subclass",
        "manifest_changed",
        lambda: world.approve(m1, expected_proposal_sha256=StrSub(m1.proposal_sha256)),
    )
    refuses(
        world,
        "a manifest id that does not exist",
        "manifest_not_found",
        lambda: world.approve(m1, manifest_id=det_uuid("none")),
    )
    refuses(
        world,
        "a manifest id of the wrong type",
        "manifest_not_found",
        lambda: world.approve(m1, manifest_id=str(m1.id)),
    )
    refuses(
        world,
        "a project that is not the manifest's",
        "manifest_not_found",
        lambda: world.approve(m1, project_id=det_uuid("elsewhere")),
    )
    refuses(
        world,
        "a project id of the wrong type",
        "manifest_not_found",
        lambda: world.approve(m1, project_id=str(_PROJECT)),
    )
    refuses(
        world,
        "reviews that are not a list",
        "invalid_review",
        lambda: world.approve(m1, reviews="x"),
    )
    refuses(
        world,
        "a review that is not a ReviewInput",
        "invalid_review",
        lambda: world.approve(m1, reviews=("x",)),
    )
    refuses(
        world,
        "a review of a finding that does not exist",
        "invalid_review",
        lambda: world.approve(m1, reviews=(store.ReviewInput("f" * 64, "no such finding"),)),
    )
    refuses(
        world,
        "a review with a blank rationale",
        "invalid_review",
        lambda: world.approve(m1, reviews=(store.ReviewInput("f" * 64, " "),)),
    )

    rejected = world.propose("throwaway", [b, d, h])
    done = store.reject_split_manifest(
        world.session, _CTX, project_id=_PROJECT, manifest_id=rejected.id, actor=ADMIN2, now=_NOW
    )
    check(
        "ACC-FREEZE-APPROVE",
        "reject moves proposed -> rejected, changes nothing else and writes one audit row",
        done.status == "rejected"
        and done.updated_at == _NOW
        and done.frozen_manifest_sha256 is None
        and done.approved_by is None
        and world.audit.actions()[-1] == "dataset_split_manifest.rejected"
        and world.audit.events[-1]["actor_id"] == ADMIN2.actor_id
        and world.audit.events[-1]["resource_id"] == str(rejected.id),
    )
    refuses(
        world,
        "approving a rejected manifest",
        "manifest_not_proposed",
        lambda: world.approve(rejected),
    )
    refuses(
        world,
        "rejecting a rejected manifest",
        "manifest_not_proposed",
        lambda: store.reject_split_manifest(
            world.session, _CTX, project_id=_PROJECT, manifest_id=rejected.id, actor=ADMIN
        ),
    )
    refuses(
        world,
        "rejecting as an actor that may not see held-out data",
        "actor_not_permitted",
        lambda: store.reject_split_manifest(
            world.session, _CTX, project_id=_PROJECT, manifest_id=m1.id, actor=ML
        ),
    )
    refuses(
        world,
        "rejecting a manifest that does not exist",
        "manifest_not_found",
        lambda: store.reject_split_manifest(
            world.session, _CTX, project_id=_PROJECT, manifest_id=det_uuid("none"), actor=ADMIN
        ),
    )

    # Tampering with the stored proposal is detected before a lock.
    original_report = dict(m1.leakage_report)
    m1.leakage_report = {**original_report, "methods_not_run": ["embedding"]}
    world.session.flush()
    refuses(world, "an edited leakage report", "manifest_tampered", lambda: world.approve(m1))
    m1.leakage_report = original_report
    m1.seed = "edited-seed"
    world.session.flush()
    refuses(
        world,
        "an edited seed (the proposal hash no longer covers the content)",
        "manifest_tampered",
        lambda: world.approve(m1),
    )
    m1.seed = "seed-0519"
    train_role = (
        world.session.query(store.DatasetSplitManifestRole)
        .filter_by(manifest_id=m1.id, role="train")
        .one()
    )
    train_hash = train_role.role_sha256
    train_role.role_sha256 = "not-hex"
    world.session.flush()
    refuses(world, "a malformed role hash", "manifest_tampered", lambda: world.approve(m1))
    train_role.role_sha256 = train_hash
    victim = (
        world.session.query(store.DatasetSplitAssignment)
        .filter_by(manifest_id=m1.id, role="train")
        .first()
    )
    victim.role = "validation"
    world.session.flush()
    refuses(
        world, "an assignment moved to another role", "manifest_tampered", lambda: world.approve(m1)
    )
    victim.role = "train"
    world.session.flush()

    locked = world.approve(m1)
    check(
        "ACC-FREEZE-APPROVE",
        "approval locks: status, approver, lock time, a frozen hash and an unchanged proposal hash",
        locked.id == m1.id
        and locked.status == "locked"
        and locked.approved_by == ADMIN.actor_id
        and locked.locked_at == _NOW
        and locked.updated_at == _NOW
        and locked.leakage_reviews == []
        and locked.reviewed_leakage_report_sha256 == locked.leakage_report_sha256
        and locked.frozen_manifest_sha256 is not None
        and len(locked.frozen_manifest_sha256) == 64
        and locked.proposal_sha256 == m1.proposal_sha256,
    )
    content = store._content_from_rows(
        locked,
        store._fetch_roles(world.session, _CTX, locked.id),
        store._fetch_sources(world.session, _CTX, locked.id),
    )
    check(
        "ACC-FREEZE-HASH",
        "the frozen hash is the pure module's hash of the stored content",
        ism.compute_frozen_manifest_sha256(content) == locked.frozen_manifest_sha256,
    )
    lock_event = world.audit.events[-1]
    check(
        "ACC-FREEZE-AUDIT",
        "one content-free audit row for the lock",
        lock_event["action"] == "dataset_split_manifest.locked"
        and lock_event["resource_id"] == str(m1.id)
        and lock_event["details"]["frozen_manifest_sha256"] == locked.frozen_manifest_sha256
        and lock_event["details"]["review_count"] == 0
        and lock_event["actor_id"] == ADMIN.actor_id,
    )
    refuses(
        world,
        "approving a locked manifest again",
        "manifest_not_proposed",
        lambda: world.approve(m1),
    )
    refuses(
        world,
        "rejecting a locked manifest",
        "manifest_not_proposed",
        lambda: store.reject_split_manifest(
            world.session, _CTX, project_id=_PROJECT, manifest_id=m1.id, actor=ADMIN
        ),
    )

    # Prior campaigns: with a locked manifest, every later proposal carries its suites exactly.
    prior = prior_for(world, locked)
    c2 = [a, b, d, h]
    refuses(
        world,
        "no prior suites although a manifest is locked",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2),
    )
    refuses(
        world,
        "only the development suite",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=prior[:1]),
    )
    forged_record = replace(prior[1].records[0], normalized_sha256="9" * 64)
    forged = PriorSource(prior[1], records=(forged_record, *prior[1].records[1:]))
    refuses(
        world,
        "a prior suite with a record hash that differs",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=(prior[0], forged)),
    )
    renamed_record = replace(prior[1].records[0], record_id="nope:0")
    renamed = PriorSource(prior[1], records=(renamed_record, *prior[1].records[1:]))
    refuses(
        world,
        "a prior suite with a record id that differs",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=(prior[0], renamed)),
    )
    short = PriorSource(prior[1], records=prior[1].records[1:])
    refuses(
        world,
        "a prior suite missing a record",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=(prior[0], short)),
    )
    relabelled = PriorSource(prior[1], label="my-label")
    refuses(
        world,
        "a prior suite with a label the store did not derive",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=(prior[0], relabelled)),
    )
    elsewhere = PriorSource(prior[1], manifest_sha256="8" * 64)
    refuses(
        world,
        "a prior suite for no locked manifest",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=(prior[0], elsewhere)),
    )
    refuses(
        world,
        "the same suite twice",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=(prior[0], prior[1], prior[1])),
    )
    refuses(
        world,
        "an extra unknown suite",
        "prior_sources_incomplete",
        lambda: world.propose("next", c2, prior_sources=(*prior, elsewhere)),
    )
    refuses(
        world,
        "prior sources that are not suites",
        "invalid_request",
        lambda: world.propose("next", c2, prior_sources=("x",)),
    )
    refuses(
        world,
        "prior sources that are not a list",
        "invalid_request",
        lambda: world.propose("next", c2, prior_sources="x"),
    )
    nxt = world.propose("next", c2, prior_sources=prior)
    check(
        "ACC-FREEZE-PRIOR",
        "the exact suites are accepted, and reusing the final suite is a blocking finding that cannot lock",
        nxt.version == 1
        and nxt.leakage_status == "leakage_found"
        and any(
            f["reason"] == "consumed_final_reuse" and f["disposition"] == "blocking"
            for f in nxt.leakage_report["findings"]
        ),
    )
    refuses(
        world,
        "a blocking finding cannot be frozen",
        "leakage_not_clear",
        lambda: world.approve(nxt),
    )
    blocking_id = next(
        f["finding_id"] for f in nxt.leakage_report["findings"] if f["disposition"] == "blocking"
    )
    refuses(
        world,
        "a blocking finding cannot be reviewed away",
        "invalid_review",
        lambda: world.approve(nxt, reviews=(store.ReviewInput(blocking_id, "fine"),)),
    )
    # A different (unlocked) manifest of the project does not need prior suites of its own kind.
    check(
        "ACC-FREEZE-PRIOR",
        "a proposed (unlocked) sibling is not a prior campaign",
        sibling.status == "proposed",
    )
    _ = conn


class PriorSource:
    """Builds a modified copy of a PriorQualificationSource."""

    def __new__(cls, source: Any, **over: Any) -> Any:
        return replace(source, **over)


def prior_for(world: World, manifest: Any) -> tuple[Any, ...]:
    out = []
    for role in ("development_eval", "final_qualification"):
        rows_ = world.session.query(store.DatasetSplitAssignment).filter_by(
            manifest_id=manifest.id, role=role
        )
        leak = []
        for r in rows_.order_by(store.DatasetSplitAssignment.record_id):
            src = world.records[r.source_dataset_id][r.batch_position]
            leak.append(
                LeakageRecord(
                    r.record_id, src.content, r.record_sha256, r.normalized_sha256, src.group_keys
                )
            )
        out.append(
            PriorQualificationSource(
                f"{manifest.name}@v{manifest.version}:{role}",
                role,
                manifest.split_manifest_sha256,
                tuple(leak),
            )
        )
    return tuple(out)


def long_record(prefix: str, position: int) -> store.SourceRecord:
    text_value = " ".join(sha(f"{prefix}-{position}-{i}")[:6] for i in range(40))
    return World.record(prefix, position, text_value)


def variant(record: store.SourceRecord, tag: str) -> store.SourceRecord:
    words = record.content["messages"][0]["content"][0]["text"].rsplit(" ", 1)[0] + f" {tag}"
    return replace(
        record,
        content={
            "messages": [{"role": "user", "content": [{"type": "text", "text": words}]}],
            "metadata": {},
        },
        record_sha256=sha(f"raw-variant-{tag}"),
        normalized_sha256=sha(f"norm-variant-{tag}"),
    )


def s_review(world: World) -> None:
    ds = standard_world(world)
    a, conn = ds["a"], ds["conn"]
    # A review_required finding: a near duplicate across development and final.
    d = world.dataset("sem-dev", "candidate_development", 4, conn)
    h = world.dataset("sem-held", "held_out_evaluation", 4, conn)
    world.records[d.id] = (long_record(str(d.id), 0), *world.records[d.id][1:])
    world.records[h.id] = (variant(world.records[d.id][0], "zzzzzz"), *world.records[h.id][1:])
    semantic = world.propose("c-sem", [a, d, h])
    findings = semantic.leakage_report["findings"]
    check(
        "ACC-FREEZE-REVIEW",
        f"a near duplicate across development and final is found and review_required: {[(f['method'], f['disposition']) for f in findings]}",
        semantic.leakage_status == "leakage_found"
        and len(findings) >= 1
        and all(
            f["disposition"] == "review_required" and f["reason"] == "cross_role" for f in findings
        ),
    )
    ids = [f["finding_id"] for f in findings]
    refuses(
        world,
        "unreviewed leakage cannot be frozen",
        "leakage_not_clear",
        lambda: world.approve(semantic),
        "ACC-FREEZE-REVIEW",
    )
    body = json.loads(json.dumps(semantic.leakage_report))
    body["findings"][0]["scores"] = [
        [name_, value + 0.001] for name_, value in body["findings"][0]["scores"]
    ]
    original_body = semantic.leakage_report
    semantic.leakage_report = body
    world.session.flush()
    refuses(
        world,
        "a self-consistent edit of a finding's scores (only the report hash sees it)",
        "manifest_tampered",
        lambda: world.approve(
            semantic, reviews=tuple(store.ReviewInput(i, "reviewed") for i in ids)
        ),
        "ACC-FREEZE-REVIEW",
    )
    semantic.leakage_report = original_body
    world.session.flush()
    if len(ids) > 1:
        refuses(
            world,
            "a partial review leaves a finding open",
            "leakage_not_clear",
            lambda: world.approve(
                semantic, reviews=(store.ReviewInput(ids[0], "different tickets"),)
            ),
            "ACC-FREEZE-REVIEW",
        )
    refuses(
        world,
        "the same finding reviewed twice",
        "invalid_review",
        lambda: world.approve(
            semantic, reviews=(store.ReviewInput(ids[0], "one"), store.ReviewInput(ids[0], "two"))
        ),
        "ACC-FREEZE-REVIEW",
    )
    refuses(
        world,
        "an over-long rationale",
        "invalid_review",
        lambda: world.approve(semantic, reviews=(store.ReviewInput(ids[0], "x" * 2001),)),
        "ACC-FREEZE-REVIEW",
    )
    locked = world.approve(
        semantic,
        reviews=tuple(store.ReviewInput(i, "different tickets, same template") for i in ids),
        actor=ADMIN2,
    )
    check(
        "ACC-FREEZE-REVIEW",
        "every finding reviewed not_leakage locks the manifest with the authenticated reviewer",
        locked.status == "locked"
        and locked.approved_by == ADMIN2.actor_id
        and len(locked.leakage_reviews) == len(ids)
        and all(
            r["reviewer_id"] == ADMIN2.actor_id
            and r["decision"] == "not_leakage"
            and r["rationale"] == "different tickets, same template"
            for r in locked.leakage_reviews
        )
        and sorted(r["finding_id"] for r in locked.leakage_reviews) == sorted(ids)
        and locked.reviewed_leakage_report_sha256 != locked.leakage_report_sha256
        and locked.leakage_report["reviews"] == [],
    )
    check(
        "ACC-FREEZE-AUDIT",
        "the lock audit row counts the reviews",
        world.audit.events[-1]["details"]["review_count"] == len(ids)
        and world.audit.events[-1]["actor_id"] == ADMIN2.actor_id,
    )
    view = store.load_training_split(
        world.session, _CTX, project_id=_PROJECT, manifest_id=locked.id
    )
    check("ACC-FREEZE-REVIEW", "a reviewed manifest serves training rows", len(view.rows) > 0)

    # Blocking: the same raw hash in development and final.
    fresh_world = world
    d2 = fresh_world.dataset("blk-dev", "candidate_development", 4, conn)
    h2 = fresh_world.dataset("blk-held", "held_out_evaluation", 4, conn)
    fresh_world.records[h2.id] = (
        replace(fresh_world.records[d2.id][0], batch_position=0),
        *fresh_world.records[h2.id][1:],
    )
    prior = prior_for(world, locked)
    blocking = world.propose("c-block", [a, d2, h2], prior_sources=prior)
    check(
        "ACC-FREEZE-REVIEW",
        "an exact cross-role duplicate is a blocking finding",
        any(f["disposition"] == "blocking" for f in blocking.leakage_report["findings"]),
    )
    refuses(
        world,
        "unresolved blocking leakage cannot be frozen",
        "leakage_not_clear",
        lambda: world.approve(blocking),
        "ACC-FREEZE-REVIEW",
    )

    # Blocked scan: an external embedding route under zero_egress.
    routed = world.propose("c-route", [a, d2, h2], route=BLOCKED_ROUTE, prior_sources=prior)
    check(
        "ACC-FREEZE-REVIEW",
        f"a blocked route is stored as blocked: {routed.leakage_status}",
        routed.leakage_status == "blocked",
    )
    refuses(
        world,
        "a blocked scan cannot be frozen",
        "leakage_not_clear",
        lambda: world.approve(routed),
        "ACC-FREEZE-REVIEW",
    )


def s_access(world: World) -> None:
    ds = standard_world(world)
    a, b, d, h, conn = ds["a"], ds["b"], ds["d"], ds["h"], ds["conn"]
    m1 = world.propose("campaign", [a, b, d, h])
    pending = world.propose("pending", [a, b, d, h])
    refuses(
        world,
        "a proposed manifest serves nothing",
        "manifest_not_locked",
        lambda: store.load_training_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=pending.id
        ),
        "NEG-FINAL-VIEW",
    )
    refuses(
        world,
        "a manifest that does not exist",
        "manifest_not_found",
        lambda: store.load_training_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=det_uuid("none")
        ),
        "NEG-FINAL-VIEW",
    )
    world.approve(m1)

    def training() -> store.TrainingSplitView:
        return store.load_training_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=m1.id
        )

    def development() -> store.TrainingSplitView:
        return store.load_development_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=m1.id
        )

    view = training()
    served = {r.record_id for r in view.rows}
    rows_by_role = {
        role: {
            r.record_id
            for r in world.session.query(store.DatasetSplitAssignment).filter_by(
                manifest_id=m1.id, role=role
            )
        }
        for role in ROLES
    }
    check(
        "NEG-FINAL-VIEW",
        "the training view is exactly the train and validation rows, in record order",
        served == rows_by_role["train"] | rows_by_role["validation"]
        and [r.record_id for r in view.rows] == sorted(served)
        and len(served) == len(view.rows)
        and view.manifest_id == m1.id
        and view.version == 1
        and view.frozen_manifest_sha256 == m1.frozen_manifest_sha256
        and all(r.role in ("train", "validation") for r in view.rows),
    )
    check(
        "NEG-FINAL-VIEW",
        "no development or final record id and no held-out or development source record is served",
        not (served & (rows_by_role["development_eval"] | rows_by_role["final_qualification"]))
        and not any(r.source_dataset_id in (str(d.id), str(h.id)) for r in view.rows),
    )
    dev = development()
    check(
        "NEG-FINAL-VIEW",
        "the development view is exactly the development_eval rows",
        {r.record_id for r in dev.rows} == rows_by_role["development_eval"]
        and all(r.role == "development_eval" for r in dev.rows)
        and dev.frozen_manifest_sha256 == m1.frozen_manifest_sha256,
    )
    check(
        "NEG-FINAL-VIEW",
        "the store has no reader of final-qualification records",
        not [
            n
            for n in dir(store)
            if "final" in n.lower()
            and callable(getattr(store, n))
            and n.startswith(("load", "get", "read"))
        ],
    )

    def refused_both(label: str, code: str, *, training_only: bool = False) -> None:
        refuses(world, f"{label} (training)", code, training, "NEG-FINAL-GRANT")
        if not training_only:
            refuses(world, f"{label} (development)", code, development, "NEG-FINAL-GRANT")

    connection = world.connections[conn]
    connection.connection_status = "revoked"
    refused_both("a revoked connection", "grant_changed")
    connection.connection_status = "active"
    check(
        "NEG-FINAL-GRANT", "control: the restored connection serves again", len(training().rows) > 0
    )
    connection.allowed_purposes_json = [
        "validation",
        "candidate_development",
        "held_out_evaluation",
    ]
    refuses(
        world, "a narrowed grant (training removed)", "grant_changed", training, "NEG-FINAL-GRANT"
    )
    check(
        "NEG-FINAL-GRANT",
        "a purpose the training view never serves does not stop it",
        len(development().rows) > 0,
    )
    connection.allowed_purposes_json = ["training", "validation", "held_out_evaluation"]
    refuses(
        world,
        "a narrowed grant (candidate_development removed) stops the development view",
        "grant_changed",
        development,
        "NEG-FINAL-GRANT",
    )
    check("NEG-FINAL-GRANT", "while the training view is unaffected", len(training().rows) > 0)
    connection.allowed_purposes_json = list(ALL_PURPOSES)
    removed = world.connections.pop(conn)
    refused_both("a connection that no longer exists", "grant_changed")
    world.connections[conn] = removed
    check("NEG-FINAL-GRANT", "control: the grant is back", len(training().rows) > 0)

    # Tampering with stored rows.
    train_role = (
        world.session.query(store.DatasetSplitManifestRole)
        .filter_by(manifest_id=m1.id, role="train")
        .one()
    )
    good = train_role.role_sha256
    train_role.role_sha256 = "0" * 64
    world.session.flush()
    refuses(world, "an edited role hash", "manifest_tampered", training, "NEG-FINAL-TAMPER")
    train_role.role_sha256 = good
    victim = (
        world.session.query(store.DatasetSplitAssignment)
        .filter_by(manifest_id=m1.id, role="train")
        .first()
    )
    victim.role = "final_qualification"
    world.session.flush()
    refused_both("a train record moved to final", "manifest_tampered")
    victim.role = "train"
    original_hash = victim.record_sha256
    victim.record_sha256 = "1" * 64
    world.session.flush()
    refuses(world, "an edited record hash", "manifest_tampered", training, "NEG-FINAL-TAMPER")
    victim.record_sha256 = original_hash
    victim.batch_position = victim.batch_position + 100
    world.session.flush()
    refuses(
        world,
        "an assignment whose id no longer matches its position",
        "manifest_tampered",
        training,
        "NEG-FINAL-TAMPER",
    )
    victim.batch_position = victim.batch_position - 100
    world.session.flush()
    for field, bad, good_value in (
        ("seed", "another-seed", m1.seed),
        ("approved_by", "someone-else", m1.approved_by),
        ("frozen_manifest_sha256", "2" * 64, m1.frozen_manifest_sha256),
        ("reviewed_leakage_report_sha256", "3" * 64, m1.reviewed_leakage_report_sha256),
        ("leakage_report_sha256", "4" * 64, m1.leakage_report_sha256),
    ):
        setattr(m1, field, bad)
        world.session.flush()
        refused_both(f"an edited {field}", "manifest_tampered")
        setattr(m1, field, good_value)
        world.session.flush()
    original_report = dict(m1.leakage_report)
    m1.leakage_report = {**original_report, "methods_not_run": ["embedding"]}
    world.session.flush()
    refused_both("an edited leakage report", "manifest_tampered")
    m1.leakage_report = original_report
    m1.leakage_reviews = [
        {"finding_id": "f" * 64, "reviewer_id": "r", "decision": "not_leakage", "rationale": "x"}
    ]
    world.session.flush()
    refused_both("a review added after the lock", "manifest_tampered")
    m1.leakage_reviews = []
    m1.status = "proposed"
    world.session.flush()
    refused_both("a lock reverted", "manifest_not_locked")
    m1.status = "locked"
    world.session.flush()
    check(
        "NEG-FINAL-TAMPER",
        "control: every edit restored, the manifest serves again",
        len(training().rows) > 0 and len(development().rows) > 0,
    )

    # Quality drift.
    blocking_report = (
        world.session.query(qstore.DatasetQualityReport).filter_by(dataset_id=b.id).one()
    )
    issue = world.issue(blocking_report, b, (4,))
    refuses(
        world,
        "an unresolved blocking issue appearing on a frozen report",
        "quality_not_clear",
        training,
        "NEG-FINAL-QUALITY",
    )
    check(
        "NEG-FINAL-QUALITY",
        "the development view is not a training view and is unaffected",
        len(development().rows) > 0,
    )
    world.session.delete(issue)
    world.session.flush()
    check(
        "NEG-FINAL-QUALITY", "control: without the issue it serves again", len(training().rows) > 0
    )
    world.exclude(blocking_report, b, (5,))
    refused_both("an exclusion added to the frozen report", "quality_report_changed")
    for model in (qstore.DatasetIssueResolution, qstore.DatasetQualityIssue):
        for row_ in world.session.query(model).filter_by(organization_id=_ORG):
            if model is qstore.DatasetQualityIssue and row_.report_id != blocking_report.id:
                continue
            world.session.delete(row_)
    world.session.flush()
    check(
        "NEG-FINAL-QUALITY",
        "control: without the exclusion it serves again",
        len(training().rows) > 0,
    )
    world.report(a)
    refused_both("a new quality report on a source", "quality_report_changed")
    new_report = (
        world.session.query(qstore.DatasetQualityReport)
        .filter_by(dataset_id=a.id, report_seq=2)
        .one()
    )
    world.session.delete(new_report)
    world.session.flush()
    check(
        "NEG-FINAL-QUALITY",
        "control: back on the frozen report it serves again",
        len(training().rows) > 0,
    )
    stored_report = (
        world.session.query(qstore.DatasetQualityReport).filter_by(dataset_id=d.id).one()
    )
    stored_report.report_sha256 = "5" * 64
    world.session.flush()
    refused_both("a frozen report whose hash changed", "quality_report_changed")
    stored_report.report_sha256 = sha(f"report-{d.id}-1")
    world.session.flush()
    gone = world.session.query(qstore.DatasetQualityReport).filter_by(dataset_id=d.id).one()
    world.session.delete(gone)
    world.session.flush()
    refused_both("a frozen report that was removed", "quality_report_changed")


def s_legacy(world: World) -> None:
    conn = world.connection("all", ALL_PURPOSES)
    a = world.dataset("l-train", "training", 30, conn)
    h = world.dataset("l-held", "held_out_evaluation", 6, conn)
    d = world.dataset("l-dev", "candidate_development", 6, conn)

    def rid(dataset: Any, position: int) -> str:
        return f"{dataset.id}:{position}"

    train_ids = [(rid(a, i), "train", f"g{i}") for i in range(14)]
    val_ids = [(rid(a, i), "validation", f"g{i}") for i in range(14, 20)]
    heldout_in_training = [(rid(a, i), "heldout_test", f"g{i}") for i in range(20, 23)]
    heldout = [(rid(h, i), "heldout_test", f"h{i}") for i in range(6)]
    assignments = tuple(sorted([*train_ids, *val_ids, *heldout_in_training, *heldout]))
    legacy = ism.LegacyThreeSplitManifest(
        "legacy-seed", assignments, ism.compute_legacy_sha256("legacy-seed", assignments)
    )

    def migrate(
        name: str, legacy_manifest: Any, datasets: list[Any], development: Any = None, **over: Any
    ) -> Any:
        kwargs: dict[str, Any] = {
            "project_id": _PROJECT,
            "name": name,
            "legacy": legacy_manifest,
            "sources": [world.source(x) for x in datasets],
            "development_dataset_id": development.id if development is not None else None,
            "prior_sources": (),
            "route": ROUTE,
            "normalization_version": "text-normalization-v1",
            "tokenizer_identity": None,
            "actor": ADMIN,
            "now": _NOW,
        }
        kwargs.update(over)
        return store.migrate_legacy_split_manifest(world.session, _CTX, **kwargs)

    pinned_legacy = ism.LegacyThreeSplitManifest(
        "s",
        ((rid(h, 0), "train", "x"), (rid(a, 0), "heldout_test", "y")),
        ism.compute_legacy_sha256(
            "s", ((rid(h, 0), "train", "x"), (rid(a, 0), "heldout_test", "y"))
        ),
    )
    refuses(
        world,
        "a legacy 'train' record of a held-out dataset",
        "source_role_pinned",
        lambda: migrate("p", pinned_legacy, [a, h]),
        "NEG-LEGACY-PIN",
    )
    dev_legacy = ism.LegacyThreeSplitManifest(
        "s",
        ((rid(d, 0), "validation", "x"),),
        ism.compute_legacy_sha256("s", ((rid(d, 0), "validation", "x"),)),
    )
    refuses(
        world,
        "a legacy 'validation' record of a development dataset",
        "source_role_pinned",
        lambda: migrate("p2", dev_legacy, [d]),
        "NEG-LEGACY-PIN",
    )
    refuses(
        world,
        "a legacy record that no source supplies",
        "invalid_request",
        lambda: migrate("m", legacy, [h]),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "a legacy manifest that is not one",
        "invalid_request",
        lambda: migrate("m", {"x": 1}, [a, h]),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "a project id that is not a UUID",
        "invalid_request",
        lambda: migrate("m", legacy, [a, h], project_id="x"),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "no sources",
        "invalid_request",
        lambda: migrate("m", legacy, [], sources=[]),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "the same source twice",
        "invalid_request",
        lambda: migrate("m", legacy, [a, a, h]),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "a development dataset that is not among the sources",
        "invalid_request",
        lambda: migrate("m", legacy, [a, h], development=d),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "a development dataset that is not candidate_development",
        "invalid_request",
        lambda: migrate("m", legacy, [a, h], development=a),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "an actor that may not see held-out data",
        "actor_not_permitted",
        lambda: migrate("m", legacy, [a, h], actor=ML),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "a blank name",
        "invalid_request",
        lambda: migrate(" ", legacy, [a, h]),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "a blank normalization version",
        "invalid_request",
        lambda: migrate("m", legacy, [a, h], normalization_version=""),
        "NEG-LEGACY-INPUT",
    )
    refuses(
        world,
        "a blank tokenizer identity",
        "invalid_request",
        lambda: migrate("m", legacy, [a, h], tokenizer_identity=""),
        "NEG-LEGACY-INPUT",
    )

    insufficient = migrate("l-nodev", legacy, [a, h])
    check(
        "NEG-LEGACY-MAP",
        "stored under the legacy strategy with its lineage hash and zero ratios, proposed",
        insufficient.strategy == "legacy_three_split_v1"
        and insufficient.migrated_from_legacy_sha256 == legacy.legacy_sha256
        and list(insufficient.ratios) == [0, 0, 0, 0]
        and insufficient.seed == "legacy-seed"
        and insufficient.status == "proposed"
        and world.audit.actions()[-1] == "dataset_split_manifest.legacy_migrated"
        and world.audit.events[-1]["resource_id"] == str(insufficient.id),
    )

    def ids(manifest: Any, role: str) -> set[str]:
        return {
            r.record_id
            for r in world.session.query(store.DatasetSplitAssignment).filter_by(
                manifest_id=manifest.id, role=role
            )
        }

    check(
        "NEG-LEGACY-MAP",
        "heldout_test lands only in final_qualification (including those of a training dataset); train/validation keep theirs",
        ids(insufficient, "final_qualification") == {x[0] for x in [*heldout, *heldout_in_training]}
        and ids(insufficient, "train") == {x[0] for x in train_ids}
        and ids(insufficient, "validation") == {x[0] for x in val_ids}
        and ids(insufficient, "development_eval") == set(),
    )
    check(
        "NEG-LEGACY-MAP",
        "records of a source that the legacy manifest does not place are not stored",
        world.session.query(store.DatasetSplitAssignment)
        .filter_by(manifest_id=insufficient.id)
        .count()
        == len(assignments)
        and world.session.query(store.DatasetSplitManifestSource)
        .filter_by(manifest_id=insufficient.id)
        .count()
        == 2,
    )
    refuses(
        world,
        "without a development role the manifest cannot lock",
        "manifest_insufficient",
        lambda: world.approve(insufficient),
        "NEG-LEGACY-INSUFFICIENT",
    )

    full = migrate("l-dev", legacy, [a, h, d], development=d)
    check(
        "NEG-LEGACY-MAP",
        "with an explicit candidate_development dataset the development role is exactly that dataset",
        ids(full, "development_eval") == {f"{d.id}:{i}" for i in range(6)},
    )
    named = migrate(
        "Legacy-Ñ",
        legacy,
        [a, h, d],
        development=d,
        normalization_version="Norm-X",
        tokenizer_identity="Tok-Y",
    )
    check(
        "NEG-LEGACY-MAP",
        "name, normalization version, tokenizer identity and clock come from the call",
        (named.name, named.normalization_version, named.tokenizer_identity, named.created_by)
        == ("Legacy-Ñ", "Norm-X", "Tok-Y", ADMIN.actor_id)
        and named.created_at.replace(tzinfo=None) == _NOW.replace(tzinfo=None)
        and named.version == 1,
    )
    spare = world.dataset("l-spare", "training", 5, conn)
    refuses(
        world,
        "a development dataset that is a plain training dataset with its own records",
        "invalid_request",
        lambda: migrate("l-spare", legacy, [a, h, spare], development=spare),
        "NEG-LEGACY-INPUT",
    )
    clash = world.dataset("l-clash", "candidate_development", 2, conn)
    world.records[clash.id] = (
        replace(world.records[clash.id][0], group_keys=("g0",)),
        *world.records[clash.id][1:],
    )
    refuses(
        world,
        "a development record whose group is a legacy group would cross a boundary",
        "invalid_request",
        lambda: migrate("l-clash", legacy, [a, h, clash], development=clash),
        "NEG-LEGACY-INPUT",
    )
    exact_legacy_assignments = tuple(
        sorted((rid(h, i), "heldout_test", f"e{i}") for i in range(6))
        + sorted((rid(a, i), "train", f"t{i}") for i in range(30))
    )
    exact_legacy = ism.LegacyThreeSplitManifest(
        "exact",
        exact_legacy_assignments,
        ism.compute_legacy_sha256("exact", exact_legacy_assignments),
    )
    exact = migrate("l-exact", exact_legacy, [a, h, d], development=d)
    check(
        "NEG-LEGACY-MAP",
        "a legacy manifest that places every supplied record is accepted",
        exact.version == 1
        and world.session.query(store.DatasetSplitAssignment)
        .filter_by(manifest_id=exact.id)
        .count()
        == 30 + 6 + 6,
    )
    locked = world.approve(full)
    check("NEG-LEGACY-MAP", "and the migrated manifest locks", locked.status == "locked")
    view = store.load_training_split(world.session, _CTX, project_id=_PROJECT, manifest_id=full.id)
    served = {r.record_id for r in view.rows}
    check(
        "NEG-LEGACY-VIEW",
        "no heldout_test record is ever in the training view",
        not (served & {x[0] for x in [*heldout, *heldout_in_training]})
        and served == {x[0] for x in [*train_ids, *val_ids]},
    )


def s_reads(world: World) -> None:
    ds = standard_world(world)
    a, b, d, h = ds["a"], ds["b"], ds["d"], ds["h"]
    made = [world.propose(f"read-{i}", [a, b, d, h]) for i in range(3)]
    summary = store.get_split_manifest(
        world.session, _CTX, project_id=_PROJECT, manifest_id=made[0].id
    )
    check(
        "ACC-FREEZE-READ",
        "the summary has the manifest, four role rows in ROLES order and four sources by dataset id",
        summary.manifest.id == made[0].id
        and [r.role for r in summary.roles] == list(ROLES)
        and len(summary.sources) == 4
        and [str(s.dataset_id) for s in summary.sources] == sorted(str(x.id) for x in (a, b, d, h)),
    )
    refuses(
        world,
        "reading a manifest that does not exist",
        "manifest_not_found",
        lambda: store.get_split_manifest(
            world.session, _CTX, project_id=_PROJECT, manifest_id=det_uuid("none")
        ),
        "ACC-FREEZE-READ",
    )
    refuses(
        world,
        "reading with a project that is not the manifest's",
        "manifest_not_found",
        lambda: store.get_split_manifest(
            world.session, _CTX, project_id=det_uuid("elsewhere"), manifest_id=made[0].id
        ),
        "ACC-FREEZE-READ",
    )
    page = store.list_split_manifests(world.session, _CTX, project_id=_PROJECT, limit=2)
    check(
        "ACC-FREEZE-READ",
        "list pages by id with a limit",
        len(page) == 2 and [m.id for m in page] == sorted(m.id for m in made)[:2],
    )
    rest = store.list_split_manifests(
        world.session, _CTX, project_id=_PROJECT, limit=200, after=page[-1].id
    )
    check(
        "ACC-FREEZE-READ",
        "list continues after the last id and a foreign project lists nothing",
        [m.id for m in rest] == sorted(m.id for m in made)[2:]
        and store.list_split_manifests(
            world.session, _CTX, project_id=det_uuid("elsewhere"), limit=5
        )
        == [],
    )
    for limit in (0, 201, True, "5", None, 1.5):
        check(
            "ACC-FREEZE-READ",
            f"list refuses limit {limit!r}",
            raises(
                lambda limit=limit: store.list_split_manifests(
                    world.session, _CTX, project_id=_PROJECT, limit=limit
                ),
                ValueError,
            ),
        )
    check(
        "ACC-FREEZE-READ",
        "list accepts limits 1 and 200",
        len(store.list_split_manifests(world.session, _CTX, project_id=_PROJECT, limit=1)) == 1,
    )
    check(
        "ACC-FREEZE-READ",
        "list refuses an 'after' that is not a UUID",
        raises(
            lambda: store.list_split_manifests(
                world.session, _CTX, project_id=_PROJECT, limit=5, after="x"
            ),
            ValueError,
        ),
    )
    check(
        "ACC-FREEZE-READ",
        "an unknown refusal code is a programming error, not a response",
        raises(lambda: store.SplitManifestError("made_up"), AssertionError),
    )
    check(
        "ACC-FREEZE-READ",
        "every stable code is unique and sorted",
        list(store.CODES) == sorted(set(store.CODES)),
    )


# -- hardening: pure helpers ----------------------------------------------------

UP = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
LOW = UP.lower()


def p_hardening() -> None:
    check(
        "ACC-FREEZE-ROWS",
        "same() is exact-type, case- and whitespace-sensitive string equality",
        ism.same("a", "a") is True
        and ism.same("A", "a") is False
        and ism.same("a", "A") is False
        and ism.same(" a", "a") is False
        and ism.same("a", " a") is False
        and ism.same("a ", "a") is False
        and ism.same(StrSub("a"), "a") is False
        and ism.same("a", StrSub("a")) is False
        and ism.same(1, 1) is False
        and ism.same(None, None) is False,
    )
    check(
        "ACC-FREEZE-ROWS",
        "pg_safe refuses NUL and lone surrogates and accepts any other text",
        ism.pg_safe("a") is True
        and ism.pg_safe("") is True
        and ism.pg_safe("é مرحبا 日本") is True
        and ism.pg_safe("a\x00") is False
        and ism.pg_safe("\x00") is False
        and ism.pg_safe("\ud800") is False,
    )
    for label, make in (
        (
            "an uppercase dataset id",
            lambda: ism.AssignmentRow(f"{UP}:0", UP, 0, "train", "g", "a" * 64, "b" * 64),
        ),
        (
            "an uppercase record id over a lowercase dataset id",
            lambda: ism.AssignmentRow(f"{UP}:0", LOW, 0, "train", "g", "a" * 64, "b" * 64),
        ),
        (
            "a group id with NUL",
            lambda: ism.AssignmentRow(f"{LOW}:0", LOW, 0, "train", "g\x00", "a" * 64, "b" * 64),
        ),
        (
            "a group id with a lone surrogate",
            lambda: ism.AssignmentRow(f"{LOW}:0", LOW, 0, "train", "\ud800", "a" * 64, "b" * 64),
        ),
        (
            "a padded group id",
            lambda: ism.AssignmentRow(f"{LOW}:0", LOW, 0, "train", "g ", "a" * 64, "b" * 64),
        ),
        (
            "a role with different case",
            lambda: ism.AssignmentRow(f"{LOW}:0", LOW, 0, "Train", "g", "a" * 64, "b" * 64),
        ),
        ("a purpose with different case", lambda: ism.RoleRow("train", "Training", 1, 1, "a" * 64)),
        ("a padded purpose", lambda: ism.RoleRow("train", " training", 1, 1, "a" * 64)),
        (
            "an uppercase source dataset id",
            lambda: ism.SourceGrant(
                UP, 1, "training", "a" * 64, CONN, ("training",), REPORT, "b" * 64, "c" * 64
            ),
        ),
        (
            "an uppercase report id",
            lambda: ism.SourceGrant(
                DS_A, 1, "training", "a" * 64, CONN, ("training",), UP, "b" * 64, "c" * 64
            ),
        ),
        ("an uppercase purpose", lambda: ism.pinned_role_for_purpose("Training")),
        ("a padded purpose name", lambda: ism.pinned_role_for_purpose(" training")),
        (
            "a purpose with an uppercase pinned name",
            lambda: ism.pinned_role_for_purpose("Held_Out_Evaluation"),
        ),
    ):
        check("ACC-FREEZE-ROWS", f"{label} is refused", raises(make))
    check(
        "ACC-FREEZE-ROWS",
        "a 200-character group id is not accepted over the 400 cap but 400 is, and so are non-ASCII ids",
        ism.AssignmentRow(f"{LOW}:0", LOW, 0, "train", "é" * 400, "a" * 64, "b" * 64).group_id
        == "é" * 400,
    )
    roles = ism.build_role_rows(
        [
            row(DS_A, 0, "train", "g0"),
            row(DS_A, 1, "validation", "g1"),
            row(DS_B, 0, "development_eval", "g2"),
            row(DS_B, 1, "final_qualification", "g3"),
        ]
    )

    def grant(**over: Any) -> ism.SourceGrant:
        values: dict[str, Any] = {
            "dataset_id": DS_A,
            "dataset_version": 1,
            "dataset_purpose": "training",
            "dataset_sha256": "a" * 64,
            "dataset_source_connection_id": CONN,
            "allowed_purposes_snapshot": ("held_out_evaluation", "training"),
            "quality_report_id": REPORT,
            "quality_report_sha256": "b" * 64,
            "excluded_positions_sha256": "c" * 64,
            **over,
        }
        return ism.SourceGrant(**values)

    base = make_content(roles, grant)
    for label, bad in (
        ("an upper-case leakage status", {"leakage_status": "CLEAR"}),
        ("a padded leakage status", {"leakage_status": " clear"}),
        ("a review that is not a mapping", {"leakage_reviews": ("x",)}),
        ("a name with NUL", {"name": "a\x00b"}),
        ("a seed with a lone surrogate", {"seed": "\ud800"}),
        ("an upper-case project id", {"project_id": UP}),
    ):
        check(
            "ACC-FREEZE-ROWS",
            f"ManifestContent refuses {label}",
            raises(lambda bad=bad: make_content(roles, grant, **bad)),
        )
    check(
        "NEG-LEGACY-LINEAGE",
        "only the exact legacy strategy name asks for a lineage hash",
        make_content(roles, grant, strategy="Legacy_Three_Split_V1").migrated_from_legacy_sha256
        is None
        and raises(
            lambda: make_content(
                roles, grant, strategy="Legacy_Three_Split_V1", migrated_from_legacy_sha256=sha("l")
            )
        ),
    )
    check(
        "ACC-FREEZE-ROWS",
        "a name of 200 mixed-case, non-ASCII characters is accepted",
        make_content(roles, grant, name="Ñ" * 200).name == "Ñ" * 200,
    )
    changed_counts = (replace(roles[0], record_count=2, group_count=2), *roles[1:])
    changed_groups = (replace(roles[0], record_count=2, group_count=1), *roles[1:])
    hashes = {
        ism.compute_proposal_sha256(replace(base, roles=r))
        for r in (roles, changed_counts, changed_groups)
    }
    check(
        "ACC-FREEZE-HASH",
        "the proposal hash covers each role's record and group counts",
        len(hashes) == 3,
    )
    check(
        "ACC-FREEZE-HASH",
        "the proposal hash covers the source dataset id",
        ism.compute_proposal_sha256(replace(base, sources=(grant(dataset_id=DS_B),)))
        != ism.compute_proposal_sha256(base),
    )
    roles_swapped = (roles[1], roles[0], *roles[2:])
    check(
        "ACC-FREEZE-ROWS",
        "role rows out of ROLES order are refused",
        raises(lambda: replace(base, roles=roles_swapped)),
    )
    check(
        "ACC-FREEZE-HASH",
        "the role rows are positional in the proposal body",
        json.dumps(ism._role_list(roles[0]))
        == json.dumps([roles[0].record_count, roles[0].group_count, roles[0].role_sha256]),
    )
    check(
        "ACC-FREEZE-ROWS",
        "build_role_rows refuses a duplicate record id among otherwise valid rows",
        raises(
            lambda: ism.build_role_rows(
                [row(DS_A, 0, "train", "g0"), row(DS_A, 0, "validation", "g1")]
            )
        ),
    )
    check(
        "ACC-FREEZE-ROWS",
        "build_role_rows accepts distinct rows of one dataset",
        len(
            ism.build_role_rows([row(DS_A, 0, "train", "g0"), row(DS_A, 1, "train", "g0")])[
                0
            ].role_sha256
        )
        == 64,
    )
    check(
        "NEG-LEGACY-REFUSE",
        "an upper-case heldout_test is not the legacy role name",
        ism.is_legacy_three_split(["HELDOUT_TEST"]) is False
        and ism.is_legacy_three_split("Heldout_Test") is False,
    )


# -- hardening: the store ---------------------------------------------------------


def bad_texts(limit: int) -> list[Any]:
    return [5, "", " x", "x ", "x\x00y", "\ud800", "n" * (limit + 1), None]


def s_hardening(world: World) -> None:
    ds = standard_world(world)
    a, b, d, h, conn = ds["a"], ds["b"], ds["d"], ds["h"], ds["conn"]
    base_args = [b, d, h]

    for bad_code in ("INVALID_REQUEST", " invalid_request", "invalid_request ", "Invalid_Request"):
        check(
            "ACC-FREEZE-REFUSE",
            f"an unknown code {bad_code!r} is a programming error",
            raises(lambda c=bad_code: store.SplitManifestError(c), AssertionError),
        )
    stamp = store._now()
    check(
        "ACC-FREEZE-AUDIT",
        "the clock is timezone-aware UTC and current",
        stamp.tzinfo is not None and abs((datetime.now(UTC) - stamp).total_seconds()) < 60,
    )

    # Early refusals: nothing queried, nothing written.
    for field, limit in (("actor_type", 64), ("actor_id", 200), ("role", 64), ("request_id", 100)):
        for bad in bad_texts(limit):
            values = {
                "actor_type": "user",
                "actor_id": "a",
                "role": "workspace_admin",
                "request_id": "r",
            }
            values[field] = bad
            refuses(
                world,
                f"actor {field}={str(bad)[:12]!r}",
                "invalid_request",
                lambda values=values: world.propose(
                    "h1", base_args, actor=store.SplitActor(**values)
                ),
                early=True,
            )
    for role in ("Workspace_Admin", "ORGANIZATION_ADMIN", "viewer", "admin"):
        refuses(
            world,
            f"role {role!r} may not see held-out data",
            "actor_not_permitted",
            lambda role=role: world.propose(
                "h2", base_args, actor=store.SplitActor("user", "a", role, "r")
            ),
            early=True,
        )
    for label, over, limit in (
        ("name", "force_name", 200),
        ("normalization_version", "normalization_version", 64),
        ("tokenizer_identity", "tokenizer_identity", 200),
    ):
        for bad in bad_texts(limit):
            if over == "tokenizer_identity" and bad is None:
                continue
            refuses(
                world,
                f"{label}={str(bad)[:12]!r}",
                "invalid_request",
                lambda over=over, bad=bad: world.propose("h3", base_args, **{over: bad}),
                early=True,
            )
    refuses(
        world,
        "a project id that is not a UUID",
        "invalid_request",
        lambda: world.propose("h4", base_args, project_id="x"),
        early=True,
    )
    refuses(
        world,
        "sources that are not a list",
        "invalid_request",
        lambda: world.propose("h5", [], sources="abc"),
        early=True,
    )
    refuses(
        world,
        "a source that is not a SplitSourceInput",
        "invalid_request",
        lambda: world.propose("h5", [], sources=["x"]),
        early=True,
    )
    refuses(
        world,
        "a repeated dataset",
        "invalid_request",
        lambda: world.propose("h5", [], sources=[world.source(b), world.source(b)]),
        early=True,
    )
    refuses(
        world,
        "no sources",
        "invalid_request",
        lambda: world.propose("h5", [], sources=()),
        early=True,
    )
    for label, bad_sources in (("an int", 5), ("None", None), ("a dict", {})):
        refuses(
            world,
            f"sources as {label}",
            "invalid_request",
            lambda bad_sources=bad_sources: world.propose("h5", [], sources=bad_sources),
            early=True,
        )
    original = store.MAX_MANIFEST_RECORDS
    store.MAX_MANIFEST_RECORDS = 15
    try:
        refuses(
            world,
            "18 supplied records over a limit of 15",
            "manifest_too_large",
            lambda: world.propose("h6", base_args),
            early=True,
        )
    finally:
        store.MAX_MANIFEST_RECORDS = original
    # The boundaries themselves are accepted, with mixed-case, non-ASCII labels.
    wide = world.propose(
        "Ñ" * 200, base_args, normalization_version="V" * 64, tokenizer_identity="Ö" * 200
    )
    check(
        "ACC-FREEZE-REFUSE",
        "a 200-character name, a 64-character normalization version and a 200-character tokenizer are accepted",
        wide.name == "Ñ" * 200
        and wide.normalization_version == "V" * 64
        and wide.tokenizer_identity == "Ö" * 200,
    )

    # Group keys: Postgres-safe text of at most 400 characters.
    rec = world.records[b.id][0]
    for label, keys in (
        ("a NUL", ("a\x00",)),
        ("a lone surrogate", ("\ud800",)),
        ("401 characters", ("k" * 401,)),
        ("an empty key", ("",)),
        ("a non-string key", (5,)),
        ("a list instead of a tuple", ["a"]),
    ):
        refuses(
            world,
            f"a group key with {label}",
            "invalid_request",
            lambda keys=keys: world.propose(
                "h7",
                [],
                sources=[
                    store.SplitSourceInput(
                        b.id, (replace(rec, group_keys=keys), *world.records[b.id][1:])
                    )
                ],
            ),
        )
    keyed = world.propose(
        "Keyed",
        [],
        sources=[
            store.SplitSourceInput(
                b.id, (replace(rec, group_keys=("z" * 400, "Ünï")), *world.records[b.id][1:])
            )
        ],
    )
    stored = (
        world.session.query(store.DatasetSplitAssignment)
        .filter_by(manifest_id=keyed.id, record_id=f"{b.id}:0")
        .one()
    )
    check(
        "ACC-FREEZE-ROWS",
        "a 400-character key is accepted and the group id is the smallest key",
        stored.group_id == "Ünï" or stored.group_id == min("z" * 400, "Ünï"),
    )

    # A dataset whose upload session is gone, a spec seed Postgres cannot store, a bad route.
    orphan = world.dataset("orphan", "training", 10, conn)
    world.session.delete(world.session.get(ustore.DatasetUploadSession, orphan.upload_session_id))
    world.session.flush()
    refuses(
        world,
        "a dataset whose upload session is missing",
        "dataset_not_found",
        lambda: world.propose("h8", [orphan, d, h]),
    )
    for label, seed in (
        ("a padded seed", "s "),
        ("a seed with NUL", "s\x00"),
        ("a seed with a lone surrogate", "\ud800"),
    ):
        refuses(
            world,
            label,
            "invalid_request",
            lambda seed=seed: world.propose(
                "h9", base_args, spec=SplitSpec("sha256_group_hash_v1", seed, (70, 10, 10, 10))
            ),
        )
    refuses(
        world,
        "a route that is not an EmbeddingRoute",
        "invalid_request",
        lambda: world.propose("h10", base_args, route="internal"),
    )

    # Mixed-case names version and replay correctly.
    name = "Campaign Ünï-A"
    first = world.propose(name, [a, b, d, h])
    again = world.propose(name, [a, b, d, h])
    check(
        "ACC-VERSION-IDEMPOTENT", "a mixed-case name replays to the same row", again.id == first.id
    )
    second = world.propose(
        name, [a, b, d, h], spec=SplitSpec("sha256_group_hash_v1", "seed-b", (70, 10, 10, 10))
    )
    check(
        "ACC-VERSION-CHANGE",
        "and versions from it",
        (second.version, second.parent_version) == (2, 1),
    )
    locked = world.approve(first)
    check("ACC-FREEZE-APPROVE", "and locks", locked.status == "locked")
    prior = prior_for(world, locked)
    check(
        "ACC-FREEZE-PRIOR",
        "the derived prior label keeps the name's case",
        prior[0].label == f"{name}@v1:development_eval",
    )
    shouted = PriorSource(prior[0], label=prior[0].label.lower())
    refuses(
        world,
        "a prior label in another case",
        "prior_sources_incomplete",
        lambda: world.propose("Next", [a, b, d, h], prior_sources=(shouted, prior[1])),
    )
    accepted = world.propose("Next", [a, b, d, h], prior_sources=prior)
    check("ACC-FREEZE-PRIOR", "the exact labels are accepted", accepted.version == 1)
    refuses(
        world,
        "prior sources as a tuple of non-suites",
        "invalid_request",
        lambda: world.propose("Next2", [a, b, d, h], prior_sources=(1, 2)),
    )
    refuses(
        world,
        "a prior suite repeating one role and omitting the other",
        "prior_sources_incomplete",
        lambda: world.propose("Next2", [a, b, d, h], prior_sources=(prior[0], prior[0])),
    )

    # Tampering with stored proposals.
    proposal = world.propose("Tamper-1", [a, b, d, h], prior_sources=prior)
    original_norm = proposal.normalization_version
    proposal.normalization_version = "norm-edited"
    world.session.flush()
    refuses(
        world,
        "an edited normalization version (only the proposal hash covers it)",
        "manifest_tampered",
        lambda: world.approve(proposal),
    )
    proposal.normalization_version = original_norm
    original_hash = proposal.proposal_sha256
    proposal.proposal_sha256 = original_hash.upper()
    world.session.flush()
    refuses(
        world,
        "a stored proposal hash in another case",
        "manifest_tampered",
        lambda: world.approve(proposal, expected_proposal_sha256=original_hash.upper()),
    )
    refuses(
        world,
        "an expected hash in another case",
        "manifest_changed",
        lambda: world.approve(proposal, expected_proposal_sha256=original_hash.upper() + " "),
    )
    proposal.proposal_sha256 = original_hash
    proposal.status = "Proposed"
    world.session.flush()
    refuses(
        world,
        "a status in another case is not proposed",
        "manifest_not_proposed",
        lambda: world.approve(proposal),
    )
    refuses(
        world,
        "and cannot be rejected",
        "manifest_not_proposed",
        lambda: store.reject_split_manifest(
            world.session, _CTX, project_id=_PROJECT, manifest_id=proposal.id, actor=ADMIN
        ),
    )
    proposal.status = "proposed"
    world.session.flush()

    # Reviews that are not lists, NUL in a rationale.
    for label, reviews in (("an int", 5), ("None", None), ("a dict", {})):
        refuses(
            world,
            f"reviews as {label}",
            "invalid_review",
            lambda reviews=reviews: world.approve(proposal, reviews=reviews),
        )
    refuses(
        world,
        "a rationale with NUL",
        "invalid_review",
        lambda: world.approve(proposal, reviews=(store.ReviewInput("f" * 64, "no\x00pe"),)),
    )
    refuses(
        world,
        "a rationale with a lone surrogate",
        "invalid_review",
        lambda: world.approve(proposal, reviews=(store.ReviewInput("f" * 64, "\ud800"),)),
    )
    refuses(
        world,
        "a rationale that is not text",
        "invalid_review",
        lambda: world.approve(proposal, reviews=(store.ReviewInput("f" * 64, 5),)),
    )


def s_tiny(world: World) -> None:
    """The smallest lockable shape: exactly one record in each evaluation role."""
    conn = world.connection("all", ALL_PURPOSES)
    a = world.dataset("tiny-train", "training", 40, conn)
    d = world.dataset("tiny-dev", "candidate_development", 1, conn)
    h = world.dataset("tiny-held", "held_out_evaluation", 1, conn)
    world.records[a.id] = tuple(
        replace(r, event_time=10 + r.batch_position) for r in world.records[a.id]
    )
    world.records[d.id] = tuple(replace(r, event_time=5000) for r in world.records[d.id])
    world.records[h.id] = tuple(replace(r, event_time=5000) for r in world.records[h.id])
    spec = SplitSpec("sha256_group_hash_v1", "tiny", (80, 10, 5, 5), cutoff=1000)
    tiny = world.propose("Tiny", [a, d, h], spec=spec)
    counts_by_role = {
        r.role: r.record_count
        for r in world.session.query(store.DatasetSplitManifestRole).filter_by(manifest_id=tiny.id)
    }
    check(
        "ACC-FREEZE-ROLES",
        f"development and final hold exactly one record each: {counts_by_role}",
        counts_by_role["development_eval"] == 1
        and counts_by_role["final_qualification"] == 1
        and counts_by_role["train"] >= 1
        and counts_by_role["validation"] >= 1,
    )
    original_flush = world.session.flush

    def exploding_flush(*args: Any, **kwargs: Any) -> None:
        if tiny.status == "locked":
            raise IntegrityError("update", {}, Exception("trigger refused the lock"))
        original_flush(*args, **kwargs)

    world.session.flush = exploding_flush  # type: ignore[method-assign]
    try:
        got = code_of(lambda: world.approve(tiny))
    finally:
        world.session.flush = original_flush  # type: ignore[method-assign]
    world.session.expire(tiny)
    check(
        "ACC-FREEZE-APPROVE",
        f"a lock the database refuses is manifest_insufficient: {got!r}",
        got == "manifest_insufficient",
    )
    check("ACC-FREEZE-APPROVE", "and the row is still proposed", tiny.status == "proposed")
    locked = world.approve(tiny)
    check("ACC-FREEZE-ROLES", "and a role with exactly one record locks", locked.status == "locked")
    cutoff_roles = {
        r.role
        for r in world.session.query(store.DatasetSplitAssignment).filter_by(manifest_id=tiny.id)
        if r.source_dataset_id == a.id
    }
    check(
        "ACC-FREEZE-ROLES",
        "pre-cutoff records of the free source stay out of evaluation roles",
        cutoff_roles <= {"train", "validation"},
    )


def s_prior_groups(world: World) -> None:
    conn = world.connection("all", ALL_PURPOSES)
    t0 = world.dataset("g-train-0", "training", 30, conn)
    d0 = world.dataset("g-dev-0", "candidate_development", 4, conn)
    h0 = world.dataset("g-held-0", "held_out_evaluation", 4, conn)
    world.records[h0.id] = (
        replace(world.records[h0.id][0], group_keys=("z",)),
        *world.records[h0.id][1:],
    )
    first = world.propose("g0", [t0, d0, h0])
    locked = world.approve(first)
    prior = prior_for(world, locked)
    final_label = prior[1].label
    found = False
    for attempt in range(6):
        t1 = world.dataset(f"g-train-1-{attempt}", "training", 30, conn)
        d1 = world.dataset(f"g-dev-1-{attempt}", "candidate_development", 4, conn)
        h1 = world.dataset(f"g-held-1-{attempt}", "held_out_evaluation", 4, conn)
        world.records[t1.id] = (
            replace(world.records[t1.id][0], group_keys=("a", "z")),
            *world.records[t1.id][1:],
        )
        second = world.propose(f"g1-{attempt}", [t1, d1, h1], prior_sources=prior)
        role = (
            world.session.query(store.DatasetSplitAssignment)
            .filter_by(manifest_id=second.id, record_id=f"{t1.id}:0")
            .one()
            .role
        )
        if role == "development_eval":
            continue
        hits = [
            f
            for f in second.leakage_report["findings"]
            if f["method"] == "group"
            and f["prior_source_label"] == final_label
            and f"{t1.id}:0" in (f["record_id_a"], f["record_id_b"])
        ]
        check(
            "ACC-FREEZE-PRIOR",
            f"a linkage key shared with a prior final record is a group finding (record in {role})",
            len(hits) >= 1,
        )
        found = True
        break
    check("ACC-FREEZE-PRIOR", "the group scenario ran", found)


def s_tamper(world: World) -> None:
    ds = standard_world(world)
    a, b, d, h, conn_a = ds["a"], ds["b"], ds["d"], ds["h"], ds["conn_a"]
    m1 = world.propose("campaign", [a, b, d, h])
    pending = world.propose("pending", [b, d, h])
    world.approve(m1)

    def training() -> Any:
        return store.load_training_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=m1.id
        )

    def development() -> Any:
        return store.load_development_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=m1.id
        )

    # A grant that only the first source's connection holds.
    world.connections[conn_a].allowed_purposes_json = [
        "validation",
        "candidate_development",
        "held_out_evaluation",
    ]
    refuses(
        world,
        "the training grant of source a alone removed",
        "grant_changed",
        training,
        "NEG-FINAL-GRANT",
    )
    world.connections[conn_a].allowed_purposes_json = list(ALL_PURPOSES)
    check("NEG-FINAL-GRANT", "restored, it serves again", len(training().rows) > 0)

    # Status / frozen columns.
    m1.frozen_manifest_sha256 = None
    world.session.flush()
    refuses(
        world,
        "a locked manifest without its frozen hash",
        "manifest_not_locked",
        training,
        "NEG-FINAL-TAMPER",
    )
    good_frozen = store._content_from_rows(
        m1,
        store._fetch_roles(world.session, _CTX, m1.id),
        store._fetch_sources(world.session, _CTX, m1.id),
    )
    m1.frozen_manifest_sha256 = ism.compute_frozen_manifest_sha256(good_frozen)
    m1.status = "Locked"
    world.session.flush()
    refuses(
        world,
        "a status in another case is not locked",
        "manifest_not_locked",
        training,
        "NEG-FINAL-TAMPER",
    )
    m1.status = "locked"
    world.session.flush()

    # A forged reviewed hash with a recomputed frozen hash: only the reviewed-report check sees it.
    real_reviewed = m1.reviewed_leakage_report_sha256
    m1.reviewed_leakage_report_sha256 = "7" * 64
    world.session.flush()
    forged = store._content_from_rows(
        m1,
        store._fetch_roles(world.session, _CTX, m1.id),
        store._fetch_sources(world.session, _CTX, m1.id),
    )
    real_frozen = m1.frozen_manifest_sha256
    m1.frozen_manifest_sha256 = ism.compute_frozen_manifest_sha256(forged)
    world.session.flush()
    refuses(
        world,
        "a reviewed-report hash forged with a recomputed frozen hash",
        "manifest_tampered",
        training,
        "NEG-FINAL-TAMPER",
    )
    m1.reviewed_leakage_report_sha256 = real_reviewed
    m1.frozen_manifest_sha256 = real_frozen
    world.session.flush()
    check("NEG-FINAL-TAMPER", "restored, it serves again", len(training().rows) > 0)

    # A development record edited: the development view refuses, the training view does not.
    dev_row = (
        world.session.query(store.DatasetSplitAssignment)
        .filter_by(manifest_id=m1.id, role="development_eval")
        .first()
    )
    good_hash = dev_row.record_sha256
    dev_row.record_sha256 = "8" * 64
    world.session.flush()
    refuses(
        world, "an edited development record", "manifest_tampered", development, "NEG-FINAL-TAMPER"
    )
    check("NEG-FINAL-TAMPER", "the training view is unaffected by it", len(training().rows) > 0)
    dev_row.record_sha256 = good_hash
    world.session.flush()
    check(
        "NEG-FINAL-TAMPER",
        "restored, the development view serves again",
        len(development().rows) > 0,
    )

    # The second, independent role assertion: if the SQL filter ever served a wrong role.
    original_read = store._read_rows
    wrong_final = ism.AssignmentRow(
        f"{h.id}:0", str(h.id), 0, "final_qualification", "g", "a" * 64, "b" * 64
    )
    wrong_train = ism.AssignmentRow(f"{a.id}:0", str(a.id), 0, "train", "g", "a" * 64, "b" * 64)
    store._read_rows = lambda *args, **kwargs: (wrong_final,)  # type: ignore[assignment]
    try:
        refuses(
            world,
            "a final record reaching the training view",
            "manifest_tampered",
            training,
            "NEG-FINAL-VIEW",
        )
    finally:
        store._read_rows = original_read  # type: ignore[assignment]
    store._read_rows = lambda *args, **kwargs: (wrong_train,)  # type: ignore[assignment]
    try:
        refuses(
            world,
            "a train record reaching the development view",
            "manifest_tampered",
            development,
            "NEG-FINAL-VIEW",
        )
    finally:
        store._read_rows = original_read  # type: ignore[assignment]

    # An open leakage finding on a forged lock.
    ds2 = standard_world_2(world)
    semantic = world.propose(
        "forged", [ds2["a"], ds2["d"], ds2["h"]], prior_sources=prior_for(world, m1)
    )
    check(
        "NEG-FINAL-TAMPER",
        "the forged-lock scenario has an open finding",
        len(semantic.leakage_report["findings"]) >= 1,
    )
    semantic.status = "locked"
    semantic.approved_by = "attacker"
    semantic.locked_at = _NOW
    semantic.reviewed_leakage_report_sha256 = semantic.leakage_report_sha256
    world.session.flush()
    forged_content = store._content_from_rows(
        semantic,
        store._fetch_roles(world.session, _CTX, semantic.id),
        store._fetch_sources(world.session, _CTX, semantic.id),
    )
    semantic.frozen_manifest_sha256 = ism.compute_frozen_manifest_sha256(forged_content)
    world.session.flush()
    refuses(
        world,
        "a lock forged over an open review_required finding (training)",
        "leakage_not_clear",
        lambda: store.load_training_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=semantic.id
        ),
        "NEG-FINAL-VIEW",
    )
    refuses(
        world,
        "and for the development view",
        "leakage_not_clear",
        lambda: store.load_development_split(
            world.session, _CTX, project_id=_PROJECT, manifest_id=semantic.id
        ),
        "NEG-FINAL-VIEW",
    )
    _ = pending


def standard_world_2(world: World) -> dict[str, Any]:
    conn = world.connection("sem", ALL_PURPOSES)
    a = world.dataset("sem-train", "training", 40, conn)
    d = world.dataset("sem-dev", "candidate_development", 4, conn)
    h = world.dataset("sem-held", "held_out_evaluation", 4, conn)
    world.records[d.id] = (long_record(str(d.id), 0), *world.records[d.id][1:])
    world.records[h.id] = (variant(world.records[d.id][0], "zzzzzz"), *world.records[h.id][1:])
    return {"a": a, "d": d, "h": h}


def valid_finding_body(scores: Any) -> dict[str, Any]:
    """A report body whose only inconsistency is `scores`: the finding id and disposition are real."""
    from workers.data import split_leakage_detector as sl  # noqa: PLC0415

    body = json.loads(json.dumps(report_body(real_report()[0])))
    finding = {
        "finding_id": sl._finding_id(
            "exact_raw", "cross_role", ("a", "train"), ("b", "final_qualification"), None
        ),
        "method": "exact_raw",
        "reason": "cross_role",
        "disposition": "blocking",
        "record_id_a": "a",
        "role_a": "train",
        "record_id_b": "b",
        "role_b": "final_qualification",
        "prior_source_label": None,
        "scores": scores,
    }
    return {**body, "status": "leakage_found", "findings": [finding]}


def p_hardening_2() -> None:
    legacy_assignments = (("r1", "train", "g1"), ("r2", "heldout_test", "g2"))
    legacy = ism.LegacyThreeSplitManifest(
        "seed", legacy_assignments, ism.compute_legacy_sha256("seed", legacy_assignments)
    )
    for bad in (5, None, "d1", {}):
        check(
            "NEG-LEGACY-INPUT",
            f"migrate_legacy_three_split refuses development={bad!r}",
            raises(lambda bad=bad: ism.migrate_legacy_three_split(legacy, bad)),
        )
    check(
        "NEG-LEGACY-INPUT",
        "a development id equal to a legacy id is refused",
        raises(lambda: ism.migrate_legacy_three_split(legacy, [("r1", "gx")])),
    )
    roles = ism.build_role_rows(
        [
            row(DS_A, 0, "train", "g0"),
            row(DS_A, 1, "validation", "g1"),
            row(DS_B, 0, "development_eval", "g2"),
            row(DS_B, 1, "final_qualification", "g3"),
        ]
    )

    def grant() -> ism.SourceGrant:
        return ism.SourceGrant(
            DS_A, 1, "training", "a" * 64, CONN, ("training",), REPORT, "b" * 64, "c" * 64
        )

    one = make_content(
        roles, grant, strategy="legacy_three_split_v1", migrated_from_legacy_sha256=sha("one")
    )
    two = make_content(
        roles, grant, strategy="legacy_three_split_v1", migrated_from_legacy_sha256=sha("two")
    )
    check(
        "NEG-LEGACY-LINEAGE",
        "two legacy manifests that differ only in their lineage hash have different proposal hashes",
        ism.compute_proposal_sha256(one) != ism.compute_proposal_sha256(two),
    )
    v1 = make_content(roles, grant)
    v2 = make_content(roles, grant, version=2, parent_version=1)
    v3 = make_content(roles, grant, version=3, parent_version=2)
    check(
        "ACC-VERSION-CHANGE",
        "versions 1, 2 and 3 have different proposal hashes",
        len({ism.compute_proposal_sha256(c) for c in (v1, v2, v3)}) == 3,
    )
    for label, scores in (
        ("a string that is not a number", [["exact", "abc"]]),
        ("None", [["exact", None]]),
        ("a list", [["exact", [1]]]),
        ("a numeric string", [["exact", "1"]]),
        ("a bool", [["exact", True]]),
        ("a string in a float's place", [["exact", "0.5"]]),
        ("an int (scores are floats)", [["exact", 1]]),
    ):
        check(
            "ACC-FREEZE-REPORT",
            f"a score that is {label} is refused with the module's own error",
            raises(lambda scores=scores: ism.report_from_body(valid_finding_body(scores))),
        )
    good = ism.report_from_body(valid_finding_body([["exact", 1.0], ["other", 0.5]]))
    check(
        "ACC-FREEZE-REPORT",
        "control: the same body with real numbers is accepted and keeps its scores",
        good.findings[0].scores == (("exact", 1.0), ("other", 0.5)),
    )
    check(
        "ACC-FREEZE-REPORT",
        "an integer-valued score and a float score both round-trip",
        ism._score(["x", 1]) == ("x", 1.0) and ism._score(["x", 0.5]) == ("x", 0.5),
    )


def main() -> None:
    part_p()
    p_hardening()
    p_hardening_2()
    part_m()
    part_s()
    if FAILURES:
        print(f"FTF-0519-P1: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0519-P1: all checks passed")


if __name__ == "__main__":
    main()
