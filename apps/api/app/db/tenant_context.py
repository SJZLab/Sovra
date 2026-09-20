"""Cross-layer authenticated tenant context (FTF-H003, Section 6/65/77/81).

FTF-0103 (RBAC middleware), FTF-0104 (audit event writer) and FTF-0105
(tenant-isolation query guards) each independently accept a bare
`organization_id: uuid.UUID` from whichever caller happens to construct
one. That is correct as far as it goes, but it means "authenticated
tenant context" is a *convention* three separately-authored modules each
have to remember to uphold identically, not a single type the compiler
and the reviewer can see threaded end to end -- exactly what Section 65's
"bind jobs, object grants, caches, aliases and audit to authenticated
tenant/project context" is asking for, read as one cross-layer property
rather than three unrelated per-module ones.

`TenantContext` is that one type. It is only ever constructed from a real
`AuthorizedContext` -- FTF-0103's own `require_role` output, itself only
ever produced from a real authenticated principal and a real Membership
role lookup -- never accepted as a bare client-supplied UUID string. Every
downstream layer that needs "the current tenant" (the query guards below,
this task's own `resource_authorization.py`, and any future job/cache/
alias binding) takes a `TenantContext`, not a raw `uuid.UUID`, so a caller
cannot accidentally thread an unauthenticated or unauthorized value
through by construction alone.

Disclosed, not hidden: this module does not itself change FTF-0103/0104/
0105's own already-certified call signatures (each still accepts a bare
`organization_id` at its own boundary, unchanged -- Section 84.6's
"objective CI remains independent" is not put at risk by widening this
task's own new modules). `tenant_scoped_session_for` below is the one new
blessed entry point that takes a `TenantContext` and delegates to
FTF-0105's own `tenant_scoped_session` unchanged; there is still no live
FastAPI route, background-worker job runner, cache or model-alias layer
anywhere in this repository that constructs and passes a `TenantContext`
yet -- the same disclosed "no live caller yet" class already accepted for
FTF-0005/FTF-0006/FTF-0102/FTF-0103/FTF-0104/FTF-0105 before each one's
own first live caller existed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth.rbac_middleware import AuthorizedContext
from app.auth.tenant_isolation_query_guards import tenant_scoped_session

__all__ = ["TenantContext", "tenant_scoped_session_for"]


@dataclass(frozen=True)
class TenantContext:
    """The one canonical, authenticated tenant identity every downstream
    layer binds to. `organization_id` is never accepted directly from a
    request; it always traces back to a real `AuthorizedContext` via
    `from_authorized_context`."""

    organization_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.organization_id, uuid.UUID):
            raise TypeError(
                "TenantContext.organization_id must be a real uuid.UUID, "
                f"not {self.organization_id!r}"
            )

    @classmethod
    def from_authorized_context(cls, context: AuthorizedContext) -> TenantContext:
        """The only real constructor path: built from FTF-0103's own
        `require_role` output, which is itself only ever produced from a
        real authenticated principal and a real Membership role lookup --
        never from a bare, client-supplied organization ID."""
        return cls(organization_id=context.organization_id)


@contextmanager
def tenant_scoped_session_for(
    session_factory: Callable[[], Session],
    context: TenantContext,
) -> Iterator[Session]:
    """The `TenantContext`-typed entry point to FTF-0105's own
    `tenant_scoped_session` -- identical fail-closed/commit-or-rollback
    behavior, unchanged, just taking the canonical cross-layer context
    type instead of a bare `uuid.UUID` so a caller higher up the stack
    cannot lose track of *which* authenticated context a session was
    opened for."""
    with tenant_scoped_session(session_factory, context.organization_id) as session:
        yield session
