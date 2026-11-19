#!/usr/bin/env python3
"""FTF-0907 -- External judge denial (no-DB unit/fault tests).

Proves, without a database or network, against the real default code:

1. ACC-1: an external or unknown-upstream judge is denied at admission and at
   request time on private (zero_egress / air_gapped) projects, with a stable
   coded error and a content-free audit reason.
2. NEG-1: a fallback alias (external, unknown or un-enumerated) cannot bypass the
   boundary check.
3. NEG-2: the direct client path cannot bypass: a missing, forged or replaced
   admission, or a changed route, is refused and `send` is never invoked.
4. NEG-3: a weaker project setting / omitted job profile cannot lower the profile;
   a profile tightened after admission denies at request time.
5. NEG-4: unknown / str-subclass / unhashable values raise the module's own typed
   error, never TypeError, never allow.
6. NET-0: no socket is opened by any decision.
7. Fault injection (Law 1): each mutation of the real module runs in a fresh
   subprocess; its behavioural probe must change. The file is restored
   byte-identical.

Run directly: python3 tests/tasks/test_ftf_0907.py
"""

# ruff: noqa: E501, B023

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.external_judge_denial import (  # noqa: E402
    ExternalJudgeDeniedError,
    FallbackAlias,
    InvalidJudgeRequestError,
    JudgeAdmission,
    JudgeRoute,
    ProfilesInForce,
    admit_judge_route,
    check_judge_request,
    guarded_judge_call,
)
from packages.security.security_profile_enforcement import (  # noqa: E402
    Destination,
    RouteAuthorization,
)

MODULE = ROOT / "packages" / "security" / "external_judge_denial.py"
PRIVATE = ("zero_egress", "air_gapped")
FAILURES: list[str] = []

INTERNAL = Destination("internal")
EXTERNAL = Destination("external", "acme")
MANAGED = Destination("managed_provider", "acme")


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _route(**overrides: Any) -> JudgeRoute:
    fields: dict[str, Any] = {
        "judge_model_id": "judge-1",
        "upstream": INTERNAL,
        "fallbacks": (FallbackAlias("alias-1", INTERNAL),),
        "fallbacks_enumerated": True,
    }
    fields.update(overrides)
    return JudgeRoute(**fields)


def _profiles(profile: str, **overrides: Any) -> ProfilesInForce:
    fields: dict[str, Any] = {"organization_profile": profile, "project_profile": profile}
    fields.update(overrides)
    return ProfilesInForce(**fields)


def _code(fn: Any) -> str:
    try:
        fn()
    except ExternalJudgeDeniedError as error:
        return error.code
    except InvalidJudgeRequestError as error:
        return error.code
    return "allowed"


def test_acc1_admission_denies() -> None:
    for profile in PRIVATE:
        p = _profiles(profile)
        check(
            "FTF-0907-ACC-1",
            f"{profile}: internal judge admitted",
            _code(lambda: admit_judge_route(_route(), p)) == "allowed",
        )
        for upstream in (EXTERNAL, MANAGED):
            code = _code(lambda: admit_judge_route(_route(upstream=upstream), p))
            check(
                "FTF-0907-ACC-1",
                f"{profile}: {upstream.kind} upstream denied",
                code == "external_judge_upstream_external",
            )
        code = _code(lambda: admit_judge_route(_route(upstream=None), p))
        check(
            "FTF-0907-ACC-1",
            f"{profile}: unknown upstream denied",
            code == "external_judge_upstream_unknown",
        )
    # a managed-provider authorization never opens a private project
    auth = RouteAuthorization("acme", frozenset({"judge"}), "approver", "a", "eu", "terms")
    code = _code(
        lambda: admit_judge_route(_route(upstream=MANAGED), _profiles("zero_egress"), auth)
    )
    check(
        "FTF-0907-ACC-1",
        "authorization never opens a private project",
        code == "external_judge_upstream_external",
    )
    # standard: external needs an explicit authorization; unknown is always denied
    std = _profiles("standard")
    code = _code(lambda: admit_judge_route(_route(upstream=EXTERNAL), std))
    check(
        "FTF-0907-ACC-1",
        "standard: external judge without authorization denied",
        code == "external_judge_upstream_external",
    )
    ext_auth = RouteAuthorization("acme", frozenset({"judge"}), "approver")
    code = _code(lambda: admit_judge_route(_route(upstream=EXTERNAL), std, ext_auth))
    check("FTF-0907-ACC-1", "standard: authorized external judge admitted", code == "allowed")
    code = _code(lambda: admit_judge_route(_route(upstream=None), std, ext_auth))
    check(
        "FTF-0907-ACC-1",
        "standard: unknown upstream denied even with authorization",
        code == "external_judge_upstream_unknown",
    )


