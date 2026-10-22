"""FTF-R006-P2 -- Argilla review-queue adapter: datasets, records and responses
(Section 8-10, 67, 77.3-77.6, 78.4, 80.1-80.4, 81).

FTF-R006-P1a's `client.py` (certified, untouched here) speaks only to the identity and workspace
endpoints. This module is the other half of the same pinned Argilla v2.8.0 `/api/v1` surface:
the dataset / record / response endpoints a review queue needs. It reuses P1a's
`ArgillaTransport` (the shared SSRF-guarded, address-pinned, bounded-body, deadline-bound request
path) and its error classes unchanged, so a destination the caller's policy denies, a malformed
body, a 5xx and a real 404/409 stay apart exactly as they do for identities (Law 17: an outage is
`ArgillaUnavailableError`, never a success and never a permanent rejection).

Route and body shapes are read from the pinned `argilla-server` v2.8.0 source
(`api/handlers/v1/datasets/{datasets,records,records_bulk,questions}.py`,
`api/handlers/v1/{records,responses}.py`, `api/schemas/v1/{datasets,fields,questions,records,
records_bulk,responses}.py`), not guessed:

- `GET /me/datasets?workspace_id=&name=`, `POST /datasets`, `POST /datasets/{id}/fields`,
  `GET /datasets/{id}/fields`, `POST|GET /datasets/{id}/questions`, `PUT /datasets/{id}/publish`;
- `POST /datasets/{id}/records/bulk` (<= 1000 items, unique `external_id`),
  `GET /datasets/{id}/records?include=responses&offset=&limit=`;
- `POST /records/{id}/responses` (the authenticated user is the author; the body cannot name
  another), `PUT /responses/{id}`.

What the Factory sends is deliberately small and content-free apart from the conversation text a
reviewer must read: one `text` field (`conversation`), one required `label_selection` question
(`decision`: accept / reject), one optional `text` question (`rationale`), and per-record metadata
`record_id`, `group_id`, `record_sha256`, `schema_version` (all hashes/identifiers). A record
whose rendered text exceeds `MAX_REVIEW_TEXT_CHARS` is refused (`ReviewMappingError`,
`record_too_large_for_review`), never truncated: the transport's response bound is 1 MiB, so a
larger record could be written but never read back for drift reconciliation.

Responses are written with the reviewer's own API key (a separate transport built by the
caller); the Factory's provisioning key is used for dataset setup, record creation and for the
read-back that proves who actually authored a response. `parse_*` helpers fail closed on any
shape the pinned schemas do not allow.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from packages.integrations.argilla.client import (
    ArgillaConflictError,
    ArgillaRejectedError,
    ArgillaTransport,
)

__all__ = [
    "DECISIONS",
    "FIELD_NAME",
    "MAX_RATIONALE_CHARS",
    "MAX_REVIEW_TEXT_CHARS",
    "QUESTION_DECISION",
    "QUESTION_RATIONALE",
    "RECORD_PAGE_SIZE",
    "SCHEMA_VERSION",
    "ArgillaReviewApi",
    "ArgillaReviewDataset",
    "ArgillaReviewRecord",
    "ArgillaReviewResponse",
    "ReviewMappingError",
    "dataset_name_for",
    "fields_sha256",
    "render_conversation",
    "response_sha256",
    "response_values",
    "review_record_payload",
]

SCHEMA_VERSION = "r006.p2.1"
FIELD_NAME = "conversation"
QUESTION_DECISION = "decision"
QUESTION_RATIONALE = "rationale"
DECISIONS: tuple[str, ...] = ("accept", "reject")
MAX_RATIONALE_CHARS = 2000
MAX_REVIEW_TEXT_CHARS = 65_536
# Bulk create is capped at 1000 server-side; smaller keeps every request body far from the
# transport's bound. The list page is small because each record carries its whole conversation
# and the transport refuses bodies over 1 MiB (65,536 chars * 10 stays inside it).
BULK_CREATE_SIZE = 200
RECORD_PAGE_SIZE = 10
MAX_RECORDS = 100_000

_UUID_TEXT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATASET_NAME = re.compile(r"^sovra-rq-[0-9a-f]{24}$")
_GUIDELINES = (
    "Decide whether this conversation is acceptable training data. "
    "Choose accept or reject and, if you wish, give a short rationale."
)
_DATASET_STATUSES = frozenset({"draft", "ready"})
_RESPONSE_STATUSES = frozenset({"submitted", "discarded", "draft"})


class ReviewMappingError(ValueError):
    """The Factory refused to build or accept something before any request was made."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArgillaReviewDataset:
    id: str
    name: str
    status: str


