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
) -> ExternalProviderRequest:
    return ExternalProviderRequest(provider, purpose, org, project, job, label)


def _outcome(request: Any, approval: Any = None, audit: Any = None) -> str:
    try:
        authorize_external_provider(request, approval, audit)
        return "allowed"
    except ExternalProviderDeniedError as exc:
        return exc.code
    except InvalidProviderRequestError as exc:
        return exc.code


class _RecordingSession:
    """Collects rows the real `write_audit_event` adds; never commits."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


def _audit(session: _RecordingSession) -> AuditContext:
    return AuditContext(cast(Any, session), uuid.uuid4(), "system", "actor-1", "req-1")


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
        for field in ("approved_by", "account", "region", "retention_terms", terms_field):
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
        _outcome(_request(), _approval(account=ok)) == "allowed",
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
    def __init__(self): self.added = []
    def add(self, row): self.added.append(row)
def appr(**o):
    f = dict(provider="acme", purposes=frozenset(PURPOSES), approved_by="a", account="x",
             region="eu", retention_terms="30d", training_terms="t", inference_terms="i")
    f.update(o); return ProviderApproval(**f)
def req(purpose="training", org="standard", project="standard", job=None,
        provider="acme", label=None):
    return ExternalProviderRequest(provider, purpose, org, project, job, label)
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
rec = Rec()
ctx = AuditContext(rec, uuid.uuid4(), "system", "actor", "req")
run(req(org="zero_egress"), appr(), ctx)
out["audit"] = [r.details_json for r in rec.added]
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
        r"            approval\.retention_terms,\n            terms,",
        "            terms,",
        ("miss_retention_terms",),
    ),
    (
        "drop-terms",
        r"            approval\.retention_terms,\n            terms,",
        "            approval.retention_terms,",
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
        and len(baseline.get("audit", [])) == 1,
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