def test_acc1_request_time_and_audit() -> None:
    p = _profiles("zero_egress")
    admission = admit_judge_route(_route(), p)
    check(
        "FTF-0907-ACC-1",
        "request-time allowed for the admitted route",
        _code(lambda: check_judge_request(admission, _route(), p)) == "allowed",
    )
    # profile tightened after admission: standard admission, then private request
    std_admission = admit_judge_route(
        _route(upstream=EXTERNAL),
        _profiles("standard"),
        RouteAuthorization("acme", frozenset({"judge"}), "approver"),
    )
    code = _code(lambda: check_judge_request(std_admission, _route(upstream=EXTERNAL), p))
    check(
        "FTF-0907-ACC-1",
        "request time re-evaluates the profile in force now",
        code == "external_judge_upstream_external",
    )
    try:
        admit_judge_route(_route(upstream=EXTERNAL), p)
    except ExternalJudgeDeniedError as error:
        text = repr(error.reason) + str(error)
        check(
            "FTF-0907-ACC-1",
            "audit reason is content-free",
            "acme" not in text
            and "judge-1" not in text
            and error.reason.profile == "zero_egress"
            and error.reason.surface == "judge",
        )
    else:
        check("FTF-0907-ACC-1", "external denial raised", False)


def test_neg1_fallbacks() -> None:
    for profile in PRIVATE:
        p = _profiles(profile)
        for label, route in (
            (
                "external fallback",
                _route(fallbacks=(FallbackAlias("a", INTERNAL), FallbackAlias("b", EXTERNAL))),
            ),
            ("unknown fallback", _route(fallbacks=(FallbackAlias("a", None),))),
            ("un-enumerated fallbacks", _route(fallbacks_enumerated=False)),
        ):
            code = _code(lambda: admit_judge_route(route, p))
            check(
                "FTF-0907-NEG-1",
                f"{profile}: {label} denied at admission",
                code == "external_judge_fallback_denied",
            )
        # request time: a route with an added fallback is not the admitted route
        admission = admit_judge_route(_route(), p)
        added = _route(
            fallbacks=(FallbackAlias("alias-1", INTERNAL), FallbackAlias("evil", EXTERNAL))
        )
        code = _code(lambda: check_judge_request(admission, added, p))
        check(
            "FTF-0907-NEG-1",
            f"{profile}: fallback added after admission denied",
            code == "external_judge_route_changed",
        )
    # an alias that merely looks local is not evidence
    code = _code(
        lambda: admit_judge_route(
            _route(fallbacks=(FallbackAlias("localhost-internal", EXTERNAL),)),
            _profiles("air_gapped"),
        )
    )
    check(
        "FTF-0907-NEG-1",
        "a local-looking alias name upgrades nothing",
        code == "external_judge_fallback_denied",
    )


