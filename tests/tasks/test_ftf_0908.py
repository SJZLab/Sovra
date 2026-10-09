#!/usr/bin/env python3
"""FTF-0908 -- External provider denial (no-DB unit/fault tests).

Proves, without a database or network, against the real default code:

1. ACC-1: a managed provider for training/inference is denied with a stable
   coded error under zero_egress, air_gapped, and standard without an exact
   provider+purpose approval; the audit reason is content-free.
2. ACC-2: audit goes through the certified FTF-0104 `write_audit_event` when
   a session is given (details: profile, surface, purpose, code only).
3. ACC-3 (Section 11.2): any missing approval field is a denial.
4. NEG-1: a "private cloud account" label never approves anything.
5. NEG-2: a weaker project setting / omitted job profile cannot lower the
   effective profile.
6. NEG-3: training and inference are separate purposes.
7. NEG-4: unknown / str-subclass / unhashable values raise the module's own
   typed error, never TypeError, never allow.
8. NET-0: no socket is opened or hostname resolved by any decision.
9. Fault injection (Law 1): each mutation of the real module runs in a fresh
   subprocess; its behavioural probe must change. The file is restored
   byte-identical.

Run directly: python3 tests/tasks/test_ftf_0908.py
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.external_provider_denial import (  # noqa: E402
    PURPOSES,
    AuditContext,
    ExternalProviderDeniedError,
    ExternalProviderRequest,
    InvalidProviderRequestError,
    ProviderApproval,
    authorize_external_provider,
)

MODULE = ROOT / "packages" / "security" / "external_provider_denial.py"
PROFILES = ("standard", "zero_egress", "air_gapped")
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _approval(**overrides: Any) -> ProviderApproval:
    fields: dict[str, Any] = {
        "provider": "acme",
        "purposes": frozenset(PURPOSES),
        "approved_by": "approver",
        "account": "acct",
        "region": "eu",
        "retention_terms": "30d",
        "training_terms": "no-train-on-data",
        "inference_terms": "no-retain",
        "applicable_agreement": "dpa-2026",
        "subprocessors": "listed-in-dpa",
        "deletion_behaviour": "deleted-30d",
    }
    fields.update(overrides)
    return ProviderApproval(**fields)


def _request(
    purpose: str = "training",
    org: str = "standard",
    project: str = "standard",
    job: str | None = None,
    provider: str = "acme",
    label: str | None = None,
    account: str | None = "acct",
    region: str | None = "eu",
) -> ExternalProviderRequest:
    return ExternalProviderRequest(provider, purpose, org, project, job, label, account, region)


def _outcome(request: Any, approval: Any = None, audit: Any = None) -> str:
    try:
        authorize_external_provider(request, approval, audit)
        return "allowed"
    except ExternalProviderDeniedError as exc:
        return exc.code
    except InvalidProviderRequestError as exc:
        return exc.code


class _Result:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self.row = row

    def one(self) -> tuple[Any, ...]:
        return self.row


class _RecordingSession:
    """Collects rows the real `write_audit_event` adds and every commit, in order.

    `tenant` (the bound context)/`has_writes` stand in for the two values the module reads from the
    caller's transaction; `executed` records every statement it runs (the
    tenant re-bind after the commit is one of them).
    """

    def __init__(self, tenant: str = "", has_writes: bool = False) -> None:
        self.added: list[Any] = []
        self.events: list[str] = []
        self.new: list[Any] = []
        self.dirty: list[Any] = []
        self.deleted: list[Any] = []
        self.bound = tenant  # the transaction-local tenant context; a commit ends it
        self.has_writes = has_writes
        self.executed: list[tuple[str, Any]] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        self.events.append("add")

    def commit(self) -> None:
        self.events.append("commit")
        self.bound = ""

    def execute(self, statement: Any, params: Any = None) -> _Result:
        self.executed.append((str(statement), params))
        if params is not None:
            self.bound = params["org_id"]
        return _Result((self.bound, self.has_writes))


def _audit(session: _RecordingSession) -> AuditContext:
    return AuditContext(cast(Any, session), uuid.uuid4(), "system", "actor-1", "req-1")


def _audit_with(session: Any = None, **overrides: Any) -> AuditContext:
    fields: dict[str, Any] = {
        "session": _RecordingSession() if session is None else session,
        "organization_id": uuid.uuid4(),
        "actor_type": "system",
        "actor_id": "actor-1",
        "request_id": "req-1",
    }
    fields.update(overrides)
    return AuditContext(**fields)


def test_denials_and_allows_for_every_profile_and_purpose() -> None:
    for profile, purpose in itertools.product(PROFILES, PURPOSES):
        request = _request(purpose, profile, profile)
        expected_without = (
            "external_provider_not_approved"
            if profile == "standard"
            else "external_provider_profile_blocked"
        )
        check(
            "FTF-0908-ACC1",
            f"{profile}/{purpose} without approval -> {expected_without}",
            _outcome(request) == expected_without,
        )
        expected_with = "allowed" if profile == "standard" else "external_provider_profile_blocked"
        check(
            "FTF-0908-ACC1",
            f"{profile}/{purpose} with full approval -> {expected_with}",
            _outcome(request, _approval()) == expected_with,
        )
    decision = authorize_external_provider(_request("training"), _approval())
    check(
        "FTF-0908-ACC1",
        "training decision uses the model surface",
        decision.allowed
        and decision.surface == "model"
        and decision.effective_profile == "standard",
    )
    decision = authorize_external_provider(_request("inference"), _approval())
    check(
        "FTF-0908-ACC1",
        "inference decision uses the serving surface",
        decision.surface == "serving",
    )


def test_denial_audit_is_content_free_and_goes_through_the_writer() -> None:
    session = _RecordingSession()
    marker = "SECRET-HOST.internal/path?prompt=hello"
    request = ExternalProviderRequest(marker, "training", "zero_egress", "standard", None, marker)
    try:
        authorize_external_provider(
            request, _approval(provider=marker, account=marker), _audit(session)
        )
        check("FTF-0908-ACC2", "zero_egress denial raised", False)
    except ExternalProviderDeniedError as exc:
        reason = exc.reason
        check(
            "FTF-0908-ACC2",
            "error carries the content-free reason",
            (reason.profile, reason.surface, reason.purpose, reason.code)
            == ("zero_egress", "model", "training", "external_provider_profile_blocked"),
        )
    check("FTF-0908-ACC2", "exactly one audit row written", len(session.added) == 1)
    if session.added:
        row = session.added[0]
        check(
            "FTF-0908-ACC2",
            "audit details are exactly profile/surface/purpose/code",
            row.details_json
            == {
                "profile": "zero_egress",
                "surface": "model",
                "purpose": "training",
                "code": "external_provider_profile_blocked",
            },
        )
        check(
            "FTF-0908-ACC2",
            "audit row carries no host/path/prompt/account text",
            "SECRET" not in json.dumps(row.details_json)
            and "SECRET"
            not in repr(
                (row.action, row.resource_type, row.resource_id, row.request_id, row.actor_id)
            ),
        )
        check("FTF-0908-ACC2", "audit action", row.action == "external_provider.denied")
    allowed_session = _RecordingSession()
    authorize_external_provider(_request(), _approval(), _audit(allowed_session))
    check("FTF-0908-ACC2", "an allowed decision writes no denial audit", not allowed_session.added)
    # Without a session the decision still carries the reason.
    try:
        authorize_external_provider(_request("inference", "air_gapped", "air_gapped"))
    except ExternalProviderDeniedError as exc:
        check(
            "FTF-0908-ACC2",
            "no-session denial still carries the reason",
            exc.reason.code == "external_provider_profile_blocked"
            and exc.reason.purpose == "inference",
        )


def test_every_missing_approval_field_is_a_denial() -> None:
    for purpose, terms_field in (("training", "training_terms"), ("inference", "inference_terms")):
        for field in (
            "approved_by",
            "account",
            "region",
            "retention_terms",
            "applicable_agreement",
            "subprocessors",
            "deletion_behaviour",
            terms_field,
        ):
            for blank in (None, "", "   "):
                outcome = _outcome(_request(purpose), _approval(**{field: blank}))
                check(
                    "FTF-0908-ACC3",
                    f"{purpose}: {field}={blank!r} -> incomplete",
                    outcome == "external_provider_approval_incomplete",
                )
    # The terms for the *other* purpose do not satisfy this purpose.
    check(
        "FTF-0908-ACC3",
        "inference needs inference_terms, not training_terms",
        _outcome(_request("inference"), _approval(inference_terms=None))
        == "external_provider_approval_incomplete",
    )
    check(
        "FTF-0908-ACC3",
        "training needs training_terms, not inference_terms",
        _outcome(_request("training"), _approval(training_terms=None))
        == "external_provider_approval_incomplete",
    )
    check(
        "FTF-0908-ACC3",
        "the other purpose's terms may be absent",
        _outcome(_request("training"), _approval(inference_terms=None)) == "allowed",
    )


def test_private_cloud_label_never_approves() -> None:
    for label in ("private cloud account", "own cloud", "private endpoint", "internal"):
        for profile in PROFILES:
            outcome = _outcome(_request("training", profile, profile, label=label))
            expected = (
                "external_provider_not_approved"
                if profile == "standard"
                else "external_provider_profile_blocked"
            )
            check(
                "FTF-0908-NEG1",
                f"label {label!r} under {profile} without approval -> {expected}",
                outcome == expected,
            )
        check(
            "FTF-0908-NEG1",
            f"label {label!r} cannot complete an incomplete approval",
            _outcome(_request("training", label=label), _approval(account=None))
            == "external_provider_approval_incomplete",
        )
    check(
        "FTF-0908-NEG1",
        "a label under zero_egress is blocked even with approval",
        _outcome(
            _request("inference", "zero_egress", "zero_egress", label="private cloud account"),
            _approval(),
        )
        == "external_provider_profile_blocked",
    )


def test_weaker_setting_or_omitted_job_profile_cannot_lower_the_profile() -> None:
    for private in ("zero_egress", "air_gapped"):
        for org, project, job in (
            (private, "standard", "standard"),
            (private, "standard", None),
            ("standard", private, "standard"),
            ("standard", private, None),
            ("standard", "standard", private),
        ):
            check(
                "FTF-0908-NEG2",
                f"org={org} project={project} job={job} -> blocked",
                _outcome(_request("training", org, project, job), _approval())
                == "external_provider_profile_blocked",
            )
    check(
        "FTF-0908-NEG2",
        "all-standard control with approval is allowed",
        _outcome(_request("training", "standard", "standard", "standard"), _approval())
        == "allowed",
    )


def test_training_and_inference_need_their_own_approval() -> None:
    inference_only = _approval(purposes=frozenset({"inference"}))
    training_only = _approval(purposes=frozenset({"training"}))
    check(
        "FTF-0908-NEG3",
        "an inference approval never authorizes training",
        _outcome(_request("training"), inference_only) == "external_provider_purpose_not_approved",
    )
    check(
        "FTF-0908-NEG3",
        "a training approval never authorizes inference",
        _outcome(_request("inference"), training_only) == "external_provider_purpose_not_approved",
    )
    check(
        "FTF-0908-NEG3",
        "inference approval authorizes inference",
        _outcome(_request("inference"), inference_only) == "allowed",
    )
    check(
        "FTF-0908-NEG3",
        "training approval authorizes training",
        _outcome(_request("training"), training_only) == "allowed",
    )
    check(
        "FTF-0908-NEG3",
        "an empty purposes set approves nothing",
        _outcome(_request("training"), _approval(purposes=frozenset()))
        == "external_provider_purpose_not_approved",
    )


def test_approval_is_for_the_exact_provider() -> None:
    for provider in ("ACME", " acme", "acme ", "acme2", "acm", "other"):
        check(
            "FTF-0908-ACC1",
            f"approval for 'acme' does not cover {provider!r}",
            _outcome(_request("training", provider=provider), _approval())
            == "external_provider_not_approved",
        )
    # Provider mismatch is reported as such even when the purpose also differs.
    check(
        "FTF-0908-ACC1",
        "wrong provider is not_approved before purpose is considered",
        _outcome(
            _request("training", provider="other"), _approval(purposes=frozenset({"inference"}))
        )
        == "external_provider_not_approved",
    )


class _Str(str):
    pass


def test_string_length_boundary_is_exactly_200() -> None:
    ok, too_long = "p" * 200, "p" * 201
    check(
        "FTF-0908-NEG4",
        "a 200-character provider is accepted",
        _outcome(_request(provider=ok), _approval(provider=ok)) == "allowed",
    )
    check(
        "FTF-0908-NEG4",
        "a 201-character provider is refused",
        _outcome(_request(provider=too_long), _approval()) == "external_provider_invalid_request",
    )
    check(
        "FTF-0908-NEG4",
        "a 200-character account is accepted",
        _outcome(_request(account=ok), _approval(account=ok)) == "allowed",
    )
    check(
        "FTF-0908-NEG4",
        "a 201-character account is refused",
        _approval_build_error(account=too_long),
    )


def _approval_build_error(**overrides: Any) -> bool:
    try:
        _approval(**overrides)
    except InvalidProviderRequestError:
        return True
    return False


def test_malformed_values_raise_the_typed_error() -> None:
    bad_values: list[Any] = ["", "   ", "x" * 500, 7, None, [], {}, _Str("acme")]
    for value in bad_values:
        check(
            "FTF-0908-NEG4",
            f"provider={value!r}",
            _outcome(
                ExternalProviderRequest(value, "training", "standard", "standard"), _approval()
            )
            == "external_provider_invalid_request",
        )
    for value in ["bogus", "", 7, None, [], {}, _Str("training"), "TRAINING"]:
        check(
            "FTF-0908-NEG4",
            f"purpose={value!r}",
            _outcome(ExternalProviderRequest("acme", value, "standard", "standard"), _approval())
            == "external_provider_invalid_request",
        )
    for value in ["bogus", "", 7, None, [], {}, _Str("standard"), "STANDARD"]:
        for slot in range(3):
            args: list[Any] = ["standard", "standard", None]
            args[slot] = value
            if slot == 2 and value is None:
                continue
            check(
                "FTF-0908-NEG4",
                f"profile slot {slot}={value!r}",
                _outcome(ExternalProviderRequest("acme", "training", *args), _approval())
                == "external_provider_invalid_request",
            )
    check(
        "FTF-0908-NEG4",
        "non-request object",
        _outcome("not a request", _approval()) == "external_provider_invalid_request",
    )
    check(
        "FTF-0908-NEG4",
        "non-approval object",
        _outcome(_request(), "approval") == "external_provider_invalid_request",
    )
    check(
        "FTF-0908-NEG4",
        "non-audit object",
        _outcome(_request(), _approval(), "audit") == "external_provider_invalid_request",
    )
    for build in (
        lambda: _approval(provider=_Str("acme")),
        lambda: _approval(purposes={"training"}),
        lambda: _approval(purposes=frozenset({"bogus"})),
        lambda: _approval(purposes=frozenset({_Str("training")})),
        lambda: _approval(account=7),
        lambda: _approval(region=[]),
        lambda: _approval(approved_by=_Str("a")),
    ):
        try:
            build()
            check("FTF-0908-NEG4", "malformed approval rejected", False)
        except InvalidProviderRequestError:
            pass
        except Exception as exc:  # TypeError etc.
            check("FTF-0908-NEG4", f"malformed approval raised {type(exc).__name__}", False)


class _TxSession(_RecordingSession):
    """Models the enclosing tenant-scoped session: rows survive only if committed,
    and the propagated error rolls back everything not yet committed."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: list[Any] = []
        self.persisted: list[Any] = []

    def add(self, row: Any) -> None:
        self.pending.append(row)

    def commit(self) -> None:
        self.persisted.extend(self.pending)
        self.pending = []

    def rollback(self) -> None:
        self.pending = []


