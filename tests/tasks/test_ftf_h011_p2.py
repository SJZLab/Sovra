#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,list-item,unused-ignore,func-returns-value,attr-defined,misc,assignment,index,var-annotated,union-attr,operator,return-value,no-any-return"
# ruff: noqa: E501
"""FTF-H011-P2 -- dataset mapping persistence: pure helpers, migration 0035 and the store.

Runnable without a database or network (the live Postgres evidence is
`tests/integration/test_ftf_h011_p2_dataset_mappings.py`, run by CI's
`dataset-mapping-store-integration` job). Three parts:

P  pure helpers of `dataset_mapping_store`: the canonical digests (golden values), the importer a
   verified content type selects, the propose-time critical checks, the ignored-message-key reader.
M  migration `0035_dataset_mappings.py`: chained onto 0034 as the single head, ENABLE + FORCE RLS and
   a non-allow-all `tenant_isolation` policy, no DELETE grant, the exact column-limited UPDATE, the
   CHECK / foreign-key / trigger names the contract names -- each shown able to fail by editing the
   file on disk.
S  the store over a real SQLite session (only the Postgres tenant guard, the audit writer, the
   connection authorization and the byte-reading preview service are replaced; the upload store,
   the mapping grammar and the preview result types are the real ones): propose, version on change,
   every refusal with its stable code writing nothing, approve / reject, resolve.

Acceptance ("Import a real irregular CSV/XLSX/JSON source via UI; preserve Arabic/text types, reject
unmapped critical roles/tool IDs and deny training for evaluation-only grants"): ACC-PERSIST-*,
ACC-CRITICAL-*, ACC-GRANT-*. Negative acceptance ("No arbitrary Python/JS eval transformations;
preview/mapping ... does not silently harvest repo/log records"): NEG-DOCUMENT-*, NEG-CONTENT-*.

Run directly: python3 tests/tasks/test_ftf_h011_p2.py
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
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data.dataset_mapping_review import (  # noqa: E402
    MappingPreviewResult,
    MappingPreviewRow,
    MappingPreviewUnavailableError,
)

from app.datasets import dataset_mapping_store as store  # noqa: E402
from app.datasets import dataset_source_connection_crud as crud  # noqa: E402
from app.datasets import dataset_upload_store as ustore  # noqa: E402
from app.datasets.dataset_object_source import DatasetSourceInconsistentError  # noqa: E402
from app.db.tenant_context import TenantContext  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


class StrSub(str):
    """A str subclass: every exact-type check must refuse it."""


_ORG = uuid.UUID("a1111111-1111-4111-8111-111111111111")
_PROJECT = uuid.UUID("b2222222-2222-4222-8222-222222222222")
_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
_LATER = datetime(2026, 10, 21, 13, 30, tzinfo=UTC)
ALL_PURPOSES = ["training", "validation", "candidate_development", "held_out_evaluation"]


def det_uuid(label: str) -> uuid.UUID:
    raw = bytearray(hashlib.sha256(label.encode()).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


def msg_doc(
    role_map: dict[str, str] | None = None, policy: str = "reject", **extra: Any
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "shape": "messages",
        "messages_pointer": "/conversation",
        "role_key": "role",
        "content_key": "content",
        "role_map": role_map if role_map is not None else {"human": "user", "bot": "assistant"},
        "on_unmapped_message_keys": policy,
    }
    document.update(extra)
    return document


def mapping_of(document: dict[str, Any]) -> ic.ImportMapping:
    return ic.mapping_from_object(document)


# ======================================================================================
# P -- pure helpers
# ======================================================================================


def part_p() -> None:
    document = {"b": [1, "é"], "a": {"z": 1, "y": None}}
    expected = hashlib.sha256(
        b'{"a":{"y":null,"z":1},"b":[1,"\\u00e9"]}',
    ).hexdigest()
    check(
        "DIGEST-1",
        "canonical_sha256 is sha256 of sorted-key, space-free, ASCII JSON",
        store.canonical_sha256(document) == expected,
    )
    check(
        "DIGEST-2",
        "canonical_sha256 ignores key order",
        store.canonical_sha256({"a": 1, "b": 2}) == store.canonical_sha256({"b": 2, "a": 1}),
    )
    check(
        "DIGEST-3",
        "canonical_sha256 equals FTF-0502-P1's own mapping digest",
        store.canonical_sha256(msg_doc()) == mapping_of(msg_doc()).mapping_sha256,
    )
    check(
        "DIGEST-4",
        "ignored_keys_sha256 is the canonical digest of the sorted key list",
        store.ignored_keys_sha256(["b", "a"]) == hashlib.sha256(b'["a","b"]').hexdigest(),
    )
    check(
        "DIGEST-5",
        "ignored_keys_sha256 is order independent and set-sensitive",
        store.ignored_keys_sha256(["a", "b"]) == store.ignored_keys_sha256(["b", "a"])
        and store.ignored_keys_sha256(["a"]) != store.ignored_keys_sha256(["a", "b"])
        and store.ignored_keys_sha256([]) == hashlib.sha256(b"[]").hexdigest(),
    )

    for content_type, importer in (
        ("application/jsonl", "jsonl"),
        ("application/x-ndjson", "jsonl"),
        ("application/json", "json"),
        ("text/csv", "csv"),
    ):
        check(
            "IMPORTER-1",
            f"{content_type} selects the {importer} importer",
            store.importer_for_content_type(content_type) == importer,
        )
    for bad in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "TEXT/CSV",
        "APPLICATION/JSONL",
        "application/jsonl ",
        "Application/Json",
        "text/csv ",
        "",
        None,
        7,
        ["text/csv"],
    ):
        try:
            store.importer_for_content_type(bad)
            outcome = "no refusal"
        except store.MappingStoreError as exc:
            outcome = exc.code
        check(
            "IMPORTER-2",
            f"{bad!r} has no importer (importer_unavailable, got {outcome})",
            outcome == "importer_unavailable",
        )

    tool_keys = {"tool_calls_key": "calls", "tool_call_id_key": "call_id"}
    cases: list[tuple[str, dict[str, Any], str | None]] = [
        ("a user/assistant message mapping", msg_doc(), None),
        ("no user target", msg_doc({"bot": "assistant"}), "critical_role_unmapped"),
        ("no assistant target", msg_doc({"human": "user"}), "critical_role_unmapped"),
        (
            "only system and tool targets",
            msg_doc({"s": "system", "t": "tool"}, **tool_keys),
            "critical_role_unmapped",
        ),
        (
            "user and assistant plus system",
            msg_doc({"human": "user", "bot": "assistant", "s": "system"}),
            None,
        ),
        (
            "a tool target with neither key",
            msg_doc({"human": "user", "bot": "assistant", "t": "tool"}),
            "tool_id_unmapped",
        ),
        (
            "a tool target with only tool_calls_key",
            msg_doc({"human": "user", "bot": "assistant", "t": "tool"}, tool_calls_key="calls"),
            "tool_id_unmapped",
        ),
        (
            "a tool target with only tool_call_id_key",
            msg_doc({"human": "user", "bot": "assistant", "t": "tool"}, tool_call_id_key="call_id"),
            "tool_id_unmapped",
        ),
        ("tool_calls_key alone", msg_doc(tool_calls_key="calls"), "tool_id_unmapped"),
        ("tool_call_id_key alone is not a tool mapping", msg_doc(tool_call_id_key="call_id"), None),
        (
            "a tool target with both keys",
            msg_doc({"human": "user", "bot": "assistant", "t": "tool"}, **tool_keys),
            None,
        ),
        ("both keys without a tool target", msg_doc(**tool_keys), None),
        (
            "a name_key does not stand in for the ids",
            msg_doc({"human": "user", "bot": "assistant", "t": "tool"}, name_key="n"),
            "tool_id_unmapped",
        ),
        (
            "messages_json shape",
            {
                "shape": "messages_json",
                "messages": {"header": "m"},
                "role_key": "role",
                "content_key": "content",
                "role_map": {"human": "user"},
                "on_unmapped_message_keys": "reject",
            },
            "critical_role_unmapped",
        ),
        (
            "prompt_completion carries neither concern",
            {"shape": "prompt_completion", "prompt": {"key": "p"}, "completion": {"key": "c"}},
            None,
        ),
    ]
    for label, doc, code in cases:
        try:
            store.check_critical_mapping(mapping_of(doc))
            outcome = None
        except store.MappingStoreError as exc:
            outcome = exc.code
        check("CRITICAL-1", f"{label}: {code} (got {outcome})", outcome == code)

    part_ignored_keys()
    check("CONST-1", "the transform version is h011.1", store.TRANSFORM_VERSION == "h011.1")
    check(
        "CONST-2",
        "proposers are the admins and ml_engineer; approvers are admins only",
        store.PROPOSER_ROLES == frozenset({"workspace_admin", "organization_admin", "ml_engineer"}),
    )
    check(
        "CONST-3",
        "every code is a lowercase identifier",
        all(re.fullmatch(r"[a-z][a-z_]*", c) for c in store.CODES),
    )
    try:
        store.MappingStoreError("not_a_code")
        check("CONST-4", "an unknown code is refused at construction", False)
    except AssertionError:
        pass
    check(
        "CONST-5",
        "the error carries its code",
        store.MappingStoreError("mapping_invalid").code == "mapping_invalid"
        and str(store.MappingStoreError("mapping_invalid")) == "mapping_invalid",
    )


def preview_rows(*raw_rows: Any) -> MappingPreviewResult:
    rows = tuple(
        MappingPreviewRow(
            locator=f"record:{i + 1}",
            outcome="accepted",
            code=None,
            slot=None,
            raw_row=raw,
            not_mapped=(),
            candidate={"record_sha256": sha(f"r{i}"), "normalized_sha256": sha(f"n{i}")},
            comparison=(),
        )
        for i, raw in enumerate(raw_rows)
    )
    return MappingPreviewResult(
        state="completed",
        failure_code=None,
        rows=rows,
        observed_role_values=(),
        counts={"seen": len(rows), "accepted": len(rows), "rejected": 0},
        complete=True,
        tool_definitions_sha256=sha("tools"),
        source_schema_sha256=None,
        preview_sha256=sha("preview"),
    )


def part_ignored_keys() -> None:
    ignore = mapping_of(msg_doc(policy="ignore"))
    rows = preview_rows(
        {
            "conversation": [
                {"role": "human", "content": "a", "weight": 1},
                {"role": "bot", "content": "b", "zeta": 2},
            ]
        },
        {"conversation": [{"role": "human", "content": "c", "weight": 3, "alpha": 1}]},
    )
    check(
        "IGNORED-1",
        "keys outside the mapped message keys are reported, sorted and unique",
        store.ignored_message_keys(ignore, rows) == ("alpha", "weight", "zeta"),
    )
    reject = mapping_of(msg_doc(policy="reject"))
    check(
        "IGNORED-2",
        "a reject-policy mapping ignores nothing",
        store.ignored_message_keys(reject, rows) == (),
    )
    pc = mapping_of(
        {"shape": "prompt_completion", "prompt": {"key": "p"}, "completion": {"key": "c"}}
    )
    check(
        "IGNORED-3",
        "a prompt_completion mapping has no message keys",
        store.ignored_message_keys(pc, preview_rows({"p": 1, "c": 2})) == (),
    )
    full = mapping_of(
        msg_doc(policy="ignore", tool_calls_key="calls", tool_call_id_key="cid", name_key="nm")
    )
    known = preview_rows(
        {"conversation": [{"role": "r", "content": "c", "calls": 1, "cid": 2, "nm": 3, "extra": 4}]}
    )
    check(
        "IGNORED-4",
        "role, content, tool_calls, tool_call_id and name keys are never ignored keys",
        store.ignored_message_keys(full, known) == ("extra",),
    )
    bad_shapes = preview_rows(
        {"conversation": "not a list"},
        {"conversation": ["text", 5, None, {"role": "u", "content": "x", "only": 1}]},
        ("tuple", "row"),
        None,
        {"other": []},
    )
    check(
        "IGNORED-5",
        "non-list arrays, non-object messages, tuple rows and missing pointers are skipped, not crashed on",
        store.ignored_message_keys(ignore, bad_shapes) == ("only",),
    )
    json_doc = {
        "shape": "messages_json",
        "messages": {"header": "m"},
        "role_key": "role",
        "content_key": "content",
        "role_map": {"human": "user"},
        "on_unmapped_message_keys": "ignore",
    }
    json_mapping = mapping_of(json_doc)
    cell = json.dumps(
        [{"role": "human", "content": "x", "k1": 1}, {"role": "human", "content": "y", "k2": 2}]
    )
    check(
        "IGNORED-6",
        "a messages_json cell is parsed strictly and its extra keys are reported",
        store.ignored_message_keys(json_mapping, preview_rows({"m": cell})) == ("k1", "k2"),
    )
    check(
        "IGNORED-7",
        "a messages_json cell that is not JSON is skipped",
        store.ignored_message_keys(json_mapping, preview_rows({"m": "{not json"})) == (),
    )
    check(
        "IGNORED-8",
        "a messages_json cell that is a JSON object (not a list) is skipped",
        store.ignored_message_keys(json_mapping, preview_rows({"m": '{"role": "u", "k": 1}'}))
        == (),
    )
    check(
        "IGNORED-9",
        "a messages_json cell given already parsed is read as is",
        store.ignored_message_keys(
            json_mapping, preview_rows({"m": [{"role": "u", "content": "x", "k3": 1}]})
        )
        == ("k3",),
    )
    check(
        "IGNORED-10",
        "a messages_json mapping over a row without its header finds nothing",
        store.ignored_message_keys(json_mapping, preview_rows({"other": cell})) == (),
    )
    index_doc = dict(json_doc, messages={"index": 1})
    index_mapping = mapping_of(index_doc)
    check(
        "IGNORED-11",
        "an index slot reads a tuple row",
        store.ignored_message_keys(index_mapping, preview_rows(("x", cell))) == ("k1", "k2"),
    )
    check(
        "IGNORED-12",
        "an index slot beyond the row finds nothing",
        store.ignored_message_keys(
            mapping_of(dict(json_doc, messages={"index": 5})), preview_rows(("x", cell))
        )
        == (),
    )
    check(
        "IGNORED-13",
        "an index slot over a keyed row finds nothing",
        store.ignored_message_keys(index_mapping, preview_rows({"1": cell})) == (),
    )
    check(
        "IGNORED-14",
        "a header slot over a tuple row finds nothing",
        store.ignored_message_keys(json_mapping, preview_rows((cell,))) == (),
    )
    check(
        "IGNORED-15",
        "a negative index is not an index",
        store.ignored_message_keys(
            mapping_of(dict(json_doc, messages={"index": 0})), preview_rows((cell,))
        )
        == ("k1", "k2"),
    )


# ======================================================================================
# M -- migration 0035
# ======================================================================================

MIGRATION_PATH = ROOT / "apps" / "api" / "alembic" / "versions" / "0035_dataset_mappings.py"
EXPECTED_UPDATABLE = {
    "status",
    "approved_purpose",
    "approved_by",
    "approver_role",
    "approved_at",
    "ignored_keys_sha256",
    "updated_at",
}
CONSTRAINTS = (
    "fk_dataset_mappings_dataset_lineage",
    "fk_dataset_mappings_connection",
    "fk_dataset_mappings_parent",
    "uq_dataset_mappings_dataset_version",
    "ck_dataset_mappings_version_min",
    "ck_dataset_mappings_first_has_no_parent",
    "ck_dataset_mappings_parent_is_previous",
    "ck_dataset_mappings_status",
    "ck_dataset_mappings_importer",
    "ck_dataset_mappings_document",
    "ck_dataset_mappings_preview_counts",
    "ck_dataset_mappings_hashes",
    "ck_dataset_mappings_allowed_purposes_snapshot",
    "ck_dataset_mappings_approval_columns",
    "ck_dataset_mappings_approved_purpose_is_dataset_purpose",
    "ck_dataset_mappings_approved_purpose_in_grant",
    "ck_dataset_mappings_approver_role",
    "ck_dataset_mappings_ignored_keys_acknowledged",
)


def migration_findings(src: str) -> list[str]:
    """Every shape rule the migration must satisfy; returns the broken ones."""
    broken: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            broken.append(label)

    need("revision id", 'revision = "0035_dataset_mappings"' in src)
    need("chained onto 0034", 'down_revision = "0034_argilla_review_identity"' in src)
    need("revision id fits alembic_version (<=32)", len("0035_dataset_mappings") <= 32)
    need("ENABLE rls", '"ALTER TABLE dataset_mappings ENABLE ROW LEVEL SECURITY"' in src)
    need("FORCE rls", '"ALTER TABLE dataset_mappings FORCE ROW LEVEL SECURITY"' in src)
    need("policy name", "CREATE POLICY tenant_isolation ON dataset_mappings FOR ALL" in src)
    need("policy using", "USING (organization_id = {tenant_setting})" in src)
    need("policy with check", "WITH CHECK (organization_id = {tenant_setting})" in src)
    need("not allow-all", "USING (true)" not in src and "WITH CHECK (true)" not in src)
    need(
        "tenant setting",
        "NULLIF(current_setting('app.current_organization_id', true), '')::uuid" in src,
    )
    need(
        "SELECT/INSERT grant only",
        'GRANT SELECT, INSERT ON dataset_mappings TO "{APP_ROLE_NAME}"' in src,
    )
    need(
        "no DELETE grant",
        "DELETE ON" not in src.split("def downgrade")[0] and "GRANT DELETE" not in src,
    )
    need(
        "column-limited UPDATE",
        'GRANT UPDATE ({", ".join(_UPDATABLE)}) ON dataset_mappings' in src
        or "GRANT UPDATE ({', '.join(_UPDATABLE)}) ON dataset_mappings" in src,
    )
    match = re.search(r"_UPDATABLE = \(([^)]*)\)", src)
    need(
        "the UPDATE columns are exactly the approval columns",
        match is not None and set(re.findall(r'"(\w+)"', match.group(1))) == EXPECTED_UPDATABLE,
    )
    for constraint in CONSTRAINTS:
        need(f"constraint {constraint}", f'"{constraint}"' in src)
    need("hash CHECK is lowercase hex64", "_HEX64 = \"'^[0-9a-f]{64}$'\"" in src)
    need(
        "every hash column is checked",
        all(
            f"{col} ~ {{_HEX64}}" in src
            for col in (
                "dataset_sha256",
                "mapping_sha256",
                "tool_definitions_sha256",
                "preview_sha256",
                "source_schema_sha256",
                "ignored_keys_sha256",
            )
        ),
    )
    need(
        "purpose equals dataset purpose",
        "approved_purpose IS NULL OR approved_purpose = dataset_purpose" in src,
    )
    need(
        "purpose within the frozen grant",
        "approved_purpose IS NULL OR approved_purpose = ANY (allowed_purposes_snapshot)" in src,
    )
    need(
        "approved iff all approval columns",
        "(status = 'approved') = (approved_purpose IS NOT NULL AND approved_by IS NOT NULL" in src,
    )
    need(
        "approver is an admin role",
        '_ADMIN_ROLES = ("workspace_admin", "organization_admin")' in src,
    )
    need(
        "ignored keys only for approved ignore mappings",
        "COALESCE(mapping_document ->> " in src
        and "'on_unmapped_message_keys', '') = 'ignore') = (ignored_keys_sha256 IS NOT NULL)"
        in src,
    )
    need(
        "parent is the previous version",
        "parent_mapping_version IS NULL OR parent_mapping_version = mapping_version - 1" in src,
    )
    need(
        "first version has no parent",
        "(mapping_version = 1) = (parent_mapping_version IS NULL)" in src,
    )
    need(
        "dataset lineage fk is RESTRICT",
        'ondelete="RESTRICT",\n            name="fk_dataset_mappings_dataset_lineage"' in src,
    )
    need(
        "connection fk is RESTRICT",
        'ondelete="RESTRICT",\n            name="fk_dataset_mappings_connection"' in src,
    )
    need(
        "parent fk is RESTRICT",
        'ondelete="RESTRICT",\n            name="fk_dataset_mappings_parent"' in src,
    )
    need(
        "lineage fk targets the datasets lineage columns",
        all(
            f'"datasets.{c}"' in src
            for c in (
                "organization_id",
                "project_id",
                "id",
                "name",
                "purpose",
                "version",
                "verified_sha256",
            )
        ),
    )
    need("insert guard refuses non-proposed", "IF NEW.status <> 'proposed' THEN" in src)
    need("update guard: one transition only", "IF OLD.status <> 'proposed' THEN" in src)
    need(
        "update guard: approved or rejected only",
        "IF NEW.status NOT IN ('approved', 'rejected') THEN" in src,
    )
    need(
        "update guard: frozen columns by to_jsonb diff",
        "IF (to_jsonb(NEW) - {_FROZEN}) IS DISTINCT FROM (to_jsonb(OLD) - {_FROZEN}) THEN" in src,
    )
    need(
        "update guard: a rejection records no acknowledgement",
        "NEW.status = 'rejected' AND NEW.ignored_keys_sha256 IS DISTINCT FROM" in src,
    )
    need(
        "update guard: a superseded mapping is never approved",
        "m.mapping_version > NEW.mapping_version) THEN" in src
        and "IF NEW.status = 'approved' AND EXISTS" in src,
    )
    need(
        "the insert guard binds the connection to the dataset's upload connection",
        "IF grant_connection IS DISTINCT FROM NEW.dataset_source_connection_id THEN" in src
        and "JOIN dataset_upload_sessions u ON u.id = d.upload_session_id" in src,
    )
    need(
        "the insert guard binds the snapshot to that connection's grant",
        "jsonb_array_elements_text(grant_json)" in src
        and "unnest(NEW.allowed_purposes_snapshot)" in src
        and "IS DISTINCT FROM (SELECT array_agg(p ORDER BY p)" in src,
    )
    need(
        "the table name is a literal (data_exposure_check reads it)",
        'op.create_table(\n        "dataset_mappings",' in src and "_TABLE" not in src,
    )
    need(
        "both triggers are created",
        "CREATE TRIGGER tr_dataset_mappings_insert_guard BEFORE INSERT" in src
        and "CREATE TRIGGER tr_dataset_mappings_update_guard BEFORE UPDATE" in src,
    )
    need(
        "counts function is content-free",
        "e.k !~ '^(seen|accepted|rejected|rejected:[a-z][a-z0-9_]*)$'" in src
        and "jsonb_typeof(e.v) <> 'number'" in src
        and "e.v::text !~ '^[0-9]{1,9}$'" in src
        and "c ?& ARRAY['seen','accepted','rejected']" in src,
    )
    need(
        "counts function is used by a CHECK",
        "dataset_mapping_counts_content_free(preview_counts)" in src,
    )
    need("the document is bounded", "octet_length(mapping_document::text) <= 65536" in src)
    need("importers are csv, json, jsonl", '_IMPORTERS = ("csv", "json", "jsonl")' in src)
    need("statuses", '_STATUSES = ("proposed", "approved", "rejected")' in src)
    need(
        "grant snapshot is a known non-empty purpose set",
        "cardinality(allowed_purposes_snapshot) >= 1" in src
        and "allowed_purposes_snapshot <@ {_array(_ALL_PURPOSES)}" in src,
    )
    need(
        "downgrade drops the table, triggers, policy and functions",
        all(
            s in src.split("def downgrade")[1]
            for s in (
                "DROP TRIGGER IF EXISTS tr_dataset_mappings_update_guard",
                "DROP FUNCTION IF EXISTS dataset_mappings_update_guard()",
                "DROP POLICY IF EXISTS tenant_isolation",
                'op.drop_table("dataset_mappings")',
                "dataset_mapping_counts_content_free(jsonb)",
            )
        ),
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
        "drop FORCE RLS": ('"ALTER TABLE dataset_mappings FORCE ROW LEVEL SECURITY"', '"SELECT 1"'),
        "drop ENABLE RLS": (
            '"ALTER TABLE dataset_mappings ENABLE ROW LEVEL SECURITY"',
            '"SELECT 1"',
        ),
        "allow-all policy": ("USING (organization_id = {tenant_setting})", "USING (true)"),
        "allow-all check": ("WITH CHECK (organization_id = {tenant_setting})", "WITH CHECK (true)"),
        "grant DELETE": (
            'GRANT SELECT, INSERT ON dataset_mappings TO "{APP_ROLE_NAME}"',
            'GRANT SELECT, INSERT, DELETE ON dataset_mappings TO "{APP_ROLE_NAME}"',
        ),
        "widen the UPDATE columns": (
            '    "status",\n    "approved_purpose",',
            '    "status",\n    "mapping_sha256",\n    "approved_purpose",',
        ),
        "drop the purpose-equality CHECK": (
            "approved_purpose IS NULL OR approved_purpose = dataset_purpose",
            "true",
        ),
        "drop the frozen-grant CHECK": (
            "approved_purpose IS NULL OR approved_purpose = ANY (allowed_purposes_snapshot)",
            "true",
        ),
        "weaken the approval-columns CHECK": (
            "(status = 'approved') = (approved_purpose IS NOT NULL AND approved_by IS NOT NULL",
            "(status = 'approved') OR (approved_purpose IS NOT NULL AND approved_by IS NOT NULL",
        ),
        "let any role approve": (
            '_ADMIN_ROLES = ("workspace_admin", "organization_admin")',
            '_ADMIN_ROLES = ("workspace_admin", "organization_admin", "ml_engineer")',
        ),
        "drop the one-transition rule": ("IF OLD.status <> 'proposed' THEN", "IF false THEN"),
        "allow any new status": (
            "IF NEW.status NOT IN ('approved', 'rejected') THEN",
            "IF false THEN",
        ),
        "drop the frozen-column guard": (
            "IF (to_jsonb(NEW) - {_FROZEN}) IS DISTINCT FROM (to_jsonb(OLD) - {_FROZEN}) THEN",
            "IF (to_jsonb(OLD) - {_FROZEN}) IS DISTINCT FROM (to_jsonb(OLD) - {_FROZEN}) THEN",
        ),
        "approve a superseded mapping": (
            "m.mapping_version > NEW.mapping_version",
            "m.mapping_version > NEW.mapping_version + 1000",
        ),
        "insert as approved": ("IF NEW.status <> 'proposed' THEN", "IF false THEN"),
        "allow a content-like counts key": (
            "e.k !~ '^(seen|accepted|rejected|rejected:[a-z][a-z0-9_]*)$'",
            "false",
        ),
        "allow non-number counts": ("jsonb_typeof(e.v) <> 'number'", "false"),
        "cascade the dataset lineage fk": (
            'ondelete="RESTRICT",\n            name="fk_dataset_mappings_dataset_lineage"',
            'ondelete="CASCADE",\n            name="fk_dataset_mappings_dataset_lineage"',
        ),
        "drop the parent-is-previous CHECK": (
            "parent_mapping_version IS NULL OR parent_mapping_version = mapping_version - 1",
            "true",
        ),
        "drop the first-has-no-parent CHECK": (
            "(mapping_version = 1) = (parent_mapping_version IS NULL)",
            "true",
        ),
        "let a hash column go unchecked": (
            "AND (ignored_keys_sha256 IS NULL OR ignored_keys_sha256 ~ {_HEX64})",
            "",
        ),
        "ignored keys without an ignore mapping": (
            "'on_unmapped_message_keys', '') = 'ignore') = (ignored_keys_sha256 IS NOT NULL)",
            "'on_unmapped_message_keys', '') = 'ignore') OR (ignored_keys_sha256 IS NOT NULL)",
        ),
        "forged connection allowed": (
            "IF grant_connection IS DISTINCT FROM NEW.dataset_source_connection_id THEN",
            "IF false THEN",
        ),
        "forged snapshot allowed": (
            "IS DISTINCT FROM (SELECT array_agg(p ORDER BY p)",
            "IS NOT DISTINCT FROM (SELECT array_agg(p ORDER BY p)",
        ),
        "table name hidden behind a variable": (
            'op.create_table(\n        "dataset_mappings",',
            "op.create_table(\n        TABLE_NAME,",
        ),
        "wrong parent revision": (
            'down_revision = "0034_argilla_review_identity"',
            'down_revision = "0033_upload_declared_encoding"',
        ),
        "empty grant snapshot allowed": ("cardinality(allowed_purposes_snapshot) >= 1", "true"),
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
    up = offline_sql("upgrade", "0034_argilla_review_identity:0035_dataset_mappings")
    for needle in (
        "CREATE TABLE dataset_mappings",
        "ALTER TABLE dataset_mappings ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE dataset_mappings FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON dataset_mappings FOR ALL",
        'GRANT SELECT, INSERT ON dataset_mappings TO "sovra_app"',
        'GRANT UPDATE (status, approved_purpose, approved_by, approver_role, approved_at, ignored_keys_sha256, updated_at) ON dataset_mappings TO "sovra_app"',
        "CREATE TRIGGER tr_dataset_mappings_insert_guard BEFORE INSERT ON dataset_mappings",
        "CREATE TRIGGER tr_dataset_mappings_update_guard BEFORE UPDATE ON dataset_mappings",
        "CREATE FUNCTION dataset_mapping_counts_content_free",
        "UPDATE alembic_version SET version_num='0035_dataset_mappings'",
    ):
        check("LAW18-MIGRATION", f"the upgrade SQL contains: {needle[:90]}", needle in up)
    for column in [
        "id UUID DEFAULT gen_random_uuid() NOT NULL",
        "organization_id UUID NOT NULL",
        "project_id UUID NOT NULL",
        "dataset_id UUID NOT NULL",
        "dataset_name VARCHAR(200) NOT NULL",
        "dataset_purpose VARCHAR(32) NOT NULL",
        "dataset_version INTEGER NOT NULL",
        "dataset_sha256 VARCHAR(64) NOT NULL",
        "dataset_source_connection_id UUID NOT NULL",
        "mapping_version INTEGER NOT NULL",
        "parent_mapping_version INTEGER,",
        "mapping_document JSONB NOT NULL",
        "mapping_sha256 VARCHAR(64) NOT NULL",
        "importer VARCHAR(16) NOT NULL",
        "importer_version VARCHAR(16) NOT NULL",
        "transform_version VARCHAR(16) NOT NULL",
        "source_schema_sha256 VARCHAR(64),",
        "tool_definitions_sha256 VARCHAR(64) NOT NULL",
        "preview_sha256 VARCHAR(64) NOT NULL",
        "preview_counts JSONB NOT NULL",
        "ignored_keys_sha256 VARCHAR(64),",
        "status VARCHAR(16) DEFAULT 'proposed' NOT NULL",
        "allowed_purposes_snapshot TEXT[] NOT NULL",
        "approved_purpose VARCHAR(32),",
        "approved_by VARCHAR(200),",
        "approver_role VARCHAR(32),",
        "approved_at TIMESTAMP WITH TIME ZONE,",
        "created_by VARCHAR(200) NOT NULL",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "CHECK (status IN ('proposed','approved','rejected'))",
        "CHECK (importer IN ('csv','json','jsonl'))",
        "ARRAY['training','validation','candidate_development','held_out_evaluation','baseline_only']::text[]",
    ]:
        check("LAW18-MIGRATION", f"the upgrade SQL pins: {column}", column in up)
    for forbidden in (
        "GRANT DELETE",
        "DELETE ON dataset_mappings",
        "USING (true)",
        "WITH CHECK (true)",
    ):
        check(
            "LAW18-MIGRATION", f"the upgrade SQL never contains: {forbidden}", forbidden not in up
        )
    down = offline_sql("downgrade", "0035_dataset_mappings:0034_argilla_review_identity")
    for needle in (
        "DROP TRIGGER IF EXISTS tr_dataset_mappings_update_guard ON dataset_mappings",
        "DROP FUNCTION IF EXISTS dataset_mappings_update_guard()",
        "DROP POLICY IF EXISTS tenant_isolation ON dataset_mappings",
        "DROP TABLE dataset_mappings",
        "DROP FUNCTION IF EXISTS dataset_mapping_counts_content_free(jsonb)",
        "UPDATE alembic_version SET version_num='0034_argilla_review_identity'",
    ):
        check("LAW18-MIGRATION", f"the downgrade SQL contains: {needle[:90]}", needle in down)
    heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env={**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"},
    )
    history = subprocess.run(
        [sys.executable, "-m", "alembic", "history"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env={**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"},
    )
    check(
        "LAW18-MIGRATION",
        f"exactly one head, with 0035 chained onto 0034: {heads.stdout!r}",
        heads.stdout.count("(head)") == 1
        and "0034_argilla_review_identity -> 0035_dataset_mappings" in history.stdout,
    )


# ======================================================================================
# S -- the store over SQLite
# ======================================================================================


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def actions(self) -> list[str]:
        return [e["action"] for e in self.events]


def passthrough(
    session: Session, organization_id: uuid.UUID, statement: Any, params: Any = None
) -> Any:
    return session.execute(statement) if params is None else session.execute(statement, params)


class World:
    """A SQLite session wired like the Postgres one, with the real upload store and mapping store."""

    def __init__(self, session: Session, audit: Audit) -> None:
        self.session = session
        self.audit = audit
        self.ctx = TenantContext(organization_id=_ORG)
        self.storage = SimpleNamespace(name="the-storage")
        self.connections: dict[uuid.UUID, Any] = {}
        self.preview_calls: list[dict[str, Any]] = []
        self.preview_seed = "p1"
        self.preview_error: BaseException | None = None
        self.preview_state = ("completed", None)
        self.preview_counts: dict[str, int] = {"seen": 2, "accepted": 2, "rejected": 0}
        self.preview_rows: Any = ()
        self.schema_sha: str | None = None
        self.tools_sha = sha("tools-1")
        self.before_preview: Any = None
        self.admin = store.MappingActor("user", "admin-1", "workspace_admin", "req-a")
        self.org_admin = store.MappingActor("user", "admin-2", "organization_admin", "req-b")
        self.ml = store.MappingActor("user", "ml-1", "ml_engineer", "req-m")

    def fake_preview(self, session: Session, context: Any, **kwargs: Any) -> MappingPreviewResult:
        self.preview_calls.append(kwargs)
        if self.before_preview is not None:
            hook, self.before_preview = self.before_preview, None
            hook()
        if self.preview_error is not None:
            raise self.preview_error
        state, failure = self.preview_state
        return MappingPreviewResult(
            state=state,
            failure_code=failure,
            rows=tuple(self.preview_rows),
            observed_role_values=(),
            counts=dict(self.preview_counts),
            complete=state == "completed",
            tool_definitions_sha256=self.tools_sha,
            source_schema_sha256=self.schema_sha,
            preview_sha256=sha(f"preview-{self.preview_seed}-{kwargs['mapping'].mapping_sha256}"),
        )

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
        conn: uuid.UUID,
        *,
        version: int = 1,
        content_type: str | None = "application/jsonl",
        tag: str = "",
    ) -> ustore.Dataset:
        dataset = ustore.Dataset(
            id=det_uuid(f"dataset-{name}-{version}-{purpose}{tag}"),
            organization_id=_ORG,
            project_id=_PROJECT,
            name=name,
            version=version,
            purpose=purpose,
            upload_session_id=det_uuid(f"upload-{name}-{version}-{purpose}{tag}"),
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
                object_key=f"k/{name}/{tag}",
                declared_size_bytes=100,
                max_bytes=100,
                declared_sha256=dataset.verified_sha256,
                declared_content_type=content_type or "application/jsonl",
                status="verified",
                verified_size_bytes=100,
                verified_sha256=dataset.verified_sha256,
                verified_content_type=content_type,
                created_by="seed",
                created_at=_NOW,
                expires_at=_NOW,
                updated_at=_NOW,
            )
        )
        self.session.flush()
        return dataset

    # -- calls --
    def propose(
        self, dataset: ustore.Dataset, document: Any = None, actor: Any = None, **kw: Any
    ) -> store.DatasetMapping:
        return store.propose_dataset_mapping(
            self.session,
            self.ctx,
            project_id=kw.pop("project_id", _PROJECT),
            dataset_id=kw.pop("dataset_id", dataset.id),
            mapping_document=msg_doc() if document is None else document,
            storage=self.storage,
            actor=actor or self.admin,
            **kw,
        )

    def approve(self, mapping: store.DatasetMapping, **kw: Any) -> store.DatasetMapping:
        args: dict[str, Any] = {
            "project_id": _PROJECT,
            "mapping_id": mapping.id,
            "expected_preview_sha256": mapping.preview_sha256,
            "purpose": mapping.dataset_purpose,
            "acknowledged_ignored_keys": [],
            "storage": self.storage,
            "actor": self.admin,
        }
        args.update(kw)
        return store.approve_dataset_mapping(self.session, self.ctx, **args)

    def reject(self, mapping: store.DatasetMapping, **kw: Any) -> store.DatasetMapping:
        args: dict[str, Any] = {
            "project_id": _PROJECT,
            "mapping_id": mapping.id,
            "actor": self.admin,
        }
        args.update(kw)
        return store.reject_dataset_mapping(self.session, self.ctx, **args)

    def resolve(self, dataset: ustore.Dataset, **kw: Any) -> store.ResolvedMapping:
        args: dict[str, Any] = {
            "project_id": _PROJECT,
            "dataset_id": dataset.id,
            "purpose": dataset.purpose,
            "storage": self.storage,
            "include_held_out": True,
        }
        args.update(kw)
        return store.resolve_approved_mapping(self.session, self.ctx, **args)

    def count(self) -> int:
        return self.session.query(store.DatasetMapping).count()


@contextlib.contextmanager
def sqlite_world() -> Any:
    engine = create_engine("sqlite://")
    for model in (ustore.Dataset, ustore.DatasetUploadSession, store.DatasetMapping):
        model.__table__.create(engine)
    audit = Audit()
    saved = (
        store.execute_tenant_scoped,
        ustore.execute_tenant_scoped,
        store.write_audit_event,
        store.authorize_dataset_source_purpose,
        store.preview_dataset_mapping,
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
        ustore.execute_tenant_scoped = passthrough
        store.write_audit_event = audit
        store.authorize_dataset_source_purpose = authorize
        store.preview_dataset_mapping = world.fake_preview
        try:
            yield world
        finally:
            (
                store.execute_tenant_scoped,
                ustore.execute_tenant_scoped,
                store.write_audit_event,
                store.authorize_dataset_source_purpose,
                store.preview_dataset_mapping,
            ) = saved
    engine.dispose()


def code_of(fn: Any) -> str | None:
    """The stable code a store call refuses with; None if it did not refuse."""
    try:
        fn()
    except store.MappingStoreError as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return None


def refuses(world: World, label: str, code: str, fn: Any, criterion: str = "ACC-REFUSE") -> None:
    """`fn` refuses with exactly `code` and writes no row, no audit event and leaves the session clean."""
    rows, events = world.count(), len(world.audit.events)
    got = code_of(fn)
    check(criterion, f"{label}: refused with {code} (got {got})", got == code)
    check(criterion, f"{label}: wrote no row", world.count() == rows)
    check(criterion, f"{label}: wrote no audit event", len(world.audit.events) == events)


def ignore_rows() -> tuple[MappingPreviewRow, ...]:
    return preview_rows(
        {"conversation": [{"role": "human", "content": "a", "weight": 1, "zeta": 2}]}
    ).rows


def part_s_propose() -> None:
    with sqlite_world() as w:
        conn = w.connection("all", ALL_PURPOSES)
        ds = w.dataset("alpha", "training", conn)
        w.schema_sha = sha("header")
        rows_before = w.count()
        m = w.propose(ds, now=_NOW)
        check(
            "ACC-PERSIST-1",
            "a proposal is stored as version 1, no parent, status proposed",
            (m.mapping_version, m.parent_mapping_version, m.status) == (1, None, "proposed")
            and w.count() == rows_before + 1,
        )
        check(
            "ACC-PERSIST-2",
            "the dataset identity is copied from the dataset row",
            (
                m.organization_id,
                m.project_id,
                m.dataset_id,
                m.dataset_name,
                m.dataset_purpose,
                m.dataset_version,
                m.dataset_sha256,
            )
            == (_ORG, _PROJECT, ds.id, "alpha", "training", 1, ds.verified_sha256),
        )
        check(
            "ACC-PERSIST-3",
            "the connection is the dataset's upload connection and the snapshot is its grant now",
            m.dataset_source_connection_id == conn
            and m.allowed_purposes_snapshot == sorted(ALL_PURPOSES),
        )
        check(
            "ACC-PERSIST-4",
            "the document is stored verbatim and hashed by the certified digest",
            m.mapping_document == msg_doc()
            and m.mapping_sha256 == mapping_of(msg_doc()).mapping_sha256,
        )
        check(
            "ACC-PERSIST-5",
            "importer, importer version and transform version",
            (m.importer, m.importer_version, m.transform_version) == ("jsonl", "1", "h011.1"),
        )
        check(
            "ACC-PERSIST-6",
            "the preview hashes, header hash and counts are the preview's",
            (m.preview_sha256, m.source_schema_sha256, m.tool_definitions_sha256)
            == (sha(f"preview-p1-{m.mapping_sha256}"), sha("header"), sha("tools-1"))
            and m.preview_counts == {"seen": 2, "accepted": 2, "rejected": 0},
        )
        check(
            "ACC-PERSIST-7",
            "every approval column is empty and nothing is acknowledged",
            (
                m.approved_purpose,
                m.approved_by,
                m.approver_role,
                m.approved_at,
                m.ignored_keys_sha256,
            )
            == (None, None, None, None, None),
        )
        check(
            "ACC-PERSIST-8",
            "created_by is the actor and the timestamps are the supplied instant",
            m.created_by == "admin-1" and m.created_at == _NOW and m.updated_at == _NOW,
        )
        check(
            "ACC-PERSIST-9",
            "the id is a fresh uuid in the caller's organization",
            isinstance(m.id, uuid.UUID) and m.organization_id == _ORG,
        )
        call = w.preview_calls[-1]
        check(
            "ACC-PERSIST-10",
            "the real preview ran once with the caller's storage, the dataset, reveal=False and held-out visible to an admin",
            len(w.preview_calls) == 1
            and call["storage"] is w.storage
            and call["dataset_id"] == ds.id
            and call["project_id"] == _PROJECT
            and call["reveal"] is False
            and call["include_held_out"] is True
            and call["mapping"].mapping_sha256 == m.mapping_sha256,
        )
        check(
            "ACC-PERSIST-11",
            "one content-free dataset_mapping.proposed audit event for the actor",
            w.audit.actions() == ["dataset_mapping.proposed"],
        )
        event = w.audit.events[0]
        check(
            "ACC-PERSIST-12",
            "the audit event names the resource, actor and request",
            (
                event["organization_id"],
                event["actor_type"],
                event["actor_id"],
                event["resource_type"],
                event["resource_id"],
                event["request_id"],
            )
            == (_ORG, "user", "admin-1", "dataset_mapping", str(m.id), "req-a"),
        )
        check(
            "NEG-CONTENT-1",
            "the audit details are exactly ids, versions, hashes and counts",
            event["details"]
            == {
                "project_id": str(_PROJECT),
                "dataset_id": str(ds.id),
                "mapping_version": 1,
                "mapping_sha256": m.mapping_sha256,
                "preview_sha256": m.preview_sha256,
                "importer": "jsonl",
                "status": "proposed",
                "seen": 2,
                "accepted": 2,
                "rejected": 0,
            },
        )

        # unchanged re-proposal
        events = len(w.audit.events)
        again = w.propose(ds)
        check(
            "ACC-VERSION-1",
            "an unchanged re-proposal returns the latest row and writes nothing",
            again.id == m.id and w.count() == rows_before + 1 and len(w.audit.events) == events,
        )
        # each field of the unchanged test, one at a time, makes a new version
        variants: list[tuple[str, Any]] = [
            (
                "the mapping",
                lambda: w.propose(
                    ds, msg_doc({"human": "user", "bot": "assistant", "s": "system"})
                ),
            ),
        ]
        for label, make in variants:
            made = make()
            check(
                "ACC-VERSION-2",
                f"a changed {label} is the next version with the previous as parent",
                made.mapping_version == made.parent_mapping_version + 1,
            )
        latest = w.session.query(store.DatasetMapping).filter_by(mapping_version=2).one()
        check(
            "ACC-VERSION-3",
            "version 2 has parent 1",
            latest.parent_mapping_version == 1 and latest.mapping_version == 2,
        )

    drift_cases = {
        "the preview hash": lambda w: setattr(w, "preview_seed", "p2"),
        "the header hash": lambda w: setattr(w, "schema_sha", sha("other header")),
        "the tool definitions": lambda w: setattr(w, "tools_sha", sha("tools-2")),
        "the connection grant": lambda w: w.connections[
            det_uuid("conn-all")
        ].allowed_purposes_json.remove("validation"),
    }
    for label, mutate in drift_cases.items():
        with sqlite_world() as w:
            conn = w.connection("all", ALL_PURPOSES)
            ds = w.dataset("alpha", "training", conn)
            first = w.propose(ds)
            mutate(w)
            second = w.propose(ds)
            check(
                "ACC-VERSION-4",
                f"a changed {label} is a new version, not a silent reuse",
                second.id != first.id
                and second.mapping_version == 2
                and second.parent_mapping_version == 1,
            )
            third = w.propose(ds)
            check(
                "ACC-VERSION-5",
                f"after a change in {label}, re-proposing the same thing returns version 2",
                third.id == second.id,
            )

    with sqlite_world() as w:
        conn = w.connection("all", ALL_PURPOSES)
        ds = w.dataset("alpha", "training", conn)
        first = w.propose(ds)
        w.reject(first)
        second = w.propose(ds)
        check(
            "ACC-VERSION-6",
            "an identical re-proposal after a rejection is a new version",
            second.id != first.id
            and (second.mapping_version, second.parent_mapping_version) == (2, 1),
        )
        w.approve(second)
        third = w.propose(ds)
        check(
            "ACC-VERSION-7",
            "an identical re-proposal after an approval returns the approved row",
            third.id == second.id and third.status == "approved",
        )

    with sqlite_world() as w:
        conn = w.connection("all", ALL_PURPOSES)
        ds = w.dataset("alpha", "training", conn)
        w.propose(ds)
        w.connections[conn].allowed_purposes_json.append("baseline_only")
        check(
            "ACC-VERSION-8",
            "a grant that only widened is also a change",
            w.propose(ds).mapping_version == 2,
        )

    part_s_propose_refusals()


def part_s_propose_refusals() -> None:
    with sqlite_world() as w:
        conn = w.connection("all", ALL_PURPOSES)
        ds = w.dataset("alpha", "training", conn)
        held = w.dataset("held", "held_out_evaluation", conn)
        csv = w.dataset("tab", "training", conn, content_type="text/csv")

        def pr(document: Any = None, actor: Any = None, dataset: Any = ds, **kw: Any) -> Any:
            return lambda: w.propose(dataset, document, actor, **kw)

        refuses(w, "a list document", "mapping_invalid", pr(["x"]))
        refuses(w, "a string document", "mapping_invalid", pr("shape"))
        refuses(
            w,
            "a dict subclass is still a dict but a str subclass shape is not",
            "mapping_invalid",
            pr({"shape": StrSub("messages")}),
        )
        refuses(
            w,
            "an unknown key (eval-like value)",
            "mapping_invalid",
            pr(msg_doc(expr="lambda x: x")),
        )
        refuses(w, "a missing required key", "mapping_invalid", pr({"shape": "messages"}))
        refuses(w, "grouped_rows", "mapping_unsupported", pr({"shape": "grouped_rows"}))
        refuses(w, "grouped_multi_row", "mapping_unsupported", pr({"shape": "grouped_multi_row"}))
        refuses(
            w,
            "no user target",
            "critical_role_unmapped",
            pr(msg_doc({"bot": "assistant"})),
            "ACC-CRITICAL-1",
        )
        refuses(
            w,
            "no assistant target",
            "critical_role_unmapped",
            pr(msg_doc({"human": "user"})),
            "ACC-CRITICAL-2",
        )
        refuses(
            w,
            "a tool target without ids",
            "tool_id_unmapped",
            pr(msg_doc({"human": "user", "bot": "assistant", "t": "tool"})),
            "ACC-CRITICAL-3",
        )
        refuses(
            w,
            "tool_calls_key alone",
            "tool_id_unmapped",
            pr(msg_doc(tool_calls_key="calls")),
            "ACC-CRITICAL-4",
        )
        check(
            "ACC-CRITICAL-5", "no preview ran for any document-level refusal", w.preview_calls == []
        )
        check(
            "ACC-CRITICAL-6",
            "positive control: a tool mapping with both ids proposes",
            w.propose(
                ds,
                msg_doc(
                    {"human": "user", "bot": "assistant", "t": "tool"},
                    tool_calls_key="c",
                    tool_call_id_key="i",
                ),
            ).mapping_version
            == 1,
        )
        check(
            "ACC-CRITICAL-7",
            "positive control: a CSV prompt/completion mapping proposes",
            w.propose(
                csv,
                {
                    "shape": "prompt_completion",
                    "csv": {
                        "encoding": "utf-8",
                        "delimiter": ",",
                        "has_header": True,
                        "header_row": 0,
                    },
                    "prompt": {"header": "q"},
                    "completion": {"header": "a"},
                },
            ).importer
            == "csv",
        )

        refuses(
            w,
            "a viewer",
            "actor_not_permitted",
            pr(actor=store.MappingActor("user", "v", "viewer", "r")),
        )
        refuses(w, "a non-actor", "invalid_request", pr(actor="admin"))
        refuses(
            w,
            "a blank actor id",
            "invalid_request",
            pr(actor=store.MappingActor("user", "", "workspace_admin", "r")),
        )
        refuses(
            w,
            "an actor id with outer whitespace",
            "invalid_request",
            pr(actor=store.MappingActor("user", " a", "workspace_admin", "r")),
        )
        refuses(
            w,
            "an actor type with a NUL",
            "invalid_request",
            pr(actor=store.MappingActor("us\x00er", "a", "workspace_admin", "r")),
        )
        refuses(
            w,
            "an over-long actor id",
            "invalid_request",
            pr(actor=store.MappingActor("user", "a" * 201, "workspace_admin", "r")),
        )
        refuses(
            w,
            "a str-subclass request id",
            "invalid_request",
            pr(actor=store.MappingActor("user", "a", "workspace_admin", StrSub("r"))),
        )
        refuses(w, "a non-uuid project", "invalid_request", pr(project_id="p"))
        refuses(w, "a non-uuid dataset", "invalid_request", pr(dataset_id="d"))
        refuses(w, "an unknown dataset", "dataset_not_found", pr(dataset_id=uuid.uuid4()))
        refuses(w, "another project's id", "dataset_not_found", pr(project_id=uuid.uuid4()))
        refuses(
            w,
            "ml_engineer on a held-out dataset",
            "dataset_not_found",
            pr(actor=w.ml, dataset=held),
            "ACC-GRANT-1",
        )
        check(
            "ACC-GRANT-2",
            "an admin proposes a held-out dataset, seeing it (include_held_out)",
            w.propose(held).dataset_purpose == "held_out_evaluation"
            and w.preview_calls[-1]["include_held_out"] is True,
        )
        ml_call = w.propose(ds, msg_doc({"human": "user", "bot": "assistant", "s": "system"}), w.ml)
        check(
            "ACC-GRANT-3",
            "an ml_engineer proposes a visible dataset and the preview hides held-out data from them",
            ml_call.created_by == "ml-1" and w.preview_calls[-1]["include_held_out"] is False,
        )
        orphan = ustore.Dataset(
            id=det_uuid("orphan"),
            organization_id=_ORG,
            project_id=_PROJECT,
            name="o",
            version=1,
            purpose="training",
            upload_session_id=det_uuid("no-session"),
            upload_status="verified",
            verified_sha256=sha("o"),
            verified_size_bytes=1,
            created_by="s",
            created_at=_NOW,
        )
        w.session.add(orphan)
        w.session.flush()
        refuses(
            w, "a dataset whose upload session is gone", "dataset_not_found", pr(dataset=orphan)
        )

        # grants
        narrow_conn = w.connection("narrow", ["validation"])
        narrowed = w.dataset("narrowed", "training", narrow_conn)
        refuses(
            w,
            "a connection that does not grant the dataset's purpose",
            "purpose_not_authorized",
            pr(dataset=narrowed),
            "ACC-GRANT-4",
        )
        revoked_conn = w.connection("revoked", ALL_PURPOSES, status="revoked")
        revoked = w.dataset("revoked", "training", revoked_conn)
        refuses(
            w, "a revoked connection", "purpose_not_authorized", pr(dataset=revoked), "ACC-GRANT-5"
        )
        gone_conn = w.connection("gone", ALL_PURPOSES)
        gone = w.dataset("gone", "training", gone_conn)
        del w.connections[gone_conn]
        refuses(
            w, "a missing connection", "purpose_not_authorized", pr(dataset=gone), "ACC-GRANT-6"
        )
        evaluation_only = w.connection("evaluation-only", ["validation", "held_out_evaluation"])
        trained = w.dataset("trained", "training", evaluation_only)
        refuses(
            w,
            "a training dataset on an evaluation-only grant",
            "purpose_not_authorized",
            pr(dataset=trained),
            "ACC-GRANT-7",
        )
        n_calls = len(w.preview_calls)

        # importer unavailable
        xlsx = w.dataset(
            "book",
            "training",
            conn,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        refuses(w, "an XLSX dataset", "importer_unavailable", pr(dataset=xlsx))
        nothing = w.dataset("nothing", "training", conn, content_type=None)
        refuses(
            w,
            "a dataset with no verified content type",
            "importer_unavailable",
            pr(dataset=nothing),
        )
        check(
            "ACC-GRANT-8",
            "authorization and the importer are decided before the preview reads a byte",
            len(w.preview_calls) == n_calls,
        )

        # preview outcomes (Law 17)
        for label, setup, code in (
            (
                "the importer vanishing",
                lambda: setattr(w, "preview_error", MappingPreviewUnavailableError("x/y")),
                "importer_unavailable",
            ),
            (
                "the dataset vanishing during the preview",
                lambda: setattr(w, "preview_error", ustore.DatasetNotFoundError("gone")),
                "dataset_not_found",
            ),
            (
                "the grant vanishing during the preview",
                lambda: setattr(w, "preview_error", crud.DatasetSourceAuthorizationError("no")),
                "purpose_not_authorized",
            ),
            (
                "the connection vanishing during the preview",
                lambda: setattr(
                    w, "preview_error", crud.DatasetSourceConnectionNotFoundError("no")
                ),
                "purpose_not_authorized",
            ),
            (
                "the dataset and its upload disagreeing",
                lambda: setattr(w, "preview_error", DatasetSourceInconsistentError("x")),
                "source_changed",
            ),
            (
                "a blocked preview",
                lambda: setattr(w, "preview_state", ("blocked", "source_unavailable")),
                "source_unavailable",
            ),
            (
                "a changed object",
                lambda: setattr(w, "preview_state", ("failed", "source_changed")),
                "source_changed",
            ),
            (
                "a failed preview",
                lambda: setattr(w, "preview_state", ("failed", "too_many_rejections")),
                "preview_incomplete",
            ),
        ):
            w.preview_error, w.preview_state = None, ("completed", None)
            setup()
            refuses(
                w,
                label,
                code,
                pr(msg_doc({"human": "user", "bot": "assistant", "q": "system"})),
                "NEG-LAW17-1",
            )
        w.preview_error, w.preview_state = None, ("completed", None)
        check(
            "NEG-LAW17-2",
            "positive control: the same proposal succeeds once the preview does",
            w.propose(
                ds, msg_doc({"human": "user", "bot": "assistant", "q": "system"})
            ).mapping_version
            >= 2,
        )

        # a lost race on the version number
        real_flush = w.session.flush

        def conflicting_flush(*a: Any, **k: Any) -> None:
            if any(isinstance(o, store.DatasetMapping) for o in w.session.new):
                raise IntegrityError("insert", {}, Exception("uq_dataset_mappings_dataset_version"))
            real_flush(*a, **k)

        w.session.flush = conflicting_flush
        try:
            got = code_of(
                lambda: w.propose(
                    ds, msg_doc({"human": "user", "bot": "assistant", "race": "system"})
                )
            )
        finally:
            w.session.flush = real_flush
        w.session.rollback()
        check(
            "ACC-VERSION-9",
            f"a concurrent proposal of the same version is mapping_conflict (got {got})",
            got == "mapping_conflict",
        )


def seeded(
    w: World, name: str = "alpha", purpose: str = "training", **kw: Any
) -> tuple[ustore.Dataset, store.DatasetMapping]:
    conn = w.connection(f"conn-{name}", ALL_PURPOSES)
    ds = w.dataset(name, purpose, conn, **kw)
    return ds, w.propose(ds, now=_NOW)


def part_s_approve() -> None:
    with sqlite_world() as w:
        ds, m = seeded(w)
        events = len(w.audit.events)
        frozen = (
            m.id,
            m.mapping_sha256,
            m.preview_sha256,
            m.mapping_version,
            m.created_at,
            m.created_by,
            m.dataset_sha256,
        )
        done = w.approve(m, now=_LATER, actor=w.org_admin)
        check(
            "ACC-APPROVE-1",
            "approval stores the approver from the actor, never from input",
            (done.approved_by, done.approver_role) == ("admin-2", "organization_admin"),
        )
        check(
            "ACC-APPROVE-2",
            "approval stores the purpose, status and instant",
            (done.status, done.approved_purpose, done.approved_at, done.updated_at)
            == ("approved", "training", _LATER, _LATER),
        )
        check(
            "ACC-APPROVE-3",
            "approval changes nothing else",
            (
                done.id,
                done.mapping_sha256,
                done.preview_sha256,
                done.mapping_version,
                done.created_at,
                done.created_by,
                done.dataset_sha256,
            )
            == frozen
            and frozen[4] == _NOW
            and frozen[5] == "admin-1",
        )
        check(
            "ACC-APPROVE-4",
            "a reject-policy mapping acknowledges nothing",
            done.ignored_keys_sha256 is None,
        )
        check(
            "ACC-APPROVE-5",
            "one content-free approval audit event for the approver",
            w.audit.actions()[events:] == ["dataset_mapping.approved"]
            and w.audit.events[-1]["actor_id"] == "admin-2"
            and w.audit.events[-1]["details"]["status"] == "approved"
            and w.audit.events[-1]["resource_id"] == str(m.id),
        )
        call = w.preview_calls[-1]
        check(
            "ACC-APPROVE-6",
            "the preview was re-run from the stored mapping with reveal=True, held-out visible, on the caller's storage",
            call["reveal"] is True
            and call["include_held_out"] is True
            and call["storage"] is w.storage
            and call["mapping"].mapping_sha256 == m.mapping_sha256
            and call["dataset_id"] == ds.id,
        )
        refuses(w, "approving twice", "mapping_not_proposed", lambda: w.approve(m))
        refuses(w, "rejecting an approved mapping", "mapping_not_proposed", lambda: w.reject(m))

    with sqlite_world() as w:
        ds, m = seeded(w)
        refuses(
            w,
            "ml_engineer approving",
            "approval_not_permitted",
            lambda: w.approve(m, actor=w.ml),
            "ACC-APPROVE-7",
        )
        refuses(
            w,
            "a viewer approving",
            "approval_not_permitted",
            lambda: w.approve(m, actor=store.MappingActor("user", "v", "viewer", "r")),
        )
        refuses(w, "a non-actor approving", "invalid_request", lambda: w.approve(m, actor=None))
        refuses(
            w, "ml_engineer rejecting", "approval_not_permitted", lambda: w.reject(m, actor=w.ml)
        )
        refuses(
            w,
            "a malformed expected hash",
            "invalid_request",
            lambda: w.approve(m, expected_preview_sha256="abc"),
        )
        refuses(
            w,
            "an upper-case expected hash",
            "invalid_request",
            lambda: w.approve(m, expected_preview_sha256=m.preview_sha256.upper()),
        )
        refuses(
            w,
            "a str-subclass expected hash",
            "invalid_request",
            lambda: w.approve(m, expected_preview_sha256=StrSub(m.preview_sha256)),
        )
        refuses(w, "an empty purpose", "invalid_request", lambda: w.approve(m, purpose=""))
        refuses(w, "a padded purpose", "invalid_request", lambda: w.approve(m, purpose=" training"))
        refuses(
            w, "an over-long purpose", "invalid_request", lambda: w.approve(m, purpose="t" * 33)
        )
        refuses(
            w,
            "acknowledgements that are not a list",
            "invalid_request",
            lambda: w.approve(m, acknowledged_ignored_keys="weight"),
        )
        refuses(
            w,
            "acknowledgements that are not strings",
            "invalid_request",
            lambda: w.approve(m, acknowledged_ignored_keys=[1]),
        )
        refuses(
            w,
            "an unknown mapping id",
            "mapping_not_found",
            lambda: w.approve(m, mapping_id=uuid.uuid4()),
        )
        refuses(
            w, "a non-uuid mapping id", "mapping_not_found", lambda: w.approve(m, mapping_id="x")
        )
        refuses(
            w,
            "a mapping of another project",
            "mapping_not_found",
            lambda: w.approve(m, project_id=uuid.uuid4()),
        )
        refuses(
            w,
            "a purpose that is not the dataset's",
            "purpose_mismatch",
            lambda: w.approve(m, purpose="validation"),
            "ACC-GRANT-9",
        )
        refuses(
            w,
            "a stale expected preview hash",
            "preview_changed",
            lambda: w.approve(m, expected_preview_sha256=sha("stale")),
            "ACC-APPROVE-8",
        )
        w.preview_seed = "moved"
        refuses(
            w,
            "a preview that moved on the live bytes",
            "preview_changed",
            lambda: w.approve(m),
            "ACC-APPROVE-9",
        )
        w.preview_seed = "p1"
        w.tools_sha = sha("tools-2")
        w.preview_seed = "p1"
        check(
            "ACC-APPROVE-10",
            "positive control: the unchanged preview approves",
            w.approve(m).status == "approved",
        )

    with sqlite_world() as w:
        ds, m = seeded(w)
        conn = ds_connection(w, ds)
        w.connections[conn].allowed_purposes_json.remove("training")
        refuses(
            w,
            "a grant narrowed after the proposal",
            "purpose_not_authorized",
            lambda: w.approve(m),
            "ACC-GRANT-10",
        )
        w.connections[conn].allowed_purposes_json.append("training")
        w.connections[conn].connection_status = "revoked"
        refuses(
            w,
            "a revoked connection",
            "purpose_not_authorized",
            lambda: w.approve(m),
            "ACC-GRANT-11",
        )
        w.connections[conn].connection_status = "active"
        w.connections[conn].allowed_purposes_json.remove("validation")
        refuses(
            w,
            "authorization is checked for the requested purpose before the mismatch",
            "purpose_not_authorized",
            lambda: w.approve(m, purpose="validation"),
            "ACC-GRANT-12",
        )
        w.connections[conn].allowed_purposes_json.append("validation")
        n = len(w.preview_calls)
        refuses(
            w, "a purpose mismatch", "purpose_mismatch", lambda: w.approve(m, purpose="validation")
        )
        check(
            "ACC-GRANT-13",
            "nothing was read from storage for a refused authorization or mismatch",
            len(w.preview_calls) == n,
        )

    with sqlite_world() as w:
        ds, m = seeded(w)
        second = w.propose(ds, msg_doc({"human": "user", "bot": "assistant", "s": "system"}))
        refuses(
            w, "a superseded version", "mapping_superseded", lambda: w.approve(m), "ACC-APPROVE-11"
        )
        check(
            "ACC-APPROVE-12", "the latest version approves", w.approve(second).status == "approved"
        )

    with sqlite_world() as w:
        ds, m = seeded(w)
        w.preview_state = ("blocked", "source_unavailable")
        refuses(w, "a blocked re-run", "source_unavailable", lambda: w.approve(m), "NEG-LAW17-3")
        w.preview_state = ("failed", "source_changed")
        refuses(
            w, "a changed object at approval", "source_changed", lambda: w.approve(m), "NEG-LAW17-4"
        )
        w.preview_state = ("failed", "too_many_rejections")
        refuses(
            w, "an unfinished re-run", "preview_incomplete", lambda: w.approve(m), "NEG-LAW17-5"
        )
        w.preview_state = ("completed", None)
        w.preview_error = MappingPreviewUnavailableError("x")
        refuses(w, "the importer vanishing", "importer_unavailable", lambda: w.approve(m))
        w.preview_error = None
        w.preview_counts = {"seen": 2, "accepted": 1, "rejected": 1, "rejected:unmapped_role": 1}
        refuses(
            w,
            "unmapped role rows",
            "critical_rows_unmapped",
            lambda: w.approve(m),
            "ACC-CRITICAL-8",
        )
        w.preview_counts = {
            "seen": 2,
            "accepted": 1,
            "rejected": 1,
            "rejected:unmapped_message_key": 1,
        }
        refuses(
            w,
            "unmapped message key rows",
            "critical_rows_unmapped",
            lambda: w.approve(m),
            "ACC-CRITICAL-9",
        )
        w.preview_counts = {
            "seen": 2,
            "accepted": 1,
            "rejected": 1,
            "rejected:canonical_schema_invalid": 1,
        }
        check(
            "ACC-CRITICAL-10",
            "other rejected rows are shown, not a critical block",
            w.approve(m).status == "approved",
        )

    with sqlite_world() as w:
        ds, m = seeded(w)
        m.mapping_document = {
            **m.mapping_document,
            "role_map": {"human": "user", "bot": "assistant", "x": "system"},
        }
        w.session.flush()
        refuses(
            w,
            "a stored document that no longer hashes",
            "mapping_tampered",
            lambda: w.approve(m),
            "NEG-TAMPER-1",
        )
        m.mapping_document = {"shape": "messages"}
        w.session.flush()
        refuses(
            w,
            "a stored document that no longer parses",
            "mapping_invalid",
            lambda: w.approve(m),
            "NEG-TAMPER-2",
        )

    # ignored keys
    with sqlite_world() as w:
        conn = w.connection("ig", ALL_PURPOSES)
        ds = w.dataset("ig", "training", conn)
        w.preview_rows = ignore_rows()
        m = w.propose(ds, msg_doc(policy="ignore"))
        refuses(
            w,
            "no acknowledgement",
            "ignored_keys_not_acknowledged",
            lambda: w.approve(m),
            "ACC-IGNORE-1",
        )
        refuses(
            w,
            "a partial acknowledgement",
            "ignored_keys_not_acknowledged",
            lambda: w.approve(m, acknowledged_ignored_keys=["weight"]),
            "ACC-IGNORE-2",
        )
        refuses(
            w,
            "an acknowledgement with an extra key",
            "ignored_keys_not_acknowledged",
            lambda: w.approve(m, acknowledged_ignored_keys=["weight", "zeta", "x"]),
            "ACC-IGNORE-3",
        )
        refuses(
            w,
            "a duplicated acknowledgement",
            "ignored_keys_not_acknowledged",
            lambda: w.approve(m, acknowledged_ignored_keys=["weight", "weight", "zeta"]),
            "ACC-IGNORE-4",
        )
        refuses(
            w,
            "a differently-cased acknowledgement",
            "ignored_keys_not_acknowledged",
            lambda: w.approve(m, acknowledged_ignored_keys=["Weight", "zeta"]),
            "ACC-IGNORE-5",
        )
        done = w.approve(m, acknowledged_ignored_keys=("zeta", "weight"))
        check("ACC-IGNORE-6", "the exact set, in any order, approves", done.status == "approved")
        check(
            "ACC-IGNORE-7",
            "only the sha256 of the sorted key list is stored",
            done.ignored_keys_sha256 == hashlib.sha256(b'["weight","zeta"]').hexdigest(),
        )
        check(
            "NEG-CONTENT-2",
            "no key name reached the row or the audit log",
            "weight" not in str(done.__dict__)
            and "zeta" not in str(done.__dict__)
            and "weight" not in json.dumps(w.audit.events, default=str),
        )

    with sqlite_world() as w:
        conn = w.connection("ig2", ALL_PURPOSES)
        ds = w.dataset("ig2", "training", conn)
        m = w.propose(ds, msg_doc(policy="ignore"))
        check(
            "ACC-IGNORE-8",
            "an ignore-policy mapping that ignored nothing is acknowledged by the empty list, hashed",
            w.approve(m, acknowledged_ignored_keys=[]).ignored_keys_sha256
            == hashlib.sha256(b"[]").hexdigest(),
        )
    with sqlite_world() as w:
        conn = w.connection("ig3", ALL_PURPOSES)
        ds = w.dataset("ig3", "training", conn)
        w.preview_rows = ignore_rows()
        m = w.propose(ds, msg_doc(policy="reject"))
        refuses(
            w,
            "an acknowledgement for a mapping that ignores nothing",
            "ignored_keys_not_acknowledged",
            lambda: w.approve(m, acknowledged_ignored_keys=["weight"]),
            "ACC-IGNORE-9",
        )
        check(
            "ACC-IGNORE-10",
            "a reject-policy mapping stores no acknowledgement hash",
            w.approve(m).ignored_keys_sha256 is None,
        )

    # the lock window: what changes between the first read and the write lock
    with sqlite_world() as w:
        ds, m = seeded(w)

        def flip_status() -> None:
            m.status = "rejected"
            w.session.flush()

        w.before_preview = flip_status
        refuses(
            w,
            "a mapping decided while its preview ran",
            "mapping_not_proposed",
            lambda: w.approve(m),
            "ACC-RACE-1",
        )
    with sqlite_world() as w:
        ds, m = seeded(w)

        def add_version() -> None:
            w.propose(ds, msg_doc({"human": "user", "bot": "assistant", "late": "system"}))

        w.before_preview = add_version
        n = len(w.audit.events)
        got = code_of(lambda: w.approve(m))
        check(
            "ACC-RACE-2",
            f"a newer version proposed while the preview ran supersedes the approval (got {got})",
            got == "mapping_superseded"
            and w.session.get(store.DatasetMapping, m.id).status == "proposed"
            and w.audit.actions()[n:] == ["dataset_mapping.proposed"],
        )

    # a database that refuses the approval
    with sqlite_world() as w:
        ds, m = seeded(w)
        real_flush = w.session.flush

        def refusing_flush(*a: Any, **k: Any) -> None:
            if any(
                isinstance(o, store.DatasetMapping) and o.status == "approved"
                for o in w.session.dirty
            ):
                raise IntegrityError(
                    "update", {}, Exception("ck_dataset_mappings_approved_purpose_in_grant")
                )
            real_flush(*a, **k)

        w.session.flush = refusing_flush
        try:
            got = code_of(lambda: w.approve(m))
        finally:
            w.session.flush = real_flush
        w.session.rollback()
        check(
            "ACC-APPROVE-13",
            f"a CHECK that refuses the approval is approval_refused (got {got})",
            got == "approval_refused",
        )

    # reject
    with sqlite_world() as w:
        ds, m = seeded(w)
        events = len(w.audit.events)
        rej = w.reject(m, now=_LATER)
        check(
            "ACC-REJECT-1",
            "a proposed mapping rejects with no approval column set",
            (
                rej.status,
                rej.approved_by,
                rej.approved_purpose,
                rej.approver_role,
                rej.approved_at,
                rej.updated_at,
            )
            == ("rejected", None, None, None, None, _LATER),
        )
        check(
            "ACC-REJECT-2",
            "one rejection audit event for the actor",
            w.audit.actions()[events:] == ["dataset_mapping.rejected"]
            and w.audit.events[-1]["actor_id"] == "admin-1",
        )
        refuses(w, "rejecting twice", "mapping_not_proposed", lambda: w.reject(m))
        refuses(w, "approving a rejected mapping", "mapping_not_proposed", lambda: w.approve(m))
        refuses(
            w,
            "rejecting an unknown mapping",
            "mapping_not_found",
            lambda: w.reject(m, mapping_id=uuid.uuid4()),
        )
        refuses(w, "rejecting as a non-actor", "invalid_request", lambda: w.reject(m, actor=7))


def ds_connection(w: World, ds: ustore.Dataset) -> uuid.UUID:
    return w.session.get(
        ustore.DatasetUploadSession, ds.upload_session_id
    ).dataset_source_connection_id


def part_s_resolve() -> None:
    with sqlite_world() as w:
        conn = w.connection("r", ALL_PURPOSES)
        ds = w.dataset("own", "training", conn)
        refuses(w, "no mapping at all", "mapping_not_found", lambda: w.resolve(ds), "ACC-RESOLVE-1")
        m = w.propose(ds)
        refuses(
            w,
            "only a proposed mapping",
            "mapping_not_found",
            lambda: w.resolve(ds),
            "ACC-RESOLVE-2",
        )
        w.approve(m)
        got = w.resolve(ds)
        check(
            "ACC-RESOLVE-3",
            "the dataset's own approved mapping resolves with its ids",
            got.mapping_id == m.id
            and got.mapping_version == 1
            and got.source_dataset_id == ds.id
            and got.approved_purpose == "training"
            and got.reused is False,
        )
        check(
            "ACC-RESOLVE-4",
            "the resolved mapping is the parsed stored document",
            got.mapping.mapping_sha256 == m.mapping_sha256
            and dict(got.mapping.document) == msg_doc(),
        )
        n = len(w.preview_calls)
        w.resolve(ds)
        check(
            "ACC-RESOLVE-5",
            "the dataset's own approval needs no preview",
            len(w.preview_calls) == n,
        )
        refuses(
            w,
            "a purpose that is not the dataset's",
            "purpose_mismatch",
            lambda: w.resolve(ds, purpose="validation"),
        )
        refuses(w, "a malformed purpose", "invalid_request", lambda: w.resolve(ds, purpose=""))
        refuses(
            w,
            "a non-bool include_held_out",
            "invalid_request",
            lambda: w.resolve(ds, include_held_out=1),
        )
        refuses(w, "a non-uuid dataset", "invalid_request", lambda: w.resolve(ds, dataset_id="x"))
        refuses(
            w,
            "an unknown dataset",
            "dataset_not_found",
            lambda: w.resolve(ds, dataset_id=uuid.uuid4()),
        )
        w.connections[conn].connection_status = "revoked"
        refuses(
            w,
            "a revoked grant resolves nothing, even the dataset's own approval",
            "purpose_not_authorized",
            lambda: w.resolve(ds),
            "ACC-RESOLVE-6",
        )
        w.connections[conn].connection_status = "active"
        w.connections[conn].allowed_purposes_json.remove("training")
        refuses(
            w, "a narrowed grant", "purpose_not_authorized", lambda: w.resolve(ds), "ACC-RESOLVE-7"
        )
        w.connections[conn].allowed_purposes_json.append("training")
        check(
            "ACC-RESOLVE-8",
            "positive control: the restored grant resolves",
            w.resolve(ds).mapping_id == m.id,
        )
        # the latest approved version wins
        m2 = w.propose(ds, msg_doc({"human": "user", "bot": "assistant", "s": "system"}))
        check(
            "ACC-RESOLVE-9",
            "a pending newer version does not hide the approved one",
            w.resolve(ds).mapping_id == m.id,
        )
        w.approve(m2)
        check(
            "ACC-RESOLVE-10",
            "the newest approved version wins",
            w.resolve(ds).mapping_id == m2.id and w.resolve(ds).mapping_version == 2,
        )
        m2.mapping_document = {
            **m2.mapping_document,
            "role_map": {"human": "user", "bot": "assistant", "x": "system"},
        }
        w.session.flush()
        refuses(
            w,
            "a stored document that no longer hashes",
            "mapping_tampered",
            lambda: w.resolve(ds),
            "NEG-TAMPER-3",
        )

    csv_doc = {
        "shape": "prompt_completion",
        "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
        "prompt": {"header": "q"},
        "completion": {"header": "a"},
    }
    with sqlite_world() as w:
        conn = w.connection("t", ALL_PURPOSES)
        w.schema_sha = sha("q,a")
        v1 = w.dataset("tab", "training", conn, version=1, content_type="text/csv")
        m = w.propose(v1, csv_doc)
        w.approve(m)
        v2 = w.dataset("tab", "training", conn, version=2, content_type="text/csv")
        n = len(w.preview_calls)
        got = w.resolve(v2, include_held_out=False)
        check(
            "ACC-REUSE-1",
            "an unchanged header and tool set reuses the prior approval",
            got.reused is True
            and got.mapping_id == m.id
            and got.source_dataset_id == v1.id
            and got.mapping_version == 1
            and got.approved_purpose == "training",
        )
        call = w.preview_calls[n]
        check(
            "ACC-REUSE-2",
            "the reuse re-probed the new dataset with the prior mapping, reveal=False, on the caller's storage and visibility",
            call["dataset_id"] == v2.id
            and call["reveal"] is False
            and call["include_held_out"] is False
            and call["storage"] is w.storage
            and call["mapping"].mapping_sha256 == m.mapping_sha256,
        )
        w.schema_sha = sha("q,a,extra")
        refuses(
            w,
            "a changed header",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "ACC-REUSE-3",
        )
        w.schema_sha = None
        refuses(
            w,
            "no header hash on the re-probe",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "ACC-REUSE-4",
        )
        w.schema_sha = sha("q,a")
        w.tools_sha = sha("tools-2")
        refuses(
            w,
            "changed tool definitions",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "ACC-REUSE-5",
        )
        w.tools_sha = sha("tools-1")
        w.preview_counts = {"seen": 2, "accepted": 1, "rejected": 1, "rejected:unmapped_role": 1}
        refuses(
            w,
            "an unmapped role on the new bytes",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "ACC-REUSE-6",
        )
        w.preview_counts = {
            "seen": 2,
            "accepted": 1,
            "rejected": 1,
            "rejected:unmapped_message_key": 1,
        }
        refuses(
            w,
            "an unmapped message key on the new bytes",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
        )
        w.preview_counts = {"seen": 2, "accepted": 2, "rejected": 0}
        w.preview_state = ("failed", "too_many_rejections")
        refuses(
            w,
            "a prior mapping that cannot finish a preview",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "ACC-REUSE-7",
        )
        w.preview_state = ("blocked", "source_unavailable")
        refuses(
            w,
            "an outage stays an outage, not a re-approval",
            "source_unavailable",
            lambda: w.resolve(v2),
            "NEG-LAW17-6",
        )
        w.preview_state = ("failed", "source_changed")
        refuses(
            w,
            "a changed object stays visible",
            "source_changed",
            lambda: w.resolve(v2),
            "NEG-LAW17-7",
        )
        w.preview_state = ("completed", None)
        w.connections[conn].connection_status = "revoked"
        refuses(
            w,
            "a revoked grant never reuses",
            "purpose_not_authorized",
            lambda: w.resolve(v2),
            "ACC-REUSE-8",
        )
        w.connections[conn].connection_status = "active"
        check(
            "ACC-REUSE-9",
            "positive control: the restored state reuses again",
            w.resolve(v2).reused is True,
        )
        # a later own approval beats the reuse
        own = w.propose(v2, csv_doc)
        w.approve(own)
        check(
            "ACC-REUSE-10",
            "the new version's own approval wins over a reuse",
            w.resolve(v2).mapping_id == own.id and w.resolve(v2).reused is False,
        )

    with sqlite_world() as w:
        conn = w.connection("j", ALL_PURPOSES)
        j1 = w.dataset("log", "training", conn, version=1)
        jm = w.propose(j1)
        w.approve(jm)
        j2 = w.dataset("log", "training", conn, version=2)
        n = len(w.preview_calls)
        refuses(
            w,
            "a JSONL prior version",
            "mapping_reapproval_required",
            lambda: w.resolve(j2),
            "ACC-REUSE-12",
        )
        check(
            "ACC-REUSE-13",
            "JSONL reuse is decided before any byte is read",
            len(w.preview_calls) == n,
        )
        jc = w.dataset("log", "training", conn, version=3, content_type="application/json")
        refuses(
            w,
            "a JSON dataset after a JSONL approval",
            "mapping_reapproval_required",
            lambda: w.resolve(jc),
        )

    with sqlite_world() as w:
        conn = w.connection("x", ALL_PURPOSES)
        w.schema_sha = sha("q,a")
        c1 = w.dataset("mixed", "training", conn, version=1, content_type="text/csv")
        cm = w.propose(c1, csv_doc)
        w.approve(cm)
        j2 = w.dataset("mixed", "training", conn, version=2, content_type="application/jsonl")
        refuses(
            w,
            "a CSV approval never serves a JSONL upload",
            "mapping_reapproval_required",
            lambda: w.resolve(j2),
            "ACC-REUSE-14",
        )
        other_name = w.dataset("different", "training", conn, version=2, content_type="text/csv")
        refuses(
            w,
            "another dataset name has no prior",
            "mapping_not_found",
            lambda: w.resolve(other_name),
            "ACC-REUSE-15",
        )
        other_purpose = w.dataset("mixed", "validation", conn, version=3, content_type="text/csv")
        refuses(
            w,
            "another purpose has no prior",
            "mapping_not_found",
            lambda: w.resolve(other_purpose),
            "ACC-REUSE-16",
        )

    with sqlite_world() as w:
        conn = w.connection("y", ALL_PURPOSES)
        w.schema_sha = sha("q,a")
        c1 = w.dataset("narrow", "training", conn, version=1, content_type="text/csv")
        cm = w.propose(c1, csv_doc)
        w.approve(cm)
        cm.approved_purpose = "validation"
        w.session.flush()
        c2 = w.dataset("narrow", "training", conn, version=2, content_type="text/csv")
        refuses(
            w,
            "a prior approval for another purpose",
            "mapping_reapproval_required",
            lambda: w.resolve(c2),
            "ACC-REUSE-17",
        )


def part_s_reads() -> None:
    with sqlite_world() as w:
        conn = w.connection("rd", ALL_PURPOSES)
        ds = w.dataset("rd", "training", conn)
        versions = [
            w.propose(ds, msg_doc({"human": "user", "bot": "assistant", f"k{i}": "system"}))
            for i in range(4)
        ]
        got = store.get_dataset_mapping(
            w.session, w.ctx, project_id=_PROJECT, mapping_id=versions[2].id
        )
        check("ACC-READ-1", "get returns the mapping", got.id == versions[2].id)
        refuses(
            w,
            "a get with the wrong project",
            "mapping_not_found",
            lambda: store.get_dataset_mapping(
                w.session, w.ctx, project_id=uuid.uuid4(), mapping_id=versions[0].id
            ),
        )
        listed = store.list_dataset_mappings(
            w.session, w.ctx, project_id=_PROJECT, dataset_id=ds.id
        )
        check(
            "ACC-READ-2",
            "list returns every version, oldest first",
            [m.mapping_version for m in listed] == [1, 2, 3, 4],
        )
        check(
            "ACC-READ-3",
            "list honours limit",
            [
                m.mapping_version
                for m in store.list_dataset_mappings(
                    w.session, w.ctx, project_id=_PROJECT, dataset_id=ds.id, limit=2
                )
            ]
            == [1, 2],
        )
        check(
            "ACC-READ-4",
            "list resumes after a version",
            [
                m.mapping_version
                for m in store.list_dataset_mappings(
                    w.session,
                    w.ctx,
                    project_id=_PROJECT,
                    dataset_id=ds.id,
                    limit=5,
                    after_version=2,
                )
            ]
            == [3, 4],
        )
        check(
            "ACC-READ-5",
            "list is empty for another dataset or project",
            store.list_dataset_mappings(
                w.session, w.ctx, project_id=_PROJECT, dataset_id=uuid.uuid4()
            )
            == []
            and store.list_dataset_mappings(
                w.session, w.ctx, project_id=uuid.uuid4(), dataset_id=ds.id
            )
            == [],
        )
        check(
            "ACC-READ-6",
            "list accepts the maximum limit",
            len(
                store.list_dataset_mappings(
                    w.session, w.ctx, project_id=_PROJECT, dataset_id=ds.id, limit=200
                )
            )
            == 4,
        )
        for label, kw in (
            ("limit 0", {"limit": 0}),
            ("limit 201", {"limit": 201}),
            ("a bool limit", {"limit": True}),
            ("a negative cursor", {"after_version": -1}),
            ("a bool cursor", {"after_version": False}),
            ("a string limit", {"limit": "5"}),
        ):
            refuses(
                w,
                f"list with {label}",
                "invalid_request",
                lambda kw=kw: store.list_dataset_mappings(
                    w.session, w.ctx, project_id=_PROJECT, dataset_id=ds.id, **kw
                ),
            )
        refuses(
            w,
            "list with a non-uuid dataset",
            "invalid_request",
            lambda: store.list_dataset_mappings(
                w.session, w.ctx, project_id=_PROJECT, dataset_id="d"
            ),
        )


def part_s_variants() -> None:
    """Values that differ only by case or whitespace are different values, everywhere."""
    from dataclasses import replace as dc_replace

    json_doc = {
        "shape": "messages_json",
        "messages": {"header": "m"},
        "role_key": "role",
        "content_key": "content",
        "role_map": {"human": "user"},
        "on_unmapped_message_keys": "ignore",
    }
    mapping = mapping_of(json_doc)
    cell = json.dumps([{"role": "human", "content": "x", "k1": 1}])
    loud = dc_replace(mapping, messages_slot=ic.Slot("HEADER", "m"))
    check(
        "VARIANT-1",
        "a slot kind is exact (HEADER is not header)",
        store.ignored_message_keys(loud, preview_rows({"m": cell})) == (),
    )
    shouting = dc_replace(mapping, messages_slot=ic.Slot("header", "M"))
    check(
        "VARIANT-2",
        "a slot name is exact (M is not m)",
        store.ignored_message_keys(shouting, preview_rows({"m": cell})) == (),
    )
    check(
        "VARIANT-3",
        "an index slot at exactly len(row) finds nothing",
        store.ignored_message_keys(
            mapping_of(dict(json_doc, messages={"index": 1})), preview_rows((cell,))
        )
        == (),
    )

    with sqlite_world() as w:
        conn = w.connection("v", [*ALL_PURPOSES, "Training"])
        ds = w.dataset("alpha", "training", conn)
        for state in ("BLOCKED", " blocked", "Blocked"):
            w.preview_state = (state, "source_unavailable")
            refuses(
                w,
                f"preview state {state!r} is not blocked",
                "preview_incomplete",
                lambda: w.propose(ds),
                "VARIANT-4",
            )
        for state in ("COMPLETED", " completed", "Completed "):
            w.preview_state = (state, None)
            refuses(
                w,
                f"preview state {state!r} is not completed",
                "preview_incomplete",
                lambda: w.propose(ds),
                "VARIANT-5",
            )
        w.preview_state = ("completed", None)
        m = w.propose(ds, now=_NOW)
        for attr, value in (
            ("mapping_sha256", m.mapping_sha256.upper()),
            ("preview_sha256", m.preview_sha256.upper()),
            ("tool_definitions_sha256", m.tool_definitions_sha256.upper()),
            ("importer", "JSONL"),
            ("importer_version", " 1"),
            ("transform_version", "H011.1"),
        ):
            saved = getattr(m, attr)
            setattr(m, attr, value)
            w.session.flush()
            again = w.propose(ds, now=_NOW)
            check(
                "VARIANT-6",
                f"a stored {attr} differing only by case/space is a different proposal",
                again.id != m.id,
            )
            w.session.delete(again)
            w.session.flush()
            setattr(m, attr, saved)
            w.session.flush()
        for status in ("Proposed", " proposed", "PROPOSED"):
            m.status = status
            w.session.flush()
            n = len(w.preview_calls)
            refuses(
                w,
                f"approve a mapping with status {status!r}",
                "mapping_not_proposed",
                lambda: w.approve(m),
                "VARIANT-7",
            )
            refuses(
                w,
                f"reject a mapping with status {status!r}",
                "mapping_not_proposed",
                lambda: w.reject(m),
                "VARIANT-8",
            )
            check(
                "VARIANT-9",
                "the status is checked before any byte is read",
                len(w.preview_calls) == n,
            )
        m.status = "proposed"
        w.session.flush()
        m.dataset_purpose = "Training"
        w.session.flush()
        refuses(
            w,
            "a stored dataset purpose that differs only by case",
            "purpose_mismatch",
            lambda: w.approve(m, purpose="training"),
            "VARIANT-10",
        )
        m.dataset_purpose = "training"
        w.session.flush()
        m2 = w.propose(ds, msg_doc({"human": "user", "bot": "assistant", "s": "system"}), now=_NOW)
        n = len(w.preview_calls)
        refuses(
            w,
            "a superseded version is refused before any byte is read",
            "mapping_superseded",
            lambda: w.approve(m),
            "VARIANT-11",
        )
        check("VARIANT-12", "no storage read for the superseded version", len(w.preview_calls) == n)
        w.approve(m2)
        refuses(
            w,
            "a purpose that differs only by case",
            "purpose_mismatch",
            lambda: w.resolve(ds, purpose="Training"),
            "VARIANT-13",
        )

    csv_doc = {
        "shape": "prompt_completion",
        "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
        "prompt": {"header": "q"},
        "completion": {"header": "a"},
    }
    with sqlite_world() as w:
        conn = w.connection("v2", [*ALL_PURPOSES, "Training"])
        w.schema_sha = sha("q,a")
        v1 = w.dataset("tab", "training", conn, version=1, content_type="text/csv")
        cm = w.propose(v1, csv_doc)
        w.approve(cm)
        v2 = w.dataset("tab", "training", conn, version=2, content_type="text/csv")
        check("VARIANT-14", "positive control: reuse works", w.resolve(v2).reused is True)
        cm.approved_purpose = "Training"
        w.session.flush()
        refuses(
            w,
            "an approved purpose that differs only by case",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "VARIANT-15",
        )
        cm.approved_purpose = "training"
        cm.importer = "CSV"
        w.session.flush()
        refuses(
            w,
            "a stored importer that differs only by case",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "VARIANT-16",
        )
        cm.importer = "csv"
        w.session.flush()
        caps = w.dataset("tab", "Training", conn, version=3, content_type="text/csv")
        refuses(
            w,
            "a dataset purpose that differs only by case has no prior",
            "mapping_not_found",
            lambda: w.resolve(caps, purpose="Training"),
            "VARIANT-17",
        )
        # hash comparisons are byte exact
        cm.source_schema_sha256 = sha("q,a").upper()
        w.session.flush()
        refuses(
            w,
            "an upper-case stored header hash",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "VARIANT-18",
        )
        cm.source_schema_sha256 = sha("q,a")
        cm.tool_definitions_sha256 = cm.tool_definitions_sha256.upper()
        w.session.flush()
        refuses(
            w,
            "an upper-case stored tool-definitions hash",
            "mapping_reapproval_required",
            lambda: w.resolve(v2),
            "VARIANT-19",
        )

    with sqlite_world() as w:
        ds, m = seeded(w)
        m.mapping_sha256 = m.mapping_sha256.upper()
        w.session.flush()
        refuses(
            w,
            "an upper-case stored mapping hash at approval",
            "mapping_tampered",
            lambda: w.approve(m),
            "VARIANT-20",
        )
        m.mapping_sha256 = m.mapping_sha256.lower()
        m.preview_sha256 = m.preview_sha256.upper()
        w.session.flush()
        refuses(
            w,
            "an upper-case stored preview hash",
            "invalid_request",
            lambda: w.approve(m),
            "VARIANT-21",
        )
        refuses(
            w,
            "an upper-case stored preview hash against the live one",
            "preview_changed",
            lambda: w.approve(m, expected_preview_sha256=m.preview_sha256.lower()),
            "VARIANT-22",
        )
        m.preview_sha256 = m.preview_sha256.lower()
        w.session.flush()
        w.approve(m)
        m.mapping_sha256 = m.mapping_sha256.upper()
        w.session.flush()
        refuses(
            w,
            "an upper-case stored mapping hash at resolve",
            "mapping_tampered",
            lambda: w.resolve(ds),
            "VARIANT-23",
        )


def part_s_variants2() -> None:
    from dataclasses import replace as dc_replace

    for code in (" mapping_invalid", "Mapping_Invalid", "MAPPING_INVALID", "mapping_invalid "):
        try:
            store.MappingStoreError(code)
            outcome = "accepted"
        except AssertionError:
            outcome = "refused"
        check("VARIANT2-1", f"the code {code!r} is not a code", outcome == "refused")
    ignore = mapping_of(msg_doc(policy="ignore"))
    rows = preview_rows(
        {"conversation": [{"role": "human", "content": "a", "ROLE": 1, " content": 2}]}
    )
    check(
        "VARIANT2-2",
        "message key names are exact (ROLE and ' content' are not the mapped keys)",
        store.ignored_message_keys(ignore, rows) == (" content", "ROLE"),
    )
    shouting = dc_replace(ignore, on_unmapped_message_keys="IGNORE")
    check(
        "VARIANT2-3", "the ignore policy is exact", store.ignored_message_keys(shouting, rows) == ()
    )
    loud_index = dc_replace(
        mapping_of(
            {
                "shape": "messages_json",
                "messages": {"index": 0},
                "role_key": "role",
                "content_key": "content",
                "role_map": {"human": "user"},
                "on_unmapped_message_keys": "ignore",
            }
        ),
        messages_slot=ic.Slot("INDEX", 0),
    )
    cell = json.dumps([{"role": "human", "content": "x", "k1": 1}])
    check(
        "VARIANT2-4",
        "an index slot kind is exact",
        store.ignored_message_keys(loud_index, preview_rows((cell,))) == (),
    )

    with sqlite_world() as w:
        conn = w.connection("v3", [*ALL_PURPOSES, "Training"])
        ds = w.dataset("alpha", "training", conn)
        for who in (
            store.MappingActor("user", "a ", "workspace_admin", "r"),
            store.MappingActor("user", "a", "workspace_admin", "r "),
        ):
            refuses(
                w,
                "trailing whitespace in an actor field",
                "invalid_request",
                lambda who=who: w.propose(ds, actor=who),
                "VARIANT2-5",
            )
        refuses(
            w,
            "a role that differs only by case",
            "actor_not_permitted",
            lambda: w.propose(ds, actor=store.MappingActor("user", "a", "Workspace_Admin", "r")),
            "VARIANT2-6",
        )
        refuses(
            w,
            "an ML_ENGINEER in capitals",
            "actor_not_permitted",
            lambda: w.propose(ds, actor=store.MappingActor("user", "a", "ML_ENGINEER", "r")),
            "VARIANT2-7",
        )
        edge = store.MappingActor("user", "a" * 200, "workspace_admin", "r" * 100)
        check(
            "VARIANT2-8",
            "an actor id of exactly 200 and a request id of exactly 100 characters are accepted",
            w.propose(ds, actor=edge).created_by == "a" * 200,
        )
        m = w.session.query(store.DatasetMapping).one()
        refuses(
            w,
            "an approver role in capitals",
            "approval_not_permitted",
            lambda: w.approve(m, actor=store.MappingActor("user", "a", "Workspace_Admin", "r")),
            "VARIANT2-9",
        )
        refuses(
            w,
            "a purpose of 33 characters",
            "invalid_request",
            lambda: w.approve(m, purpose="t" * 33),
            "VARIANT2-10",
        )
        refuses(
            w,
            "a purpose that differs only by case from the dataset's",
            "purpose_mismatch",
            lambda: w.approve(m, purpose="Training"),
            "VARIANT2-11",
        )
        m.status = "rejected"
        w.session.flush()
        m.status = "REJECTED"
        w.session.flush()
        again = w.propose(ds, now=_NOW)
        check(
            "VARIANT2-12",
            "a stored status that only looks like rejected is not rejected (the identical proposal is reused)",
            again.id == m.id,
        )
        m.status = "proposed"
        w.session.flush()

        def shout() -> None:
            m.status = "PROPOSED"
            w.session.flush()

        w.before_preview = shout
        refuses(
            w,
            "a mapping whose status changed to a lookalike while its preview ran",
            "mapping_not_proposed",
            lambda: w.approve(m),
            "VARIANT2-13",
        )
        m.status = "proposed"
        w.session.flush()
        for limit in (1, 200):
            check(
                "VARIANT2-14",
                f"list accepts limit {limit}",
                len(
                    store.list_dataset_mappings(
                        w.session, w.ctx, project_id=_PROJECT, dataset_id=ds.id, limit=limit
                    )
                )
                == 1,
            )

    csv_doc = {
        "shape": "prompt_completion",
        "csv": {"encoding": "utf-8", "delimiter": ",", "has_header": True, "header_row": 0},
        "prompt": {"header": "q"},
        "completion": {"header": "a"},
    }
    with sqlite_world() as w:
        conn = w.connection("v4", [*ALL_PURPOSES, "Training"])
        w.schema_sha = sha("q,a")
        v1 = w.dataset("tab", "training", conn, version=1, content_type="text/csv")
        cm = w.propose(v1, csv_doc)
        w.approve(cm)
        twin = w.dataset("tab", "training", conn, version=1, content_type="text/csv", tag="twin")
        refuses(
            w,
            "a prior of the same version number is not a prior",
            "mapping_not_found",
            lambda: w.resolve(twin),
            "VARIANT2-15",
        )
        shout_name = w.dataset("TAB", "training", conn, version=2, content_type="text/csv")
        refuses(
            w,
            "a dataset name that differs only by case has no prior",
            "mapping_not_found",
            lambda: w.resolve(shout_name),
            "VARIANT2-16",
        )
        v2 = w.dataset("tab", "training", conn, version=2, content_type="text/csv")
        check(
            "VARIANT2-17",
            "positive control: the real next version reuses",
            w.resolve(v2).reused is True,
        )

    with sqlite_world() as w:
        conn = w.connection("v5", [*ALL_PURPOSES, "Training"])
        w.schema_sha = sha("q,a")
        c1 = w.dataset("cap", "Training", conn, version=1, content_type="text/csv")
        cm = w.propose(c1, csv_doc)
        w.approve(cm, purpose="Training")
        c2 = w.dataset("cap", "Training", conn, version=2, content_type="text/csv")
        check(
            "VARIANT2-18",
            "positive control: a Training approval reuses for Training",
            w.resolve(c2, purpose="Training").reused is True,
        )
        cm.approved_purpose = "training"
        w.session.flush()
        refuses(
            w,
            "an approved purpose 'training' against a 'Training' resolve",
            "mapping_reapproval_required",
            lambda: w.resolve(c2, purpose="Training"),
            "VARIANT2-19",
        )
        refuses(
            w,
            "a resolve purpose that differs only by case from the dataset's",
            "purpose_mismatch",
            lambda: w.resolve(c2, purpose="training"),
            "VARIANT2-20",
        )


def part_s_review() -> None:
    """Pre-review findings: JSON never reuses; a mapping bound to another connection never resolves."""
    with sqlite_world() as w:
        conn = w.connection("rv", ALL_PURPOSES)
        j1 = w.dataset("doc", "training", conn, version=1, content_type="application/json")
        jm = w.propose(j1)
        w.approve(jm)
        j2 = w.dataset("doc", "training", conn, version=2, content_type="application/json")
        n = len(w.preview_calls)
        refuses(
            w,
            "a JSON prior version",
            "mapping_reapproval_required",
            lambda: w.resolve(j2),
            "REVIEW-1",
        )
        check(
            "REVIEW-2", "JSON reuse is decided before any byte is read", len(w.preview_calls) == n
        )
        check(
            "REVIEW-3",
            "the JSON dataset's own approval still resolves",
            w.resolve(j1).mapping_id == jm.id,
        )
        jm.dataset_source_connection_id = det_uuid("another-connection")
        w.session.flush()
        refuses(
            w,
            "an approval recorded for another connection",
            "mapping_tampered",
            lambda: w.resolve(j1),
            "REVIEW-4",
        )


def part_s_round2() -> None:
    """Pre-review round 2: importer-level refusals are coded; a stored mapping that fails the critical checks never resolves."""
    with sqlite_world() as w:
        conn = w.connection("r2", ALL_PURPOSES)
        ds = w.dataset("alpha", "training", conn)
        for exc, code in (
            (ic.MappingInvalidError("bad_shape"), "mapping_invalid"),
            (ic.MappingUnsupportedError(), "mapping_unsupported"),
        ):
            w.preview_error = exc
            refuses(
                w,
                f"{type(exc).__name__} from the preview",
                code,
                lambda: w.propose(ds),
                "REVIEW2-1",
            )
        w.preview_error = None
        m = w.propose(ds)
        w.approve(m)
        no_assistant = {"human": "user"}
        m.mapping_document = {**m.mapping_document, "role_map": no_assistant}
        m.mapping_sha256 = mapping_of(m.mapping_document).mapping_sha256
        w.session.flush()
        refuses(
            w,
            "a stored approval without an assistant target",
            "mapping_tampered",
            lambda: w.resolve(ds),
            "REVIEW2-2",
        )


def part_s_approver() -> None:
    """The stored approver role is the actor's own role, whichever admin role approves."""
    for actor_name, role in (("admin", "workspace_admin"), ("org_admin", "organization_admin")):
        with sqlite_world() as w:
            _ds, m = seeded(w)
            done = w.approve(m, actor=getattr(w, actor_name))
            check(
                "APPROVER-1",
                f"{role} approval stores approver_role {role}",
                done.approver_role == role,
            )


def main() -> None:
    part_p()
    part_m()
    part_s_propose()
    part_s_approve()
    part_s_resolve()
    part_s_reads()
    part_s_variants()
    part_s_variants2()
    part_s_review()
    part_s_round2()
    part_s_approver()
    if FAILURES:
        for failure in FAILURES:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} check(s) failed", file=sys.stderr)
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