def test_neg2_direct_path() -> None:
    p = _profiles("zero_egress")
    admission = admit_judge_route(_route(), p)
    calls: list[int] = []

    def send(_route: JudgeRoute) -> str:
        calls.append(1)
        return "ok"

    check(
        "FTF-0907-NEG-2",
        "guarded call returns send result",
        guarded_judge_call(admission, _route(), p, send) == "ok" and calls == [1],
    )
    calls.clear()
    forged = dataclasses.replace(admission)
    for label, adm in (("none", None), ("string", "x"), ("replaced", forged)):
        code = _code(lambda: guarded_judge_call(adm, _route(), p, send))
        check(
            "FTF-0907-NEG-2",
            f"{label} admission refused",
            code == "external_judge_admission_missing",
        )
    handbuilt = JudgeAdmission(_route(upstream=EXTERNAL), "zero_egress")
    code = _code(lambda: guarded_judge_call(handbuilt, _route(upstream=EXTERNAL), p, send))
    check(
        "FTF-0907-NEG-2", "hand-built admission refused", code == "external_judge_admission_missing"
    )
    code = _code(lambda: guarded_judge_call(admission, _route(upstream=EXTERNAL), p, send))
    check("FTF-0907-NEG-2", "changed route refused", code == "external_judge_route_changed")
    code = _code(lambda: guarded_judge_call(admission, _route(judge_model_id="other"), p, send))
    check("FTF-0907-NEG-2", "changed judge model refused", code == "external_judge_route_changed")
    check("FTF-0907-NEG-2", "send never invoked on any denial", calls == [])


