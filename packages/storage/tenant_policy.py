"""Tenant-scoped object storage key policy (FTF-H003, Section 65.2).

Section 65.2 requires "private storage policies, per-tenant prefixes and
keys/access policy" and that "no user-supplied filesystem or bucket path
may bypass project ownership." No live object-storage route or
presigned-URL issuer exists anywhere in this repository yet (FTF-R017
delivered a real, generically-endpointed S3 signing client --
`apps/api/app/services/storage_semantics_client.py` -- but nothing yet
calls it from a live product route); this module is the tenant-scoped key
policy such a route is required to apply before ever asking that client
to sign a grant, proven directly and independently of any live route --
the same disclosed "no live caller yet" class already accepted for
FTF-0005/FTF-0006/FTF-0102/FTF-0103/FTF-0104/FTF-0105 before each one's
own first live caller existed.

`build_tenant_scoped_object_key` is the one way an object key is ever
constructed: always `organizations/{organization_id}/{resource_type}/
{resource_id}/{filename}`, from real UUIDs and a filename restricted to a
safe character set -- there is no code path that accepts an
already-assembled key from a caller and trusts it, so path traversal
cannot be introduced by construction.

`validate_object_key_belongs_to_tenant` is the second, independent check
a download/presigned-URL issuer must run against a key from any other
source (a stored `artifact_uri`, an object-storage listing) before
granting access -- Section 65.2's "no user-supplied filesystem or bucket
path may bypass project ownership" as a guard a caller cannot skip
in-line, not a convention. It rejects a foreign-tenant prefix (a guessed
or borrowed key that starts with a different organization's UUID), any
path-traversal or absolute-path attempt, and any key that is not even
shaped like this policy's own keys -- fail-closed (`False`, never an
exception a careless caller might catch and ignore) on every malformed
input.
"""

from __future__ import annotations

import re
import uuid

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

__all__ = [
    "ObjectKeyPolicyError",
    "build_tenant_scoped_object_key",
    "validate_object_key_belongs_to_tenant",
]


class ObjectKeyPolicyError(ValueError):
    """A candidate object key is not a well-formed, tenant-scoped key --
    refused before any storage call is made, never sanitized/rewritten
    and retried."""


def build_tenant_scoped_object_key(
    organization_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
    filename: str,
) -> str:
    """The one way a tenant-scoped object key is ever constructed. Refuses
    (never silently truncates/sanitizes) any input that is not already a
    real UUID or a safe filename/resource-type segment."""
    if not isinstance(organization_id, uuid.UUID):
        raise ObjectKeyPolicyError(
            f"organization_id must be a real uuid.UUID, not {organization_id!r}"
        )
    if not isinstance(resource_id, uuid.UUID):
        raise ObjectKeyPolicyError(f"resource_id must be a real uuid.UUID, not {resource_id!r}")
    for label, value in (("resource_type", resource_type), ("filename", filename)):
        if not isinstance(value, str) or not _SAFE_SEGMENT.fullmatch(value):
            raise ObjectKeyPolicyError(f"{label} is not a safe key segment: {value!r}")
    return f"organizations/{organization_id}/{resource_type}/{resource_id}/{filename}"


def validate_object_key_belongs_to_tenant(key: str, organization_id: uuid.UUID) -> bool:
    """`True` only if `key` is a well-formed key produced by
    `build_tenant_scoped_object_key`'s own policy *and* its leading tenant
    segment equals `organization_id` exactly. Fail-closed `False` (never
    raises) on any malformed, path-traversing or foreign-tenant key, so a
    caller cannot forget a try/except and accidentally treat a raised
    exception as "allowed"."""
    if not isinstance(organization_id, uuid.UUID):
        raise ObjectKeyPolicyError(
            f"organization_id must be a real uuid.UUID, not {organization_id!r}"
        )
    if not isinstance(key, str) or not key:
        return False
    if ".." in key or key.startswith("/") or "\\" in key or "\x00" in key:
        return False
    segments = key.split("/")
    if len(segments) != 5 or segments[0] != "organizations":
        return False
    _prefix, org_segment, resource_type, resource_id_segment, filename = segments
    try:
        key_organization_id = uuid.UUID(org_segment)
        resource_id = uuid.UUID(resource_id_segment)
    except (ValueError, AttributeError, TypeError):
        return False
    if not _SAFE_SEGMENT.fullmatch(resource_type) or not _SAFE_SEGMENT.fullmatch(filename):
        return False
    del resource_id  # validated shape only; identity is not this check's concern
    return key_organization_id == organization_id