def test_denial_audit_is_committed_before_the_error_is_raised() -> None:
    session = _RecordingSession()
    try:
        authorize_external_provider(_request(org="zero_egress"), _approval(), _audit(session))
        check("FTF-0908-R2-1", "deny raised", False)
    except ExternalProviderDeniedError:
        pass
    check(
        "FTF-0908-R2-1",
        "the audit row is added, then committed, before the error propagates",
        session.events == ["add", "commit"],
    )
    allowed = _RecordingSession()
    authorize_external_provider(_request(), _approval(), _audit(allowed))
    check("FTF-0908-R2-1", "an allow neither writes nor commits", allowed.events == [])
    # The real default code under the real rollback-on-exception shape.
    tx = _TxSession()
    try:
        authorize_external_provider(
            _request("inference", "standard", "air_gapped"), None, _audit_with(tx)
        )
    except ExternalProviderDeniedError:
        tx.rollback()
    check(
        "FTF-0908-R2-1",
        "the denial audit survives the enclosing session's rollback",
        len(tx.persisted) == 1 and not tx.pending,
    )
    # Every denial code is durable, not only the profile block.
    for label, request, approval in (
        ("not_approved", _request(), None),
        ("purpose", _request("inference"), _approval(purposes=frozenset({"training"}))),
        ("incomplete", _request(), _approval(region=None)),
        ("mismatch", _request(region="us"), _approval()),
    ):
        tx2 = _TxSession()
        try:
            authorize_external_provider(request, approval, _audit_with(tx2))
        except ExternalProviderDeniedError:
            tx2.rollback()
        check("FTF-0908-R2-1", f"{label}: denial audit durable", len(tx2.persisted) == 1)


