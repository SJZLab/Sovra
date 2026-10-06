"""Operator CLI for Membership writes (FTF-0103A, Section 6.4/65).

There is no public route that writes memberships and the app role
(`sovra_app`) has SELECT only on `memberships`: an operator grants and
revokes memberships with this CLI, connecting as the migrate role named by
`SOVRA_DATABASE_URL` (the same owner/app split every migration uses). It
never reads an organization, role or identity from anywhere but its own
arguments.

    python -m app.auth.membership_admin_cli grant \\
        --organization-id <uuid> --issuer <iss> --subject <sub> --role viewer \\
        [--workspace-id <uuid>]
    python -m app.auth.membership_admin_cli revoke \\
        --organization-id <uuid> --issuer <iss> --subject <sub> [--workspace-id <uuid>]

Exit codes: 0 done, 1 refused/nothing to do, 2 bad usage or missing config.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.membership_store import MembershipError, grant_membership, revoke_membership

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="membership_admin_cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("grant", "revoke"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--organization-id", type=uuid.UUID, required=True)
        cmd.add_argument("--issuer", required=True)
        cmd.add_argument("--subject", required=True)
        cmd.add_argument("--workspace-id", type=uuid.UUID, default=None)
        if name == "grant":
            cmd.add_argument("--role", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_url = os.environ.get("SOVRA_DATABASE_URL")
    if not database_url:
        print("SOVRA_DATABASE_URL (the migrate-role connection) is required", file=sys.stderr)
        return 2

    engine = create_engine(database_url)
    session = sessionmaker(bind=engine)()
    try:
        if args.command == "grant":
            membership_id = grant_membership(
                session,
                organization_id=args.organization_id,
                workspace_id=args.workspace_id,
                issuer=args.issuer,
                subject=args.subject,
                role=args.role,
            )
            session.commit()
            print(f"granted membership {membership_id}")
            return 0
        changed = revoke_membership(
            session,
            organization_id=args.organization_id,
            workspace_id=args.workspace_id,
            issuer=args.issuer,
            subject=args.subject,
        )
        session.commit()
        if changed == 0:
            print("no active membership matched; nothing revoked", file=sys.stderr)
            return 1
        print(f"revoked {changed} membership(s)")
        return 0
    except MembershipError as exc:
        session.rollback()
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
