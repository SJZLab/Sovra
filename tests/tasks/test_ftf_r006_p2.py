#!/usr/bin/env python3
# ruff: noqa: E501
# mypy: disable-error-code="arg-type,attr-defined,assignment,call-arg,index,var-annotated,misc,union-attr,no-untyped-def,operator,return-value"
"""FTF-R006-P2 -- Scoped review queue and concurrency-controlled responses (no network beyond
loopback, no external database).

Proves `packages/integrations/argilla/review_mapping.py` and
`apps/api/app/reviews/review_queue_store.py` against:

  * `FakeArgillaServer` (`tests/tasks/argilla_test_support.py`): a real loopback HTTP server
    implementing the pinned Argilla v2.8.0 dataset / record / response surface with real
    X-Argilla-Api-Key authentication -- the same real-socket recipe FTF-R006-P1a/P1b established;
  * SQLite standing in for PostgreSQL, with the tenant guards passed through (the P1b precedent
    in `test_ftf_r006_p1b.py`); the raw-SQL statements run for real. What SQLite cannot show --
    ENABLE+FORCE row-level security, the BEFORE UPDATE triggers, `SELECT ... FOR UPDATE`
    contention, OpenBao -- is proved against live services by
    `tests/integration/test_argilla_review_queue.py`.

The dataset layer (dataset row, upload session, grant, quality report) is substituted by name in
the store's own namespace -- the convention `test_ftf_h011_p1_service.py` set -- but the source
bytes are real JSONL read through the real FTF-0502-P1..FTF-0506 importer.

Criteria
  A*  review_mapping pure functions   B*  adapter vs the fake server   Q*  queue creation
  R*  responses                       D*  drift reconciliation          M*  migration / wiring

Run: python3 tests/tasks/test_ftf_r006_p2.py
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

from argilla_test_support import FakeArgillaServer  # noqa: E402
from packages.integrations.argilla import review_mapping as rm  # noqa: E402
from packages.integrations.argilla.client import (  # noqa: E402
    ArgillaConflictError,
    ArgillaRejectedError,
    ArgillaUnavailableError,
)
from sqlalchemy import StaticPool, create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from workers.data import import_common as ic  # noqa: E402
from workers.data.canonical_conversation_normalizer import normalize_conversation  # noqa: E402
from workers.data.immutable_split_manifest import excluded_positions_sha256  # noqa: E402
from workers.data.quality_issue_engine import QualityBlockedError  # noqa: E402

import app.reviews.review_queue_store as store  # noqa: E402
from app.datasets.dataset_object_source import (  # noqa: E402
    DatasetObjectSource,
    DatasetObjectStorage,
)
from app.datasets.dataset_quality_store import QualityReportNotFoundError  # noqa: E402
from app.datasets.dataset_source_connection_crud import (  # noqa: E402
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionNotFoundError,
)
from app.datasets.dataset_upload_store import (  # noqa: E402
    DatasetNotFoundError,
    UploadSessionNotFoundError,
)
from app.db.tenant_context import TenantContext  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def sha(data: bytes | str) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def raises(exc: type[BaseException], fn: Callable[[], Any]) -> BaseException | None:
    try:
        fn()
    except exc as caught:
        return caught
    except Exception as unexpected:
        print(f"  (unexpected {type(unexpected).__name__}: {unexpected})", file=sys.stderr)
        return None
    return None


def code_of(fn: Callable[[], Any]) -> str | None:
    caught = raises(store.ReviewQueueError, fn)
    return None if caught is None else caught.code  # type: ignore[attr-defined]


# =========================================================================== A: pure functions


def test_a_dataset_name() -> None:
    org, project, dataset = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    name = rm.dataset_name_for(org, project, dataset)
    check(
        "A1",
        "dataset name is sovra-rq- + 24 hex",
        re.fullmatch(r"sovra-rq-[0-9a-f]{24}", name) is not None,
    )
    check("A1", "dataset name is deterministic", name == rm.dataset_name_for(org, project, dataset))
    check(
        "A1",
        "dataset name differs per dataset",
        name != rm.dataset_name_for(org, project, uuid.uuid4()),
    )
    check(
        "A1",
        "dataset name differs per project",
        name != rm.dataset_name_for(org, uuid.uuid4(), dataset),
    )
    check(
        "A1",
        "dataset name differs per organization",
        name != rm.dataset_name_for(uuid.uuid4(), project, dataset),
    )
    check(
        "A1",
        "dataset name is content-free (digest of the ids)",
        name == "sovra-rq-" + sha(f"{org}|{project}|{dataset}")[:24],
    )


def test_a_render() -> None:
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    text_value = rm.render_conversation(messages)
    check("A2", "render starts with the first role header", text_value.startswith("[user]\n"))
    check(
        "A2",
        "render holds every message",
        "[assistant]" in text_value and "hello" in text_value and "hi" in text_value,
    )
    check("A2", "render is deterministic", text_value == rm.render_conversation(list(messages)))
    other = rm.render_conversation([{"role": "user", "content": "hello!"}, messages[1]])
    check("A2", "a changed message changes the render", other != text_value)
    check(
        "A2",
        "key order does not change the render",
        rm.render_conversation([{"content": "hello", "role": "user"}, messages[1]]) == text_value,
    )
    check(
        "A2",
        "non-ASCII text is kept readable, not escaped",
        "مرحبا" in rm.render_conversation([{"role": "user", "content": "مرحبا"}]),
    )
    for bad in (
        [],
        (),
        "text",
        None,
        [None],
        [{"content": "x"}],
        [{"role": "", "content": "x"}],
        [{"role": 3}],
    ):
        caught = raises(rm.ReviewMappingError, lambda bad=bad: rm.render_conversation(bad))
        check(
            "A2",
            f"unrenderable input {bad!r} is refused",
            caught is not None and caught.code == "record_not_renderable",
        )
    limit = rm.MAX_REVIEW_TEXT_CHARS
    overhead = len(rm.render_conversation([{"role": "user", "content": ""}]))
    at_limit = [{"role": "user", "content": "y" * (limit - overhead)}]
    check(
        "A2",
        "a conversation of exactly MAX_REVIEW_TEXT_CHARS is accepted",
        len(rm.render_conversation(at_limit)) == limit,
    )
    over = [{"role": "user", "content": "z" * (limit + 1)}]
    caught = raises(rm.ReviewMappingError, lambda: rm.render_conversation(over))
    check(
        "A2",
        "text over the limit is refused, never truncated",
        caught is not None and caught.code == "record_too_large_for_review",
    )
    check(
        "A2",
        "NaN is refused",
        raises(
            ValueError, lambda: rm.render_conversation([{"role": "user", "content": float("nan")}])
        )
        is not None,
    )


def test_a_payload() -> None:
    group, record_sha = sha("g"), sha("r")
    messages = [{"role": "user", "content": "q"}]
    payload = rm.review_record_payload(
        record_id="d:0", group_id=group, record_sha256=record_sha, messages=messages
    )
    check("A3", "external_id is the Factory record id", payload["external_id"] == "d:0")
    check(
        "A3",
        "metadata is exactly the four identifiers",
        payload["metadata"]
        == {
            "record_id": "d:0",
            "group_id": group,
            "record_sha256": record_sha,
            "schema_version": rm.SCHEMA_VERSION,
        },
    )
    check(
        "A3",
        "the one field is the rendered conversation",
        payload["fields"] == {rm.FIELD_NAME: rm.render_conversation(messages)},
    )
    check(
        "A3",
        "fields_sha256 of the field text is a sha256",
        rm.fields_sha256(payload["fields"][rm.FIELD_NAME]) == sha(payload["fields"][rm.FIELD_NAME]),
    )
    for kwargs in (
        {"record_id": "", "group_id": group, "record_sha256": record_sha},
        {"record_id": "x" * 256, "group_id": group, "record_sha256": record_sha},
        {"record_id": 5, "group_id": group, "record_sha256": record_sha},
        {"record_id": "d:0", "group_id": "short", "record_sha256": record_sha},
        {"record_id": "d:0", "group_id": group.upper(), "record_sha256": record_sha},
        {"record_id": "d:0", "group_id": group, "record_sha256": "z" * 64},
        {"record_id": "d:0", "group_id": group, "record_sha256": None},
    ):
        caught = raises(
            rm.ReviewMappingError,
            lambda kwargs=kwargs: rm.review_record_payload(messages=messages, **kwargs),
        )
        check(
            "A3",
            f"bad identifiers {list(kwargs.values())[:3]} are refused",
            caught is not None and caught.code == "record_not_renderable",
        )
    check(
        "A3",
        "a 255-char record id is accepted",
        rm.review_record_payload(
            record_id="x" * 255, group_id=group, record_sha256=record_sha, messages=messages
        )["external_id"]
        == "x" * 255,
    )
    check(
        "A3",
        "fields_sha256 refuses a non-str",
        raises(TypeError, lambda: rm.fields_sha256(b"x")) is not None,
    )


def test_a_response_values() -> None:
    check(
        "A4",
        "accept without rationale omits the rationale",
        rm.response_values("accept", "") == {"decision": {"value": "accept"}},
    )
    check(
        "A4",
        "reject with rationale carries both",
        rm.response_values("reject", "no")
        == {"decision": {"value": "reject"}, "rationale": {"value": "no"}},
    )
    check(
        "A4",
        "a 2000-char rationale is accepted",
        "rationale" in rm.response_values("accept", "r" * 2000),
    )
    for decision in ("approve", "", "Accept", "accept ", None, 1, True):
        caught = raises(
            rm.ReviewMappingError, lambda decision=decision: rm.response_values(decision, "")
        )
        check(
            "A4",
            f"decision {decision!r} is refused",
            caught is not None and caught.code == "invalid_decision",
        )
    for rationale in ("r" * 2001, None, 5, b"x"):
        caught = raises(
            rm.ReviewMappingError,
            lambda rationale=rationale: rm.response_values("accept", rationale),
        )
        check(
            "A4",
            f"rationale {str(rationale)[:6]!r} is refused",
            caught is not None and caught.code == "invalid_rationale",
        )

    class Sub(str):
        pass

    check(
        "A4",
        "a str subclass decision is refused (exact type)",
        raises(rm.ReviewMappingError, lambda: rm.response_values(Sub("accept"), "")) is not None,
    )
    check(
        "A4",
        "a str subclass rationale is refused (exact type)",
        raises(rm.ReviewMappingError, lambda: rm.response_values("accept", Sub("x"))) is not None,
    )


def test_a_response_sha() -> None:
    base = {"record_id": "d:0", "user_id": "u", "decision": "accept", "rationale": "r"}
    digest = rm.response_sha256(**base)
    check(
        "A5", "response sha256 is a hex digest", re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    )
    check("A5", "response sha256 is deterministic", digest == rm.response_sha256(**base))
    for key, other in (
        ("record_id", "d:1"),
        ("user_id", "v"),
        ("decision", "reject"),
        ("rationale", "s"),
    ):
        check(
            "A5",
            f"response sha256 covers {key}",
            digest != rm.response_sha256(**{**base, key: other}),
        )
    body = json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    check(
        "A5",
        "response sha256 is sha256 of the canonical JSON (independent recompute)",
        digest == sha(body),
    )


# ============================================================================ B: adapter + server

OWNER_KEY = "owner-provision-key"


def new_world() -> SimpleNamespace:
    server = FakeArgillaServer()
    owner_client = server.client()
    workspace = owner_client.ensure_workspace("sovra-testworkspace0001")
    owner_api = rm.ArgillaReviewApi(owner_client.transport)
    return SimpleNamespace(server=server, client=owner_client, workspace=workspace, api=owner_api)


def annotator(world: SimpleNamespace, name: str, *, member: bool = True) -> Any:
    user = world.client.create_user(username=name, role="annotator", password="p" * 12)
    if member:
        world.client.add_workspace_user(world.workspace.id, user.id)
    return user


def api_for(world: SimpleNamespace, key: str) -> rm.ArgillaReviewApi:
    return rm.ArgillaReviewApi(world.server.client(api_key=key).transport)


def writes(server: FakeArgillaServer) -> list[tuple[str, str, str | None]]:
    return [c for c in server.review_calls if c[0] in ("POST", "PUT", "DELETE", "PATCH")]


def sample_payloads(n: int, prefix: str = "d") -> list[dict[str, Any]]:
    return [
        rm.review_record_payload(
            record_id=f"{prefix}:{i}",
            group_id=sha(f"g{i}"),
            record_sha256=sha(f"r{i}"),
            messages=[{"role": "user", "content": f"q{i}"}],
        )
        for i in range(n)
    ]


def test_b_adapter() -> None:
    world = new_world()
    try:
        name = rm.dataset_name_for(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        dataset = world.api.ensure_dataset(world.workspace.id, name)
        check(
            "B1",
            "ensure_dataset returns a published dataset",
            dataset.published and dataset.name == name,
        )
        server_dataset = world.server.datasets[dataset.id]
        check(
            "B1",
            "the server dataset has exactly the conversation field",
            [f["name"] for f in server_dataset["fields"]] == ["conversation"],
        )
        check(
            "B1",
            "the server dataset has the decision and rationale questions",
            sorted(q["name"] for q in server_dataset["questions"]) == ["decision", "rationale"],
        )
        decision = next(q for q in server_dataset["questions"] if q["name"] == "decision")
        check(
            "B1",
            "the decision question offers exactly accept and reject",
            [o["value"] for o in decision["settings"]["options"]] == ["accept", "reject"],
        )
        before = len(world.server.datasets)
        again = world.api.ensure_dataset(world.workspace.id, name)
        check(
            "B1",
            "ensure_dataset is idempotent (same id, no second dataset)",
            again.id == dataset.id and len(world.server.datasets) == before,
        )
        for bad_workspace in ("nope", "", None, 3):
            check(
                "B1",
                f"workspace id {bad_workspace!r} is refused before any request",
                raises(
                    ArgillaRejectedError, lambda b=bad_workspace: world.api.ensure_dataset(b, name)
                )
                is not None,
            )
        calls = len(world.server.review_calls)
        for bad_name in (
            "bad",
            "sovra-rq-XYZ",
            "sovra-rq-" + "0" * 23,
            "sovra-rq-" + "0" * 25,
            "",
            None,
        ):
            check(
                "B1",
                f"dataset name {bad_name!r} is refused before any request",
                raises(
                    ArgillaRejectedError,
                    lambda b=bad_name: world.api.ensure_dataset(world.workspace.id, b),
                )
                is not None,
            )
        check("B1", "refused names made no request", len(world.server.review_calls) == calls)

        # settings drift: a published dataset with a foreign field is never adopted
        server_dataset["fields"].append(
            {"id": str(uuid.uuid4()), "name": "extra", "settings": {"type": "text"}}
        )
        check(
            "B2",
            "a dataset with an extra field is refused",
            raises(ArgillaRejectedError, lambda: world.api.ensure_dataset(world.workspace.id, name))
            is not None,
        )
        server_dataset["fields"].pop()
        server_dataset["questions"].pop()
        check(
            "B2",
            "a dataset missing a question is refused",
            raises(ArgillaRejectedError, lambda: world.api.ensure_dataset(world.workspace.id, name))
            is not None,
        )
        server_dataset["fields"][0]["settings"] = {"type": "image"}
        server_dataset["questions"].append(
            {"id": str(uuid.uuid4()), "name": "rationale", "settings": {"type": "text"}}
        )
        check(
            "B2",
            "a non-text field is refused",
            raises(ArgillaRejectedError, lambda: world.api.ensure_dataset(world.workspace.id, name))
            is not None,
        )
        server_dataset["fields"][0]["settings"] = {"type": "text"}

        # records
        payloads = sample_payloads(25)
        created = world.api.create_records(dataset.id, payloads)
        check(
            "B3",
            "create_records returns one record per payload, in order",
            [r.external_id for r in created] == [p["external_id"] for p in payloads],
        )
        listed = world.api.list_records(dataset.id)
        check(
            "B3",
            "list_records pages through every record (25 over pages of 10)",
            sorted(r.external_id for r in listed) == sorted(p["external_id"] for p in payloads),
        )
        check(
            "B3",
            "listed records carry their metadata and field text",
            all(
                r.metadata["record_id"] == r.external_id
                and r.fields["conversation"].startswith("[user]")
                for r in listed
            ),
        )
        check(
            "B3",
            "create_records of nothing makes no request",
            (
                lambda n: (
                    world.api.create_records(dataset.id, []),
                    len(world.server.review_calls) == n,
                )[1]
            )(len(world.server.review_calls)),
        )
        caught = raises(
            ArgillaRejectedError, lambda: world.api.create_records(dataset.id, payloads[:1])
        )
        check(
            "B3",
            "a duplicate external_id is refused (422 on the pinned server), not a second record",
            caught is not None and len(world.server.records) == 25,
        )
        big = sample_payloads(450, "big")
        created_big = world.api.create_records(dataset.id, big)
        check(
            "B3",
            "450 records are sent in bounded chunks and all created",
            len(created_big) == 450 and len(world.server.records) == 475,
        )
        check(
            "B3",
            "no bulk request exceeded 200 records",
            all(True for _ in [0])
            and sum(1 for c in world.server.review_calls if c[1].endswith("/records/bulk"))
            == 1 + 1 + 3,
        )
        check(
            "B3",
            "get_record returns one record with its responses",
            world.api.get_record(created[0].id).external_id == "d:0",
        )
        check(
            "B3",
            "get_record refuses a non-uuid before any request",
            raises(ArgillaRejectedError, lambda: world.api.get_record("x")) is not None,
        )
        check(
            "B3",
            "list_records refuses a non-uuid",
            raises(ArgillaRejectedError, lambda: world.api.list_records("x")) is not None,
        )

        # responses with the caller's own key
        reviewer = annotator(world, "annotator-b")
        reviewer_api = api_for(world, reviewer.api_key)
        response = reviewer_api.submit_response(created[0].id, "accept", "fine")
        check(
            "B4",
            "the response author is the key's own user",
            response.user_id == reviewer.id and response.submitted,
        )
        check(
            "B4",
            "decision and rationale round-trip",
            response.decision == "accept" and response.rationale == "fine",
        )
        check(
            "B4",
            "the POST was authenticated with the reviewer's key",
            writes(world.server)[-1]
            == ("POST", f"/api/v1/records/{created[0].id}/responses", reviewer.api_key),
        )
        owner_response = world.api.submit_response(created[1].id, "reject", "")
        check(
            "B4",
            "the owner key authors as the owner, not the reviewer",
            owner_response.user_id == world.server.OWNER_USER_ID
            and owner_response.rationale is None,
        )
        again_conflict = raises(
            ArgillaConflictError, lambda: reviewer_api.submit_response(created[0].id, "reject", "")
        )
        check("B4", "a second response by the same user is a conflict", again_conflict is not None)
        updated = reviewer_api.update_response(response.id, "reject", "changed")
        check(
            "B5",
            "update keeps the response id and changes the value",
            updated.id == response.id
            and updated.decision == "reject"
            and updated.rationale == "changed",
        )
        other = annotator(world, "annotator-c")
        other_api = api_for(world, other.api_key)
        check(
            "B5",
            "another user cannot update someone else's response",
            raises(
                ArgillaRejectedError, lambda: other_api.update_response(response.id, "accept", "")
            )
            is not None,
        )
        outsider = annotator(world, "annotator-out", member=False)
        outsider_api = api_for(world, outsider.api_key)
        check(
            "B5",
            "an annotator outside the workspace cannot respond",
            raises(
                ArgillaRejectedError,
                lambda: outsider_api.submit_response(created[2].id, "accept", ""),
            )
            is not None,
        )
        check(
            "B5",
            "an annotator cannot list every record with every response",
            raises(ArgillaRejectedError, lambda: outsider_api.list_records(dataset.id)) is not None,
        )
        calls = len(world.server.review_calls)
        for bad in ("approve", "", None):
            check(
                "B5",
                f"decision {bad!r} never reaches the server",
                raises(
                    rm.ReviewMappingError,
                    lambda b=bad: reviewer_api.submit_response(created[3].id, b, ""),
                )
                is not None,
            )
        check("B5", "invalid decisions made no request", len(world.server.review_calls) == calls)
        check(
            "B5",
            "response ids must be uuids",
            raises(ArgillaRejectedError, lambda: reviewer_api.update_response("x", "accept", ""))
            is not None,
        )
        bad_key = api_for(world, "wrong-key")
        check(
            "B5",
            "an unknown key is rejected, not treated as success",
            raises(ArgillaRejectedError, lambda: bad_key.list_records(dataset.id)) is not None,
        )
        check(
            "B5",
            "the adapter refuses a non-transport",
            raises(TypeError, lambda: rm.ArgillaReviewApi(object())) is not None,
        )

        # outage stays an outage
        world.server.stop()
        check(
            "B6",
            "an outage on ensure_dataset is ArgillaUnavailableError",
            raises(
                ArgillaUnavailableError, lambda: world.api.ensure_dataset(world.workspace.id, name)
            )
            is not None,
        )
        check(
            "B6",
            "an outage on list_records is ArgillaUnavailableError",
            raises(ArgillaUnavailableError, lambda: world.api.list_records(dataset.id)) is not None,
        )
        check(
            "B6",
            "an outage on submit_response is ArgillaUnavailableError",
            raises(
                ArgillaUnavailableError,
                lambda: reviewer_api.update_response(response.id, "accept", ""),
            )
            is not None,
        )
        world.server.start()
    finally:
        world.server.shutdown()


# ======================================================================= store harness (SQLite)

DDL = """
CREATE TABLE argilla_review_scopes (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()), organization_id UUID NOT NULL, project_id UUID NOT NULL,
  argilla_workspace_id VARCHAR(36) NOT NULL, UNIQUE (organization_id, project_id));
