#!/usr/bin/env python3
"""FTF-0901 -- Security profile enforcement (no-DB unit/fault tests).

Proves, without any database or network:

1. ACC-1 (policy evaluated at each effect): the full profile x surface x
   destination-kind matrix, against the real default code.
2. NEG-1 (private job cannot switch to a managed route by bypassing the UI
   or applying a weaker project setting): a managed/external route is
   refused when the organization, the project or the job's frozen profile is
   private, whatever the other two say, with or without a authorization.
3. Standard-profile routes need an explicit, surface- and provider-scoped
   authorization; managed providers also need account/region/retention.
4. Fail-closed input handling (unknown names, unhashable values, wrong
   types) raises the module's own coded errors.
5. Fault injection (Law 1): regex mutations of the real module, run in a
   fresh subprocess, each must change behaviour; the file is restored
   byte-identical.

Run directly: python3 tests/tasks/test_ftf_0901.py
"""

from __future__ import annotations

import itertools
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.security_profile_enforcement import (  # noqa: E402
    DESTINATION_KINDS,
    SURFACES,
    Destination,
    EffectRequest,
    InvalidEffectRequestError,
    RouteAuthorization,
    RouteBlockedByProfileError,
    RouteNotAuthorizedError,
    effective_profile,
    enforce_effect,
)

MODULE = ROOT / "packages" / "security" / "security_profile_enforcement.py"
PROFILES = ("standard", "zero_egress", "air_gapped")
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _dest(kind: str) -> Destination:
    return Destination(kind) if kind == "internal" else Destination(kind, "acme")


def _auth(kind: str, surface: str) -> RouteAuthorization:
    return RouteAuthorization(
        "acme",
        frozenset({surface}),
        "approver",
        account="acct" if kind == "managed_provider" else None,
        region="eu" if kind == "managed_provider" else None,
        retention_terms="30d" if kind == "managed_provider" else None,
    )


def _outcome(fn, *args: object, **kwargs: object) -> str:
    try:
        fn(*args, **kwargs)
        return "allowed"
    except RouteBlockedByProfileError:
        return "blocked"
    except RouteNotAuthorizedError:
        return "not_authorized"
    except InvalidEffectRequestError:
        return "invalid"


def _request(
    surface: str, kind: str, org: str, project: str, job: str | None = None
) -> EffectRequest:
    return EffectRequest(surface, _dest(kind), org, project, job)


def test_matrix_every_profile_surface_and_destination() -> None:
    for profile, surface, kind in itertools.product(PROFILES, SURFACES, DESTINATION_KINDS):
        request = _request(surface, kind, profile, profile)
        if kind == "internal":
            expected = "allowed"
        elif profile == "standard":
            expected = "not_authorized"
        else:
            expected = "blocked"
        check(
            "FTF-0901-ACC1",
            f"{profile}/{surface}/{kind} without authorization -> {expected}",
            _outcome(enforce_effect, request) == expected,
        )
        if kind != "internal":
            expected_with_auth = "allowed" if profile == "standard" else "blocked"
            check(
                "FTF-0901-ACC1",
                f"{profile}/{surface}/{kind} with matching authorization -> {expected_with_auth}",
                _outcome(enforce_effect, request, _auth(kind, surface)) == expected_with_auth,
            )


def test_private_job_cannot_reach_managed_route_by_any_weaker_setting() -> None:
    # NEG-1: whichever of org / project / job is private, a managed route is
    # refused even with a valid authorization for it.
    for private in ("zero_egress", "air_gapped"):
        for org, project, job in (
            (private, "standard", "standard"),
            ("standard", private, "standard"),
            ("standard", "standard", private),
            ("standard", "standard", None),  # control: standard allows with auth
        ):
            request = _request("model", "managed_provider", org, project, job)
            outcome = _outcome(enforce_effect, request, _auth("managed_provider", "model"))
            expected = "allowed" if private not in (org, project, job) else "blocked"
            check(
                "FTF-0901-NEG1",
                f"managed route org={org} project={project} job={job} -> {expected}",
                outcome == expected,
            )
    # The frozen job profile outlives a later project relaxation.
    request = _request("judge", "managed_provider", "standard", "standard", "zero_egress")
    check(
        "FTF-0901-NEG1",
        "a job started under zero_egress stays blocked after the project is set to standard",
        _outcome(enforce_effect, request, _auth("managed_provider", "judge")) == "blocked",
    )
    check(
        "FTF-0901-NEG1",
        "the same managed route for a standard job without a job profile is allowed",
        _outcome(
            enforce_effect,
            _request("judge", "managed_provider", "standard", "standard"),
            _auth("managed_provider", "judge"),
        )
        == "allowed",
    )