@dataclass(frozen=True)
class ArgillaReviewResponse:
    id: str
    user_id: str
    status: str
    decision: str | None
    rationale: str | None


@dataclass(frozen=True)
class ArgillaReviewRecord:
    id: str
    external_id: str | None
    metadata: Mapping[str, object]
    fields: Mapping[str, object]
    responses: tuple[ArgillaReviewResponse, ...]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dataset_name_for(organization_id: object, project_id: object, dataset_id: object) -> str:
    """Deterministic, content-free Argilla dataset name for one queue source."""
    key = f"{organization_id}|{project_id}|{dataset_id}"
    return f"sovra-rq-{_sha256_text(key)[:24]}"


def _require_uuid(value: object, name: str) -> str:
    if not isinstance(value, str) or not _UUID_TEXT.fullmatch(value):
        raise ArgillaRejectedError(f"{name} must be a lowercase UUID")
    return value


def render_conversation(messages: Sequence[Mapping[str, object]]) -> str:
    """The reviewer-facing text of one canonical conversation: one block per message, role first,
    tool calls and non-text parts as canonical JSON. Deterministic, so `fields_sha256` of it is a
    stable drift fingerprint. Refuses (never truncates) text over `MAX_REVIEW_TEXT_CHARS`."""
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes) or not messages:
        raise ReviewMappingError("record_not_renderable")
    blocks: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ReviewMappingError("record_not_renderable")
        role = message.get("role")
        if not isinstance(role, str) or not role:
            raise ReviewMappingError("record_not_renderable")
        body = json.dumps(
            {k: v for k, v in message.items() if k != "role"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        blocks.append(f"[{role}]\n{body}")
    text = "\n\n".join(blocks)
    if len(text) > MAX_REVIEW_TEXT_CHARS:
        raise ReviewMappingError("record_too_large_for_review")
    return text


def fields_sha256(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return _sha256_text(text)


def review_record_payload(
    *,
    record_id: str,
    group_id: str,
    record_sha256: str,
    messages: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """One `RecordCreate` body. `external_id` is the Factory record id, so the server itself is the
    backstop against a duplicate even if two pushes race."""
    if not isinstance(record_id, str) or not record_id or len(record_id) > 255:
        raise ReviewMappingError("record_not_renderable")
    if not isinstance(group_id, str) or not _SHA256.fullmatch(group_id):
        raise ReviewMappingError("record_not_renderable")
    if not isinstance(record_sha256, str) or not _SHA256.fullmatch(record_sha256):
        raise ReviewMappingError("record_not_renderable")
    return {
        "fields": {FIELD_NAME: render_conversation(messages)},
        "metadata": {
            "record_id": record_id,
            "group_id": group_id,
            "record_sha256": record_sha256,
            "schema_version": SCHEMA_VERSION,
        },
        "external_id": record_id,
    }


def response_values(decision: str, rationale: str) -> dict[str, object]:
    """The `values` of a response. An empty rationale is omitted (the question is optional and
    Argilla refuses an empty text value)."""
    if type(decision) is not str or decision not in DECISIONS:
        raise ReviewMappingError("invalid_decision")
    if type(rationale) is not str or len(rationale) > MAX_RATIONALE_CHARS:
        raise ReviewMappingError("invalid_rationale")
    values: dict[str, object] = {QUESTION_DECISION: {"value": decision}}
    if rationale:
        values[QUESTION_RATIONALE] = {"value": rationale}
    return values


def response_sha256(*, record_id: str, user_id: str, decision: str, rationale: str) -> str:
    """Content-bound fingerprint of one response, over canonical JSON."""
    body = json.dumps(
        {
            "record_id": record_id,
            "user_id": user_id,
            "decision": decision,
            "rationale": rationale,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return _sha256_text(body)


def _field_body() -> dict[str, object]:
    return {
        "name": FIELD_NAME,
        "title": "Conversation",
        "required": True,
        "settings": {"type": "text", "use_markdown": False},
    }


def _question_bodies() -> list[dict[str, object]]:
    return [
        {
            "name": QUESTION_DECISION,
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
        {
            "name": QUESTION_RATIONALE,
            "title": "Rationale",
            "required": False,
            "settings": {"type": "text", "use_markdown": False},
        },
    ]


def _items(payload: object, what: str) -> list[object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ArgillaRejectedError(f"Argilla returned a malformed {what} list")
    return payload["items"]


def _dataset_of(payload: object) -> ArgillaReviewDataset:
    if not isinstance(payload, dict):
        raise ArgillaRejectedError("Argilla returned a malformed dataset")
    name = payload.get("name")
    status = payload.get("status")
    if not isinstance(name, str) or not isinstance(status, str) or status not in _DATASET_STATUSES:
        raise ArgillaRejectedError("Argilla returned a malformed dataset")
    return ArgillaReviewDataset(
        id=_require_uuid(payload.get("id"), "dataset id"), name=name, status=status
    )


def _response_of(payload: object) -> ArgillaReviewResponse:
    if not isinstance(payload, dict):
        raise ArgillaRejectedError("Argilla returned a malformed response")
    status = payload.get("status")
    if not isinstance(status, str) or status not in _RESPONSE_STATUSES:
        raise ArgillaRejectedError("Argilla returned a malformed response")
    values = payload.get("values")
    decision: str | None = None
    rationale: str | None = None
    if values is not None:
        if not isinstance(values, dict):
            raise ArgillaRejectedError("Argilla returned a malformed response")
        decision = _value_of(values.get(QUESTION_DECISION))
        rationale = _value_of(values.get(QUESTION_RATIONALE))
    return ArgillaReviewResponse(
        id=_require_uuid(payload.get("id"), "response id"),
        user_id=_require_uuid(payload.get("user_id"), "response user_id"),
        status=status,
        decision=decision,
        rationale=rationale,
    )


def _value_of(entry: object) -> str | None:
    if entry is None:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
        raise ArgillaRejectedError("Argilla returned a malformed response value")
    return entry["value"]


def _record_of(payload: object) -> ArgillaReviewRecord:
    if not isinstance(payload, dict):
        raise ArgillaRejectedError("Argilla returned a malformed record")
    external_id = payload.get("external_id")
    metadata = payload.get("metadata")
    fields = payload.get("fields")
    responses = payload.get("responses")
    if external_id is not None and not isinstance(external_id, str):
        raise ArgillaRejectedError("Argilla returned a malformed record")
    if not isinstance(fields, dict):
        raise ArgillaRejectedError("Argilla returned a malformed record")
    if metadata is not None and not isinstance(metadata, dict):
        raise ArgillaRejectedError("Argilla returned a malformed record")
    if responses is not None and not isinstance(responses, list):
        raise ArgillaRejectedError("Argilla returned a malformed record")
    return ArgillaReviewRecord(
        id=_require_uuid(payload.get("id"), "record id"),
        external_id=external_id,
        metadata=dict(metadata or {}),
        fields=dict(fields),
        responses=tuple(_response_of(item) for item in (responses or [])),
    )


class ArgillaReviewApi:
    """Dataset / record / response calls over one `ArgillaTransport` (one API key)."""

    def __init__(self, transport: ArgillaTransport) -> None:
        if not isinstance(transport, ArgillaTransport):
            raise TypeError("transport must be an ArgillaTransport")
        self._transport = transport

    # -- dataset setup ---------------------------------------------------

    def _find_dataset(self, workspace_id: str, name: str) -> ArgillaReviewDataset | None:
        found = self._transport.call(
            "GET", "/me/datasets", query={"workspace_id": workspace_id, "name": name}
        )
        matches = [_dataset_of(item) for item in _items(found, "dataset")]
        matches = [dataset for dataset in matches if dataset.name == name]
        if len(matches) > 1:
            raise ArgillaRejectedError("Argilla returned duplicate datasets for one name")
        return matches[0] if matches else None

    def ensure_dataset(self, workspace_id: str, name: str) -> ArgillaReviewDataset:
        """Idempotent. Creates the dataset, its field and questions and publishes it, or returns
        the existing one after checking its settings are exactly the Factory's (a published
        dataset with a different field/question set is `ArgillaRejectedError`, never adopted)."""
        _require_uuid(workspace_id, "workspace_id")
        if not isinstance(name, str) or not _DATASET_NAME.fullmatch(name):
            raise ArgillaRejectedError("dataset name has an invalid shape")
        dataset = self._find_dataset(workspace_id, name)
        if dataset is None:
            try:
                created = self._transport.call(
                    "POST",
                    "/datasets",
                    body={
                        "name": name,
                        "workspace_id": workspace_id,
                        "guidelines": _GUIDELINES,
                        "allow_extra_metadata": True,
                    },
                )
                dataset = _dataset_of(created)
            except ArgillaConflictError:
                dataset = self._find_dataset(workspace_id, name)
                if dataset is None:
                    raise ArgillaRejectedError(
                        "dataset create conflicted but no matching dataset was found on re-list"
                    ) from None
        if dataset.status == "draft":
            self._ensure_settings(dataset.id)
            published = self._transport.call("PUT", f"/datasets/{dataset.id}/publish")
            dataset = _dataset_of(published)
        else:
            self._verify_settings(dataset.id)
        return dataset

    def _field_names(self, dataset_id: str) -> list[str]:
        found = self._transport.call("GET", f"/datasets/{dataset_id}/fields")
        names = []
        for item in _items(found, "field"):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ArgillaRejectedError("Argilla returned a malformed field")
            settings = item.get("settings")
            if not isinstance(settings, dict) or settings.get("type") != "text":
                raise ArgillaRejectedError("Argilla dataset has a field the Factory did not create")
            names.append(item["name"])
        return names

    def _question_names(self, dataset_id: str) -> list[str]:
        found = self._transport.call("GET", f"/datasets/{dataset_id}/questions")
        names = []
        for item in _items(found, "question"):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ArgillaRejectedError("Argilla returned a malformed question")
            names.append(item["name"])
        return names

    def _verify_settings(self, dataset_id: str) -> None:
        if self._field_names(dataset_id) != [FIELD_NAME]:
            raise ArgillaRejectedError("Argilla dataset fields differ from the Factory's schema")
        if sorted(self._question_names(dataset_id)) != sorted(
            [QUESTION_DECISION, QUESTION_RATIONALE]
        ):
            raise ArgillaRejectedError("Argilla dataset questions differ from the Factory's schema")

    def _ensure_settings(self, dataset_id: str) -> None:
        if FIELD_NAME not in self._field_names(dataset_id):
            self._create_ignoring_conflict(f"/datasets/{dataset_id}/fields", _field_body())
        have = set(self._question_names(dataset_id))
        for body in _question_bodies():
            if body["name"] not in have:
                self._create_ignoring_conflict(f"/datasets/{dataset_id}/questions", body)
        self._verify_settings(dataset_id)

    def _create_ignoring_conflict(self, path: str, body: dict[str, object]) -> None:
        try:
            self._transport.call("POST", path, body=body)
        except ArgillaConflictError:
            # A concurrent setup call created it first; _verify_settings re-reads the real state.
            return

    # -- records ----------------------------------------------------------

    def list_records(self, dataset_id: str) -> list[ArgillaReviewRecord]:
        """Every record with its responses, in bounded pages. A server that keeps returning pages
        past `MAX_RECORDS` is refused, not followed."""
        _require_uuid(dataset_id, "dataset_id")
        records: list[ArgillaReviewRecord] = []
        offset = 0
        while True:
            page = self._transport.call(
                "GET",
                f"/datasets/{dataset_id}/records",
                query={
                    "include": "responses",
                    "offset": str(offset),
                    "limit": str(RECORD_PAGE_SIZE),
                },
            )
            items = _items(page, "record")
            records.extend(_record_of(item) for item in items)
            if len(records) > MAX_RECORDS:
                raise ArgillaRejectedError("Argilla dataset holds more records than a queue allows")
            if len(items) < RECORD_PAGE_SIZE:
                return records
            offset += RECORD_PAGE_SIZE

    def create_records(
        self, dataset_id: str, payloads: Sequence[Mapping[str, object]]
    ) -> list[ArgillaReviewRecord]:
        """Creates the records and returns what the server created, in request order. A reply
        that does not carry exactly one record per request item is refused (the Factory links
        its items to Argilla record ids from this reply)."""
        _require_uuid(dataset_id, "dataset_id")
        created: list[ArgillaReviewRecord] = []
        for start in range(0, len(payloads), BULK_CREATE_SIZE):
            chunk = [dict(item) for item in payloads[start : start + BULK_CREATE_SIZE]]
            reply = self._transport.call(
                "POST", f"/datasets/{dataset_id}/records/bulk", body={"items": chunk}
            )
            records = [_record_of(item) for item in _items(reply, "record")]
            if [r.external_id for r in records] != [item.get("external_id") for item in chunk]:
                raise ArgillaRejectedError("Argilla created different records than requested")
            created.extend(records)
        return created

    def get_record(self, record_id: str) -> ArgillaReviewRecord:
        _require_uuid(record_id, "record_id")
        return _record_of(self._transport.call("GET", f"/records/{record_id}"))

    # -- responses (call with the reviewer's own transport) ---------------

    def submit_response(
        self, record_id: str, decision: str, rationale: str
    ) -> ArgillaReviewResponse:
        _require_uuid(record_id, "record_id")
        body = {"values": response_values(decision, rationale), "status": "submitted"}
        return _response_of(
            self._transport.call("POST", f"/records/{record_id}/responses", body=body)
        )

    def update_response(
        self, response_id: str, decision: str, rationale: str
    ) -> ArgillaReviewResponse:
        _require_uuid(response_id, "response_id")
        body = {"values": response_values(decision, rationale), "status": "submitted"}
        return _response_of(self._transport.call("PUT", f"/responses/{response_id}", body=body))