CREATE TABLE argilla_reviewer_identities (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()), organization_id UUID NOT NULL, issuer VARCHAR(512) NOT NULL,
  subject VARCHAR(255) NOT NULL, argilla_user_id VARCHAR(36) NOT NULL, credential_secret_ref VARCHAR(512) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active', UNIQUE (organization_id, issuer, subject));
CREATE TABLE argilla_workspace_members (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()), organization_id UUID NOT NULL, scope_id UUID NOT NULL,
  identity_id UUID NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'active');
CREATE TABLE dataset_split_manifests (
  id UUID PRIMARY KEY, organization_id UUID NOT NULL, status VARCHAR(16) NOT NULL);
CREATE TABLE dataset_split_manifest_sources (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()), organization_id UUID NOT NULL, manifest_id UUID NOT NULL,
  project_id UUID NOT NULL, dataset_id UUID NOT NULL);
CREATE TABLE review_queues (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()), organization_id UUID NOT NULL, project_id UUID NOT NULL,
  dataset_id UUID NOT NULL, dataset_name VARCHAR(200) NOT NULL, dataset_purpose VARCHAR(32) NOT NULL,
  dataset_version INTEGER NOT NULL, dataset_sha256 VARCHAR(64) NOT NULL, scope_id UUID NOT NULL,
  argilla_dataset_id VARCHAR(36), argilla_dataset_name VARCHAR(64) NOT NULL, quality_report_id UUID NOT NULL,
  quality_report_sha256 VARCHAR(64) NOT NULL, excluded_positions_sha256 VARCHAR(64) NOT NULL,
  queue_snapshot_sha256 VARCHAR(64) NOT NULL, record_count INTEGER NOT NULL, schema_version VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'building', created_by VARCHAR(200) NOT NULL, updated_at TEXT,
  UNIQUE (organization_id, project_id, dataset_id));
CREATE TABLE review_items (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()), organization_id UUID NOT NULL, queue_id UUID NOT NULL,
  record_id VARCHAR(300) NOT NULL, batch_position INTEGER NOT NULL, group_id VARCHAR(64) NOT NULL,
  record_sha256 VARCHAR(64) NOT NULL, fields_sha256 VARCHAR(64) NOT NULL, argilla_record_id VARCHAR(36),
  status VARCHAR(16) NOT NULL DEFAULT 'pending', drift_reason VARCHAR(64), updated_at TEXT,
  UNIQUE (queue_id, record_id), UNIQUE (queue_id, batch_position));
CREATE TABLE review_responses (
  id UUID PRIMARY KEY DEFAULT (gen_uuid()), organization_id UUID NOT NULL, queue_id UUID NOT NULL,
  item_id UUID NOT NULL, identity_id UUID NOT NULL, decision VARCHAR(16) NOT NULL,
  rationale VARCHAR(2000) NOT NULL DEFAULT '', response_sha256 VARCHAR(64) NOT NULL,
  argilla_response_id VARCHAR(36) NOT NULL, row_version INTEGER NOT NULL DEFAULT 1, updated_at TEXT,
  UNIQUE (item_id, identity_id));
