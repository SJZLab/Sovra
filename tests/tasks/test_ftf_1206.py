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
        _accepts(enforce_route_change_authority, org_policy, grant, actor="user:jane") is None,
    )
    check(
        "FTF-1206",
        "an actor outside the organization's own approvers is denied, even if "
        "the actor string is otherwise well-formed",
        _rejects(enforce_route_change_authority, org_policy, grant, actor="user:outsider"),
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
        )
        is None,
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