def test_effective_profile_is_pinned_in_reason_and_audit() -> None:
    cases = (
        ("zero_egress", "standard", None, "zero_egress"),
        ("standard", "zero_egress", None, "zero_egress"),
        ("standard", "standard", "zero_egress", "zero_egress"),
        ("air_gapped", "standard", "standard", "air_gapped"),
        ("standard", "air_gapped", "zero_egress", "air_gapped"),
        ("zero_egress", "standard", "air_gapped", "air_gapped"),
        ("standard", "standard", "air_gapped", "air_gapped"),
    )
    for org, project, job, expected in cases:
        session = _RecordingSession()
        try:
            authorize_external_provider(
                _request("training", org, project, job), _approval(), _audit(session)
            )
            check("FTF-0908-R2-2", f"{org}/{project}/{job} denied", False)
            continue
        except ExternalProviderDeniedError as exc:
            reason_profile = exc.reason.profile
        details = session.added[0].details_json if session.added else {}
        check(
            "FTF-0908-R2-2",
            f"{org}/{project}/{job}: reason profile is {expected}",
            reason_profile == expected,
        )
        check(
            "FTF-0908-R2-2",
            f"{org}/{project}/{job}: audit profile is {expected}",
            details.get("profile") == expected,
        )


def test_audit_row_carries_the_audit_context() -> None:
    for actor_type, actor_id, request_id, purpose in (
        ("worker", "actor-xyz", "req-xyz", "inference"),
        ("user", "actor-abc.1", "req-abc:2", "training"),
    ):
        session = _RecordingSession()
        org_id = uuid.uuid4()
        context = _audit_with(
            session,
            organization_id=org_id,
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=request_id,
        )
        try:
            authorize_external_provider(_request(purpose, "zero_egress"), None, context)
        except ExternalProviderDeniedError:
            pass
        row = session.added[0] if session.added else None
        check("FTF-0908-R2-3", f"{actor_type}: one row written", row is not None)
        if row is None:
            continue
        check("FTF-0908-R2-3", f"{actor_type}: organization_id", row.organization_id == org_id)
        check("FTF-0908-R2-3", f"{actor_type}: request_id", row.request_id == request_id)
        check("FTF-0908-R2-3", f"{actor_type}: actor_id", row.actor_id == actor_id)
        check("FTF-0908-R2-3", f"{actor_type}: actor_type", row.actor_type == actor_type)
        check(
            "FTF-0908-R2-3", f"{actor_type}: resource_id is the purpose", row.resource_id == purpose
        )
        check(
            "FTF-0908-R2-3",
            f"{actor_type}: resource_type",
            row.resource_type == "external_provider_request",
        )