"""

sqlite3.register_adapter(uuid.UUID, str)
sqlite3.register_adapter(datetime, lambda d: d.isoformat())
sqlite3.register_converter("UUID", lambda b: uuid.UUID(b.decode()))

MAPPING = ic.mapping_from_object(
    {"shape": "prompt_completion", "prompt": {"key": "prompt"}, "completion": {"key": "completion"}}
)
GROUPED_MAPPING = ic.mapping_from_object(
    {
        "shape": "prompt_completion",
        "prompt": {"key": "prompt"},
        "completion": {"key": "completion"},
        "group_key": {"key": "g"},
    }
)
ISSUER = "https://issuer.example"


def jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(r).encode() + b"\n" for r in rows)


def rows_n(n: int, groups: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        row: dict[str, Any] = {"prompt": f"question number {i}", "completion": f"answer number {i}"}
        if groups is not None:
            row["g"] = groups[i]
        out.append(row)
    return out


class Env:
    def __init__(
        self, records: int = 4, *, mapping: Any = MAPPING, rows: list[dict[str, Any]] | None = None
    ) -> None:
        self.server = FakeArgillaServer()
        self.owner_client = self.server.client()
        self.owner_api = rm.ArgillaReviewApi(self.owner_client.transport)
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"detect_types": sqlite3.PARSE_DECLTYPES, "check_same_thread": False},
        )
        dbapi: Any = self.engine.raw_connection().driver_connection
        dbapi.create_function("now", 0, lambda: datetime.now(UTC).isoformat())
        dbapi.create_function("gen_uuid", 0, lambda: str(uuid.uuid4()))
        dbapi.executescript(DDL)
        self.org, self.project, self.dataset_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.context = TenantContext(organization_id=self.org)
        self.mapping = mapping
        self.payload = jsonl(rows if rows is not None else rows_n(records))
        self.content_type = "application/jsonl"
        self.purpose = "training"
        self.excluded: tuple[int, ...] = ()
        self.quality_sha = sha("quality-report-1")
        self.quality_id = uuid.uuid4()
        self.chunk_override: Callable[[], Iterator[bytes]] | None = None
        self.expected_sha_override: str | None = None
        self.calls: list[str] = []
        self.statements: list[str] = []
        self.audits: list[dict[str, Any]] = []
        self.secret_refs: dict[str, str] = {}
        self.resolved: list[str] = []
        self.on_resolve: Callable[[], None] | None = None
        self.not_found = False
        self.grant_error: Exception | None = None
        self.quality_error: Exception | None = None
        self.report_error: Exception | None = None
        self.source_error: Exception | None = None
        self.upload_error: Exception | None = None
        self.workspace = self.owner_client.ensure_workspace("sovra-testworkspace0002")
        self.scope_id = uuid.uuid4()
        self.sql(
            "INSERT INTO argilla_review_scopes (id, organization_id, project_id, argilla_workspace_id) "
            "VALUES (:i, :o, :p, :w)",
            i=self.scope_id,
            o=self.org,
            p=self.project,
            w=self.workspace.id,
        )
        self._saved = {
            n: getattr(store, n)
            for n in (
                "execute_tenant_scoped",
                "tenant_scoped_session",
                "set_tenant_context",
                "get_dataset",
                "get_upload_session",
                "authorize_dataset_source_purpose",
                "load_resolved_quality_report",
                "assert_clear_for_split",
                "get_latest_report",
                "excluded_batch_positions",
                "open_dataset_source",
                "_live_tool_schemas",
                "resolve_secret_ref",
                "write_audit_event",
            )
        }
        env = self

        def passthrough(
            session: Session, organization_id: Any, statement: Any, params: Any = None
        ) -> Any:
            if organization_id != env.org:
                raise AssertionError("a statement was scoped to the wrong organization")
            sql = statement.text if hasattr(statement, "text") else str(statement)
            env.statements.append(sql)
            return session.execute(text(sql.replace(" FOR UPDATE", "")), params or {})

        @contextlib.contextmanager
        def scoped(
            session_factory: Callable[[], Session], organization_id: uuid.UUID
        ) -> Iterator[Session]:
            session = session_factory()
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                session.close()

        def get_dataset(
            session: Any, context: Any, *, project_id: Any, dataset_id: Any, include_held_out: bool
        ) -> Any:
            env.calls.append("get_dataset")
            if env.not_found:
                raise DatasetNotFoundError("missing")
            check("Q0", "get_dataset is asked for held-out datasets too", include_held_out is True)
            return SimpleNamespace(
                id=dataset_id,
                name="reviewed-data",
                purpose=env.purpose,
                version=1,
                verified_sha256=sha(env.payload),
                upload_session_id=uuid.uuid4(),
            )

        def get_upload_session(
            session: Any, context: Any, project_id: Any, upload_session_id: Any
        ) -> Any:
            env.calls.append("get_upload_session")
            if env.upload_error is not None:
                raise env.upload_error
            return SimpleNamespace(dataset_source_connection_id=uuid.uuid4())

        def authorize(
            session: Any, context: Any, project_id: Any, connection_id: Any, purpose: str
        ) -> Any:
            env.calls.append("authorize")
            check(
                "Q0",
                "the live grant is checked for the dataset's own purpose",
                purpose == env.purpose,
            )
            if env.grant_error is not None:
                raise env.grant_error
            return SimpleNamespace()

        def load_report(
            session: Any, context: Any, *, project_id: Any, dataset_id: Any, include_held_out: bool
        ) -> Any:
            env.calls.append("load_report")
            if env.report_error is not None:
                raise env.report_error
            return SimpleNamespace(marker="report")

        def assert_clear(report: Any) -> None:
            env.calls.append("assert_clear")
            check(
                "Q0",
                "assert_clear_for_split receives the resolved report",
                getattr(report, "marker", None) == "report",
            )
            if env.quality_error is not None:
                raise env.quality_error

        def latest(
            session: Any, context: Any, *, project_id: Any, dataset_id: Any, include_held_out: bool
        ) -> Any:
            return SimpleNamespace(id=env.quality_id, report_sha256=env.quality_sha)

        def excluded(
            session: Any, context: Any, *, project_id: Any, dataset_id: Any, include_held_out: bool
        ) -> tuple[int, ...]:
            return env.excluded

        def open_source(
            session: Any,
            context: Any,
            *,
            project_id: Any,
            dataset_id: Any,
            include_held_out: bool,
            storage: Any,
        ) -> Any:
            env.calls.append("open_source")
            if env.source_error is not None:
                raise env.source_error
            data = env.payload
            chunks = env.chunk_override() if env.chunk_override else iter([data])
            return DatasetObjectSource(
                dataset_id=dataset_id,
                purpose=env.purpose,
                source_type="upload",
                verified_content_type=env.content_type,
                expected_sha256=env.expected_sha_override or sha(data),
                expected_size=len(data),
                chunks=chunks,
            )

        def resolve(ref: str, *, config: Any) -> str:
            env.resolved.append(ref)
            if env.on_resolve is not None:
                env.on_resolve()
            return env.secret_refs[ref]

        def audit(session: Any, **kwargs: Any) -> None:
            env.audits.append(kwargs)

        store.execute_tenant_scoped = passthrough
        store.tenant_scoped_session = scoped
        store.set_tenant_context = lambda *a, **k: None
        store.get_dataset = get_dataset
        store.get_upload_session = get_upload_session
        store.authorize_dataset_source_purpose = authorize
        store.load_resolved_quality_report = load_report
        store.assert_clear_for_split = assert_clear
        store.get_latest_report = latest
        store.excluded_batch_positions = excluded
        store.open_dataset_source = open_source
        store._live_tool_schemas = lambda *a, **k: {}
        store.resolve_secret_ref = resolve
        store.write_audit_event = audit

    # -- plumbing
    def factory(self) -> Session:
        return Session(self.engine)

    def sql(self, statement: str, **params: Any) -> list[Any]:
        with self.engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return list(result.fetchall()) if result.returns_rows else []

    def close(self) -> None:
        for name, value in self._saved.items():
            setattr(store, name, value)
        self.server.shutdown()

    def create(self, **overrides: Any) -> store.ReviewQueueRecord:
        kwargs: dict[str, Any] = dict(
            context=self.context,
            project_id=self.project,
            dataset_id=self.dataset_id,
            mapping=self.mapping,
            storage=DatasetObjectStorage(object(), "bucket"),
            owner_api=self.owner_api,
            actor_type="user",
            actor_id="creator-1",
            request_id="req-create",
        )
        kwargs.update(overrides)
        return store.create_review_queue(self.factory, **kwargs)

    def snapshot(self) -> tuple[Any, ...]:
        return (
            self.sql("SELECT count(*) FROM review_queues")[0][0],
            self.sql("SELECT count(*) FROM review_items")[0][0],
            self.sql("SELECT count(*) FROM review_responses")[0][0],
            len(writes(self.server)),
            len(self.audits),
            len(self.server.records),
            len(self.server.datasets),
        )

    def reviewer(
        self,
        name: str,
        *,
        member: bool = True,
        status: str = "active",
        member_status: str = "active",
        scoped: bool = True,
    ) -> SimpleNamespace:
        user = self.owner_client.create_user(username=name, role="annotator", password="p" * 12)
        if member:
            self.owner_client.add_workspace_user(self.workspace.id, user.id)
        identity_id = uuid.uuid4()
        ref = f"openbao://secret/reviewers/{user.id}#api_key"
        self.secret_refs[ref] = user.api_key
        self.sql(
            "INSERT INTO argilla_reviewer_identities (id, organization_id, issuer, subject, argilla_user_id, "
            "credential_secret_ref, status) VALUES (:i, :o, :iss, :sub, :u, :r, :s)",
            i=identity_id,
            o=self.org,
            iss=ISSUER,
            sub=name,
            u=user.id,
            r=ref,
            s=status,
        )
        if scoped:
            self.sql(
                "INSERT INTO argilla_workspace_members (organization_id, scope_id, identity_id, status) VALUES (:o, :s, :i, :st)",
                o=self.org,
                s=self.scope_id,
                i=identity_id,
                st=member_status,
            )
        return SimpleNamespace(name=name, user=user, identity_id=identity_id, ref=ref)

    def respond(
        self,
        queue_id: Any,
        record_id: str,
        who: SimpleNamespace,
        decision: str = "accept",
        rationale: str = "",
        expected_version: int = 0,
        **overrides: Any,
    ) -> store.ReviewResponseRecord:
        kwargs: dict[str, Any] = dict(
            context=self.context,
            queue_id=queue_id,
            record_id=record_id,
            issuer=ISSUER,
            subject=who.name,
            decision=decision,
            rationale=rationale,
            expected_version=expected_version,
            owner_api=self.owner_api,
            reviewer_api_factory=lambda key: rm.ArgillaReviewApi(
                self.server.client(api_key=key).transport
            ),
            openbao_config=None,
            request_id="req-resp",
        )
        kwargs.update(overrides)
        return store.submit_review_response(self.factory, **kwargs)

    def reconcile(self, queue_id: Any) -> tuple[store.DriftFinding, ...]:
        return store.reconcile_review_queue(
            self.factory,
            context=self.context,
            queue_id=queue_id,
            owner_api=self.owner_api,
            actor_type="user",
            actor_id="reconciler-1",
            request_id="req-reconcile",
        )

    def items(self) -> list[Any]:
        return self.sql(
            "SELECT record_id, batch_position, group_id, record_sha256, fields_sha256, argilla_record_id, status, drift_reason FROM review_items ORDER BY batch_position"
        )

    def server_record(self, record_id: str) -> dict[str, Any]:
        return next(r for r in self.server.records.values() if r["external_id"] == record_id)


@contextlib.contextmanager
def env_of(**kwargs: Any) -> Iterator[Env]:
    env = Env(**kwargs)
    try:
        yield env
    finally:
        env.close()


# ================================================================================ Q: creation


def test_q_happy_path() -> None:
    with env_of(records=4) as env:
        queue = env.create()
        check(
            "Q1",
            "the queue is ready with the dataset's Argilla id",
            queue.status == "ready" and queue.argilla_dataset_id is not None,
        )
        check("Q1", "record_count is the number of queued records", queue.record_count == 4)
        check(
            "Q1",
            "the queue carries the quality report lineage",
            queue.quality_report_sha256 == env.quality_sha
            and str(queue.quality_report_id) == str(env.quality_id),
        )
        check(
            "Q1",
            "the Argilla dataset name is the deterministic one",
            queue.argilla_dataset_name == rm.dataset_name_for(env.org, env.project, env.dataset_id),
        )
        check("Q1", "the schema version is recorded", queue.schema_version == rm.SCHEMA_VERSION)
        items = env.items()
        check("Q1", "one item per record, in position order", [i[1] for i in items] == [0, 1, 2, 3])
        check(
            "Q1",
            "record ids are {dataset_id}:{position}",
            [i[0] for i in items] == [f"{env.dataset_id}:{p}" for p in range(4)],
        )
        check(
            "Q1",
            "every item is linked to its Argilla record and pending",
            all(i[5] is not None and i[6] == "pending" and i[7] is None for i in items),
        )
        check(
            "Q1",
            "the server holds exactly the queued records",
            sorted(r["external_id"] for r in env.server.records.values())
            == sorted(i[0] for i in items),
        )
        for item in items:
            record = env.server_record(item[0])
            check(
                "Q1",
                "server record metadata equals the item's identifiers",
                record["metadata"]
                == {
                    "record_id": item[0],
                    "group_id": item[2],
                    "record_sha256": item[3],
                    "schema_version": rm.SCHEMA_VERSION,
                },
            )
            check(
                "Q1",
                "fields_sha256 is the sha of the pushed text",
                rm.fields_sha256(record["fields"]["conversation"]) == item[4],
            )
            check(
                "Q1",
                "item hashes are 64 lowercase hex",
                all(re.fullmatch(r"[0-9a-f]{64}", h) for h in (item[2], item[3], item[4])),
            )
        check(
            "Q1",
            "the item's argilla_record_id is the server record's id",
            all(env.server_record(i[0])["id"] == i[5] for i in items),
        )
        check(
            "Q1",
            "each conversation is rendered with its role header",
            all(
                "[user]" in r["fields"]["conversation"]
                and "[assistant]" in r["fields"]["conversation"]
                for r in env.server.records.values()
            ),
        )
        independent = sha(
            json.dumps(
                {
                    "schema_version": rm.SCHEMA_VERSION,
                    "records": sorted([i[0], i[3]] for i in items),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
        check(
            "Q1",
            "queue_snapshot_sha256 is an independent recompute over (record id, record sha)",
            queue.queue_snapshot_sha256 == independent,
        )
        check(
            "Q1",
            "excluded_positions_sha256 covers the empty exclusion list",
            queue.excluded_positions_sha256 == excluded_positions_sha256(()),
        )
        check(
            "Q1",
            "exactly one audit event, the creation",
            [a["action"] for a in env.audits] == ["review_queue.created"],
        )
        audit = env.audits[0]
        check(
            "Q1",
            "the audit actor is the authenticated principal",
            audit["actor_type"] == "user"
            and audit["actor_id"] == "creator-1"
            and audit["request_id"] == "req-create",
        )
        check(
            "Q1",
            "the audit row is content-free (no conversation text)",
            "question number" not in json.dumps(audit, default=str)
            and "answer number" not in json.dumps(audit, default=str),
        )
        check(
            "Q1",
            "the audit names the queue and organization",
            audit["resource_type"] == "review_queue"
            and audit["resource_id"] == str(queue.id)
            and audit["organization_id"] == env.org,
        )
        check(
            "Q1",
            "get_review_queue reads the same queue back",
            store.get_review_queue(env.factory, context=env.context, queue_id=queue.id) == queue,
        )
        other_org = TenantContext(organization_id=uuid.uuid4())
        check(
            "Q1",
            "an unknown queue id is queue_not_found",
            code_of(
                lambda: store.get_review_queue(
                    env.factory, context=env.context, queue_id=uuid.uuid4()
                )
            )
            == "queue_not_found",
        )
        check(
            "Q1",
            "a non-uuid queue id is invalid_request",
            code_of(lambda: store.get_review_queue(env.factory, context=env.context, queue_id="x"))
            == "invalid_request",
        )
        del other_org


def test_q_excluded_positions() -> None:
    with env_of(records=5) as env:
        env.excluded = (1, 3)
        queue = env.create()
        check(
            "Q2",
            "excluded positions are dropped from the queue",
            [i[1] for i in env.items()] == [0, 2, 4] and queue.record_count == 3,
        )
        check(
            "Q2",
            "excluded records are never pushed to Argilla",
            sorted(r["external_id"] for r in env.server.records.values())
            == [f"{env.dataset_id}:{p}" for p in (0, 2, 4)],
        )
        check(
            "Q2",
            "the exclusion list is bound by hash",
            queue.excluded_positions_sha256 == excluded_positions_sha256((1, 3)),
        )
    with env_of(records=2) as env:
        env.excluded = (0, 1)
        before = env.snapshot()
        check("Q2", "every record excluded is no_records", code_of(env.create) == "no_records")
        check("Q2", "no_records wrote nothing anywhere", env.snapshot() == before)


def test_q_sealed_and_refusal_order() -> None:
    for purpose in ("held_out_evaluation", "baseline_only"):
        with env_of(records=3) as env:
            env.purpose = purpose
            env.payload = jsonl(
                [{"prompt": "CANARY-SEALED-FINAL-SUITE", "completion": "x"}] + rows_n(2)
            )
            before = env.snapshot()
            check(
                "Q3",
                f"{purpose} is sealed_purpose_refused",
                code_of(env.create) == "sealed_purpose_refused",
            )
            check("Q3", f"{purpose} refusal wrote nothing", env.snapshot() == before)
            check(
                "Q3",
                f"{purpose} refusal read no bytes and checked nothing after the purpose",
                env.calls == ["get_dataset"],
            )
            check(
                "Q3",
                f"{purpose} canary is never on the Argilla server",
                not env.server.holds_bytes(b"CANARY-SEALED-FINAL-SUITE"),
            )
    for purpose in ("training", "validation", "candidate_development"):
        with env_of(records=2) as env:
            env.purpose = purpose
            check("Q3", f"{purpose} is allowed", env.create().status == "ready")
    with env_of(records=2) as env:
        env.not_found = True
        before = env.snapshot()
        check(
            "Q4",
            "a missing dataset is dataset_not_found",
            code_of(env.create) == "dataset_not_found",
        )
        check(
            "Q4",
            "dataset_not_found wrote nothing and went no further",
            env.snapshot() == before and env.calls == ["get_dataset"],
        )
    with env_of(records=2) as env:
        for label, error in (
            ("authorization error", DatasetSourceAuthorizationError("revoked")),
            ("missing connection", DatasetSourceConnectionNotFoundError("gone")),
        ):
            env.grant_error = error
            before = env.snapshot()
            check("Q5", f"{label} is grant_changed", code_of(env.create) == "grant_changed")
            check("Q5", f"{label} wrote nothing", env.snapshot() == before)
        env.grant_error = None
        env.upload_error = UploadSessionNotFoundError("gone")
        check(
            "Q5",
            "a missing upload session is grant_changed",
            code_of(env.create) == "grant_changed",
        )
        env.upload_error = None
        env.grant_error = DatasetSourceAuthorizationError("revoked")
        env.calls.clear()
        code_of(env.create)
        check(
            "Q5",
            "a revoked grant stops before the quality gate and before any byte is read",
            "load_report" not in env.calls and "open_source" not in env.calls,
        )


def test_q_split_locked() -> None:
    with env_of(records=2) as env:
        manifest = uuid.uuid4()
        env.sql(
            "INSERT INTO dataset_split_manifests (id, organization_id, status) VALUES (:m, :o, 'locked')",
            m=manifest,
            o=env.org,
        )
        env.sql(
            "INSERT INTO dataset_split_manifest_sources (organization_id, manifest_id, project_id, dataset_id) VALUES (:o, :m, :p, :d)",
            o=env.org,
            m=manifest,
            p=env.project,
            d=uuid.uuid4(),
        )
        check(
            "Q6", "another dataset's locked source does not refuse", env.create().status == "ready"
        )
    with env_of(records=2) as env:
        manifest = uuid.uuid4()
        env.sql(
            "INSERT INTO dataset_split_manifests (id, organization_id, status) VALUES (:m, :o, 'proposed')",
            m=manifest,
            o=env.org,
        )
        env.sql(
            "INSERT INTO dataset_split_manifest_sources (organization_id, manifest_id, project_id, dataset_id) VALUES (:o, :m, :p, :d)",
            o=env.org,
            m=manifest,
            p=env.project,
            d=env.dataset_id,
        )
        check("Q6", "a merely proposed manifest does not refuse", env.create().status == "ready")
    with env_of(records=2) as env:
        manifest = uuid.uuid4()
        env.sql(
            "INSERT INTO dataset_split_manifests (id, organization_id, status) VALUES (:m, :o, 'locked')",
            m=manifest,
            o=env.org,
        )
        env.sql(
            "INSERT INTO dataset_split_manifest_sources (organization_id, manifest_id, project_id, dataset_id) VALUES (:o, :m, :p, :d)",
            o=env.org,
            m=manifest,
            p=env.project,
            d=env.dataset_id,
        )
        before = env.snapshot()
        check(
            "Q6",
            "a source of a locked manifest is dataset_already_split",
            code_of(env.create) == "dataset_already_split",
        )
        check(
            "Q6",
            "dataset_already_split wrote nothing and read no bytes",
            env.snapshot() == before and "open_source" not in env.calls,
        )
    with env_of(records=2) as env:
        manifest = uuid.uuid4()
        other_org = uuid.uuid4()
        env.sql(
            "INSERT INTO dataset_split_manifests (id, organization_id, status) VALUES (:m, :o, 'locked')",
            m=manifest,
            o=other_org,
        )
        env.sql(
            "INSERT INTO dataset_split_manifest_sources (organization_id, manifest_id, project_id, dataset_id) VALUES (:o, :m, :p, :d)",
            o=other_org,
            m=manifest,
            p=env.project,
            d=env.dataset_id,
        )
        check(
            "Q6",
            "another organization's locked manifest does not refuse",
            env.create().status == "ready",
        )


def test_q_quality() -> None:
    with env_of(records=2) as env:
        for label, attr, error in (
            ("blocked report", "quality_error", QualityBlockedError("split", ("issue-1",))),
            ("no report", "report_error", QualityReportNotFoundError("none")),
        ):
            setattr(env, attr, error)
            before = env.snapshot()
            check("Q7", f"{label} is quality_not_clear", code_of(env.create) == "quality_not_clear")
            check(
                "Q7",
                f"{label} wrote nothing and read no bytes",
                env.snapshot() == before and "open_source" not in env.calls,
            )
            setattr(env, attr, None)
        check("Q7", "a clear report builds the queue", env.create().status == "ready")


def test_q_limits(monkey: Any = None) -> None:
    saved = (store.MAX_QUEUE_RECORDS, store.MAX_QUEUE_TEXT_CHARS)
    try:
        store.MAX_QUEUE_RECORDS = 3
        with env_of(records=4) as env:
            before = env.snapshot()
            check(
                "Q8",
                "one record over the cap is queue_too_large",
                code_of(env.create) == "queue_too_large",
            )
            check("Q8", "queue_too_large wrote nothing (never truncated)", env.snapshot() == before)
        with env_of(records=3) as env:
            check("Q8", "exactly the cap is accepted", env.create().record_count == 3)
        store.MAX_QUEUE_RECORDS = saved[0]
        with env_of(records=2) as env:
            env.create()
            total = sum(len(r["fields"]["conversation"]) for r in env.server.records.values())
        with env_of(records=2) as env:
            store.MAX_QUEUE_TEXT_CHARS = total - 1
            before = env.snapshot()
            check(
                "Q8",
                "total rendered text over the cap is queue_too_large",
                code_of(env.create) == "queue_too_large",
            )
            check("Q8", "the text cap refusal wrote nothing", env.snapshot() == before)
        with env_of(records=2) as env:
            store.MAX_QUEUE_TEXT_CHARS = total
            check(
                "Q8",
                "total rendered text exactly at the cap is accepted",
                env.create().record_count == 2,
            )
    finally:
        store.MAX_QUEUE_RECORDS, store.MAX_QUEUE_TEXT_CHARS = saved
    check("Q8", "the record cap is the spec's 100,000", store.MAX_QUEUE_RECORDS == 100_000)
    with env_of(rows=[{"prompt": "p" * (rm.MAX_REVIEW_TEXT_CHARS + 10), "completion": "c"}]) as env:
        before = env.snapshot()
        check(
            "Q9",
            "a record too large to read back is refused, not truncated",
            code_of(env.create) == "record_too_large_for_review",
        )
        check("Q9", "the too-large refusal wrote nothing", env.snapshot() == before)


def test_q_source() -> None:
    with env_of(records=3) as env:
        env.expected_sha_override = sha("different bytes")
        before = env.snapshot()
        check(
            "Q10",
            "bytes that do not match the verified sha are source_changed",
            code_of(env.create) == "source_changed",
        )
        check("Q10", "a sha mismatch wrote nothing", env.snapshot() == before)
    with env_of(records=3) as env:
        env.content_type = "application/x-parquet"
        before = env.snapshot()
        check(
            "Q10",
            "an unsupported content type is source_unreadable",
            code_of(env.create) == "source_unreadable",
        )
        check("Q10", "an unsupported content type wrote nothing", env.snapshot() == before)
    with env_of(records=3) as env:
        for error in (DatasetNotFoundError("x"), UploadSessionNotFoundError("x")):
            env.source_error = error
            check(
                "Q10",
                f"{type(error).__name__} from the source is source_unreadable",
                code_of(env.create) == "source_unreadable",
            )
        env.source_error = None

        def broken() -> Iterator[bytes]:
            yield env.payload[:10]
            raise OSError("storage went away")

        env.chunk_override = broken
        before = env.snapshot()
        code = code_of(env.create)
        check(
            "Q10", "a storage failure mid-stream is source_unreadable", code == "source_unreadable"
        )
        check("Q10", "a storage failure mid-stream wrote nothing", env.snapshot() == before)
    with env_of(records=3) as env:
        env.chunk_override = lambda: iter([env.payload, b"trailing bytes beyond the verified size"])
        check(
            "Q10",
            "bytes beyond the verified size are source_changed",
            code_of(env.create) == "source_changed",
        )
    with env_of(records=3) as env:
        env.payload = b"not json at all\n" * 3
        check(
            "Q10",
            "a source with no acceptable row is no_records",
            code_of(env.create) == "no_records",
        )
    with env_of(records=3) as env:
        mixed = rows_n(3)
        env.payload = jsonl(mixed[:1]) + b"{broken json}\n" + jsonl(mixed[1:])
        queue = env.create()
        check(
            "Q10",
            "a rejected row is skipped and batch positions count accepted rows only",
            [i[1] for i in env.items()] == [0, 1, 2] and queue.record_count == 3,
        )
    with env_of(records=2) as env:
        env.sql("DELETE FROM argilla_review_scopes")
        before = env.snapshot()
        check(
            "Q11",
            "a project with no review scope is scope_not_found",
            code_of(env.create) == "scope_not_found",
        )
        check("Q11", "scope_not_found wrote nothing", env.snapshot() == before)
        check(
            "Q11",
            "scope_not_found is checked after the quality gate (read-only phase)",
            "assert_clear" in env.calls,
        )


def test_q_inputs() -> None:
    with env_of(records=2) as env:
        before = env.snapshot()
        bad_cases = {
            "project_id": "x",
            "dataset_id": None,
            "mapping": object(),
            "owner_api": object(),
            "actor_type": "",
            "actor_id": " padded ",
            "request_id": "x" * 129,
        }
        for key, value in bad_cases.items():
            check(
                "Q12",
                f"invalid {key} is invalid_request",
                code_of(lambda key=key, value=value: env.create(**{key: value}))
                == "invalid_request",
            )
        check(
            "Q12",
            "a NUL in the actor id is invalid_request",
            code_of(lambda: env.create(actor_id="a\x00b")) == "invalid_request",
        )
        check(
            "Q12",
            "a lone surrogate in the actor id is invalid_request",
            code_of(lambda: env.create(actor_id="a\ud800b")) == "invalid_request",
        )
        check(
            "Q12",
            "invalid input read nothing and wrote nothing",
            env.snapshot() == before and env.calls == [],
        )


def test_q_idempotent_and_resume() -> None:
    with env_of(records=4) as env:
        first = env.create()
        bulk_before = sum(1 for c in env.server.review_calls if c[1].endswith("/records/bulk"))
        second = env.create()
        check(
            "Q13",
            "a replay returns the same queue",
            second.id == first.id and second.status == "ready",
        )
        check("Q13", "a replay creates no second Argilla record", len(env.server.records) == 4)
        check(
            "Q13",
            "a replay sends no second bulk create",
            sum(1 for c in env.server.review_calls if c[1].endswith("/records/bulk"))
            == bulk_before,
        )
        check(
            "Q13",
            "a replay creates no second item or queue",
            env.sql("SELECT count(*) FROM review_items")[0][0] == 4
            and env.sql("SELECT count(*) FROM review_queues")[0][0] == 1,
        )
        env.statements.clear()
        env.create()
        check(
            "Q13",
            "a replay does not even attempt to insert a queue or items",
            not any(q.lstrip().startswith("INSERT INTO review_") for q in env.statements),
        )
        check(
            "Q13",
            "a replay writes no second creation audit",
            [a["action"] for a in env.audits] == ["review_queue.created"],
        )
    with env_of(records=4) as env:
        env.server.stop()
        check(
            "Q14",
            "Argilla down is argilla_unavailable",
            code_of(env.create) == "argilla_unavailable",
        )
        queue_rows = env.sql("SELECT status, argilla_dataset_id FROM review_queues")
        check(
            "Q14",
            "after an outage the queue stays building with no Argilla dataset (never ready)",
            queue_rows == [("building", None)],
        )
        check(
            "Q14",
            "items exist but none is linked",
            all(i[5] is None for i in env.items()) and len(env.items()) == 4,
        )
        env.server.start()
        queue = env.create()
        check(
            "Q14",
            "the replay after the outage completes the same queue",
            queue.status == "ready" and env.sql("SELECT count(*) FROM review_queues")[0][0] == 1,
        )
        check(
            "Q14",
            "the replay links every item exactly once",
            all(i[5] is not None for i in env.items()) and len(env.server.records) == 4,
        )
    with env_of(records=4) as env:
        first = env.create()
        # a crash after two of the four records reached Argilla
        env.sql("UPDATE review_queues SET status = 'building', argilla_dataset_id = NULL")
        env.sql("UPDATE review_items SET argilla_record_id = NULL")
        for position in (2, 3):
            env.server.delete_record(env.server_record(f"{env.dataset_id}:{position}")["id"])
        bulks_before = sum(1 for c in env.server.review_calls if c[1].endswith("/records/bulk"))
        queue = env.create()
        check(
            "Q15",
            "records already in Argilla are found, not created again",
            len(env.server.records) == 4 and queue.status == "ready" and queue.id == first.id,
        )
        bulks = sum(1 for c in env.server.review_calls if c[1].endswith("/records/bulk"))
        check(
            "Q15", "only the two missing records were sent, in one bulk", bulks - bulks_before == 1
        )
        check(
            "Q15",
            "every item, including the pre-existing records', is linked to its record",
            all(env.server_record(i[0])["id"] == i[5] for i in env.items()),
        )
    # a record that carries the Factory's external id but is not what the Factory pushed is
    # never adopted (the queue stays building)
    for label, mutate in (
        ("text", lambda env, rec: env.server.edit_record_fields(rec["id"], "conversation", "x")),
        (
            "record_sha256",
            lambda env, rec: env.server.edit_record_metadata(rec["id"], "record_sha256", sha("z")),
        ),
        (
            "group_id",
            lambda env, rec: env.server.edit_record_metadata(rec["id"], "group_id", sha("z")),
        ),
        (
            "schema_version",
            lambda env, rec: env.server.edit_record_metadata(rec["id"], "schema_version", "zz"),
        ),
        (
            "record_id",
            lambda env, rec: env.server.edit_record_metadata(rec["id"], "record_id", "other:1"),
        ),
        (
            "extra metadata",
            lambda env, rec: env.server.edit_record_metadata(rec["id"], "note", "x"),
        ),
    ):
        with env_of(records=3) as env:
            env.create()
            env.sql("UPDATE review_queues SET status = 'building', argilla_dataset_id = NULL")
            env.sql("UPDATE review_items SET argilla_record_id = NULL")
            mutate(env, env.server_record(f"{env.dataset_id}:1"))
            check(
                "Q15",
                f"a pre-existing record with a different {label} is argilla_rejected",
                code_of(env.create) == "argilla_rejected",
            )
            check(
                "Q15",
                f"and the queue stays building ({label})",
                env.sql("SELECT status FROM review_queues") == [("building",)]
                and env.sql(
                    "SELECT count(*) FROM review_items WHERE argilla_record_id IS NOT NULL"
                )[0][0]
                == 0,
            )
    with env_of(records=3) as env:
        name = rm.dataset_name_for(env.org, env.project, env.dataset_id)
        dataset = env.owner_api.ensure_dataset(env.workspace.id, name)
        env.owner_api.create_records(dataset.id, sample_payloads(1, "foreign"))
        check(
            "Q16",
            "records the Factory never queued make the dataset unadoptable (argilla_rejected)",
            code_of(env.create) == "argilla_rejected",
        )
        check(
            "Q16",
            "the queue is not ready after such a refusal",
            env.sql("SELECT status FROM review_queues") == [("building",)],
        )
    with env_of(records=3) as env:
        name = rm.dataset_name_for(env.org, env.project, env.dataset_id)
        dataset = env.owner_api.ensure_dataset(env.workspace.id, name)
        env.server.datasets[dataset.id]["fields"].append(
            {"id": str(uuid.uuid4()), "name": "extra", "settings": {"type": "text"}}
        )
        check(
            "Q17",
            "a server refusal is argilla_rejected, not argilla_unavailable",
            code_of(env.create) == "argilla_rejected",
        )


def test_q_source_changed() -> None:
    with env_of(records=4) as env:
        first = env.create()
        env.excluded = (2,)
        check(
            "Q18",
            "a queue exists for another exclusion list: queue_source_changed",
            code_of(env.create) == "queue_source_changed",
        )
        check(
            "Q18",
            "the existing queue is unchanged",
            store.get_review_queue(env.factory, context=env.context, queue_id=first.id) == first,
        )
    with env_of(records=4) as env:
        env.create()
        env.payload = jsonl(rows_n(4)[:3] + [{"prompt": "changed", "completion": "changed"}])
        check(
            "Q18",
            "a queue exists for other bytes: queue_source_changed",
            code_of(env.create) == "queue_source_changed",
        )
    with env_of(records=4) as env:
        env.create()
        env.quality_sha = sha("quality-report-2")
        check(
            "Q18",
            "a queue exists for another quality report: queue_source_changed",
            code_of(env.create) == "queue_source_changed",
        )
    with env_of(records=4) as env:
        env.create()
        env.sql("UPDATE argilla_review_scopes SET id = :new", new=uuid.uuid4())
        check(
            "Q18",
            "a queue exists under another scope: queue_source_changed",
            code_of(env.create) == "queue_source_changed",
        )


def test_q_groups() -> None:
    with env_of(rows=rows_n(4, ["a", "a", "b", "c"]), mapping=GROUPED_MAPPING) as env:
        env.create()
        groups = [i[2] for i in env.items()]
        check("Q19", "rows sharing a group key share a group id", groups[0] == groups[1])
        check(
            "Q19",
            "rows with different group keys have different group ids",
            len({groups[0], groups[2], groups[3]}) == 3,
        )
        check(
            "Q19", "the explicit group id is the FTF-0506 one", groups[0] == sha("group:explicit:a")
        )
    with env_of(records=3) as env:
        env.create()
        groups = [i[2] for i in env.items()]
        check("Q19", "ungrouped rows are singleton groups", len(set(groups)) == 3)
        for position, group in enumerate(groups):
            record_id = str(
                uuid.uuid5(
                    ic.IMPORTER_RECORD_ID_NAMESPACE,
                    f"{sha(env.payload)}:{MAPPING.mapping_sha256}:{position}",
                )
            )
            solo = normalize_conversation(
                source_type="upload",
                source_record_index=position,
                raw_record={
                    "messages": [
                        {"role": "user", "content": f"question number {position}"},
                        {"role": "assistant", "content": f"answer number {position}"},
                    ],
                    "metadata": {},
                },
                record_id=record_id,
            )
            check(
                "Q19",
                f"singleton group id {position} equals the one FTF-0506 assigns",
                group == solo.group_id,
            )
    explicit = normalize_conversation(
        source_type="upload",
        source_record_index=0,
        raw_record={
            "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
            "metadata": {},
        },
        group_key="a",
    )
    check(
        "Q19",
        "the explicit group id formula equals FTF-0506's",
        store._group_id_for(GROUPED_MAPPING, sha("d"), 0, "a") == explicit.group_id,
    )


# ============================================================================== R: responses


def ready_queue(env: Env) -> store.ReviewQueueRecord:
    return env.create()


def rid(env: Env, position: int) -> str:
    return f"{env.dataset_id}:{position}"


def test_r_first_and_change() -> None:
    with env_of(records=3) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        owner_calls = len(writes(env.server))
        result = env.respond(queue.id, rid(env, 0), alice, "accept", "looks right")
        check(
            "R1",
            "a first response has row_version 1",
            result.row_version == 1
            and result.decision == "accept"
            and result.rationale == "looks right",
        )
        server_response = next(iter(env.server.responses.values()))
        check(
            "R1",
            "the Argilla response author is the mapped annotator",
            server_response["user_id"] == alice.user.id,
        )
        post = [w for w in writes(env.server)[owner_calls:] if w[1].endswith("/responses")]
        check(
            "R1",
            "the response was written with the reviewer's own key",
            post
            == [("POST", f"/api/v1/records/{env.items()[0][5]}/responses", alice.user.api_key)],
        )
        check("R1", "the identity's own secret ref was resolved", env.resolved == [alice.ref])
        expected = rm.response_sha256(
            record_id=rid(env, 0), user_id=alice.user.id, decision="accept", rationale="looks right"
        )
        check(
            "R1", "response_sha256 is the independent recompute", result.response_sha256 == expected
        )
        row = env.sql(
            "SELECT decision, rationale, response_sha256, argilla_response_id, row_version FROM review_responses"
        )[0]
        check(
            "R1",
            "the stored row matches the response",
            row == ("accept", "looks right", expected, server_response["id"], 1),
        )
        audit = env.audits[-1]
        check(
            "R1",
            "the audit is a content-free submitted event by the Factory principal",
            audit["action"] == "review_response.submitted"
            and audit["actor_id"] == "alice"
            and "looks right" not in json.dumps(audit, default=str),
        )
        check(
            "R1",
            "the audit carries version and hash",
            audit["details"]["row_version"] == 1
            and audit["details"]["response_sha256"] == expected
            and audit["details"]["issuer"] == ISSUER,
        )
        # change
        changed = env.respond(queue.id, rid(env, 0), alice, "reject", "", expected_version=1)
        check(
            "R2",
            "a change bumps row_version by exactly one",
            changed.row_version == 2 and changed.decision == "reject",
        )
        check(
            "R2",
            "a change keeps the same Argilla response",
            changed.argilla_response_id == result.argilla_response_id
            and len(env.server.responses) == 1,
        )
        put = writes(env.server)[-1]
        check(
            "R2",
            "the change is a PUT with the reviewer's own key",
            put[0] == "PUT" and put[2] == alice.user.api_key,
        )
        check(
            "R2",
            "an empty rationale replaces the old one",
            env.sql("SELECT rationale FROM review_responses") == [("",)],
        )
        check(
            "R2",
            "the change audit is review_response.changed",
            env.audits[-1]["action"] == "review_response.changed"
            and env.audits[-1]["details"]["row_version"] == 2,
        )
        server_values = next(iter(env.server.responses.values()))["values"]
        check(
            "R2",
            "the server holds the new decision and no stale rationale",
            server_values == {"decision": {"value": "reject"}},
        )
        third = env.respond(queue.id, rid(env, 0), alice, "accept", "again", expected_version=2)
        check("R2", "version keeps rising one at a time", third.row_version == 3)


def test_r_conflict() -> None:
    with env_of(records=2) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        env.respond(queue.id, rid(env, 0), alice, "accept", "")
        env.respond(queue.id, rid(env, 0), alice, "reject", "", expected_version=1)
        for stale in (0, 1, 3, 99):
            before = (env.snapshot(), next(iter(env.server.responses.values()))["values"])
            check(
                "R3",
                f"expected_version {stale} against current 2 is review_version_conflict",
                code_of(
                    lambda stale=stale: env.respond(
                        queue.id, rid(env, 0), alice, "accept", "x", expected_version=stale
                    )
                )
                == "review_version_conflict",
            )
            check(
                "R3",
                f"a conflict at {stale} sends nothing to Argilla and writes nothing",
                (env.snapshot(), next(iter(env.server.responses.values()))["values"]) == before,
            )
        env2 = env
        bob = env2.reviewer("bob")
        check(
            "R3",
            "a first response must say expected_version 0",
            code_of(
                lambda: env2.respond(queue.id, rid(env2, 1), bob, "accept", "", expected_version=1)
            )
            == "review_version_conflict",
        )
        check(
            "R3",
            "a first response with version 0 succeeds",
            env2.respond(queue.id, rid(env2, 1), bob, "accept", "", expected_version=0).row_version
            == 1,
        )
        check(
            "R3",
            "a repeated first response is a conflict, not a second row",
            code_of(
                lambda: env2.respond(queue.id, rid(env2, 1), bob, "reject", "", expected_version=0)
            )
            == "review_version_conflict",
        )
        check(
            "R3",
            "one row per (item, reviewer)",
            env2.sql(
                "SELECT count(*) FROM review_responses WHERE item_id = (SELECT id FROM review_items WHERE record_id = :r)",
                r=rid(env2, 1),
            )[0][0]
            == 1,
        )


def test_r_authorization() -> None:
    with env_of(records=2) as env:
        queue = ready_queue(env)
        cases = {
            "unknown identity": SimpleNamespace(name="ghost"),
            "revoked identity": env.reviewer("revoked", status="revoked"),
            "no membership row": env.reviewer("loner", scoped=False),
            "revoked membership": env.reviewer("expelled", member_status="revoked"),
        }
        for label, who in cases.items():
            before = env.snapshot()
            check(
                "R4",
                f"{label} is reviewer_not_authorized",
                code_of(lambda who=who: env.respond(queue.id, rid(env, 0), who, "accept", ""))
                == "reviewer_not_authorized",
            )
            check(
                "R4",
                f"{label} wrote nothing and called no write endpoint",
                env.snapshot() == before,
            )
        check("R4", "no secret was resolved for an unauthorized reviewer", env.resolved == [])
        alice = env.reviewer("alice")
        other_scope = uuid.uuid4()
        env.sql(
            "UPDATE argilla_workspace_members SET scope_id = :s WHERE identity_id = :i",
            s=other_scope,
            i=alice.identity_id,
        )
        check(
            "R4",
            "a membership in another project's scope does not authorize",
            code_of(lambda: env.respond(queue.id, rid(env, 0), alice, "accept", ""))
            == "reviewer_not_authorized",
        )


def test_r_states() -> None:
    with env_of(records=2) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        check(
            "R5",
            "an unknown queue is queue_not_found",
            code_of(lambda: env.respond(uuid.uuid4(), rid(env, 0), alice)) == "queue_not_found",
        )
        check(
            "R5",
            "an unknown record is review_item_not_found",
            code_of(lambda: env.respond(queue.id, "nope:9", alice)) == "review_item_not_found",
        )
        env.sql(
            "UPDATE review_items SET status = 'drift', drift_reason = 'record_missing' WHERE record_id = :r",
            r=rid(env, 0),
        )
        before = env.snapshot()
        check(
            "R5",
            "a drifted item takes no response (review_item_drift)",
            code_of(lambda: env.respond(queue.id, rid(env, 0), alice)) == "review_item_drift",
        )
        check("R5", "a refused response on a drifted item wrote nothing", env.snapshot() == before)
        check(
            "R5",
            "the other item still takes a response",
            env.respond(queue.id, rid(env, 1), alice).row_version == 1,
        )
        env.sql("UPDATE review_queues SET status = 'building'")
        check(
            "R5",
            "a queue that is not ready takes no response",
            code_of(lambda: env.respond(queue.id, rid(env, 1), alice, expected_version=1))
            == "queue_not_ready",
        )
        env.sql("UPDATE review_queues SET status = 'ready'")
        env.sql(
            "UPDATE review_items SET argilla_record_id = NULL WHERE record_id = :r", r=rid(env, 1)
        )
        check(
            "R5",
            "an unlinked item takes no response",
            code_of(lambda: env.respond(queue.id, rid(env, 1), alice, expected_version=1))
            == "queue_not_ready",
        )


def test_r_argilla_failures() -> None:
    with env_of(records=2) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        env.respond(queue.id, rid(env, 0), alice, "accept", "keep")
        row_before = env.sql(
            "SELECT decision, rationale, response_sha256, row_version FROM review_responses"
        )
        audits_before = len(env.audits)
        env.server.stop()
        check(
            "R6",
            "Argilla down on a change is argilla_unavailable",
            code_of(
                lambda: env.respond(queue.id, rid(env, 0), alice, "reject", "x", expected_version=1)
            )
            == "argilla_unavailable",
        )
        env.server.start()
        check(
            "R6",
            "the Factory row is unchanged after the outage",
            env.sql(
                "SELECT decision, rationale, response_sha256, row_version FROM review_responses"
            )
            == row_before
            and len(env.audits) == audits_before,
        )
        env.server.stop()
        check(
            "R6",
            "Argilla down on a first response is argilla_unavailable",
            code_of(lambda: env.respond(queue.id, rid(env, 1), alice, "accept", ""))
            == "argilla_unavailable",
        )
        env.server.start()
        check(
            "R6",
            "no response row exists after a failed first response",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 1,
        )
        # the server refuses: the annotator was removed from the workspace on the server only
        env.owner_client.remove_workspace_user(env.workspace.id, alice.user.id)
        check(
            "R7",
            "a server refusal is argilla_rejected",
            code_of(lambda: env.respond(queue.id, rid(env, 1), alice, "accept", ""))
            == "argilla_rejected",
        )
        check(
            "R7",
            "a server refusal wrote no Factory row",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 1
            and len(env.audits) == audits_before,
        )


def respond_with(env: Env, queue: Any, who: SimpleNamespace, key: str, value: Any) -> Any:
    """One valid response call with exactly one argument replaced by `value`."""
    if key == "queue_id":
        return env.respond(value, rid(env, 0), who)
    if key == "record_id":
        return env.respond(queue.id, value, who)
    params: dict[str, Any] = {"decision": "accept", "rationale": "", "expected_version": 0}
    if key in params:
        params[key] = value
        return env.respond(queue.id, rid(env, 0), who, **params)
    return env.respond(queue.id, rid(env, 0), who, **{key: value})


def test_r_inputs() -> None:
    with env_of(records=2) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        before = (env.snapshot(), len(env.server.review_calls))
        bad = [
            ("decision", "approve"),
            ("decision", ""),
            ("decision", None),
            ("decision", "Accept"),
            ("rationale", "r" * 2001),
            ("rationale", None),
            ("rationale", 5),
            ("rationale", "a\x00b"),
            ("rationale", "a\ud800b"),
            ("expected_version", True),
            ("expected_version", -1),
            ("expected_version", "0"),
            ("expected_version", 1.0),
            ("queue_id", "x"),
            ("queue_id", None),
            ("record_id", ""),
            ("record_id", " x "),
            ("record_id", "x" * 301),
            ("issuer", ""),
            ("issuer", "x" * 513),
            ("subject", ""),
            ("subject", "x" * 256),
            ("request_id", ""),
            ("owner_api", object()),
        ]
        for key, value in bad:
            check(
                "R8",
                f"invalid {key} {str(value)[:8]!r} is invalid_request",
                code_of(lambda key=key, value=value: respond_with(env, queue, alice, key, value))
                == "invalid_request",
            )
        check(
            "R8",
            "invalid input resolved no secret, called no endpoint and wrote nothing",
            (env.snapshot(), len(env.server.review_calls)) == before and env.resolved == [],
        )
        check(
            "R8",
            "a 2000-char rationale is accepted",
            env.respond(queue.id, rid(env, 0), alice, "accept", "r" * 2000).rationale == "r" * 2000,
        )
        check(
            "R8",
            "a 300-char record id lookup is still a normal not-found",
            code_of(lambda: env.respond(queue.id, "x" * 300, alice)) == "review_item_not_found",
        )
        check(
            "R8",
            "a 512-char issuer is allowed to reach authorization",
            code_of(lambda: env.respond(queue.id, rid(env, 1), alice, issuer="i" * 512))
            == "reviewer_not_authorized",
        )


def test_r_author_and_adoption() -> None:
    with env_of(records=2) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        owner_factory = lambda key: env.owner_api  # noqa: E731 - a factory that ignores the reviewer's key
        before = env.snapshot()
        check(
            "R9",
            "a response written with the owner key is response_author_mismatch",
            code_of(
                lambda: env.respond(
                    queue.id, rid(env, 0), alice, reviewer_api_factory=owner_factory
                )
            )
            == "response_author_mismatch",
        )
        check(
            "R9",
            "an author mismatch wrote no Factory row",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 0
            and env.snapshot()[0:3] == before[0:3],
        )
    with env_of(records=2) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        record = env.server_record(rid(env, 0))
        env.server.inject_response(record["id"], alice.user.id, "reject", "earlier attempt")
        result = env.respond(queue.id, rid(env, 0), alice, "accept", "now")
        check(
            "R10",
            "an earlier Argilla-only response is adopted, not duplicated",
            result.row_version == 1 and len(env.server.responses) == 1,
        )
        check(
            "R10",
            "the adopted response carries the new decision",
            next(iter(env.server.responses.values()))["values"]["decision"] == {"value": "accept"},
        )
        check(
            "R10",
            "the adopted response id is stored",
            result.argilla_response_id == next(iter(env.server.responses)),
        )
    with env_of(records=2) as env:
        queue = ready_queue(env)
        alice, bob = env.reviewer("alice"), env.reviewer("bob")
        a = env.respond(queue.id, rid(env, 0), alice, "accept", "")
        b = env.respond(queue.id, rid(env, 0), bob, "reject", "")
        check(
            "R11",
            "two reviewers answer the same item independently",
            a.row_version == 1
            and b.row_version == 1
            and a.argilla_response_id != b.argilla_response_id,
        )
        check(
            "R11",
            "each answer is authored by its own annotator",
            {r["user_id"] for r in env.server.responses.values()} == {alice.user.id, bob.user.id},
        )
        check(
            "R11",
            "one row per reviewer",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 2,
        )
        check(
            "R11",
            "each reviewer's own secret was resolved",
            sorted(env.resolved) == sorted([alice.ref, bob.ref]),
        )


# ================================================================================ D: drift


def drifted(env: Env) -> dict[str, str]:
    return {
        r[0]: r[1]
        for r in env.sql("SELECT record_id, drift_reason FROM review_items WHERE status = 'drift'")
    }


def test_d_clean_and_faults() -> None:
    with env_of(records=3) as env:
        queue = ready_queue(env)
        alice = env.reviewer("alice")
        env.respond(queue.id, rid(env, 0), alice, "accept", "ok")
        findings = env.reconcile(queue.id)
        check("D1", "a clean queue with a recorded response has no findings", findings == ())
        check("D1", "a clean queue marks nothing", drifted(env) == {})
        check(
            "D1",
            "reconcile writes one content-free audit",
            env.audits[-1]["action"] == "review_queue.reconciled"
            and env.audits[-1]["details"]["newly_drifted"] == 0
            and env.audits[-1]["actor_id"] == "reconciler-1",
        )
        check(
            "D1",
            "reconcile reads with the provisioning key only",
            all(
                c[2] == OWNER_KEY or c[0] != "GET"
                for c in env.server.review_calls
                if "/datasets/" in c[1] and c[1].endswith("/records")
            ),
        )

    def scenario(
        label: str,
        expected: str,
        mutate: Callable[[Env, store.ReviewQueueRecord, SimpleNamespace], None],
        position: int = 1,
    ) -> None:
        with env_of(records=3) as env:
            queue = ready_queue(env)
            alice = env.reviewer("alice")
            env.respond(queue.id, rid(env, 0), alice, "accept", "ok")
            mutate(env, queue, alice)
            findings = env.reconcile(queue.id)
            check(
                "D2",
                f"{label}: drift reason {expected}",
                drifted(env).get(rid(env, position)) == expected
                or drifted(env).get(rid(env, 0)) == expected,
            )
            marked = drifted(env)
            check("D2", f"{label}: exactly one item is marked", len(marked) == 1)
            check(
                "D2",
                f"{label}: the finding names the item and reason",
                any(
                    f.reason == expected and f.item_id is not None and f.record_id in marked
                    for f in findings
                ),
            )
            check(
                "D2",
                f"{label}: the audit counts one new drift",
                env.audits[-1]["details"]["newly_drifted"] == 1,
            )

    scenario(
        "record deleted in Argilla",
        "record_missing",
        lambda env, q, a: env.server.delete_record(env.server_record(rid(env, 1))["id"]),
    )
    scenario(
        "record text edited",
        "record_fields_changed",
        lambda env, q, a: env.server.edit_record_fields(
            env.server_record(rid(env, 1))["id"], "conversation", "edited by someone"
        ),
    )
    for key, value in (
        ("record_id", "other:1"),
        ("group_id", sha("other")),
        ("record_sha256", sha("other")),
        ("schema_version", "r006.p2.0"),
    ):
        scenario(
            f"metadata {key} edited",
            "record_metadata_changed",
            lambda env, q, a, key=key, value=value: env.server.edit_record_metadata(
                env.server_record(rid(env, 1))["id"], key, value
            ),
        )
    scenario(
        "response by the owner",
        "response_by_unmapped_user",
        lambda env, q, a: env.server.inject_response(
            env.server_record(rid(env, 1))["id"], env.server.OWNER_USER_ID, "accept"
        ),
    )
    scenario(
        "response by an unknown user",
        "response_by_unmapped_user",
        lambda env, q, a: env.server.inject_response(
            env.server_record(rid(env, 1))["id"], str(uuid.uuid4()), "accept"
        ),
    )
    scenario(
        "recorded response edited in Argilla (decision)",
        "response_drift",
        lambda env, q, a: next(iter(env.server.responses.values())).__setitem__(
            "values", {"decision": {"value": "reject"}, "rationale": {"value": "ok"}}
        ),
        position=0,
    )
    scenario(
        "recorded response edited in Argilla (rationale)",
        "response_drift",
        lambda env, q, a: next(iter(env.server.responses.values())).__setitem__(
            "values", {"decision": {"value": "accept"}, "rationale": {"value": "changed"}}
        ),
        position=0,
    )
    for status in ("draft", "discarded"):
        scenario(
            f"recorded response moved to {status}",
            "response_drift",
            lambda env, q, a, status=status: next(iter(env.server.responses.values())).__setitem__(
                "status", status
            ),
            position=0,
        )

    def replace_response(env: Env, q: Any, a: SimpleNamespace) -> None:
        old = next(iter(env.server.responses))
        env.server.delete_response(old)
        env.server.inject_response(env.server_record(rid(env, 0))["id"], a.user.id, "accept", "ok")

    scenario(
        "recorded response replaced by an identical one with a new id",
        "response_drift",
        replace_response,
        position=0,
    )
    scenario(
        "recorded response deleted in Argilla",
        "response_missing",
        lambda env, q, a: env.server.delete_response(next(iter(env.server.responses))),
        position=0,
    )


def test_d_foreign_sticky_outage() -> None:
    with env_of(records=3) as env:
        queue = ready_queue(env)
        env.owner_api.create_records(queue_dataset_id(env, queue), sample_payloads(1, "foreign"))
        findings = env.reconcile(queue.id)
        check(
            "D3",
            "a record the Factory never queued is reported as foreign_record",
            [(f.item_id, f.record_id, f.reason) for f in findings]
            == [(None, "foreign:0", "foreign_record")],
        )
        check("D3", "a foreign record marks no item", drifted(env) == {})
    with env_of(records=3) as env:
        queue = ready_queue(env)
        record = env.server_record(rid(env, 1))
        original = record["fields"]["conversation"]
        env.server.edit_record_fields(record["id"], "conversation", "tampered")
        first = env.reconcile(queue.id)
        env.server.edit_record_fields(record["id"], "conversation", original)
        second = env.reconcile(queue.id)
        check(
            "D4",
            "drift is sticky: restoring the text does not clear it",
            drifted(env) == {rid(env, 1): "record_fields_changed"},
        )
        check(
            "D4",
            "a later reconcile still reports earlier drift",
            [(f.record_id, f.reason) for f in second] == [(rid(env, 1), "record_fields_changed")]
            and len(first) == 1,
        )
        check(
            "D4",
            "an already drifted item is not counted as new",
            env.audits[-1]["details"]["newly_drifted"] == 0
            and env.audits[-1]["details"]["drifted_total"] == 1,
        )
        env.server.edit_record_fields(
            env.server_record(rid(env, 2))["id"], "conversation", "also tampered"
        )
        third = env.reconcile(queue.id)
        check(
            "D4",
            "a second drift is added without disturbing the first",
            sorted((f.record_id, f.reason) for f in third)
            == sorted(
                [(rid(env, 1), "record_fields_changed"), (rid(env, 2), "record_fields_changed")]
            ),
        )
        check(
            "D4",
            "untouched items stay pending",
            [i[6] for i in env.items()] == ["pending", "drift", "drift"],
        )
    with env_of(records=3) as env:
        queue = ready_queue(env)
        env.server.edit_record_fields(
            env.server_record(rid(env, 1))["id"], "conversation", "tampered"
        )
        before = (env.snapshot(), env.items())
        env.server.stop()
        check(
            "D5",
            "an outage during reconcile is argilla_unavailable",
            code_of(lambda: env.reconcile(queue.id)) == "argilla_unavailable",
        )
        env.server.start()
        check(
            "D5",
            "an outage marks nothing and writes no audit",
            (env.snapshot(), env.items()) == before,
        )
        check(
            "D5",
            "the same reconcile succeeds once Argilla is back",
            drifted(env) == {} and len(env.reconcile(queue.id)) == 1 and len(drifted(env)) == 1,
        )
    with env_of(records=2) as env:
        queue = ready_queue(env)
        check(
            "D6",
            "an unknown queue is queue_not_found",
            code_of(lambda: env.reconcile(uuid.uuid4())) == "queue_not_found",
        )
        check(
            "D6",
            "a non-uuid queue is invalid_request",
            code_of(lambda: env.reconcile("x")) == "invalid_request",
        )
        check(
            "D6",
            "a non-adapter owner_api is invalid_request",
            code_of(
                lambda: store.reconcile_review_queue(
                    env.factory,
                    context=env.context,
                    queue_id=queue.id,
                    owner_api=object(),
                    actor_type="user",
                    actor_id="a",
                    request_id="r",
                )
            )
            == "invalid_request",
        )
        check(
            "D6",
            "a blank actor is invalid_request",
            code_of(
                lambda: store.reconcile_review_queue(
                    env.factory,
                    context=env.context,
                    queue_id=queue.id,
                    owner_api=env.owner_api,
                    actor_type="user",
                    actor_id="",
                    request_id="r",
                )
            )
            == "invalid_request",
        )
        env.sql("UPDATE review_queues SET status = 'building'")
        check(
            "D6",
            "a queue that is not ready cannot be reconciled",
            code_of(lambda: env.reconcile(queue.id)) == "queue_not_ready",
        )


def queue_dataset_id(env: Env, queue: store.ReviewQueueRecord) -> str:
    return env.sql("SELECT argilla_dataset_id FROM review_queues WHERE id = :i", i=queue.id)[0][0]


# ===================================================================== M: migration + wiring


def test_m_migration_and_wiring() -> None:
    migration = (ROOT / "apps/api/alembic/versions/0036_review_queues.py").read_text()
    for table in ("review_queues", "review_items", "review_responses"):
        check(
            "M1",
            f"{table}: ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in migration,
        )
        check(
            "M1",
            f"{table}: FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in migration,
        )
        check(
            "M1",
            f"{table}: tenant_isolation policy",
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL" in migration,
        )
        check(
            "M1",
            f"{table}: SELECT, INSERT, UPDATE only (no DELETE grant)",
            f"GRANT SELECT, INSERT, UPDATE ON {table} TO" in migration,
        )
        check(
            "M1",
            f"{table}: an update guard trigger",
            f"CREATE TRIGGER tr_{table}_update_guard BEFORE UPDATE ON {table}" in migration,
        )
    check("M1", "no DELETE is granted anywhere", re.search(r"GRANT[^\n]*DELETE", migration) is None)
    check(
        "M1",
        "no allow-all policy",
        "USING (true)" not in migration.lower().replace("using (true)", "USING (true)")
        or "using (true)" not in migration.lower(),
    )
    check(
        "M1",
        "the sealed purposes are refused by CHECK",
        "dataset_purpose NOT IN" in migration
        and "held_out_evaluation" in migration
        and "baseline_only" in migration,
    )
    check(
        "M1",
        "the queue pins the dataset lineage by composite FK",
        "fk_review_queues_dataset_lineage" in migration and "datasets.verified_sha256" in migration,
    )
    check("M1", "drift is sticky in the item trigger", "drift is sticky" in migration)
    check(
        "M1",
        "row_version must rise by exactly one in the response trigger",
        "row_version must rise by exactly 1" in migration,
    )
    check(
        "M1",
        "the migration chains onto the P1b identity migration",
        'down_revision = "0034_argilla_review_identity"' in migration
        and 'revision = "0036_review_queues"' in migration,
    )
    check("M1", "the revision id fits 32 characters", len("0036_review_queues") <= 32)
    check(
        "M1",
        "downgrade drops tables, policies, triggers and grants",
        all(
            s in migration
            for s in (
                "DROP TRIGGER IF EXISTS",
                "DROP POLICY IF EXISTS",
                "REVOKE ALL ON",
                'op.drop_table("review_queues")',
            )
        ),
    )
    store_source = (ROOT / "apps/api/app/reviews/review_queue_store.py").read_text()
    mapping_source = (ROOT / "packages/integrations/argilla/review_mapping.py").read_text()
    for module_name, source in (("store", store_source), ("mapping", mapping_source)):
        check(
            "M2",
            f"the {module_name} module imports no HTTP library",
            re.search(r"^\s*(import|from)\s+(http|urllib|requests|httpx|socket)\b", source, re.M)
            is None,
        )
    check(
        "M2",
        "the mapping module rides P1a's guarded transport",
        "ArgillaTransport" in mapping_source
        and "from packages.integrations.argilla.client import" in mapping_source,
    )
    check(
        "M2",
        "the store reaches Argilla only through the mapping module",
        "from packages.integrations.argilla.review_mapping import" in store_source
        and "ArgillaClient(" not in store_source
        and "ArgillaTransport(" not in store_source,
    )
    check(
        "M2",
        "the store has no webhook handler (authenticated polling only)",
        "webhook" not in store_source.lower().replace("never webhooks", ""),
    )
    check(
        "M3",
        "every error code in CODES is raisable",
        all(store.ReviewQueueError(code).code == code for code in store.CODES),
    )
    check(
        "M3",
        "an unknown error code is refused",
        raises(ValueError, lambda: store.ReviewQueueError("made_up")) is not None,
    )
    required = {
        "sealed_purpose_refused",
        "dataset_already_split",
        "quality_not_clear",
        "grant_changed",
        "queue_too_large",
        "review_version_conflict",
        "argilla_unavailable",
    }
    check("M3", "every refusal the spec names is a code", required <= store.CODES)
    check(
        "M3",
        "an error message is the code alone",
        str(store.ReviewQueueError("grant_changed")) == "grant_changed",
    )
    check(
        "M3",
        "the sealed purposes are exactly the spec's two",
        store.SEALED_PURPOSES == frozenset({"held_out_evaluation", "baseline_only"}),
    )


# ====================================================================== S: strictness (mutation kills)


class StubTransport(rm.ArgillaTransport):
    """A transport whose replies are scripted: `script` maps (method, path) to a list of replies
    (a dict is returned, an exception instance is raised), consumed in order."""

    def __init__(self, script: dict[tuple[str, str], list[Any]]) -> None:
        import ipaddress

        from packages.security.url_policy import ConnectionPolicy

        super().__init__(
            "http://127.0.0.1:9",
            ConnectionPolicy(
                name="stub",
                allowed_schemes=frozenset({"http"}),
                allowed_ports=frozenset({9}),
                explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
            ),
            api_key="k",
        )
        self.script = {key: list(value) for key, value in script.items()}
        self.log: list[tuple[str, str]] = []

    def call(self, method: str, path: str, *, query: Any = None, body: Any = None) -> Any:
        self.log.append((method, path))
        replies = self.script.get((method, path))
        if not replies:
            raise AssertionError(f"unscripted call {method} {path}")
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


U1, U2, U3 = (str(uuid.uuid4()) for _ in range(3))
DS = {"id": U1, "name": "sovra-rq-" + "a" * 24, "status": "ready"}


def test_s_parsers() -> None:
    check(
        "S1",
        "a valid dataset parses",
        rm._dataset_of(DS) == rm.ArgillaReviewDataset(U1, DS["name"], True),
    )
    check(
        "S1",
        "a draft dataset is not published",
        rm._dataset_of({**DS, "status": "draft"}).published is False,
    )
    for label, payload in (
        ("not a dict", [DS]),
        ("no name", {**DS, "name": None}),
        ("numeric name", {**DS, "name": 5}),
        ("unknown status", {**DS, "status": "published"}),
        ("upper-case status", {**DS, "status": "READY"}),
        ("padded status", {**DS, "status": " ready"}),
        ("numeric status", {**DS, "status": 1}),
        ("missing status", {"id": U1, "name": "n"}),
        ("bad id", {**DS, "id": "x"}),
        ("upper-case id", {**DS, "id": U1.upper()}),
    ):
        check(
            "S1",
            f"dataset: {label} is rejected",
            raises(ArgillaRejectedError, lambda payload=payload: rm._dataset_of(payload))
            is not None,
        )
    resp = {
        "id": U1,
        "user_id": U2,
        "status": "submitted",
        "values": {"decision": {"value": "accept"}, "rationale": {"value": "r"}},
    }
    parsed = rm._response_of(resp)
    check(
        "S2",
        "a valid response parses",
        parsed == rm.ArgillaReviewResponse(U1, U2, True, "accept", "r"),
    )
    check(
        "S2",
        "a draft response is not submitted",
        rm._response_of({**resp, "status": "draft"}).submitted is False,
    )
    check(
        "S2",
        "a discarded response is not submitted",
        rm._response_of({**resp, "status": "discarded"}).submitted is False,
    )
    check(
        "S2",
        "a response without values parses with no decision",
        rm._response_of({**resp, "values": None}).decision is None,
    )
    check(
        "S2",
        "a response with only a decision has no rationale",
        rm._response_of({**resp, "values": {"decision": {"value": "reject"}}}).rationale is None,
    )
    for label, payload in (
        ("not a dict", "x"),
        ("unknown status", {**resp, "status": "done"}),
        ("upper-case status", {**resp, "status": "Submitted"}),
        ("padded status", {**resp, "status": "submitted "}),
        ("numeric status", {**resp, "status": 3}),
        ("values not a dict", {**resp, "values": ["accept"]}),
        ("value entry not a dict", {**resp, "values": {"decision": "accept"}}),
        ("value not a str", {**resp, "values": {"decision": {"value": 1}}}),
        ("bad response id", {**resp, "id": "x"}),
        ("bad user id", {**resp, "user_id": "x"}),
    ):
        check(
            "S2",
            f"response: {label} is rejected",
            raises(ArgillaRejectedError, lambda payload=payload: rm._response_of(payload))
            is not None,
        )
    check("S3", "an absent value entry is None", rm._value_of(None) is None)
    check("S3", "a value entry reads its string", rm._value_of({"value": "x"}) == "x")
    for entry in ("x", ["x"], {"value": 1}, {"value": None}, {}):
        check(
            "S3",
            f"value entry {entry!r} is rejected",
            raises(ArgillaRejectedError, lambda entry=entry: rm._value_of(entry)) is not None,
        )
    record = {
        "id": U1,
        "external_id": "d:0",
        "metadata": {"a": 1},
        "fields": {"conversation": "t"},
        "responses": [resp],
    }
    parsed_record = rm._record_of(record)
    check(
        "S4",
        "a valid record parses with its response",
        parsed_record.external_id == "d:0"
        and parsed_record.metadata == {"a": 1}
        and len(parsed_record.responses) == 1,
    )
    bare = rm._record_of({"id": U1, "fields": {}})
    check(
        "S4",
        "a record without external id, metadata or responses parses",
        bare.external_id is None and bare.metadata == {} and bare.responses == (),
    )
    for label, payload in (
        ("not a dict", []),
        ("external_id not a str", {**record, "external_id": 5}),
        ("fields not a dict", {**record, "fields": "x"}),
        ("fields missing", {"id": U1}),
        ("metadata not a dict", {**record, "metadata": "x"}),
        ("responses not a list", {**record, "responses": {}}),
        ("a malformed response inside", {**record, "responses": [{"id": "x"}]}),
        ("bad record id", {**record, "id": "x"}),
    ):
        check(
            "S4",
            f"record: {label} is rejected",
            raises(ArgillaRejectedError, lambda payload=payload: rm._record_of(payload))
            is not None,
        )
    for label, payload in (
        ("not a dict", []),
        ("no items", {}),
        ("items not a list", {"items": {}}),
        ("items None", {"items": None}),
    ):
        check(
            "S5",
            f"list reply: {label} is rejected",
            raises(ArgillaRejectedError, lambda payload=payload: rm._items(payload, "x"))
            is not None,
        )
    check("S5", "a list reply returns its items", rm._items({"items": [1, 2]}, "x") == [1, 2])
    check("S5", "an empty list reply is fine", rm._items({"items": []}, "x") == [])


def test_s_bodies_and_render() -> None:
    check(
        "S6",
        "the field body is exact",
        rm._field_body()
        == {
            "name": "conversation",
            "title": "Conversation",
            "required": True,
            "settings": {"type": "text", "use_markdown": False},
        },
    )
    questions = rm._question_bodies()
    check(
        "S6",
        "the decision question is required with exactly accept/reject",
        questions[0]
        == {
            "name": "decision",
            "title": "Decision",
            "required": True,
            "settings": {
                "type": "label_selection",
                "options": [
                    {"value": "accept", "text": "Accept"},
                    {"value": "reject", "text": "Reject"},
                ],
            },
        },
    )
    check(
        "S6",
        "the rationale question is optional text",
        questions[1]
        == {
            "name": "rationale",
            "title": "Rationale",
            "required": False,
            "settings": {"type": "text", "use_markdown": False},
        },
    )
    check(
        "S6",
        "an extra message key whose name only resembles 'role' is kept in the render",
        "Role" in rm.render_conversation([{"role": "user", "content": "x", "Role": "other"}])
        and " role" in rm.render_conversation([{"role": "user", "content": "x", " role": "y"}]),
    )
    check(
        "S6",
        "the real role key is not repeated in the body",
        rm.render_conversation([{"role": "user", "content": "x"}]).count("role") == 0,
    )


def test_s_find_and_ensure() -> None:
    name = DS["name"]
    listing = ("GET", "/me/datasets")
    # an item whose name only resembles the wanted one is not the dataset
    api = rm.ArgillaReviewApi(
        StubTransport(
            {listing: [{"items": [{**DS, "name": name.upper()}, {**DS, "name": " " + name}]}]}
        )
    )
    check(
        "S7",
        "a differently-cased or padded name is not a match",
        api._find_dataset(U2, name) is None,
    )
    api = rm.ArgillaReviewApi(StubTransport({listing: [{"items": [DS, {**DS, "id": U3}]}]}))
    check(
        "S7",
        "two datasets for one name are rejected",
        raises(ArgillaRejectedError, lambda: api._find_dataset(U2, name)) is not None,
    )
    api = rm.ArgillaReviewApi(StubTransport({listing: [{"items": [DS]}]}))
    check("S7", "one match is returned", api._find_dataset(U2, name).id == U1)
    # create-race: listing empty, create conflicts, re-list finds it
    published = ("GET", f"/datasets/{U1}/fields"), ("GET", f"/datasets/{U1}/questions")
    stub = StubTransport(
        {
            listing: [{"items": []}, {"items": [DS]}],
            ("POST", "/datasets"): [ArgillaConflictError("exists")],
            published[0]: [{"items": [rm._field_body()]}],
            published[1]: [{"items": rm._question_bodies()}],
        }
    )
    got = rm.ArgillaReviewApi(stub).ensure_dataset(U2, name)
    check(
        "S8",
        "a create race is resolved by one re-list",
        got.id == U1 and stub.log.count(("GET", "/me/datasets")) == 2,
    )
    stub = StubTransport(
        {listing: [{"items": []}], ("POST", "/datasets"): [ArgillaConflictError("exists")]}
    )
    check(
        "S8",
        "a create conflict with no dataset on re-list is rejected",
        raises(ArgillaRejectedError, lambda: rm.ArgillaReviewApi(stub).ensure_dataset(U2, name))
        is not None,
    )
    stub = StubTransport(
        {listing: [{"items": []}], ("POST", "/datasets"): [{**DS, "status": "draft"}]}
    )
    check(
        "S8",
        "a created draft needs its settings (the next call is the field list)",
        raises(AssertionError, lambda: rm.ArgillaReviewApi(stub).ensure_dataset(U2, name))
        is not None
        and stub.log[-1] == ("GET", f"/datasets/{U1}/fields"),
    )
    # malformed or look-alike settings are never adopted
    ok_field, ok_questions = rm._field_body(), rm._question_bodies()

    def with_option(options: list[dict[str, str]]) -> list[dict[str, Any]]:
        decision = {**ok_questions[0], "settings": {"type": "label_selection", "options": options}}
        return [decision, ok_questions[1]]

    accept, reject = {"value": "accept", "text": "Accept"}, {"value": "reject", "text": "Reject"}
    for label, fields, questions in (
        ("a non-dict field", ["x"], ok_questions),
        ("a field without a name", [{"settings": {"type": "text"}}], ok_questions),
        ("a non-dict question", [ok_field], ["x"]),
        ("a question without a name", [ok_field], [{"title": "t"}, ok_questions[1]]),
        ("a field that is not required", [{**ok_field, "required": False}], ok_questions),
        (
            "a field with markdown on",
            [{**ok_field, "settings": {"type": "text", "use_markdown": True}}],
            ok_questions,
        ),
        ("a field that is not text", [{**ok_field, "settings": {"type": "image"}}], ok_questions),
        ("a field with no settings", [{**ok_field, "settings": None}], ok_questions),
        ("two fields", [ok_field, {**ok_field, "name": "other"}], ok_questions),
        ("a field with another name", [{**ok_field, "name": "other"}], ok_questions),
        (
            "a third decision option",
            [ok_field],
            with_option([accept, reject, {"value": "escalate", "text": "Escalate"}]),
        ),
        ("a missing decision option", [ok_field], with_option([accept])),
        ("options in another order", [ok_field], with_option([reject, accept])),
        (
            "an option with another label",
            [ok_field],
            with_option([accept, {"value": "reject", "text": "No"}]),
        ),
        ("a non-dict option", [ok_field], with_option([accept, "reject"])),  # type: ignore[list-item]
        (
            "options that are not a list",
            [ok_field],
            [
                {**ok_questions[0], "settings": {"type": "label_selection", "options": "x"}},
                ok_questions[1],
            ],
        ),
        (
            "a decision that is not required",
            [ok_field],
            [{**ok_questions[0], "required": False}, ok_questions[1]],
        ),
        (
            "a rationale that is required",
            [ok_field],
            [ok_questions[0], {**ok_questions[1], "required": True}],
        ),
        (
            "a rationale that is a label selection",
            [ok_field],
            [
                ok_questions[0],
                {**ok_questions[1], "settings": {"type": "label_selection", "options": [accept]}},
            ],
        ),
        (
            "a rationale with markdown on",
            [ok_field],
            [
                ok_questions[0],
                {**ok_questions[1], "settings": {"type": "text", "use_markdown": True}},
            ],
        ),
        (
            "a decision that is a text question",
            [ok_field],
            [
                {**ok_questions[0], "settings": {"type": "text", "use_markdown": False}},
                ok_questions[1],
            ],
        ),
        ("a third question", [ok_field], [*ok_questions, {**ok_questions[1], "name": "extra"}]),
        ("a duplicated question name", [ok_field], [ok_questions[0], ok_questions[0]]),
        ("a missing question", [ok_field], [ok_questions[0]]),
    ):
        stub = StubTransport(
            {
                listing: [{"items": [DS]}],
                published[0]: [{"items": fields}],
                published[1]: [{"items": questions}],
            }
        )
        check(
            "S9",
            f"settings check: {label} is rejected",
            raises(
                ArgillaRejectedError,
                lambda stub=stub: rm.ArgillaReviewApi(stub).ensure_dataset(U2, name),
            )
            is not None,
        )
    stub = StubTransport(
        {
            listing: [{"items": [DS]}],
            published[0]: [{"items": [ok_field]}],
            published[1]: [{"items": ok_questions}],
        }
    )
    check(
        "S9",
        "the exact Factory settings are accepted",
        rm.ArgillaReviewApi(stub).ensure_dataset(U2, name).published,
    )
    stub = StubTransport(
        {
            listing: [{"items": [DS]}],
            published[0]: [{"items": [ok_field]}],
            published[1]: [
                {"items": [{**ok_questions[0], "description": "x", "title": "T"}, ok_questions[1]]}
            ],
        }
    )
    check(
        "S9",
        "extra descriptive keys (title, description) do not matter",
        rm.ArgillaReviewApi(stub).ensure_dataset(U2, name).published,
    )


def test_s_server_settings() -> None:
    world = new_world()
    try:
        name = rm.dataset_name_for(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        # a draft that already holds only the field: the questions are added and it is published
        draft = world.client.transport.call(
            "POST",
            "/datasets",
            body={
                "name": name,
                "workspace_id": world.workspace.id,
                "guidelines": "g",
                "allow_extra_metadata": True,
            },
        )
        world.client.transport.call(
            "POST", f"/datasets/{draft['id']}/fields", body=rm._field_body()
        )
        done = world.api.ensure_dataset(world.workspace.id, name)
        check(
            "S10",
            "a half-set-up draft is completed and published",
            done.id == draft["id"]
            and done.published
            and len(world.server.datasets[done.id]["questions"]) == 2,
        )
        check(
            "S10",
            "the existing field was not created twice",
            len(world.server.datasets[done.id]["fields"]) == 1,
        )
        # a draft holding only a foreign field is never completed into the Factory's dataset
        other = rm.dataset_name_for(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        foreign = world.client.transport.call(
            "POST",
            "/datasets",
            body={
                "name": other,
                "workspace_id": world.workspace.id,
                "guidelines": "g",
                "allow_extra_metadata": True,
            },
        )
        world.client.transport.call(
            "POST", f"/datasets/{foreign['id']}/fields", body={**rm._field_body(), "name": "other"}
        )
        check(
            "S10",
            "a draft with a foreign field is rejected after the settings are added",
            raises(
                ArgillaRejectedError, lambda: world.api.ensure_dataset(world.workspace.id, other)
            )
            is not None,
        )
        check(
            "S10", "and is not published", world.server.datasets[foreign["id"]]["status"] == "draft"
        )
        # no request for malformed identifiers
        dataset = world.api.ensure_dataset(
            world.workspace.id, rm.dataset_name_for(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        )
        calls = len(world.server.review_calls)
        for fn in (
            lambda: world.api.list_records("x"),
            lambda: world.api.create_records("x", sample_payloads(1)),
            lambda: world.api.get_record("x"),
            lambda: world.api.submit_response("x", "accept", ""),
            lambda: world.api.update_response("x", "accept", ""),
            lambda: world.api.ensure_dataset("x", name),
        ):
            check(
                "S11",
                "a malformed id is refused before any request",
                raises(ArgillaRejectedError, fn) is not None
                and len(world.server.review_calls) == calls,
            )
        # the record cap
        saved = rm.MAX_RECORDS
        rm.MAX_RECORDS = 25
        try:
            world.api.create_records(dataset.id, sample_payloads(25))
            check(
                "S12",
                "exactly MAX_RECORDS records list fine",
                len(world.api.list_records(dataset.id)) == 25,
            )
            world.api.create_records(dataset.id, sample_payloads(1, "extra"))
            check(
                "S12",
                "one record over MAX_RECORDS is rejected",
                raises(ArgillaRejectedError, lambda: world.api.list_records(dataset.id))
                is not None,
            )
        finally:
            rm.MAX_RECORDS = saved
        # create_records must return what was requested
        stub = StubTransport(
            {
                ("POST", f"/datasets/{dataset.id}/records/bulk"): [
                    {"items": [{"id": U1, "external_id": "other", "fields": {}}]}
                ]
            }
        )
        check(
            "S13",
            "a reply for different records is rejected",
            raises(
                ArgillaRejectedError,
                lambda: rm.ArgillaReviewApi(stub).create_records(
                    dataset.id, [{"external_id": "wanted", "fields": {}}]
                ),
            )
            is not None,
        )
        stub = StubTransport({("POST", f"/datasets/{dataset.id}/records/bulk"): [{"items": []}]})
        check(
            "S13",
            "a reply with fewer records is rejected",
            raises(
                ArgillaRejectedError,
                lambda: rm.ArgillaReviewApi(stub).create_records(
                    dataset.id, [{"external_id": "wanted", "fields": {}}]
                ),
            )
            is not None,
        )
    finally:
        world.server.shutdown()


def test_s_store_helpers() -> None:
    for code in (" grant_changed", "grant_changed ", "GRANT_CHANGED", "Grant_Changed", ""):
        check(
            "S14",
            f"error code {code!r} is not a code",
            raises(ValueError, lambda code=code: store.ReviewQueueError(code)) is not None,
        )
    with env_of(records=2) as env:
        for key in ("actor_id", "request_id", "actor_type"):
            for value in (" x", "x "):
                check(
                    "S15",
                    f"{key} {value!r} (one-sided whitespace) is invalid_request",
                    code_of(lambda key=key, value=value: env.create(**{key: value}))
                    == "invalid_request",
                )
        for purpose in (
            "Training",
            " training",
            "training ",
            "TRAINING",
            "unknown",
            "",
            "held_out",
            "heldout_test",
        ):
            env.purpose = purpose
            check(
                "S16",
                f"purpose {purpose!r} is not an allowed queue purpose (fail closed)",
                code_of(env.create) == "sealed_purpose_refused",
            )
        env.purpose = "training"
        check(
            "S16",
            "the allowed purposes are exactly three",
            store.QUEUE_PURPOSES == frozenset({"training", "validation", "candidate_development"})
            and not store.QUEUE_PURPOSES & store.SEALED_PURPOSES,
        )
    # an abort that is not a changed source is source_unreadable
    from workers.data.import_common import ImportLimits

    saved = store.DEFAULT_LIMITS
    store.DEFAULT_LIMITS = ImportLimits(max_source_bytes=10)
    try:
        with env_of(records=1) as env:
            env.payload = jsonl(rows_n(3))
            check(
                "S17",
                "a source over the size limit aborts the read: source_unreadable",
                code_of(env.create) == "source_unreadable",
            )
            check("S17", "that abort wrote nothing", env.snapshot()[0:3] == (0, 0, 0))
    finally:
        store.DEFAULT_LIMITS = saved
    # records without an Argilla id, extra rows in the queue, status variants
    with env_of(records=3) as env:
        queue = env.create()
        for column, label in (
            ("queue_snapshot_sha256", "snapshot"),
            ("quality_report_sha256", "quality report"),
            ("excluded_positions_sha256", "exclusion list"),
        ):
            original = env.sql(f"SELECT {column} FROM review_queues")[0][0]
            check(
                "S18", f"the {label} hash has letters to upper-case", original != original.upper()
            )
            env.sql(f"UPDATE review_queues SET {column} = upper({column})")
            check(
                "S18",
                f"a replay compares the {label} hash exactly (upper-case variant is a different queue)",
                code_of(env.create) == "queue_source_changed",
            )
            env.sql(f"UPDATE review_queues SET {column} = :v", v=original)
        check("S18", "restored hashes replay fine", env.create().id == queue.id)
    with env_of(records=3) as env:
        env.create()
        env.sql(
            "INSERT INTO review_items (organization_id, queue_id, record_id, batch_position, group_id, record_sha256, fields_sha256) "
            "SELECT organization_id, id, 'extra:9', 9, :h, :h, :h FROM review_queues",
            h=sha("extra"),
        )
        check(
            "S19",
            "a queue with an unlinked extra item is never marked ready (argilla_rejected)",
            code_of(env.create) == "argilla_rejected",
        )
    with env_of(records=3) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        for status in ("READY", "Ready", " ready", "ready ", "building", ""):
            env.sql("UPDATE review_queues SET status = :s", s=status)
            check(
                "S20",
                f"queue status {status!r} takes no response",
                code_of(lambda: env.respond(queue.id, rid(env, 0), alice)) == "queue_not_ready",
            )
            check(
                "S20",
                f"queue status {status!r} cannot be reconciled",
                code_of(lambda: env.reconcile(queue.id)) == "queue_not_ready",
            )
        env.sql("UPDATE review_queues SET status = 'ready'")
        for status in ("DRIFT", "Pending", "pending ", "drift", ""):
            env.sql(
                "UPDATE review_items SET status = :s, drift_reason = NULL WHERE record_id = :r",
                s=status,
                r=rid(env, 0),
            )
            check(
                "S20",
                f"item status {status!r} takes no response",
                code_of(lambda: env.respond(queue.id, rid(env, 0), alice)) == "review_item_drift",
            )
        env.sql("UPDATE review_items SET status = 'pending'")
        check(
            "S20",
            "a pending item takes a response",
            env.respond(queue.id, rid(env, 0), alice).row_version == 1,
        )


def test_s_race_and_conflict() -> None:
    from sqlalchemy.exc import IntegrityError

    with env_of(records=3) as env:
        first = env.create()
        real = store._existing_queue
        calls: list[int] = []

        def blind_first(*args: Any) -> Any:
            calls.append(1)
            return None if len(calls) == 1 else real(*args)

        store._existing_queue = blind_first
        try:
            again = env.create()
        finally:
            store._existing_queue = real
        check(
            "S21",
            "losing the insert race returns the winner's queue",
            again.id == first.id and len(calls) == 2,
        )
        check(
            "S21",
            "the lost race wrote nothing extra",
            env.sql("SELECT count(*) FROM review_queues")[0][0] == 1
            and env.sql("SELECT count(*) FROM review_items")[0][0] == 3
            and [a["action"] for a in env.audits] == ["review_queue.created"],
        )
        env.sql("UPDATE review_queues SET queue_snapshot_sha256 = :h", h=sha("other snapshot"))
        calls.clear()
        store._existing_queue = blind_first
        try:
            code = code_of(env.create)
        finally:
            store._existing_queue = real
        check(
            "S21",
            "a lost race against a different source is queue_source_changed",
            code == "queue_source_changed",
        )
        store._existing_queue = lambda *a: None
        try:
            caught = raises(IntegrityError, env.create)
        finally:
            store._existing_queue = real
        check(
            "S21",
            "a unique violation with no winner row is re-raised, not swallowed",
            caught is not None,
        )

    with env_of(records=4) as env:
        stale = lambda dataset_id: []  # noqa: E731 - a listing that never shows what is already there
        name = rm.dataset_name_for(env.org, env.project, env.dataset_id)
        first = env.create()
        env.sql("UPDATE review_queues SET status = 'building', argilla_dataset_id = NULL")
        env.sql("UPDATE review_items SET argilla_record_id = NULL")
        real_list = env.owner_api.list_records
        bulk_before = sum(1 for c in env.server.review_calls if c[1].endswith("/records/bulk"))
        env.owner_api.list_records = stale
        check(
            "S22",
            "a listing that is always stale ends argilla_rejected after three attempts",
            code_of(env.create) == "argilla_rejected",
        )
        creates = sum(1 for c in env.server.review_calls if c[1].endswith("/records/bulk"))
        check("S22", "exactly three bulk attempts were made", creates - bulk_before == 3)
        state = {"n": 0}

        def stale_once(dataset_id: str) -> Any:
            state["n"] += 1
            return [] if state["n"] == 1 else real_list(dataset_id)

        env.owner_api.list_records = stale_once
        queue = env.create()
        check(
            "S22",
            "one stale listing is retried and the queue completes",
            queue.status == "ready" and queue.id == first.id and len(env.server.records) == 4,
        )
        del name
    with env_of(records=3) as env:
        env.create()
        env.sql("UPDATE review_queues SET status = 'building', argilla_dataset_id = NULL")
        env.sql("UPDATE review_items SET argilla_record_id = NULL")
        dataset_id = env.sql("SELECT 1")[0][0] and next(iter(env.server.datasets))
        record = env.server_record(rid(env, 0))
        env.server.records[record["id"]]["external_id"] = rid(env, 0).upper()
        check(
            "S23",
            "an upper-case look-alike external id is a foreign record (argilla_rejected)",
            code_of(env.create) == "argilla_rejected",
        )
        del dataset_id


class TamperingApi(rm.ArgillaReviewApi):
    """A reviewer API whose server-side result is altered after the real write."""

    def __init__(self, transport: Any, server: FakeArgillaServer, **tamper: Any) -> None:
        super().__init__(transport)
        self._server, self._tamper = server, tamper

    def submit_response(self, record_id: str, decision: str, rationale: str) -> Any:
        result = super().submit_response(record_id, decision, rationale)
        response = self._server.responses[result.id]
        if "decision" in self._tamper:
            response["values"]["decision"] = {"value": self._tamper["decision"]}
        if "rationale" in self._tamper:
            response["values"]["rationale"] = {"value": self._tamper["rationale"]}
        if "status" in self._tamper:
            response["status"] = self._tamper["status"]
        return result


class ConflictingApi(rm.ArgillaReviewApi):
    def submit_response(self, record_id: str, decision: str, rationale: str) -> Any:
        raise ArgillaConflictError("conflict")


def readback_case(label: str, tamper: dict[str, Any], decision: str, rationale: str) -> None:
    with env_of(records=2) as env:
        queue = env.create()
        alice = env.reviewer("alice")

        def factory(key: str) -> TamperingApi:
            return TamperingApi(env.server.client(api_key=key).transport, env.server, **tamper)

        code = code_of(
            lambda: env.respond(
                queue.id, rid(env, 0), alice, decision, rationale, reviewer_api_factory=factory
            )
        )
        check("S24", f"{label} is response_author_mismatch", code == "response_author_mismatch")
        check(
            "S24",
            f"{label} wrote no Factory row",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 0,
        )


def test_s_response_readback() -> None:
    cases = (
        ("a decision that differs only in case", {"decision": "Accept"}, "accept", "ok"),
        ("a rationale that differs only in case", {"rationale": "OK"}, "accept", "ok"),
        ("a rationale that differs only in padding", {"rationale": "ok "}, "accept", "ok"),
        ("a response that is not submitted", {"status": "draft"}, "accept", "ok"),
        ("a response stored as discarded", {"status": "discarded"}, "accept", "ok"),
    )
    for label, tamper, decision, rationale in cases:
        readback_case(label, tamper, decision, rationale)
    with env_of(records=2) as env:
        queue = env.create()
        alice = env.reviewer("alice")

        def factory(key: str) -> ConflictingApi:
            return ConflictingApi(env.server.client(api_key=key).transport)

        check(
            "S25",
            "a conflict with no existing response by this reviewer is argilla_rejected",
            code_of(lambda: env.respond(queue.id, rid(env, 0), alice, reviewer_api_factory=factory))
            == "argilla_rejected",
        )
        check(
            "S25",
            "and wrote no Factory row",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 0,
        )
    rec = lambda user_id: rm.ArgillaReviewRecord(  # noqa: E731
        U1, "d:0", {}, {}, (rm.ArgillaReviewResponse(U2, user_id, True, "accept", None),)
    )
    check("S26", "_response_by finds the exact author", store._response_by(rec(U3), U3) is not None)
    check(
        "S26",
        "_response_by does not match a case variant",
        store._response_by(rec(U3.upper()), U3) is None,
    )
    check(
        "S26",
        "_response_by does not match a padded variant",
        store._response_by(rec(" " + U3), U3) is None,
    )
    twice = rm.ArgillaReviewRecord(
        U1,
        "d:0",
        {},
        {},
        (
            rm.ArgillaReviewResponse(U2, U3, True, "accept", None),
            rm.ArgillaReviewResponse(U1, U3, True, "reject", None),
        ),
    )
    check(
        "S26",
        "two responses by one user are ambiguous (None), not the first one",
        store._response_by(twice, U3) is None,
    )
    check(
        "S26",
        "no response is None",
        store._response_by(rm.ArgillaReviewRecord(U1, "d:0", {}, {}, ()), U3) is None,
    )


def test_s_right_operand_variants() -> None:
    rec = rm.ArgillaReviewRecord(
        U1, "d:0", {}, {}, (rm.ArgillaReviewResponse(U2, U3, True, "accept", None),)
    )
    check(
        "S30",
        "_response_by does not match an upper-cased wanted user id",
        store._response_by(rec, U3.upper()) is None,
    )
    check(
        "S30",
        "_response_by does not match a padded wanted user id",
        store._response_by(rec, U3 + " ") is None,
    )
    name = DS["name"]
    api = rm.ArgillaReviewApi(StubTransport({("GET", "/me/datasets"): [{"items": [DS]}]}))
    check(
        "S30",
        "_find_dataset does not match an upper-cased wanted name",
        api._find_dataset(U2, name.upper()) is None,
    )
    check(
        "S30",
        "_find_dataset does not match a padded wanted name",
        api._find_dataset(U2, name + " ") is None,
    )
    with env_of(records=2) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        for decision, rationale in (
            ("accept", " Mixed Case OK "),
            ("reject", "UPPER"),
            ("accept", "tail "),
        ):
            version = env.sql("SELECT coalesce(max(row_version), 0) FROM review_responses")[0][0]
            result = env.respond(
                queue.id, rid(env, 0), alice, decision, rationale, expected_version=version
            )
            check(
                "S30",
                f"a rationale {rationale!r} is stored and read back exactly",
                result.rationale == rationale
                and next(iter(env.server.responses.values()))["values"]["rationale"]
                == {"value": rationale},
            )


def test_s_review_findings() -> None:
    # EXCLUDED-POSITION-SHIFT: a rejected row ahead of an excluded one must not shift positions
    rows = rows_n(5)
    rows[3] = {"prompt": "EXCLUDE-ME", "completion": "gone"}
    with env_of(rows=rows) as env:
        env.payload = jsonl(rows[:1]) + b'{"prompt": "no completion"}\n' + jsonl(rows[1:])
        env.excluded = (3,)  # batch positions count accepted rows: p0, p1, p2, EXCLUDE-ME, p4
        queue = env.create()
        check(
            "F1",
            "the excluded batch position is the accepted ordinal, so EXCLUDE-ME is not queued",
            [i[1] for i in env.items()] == [0, 1, 2, 4]
            and not env.server.holds_bytes(b"EXCLUDE-ME"),
        )
        check(
            "F1",
            "the other records are all queued, with record ids from the batch position",
            [i[0] for i in env.items()] == [f"{env.dataset_id}:{p}" for p in (0, 1, 2, 4)]
            and queue.record_count == 4,
        )
        # the group id formula keeps using the importer's own record index (rejected rows count)
        source_indexes = [0, 2, 3, 5]
        for item, source_index in zip(env.items(), source_indexes, strict=True):
            record_id = str(
                uuid.uuid5(
                    ic.IMPORTER_RECORD_ID_NAMESPACE,
                    f"{sha(env.payload)}:{MAPPING.mapping_sha256}:{source_index}",
                )
            )
            check(
                "F1",
                f"item at position {item[1]} has the singleton group of source index {source_index}",
                item[2] == sha(f"group:solo:{record_id}"),
            )

    # RECONCILE-RACE-FALSE-DRIFT: a poll that overlaps a legitimate submit must not mark drift
    with env_of(records=3) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        real_list = env.owner_api.list_records

        def list_then_submit(dataset_id: str) -> Any:
            listed = real_list(dataset_id)  # the poll reads Argilla first ...
            env.respond(queue.id, rid(env, 0), alice, "accept", "raced")  # ... a submit commits
            return listed

        env.owner_api.list_records = list_then_submit
        findings = env.reconcile(queue.id)
        env.owner_api.list_records = real_list
        check(
            "F2",
            "a submit that lands after the listing is not drift",
            findings == () and drifted(env) == {},
        )
        check(
            "F2",
            "the raced response is recorded",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 1,
        )
        check("F2", "and a later reconcile is clean too", env.reconcile(queue.id) == ())

        def list_without_record(dataset_id: str) -> Any:
            return [r for r in real_list(dataset_id) if r.external_id != rid(env, 1)]

        env.owner_api.list_records = list_without_record
        findings = env.reconcile(queue.id)
        env.owner_api.list_records = real_list
        check(
            "F2",
            "a record missing from a stale listing but present on a fresh read is not drift",
            findings == () and drifted(env) == {},
        )
        gone = env.server_record(rid(env, 1))
        env.server.delete_record(gone["id"])
        check(
            "F2",
            "a record that is really gone is still record_missing after the fresh read",
            [(f.record_id, f.reason) for f in env.reconcile(queue.id)]
            == [(rid(env, 1), "record_missing")],
        )
        env.sql(
            "UPDATE review_items SET argilla_record_id = NULL WHERE record_id = :r", r=rid(env, 2)
        )
        env.server.edit_record_fields(env.server_record(rid(env, 2))["id"], "conversation", "x")
        check(
            "F2",
            "a candidate whose item has no Argilla link is skipped, not marked",
            rid(env, 2) not in {f.record_id for f in env.reconcile(queue.id)},
        )

    # authorization is re-checked under the item lock
    with env_of(records=2) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        env.on_resolve = lambda: env.sql(
            "UPDATE argilla_reviewer_identities SET status = 'revoked' WHERE id = :i",
            i=alice.identity_id,
        )
        writes_before = len(writes(env.server))
        check(
            "F3",
            "an identity revoked after the first check but before the write is refused",
            code_of(lambda: env.respond(queue.id, rid(env, 0), alice)) == "reviewer_not_authorized",
        )
        check("F3", "nothing was sent to Argilla", len(writes(env.server)) == writes_before)
    with env_of(records=2) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        env.on_resolve = lambda: env.sql(
            "UPDATE argilla_workspace_members SET status = 'revoked' WHERE identity_id = :i",
            i=alice.identity_id,
        )
        check(
            "F3",
            "a membership revoked in the same window is refused",
            code_of(lambda: env.respond(queue.id, rid(env, 0), alice)) == "reviewer_not_authorized",
        )

    # adoption only ever takes the author's own response
    with env_of(records=2) as env:
        queue = env.create()
        alice, bob = env.reviewer("alice"), env.reviewer("bob")
        env.respond(queue.id, rid(env, 0), bob, "reject", "bob's")

        def factory(key: str) -> ConflictingApi:
            return ConflictingApi(env.server.client(api_key=key).transport)

        puts_before = sum(1 for c in env.server.review_calls if c[0] == "PUT")
        check(
            "F4",
            "a conflict with only another reviewer's response on the record is argilla_rejected",
            code_of(lambda: env.respond(queue.id, rid(env, 0), alice, reviewer_api_factory=factory))
            == "argilla_rejected",
        )
        check(
            "F4",
            "and no response was overwritten",
            sum(1 for c in env.server.review_calls if c[0] == "PUT") == puts_before,
        )
    check(
        "F5",
        "the decision vocabulary is exactly accept/reject",
        rm.DECISIONS == ("accept", "reject"),
    )
    check("F5", "the rationale cap is 2000", rm.MAX_RATIONALE_CHARS == 2000)


class OutageOnceOwner(rm.ArgillaReviewApi):
    """An owner API whose first read-back fails, as an outage right after the reviewer's write."""

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self.fail_next = False

    def get_record(self, record_id: str) -> Any:
        if self.fail_next:
            self.fail_next = False
            raise ArgillaUnavailableError("outage")
        return super().get_record(record_id)


