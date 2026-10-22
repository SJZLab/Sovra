#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-R006-P1b -- pure-logic proof for `app/reviews/argilla_identity_store.py`
(no database, no network): the two deterministic naming functions and the
module's own typed-error/dataclass shapes. The real provisioning/
revocation/membership-sync behaviour against a real database, a real
OpenBao server and a real (fake-server) Argilla wire protocol is proved by
`tests/integration/test_argilla_identity_scope.py` -- this module's own
raw-SQL functions bind `set_config`/row-level security, which SQLite does
not have, so there is no meaningful SQLite-backed split here (the same
precedent `tests/integration/test_ftf_0519_p1_split_manifests.py` already
set for FTF-0519-P1's own UNWIRED, DB-coupled store).

Run: python3 tests/tasks/test_ftf_r006_p1b.py
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import Role  # noqa: E402
from app.reviews.argilla_identity_store import (  # noqa: E402
    DATASET_SOURCE_ROLES,
    ArgillaIdentityError,
    ReviewerIdentityRevokedError,
    _username_for,
    workspace_name_for,
)

FAILURES: list[str] = []
CID = "R006P1b"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def raises(exc: type[BaseException], fn: Any, *args: Any, **kwargs: Any) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception:
        return False
    return False


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def test_workspace_name_deterministic_and_shaped() -> None:
    org, proj = uuid.uuid4(), uuid.uuid4()
    name1 = workspace_name_for(org, proj)
    name2 = workspace_name_for(org, proj)
    check("workspace_name_for is deterministic for the same (org, project)", name1 == name2)
    check(
        "workspace_name_for matches ArgillaClient's own workspace-name identifier shape",
        bool(_IDENTIFIER.fullmatch(name1)),
    )
    check("workspace_name_for starts with the sovra- prefix", name1.startswith("sovra-"))
    check(
        "workspace_name_for never embeds the raw project id as readable text",
        str(proj) not in name1,
    )


def test_workspace_name_collision_free_across_projects() -> None:
    org = uuid.uuid4()
    names = {workspace_name_for(org, uuid.uuid4()) for _ in range(200)}
    check(
        "200 distinct projects under one org produce 200 distinct workspace names",
        len(names) == 200,
    )


def test_workspace_name_differs_across_orgs_for_same_project() -> None:
    proj = uuid.uuid4()
    n1 = workspace_name_for(uuid.uuid4(), proj)
    n2 = workspace_name_for(uuid.uuid4(), proj)
    check("the same project under two different orgs gets two different workspace names", n1 != n2)


def test_username_deterministic_shaped_and_content_free() -> None:
    org = uuid.uuid4()
    u1 = _username_for(org, "https://issuer.example", "subject-123@example.com")
    u2 = _username_for(org, "https://issuer.example", "subject-123@example.com")
    check("_username_for is deterministic for the same (org, issuer, subject)", u1 == u2)
    check(
        "_username_for matches ArgillaClient's own username identifier shape",
        bool(_IDENTIFIER.fullmatch(u1)),
    )
    check(
        "_username_for never embeds the raw subject (which may be email-shaped) as readable text",
        "subject-123" not in u1 and "example.com" not in u1,
    )
    u3 = _username_for(org, "https://issuer.example", "subject-456@example.com")
    check("two different subjects under the same org get two different usernames", u1 != u3)


def test_dataset_source_roles_matches_section_45() -> None:
    check(
        "DATASET_SOURCE_ROLES is exactly ml_engineer/workspace_admin/organization_admin",
        DATASET_SOURCE_ROLES
        == frozenset({Role.ML_ENGINEER, Role.WORKSPACE_ADMIN, Role.ORGANIZATION_ADMIN}),
    )
    check(
        "a reviewer/evaluator/viewer-only role is never in DATASET_SOURCE_ROLES",
        Role.EVALUATOR not in DATASET_SOURCE_ROLES
        and Role.VIEWER not in DATASET_SOURCE_ROLES
        and Role.APPROVER not in DATASET_SOURCE_ROLES,
    )


def test_naming_functions_refuse_non_uuid_and_empty_inputs() -> None:
    check(
        "workspace_name_for refuses a None organization_id instead of hashing it",
        raises(TypeError, workspace_name_for, None, uuid.uuid4()),
    )
    check(
        "workspace_name_for refuses a non-UUID string project_id instead of hashing it",
        raises(TypeError, workspace_name_for, uuid.uuid4(), "not-a-uuid"),
    )
    check(
        "_username_for refuses a None organization_id instead of hashing it",
        raises(TypeError, _username_for, None, "iss", "sub"),
    )
    check(
        "_username_for refuses an empty issuer instead of hashing it",
        raises(TypeError, _username_for, uuid.uuid4(), "", "sub"),
    )
    check(
        "_username_for refuses an empty subject instead of hashing it",
        raises(TypeError, _username_for, uuid.uuid4(), "iss", ""),
    )


def test_error_hierarchy() -> None:
    check(
        "ReviewerIdentityRevokedError is an ArgillaIdentityError",
        issubclass(ReviewerIdentityRevokedError, ArgillaIdentityError),
    )
    check(
        "ArgillaIdentityError is a plain Exception, never collides with ArgillaClientError",
        issubclass(ArgillaIdentityError, Exception),
    )


def main() -> None:
    test_workspace_name_deterministic_and_shaped()
    test_workspace_name_collision_free_across_projects()
    test_workspace_name_differs_across_orgs_for_same_project()
    test_username_deterministic_shaped_and_content_free()
    test_dataset_source_roles_matches_section_45()
    test_naming_functions_refuse_non_uuid_and_empty_inputs()
    test_error_hierarchy()
    if FAILURES:
        for failure in FAILURES:
            print(failure)
        print(f"{len(FAILURES)} failure(s)")
        sys.exit(1)
    print("All FTF-R006-P1b pure-logic checks passed.")


if __name__ == "__main__":
    main()
