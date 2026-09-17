# Component lifecycle and storage qualification (FTF-R017)

Section 80.4 requires every selected reuse component and its transitive
dependencies to carry "approved license/source notices, pinned
versions/digests, vulnerability checks, upgrade/rollback tests,
provenance and an identified maintainer/operator", and requires
qualifying the maintained storage backend "against the actual multipart,
checksum, versioning, immutable-publication, identity, retention/
deletion, encryption, backup and restore contract."

This document is the human-readable summary of the two machine-validated
records that actually carry that evidence. It never restates a claim
those records don't already make -- read them directly for the binding
text.

## OSS dependency lifecycle

Source of truth: `spec/component-maintenance.yaml`, schema
`spec/component-maintenance.schema.json`, validated by
`scripts/validate_component_maintenance.py` (wired into
`.github/workflows/ci.yml`'s `gate` job).

All seven canonical components (Axolotl, Inspect AI, MLflow, vLLM,
Temporal, the existing LiteLLM gateway, Argilla) are recorded exactly
once. Every license claim was verified against that project's own current
`LICENSE` file this task (the exact URL fetched is recorded on each
record as `license_evidence_url`), not asserted from memory:

| Component | License | Support model |
|---|---|---|
| Axolotl | Apache-2.0 | community_oss_self_hosted |
| Inspect AI | MIT | community_oss_self_hosted |
| MLflow | Apache-2.0 | community_oss_self_hosted |
| vLLM | Apache-2.0 | community_oss_self_hosted |
| Temporal | MIT | community_oss_self_hosted |
| LiteLLM | MIT (core; `enterprise/` directory is separately licensed and **not used**) | existing_internal_deployment |
| Argilla | Apache-2.0 | community_oss_self_hosted |

All seven are permissive OSS licenses; none required an owner-approval
ask under CLAUDE.md's "new runtime deps" / "keyed external services"
hard stops. `enterprise_or_proprietary_features_used` is structurally
`false` for every record (`spec/component-maintenance.schema.json` never
accepts `true` there) -- this Factory reuses each component's open-core
surface only, including LiteLLM's, whose separate proprietary
`enterprise/` directory this Factory never imports or deploys.

Exact pinned package/image versions are **not yet resolved** for any of
the seven -- Section 64.3's onboarding gate has not run yet
(`spec/release-scope.yaml` records every capability's
`supported_version_profile` as `NOT_YET_RESOLVED` for the same reason).
`version_status` on each record says so honestly rather than fabricating
a pin ahead of that gate; the eventual pinned tuple belongs in
`spec/component-lock.schema.json`'s `ComponentVersionLock` instance
(FTF-R001's frozen contract), a separate, not-yet-delivered task.

Each record also carries a real `export_plan` and `restore_plan` -- see
`spec/component-maintenance.yaml` directly for the per-component text;
none of the seven depend on that component's own proprietary export
format for Factory-owned data.

## Old external S3 source compatibility

`spec/component-maintenance.yaml`'s `old_external_s3_compatibility` block
records the real policy: the storage client
(`apps/api/app/services/storage_semantics_client.py`) takes its target
endpoint and credentials as plain constructor arguments
(`S3Endpoint`/`S3Credentials`), never a hardcoded host, so the identical
signed-request code path qualifies the organization's real external S3
endpoint the moment one is provisioned -- switching `backend` in
`infra/internal/storage-profile.yaml` is a configuration change, never a
code migration. This repository has no pre-existing organizational S3
data to migrate away from today; nothing here performs or forces a
silent migration.

## Storage semantics qualification

Source of truth: `infra/internal/storage-profile.yaml`, validated by
`scripts/validate_storage_profile.py`. `backend` is currently
`seaweedfs_offline_fixture` -- `infra/docker/docker-compose.yml`'s
already-booted `s3` service (FTF-0004), now with a real, non-anonymous S3
identity (`infra/docker/seaweedfs-s3-identity.json`) so the endpoint
genuinely enforces caller identity rather than SeaweedFS's own
unconfigured "accept any access key" default. `minio` is not a value this
schema can select at all (Section 80.4: "the old blanket MinIO default is
removed").

The real pass/fail/blocked evidence for each of the eight required
semantics is produced by `tests/integration/test_storage_semantics.py`
against the live container, run in
`.github/workflows/ci.yml`'s `storage-semantics-integration` job. This
repository's interactive dev sandbox has no container-registry egress
(confirmed again this task with a real `docker pull` against this exact
pinned SeaweedFS digest -- the same reproducible 403 FTF-0004's own
evidence already documents), so that job runs on GitHub Actions' hosted
runner instead, matching FTF-0004's own `docker-local-stack-integration`
precedent. `infra/internal/storage-profile.yaml`'s `required_semantics`
entries stay honestly `qualified: false` / `evidence: NOT_YET_RUN` until
a maintainer records that CI job's real result -- `scripts/
validate_storage_profile.py` structurally rejects `qualified: true`
paired with placeholder evidence, so this file cannot be hand-flipped to
claim a result the container never produced.

The SigV4 request-signing math itself (the highest-risk part of the
client -- an HMAC chain, not something covered by "the request returned
2xx") is proven without any container in
`tests/tasks/test_ftf_r017.py`, against a real, externally-published AWS
Signature Version 4 test vector fetched and independently re-verified
this task, not asserted from memory.