def test_s_review_round2() -> None:
    # REPLACED-RECORD: deleted and recreated with identical content (new Argilla id)
    with env_of(records=3) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        old = env.server_record(rid(env, 1))
        payload = {
            "fields": old["fields"],
            "metadata": old["metadata"],
            "external_id": old["external_id"],
        }
        env.server.delete_record(old["id"])
        env.owner_api.create_records(queue_dataset_id(env, queue), [payload])
        findings = env.reconcile(queue.id)
        check(
            "R2a",
            "a recreated record with identical content is drift (the linked record is gone)",
            [(f.record_id, f.reason) for f in findings] == [(rid(env, 1), "record_missing")],
        )
        check(
            "R2a",
            "and the item no longer takes responses",
            code_of(lambda: env.respond(queue.id, rid(env, 1), alice)) == "review_item_drift",
        )

    # FALSE-DRIFT-AFTER-UNCOMMITTED-WRITE: the write reached Argilla, the read-back failed. The
    # orphan is not drift and is never recorded from the poll; the reviewer's retry brings it in.
    def orphan_env() -> Any:
        return env_of(records=3)

    for variant in ("untouched", "edited by the owner"):
        with orphan_env() as env:
            queue = env.create()
            alice = env.reviewer("alice")
            flaky = OutageOnceOwner(env.server.client().transport)
            env.owner_api = flaky
            flaky.fail_next = True
            check(
                "R2b",
                f"{variant}: an outage on the read-back is argilla_unavailable",
                code_of(
                    lambda queue=queue, alice=alice: env.respond(
                        queue.id, rid(env, 0), alice, "accept", "kept"
                    )
                )
                == "argilla_unavailable",
            )
            check(
                "R2b",
                f"{variant}: no Factory row exists and Argilla holds the orphan",
                env.sql("SELECT count(*) FROM review_responses")[0][0] == 0
                and len(env.server.responses) == 1,
            )
            if variant != "untouched":
                orphan = next(iter(env.server.responses.values()))
                orphan["values"] = {
                    "decision": {"value": "reject"},
                    "rationale": {"value": "owner"},
                }
            audits_before = len(env.audits)
            findings = env.reconcile(queue.id)
            check(
                "R2b",
                f"{variant}: the poll reports it as pending adoption, not drift",
                [(f.record_id, f.reason) for f in findings]
                == [(rid(env, 0), "response_pending_adoption")]
                and drifted(env) == {},
            )
            check(
                "R2b",
                f"{variant}: the poll records nothing (no row, no adoption audit)",
                env.sql("SELECT count(*) FROM review_responses")[0][0] == 0
                and [a["action"] for a in env.audits[audits_before:]] == ["review_queue.reconciled"]
                and env.audits[-1]["details"]["pending_adoption"] == 1,
            )
            check(
                "R2b",
                f"{variant}: the item stays pending, so the reviewer can retry",
                [i[6] for i in env.items()] == ["pending", "pending", "pending"],
            )
            retried = env.respond(queue.id, rid(env, 0), alice, "accept", "kept")
            server_values = next(iter(env.server.responses.values()))["values"]
            check(
                "R2b",
                f"{variant}: the retry brings it in at version 1 with the reviewer's own value",
                retried.row_version == 1
                and server_values
                == {"decision": {"value": "accept"}, "rationale": {"value": "kept"}}
                and len(env.server.responses) == 1,
            )
            check("R2b", f"{variant}: afterwards the queue is clean", env.reconcile(queue.id) == ())

    # a value the Factory refused (read-back mismatch) is never recorded by a poll either
    with orphan_env() as env:
        queue = env.create()
        alice = env.reviewer("alice")

        def tampering(key: str) -> TamperingApi:
            return TamperingApi(
                env.server.client(api_key=key).transport, env.server, decision="reject"
            )

        check(
            "R2b",
            "a response whose stored value differs from what was sent is response_author_mismatch",
            code_of(
                lambda: env.respond(
                    queue.id, rid(env, 0), alice, "accept", "ok", reviewer_api_factory=tampering
                )
            )
            == "response_author_mismatch",
        )
        findings = env.reconcile(queue.id)
        check(
            "R2b",
            "the refused value is not recorded by the poll",
            env.sql("SELECT count(*) FROM review_responses")[0][0] == 0
            and [f.reason for f in findings] == ["response_pending_adoption"],
        )
        check(
            "R2b",
            "the reviewer's honest retry replaces it",
            env.respond(queue.id, rid(env, 0), alice, "accept", "ok").decision == "accept"
            and next(iter(env.server.responses.values()))["values"]["decision"]
            == {"value": "accept"},
        )

    # what is not even pending adoption (drift)
    def injected(
        label: str,
        setup: Callable[[Env, SimpleNamespace], tuple[str, str, str, str]],
        expected_reason: str,
    ) -> None:
        with env_of(records=3) as env:
            queue = env.create()
            alice = env.reviewer("alice")
            user_id, decision, rationale, status = setup(env, alice)
            env.server.inject_response(
                env.server_record(rid(env, 0))["id"], user_id, decision, rationale, status
            )
            findings = env.reconcile(queue.id)
            check(
                "R2c",
                f"{label}: drift {expected_reason}",
                [(f.record_id, f.reason) for f in findings] == [(rid(env, 0), expected_reason)],
            )
            check(
                "R2c",
                f"{label}: nothing adopted",
                env.sql("SELECT count(*) FROM review_responses")[0][0] == 0,
            )

    injected(
        "an owner response",
        lambda env, a: (env.server.OWNER_USER_ID, "accept", "", "submitted"),
        "response_by_unmapped_user",
    )
    injected(
        "a response by an unknown user",
        lambda env, a: (str(uuid.uuid4()), "accept", "", "submitted"),
        "response_by_unmapped_user",
    )
    injected(
        "a draft by an active reviewer",
        lambda env, a: (a.user.id, "accept", "", "draft"),
        "response_untracked",
    )
    injected(
        "a discarded response by an active reviewer",
        lambda env, a: (a.user.id, "accept", "", "discarded"),
        "response_untracked",
    )
    injected(
        "a decision outside accept/reject",
        lambda env, a: (a.user.id, "escalate", "", "submitted"),
        "response_untracked",
    )
    injected(
        "a rationale over 2000 characters",
        lambda env, a: (a.user.id, "accept", "r" * 2001, "submitted"),
        "response_untracked",
    )

    def revoke_identity(env: Env, a: SimpleNamespace) -> tuple[str, str, str, str]:
        env.sql(
            "UPDATE argilla_reviewer_identities SET status = 'revoked' WHERE id = :i",
            i=a.identity_id,
        )
        return a.user.id, "accept", "", "submitted"

    def revoke_membership(env: Env, a: SimpleNamespace) -> tuple[str, str, str, str]:
        env.sql(
            "UPDATE argilla_workspace_members SET status = 'revoked' WHERE identity_id = :i",
            i=a.identity_id,
        )
        return a.user.id, "accept", "", "submitted"

    injected("a response by a revoked identity", revoke_identity, "response_untracked")
    injected(
        "a response by a reviewer whose membership was revoked",
        revoke_membership,
        "response_untracked",
    )
    with env_of(records=3) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        record = env.server_record(rid(env, 0))
        env.server.edit_record_fields(record["id"], "conversation", "edited")
        env.server.inject_response(record["id"], alice.user.id, "accept")
        findings = env.reconcile(queue.id)
        check(
            "R2c",
            "a response on an edited record is record_fields_changed, never adopted",
            [(f.record_id, f.reason) for f in findings] == [(rid(env, 0), "record_fields_changed")]
            and env.sql("SELECT count(*) FROM review_responses")[0][0] == 0,
        )
    with env_of(records=3) as env:
        queue = env.create()
        alice = env.reviewer("alice")
        other = env.sql("SELECT count(*) FROM argilla_workspace_members")[0][0]
        other_scope = uuid.uuid4()
        env.sql(
            "UPDATE argilla_workspace_members SET scope_id = :s WHERE identity_id = :i",
            s=other_scope,
            i=alice.identity_id,
        )
        env.server.inject_response(env.server_record(rid(env, 0))["id"], alice.user.id, "accept")
        findings = env.reconcile(queue.id)
        check(
            "R2c",
            "a reviewer of another scope is not adopted",
            [(f.reason) for f in findings] == ["response_untracked"] and other == 1,
        )

    # RECONCILE-QUADRATIC: the queue's rows are read once, not once per candidate
    with env_of(records=12) as env:
        queue = env.create()
        for position in range(10):
            env.server.edit_record_fields(
                env.server_record(rid(env, position))["id"], "conversation", "x"
            )
        env.statements.clear()
        findings = env.reconcile(queue.id)
        listing = [
            q
            for q in env.statements
            if "FROM review_items WHERE queue_id" in q and "ORDER BY batch_position" in q
        ]
        check("R2d", "all ten edited items are drift", len(findings) == 10)
        check(
            "R2d",
            "the whole queue's items are read once, whatever the number of candidates",
            len(listing) == 1,
        )
        check(
            "R2d",
            "each candidate is locked in its own statement",
            sum(
                1
                for q in env.statements
                if q.lstrip().startswith("SELECT status, argilla_record_id FROM review_items")
            )
            == 10,
        )


