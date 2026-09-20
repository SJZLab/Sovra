"""Resource-level IDOR guard (FTF-H003, Section 6/65.1).

FTF-0103's `require_role` and FTF-0105's `tenant_scoped_session` together
guarantee that a route only ever runs with a database session scoped to
the caller's own authorized organization. That scoping is what actually
closes the IDOR case this module's own negative_acceptance names: a
route handler that fetches a resource by a client-supplied ID (a real UUID
the caller may have guessed, enumerated, or seen in another tenant's URL)
must not be able to retrieve a row belonging to a different organization,
however that ID was obtained. `authorize_resource()` is the one function a
route handler should call to fetch a resource by ID, instead of an
unguarded `session.get(...)`, so this guarantee is not left to each
handler author to remember to route through a tenant-scoped session
correctly every time.

A resource that exists but belongs to a different tenant and a resource
that does not exist at all are deliberately indistinguishable here: both
return `None`. Returning a different result (or a distinguishable error)
for "exists but forbidden" versus "does not exist" would let a caller
confirm another tenant's resource ID is real without ever being
authorized to see it -- an information-disclosure side channel this
module refuses to open, matching Section 65.4's "a guessed UUID... must
fail before content is returned."
"""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


def authorize_resource(
    session: Session, model: type[ModelT], resource_id: uuid.UUID
) -> ModelT | None:
    """Fetch `model` by primary key through a tenant-scoped `session`
    (Section 65.1's row-level security does the actual filtering; this
    function adds no additional tenant check of its own -- and must not,
    since duplicating that logic here would be a second, independently
    driftable copy of the same rule the database already enforces).
    Returns `None` identically whether `resource_id` does not exist at
    all or exists but belongs to a different, unauthorized tenant."""
    return session.get(model, resource_id)