def test_effective_profile_is_most_restrictive() -> None:
    check(
        "FTF-0901-ACC1",
        "most restrictive of org/project/job wins in every order",
        all(
            effective_profile(*perm) == "air_gapped"
            for perm in itertools.permutations(("standard", "zero_egress", "air_gapped"))
        )
        and effective_profile("standard", "standard") == "standard"
        and effective_profile("zero_egress", "standard", None) == "zero_egress",
    )


def test_standard_authorization_is_scoped_to_provider_surface_and_terms() -> None:
    request = _request("storage", "external", "standard", "standard")
    good = _auth("external", "storage")
    check(
        "FTF-0901-ACC1",
        "matching authorization allows",
        _outcome(enforce_effect, request, good) == "allowed",
    )
    other_provider = RouteAuthorization("other", frozenset({"storage"}), "approver")
    check(
        "FTF-0901-ACC1",
        "an authorization for a different provider is refused (no silent provider change)",
        _outcome(enforce_effect, request, other_provider) == "not_authorized",
    )
    other_surface = RouteAuthorization("acme", frozenset({"data"}), "approver")
    check(
        "FTF-0901-ACC1",
        "an authorization for a different surface is refused",
        _outcome(enforce_effect, request, other_surface) == "not_authorized",
    )
    managed_request = _request("storage", "managed_provider", "standard", "standard")
    for missing in ("account", "region", "retention_terms"):
        fields: dict[str, Any] = {"account": "a", "region": "r", "retention_terms": "t"}
        fields[missing] = None
        partial = RouteAuthorization("acme", frozenset({"storage"}), "approver", **fields)
        check(
            "FTF-0901-ACC1",
            f"managed provider without {missing} is refused",
            _outcome(enforce_effect, managed_request, partial) == "not_authorized",
        )
    blank = RouteAuthorization(
        "acme", frozenset({"storage"}), "approver", account=" ", region="r", retention_terms="t"
    )
    check(
        "FTF-0901-ACC1",
        "a blank managed-provider account is refused",
        _outcome(enforce_effect, managed_request, blank) == "not_authorized",
    )


def test_fail_closed_input_handling() -> None:
    bad_values: list[Any] = [[], {}, 1, b"standard", None, "", "super_secure"]
    for value in bad_values:
        check(
            "FTF-0901-ACC1",
            f"surface {value!r} raises InvalidEffectRequestError",
            _outcome(
                enforce_effect,
                EffectRequest(value, Destination("internal"), "standard", "standard"),
            )
            == "invalid",
        )
        check(
            "FTF-0901-ACC1",
            f"org profile {value!r} raises InvalidEffectRequestError",
            _outcome(
                enforce_effect, EffectRequest("data", Destination("internal"), value, "standard")
            )
            == "invalid",
        )
        check(
            "FTF-0901-ACC1",
            f"destination kind {value!r} raises InvalidEffectRequestError",
            _outcome(Destination, value, "acme") == "invalid",
        )
        if value is not None:
            check(
                "FTF-0901-ACC1",
                f"job profile {value!r} raises InvalidEffectRequestError",
                _outcome(effective_profile, "standard", "standard", value) == "invalid",
            )
    check("FTF-0901-ACC1", "non-request refused", _outcome(enforce_effect, "x") == "invalid")
    check(
        "FTF-0901-ACC1",
        "non-authorization refused",
        _outcome(enforce_effect, _request("data", "external", "standard", "standard"), "x")
        == "invalid",
    )
    check(
        "FTF-0901-ACC1",
        "internal with provider refused",
        _outcome(Destination, "internal", "acme") == "invalid",
    )
    check(
        "FTF-0901-ACC1",
        "external without provider refused",
        _outcome(Destination, "external") == "invalid",
    )
    check(
        "FTF-0901-ACC1",
        "empty or unknown authorization surfaces refused",
        _outcome(RouteAuthorization, "acme", frozenset(), "a") == "invalid"
        and _outcome(RouteAuthorization, "acme", frozenset({"nope"}), "a") == "invalid"
        and _outcome(RouteAuthorization, "acme", ["data"], "a") == "invalid",
    )


