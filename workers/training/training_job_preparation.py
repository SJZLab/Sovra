"""Training job preparation stage (FTF-0706; FINE_TUNING_FACTORY_RELEASE_1.md Section 16 steps 4-8).

The training worker prepares a job's workspace in one fixed order:

  1. create the job's isolated directory `<workspace_root>/<config_sha256>/` (mode 0700, must not
     exist: a second preparation of the same job is refused, never merged);
  2. load the exact approved base model and tokenizer (`load_base_artifact`, Section 16 steps 4-5):
     the base files land in `<job>/base/` only after every digest verified;
  3. only then open the sensitive dataset mount (`mount_datasets(<job>/data)`, steps 6-7).

The order is the security property: unknown remote code or an inconsistent or missing artifact is
refused before any training data is mounted into the worker (FTF-0706 negative acceptance). If the
base load fails, `mount_datasets` is never called and the job directory is removed, so a failed or
blocked job leaves nothing behind. A mount failure also removes the directory.

`mount_datasets` is the worker's dataset-mount step, passed in by the worker runtime (the FTF-0519
split loader behind the signed job spec); this stage decides only when it may run.

Disclosed: the signed job spec receive/verify (Section 16 steps 1-3) and the dispatcher that runs
this stage on a claimed job (FTF-0801/FTF-R003) are later tasks; this stage is their entry into the
worker's base-artifact step.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from packages.security.outbound_guard import GuardedResponse, guarded_fetch
from packages.security.url_policy import ConnectionPolicy

from app.artifacts.artifact_schema import TrainableArtifact
from app.artifacts.private_s3_artifact_resolver import PrivateS3ConnectionConfig
from workers.training.base_artifact_loading import (
    BaseArtifactLoadError,
    LoadedBaseArtifact,
    load_base_artifact,
)
from workers.training.training_config_schema import ResolvedTrainingConfig

__all__ = ["PreparedTrainingJob", "prepare_training_job"]

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

MountT = TypeVar("MountT")


@dataclass(frozen=True)
class PreparedTrainingJob(Generic[MountT]):
    job_directory: Path
    base: LoadedBaseArtifact
    datasets: MountT


def prepare_training_job(
    config: ResolvedTrainingConfig,
    artifact: TrainableArtifact,
    *,
    connection: PrivateS3ConnectionConfig,
    workspace_root: Path,
    mount_datasets: Callable[[Path], MountT],
    policy: ConnectionPolicy | None = None,
    fetch: Callable[..., GuardedResponse] = guarded_fetch,
) -> PreparedTrainingJob[MountT]:
    """Creates the job directory, loads the approved base artifact, then mounts the datasets.
    Raises `BaseArtifactLoadError` (and never mounts) when the base artifact is refused."""
    job_id = config.config_sha256
    if type(job_id) is not str or _SHA256_HEX.fullmatch(job_id) is None:
        raise BaseArtifactLoadError("config_integrity_failed", "config_sha256_malformed")
    job_directory = workspace_root / job_id
    try:
        job_directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError:
        raise BaseArtifactLoadError(
            "workspace_unavailable", "job_directory_not_creatable"
        ) from None
    try:
        base = load_base_artifact(
            config,
            artifact,
            connection=connection,
            directory=job_directory / "base",
            policy=policy,
            fetch=fetch,
        )
        datasets = mount_datasets(job_directory / "data")
    except BaseException:
        shutil.rmtree(job_directory, ignore_errors=True)
        raise
    return PreparedTrainingJob(job_directory=job_directory, base=base, datasets=datasets)