class _NoCommit:
    def add(self, row: Any) -> None:
        pass


class _NoAdd:
    def commit(self) -> None:
        pass


class _UuidSub(uuid.UUID):
    pass


def test_audit_context_is_validated_up_front() -> None:
    good = _RecordingSession()
    bad_contexts: list[tuple[str, AuditContext]] = [
        ("session=None", AuditContext(cast(Any, None), uuid.uuid4(), "system", "a", "r")),
        ("session without commit", _audit_with(_NoCommit())),
        ("session without add", _audit_with(_NoAdd())),
        ("session is a str", _audit_with("session")),
        ("organization_id str", _audit_with(good, organization_id=str(uuid.uuid4()))),
        ("organization_id None", _audit_with(good, organization_id=None)),
        ("organization_id subclass", _audit_with(good, organization_id=_UuidSub(int=5))),
        ("actor_type unknown", _audit_with(good, actor_type="admin")),
        ("actor_type upper", _audit_with(good, actor_type="SYSTEM")),
        ("actor_type None", _audit_with(good, actor_type=None)),
        ("actor_type subclass", _audit_with(good, actor_type=_Str("system"))),
        ("actor_type unhashable", _audit_with(good, actor_type=[])),
        ("actor_id empty", _audit_with(good, actor_id="")),
        ("actor_id space", _audit_with(good, actor_id="a b")),
        ("actor_id newline", _audit_with(good, actor_id="actor-1\n")),
        ("actor_id int", _audit_with(good, actor_id=7)),
        ("actor_id subclass", _audit_with(good, actor_id=_Str("actor-1"))),
        ("actor_id 129 chars", _audit_with(good, actor_id="a" * 129)),
        ("request_id empty", _audit_with(good, request_id="")),
        ("request_id slash", _audit_with(good, request_id="req/1")),
        ("request_id newline", _audit_with(good, request_id="req-1\n")),
        ("request_id None", _audit_with(good, request_id=None)),
        ("request_id subclass", _audit_with(good, request_id=_Str("req-1"))),
    ]
    for label, context in bad_contexts:
        for request_label, request, approval in (
            ("deny", _request(org="zero_egress"), _approval()),
            ("allow", _request(), _approval()),
        ):
            try:
                outcome = _outcome(request, approval, context)
            except BaseException as exc:  # a raw AttributeError / ValueError etc.
                outcome = f"CRASH:{type(exc).__name__}"
            check(
                "FTF-0908-R2-4",
                f"{label} ({request_label}) -> typed external_provider_invalid_request",
                outcome == "external_provider_invalid_request",
            )
    check("FTF-0908-R2-4", "a refused context writes and commits nothing", good.events == [])
    exact = _audit_with(_RecordingSession(), actor_id="a" * 128, request_id="r" * 128)
    check(
        "FTF-0908-R2-4",
        "128-character identifiers are accepted",
        _outcome(_request(org="zero_egress"), _approval(), exact)
        == "external_provider_profile_blocked",
    )


def test_section_11_2_fields_and_target_matching() -> None:
    for purpose in PURPOSES:
        for field in ("applicable_agreement", "subprocessors", "deletion_behaviour"):
            for blank in (None, "", "   "):
                check(
                    "FTF-0908-R2-5",
                    f"{purpose}: {field}={blank!r} -> incomplete",
                    _outcome(_request(purpose), _approval(**{field: blank}))
                    == "external_provider_approval_incomplete",
                )
        for target in ("other", "ACCT", "acct ", " acct", "acc", None):
            check(
                "FTF-0908-R2-5",
                f"{purpose}: target account {target!r} -> mismatch",
                _outcome(_request(purpose, account=target), _approval())
                == "external_provider_approval_mismatch",
            )
        for target in ("us", "EU", "eu ", " eu", "e", None):
            check(
                "FTF-0908-R2-5",
                f"{purpose}: target region {target!r} -> mismatch",
                _outcome(_request(purpose, region=target), _approval())
                == "external_provider_approval_mismatch",
            )
        check(
            "FTF-0908-R2-5",
            f"{purpose}: matching account and region -> allowed",
            _outcome(_request(purpose, account="acct", region="eu"), _approval()) == "allowed",
        )
    check(
        "FTF-0908-R2-5",
        "a label equal to the approval's account never supplies the target",
        _outcome(_request(label="acct", account=None), _approval())
        == "external_provider_approval_mismatch",
    )
    # Precedence: profile block, then missing approval, purpose, incomplete, mismatch.
    check(
        "FTF-0908-R2-5",
        "zero_egress is blocked before the mismatch is considered",
        _outcome(_request(org="zero_egress", account="other"), _approval())
        == "external_provider_profile_blocked",
    )
    check(
        "FTF-0908-R2-5",
        "an incomplete approval is reported before a mismatch",
        _outcome(_request(account="other"), _approval(subprocessors=None))
        == "external_provider_approval_incomplete",
    )
    check(
        "FTF-0908-R2-5",
        "a purpose gap is reported before a mismatch",
        _outcome(_request(account="other"), _approval(purposes=frozenset({"inference"})))
        == "external_provider_purpose_not_approved",
    )
    marker = "SECRET-ACCT-9"
    session = _RecordingSession()
    outcome = _outcome(
        _request(account=marker, region="SECRET-REGION-9"), _approval(), _audit(session)
    )
    check("FTF-0908-R2-5", "mismatch denial code", outcome == "external_provider_approval_mismatch")
    check(
        "FTF-0908-R2-5",
        "the mismatch audit is content-free",
        len(session.added) == 1
        and "SECRET" not in json.dumps(session.added[0].details_json)
        and session.added[0].details_json["code"] == "external_provider_approval_mismatch",
    )
    bad_targets: list[Any] = [7, [], {}, _Str("acct"), "x" * 201]
    for bad in bad_targets:
        check(
            "FTF-0908-R2-5",
            f"malformed target account {bad!r} -> invalid_request",
            _outcome(_request(account=cast(Any, bad)), _approval())
            == "external_provider_invalid_request",
        )
        check(
            "FTF-0908-R2-5",
            f"malformed target region {bad!r} -> invalid_request",
            _outcome(_request(region=cast(Any, bad)), _approval())
            == "external_provider_invalid_request",
        )
    for field in ("applicable_agreement", "subprocessors", "deletion_behaviour"):
        for bad in (7, [], _Str("x"), "x" * 201):
            check(
                "FTF-0908-R2-5",
                f"malformed approval {field}={bad!r}",
                _approval_build_error(**{field: bad}),
            )