def test_neg2_authorization_fields() -> None:
    class Lie(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        __hash__ = str.__hash__

    std = _profiles("standard")
    ext = _route(upstream=EXTERNAL)
    for label, auth in (
        ("provider", RouteAuthorization(Lie("zzz"), frozenset({"judge"}), "me")),
        ("surface", RouteAuthorization("acme", frozenset({Lie("judge")}), "me")),
        ("account", RouteAuthorization("acme", frozenset({"judge"}), "me", cast(Any, Lie("a")))),
    ):
        code = _code(lambda: admit_judge_route(ext, std, auth))
        check(
            "FTF-0907-NEG-2",
            f"{label} str-subclass authorization refused",
            code == "external_judge_invalid_request",
        )


def test_neg2_subclasses() -> None:
    p = _profiles("zero_egress")

    class R2(JudgeRoute):
        calls = 0
        judge_model_id = "m"
        fallbacks = ()
        fallbacks_enumerated = True

        def __init__(self) -> None:
            pass

        @property
        def upstream(self) -> Destination:
            R2.calls += 1
            return INTERNAL if R2.calls <= 2 else EXTERNAL

    class FA(FallbackAlias):
        pass

    class PF(ProfilesInForce):
        pass

    class RA(RouteAuthorization):
        pass

    class Adm(JudgeAdmission):
        pass

    check(
        "FTF-0907-NEG-2",
        "route subclass refused",
        _code(lambda: admit_judge_route(R2(), p)) == "external_judge_invalid_request",
    )
    check(
        "FTF-0907-NEG-2",
        "fallback subclass refused",
        _code(lambda: _route(fallbacks=(FA("a", INTERNAL),))) == "external_judge_invalid_request",
    )
    check(
        "FTF-0907-NEG-2",
        "profiles subclass refused",
        _code(lambda: admit_judge_route(_route(), PF("zero_egress", "zero_egress")))
        == "external_judge_invalid_request",
    )
    check(
        "FTF-0907-NEG-2",
        "authorization subclass refused",
        _code(lambda: admit_judge_route(_route(), p, RA("acme", frozenset({"judge"}), "x")))
        == "external_judge_invalid_request",
    )
    admission = admit_judge_route(_route(), p)
    sub = Adm(admission.route, admission.profile)
    object.__setattr__(sub, "_issued", admission._issued)
    check(
        "FTF-0907-NEG-2",
        "admission subclass refused",
        _code(lambda: check_judge_request(sub, _route(), p)) == "external_judge_admission_missing",
    )
    seen: list[JudgeRoute] = []
    guarded_judge_call(admission, _route(), p, lambda r: seen.append(r))
    check(
        "FTF-0907-NEG-2",
        "send receives the admitted snapshot",
        len(seen) == 1 and seen[0] is admission.route,
    )


def test_neg3_profile_cannot_be_lowered() -> None:
    weak = ProfilesInForce("zero_egress", "standard", None)
    code = _code(lambda: admit_judge_route(_route(upstream=EXTERNAL), weak))
    check(
        "FTF-0907-NEG-3",
        "weaker project setting cannot lower the org profile",
        code == "external_judge_upstream_external",
    )
    weak_job = ProfilesInForce("standard", "standard", "air_gapped")
    code = _code(lambda: admit_judge_route(_route(upstream=EXTERNAL), weak_job))
    check("FTF-0907-NEG-3", "job snapshot tightens", code == "external_judge_upstream_external")
    omitted = ProfilesInForce("standard", "zero_egress", None)
    code = _code(lambda: admit_judge_route(_route(upstream=EXTERNAL), omitted))
    check(
        "FTF-0907-NEG-3",
        "omitted job profile cannot lower",
        code == "external_judge_upstream_external",
    )


class _S(str):
    pass


class _Liar(str):
    def __eq__(self, other: object) -> bool:
        return True

    __hash__ = str.__hash__


def test_neg4_invalid_values() -> None:
    p = _profiles("zero_egress")
    bad: list[tuple[str, Any]] = [
        ("route None", lambda: admit_judge_route(cast(Any, None), p)),
        ("route dict", lambda: admit_judge_route(cast(Any, {}), p)),
        ("profiles None", lambda: admit_judge_route(_route(), cast(Any, None))),
        (
            "profile str-subclass",
            lambda: admit_judge_route(_route(), _profiles(cast(Any, _S("standard")))),
        ),
        (
            "profile unhashable",
            lambda: admit_judge_route(_route(), _profiles(cast(Any, ["standard"]))),
        ),
        ("profile unknown", lambda: admit_judge_route(_route(), _profiles("nope"))),
        ("model id str-subclass", lambda: _route(judge_model_id=cast(Any, _S("m")))),
        ("model id blank", lambda: _route(judge_model_id="  ")),
        ("model id unhashable", lambda: _route(judge_model_id=cast(Any, []))),
        ("upstream str", lambda: _route(upstream=cast(Any, "internal"))),
        ("fallbacks list", lambda: _route(fallbacks=cast(Any, []))),
        ("fallback element", lambda: _route(fallbacks=cast(Any, ("x",)))),
        ("fallback alias blank", lambda: _route(fallbacks=(FallbackAlias("", INTERNAL),))),
        (
            "fallback upstream str",
            lambda: _route(fallbacks=(FallbackAlias("a", cast(Any, "internal")),)),
        ),
        ("model id 201 chars", lambda: _route(judge_model_id="m" * 201)),
        (
            "unknown profile at request time",
            lambda: check_judge_request(cast(Any, None), _route(), _profiles("nope")),
        ),
        (
            "upstream kind str-subclass",
            lambda: _route(upstream=Destination(cast(Any, _Liar("managed_provider")), None)),
        ),
        (
            "fallback kind str-subclass",
            lambda: _route(
                fallbacks=(
                    FallbackAlias("a", Destination(cast(Any, _Liar("managed_provider")), None)),
                )
            ),
        ),
        (
            "upstream provider str-subclass",
            lambda: _route(upstream=Destination("external", cast(Any, _Liar("acme")))),
        ),
        ("enumerated non-bool", lambda: _route(fallbacks_enumerated=cast(Any, 1))),
        ("authorization wrong type", lambda: admit_judge_route(_route(), p, cast(Any, "auth"))),
    ]
    for label, fn in bad:
        try:
            fn()
        except InvalidJudgeRequestError as error:
            check(
                "FTF-0907-NEG-4",
                f"{label}: typed error code",
                error.code == "external_judge_invalid_request",
            )
        except BaseException as error:  # noqa: BLE001
            check(
                "FTF-0907-NEG-4",
                f"{label}: raised {type(error).__name__}, not the typed error",
                False,
            )
        else:
            check("FTF-0907-NEG-4", f"{label}: must not allow", False)


def test_boundary_length() -> None:
    check(
        "FTF-0907-NEG-4",
        "200-char judge model id accepted",
        _code(lambda: _route(judge_model_id="m" * 200)) == "allowed",
    )


def test_net0_no_socket() -> None:
    original = socket.socket

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("socket opened")

    socket.socket = _boom  # type: ignore[misc,assignment]
    try:
        p = _profiles("zero_egress")
        admission = admit_judge_route(_route(), p)
        check_judge_request(admission, _route(), p)
        _code(lambda: admit_judge_route(_route(upstream=EXTERNAL), p))
    except AssertionError:
        check("FTF-0907-NET-0", "a decision opened a socket", False)
    finally:
        socket.socket = original  # type: ignore[misc]


_PROBE = r"""
import json, sys
sys.path.insert(0, {root!r}); sys.path.insert(0, {api!r})
from packages.security.external_judge_denial import *
from packages.security.security_profile_enforcement import Destination, RouteAuthorization
import dataclasses
I, E = Destination("internal"), Destination("external", "acme")
def code(fn):
    try:
        fn()
    except ExternalJudgeError as e:
        return e.code
    return "allowed"
def route(**o):
    f = dict(judge_model_id="j", upstream=I, fallbacks=(FallbackAlias("a", I),), fallbacks_enumerated=True)
    f.update(o)
    return JudgeRoute(**f)
P = ProfilesInForce("zero_egress", "zero_egress")
S = ProfilesInForce("standard", "standard")
auth = RouteAuthorization("acme", frozenset({{"judge"}}), "x")
adm = admit_judge_route(route(), P)
sadm = admit_judge_route(route(upstream=E), S, auth)
sent = []
out = {{
    "external": code(lambda: admit_judge_route(route(upstream=E), P)),
    "unknown": code(lambda: admit_judge_route(route(upstream=None), P)),
    "unknown_std": code(lambda: admit_judge_route(route(upstream=None), S, auth)),
    "std_noauth": code(lambda: admit_judge_route(route(upstream=E), S)),
    "std_auth": code(lambda: admit_judge_route(route(upstream=E), S, auth)),
    "managed_auth_private": code(lambda: admit_judge_route(route(upstream=Destination("managed_provider", "acme")), P, RouteAuthorization("acme", frozenset({{"judge"}}), "x", "a", "eu", "t"))),
    "fb_external": code(lambda: admit_judge_route(route(fallbacks=(FallbackAlias("a", E),)), P)),
    "fb_unknown": code(lambda: admit_judge_route(route(fallbacks=(FallbackAlias("a", None),)), P)),
    "fb_unenum": code(lambda: admit_judge_route(route(fallbacks_enumerated=False), P)),
    "fb_second": code(lambda: admit_judge_route(route(fallbacks=(FallbackAlias("a", I), FallbackAlias("b", E))), P)),
    "req_ok": code(lambda: check_judge_request(adm, route(), P)),
    "req_none": code(lambda: check_judge_request(None, route(), P)),
    "req_replaced": code(lambda: check_judge_request(dataclasses.replace(adm), route(), P)),
    "req_changed": code(lambda: check_judge_request(adm, route(upstream=E), P)),
    "req_fb_added": code(lambda: check_judge_request(adm, route(fallbacks=(FallbackAlias("a", I), FallbackAlias("b", E))), P)),
    "req_tightened": code(lambda: check_judge_request(sadm, route(upstream=E), P)),
    "guard_denied": code(lambda: guarded_judge_call(None, route(), P, lambda r: sent.append(1))),
    "weak_project": code(lambda: admit_judge_route(route(upstream=E), ProfilesInForce("zero_egress", "standard"), auth)),
    "weak_job": code(lambda: admit_judge_route(route(upstream=E), ProfilesInForce("standard", "standard", "air_gapped"), auth)),
    "sent": len(sent),
    "reason": None,
}}
try:
    admit_judge_route(route(upstream=E), P)
except ExternalJudgeDeniedError as e:
    out["reason"] = [e.reason.profile, e.reason.surface, e.reason.code, "acme" in repr(e.reason)]
print(json.dumps(out))
"""


def _run_probe(source: str | None) -> dict[str, Any]:
    original = MODULE.read_bytes()
    cache_dir = tempfile.mkdtemp(prefix="ftf0907-pyc-")
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


# (label, regex, replacement, probe keys that must change)
_MUTATIONS: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "unknown-upstream-allowed",
        r"    if destination is None:\n        raise _deny\(\"external_judge_upstream_unknown\", profile\)\n",
        "    if destination is None:\n        return\n",
        ("unknown", "unknown_std", "fb_unknown"),
    ),
    (
        "skip-enforce-effect",
        r"        enforce_effect\(\n",
        "        (lambda *a: None)(\n",
        ("std_noauth", "external", "managed_auth_private", "req_tightened"),
    ),
    (
        "skip-fallback-loop",
        r"    for fallback in route\.fallbacks:\n",
        "    for fallback in ():\n",
        ("fb_external", "fb_unknown", "fb_second"),
    ),
    (
        "unenumerated-fallbacks-allowed",
        r"    if not route\.fallbacks_enumerated:\n        raise _deny\(\"external_judge_fallback_denied\", profile\)\n",
        "",
        ("fb_unenum",),
    ),
    (
        "skip-request-evaluate",
        r"    _evaluate\(route, profiles, authorization\)\n    return admission\.route",
        "    return admission.route",
        ("req_tightened",),
    ),
    (
        "admission-issuer-not-checked",
        r" or admission\._issued is not _ISSUER",
        "",
        ("req_replaced",),
    ),
    (
        "route-change-allowed",
        r"    if route != admission\.route:\n        raise _deny\(\"external_judge_route_changed\", profile\)\n",
        "",
        ("req_changed", "req_fb_added"),
    ),
    (
        "guard-skips-check",
        r"    return send\(check_judge_request\(admission, route, profiles, authorization\)\)",
        "    return send(admission.route)",
        ("guard_denied", "sent"),
    ),
    (
        "audit-reason-leaks-provider",
        r"    return ExternalJudgeDeniedError\(code, AuditReason\(profile, JUDGE_SURFACE, code\)\)",
        "    return ExternalJudgeDeniedError(\n        code, AuditReason(profile + 'acme', JUDGE_SURFACE, code)\n    )",
        ("reason",),
    ),
    (
        "profile-lowered-to-project",
        r"return effective_profile\(\n            profiles\.organization_profile, profiles\.project_profile, profiles\.job_profile",
        "return effective_profile(\n            profiles.project_profile, profiles.project_profile, None",
        ("weak_project", "weak_job"),
    ),
]