def test_provider_matching_is_exact_no_normalization() -> None:
    # Round 2 (QUEUE 0e): a real-looking case/padding/prefix variant of an
    # authorized provider must still be refused -- _covers compares with
    # plain `!=`, never casefold/strip/startswith.
    request = _request("data", "external", "standard", "standard")
    for variant in ("ACME", " acme", "acme ", "acme2", "acm", "Acme"):
        mismatched = RouteAuthorization(variant, frozenset({"data"}), "approver")
        check(
            "FTF-0901-ACC1",
            f"authorization provider {variant!r} does not cover destination provider 'acme'",
            _outcome(enforce_effect, request, mismatched) == "not_authorized",
        )
    check(
        "FTF-0901-ACC1",
        "the exact provider 'acme' does cover it",
        _outcome(enforce_effect, request, _auth("external", "data")) == "allowed",
    )


def test_approved_by_is_required() -> None:
    # Round 2 (QUEUE 0e, Section 11.2): an authorization with no real
    # approver identity is invalid, never silently accepted.
    bad_values: list[Any] = ["", " ", "\t", "  \n ", None, 1, [], {}]
    for bad in bad_values:
        check(
            "FTF-0901-ACC1",
            f"approved_by {bad!r} raises InvalidEffectRequestError",
            _outcome(RouteAuthorization, "acme", frozenset({"data"}), bad) == "invalid",
        )
    check(
        "FTF-0901-ACC1",
        "a real approver identity is accepted",
        _outcome(RouteAuthorization, "acme", frozenset({"data"}), "reviewer@org") == "allowed",
    )


def test_surfaces_set_is_pinned() -> None:
    # Optional (QUEUE 0e): pin the exact SURFACES set from the acceptance
    # clause so a sibling task cannot silently add or drop a surface.
    check(
        "FTF-0901-ACC1",
        "SURFACES is the exact 8-surface set the acceptance clause names",
        SURFACES
        == (
            "source",
            "data",
            "model",
            "judge",
            "storage",
            "browser",
            "history",
            "serving",
        ),
    )


def test_error_codes_are_stable_and_decision_is_content_free() -> None:
    codes = {}
    for profile, kind in (("zero_egress", "external"), ("standard", "external")):
        try:
            enforce_effect(_request("data", kind, profile, profile))
        except (RouteBlockedByProfileError, RouteNotAuthorizedError) as exc:
            codes[profile] = exc.code
    check(
        "FTF-0901-NEG1",
        "stable policy codes for blocked and unauthorized routes",
        codes
        == {
            "zero_egress": "security_profile_route_blocked",
            "standard": "security_profile_route_not_authorized",
        },
    )
    decision = enforce_effect(_request("serving", "internal", "zero_egress", "zero_egress"))
    check(
        "FTF-0901-ACC1",
        "decision carries only profile, surface and kind",
        (decision.allowed, decision.effective_profile, decision.surface, decision.destination_kind)
        == (True, "zero_egress", "serving", "internal")
        and set(decision.__dataclass_fields__)
        == {"allowed", "effective_profile", "surface", "destination_kind"},
    )