def test_no_network_is_touched_by_any_decision() -> None:
    counts = {"connect": 0, "resolve": 0}
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def counting_connect(self: socket.socket, *args: Any) -> None:
        counts["connect"] += 1
        raise OSError("network disabled in test")

    def counting_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        counts["resolve"] += 1
        raise OSError("network disabled in test")

    socket.socket.connect = counting_connect  # type: ignore[method-assign,assignment]
    socket.getaddrinfo = counting_getaddrinfo
    try:
        for profile, purpose in itertools.product(PROFILES, PURPOSES):
            _outcome(_request(purpose, profile, profile), _approval())
            _outcome(_request(purpose, profile, profile))
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.getaddrinfo = real_getaddrinfo
    check(
        "FTF-0908-NET0",
        "zero connects and zero resolutions across all decisions",
        counts == {"connect": 0, "resolve": 0},
    )


_PROBE = r"""
import json, sys, uuid
sys.path.insert(0, {root!r}); sys.path.insert(0, {api!r})
from packages.security.external_provider_denial import *
class S(str): pass
class Rec:
    def __init__(self, bound="", writes=False, new=None, dirty=None, deleted=None):
        self.added = []; self.events = []; self.bound = bound; self.writes = writes
        self.new = new or []; self.dirty = dirty or []; self.deleted = deleted or []
    def add(self, row): self.added.append(row); self.events.append("add")
    def commit(self): self.events.append("commit"); self.bound = ""
    def execute(self, stmt, params=None):
        if params is not None: self.bound = params["org_id"]; self.events.append("bind")
        class R:
            def one(_, b=self.bound, w=self.writes): return (b, w)
        return R()
class NoCommit:
    def add(self, row): pass
    def execute(self, stmt, params=None): pass
    new = dirty = deleted = ()
class NoAdd:
    def commit(self): pass
    def execute(self, stmt, params=None): pass
    new = dirty = deleted = ()
class NoTracked:
    def add(self, row): pass
    def commit(self): pass
    def execute(self, stmt, params=None): pass
class NoExecute:
    def add(self, row): pass
    def commit(self): pass
    new = dirty = deleted = ()
def appr(**o):
    f = dict(provider="acme", purposes=frozenset(PURPOSES), approved_by="a", account="x",
             region="eu", retention_terms="30d", training_terms="t", inference_terms="i",
             applicable_agreement="dpa", subprocessors="sp", deletion_behaviour="del")
    f.update(o); return ProviderApproval(**f)
def req(purpose="training", org="standard", project="standard", job=None,
        provider="acme", label=None, account="x", region="eu"):
    return ExternalProviderRequest(provider, purpose, org, project, job, label, account, region)
def raw(*a): return ExternalProviderRequest(*a)
def run(request, approval=None, audit=None):
    try:
        d = authorize_external_provider(request, approval, audit)
        return "allowed:" + d.surface
    except ExternalProviderDeniedError as e: return e.code
    except InvalidProviderRequestError as e: return e.code
    except BaseException as e: return "CRASH:" + type(e).__name__
out = {{}}
out["zero"] = run(req(org="zero_egress"), appr())
out["air"] = run(req("inference", project="air_gapped"), appr())
out["none"] = run(req())
out["job_frozen"] = run(req(job="zero_egress"), appr())
out["project_weaker"] = run(req(org="zero_egress", project="standard"), appr())
out["inf_not_train"] = run(req("training"), appr(purposes=frozenset({{"inference"}})))
out["train_not_inf"] = run(req("inference"), appr(purposes=frozenset({{"training"}})))
out["wrong_provider"] = run(req(provider="other"), appr(purposes=frozenset({{"inference"}})))
out["label"] = run(req(label="private cloud account"))
out["label_zero"] = run(req(org="zero_egress", label="private cloud account"), appr())
for f in ("approved_by", "account", "region", "retention_terms", "training_terms"):
    out["miss_" + f] = run(req(), appr(**{{f: None}}))
out["blank_approved_by"] = run(req(), appr(approved_by="   "))
out["miss_inference_terms"] = run(req("inference"), appr(inference_terms=None, training_terms="t"))
out["ok_train"] = run(req(), appr())
out["ok_inf"] = run(req("inference"), appr())
out["sub_provider"] = run(raw(S("acme"), "training", "standard", "standard"), appr())
out["sub_purpose"] = run(raw("acme", S("training"), "standard", "standard"), appr())
out["sub_profile"] = run(raw("acme", "training", S("standard"), "standard"), appr())
out["unhash_provider"] = run(raw([], "training", "standard", "standard"), appr())
out["unhash_purpose"] = run(raw("acme", [], "standard", "standard"), appr())
out["unhash_profile"] = run(raw("acme", "training", {{}}, "standard"), appr())
out["unhash_job"] = run(raw("acme", "training", "standard", "standard", []), appr())
for f in ("applicable_agreement", "subprocessors", "deletion_behaviour"):
    out["miss_" + f] = run(req(), appr(**{{f: None}}))
out["mismatch_account"] = run(req(account="other"), appr())
out["mismatch_region"] = run(req(region="us"), appr())
out["mismatch_none_account"] = run(req(account=None), appr())
out["mismatch_none_region"] = run(req(region=None), appr())
STD = ("acme", "training", "standard", "standard", None, None)
out["bad_target_account"] = run(raw(*STD, 7, "eu"), appr())
out["bad_target_region"] = run(raw(*STD, "x", []), appr())
rec = Rec()
ORG = uuid.UUID(int=77)
ctx = AuditContext(rec, ORG, "worker", "actor-9", "req-9")
run(req("inference", org="zero_egress"), appr(), ctx)
out["audit"] = [r.details_json for r in rec.added]
out["audit_events"] = rec.events
# R3-1: the tenant context a commit discards is bound again; a second denial works.
recr = Rec(bound=str(uuid.UUID(int=5)))
run(req("inference", org="zero_egress"), appr(), AuditContext(recr, ORG, "worker", "a", "r"))
out["rebound_tenant"] = [recr.bound, recr.events]
rec_two = Rec(bound=str(uuid.UUID(int=5)))
ctx_two = AuditContext(rec_two, ORG, "worker", "a", "r")
out["second_denial"] = [run(req(org="zero_egress"), appr(), ctx_two) for _ in range(2)]
out["second_denial_rows"] = len(rec_two.added)
recn = Rec()
run(req(org="zero_egress"), appr(), AuditContext(recn, ORG, "worker", "a", "r"))
out["no_context_no_rebind"] = recn.events
# R4-1: a tenant context that is not an organization id is refused typed, before any write.
recx = Rec(bound="org-77")
out["non_uuid_tenant"] = [
    run(req("inference", org="zero_egress"), appr(), AuditContext(recx, ORG, "worker", "a", "r")),
    recx.events, len(recx.added),
]
# R3-2: pending work on the caller's session is refused before any decision.
for name, kw in (
    ("new", dict(new=[1])), ("dirty", dict(dirty=[1])), ("deleted", dict(deleted=[1])),
    ("flushed", dict(writes=True)),
):
    recp = Rec(**kw)
    out["pending_" + name] = [
        run(req(org="zero_egress"), appr(), AuditContext(recp, ORG, "system", "a", "r")),
        run(req(), appr(), AuditContext(recp, ORG, "system", "a", "r")),
        recp.events,
    ]
out["audit_row"] = [
    [str(r.organization_id), r.actor_type, r.actor_id, r.request_id, r.resource_id]
    for r in rec.added
]
rec3 = Rec()
try:
    c3 = AuditContext(rec3, ORG, "system", "a", "r")
    authorize_external_provider(req(job="air_gapped"), appr(), c3)
except ExternalProviderDeniedError as e:
    out["reason_job_profile"] = e.reason.profile
out["audit_job_profile"] = [r.details_json["profile"] for r in rec3.added]
recb = Rec()
for name, c in (
    ("session_none", AuditContext(None, ORG, "system", "a", "r")),
    ("session_nocommit", AuditContext(NoCommit(), ORG, "system", "a", "r")),
    ("session_noadd", AuditContext(NoAdd(), ORG, "system", "a", "r")),
    ("session_noexecute", AuditContext(NoExecute(), ORG, "system", "a", "r")),
    ("session_notracked", AuditContext(NoTracked(), ORG, "system", "a", "r")),
    ("org_str", AuditContext(recb, str(ORG), "system", "a", "r")),
    ("actor_type", AuditContext(recb, ORG, "admin", "a", "r")),
    ("actor_id", AuditContext(recb, ORG, "system", "a b", "r")),
    ("actor_id_nl", AuditContext(recb, ORG, "system", "a\n", "r")),
    ("actor_id_sub", AuditContext(recb, ORG, "system", S("a"), "r")),
    ("request_id", AuditContext(recb, ORG, "system", "a", "r/1")),
    ("request_id_none", AuditContext(recb, ORG, "system", "a", None)),
):
    out["bad_audit_" + name] = run(req(org="zero_egress"), appr(), c)
out["bad_audit_events"] = recb.events
rec2 = Rec()
run(req(), appr(), AuditContext(rec2, uuid.uuid4(), "system", "actor", "req"))
out["audit_on_allow"] = len(rec2.added)
print(json.dumps(out, sort_keys=True))
"""