def test_fault_injection() -> None:
    baseline = _run_probe(None)
    check(
        "FTF-0907-FAULT",
        f"baseline probe runs ({baseline.get('ERROR', '')})",
        "ERROR" not in baseline,
    )
    expected = {
        "external": "external_judge_upstream_external",
        "unknown": "external_judge_upstream_unknown",
        "unknown_std": "external_judge_upstream_unknown",
        "std_noauth": "external_judge_upstream_external",
        "std_auth": "allowed",
        "managed_auth_private": "external_judge_upstream_external",
        "fb_external": "external_judge_fallback_denied",
        "fb_unknown": "external_judge_fallback_denied",
        "fb_unenum": "external_judge_fallback_denied",
        "fb_second": "external_judge_fallback_denied",
        "req_ok": "allowed",
        "req_none": "external_judge_admission_missing",
        "req_replaced": "external_judge_admission_missing",
        "req_changed": "external_judge_route_changed",
        "req_fb_added": "external_judge_route_changed",
        "req_tightened": "external_judge_upstream_external",
        "guard_denied": "external_judge_admission_missing",
        "weak_project": "external_judge_upstream_external",
        "weak_job": "external_judge_upstream_external",
        "sent": 0,
        "reason": ["zero_egress", "judge", "external_judge_upstream_external", False],
    }
    for key, want in expected.items():
        check(
            "FTF-0907-FAULT",
            f"baseline {key} == {want!r} (got {baseline.get(key)!r})",
            baseline.get(key) == want,
        )
    text = MODULE.read_text(encoding="utf-8")
    for label, pattern, replacement, keys in _MUTATIONS:
        mutated, count = re.subn(pattern, replacement.replace("\\", "\\\\"), text, count=1)
        check("FTF-0907-FAULT", f"{label}: mutation pattern matches once", count == 1)
        if count != 1:
            continue
        result = _run_probe(mutated)
        changed = [k for k in keys if result.get(k) != baseline.get(k)]
        check(
            "FTF-0907-FAULT",
            f"{label}: probe changes (expected one of {keys}; got {result})",
            bool(changed),
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0907 External judge denial tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0907 External judge denial tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