def test_exact_boundaries_and_non_normalized_names() -> None:
    long_ok, long_bad = "p" * 200, "p" * 201
    check(
        "FTF-0901-ACC1",
        "a 200-char provider is accepted, 201 refused",
        _outcome(Destination, "external", long_ok) == "allowed"
        and _outcome(Destination, "external", long_bad) == "invalid",
    )
    for variant in (" standard", "standard ", "STANDARD", "Zero_Egress"):
        check(
            "FTF-0901-ACC1",
            f"profile {variant!r} is not normalized into a valid name",
            _outcome(effective_profile, variant, "standard") == "invalid",
        )
        check(
            "FTF-0901-ACC1",
            f"surface {variant!r} is not normalized into a valid name",
            _outcome(
                enforce_effect,
                EffectRequest(variant, Destination("internal"), "standard", "standard"),
            )
            == "invalid",
        )
    for variant in (" internal", "Internal", "internal ", "external "):
        check(
            "FTF-0901-ACC1",
            f"destination kind {variant!r} is refused at construction",
            _outcome(Destination, variant, None if "internal" in variant.lower() else "acme")
            == "invalid",
        )
        # A Destination whose kind is mutated after construction is still not
        # treated as internal by enforce_effect.
        dest = Destination("external", "acme")
        object.__setattr__(dest, "kind", variant)
        check(
            "FTF-0901-NEG1",
            f"post-construction kind {variant!r} is never treated as internal",
            _outcome(
                enforce_effect,
                EffectRequest("data", dest, "zero_egress", "zero_egress"),
            )
            == "blocked",
        )
    check(
        "FTF-0901-ACC1",
        "a non-Destination destination is refused",
        _outcome(
            enforce_effect,
            EffectRequest("data", cast(Any, "internal"), "standard", "standard"),
        )
        == "invalid",
    )
    request = _request("data", "managed_provider", "standard", "standard")
    for blank in ("\t", "  "):
        auth = RouteAuthorization(
            "acme", frozenset({"data"}), "a", account="x", region=blank, retention_terms="t"
        )
        check(
            "FTF-0901-ACC1",
            f"managed region {blank!r} is refused",
            _outcome(enforce_effect, request, auth) == "not_authorized",
        )
    padded = RouteAuthorization(
        "acme", frozenset({"data"}), "a", account=" x ", region=" r", retention_terms="t "
    )
    check(
        "FTF-0901-ACC1",
        "a padded but non-blank managed term is accepted",
        _outcome(enforce_effect, request, padded) == "allowed",
    )


_PROBE = """
import sys
sys.path.insert(0, {root!r}); sys.path.insert(0, {api!r})
from packages.security.security_profile_enforcement import *
def run(profile, kind, job=None, auth=None):
    d = Destination(kind) if kind == "internal" else Destination(kind, "acme")
    try:
        enforce_effect(EffectRequest("model", d, "standard", profile, job), auth)
        return "allowed"
    except SecurityProfileEnforcementError as exc:
        return exc.code
RA = RouteAuthorization
a = RA("acme", frozenset({{"model"}}), "x", account="a", region="r", retention_terms="t")
print(run("zero_egress", "managed_provider", None, a))
print(run("standard", "managed_provider", "air_gapped", a))
print(run("standard", "external", None, None))
print(run("standard", "external", None, RA("other", frozenset({{"model"}}), "x")))
print(run("standard", "managed_provider", None, RA("acme", frozenset({{"model"}}), "x")))
print(run("standard", "external", None, RA("acme", frozenset({{"data"}}), "x")))
"""
BASELINE = [
    "security_profile_route_blocked",
    "security_profile_route_blocked",
    "security_profile_route_not_authorized",
    "security_profile_route_not_authorized",
    "security_profile_route_not_authorized",
    "security_profile_route_not_authorized",
]

# (description, regex, replacement, output line index that must change)
MUTATIONS = [
    (
        "boundary profiles allow external routes",
        r"if profile in _BOUNDARY_ONLY_PROFILES:",
        "if False:",
        0,
    ),
    ("job profile ignored", r"if job_profile is not None:", "if False:", 1),
    (
        "missing authorization allowed",
        r"if authorization is None or not _covers\(authorization, request\):",
        "if False:",
        2,
    ),
    (
        "provider not compared",
        r"if authorization\.provider != destination\.provider:\n        return False",
        "pass",
        3,
    ),
    (
        "managed terms not required",
        r"if destination\.kind == \"managed_provider\":",
        "if False:",
        4,
    ),
    (
        "surface not compared",
        r"if request\.surface not in authorization\.surfaces:\n        return False",
        "pass",
        5,
    ),
]