def _run_probe(source: str | None) -> dict[str, Any]:
    original = MODULE.read_bytes()
    # A fresh bytecode cache per probe: a same-size, same-second mutation
    # would otherwise be served a stale .pyc of the unmutated module.
    cache_dir = tempfile.mkdtemp(prefix="ftf0908-pyc-")
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "pycache_prefix=" + cache_dir,
                "-c",
                _PROBE.format(root=str(ROOT), api=str(ROOT / "apps" / "api")),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        MODULE.write_bytes(original)
        shutil.rmtree(cache_dir, ignore_errors=True)
    if MODULE.read_bytes() != original:
        raise RuntimeError("module not restored")
    if result.returncode != 0:
        return {"ERROR": result.stderr[-300:]}
    return cast(dict[str, Any], json.loads(result.stdout))


_TERMS_RE = (
    r"approval\.training_terms if request\.purpose == \"training\""
    r" else approval\.inference_terms"
)
# (label, regex, replacement, probe keys that must change)
_MUTATIONS: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "skip-enforce-effect",
        r"        enforce_effect\(effect, authorization\)\n",
        "        pass\n",
        ("zero", "air", "none", "job_frozen", "project_weaker", "label", "label_zero"),
    ),
    (
        "destination-internal",
        r'Destination\("managed_provider", request\.provider\)',
        'Destination("internal")',
        ("zero", "air", "none", "job_frozen", "project_weaker", "label", "label_zero"),
    ),
    (
        "blocked-code-from-gap",
        r'code = "external_provider_profile_blocked"',
        'code = gap or "external_provider_not_approved"',
        ("zero", "air", "job_frozen", "label_zero"),
    ),
    (
        "no-purpose-check",
        r"    if request\.purpose not in approval\.purposes:\n",
        "    if False:\n",
        ("inf_not_train", "train_not_inf"),
    ),
    (
        "no-provider-gap-check",
        r"if approval is None or approval\.provider != request\.provider:",
        "if approval is None:",
        ("wrong_provider",),
    ),
    (
        "terms-ignore-purpose",
        _TERMS_RE,
        "approval.training_terms",
        ("miss_inference_terms",),
    ),
    (
        "terms-swapped",
        _TERMS_RE,
        'approval.inference_terms if request.purpose == "training" else approval.training_terms',
        ("miss_training_terms", "miss_inference_terms"),
    ),
    (
        "drop-approved_by",
        r"            approval\.approved_by,\n            approval\.account,",
        "            approval.account,",
        ("miss_approved_by",),
    ),
    ("drop-account", r"            approval\.account,\n", "", ("miss_account",)),
    ("drop-region", r"            approval\.region,\n", "", ("miss_region",)),
    (
        "drop-retention",
        r"            approval\.retention_terms,\n",
        "",
        ("miss_retention_terms",),
    ),
    (
        "drop-terms",
        r"            terms,\n",
        "",
        ("miss_training_terms", "miss_inference_terms"),
    ),
    (
        "filled-accepts-blank",
        r"return value is not None and bool\(value\) and not value\.isspace\(\)",
        "return value is not None",
        ("blank_approved_by",),
    ),
    (
        "text-accepts-subclass",
        r"if type\(value\) is not str or not value or value\.isspace\(\)",
        "if not isinstance(value, str) or not value or value.isspace()",
        ("sub_provider",),
    ),
    (
        "member-accepts-subclass",
        r"if type\(value\) is not str or value not in allowed:",
        "if not isinstance(value, str) or value not in allowed:",
        ("sub_purpose", "sub_profile"),
    ),
    (
        "provider-not-validated",
        r'    _exact_text\(request\.provider, "provider"\)\n',
        "",
        ("sub_provider", "unhash_provider"),
    ),
    (
        "no-audit",
        r"    if audit is not None:\n        write_audit_event",
        "    if False:\n        write_audit_event",
        ("audit",),
    ),
    ("audit-drop-purpose", r'                "purpose": reason\.purpose,\n', "", ("audit",)),
    (
        "audit-on-allow",
        r"        return ExternalProviderDecision\(True, profile, request\.purpose, surface\)",
        "        _deny('x', AuditReason(profile, surface, request.purpose, 'x'), audit)\n"
        "        return ExternalProviderDecision(True, profile, request.purpose, surface)",
        ("audit_on_allow",),
    ),
    (
        "no-commit-before-raise",
        r"        audit\.session\.commit\(\)\n",
        "",
        ("audit_events",),
    ),
    (
        "profile-computed-without-job",
        r"    profile = effective_profile\(\n        request\.organization_profile, "
        r"request\.project_profile, request\.job_profile\n    \)",
        "    profile = effective_profile(request.organization_profile, request.project_profile)",
        ("reason_job_profile", "audit_job_profile"),
    ),
    (
        "audit-organization-id-constant",
        r"organization_id=audit\.organization_id,",
        "organization_id=uuid.UUID(int=1),",
        ("audit_row",),
    ),
    (
        "audit-request-id-constant",
        r"request_id=audit\.request_id,",
        "request_id='req-const',",
        ("audit_row",),
    ),
    (
        "audit-actor-id-constant",
        r"actor_id=audit\.actor_id,",
        "actor_id='actor-const',",
        ("audit_row",),
    ),
    (
        "audit-actor-type-constant",
        r"actor_type=audit\.actor_type,",
        "actor_type='system',",
        ("audit_row",),
    ),
    (
        "audit-resource-id-constant",
        r"resource_id=reason\.purpose,",
        "resource_id='x',",
        ("audit_row",),
    ),
    (
        "audit-validation-skipped",
        r"        audit = _validate_audit\(audit\)\n",
        "        pass\n",
        (
            "bad_audit_session_none",
            "bad_audit_org_str",
            "bad_audit_actor_type",
            "bad_audit_actor_id",
            "bad_audit_request_id",
        ),
    ),
    (
        "audit-session-commit-unchecked",
        r'for method in \("add", "commit", "execute"\):',
        'for method in ("add", "execute"):',
        ("bad_audit_session_nocommit",),
    ),
    (
        "audit-session-add-unchecked",
        r'for method in \("add", "commit", "execute"\):',
        'for method in ("commit", "execute"):',
        ("bad_audit_session_none", "bad_audit_session_noadd"),
    ),
    (
        "audit-session-execute-unchecked",
        r'for method in \("add", "commit", "execute"\):',
        'for method in ("add", "commit"):',
        ("bad_audit_session_noexecute",),
    ),
    (
        "audit-session-tracking-attrs-unchecked",
        r"        if getattr\(audit\.session, tracked, None\) is None:\n",
        "        if False:\n",
        ("bad_audit_session_notracked",),
    ),
    (
        "audit-session-tracking-attrs-no-raise",
        r"(getattr\(audit\.session, tracked, None\) is None:\n)            raise "
        r'InvalidProviderRequestError\("audit session must be a database session"\)',
        r"\1            pass",
        ("bad_audit_session_notracked",),
    ),
    (
        "tenant-not-rebound-after-commit",
        r"        if tenant is not None:\n",
        "        if False:\n",
        ("rebound_tenant", "second_denial"),
    ),
    (
        "rebind-uses-audit-org-not-session-tenant",
        r"set_tenant_context\(audit\.session, tenant\)",
        "set_tenant_context(audit.session, audit.organization_id)",
        ("rebound_tenant", "no_context_no_rebind"),
    ),
    (
        "rebind-unconditional",
        r"        if tenant is not None:\n",
        "        if True:\n",
        ("no_context_no_rebind",),
    ),
    (
        "non-uuid-tenant-raw-valueerror",
        r"        raise InvalidProviderRequestError\(\n"
        r'            "audit session tenant context is not an organization id"\n'
        r"        \) from None\n",
        "        raise\n",
        ("non_uuid_tenant",),
    ),
    (
        "pending-guard-skipped",
        r"        tenant = _bound_tenant\(audit\)\n",
        "        tenant = ''\n",
        ("pending_new", "pending_dirty", "pending_deleted", "pending_flushed"),
    ),
    (
        "pending-unit-of-work-ignored",
        r"    if session\.new or session\.dirty or session\.deleted:\n",
        "    if False:\n",
        ("pending_new", "pending_dirty", "pending_deleted"),
    ),
    (
        "pending-new-ignored",
        r"session\.new or session\.dirty",
        "session.dirty",
        ("pending_new",),
    ),
    (
        "pending-dirty-ignored",
        r"session\.dirty or session\.deleted",
        "session.deleted",
        ("pending_dirty",),
    ),
    (
        "pending-deleted-ignored",
        r"or session\.deleted:",
        ":",
        ("pending_deleted",),
    ),
    (
        "pending-flushed-ignored",
        r"    if has_writes:\n",
        "    if False:\n",
        ("pending_flushed",),
    ),
    (
        "pending-guard-early-return-allowed-only",
        r"    session = audit\.session\n",
        "    session = audit.session\n"
        "    if not (session.new or session.dirty or session.deleted):\n"
        "        return ''\n",
        ("pending_flushed",),
    ),
    (
        "audit-org-type-unchecked",
        r"    if type\(audit\.organization_id\) is not uuid\.UUID:",
        "    if False:",
        ("bad_audit_org_str",),
    ),
    (
        "audit-actor-type-unchecked",
        r"    if type\(audit\.actor_type\) is not str or "
        r"audit\.actor_type not in ALLOWED_ACTOR_TYPES:",
        "    if False:",
        ("bad_audit_actor_type",),
    ),
    (
        "audit-identifier-match-not-fullmatch",
        r"_SAFE_IDENTIFIER\.fullmatch\(value\)",
        "_SAFE_IDENTIFIER.match(value)",
        ("bad_audit_actor_id_nl",),
    ),
    (
        "audit-identifier-accepts-subclass",
        r"if type\(value\) is not str or not _SAFE_IDENTIFIER",
        "if not isinstance(value, str) or not _SAFE_IDENTIFIER",
        ("bad_audit_actor_id_sub",),
    ),
    (
        "audit-identifier-unchecked",
        r"or not _SAFE_IDENTIFIER\.fullmatch\(value\)",
        "",
        ("bad_audit_actor_id", "bad_audit_request_id", "bad_audit_actor_id_nl"),
    ),
    (
        "drop-applicable_agreement",
        r"            approval\.applicable_agreement,\n",
        "",
        ("miss_applicable_agreement",),
    ),
    ("drop-subprocessors", r"            approval\.subprocessors,\n", "", ("miss_subprocessors",)),
    (
        "drop-deletion_behaviour",
        r"            approval\.deletion_behaviour,\n",
        "",
        ("miss_deletion_behaviour",),
    ),
    (
        "account-mismatch-ignored",
        r"approval\.account != request\.target_account or ",
        "",
        ("mismatch_account", "mismatch_none_account"),
    ),
    (
        "region-mismatch-ignored",
        r" or approval\.region != request\.target_region",
        "",
        ("mismatch_region", "mismatch_none_region"),
    ),
    (
        "target-account-unvalidated",
        r'    _optional_text\(request\.target_account, "target account"\)\n',
        "",
        ("bad_target_account",),
    ),
    (
        "target-region-unvalidated",
        r'    _optional_text\(request\.target_region, "target region"\)\n',
        "",
        ("bad_target_region",),
    ),
    (
        "surface-swapped",
        r'\{"training": "model", "inference": "serving"\}',
        '{"training": "serving", "inference": "model"}',
        ("ok_train", "ok_inf"),
    ),
]