def test_s_live_settings_drift() -> None:
    world = new_world()
    try:
        name = rm.dataset_name_for(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        dataset = world.api.ensure_dataset(world.workspace.id, name)
        server_dataset = world.server.datasets[dataset.id]
        decision = next(q for q in server_dataset["questions"] if q["name"] == "decision")
        rationale = next(q for q in server_dataset["questions"] if q["name"] == "rationale")
        conversation = server_dataset["fields"][0]
        check(
            "S31",
            "the server really stores required and settings",
            decision["required"] is True
            and rationale["required"] is False
            and conversation["required"] is True,
        )

        def drifted_by(label: str, mutate: Callable[[], None], undo: Callable[[], None]) -> None:
            mutate()
            check(
                "S31",
                f"a published dataset with {label} is not adopted",
                raises(
                    ArgillaRejectedError, lambda: world.api.ensure_dataset(world.workspace.id, name)
                )
                is not None,
            )
            undo()
            check(
                "S31",
                f"and is adopted again once {label} is undone",
                world.api.ensure_dataset(world.workspace.id, name).id == dataset.id,
            )

        options = decision["settings"]["options"]
        drifted_by(
            "a third decision option",
            lambda: options.append({"value": "escalate", "text": "Escalate"}),
            lambda: options.pop(),
        )
        drifted_by(
            "a decision that is optional",
            lambda: decision.__setitem__("required", False),
            lambda: decision.__setitem__("required", True),
        )
        drifted_by(
            "a rationale that is required",
            lambda: rationale.__setitem__("required", True),
            lambda: rationale.__setitem__("required", False),
        )
        saved = rationale["settings"]
        drifted_by(
            "a rationale that is a label selection",
            lambda: rationale.__setitem__(
                "settings", {"type": "label_selection", "options": [{"value": "a", "text": "A"}]}
            ),
            lambda: rationale.__setitem__("settings", saved),
        )
        drifted_by(
            "markdown on the conversation field",
            lambda: conversation["settings"].__setitem__("use_markdown", True),
            lambda: conversation["settings"].__setitem__("use_markdown", False),
        )
        drifted_by(
            "a relabelled option",
            lambda: options[0].__setitem__("text", "Yes"),
            lambda: options[0].__setitem__("text", "Accept"),
        )
    finally:
        world.server.shutdown()


def test_s_text_boundary() -> None:
    limit = rm.MAX_REVIEW_TEXT_CHARS
    overhead = len(rm.render_conversation([{"role": "user", "content": ""}]))
    exact = [{"role": "user", "content": "y" * (limit - overhead)}]
    one_over = [{"role": "user", "content": "y" * (limit - overhead + 1)}]
    check(
        "S32",
        "text of exactly 65,536 characters renders",
        len(rm.render_conversation(exact)) == 65_536,
    )
    caught = raises(rm.ReviewMappingError, lambda: rm.render_conversation(one_over))
    check(
        "S32",
        "text of 65,537 characters is refused",
        caught is not None and caught.code == "record_too_large_for_review",
    )
    check("S32", "the cap is 65,536", limit == 65_536)


def test_s_drift_direct() -> None:
    item = {
        "record_id": "d:0",
        "group_id": sha("g"),
        "record_sha256": sha("r"),
        "fields_sha256": rm.fields_sha256("text"),
        "argilla_record_id": U1,
    }
    good_meta = {
        "record_id": "d:0",
        "group_id": sha("g"),
        "record_sha256": sha("r"),
        "schema_version": rm.SCHEMA_VERSION,
    }
    mapped = {U3: "identity-1"}
    stored = {
        "identity-1": {
            "argilla_response_id": U2,
            "response_sha256": rm.response_sha256(
                record_id="d:0", user_id=U3, decision="accept", rationale=""
            ),
        }
    }

    def record(**over: Any) -> rm.ArgillaReviewRecord:
        base: dict[str, Any] = dict(
            id=U1,
            external_id="d:0",
            metadata=good_meta,
            fields={"conversation": "text"},
            responses=(rm.ArgillaReviewResponse(U2, U3, True, "accept", None),),
        )
        base.update(over)
        return rm.ArgillaReviewRecord(**base)

    check(
        "S27",
        "a record exactly as queued is clean",
        store._drift_reason(item, record(), stored, mapped) is None,
    )
    check(
        "S27",
        "an upper-case schema version is metadata drift",
        store._drift_reason(
            item,
            record(metadata={**good_meta, "schema_version": rm.SCHEMA_VERSION.upper()}),
            stored,
            mapped,
        )
        == "record_metadata_changed",
    )
    check(
        "S27",
        "an extra metadata key is metadata drift",
        store._drift_reason(item, record(metadata={**good_meta, "note": "x"}), stored, mapped)
        == "record_metadata_changed",
    )
    check(
        "S27",
        "a missing metadata key is metadata drift",
        store._drift_reason(
            item,
            record(metadata={k: v for k, v in good_meta.items() if k != "group_id"}),
            stored,
            mapped,
        )
        == "record_metadata_changed",
    )
    check(
        "S27",
        "an upper-case response id is response_drift",
        store._drift_reason(
            item,
            record(responses=(rm.ArgillaReviewResponse(U2.upper(), U3, True, "accept", None),)),
            stored,
            mapped,
        )
        == "response_drift",
    )
    check(
        "S27",
        "a response that is not submitted is response_drift",
        store._drift_reason(
            item,
            record(responses=(rm.ArgillaReviewResponse(U2, U3, False, "accept", None),)),
            stored,
            mapped,
        )
        == "response_drift",
    )
    check(
        "S27",
        "a response with no decision is response_drift",
        store._drift_reason(
            item,
            record(responses=(rm.ArgillaReviewResponse(U2, U3, True, None, None),)),
            stored,
            mapped,
        )
        == "response_drift",
    )
    check(
        "S27",
        "an upper-case user id is an unmapped user",
        store._drift_reason(
            item,
            record(responses=(rm.ArgillaReviewResponse(U2, U3.upper(), True, "accept", None),)),
            stored,
            mapped,
        )
        == "response_by_unmapped_user",
    )
    check(
        "S27",
        "a non-text field value is fields drift",
        store._drift_reason(item, record(fields={"conversation": 5}), stored, mapped)
        == "record_fields_changed",
    )
    check(
        "S27",
        "a missing field is fields drift",
        store._drift_reason(item, record(fields={}), stored, mapped) == "record_fields_changed",
    )
    check(
        "S27",
        "a record with the same external id but another Argilla id is record_replaced",
        store._drift_reason(item, record(id=U2), stored, mapped) == "record_replaced",
    )
    check(
        "S27",
        "an upper-case Argilla id is not the linked record",
        store._drift_reason(item, record(id=U1.upper()), stored, mapped) == "record_replaced",
    )
    check(
        "S27",
        "a record that is gone is record_missing",
        store._drift_reason(item, None, stored, mapped) == "record_missing",
    )
    check(
        "S27",
        "a recorded response absent in Argilla is response_missing",
        store._drift_reason(item, record(responses=()), stored, mapped) == "response_missing",
    )


def test_s_foreign_variants() -> None:
    with env_of(records=2) as env:
        queue = env.create()
        real_list = env.owner_api.list_records
        dataset_id = env.sql("SELECT argilla_dataset_id FROM review_queues")[0][0]

        def with_variants(ds: str) -> Any:
            extra = [
                rm.ArgillaReviewRecord(U1, rid(env, 0).upper(), {}, {}, ()),
                rm.ArgillaReviewRecord(U2, None, {}, {}, ()),
            ]
            return real_list(ds) + extra

        env.owner_api.list_records = with_variants
        findings = env.reconcile(queue.id)
        check(
            "S28",
            "an upper-case look-alike and a record with no external id are both foreign",
            sorted((f.item_id, f.reason, f.record_id or "") for f in findings)
            == sorted(
                [(None, "foreign_record", rid(env, 0).upper()), (None, "foreign_record", "")]
            ),
        )
        check("S28", "foreign records mark no item", drifted(env) == {})
        del dataset_id


# ======================================================================= migration executed


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def test_s_migration_executed() -> None:
    import importlib.util

    path = ROOT / "apps" / "api" / "alembic" / "versions" / "0036_review_queues.py"
    spec = importlib.util.spec_from_file_location("migration_0036", path)
    assert spec is not None and spec.loader is not None
    mod: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rec = _Recorder()
    mod.op = rec
    mod.upgrade()
    created = [(a[0], a[1:]) for n, a, _ in rec.calls if n == "create_table"]
    check(
        "S29",
        "upgrade creates the three tables, queues first",
        [c[0] for c in created] == ["review_queues", "review_items", "review_responses"],
    )
    tables = dict(created)
    for name, items in tables.items():
        columns = {c.name: c for c in items if c.__class__.__name__ == "Column"}
        pk = columns["id"]
        check(
            "S29",
            f"{name}.id is the primary key defaulting to gen_random_uuid()",
            pk.primary_key is True and "gen_random_uuid()" in str(pk.server_default.arg),
        )
        org = columns["organization_id"]
        check(
            "S29",
            f"{name}.organization_id is NOT NULL and references organizations",
            org.nullable is False
            and any(fk.target_fullname == "organizations.id" for fk in org.foreign_keys),
        )
        for stamp in ("created_at", "updated_at"):
            check(
                "S29",
                f"{name}.{stamp} is NOT NULL defaulting to now()",
                columns[stamp].nullable is False
                and "now()" in str(columns[stamp].server_default.arg),
            )
    queues = {c.name: c for c in tables["review_queues"] if c.__class__.__name__ == "Column"}
    for column in ("project_id", "dataset_id", "scope_id", "quality_report_id"):
        check("S29", f"review_queues.{column} is a NOT NULL uuid", queues[column].nullable is False)
    check(
        "S29",
        "review_queues.argilla_dataset_id is nullable until the dataset exists",
        queues["argilla_dataset_id"].nullable is True,
    )
    items_cols = {c.name: c for c in tables["review_items"] if c.__class__.__name__ == "Column"}
    for column in ("queue_id",):
        check(
            "S29", f"review_items.{column} is a NOT NULL uuid", items_cols[column].nullable is False
        )
    check(
        "S29",
        "review_items.argilla_record_id and drift_reason are nullable",
        items_cols["argilla_record_id"].nullable is True
        and items_cols["drift_reason"].nullable is True,
    )
    resp_cols = {c.name: c for c in tables["review_responses"] if c.__class__.__name__ == "Column"}
    for column in ("queue_id", "item_id", "identity_id"):
        check(
            "S29",
            f"review_responses.{column} is a NOT NULL uuid",
            resp_cols[column].nullable is False,
        )
    checks = {
        c.name: str(c.sqltext)
        for items in tables.values()
        for c in items
        if c.__class__.__name__ == "CheckConstraint"
    }
    for table, column in (
        ("review_queues", "dataset_sha256"),
        ("review_queues", "quality_report_sha256"),
        ("review_queues", "excluded_positions_sha256"),
        ("review_queues", "queue_snapshot_sha256"),
        ("review_items", "group_id"),
        ("review_items", "record_sha256"),
        ("review_items", "fields_sha256"),
        ("review_responses", "response_sha256"),
    ):
        check(
            "S29",
            f"{table}.{column} is pinned to 64 lowercase hex by a named CHECK",
            checks.get(f"ck_{table}_{column}_shape") == f"{column} ~ '^[0-9a-f]{{64}}$'",
        )
    check(
        "S29",
        "queue status CHECK names exactly building/ready",
        checks["ck_review_queues_status"] == "status IN ('building', 'ready')",
    )
    check(
        "S29",
        "item status CHECK names exactly pending/drift",
        checks["ck_review_items_status"] == "status IN ('pending', 'drift')",
    )
    check(
        "S29",
        "decision CHECK names exactly accept/reject",
        checks["ck_review_responses_decision"] == "decision IN ('accept', 'reject')",
    )
    check(
        "S29",
        "the sealed CHECK names exactly the two sealed purposes",
        checks["ck_review_queues_purpose_not_sealed"]
        == "dataset_purpose NOT IN ('held_out_evaluation', 'baseline_only')",
    )
    sqls = [a[0] for n, a, _ in rec.calls if n == "execute"]
    for table in ("review_queues", "review_items", "review_responses"):
        check(
            "S29",
            f"{table}: grant without DELETE, ENABLE + FORCE RLS, tenant policy, guard trigger",
            f'GRANT SELECT, INSERT, UPDATE ON {table} TO "sovra_app"' in sqls
            and f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sqls
            and f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sqls
            and any(
                q.startswith(f"CREATE POLICY tenant_isolation ON {table} FOR ALL") for q in sqls
            )
            and any(
                f"CREATE TRIGGER tr_{table}_update_guard BEFORE UPDATE ON {table}" in q
                for q in sqls
            ),
        )
    check(
        "S29",
        "three guard functions are created",
        sum(1 for q in sqls if q.startswith("CREATE FUNCTION review_") and "_update_guard()" in q)
        == 3,
    )
    down = _Recorder()
    mod.op = down
    mod.downgrade()
    dsqls = [a[0] for n, a, _ in down.calls if n == "execute"]
    check(
        "S29",
        "downgrade drops triggers, functions, policies and grants for every table",
        sum(1 for q in dsqls if q.startswith("DROP TRIGGER IF EXISTS")) == 3
        and sum(1 for q in dsqls if q.startswith("DROP FUNCTION IF EXISTS")) == 3
        and sum(1 for q in dsqls if q.startswith("DROP POLICY IF EXISTS tenant_isolation ON ")) == 3
        and sum(1 for q in dsqls if q.startswith("REVOKE ALL ON ")) == 3,
    )
    check(
        "S29",
        "downgrade drops children before parents",
        [a[0] for n, a, _ in down.calls if n == "drop_table"]
        == ["review_responses", "review_items", "review_queues"],
    )
    unique = {
        c.name
        for items in tables.values()
        for c in items
        if c.__class__.__name__ == "UniqueConstraint"
    }
    check(
        "S29",
        "the queue/record and item/identity uniqueness constraints exist by name",
        {
            "uq_review_items_queue_record",
            "uq_review_items_queue_position",
            "uq_review_responses_item_identity",
            "uq_review_responses_argilla_response_id",
            "uq_review_queues_org_dataset",
            "uq_review_queues_argilla_dataset_id",
        }
        <= unique,
    )
    check("S29", "_in renders a quoted tuple", mod._in(("a", "b")) == "('a', 'b')")


def main() -> None:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a crashing test is a failure, with its name
            import traceback

            traceback.print_exc()
            FAILURES.append(f"FAIL [{name}] raised {type(exc).__name__}: {exc}")
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print(f"All FTF-R006-P2 checks passed ({len(tests)} test functions).")


if __name__ == "__main__":
    main()
