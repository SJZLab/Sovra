"""FTF-R005 -- read-only registry binding for `/model-versions/{id}/registry-binding`
(Section 78.3, 76.3 Q38).

A Factory `ModelVersion` is the governance envelope; MLflow holds an external
version record. This module binds the two and projects that binding read-only.
It has no approval concept on purpose: nothing read from MLflow (a tag, an
alias, a stage, a description) can approve, promote or change identity
(Section 78.3: "Never rely solely on mutable tags for identity, permissions,
approval or final result truth"). The Factory-side artifact digest is the
authority; the MLflow record is checked against it, and any difference is
reported as drift, never absorbed.

Tenant scoping: the registered-model name is derived from the caller's
organization and the Factory ModelVersion id only, and a returned record whose
tenant or identity tags disagree is refused, so an id from another tenant
resolves to nothing.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from packages.integrations.mlflow.client import (
    MlflowClient,
    MlflowClientError,
    TrackingRejectedError,
    _tags_of,
    is_approved_source,
)

__all__ = [
    "PROJECTION_FIELDS",
    "RegistryBinding",
    "RegistryDriftError",
    "bind_model_version",
    "project_registry_binding",
    "registered_model_name",
    "verify_binding",
]

TAG_ORG = "factory.organization_id"
TAG_MODEL_VERSION = "factory.model_version_id"
TAG_ARTIFACT = "factory.artifact_sha256"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")

PROJECTION_FIELDS = (
    "model_version_id",
    "registry",
    "registered_model_name",
    "registry_version",
    "run_id",
    "artifact_sha256",
    "approval_authority",
    "drift",
)


class RegistryDriftError(MlflowClientError):
    """The MLflow record disagrees with the Factory envelope."""


@dataclass(frozen=True)
class RegistryBinding:
    organization_id: uuid.UUID
    model_version_id: uuid.UUID
    registered_model_name: str
    registry_version: str
    run_id: str
    source_uri: str
    artifact_sha256: str


def registered_model_name(organization_id: uuid.UUID, model_version_id: uuid.UUID) -> str:
    if not isinstance(organization_id, uuid.UUID) or not isinstance(model_version_id, uuid.UUID):
        raise ValueError("organization_id and model_version_id must be uuid.UUID")
    return f"sovra-{organization_id}-{model_version_id}"


def _check_inputs(artifact_sha256: object, run_id: object) -> None:
    if not isinstance(artifact_sha256, str) or not _HASH.fullmatch(artifact_sha256):
        raise ValueError("artifact_sha256 must be lowercase sha256 hex")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a 32-hex MLflow run id")


def bind_model_version(
    client: MlflowClient,
    *,
    organization_id: uuid.UUID,
    model_version_id: uuid.UUID,
    run_id: str,
    artifact_sha256: str,
    source_uri: str,
    approved_source_prefixes: Sequence[str],
) -> RegistryBinding:
    """Creates (or finds, on replay) the one external version for a Factory
    ModelVersion. A replay after a lost response finds the existing version by
    its identity tag instead of creating a second one."""
    _check_inputs(artifact_sha256, run_id)
    if not is_approved_source(source_uri, approved_source_prefixes):
        raise TrackingRejectedError("model source is outside the approved artifact prefixes")
    name = registered_model_name(organization_id, model_version_id)
    identity = {TAG_ORG: str(organization_id), TAG_MODEL_VERSION: str(model_version_id)}
    client.ensure_registered_model(name, identity)
    existing = client.search_model_versions_by_tag(name, TAG_MODEL_VERSION, str(model_version_id))
    if len(existing) > 1:
        raise RegistryDriftError("more than one registry version claims this ModelVersion")
    if existing:
        record = existing[0]
    else:
        record = client.create_model_version(
            name,
            source=source_uri,
            run_id=run_id,
            tags={**identity, TAG_ARTIFACT: artifact_sha256},
            approved_source_prefixes=approved_source_prefixes,
        )
    binding = RegistryBinding(
        organization_id=organization_id,
        model_version_id=model_version_id,
        registered_model_name=name,
        registry_version=str(record.get("version", "")),
        run_id=run_id,
        source_uri=source_uri,
        artifact_sha256=artifact_sha256,
    )
    verify_binding(client, binding, approved_source_prefixes=approved_source_prefixes)
    return binding


def verify_binding(
    client: MlflowClient,
    binding: RegistryBinding,
    *,
    approved_source_prefixes: Sequence[str],
) -> None:
    """Raises `RegistryDriftError` unless the live MLflow record still matches
    the Factory envelope exactly. Aliases, stages and approval-looking tags
    are ignored: they carry no authority and cannot cause or mask drift."""
    if binding.registered_model_name != registered_model_name(
        binding.organization_id, binding.model_version_id
    ):
        raise RegistryDriftError("binding name is not derived from its tenant and ModelVersion")
    record = client.get_model_version(binding.registered_model_name, binding.registry_version)
    tags = _tags_of(record)
    checks: Mapping[str, tuple[Any, Any]] = {
        "organization tag": (tags.get(TAG_ORG), str(binding.organization_id)),
        "ModelVersion tag": (tags.get(TAG_MODEL_VERSION), str(binding.model_version_id)),
        "artifact digest": (tags.get(TAG_ARTIFACT), binding.artifact_sha256),
        "run id": (record.get("run_id"), binding.run_id),
        "source": (record.get("source"), binding.source_uri),
        "name": (record.get("name"), binding.registered_model_name),
    }
    for label, (actual, expected) in checks.items():
        if actual != expected:
            raise RegistryDriftError(f"registry {label} differs from the Factory envelope")
    if not is_approved_source(record.get("source"), approved_source_prefixes):
        raise RegistryDriftError("registry source is outside the approved artifact prefixes")


def project_registry_binding(binding: RegistryBinding, *, drift: str | None) -> dict[str, Any]:
    """The read-only response body. Fixed field set; carries no tags,
    aliases, stages or approval state from MLflow."""
    return {
        "model_version_id": str(binding.model_version_id),
        "registry": "mlflow",
        "registered_model_name": binding.registered_model_name,
        "registry_version": binding.registry_version,
        "run_id": binding.run_id,
        "artifact_sha256": binding.artifact_sha256,
        "approval_authority": "factory",
        "drift": drift,
    }