def test_each_guard_is_load_bearing() -> None:
    baseline = _run_probe(None)
    check("FTF-0908-FAULT", "baseline probe runs", "ERROR" not in baseline)
    check(
        "FTF-0908-FAULT",
        "baseline outcomes are the expected codes",
        baseline.get("zero") == "external_provider_profile_blocked"
        and baseline.get("none") == "external_provider_not_approved"
        and baseline.get("ok_train") == "allowed:model"
        and baseline.get("unhash_profile") == "external_provider_invalid_request"
        and baseline.get("audit_on_allow") == 0
        and len(baseline.get("audit", [])) == 1
        and baseline.get("audit_events") == ["add", "commit"]
        and baseline.get("reason_job_profile") == "air_gapped"
        and baseline.get("audit_job_profile") == ["air_gapped"]
        and baseline.get("audit_row")
        == [[str(uuid.UUID(int=77)), "worker", "actor-9", "req-9", "inference"]]
        and baseline.get("mismatch_account") == "external_provider_approval_mismatch"
        and baseline.get("bad_audit_events") == []
        and baseline.get("rebound_tenant") == [str(uuid.UUID(int=5)), ["add", "commit", "bind"]]
        and baseline.get("non_uuid_tenant") == ["external_provider_invalid_request", [], 0]
        and baseline.get("second_denial") == ["external_provider_profile_blocked"] * 2
        and baseline.get("second_denial_rows") == 2
        and baseline.get("no_context_no_rebind") == ["add", "commit"]
        and all(
            baseline.get("pending_" + n)
            == ["external_provider_invalid_request", "external_provider_invalid_request", []]
            for n in ("new", "dirty", "deleted", "flushed")
        )
        and all(
            baseline.get("bad_audit_" + n) == "external_provider_invalid_request"
            for n in (
                "session_none",
                "session_nocommit",
                "session_noadd",
                "session_noexecute",
                "session_notracked",
                "org_str",
                "actor_type",
                "actor_id",
                "actor_id_nl",
                "actor_id_sub",
                "request_id",
                "request_id_none",
            )
        ),
    )
    text = MODULE.read_text(encoding="utf-8")
    for label, pattern, replacement, keys in _MUTATIONS:
        mutated, count = re.subn(pattern, replacement.replace("\\", "\\\\"), text, count=1)
        check("FTF-0908-FAULT", f"{label}: mutation pattern matches once", count == 1)
        if count != 1:
            continue
        result = _run_probe(mutated)
        changed = [k for k in keys if result.get(k) != baseline.get(k)]
        check("FTF-0908-FAULT", f"{label}: probe changes (expected one of {keys})", bool(changed))


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0908 External provider denial tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0908 External provider denial tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
