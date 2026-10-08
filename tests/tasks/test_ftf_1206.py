#!/usr/bin/env python3
"""FTF-1206 -- Organization policy control (no-DB unit/fault tests).

Runnable without Postgres or network egress -- this task's own
`database_changes` clause ("No independent schema change... any required
change is a separately claimed migration prerequisite") and empty
`shared_lock_required` mean it adds no live route, table or column; there is
no real-database or real-gateway surface to integration-test, the same
disclosed-gap posture `test_ftf_0402.py`'s own module docstring already
established for FTF-0103/FTF-0104/FTF-0105/FTF-H003. This suite proves,
without any database or network call:

1. `OrganizationGatewayPolicy`/`ProjectGatewayPermissionRequest` validate
   every namespace/approver identity at construction -- an unsafe or empty
   value is refused, never silently accepted.
2. `enforce_project_gateway_permission_request`: this task's own
   negative_acceptance ("Project-level permission cannot widen gateway
   management capability or data boundary") proven at the pure-function
   level for all three dimensions (registration namespaces, deployment
   namespaces, approvers), individually and combined.
3. A request that is an exact subset (including the empty request, and the
   full organization policy itself) is granted unchanged.
4. `enforce_route_change_authority`: an actor outside the organization's own
   approvers is denied; a bypass attempt is denied; an organization that
   does not require approval authorizes any actor.

Run directly: python3 tests/tasks/test_ftf_1206.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.organization_policy_control import (  # noqa: E402
    GatewayPolicyError,
    InvalidNamespaceError,
    OrganizationGatewayPolicy,
    ProjectGatewayGrant,
    ProjectGatewayPermissionRequest,
    ProjectPermissionWideningError,
    RouteChangeNotAuthorizedError,
    enforce_project_gateway_permission_request,
    enforce_route_change_authority,
    validate_approver_identity,
    validate_namespace,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except GatewayPolicyError:
        return True


def _accepts(fn, *args: object, **kwargs: object):
    return fn(*args, **kwargs)


def test_validate_namespace_and_approver_identity() -> None:
    check(
        "FTF-1206",
        "a safe alphanumeric namespace is accepted",
        validate_namespace("factory-candidates") == "factory-candidates",
    )
    check(
        "FTF-1206",
        "a namespace with dots/colons is accepted (matches ActorInfo-style identifiers)",
        validate_namespace("factory.candidates") == "factory.candidates",
    )
    check(
        "FTF-1206",
        "an empty namespace is refused",
        _rejects(validate_namespace, ""),
    )
    check(
        "FTF-1206",
        "a non-string namespace is refused",
        _rejects(validate_namespace, None),
    )
    check(
        "FTF-1206",
        "a namespace with an embedded space is refused",
        _rejects(validate_namespace, "not a namespace"),
    )
    check(
        "FTF-1206",
        "a leading-separator namespace is refused",
        _rejects(validate_namespace, "-leading"),
    )
    check(
        "FTF-1206",
        "a safe approver identity is accepted",
        validate_approver_identity("user:jane") == "user:jane",
    )
    check(
        "FTF-1206",
        "an empty approver identity is refused",
        _rejects(validate_approver_identity, ""),
    )


def test_organization_gateway_policy_validates_every_member() -> None:
    policy = OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset({"factory-candidates"}),
        allowed_deployment_namespaces=frozenset({"factory-production"}),
        approvers=frozenset({"user:jane", "user:ahmed"}),
    )
    check(
        "FTF-1206",
        "a well-formed OrganizationGatewayPolicy round-trips its own fields",
        policy.allowed_registration_namespaces == frozenset({"factory-candidates"})
        and policy.approvers == frozenset({"user:jane", "user:ahmed"}),
    )
    check(
        "FTF-1206",
        "route_change_requires_approval defaults to True (fail-closed)",
        policy.route_change_requires_approval is True,
    )
    check(
        "FTF-1206",
        "an unsafe namespace anywhere in the policy is refused at construction, "
        "not merely when later consumed",
        _rejects(
            OrganizationGatewayPolicy,
            allowed_registration_namespaces=frozenset({"bad namespace"}),
            allowed_deployment_namespaces=frozenset(),
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "an unsafe approver identity is refused at construction",
        _rejects(
            OrganizationGatewayPolicy,
            allowed_registration_namespaces=frozenset(),
            allowed_deployment_namespaces=frozenset(),
            approvers=frozenset({""}),
        ),
    )


def _policy() -> OrganizationGatewayPolicy:
    return OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset({"factory-candidates", "factory-staging"}),
        allowed_deployment_namespaces=frozenset({"factory-production"}),
        approvers=frozenset({"user:jane", "user:ahmed"}),
    )


def test_enforce_project_gateway_permission_request_blocks_widening() -> None:
    org_policy = _policy()

    # negative_acceptance, made real at the pure-function level: a project
    # requesting a registration namespace its organization never authorized
    # is refused outright, not granted a partial/narrowed subset.
    check(
        "FTF-1206",
        "negative_acceptance: an unauthorized registration namespace is refused",
        _rejects(
            enforce_project_gateway_permission_request,
            org_policy,
            ProjectGatewayPermissionRequest(
                registration_namespaces=frozenset({"factory-candidates", "unowned-namespace"}),
                deployment_namespaces=frozenset(),
                approvers=frozenset(),
            ),
        ),
    )
    check(
        "FTF-1206",
        "negative_acceptance: an unauthorized deployment namespace is refused",
        _rejects(
            enforce_project_gateway_permission_request,
            org_policy,
            ProjectGatewayPermissionRequest(
                registration_namespaces=frozenset(),
                deployment_namespaces=frozenset({"someone-elses-production"}),
                approvers=frozenset(),
            ),
        ),
    )
    check(
        "FTF-1206",
        "negative_acceptance: an unauthorized approver identity is refused "
        "(a project cannot widen the data boundary of who can act for it)",
        _rejects(
            enforce_project_gateway_permission_request,
            org_policy,
            ProjectGatewayPermissionRequest(
                registration_namespaces=frozenset(),
                deployment_namespaces=frozenset(),
                approvers=frozenset({"user:outsider"}),
            ),
        ),
    )

    # An exact-subset request -- including the empty request, and the org's
    # own full policy -- is granted unchanged.
    empty_grant = _accepts(
        enforce_project_gateway_permission_request,
        org_policy,
        ProjectGatewayPermissionRequest(
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset(),
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "the empty request is always a valid subset and is granted",
        empty_grant.registration_namespaces == frozenset()
        and empty_grant.deployment_namespaces == frozenset()
        and empty_grant.approvers == frozenset(),
    )

    full_grant = _accepts(
        enforce_project_gateway_permission_request,
        org_policy,
        ProjectGatewayPermissionRequest(
            registration_namespaces=org_policy.allowed_registration_namespaces,
            deployment_namespaces=org_policy.allowed_deployment_namespaces,
            approvers=org_policy.approvers,
        ),
    )
    check(
        "FTF-1206",
        "requesting exactly the organization's own full policy is granted unchanged",
        full_grant.registration_namespaces == org_policy.allowed_registration_namespaces
        and full_grant.deployment_namespaces == org_policy.allowed_deployment_namespaces
        and full_grant.approvers == org_policy.approvers,
    )

    partial_grant = _accepts(
        enforce_project_gateway_permission_request,
        org_policy,
        ProjectGatewayPermissionRequest(
            registration_namespaces=frozenset({"factory-candidates"}),
            deployment_namespaces=frozenset(),
            approvers=frozenset({"user:jane"}),
        ),
    )
    check(
        "FTF-1206",
        "a genuine subset request is granted exactly as requested, never widened "
        "to the organization's own full policy",
        partial_grant.registration_namespaces == frozenset({"factory-candidates"})
        and partial_grant.deployment_namespaces == frozenset()
        and partial_grant.approvers == frozenset({"user:jane"}),
    )


def test_enforce_route_change_authority() -> None:
    org_policy = _policy()
    grant = enforce_project_gateway_permission_request(
        org_policy,
        ProjectGatewayPermissionRequest(
            registration_namespaces=frozenset({"factory-candidates"}),
            deployment_namespaces=frozenset({"factory-production"}),
            approvers=frozenset({"user:jane"}),
        ),
    )

    check(
        "FTF-1206",
        "a recognized organization approver is authorized",
        _accepts(
            enforce_route_change_authority,
            org_policy,
            grant,
            actor="user:jane",
            target_deployment_namespace="factory-production",
        )
        is None,
    )
    check(
        "FTF-1206",
        "an actor outside the organization's own approvers is denied, even if "
        "the actor string is otherwise well-formed",
        _rejects(
            enforce_route_change_authority,
            org_policy,
            grant,
            actor="user:outsider",
            target_deployment_namespace="factory-production",
        ),
    )
    check(
        "FTF-1206",
        "a bypass-approval request is denied when the organization requires approval",
        _rejects(
            enforce_route_change_authority,
            org_policy,
            grant,
            actor="user:jane",
            bypass_requested=True,
            target_deployment_namespace="factory-production",
        ),
    )

    no_approval_policy = OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset({"factory-candidates"}),
        allowed_deployment_namespaces=frozenset({"factory-production"}),
        approvers=frozenset(),
        route_change_requires_approval=False,
    )
    check(
        "FTF-1206",
        "when an organization's own policy does not require approval, any "
        "actor is authorized (no approvers need be enumerated)",
        _accepts(
            enforce_route_change_authority,
            no_approval_policy,
            grant,
            actor="user:anyone",
            target_deployment_namespace="factory-production",
        )
        is None,
    )


def test_enforce_route_change_authority_checks_target_namespace_against_grant() -> None:
    # Section 38.5/38.6 + this task's own negative_acceptance: a route change
    # is a change *into a specific deployment namespace*. A recognized
    # organization approver authorizing a route change does not, on its own,
    # authorize rolling that route into a namespace this specific project was
    # never actually granted -- `grant` must be consulted, not merely
    # accepted as a type-presence proof and then discarded.
    org_policy = _policy()
    grant = enforce_project_gateway_permission_request(
        org_policy,
        ProjectGatewayPermissionRequest(
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset({"factory-production"}),
            approvers=frozenset({"user:jane"}),
        ),
    )

    check(
        "FTF-1206",
        "a route change into the project's own granted deployment namespace, "
        "by a recognized approver, is authorized",
        _accepts(
            enforce_route_change_authority,
            org_policy,
            grant,
            actor="user:jane",
            target_deployment_namespace="factory-production",
        )
        is None,
    )
    check(
        "FTF-1206",
        "negative_acceptance: a recognized organization approver cannot route "
        "a change into a namespace this project's own grant never included, "
        "even though the namespace belongs to the same organization's wider "
        "policy",
        _rejects(
            enforce_route_change_authority,
            org_policy,
            grant,
            actor="user:jane",
            target_deployment_namespace="factory-staging-production-not-granted",
        ),
    )
    check(
        "FTF-1206",
        "an organization-wide namespace the project never requested or was "
        "granted is refused even when approval is not required at all",
        _rejects(
            enforce_route_change_authority,
            OrganizationGatewayPolicy(
                allowed_registration_namespaces=frozenset({"factory-candidates"}),
                allowed_deployment_namespaces=frozenset({"factory-production", "factory-eu"}),
                approvers=frozenset(),
                route_change_requires_approval=False,
            ),
            grant,
            actor="user:anyone",
            target_deployment_namespace="factory-eu",
        ),
    )


def test_project_gateway_permission_request_validates_every_member() -> None:
    # Round-3 return, finding 1 (mutation survivor): `ProjectGatewayPermissionRequest
    # .__post_init__` had no test of its own proving construction-time validation --
    # an `insert return None first` mutant on that method survived (nothing exercised
    # the difference between "validated" and "never touched"). Symmetric to
    # `test_organization_gateway_policy_validates_every_member` above.
    check(
        "FTF-1206",
        "an unsafe registration namespace in a project's own request is refused "
        "at construction",
        _rejects(
            ProjectGatewayPermissionRequest,
            registration_namespaces=frozenset({"bad namespace"}),
            deployment_namespaces=frozenset(),
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "an unsafe deployment namespace in a project's own request is refused "
        "at construction",
        _rejects(
            ProjectGatewayPermissionRequest,
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset({""}),
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "an unsafe approver identity in a project's own request is refused "
        "at construction",
        _rejects(
            ProjectGatewayPermissionRequest,
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset(),
            approvers=frozenset({"not an approver"}),
        ),
    )
    request = ProjectGatewayPermissionRequest(
        registration_namespaces=frozenset({"factory-candidates"}),
        deployment_namespaces=frozenset(),
        approvers=frozenset(),
    )
    check(
        "FTF-1206",
        "a well-formed request round-trips its own fields unchanged",
        request.registration_namespaces == frozenset({"factory-candidates"}),
    )


def test_route_change_authority_revalidates_inputs_defensively() -> None:
    # Round-3 return, finding 1 (mutation survivors): `validate_namespace`
    # being dropped (replaced by `pass`) on `target_deployment_namespace`,
    # and a `.strip().lower()` normalization bypass on `target_deployment_
    # namespace`/`actor`, both survived the existing suite because every
    # existing test used identifiers that were already both well-formed and
    # exact-case. These tests force a malformed/case-different value to
    # reach the grant/policy objects directly (bypassing the dataclasses'
    # own construction-time validation via `object.__setattr__`, or by
    # building a `ProjectGatewayGrant` directly rather than through
    # `enforce_project_gateway_permission_request`), so only
    # `enforce_route_change_authority`'s own re-validation can catch them.
    org_policy = _policy()

    # (a) a malformed target namespace, already present (unvalidated) in
    # both the grant and the organization's own policy sets, must still be
    # refused by `validate_namespace` itself -- not by either membership
    # check, since both would otherwise (incorrectly) treat it as granted.
    tampered_policy = _policy()
    object.__setattr__(
        tampered_policy, "allowed_deployment_namespaces", frozenset({"not a namespace"})
    )
    tampered_grant = ProjectGatewayGrant(
        registration_namespaces=frozenset(),
        deployment_namespaces=frozenset({"not a namespace"}),
        approvers=frozenset(),
    )
    check(
        "FTF-1206",
        "a malformed target namespace is refused by validate_namespace itself, "
        "even when the exact same malformed string is already present in both "
        "the grant and the organization's own (tampered) policy sets",
        _rejects(
            enforce_route_change_authority,
            tampered_policy,
            tampered_grant,
            actor="user:jane",
            target_deployment_namespace="not a namespace",
        ),
    )

    # (b) a malformed actor, already present (unvalidated) in the
    # organization's own approvers set, must still be refused by
    # `validate_approver_identity` itself -- not by the approver-membership
    # check, which would otherwise treat it as recognized.
    tampered_approvers_policy = _policy()
    object.__setattr__(
        tampered_approvers_policy, "approvers", frozenset({"not an approver"})
    )
    plain_grant = enforce_project_gateway_permission_request(
        _policy(),
        ProjectGatewayPermissionRequest(
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset({"factory-production"}),
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "a malformed actor is refused by validate_approver_identity itself, "
        "even when the exact same malformed string is already present in the "
        "organization's own (tampered) approvers set",
        _rejects(
            enforce_route_change_authority,
            tampered_approvers_policy,
            plain_grant,
            actor="not an approver",
            target_deployment_namespace="factory-production",
        ),
    )

    # (c) case-different target: the organization's CURRENT policy holds the
    # exact-case target (so that check alone would pass), but the project's
    # own grant holds a different-case string -- the grant check must still
    # refuse an exact-case mismatch rather than normalize it away.
    case_mismatch_grant = ProjectGatewayGrant(
        registration_namespaces=frozenset(),
        deployment_namespaces=frozenset({"factory-production"}),
        approvers=frozenset(),
    )
    case_mismatch_policy = OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset(),
        allowed_deployment_namespaces=frozenset({"FACTORY-PRODUCTION"}),
        approvers=frozenset({"user:jane"}),
    )
    check(
        "FTF-1206",
        "a target namespace that differs only in case from the project's own "
        "granted namespace is refused -- grant membership is exact-string, "
        "never case-insensitive -- even though the organization's current "
        "policy happens to allow the exact-case target",
        _rejects(
            enforce_route_change_authority,
            case_mismatch_policy,
            case_mismatch_grant,
            actor="user:jane",
            target_deployment_namespace="FACTORY-PRODUCTION",
        ),
    )

    # (d) case-different target: the project's own grant holds the exact-case
    # target (so the grant check alone would pass), but the organization's
    # CURRENT policy holds a different-case string -- the policy re-check
    # (finding 3) must still refuse an exact-case mismatch rather than
    # normalize it away.
    case_mismatch_grant_2 = ProjectGatewayGrant(
        registration_namespaces=frozenset(),
        deployment_namespaces=frozenset({"FACTORY-PRODUCTION"}),
        approvers=frozenset(),
    )
    case_mismatch_policy_2 = OrganizationGatewayPolicy(
        allowed_registration_namespaces=frozenset(),
        allowed_deployment_namespaces=frozenset({"factory-production"}),
        approvers=frozenset({"user:jane"}),
    )
    check(
        "FTF-1206",
        "a target namespace that differs only in case from the organization's "
        "own current policy is refused -- the policy re-check is exact-string, "
        "never case-insensitive -- even though the project's own grant happens "
        "to hold the exact-case target",
        _rejects(
            enforce_route_change_authority,
            case_mismatch_policy_2,
            case_mismatch_grant_2,
            actor="user:jane",
            target_deployment_namespace="FACTORY-PRODUCTION",
        ),
    )

    # (e) case-different actor: an approver identity that differs only in
    # case from the organization's own recognized approver is refused, never
    # normalized into a match.
    check(
        "FTF-1206",
        "an actor that differs only in case from a recognized organization "
        "approver is refused -- approver membership is exact-string, never "
        "case-insensitive",
        _rejects(
            enforce_route_change_authority,
            org_policy,
            enforce_project_gateway_permission_request(
                org_policy,
                ProjectGatewayPermissionRequest(
                    registration_namespaces=frozenset(),
                    deployment_namespaces=frozenset({"factory-production"}),
                    approvers=frozenset(),
                ),
            ),
            actor="USER:JANE",
            target_deployment_namespace="factory-production",
        ),
    )


def test_route_change_authority_rechecks_current_organization_policy() -> None:
    # Round-3 return, finding 3 (negative_acceptance): a grant issued before
    # the organization narrowed its own policy must never still authorize a
    # route change into a namespace the organization no longer allows at
    # all -- the real-world scenario (no case tricks), distinct from the
    # case-mismatch tests above.
    original_policy = _policy()
    grant = enforce_project_gateway_permission_request(
        original_policy,
        ProjectGatewayPermissionRequest(
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset({"factory-production"}),
            approvers=frozenset({"user:jane"}),
        ),
    )
    narrowed_policy = OrganizationGatewayPolicy(
        allowed_registration_namespaces=original_policy.allowed_registration_namespaces,
        allowed_deployment_namespaces=frozenset({"factory-eu"}),
        approvers=original_policy.approvers,
    )
    check(
        "FTF-1206",
        "negative_acceptance: narrowing the organization's own policy after a "
        "grant was issued refuses a route change into the now-disallowed "
        "namespace, even though the project's own (stale) grant still "
        "includes it",
        _rejects(
            enforce_route_change_authority,
            narrowed_policy,
            grant,
            actor="user:jane",
            target_deployment_namespace="factory-production",
        ),
    )


def test_malformed_inputs_fail_closed_with_typed_errors() -> None:
    # Round-3 return, finding 2: malformed inputs must raise this module's
    # own typed error, never a bare TypeError, and never be silently
    # coerced (a bare str becoming per-character membership; a falsy-but-
    # not-bool flag silently disabling approval).
    check(
        "FTF-1206",
        "a bare str passed where a set of registration namespaces is "
        "expected is refused outright, never iterated into per-character "
        "membership ('jane' must never become {j,a,n,e})",
        _rejects(
            OrganizationGatewayPolicy,
            allowed_registration_namespaces="jane",
            allowed_deployment_namespaces=frozenset(),
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "None in place of a namespace set raises this module's own typed "
        "error, never a bare TypeError",
        _rejects(
            OrganizationGatewayPolicy,
            allowed_registration_namespaces=None,
            allowed_deployment_namespaces=frozenset(),
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "an int in place of an approver set raises this module's own typed "
        "error, never a bare TypeError",
        _rejects(
            OrganizationGatewayPolicy,
            allowed_registration_namespaces=frozenset(),
            allowed_deployment_namespaces=frozenset(),
            approvers=7,
        ),
    )
    check(
        "FTF-1206",
        "a bare str passed where a project's own requested namespace set is "
        "expected is refused outright (same class, ProjectGatewayPermissionRequest)",
        _rejects(
            ProjectGatewayPermissionRequest,
            registration_namespaces=frozenset(),
            deployment_namespaces="production",
            approvers=frozenset(),
        ),
    )
    check(
        "FTF-1206",
        "None in place of a project's own requested approver set raises this "
        "module's own typed error",
        _rejects(
            ProjectGatewayPermissionRequest,
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset(),
            approvers=None,
        ),
    )
    check(
        "FTF-1206",
        "a non-bool route_change_requires_approval is refused outright -- "
        "None is falsy and must never silently switch approval off",
        _rejects(
            OrganizationGatewayPolicy,
            allowed_registration_namespaces=frozenset(),
            allowed_deployment_namespaces=frozenset(),
            approvers=frozenset(),
            route_change_requires_approval=None,
        ),
    )
    check(
        "FTF-1206",
        "an int route_change_requires_approval (e.g. 0, falsy like False) is "
        "refused outright, never silently treated as a bool",
        _rejects(
            OrganizationGatewayPolicy,
            allowed_registration_namespaces=frozenset(),
            allowed_deployment_namespaces=frozenset(),
            approvers=frozenset(),
            route_change_requires_approval=0,
        ),
    )

    org_policy = _policy()
    grant = enforce_project_gateway_permission_request(
        org_policy,
        ProjectGatewayPermissionRequest(
            registration_namespaces=frozenset(),
            deployment_namespaces=frozenset({"factory-production"}),
            approvers=frozenset({"user:jane"}),
        ),
    )
    check(
        "FTF-1206",
        "an unhashable actor (a list) raises this module's own typed error, "
        "never a bare TypeError from frozenset membership",
        _rejects(
            enforce_route_change_authority,
            org_policy,
            grant,
            actor=[],
            target_deployment_namespace="factory-production",
        ),
    )
    check(
        "FTF-1206",
        "an unhashable actor (a dict) raises this module's own typed error, "
        "never a bare TypeError from frozenset membership",
        _rejects(
            enforce_route_change_authority,
            org_policy,
            grant,
            actor={},
            target_deployment_namespace="factory-production",
        ),
    )


def test_error_types_are_distinct_and_specific() -> None:
    check(
        "FTF-1206",
        "InvalidNamespaceError is a GatewayPolicyError",
        issubclass(InvalidNamespaceError, GatewayPolicyError),
    )
    check(
        "FTF-1206",
        "ProjectPermissionWideningError is a GatewayPolicyError",
        issubclass(ProjectPermissionWideningError, GatewayPolicyError),
    )
    check(
        "FTF-1206",
        "RouteChangeNotAuthorizedError is a GatewayPolicyError",
        issubclass(RouteChangeNotAuthorizedError, GatewayPolicyError),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1206 Organization policy control tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1206 Organization policy control tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