def _probe(source: str | None) -> list[str]:
    original = MODULE.read_bytes()
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", _PROBE.format(root=str(ROOT), api=str(ROOT / "apps" / "api"))],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        MODULE.write_bytes(original)
    if MODULE.read_bytes() != original:
        raise RuntimeError("module not restored")
    return result.stdout.split() if result.returncode == 0 else ["ERROR:" + result.stderr[-300:]]


def test_fault_injection_each_guard_is_load_bearing() -> None:
    check("FTF-0901-FAULT", "unmutated probe matches baseline", _probe(None) == BASELINE)
    text = MODULE.read_text(encoding="utf-8")
    for description, pattern, replacement, index in MUTATIONS:
        mutated, count = re.subn(pattern, replacement, text, count=1)
        check("FTF-0901-FAULT", f"mutation pattern matches once: {description}", count == 1)
        if count != 1:
            continue
        out = _probe(mutated)
        check(
            "FTF-0901-FAULT",
            f"mutation turns the probe RED: {description}",
            len(out) == len(BASELINE) and out[index] != BASELINE[index],
        )


_PROVIDER_PROBE = """
import sys
sys.path.insert(0, {root!r}); sys.path.insert(0, {api!r})
from packages.security.security_profile_enforcement import *
d = Destination("external", "ACME")
req = EffectRequest("data", d, "standard", "standard")
auth = RouteAuthorization("acme", frozenset({{"data"}}), "approver")
try:
    enforce_effect(req, auth)
    print("allowed")
except SecurityProfileEnforcementError as exc:
    print(exc.code)
"""

_APPROVED_BY_PROBE = """
import sys
sys.path.insert(0, {root!r}); sys.path.insert(0, {api!r})
from packages.security.security_profile_enforcement import *
try:
    RouteAuthorization("acme", frozenset({{"data"}}), "   ")
    print("allowed")
except SecurityProfileEnforcementError as exc:
    print(exc.code)
"""


def _run_probe(template: str, source: str | None) -> str:
    original = MODULE.read_bytes()
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                template.format(root=str(ROOT), api=str(ROOT / "apps" / "api")),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        MODULE.write_bytes(original)
    if MODULE.read_bytes() != original:
        raise RuntimeError("module not restored")
    return result.stdout.strip() if result.returncode == 0 else "ERROR:" + result.stderr[-300:]


def test_provider_and_approved_by_guards_are_load_bearing() -> None:
    # Round 2 (QUEUE 0e): each new behavioural guard above must be
    # load-bearing, not merely untested-but-incidentally-true.
    check(
        "FTF-0901-FAULT",
        "unmutated probe refuses a mismatched-case provider",
        _run_probe(_PROVIDER_PROBE, None) == "security_profile_route_not_authorized",
    )
    text = MODULE.read_text(encoding="utf-8")
    normalized, count = re.subn(
        r"if authorization\.provider != destination\.provider:\n        return False",
        "if authorization.provider.strip().lower() != destination.provider.strip().lower():"
        "\n        return False",
        text,
        count=1,
    )
    check("FTF-0901-FAULT", "provider-normalization mutation pattern matches once", count == 1)
    if count == 1:
        check(
            "FTF-0901-FAULT",
            "normalizing the provider comparison turns the mismatched-case probe GREEN",
            _run_probe(_PROVIDER_PROBE, normalized) == "allowed",
        )

    check(
        "FTF-0901-FAULT",
        "unmutated probe refuses a whitespace-only approved_by",
        _run_probe(_APPROVED_BY_PROBE, None) == "security_profile_invalid_request",
    )
    unguarded, count2 = re.subn(
        r'_text\(self\.approved_by, "authorization approver"\)',
        "self.approved_by",
        text,
        count=1,
    )
    check("FTF-0901-FAULT", "approved_by-guard-removal mutation pattern matches once", count2 == 1)
    if count2 == 1:
        check(
            "FTF-0901-FAULT",
            "removing the approved_by guard turns the whitespace-only probe GREEN",
            _run_probe(_APPROVED_BY_PROBE, unguarded) == "allowed",
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0901 Security profile enforcement tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0901 Security profile enforcement tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
