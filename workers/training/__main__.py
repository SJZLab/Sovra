"""Training worker entry (`python -m workers.training <command>`).

`validate-environment` is the worker's preflight (FTF-0705, Section 19 "Validation checks"): it
runs `validate_nvidia_environment` against the real machine, prints the typed result as one JSON
object and exits 0 for SUPPORTED, 3 for BLOCKED and 2 for a usage error. A worker that is not
SUPPORTED must not accept a training job; the exit code is how its supervisor knows.

Requirements come from the model's Section 13 `TrainingCapability` (`--min-gpu-vram-gb`,
`--precision`), the job's storage need (`--min-free-storage-gb`) and the worker's configuration:
`SOVRA_TRAINING_WORK_DIR` (or `--work-dir`) and the `SOVRA_STORAGE_S3_*` /
`SOVRA_TRAINING_OBJECT_STORE_BUCKET` variables read by `object_store_probe.probe_from_environment`.
Secrets are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence

from workers.training.nvidia_environment_validation import (
    EnvironmentRequirements,
    HostAccess,
    SupportedNvidiaProfile,
    validate_nvidia_environment,
)
from workers.training.object_store_probe import probe_from_environment

__all__ = ["EXIT_BLOCKED", "EXIT_SUPPORTED", "EXIT_USAGE", "main"]

EXIT_SUPPORTED = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3
GIB = 1024**3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m workers.training")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser(
        "validate-environment", help="probe this machine's NVIDIA training environment"
    )
    validate.add_argument("--min-gpu-vram-gb", type=int, required=True)
    validate.add_argument("--precision", choices=("bf16", "fp16"), required=True)
    validate.add_argument("--min-free-storage-gb", type=int, required=True)
    validate.add_argument("--work-dir", default=None)
    validate.add_argument("--allow-development-host", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    host: HostAccess | None = None,
) -> int:
    environ = os.environ if env is None else env
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_SUPPORTED
    work_dir = args.work_dir or environ.get("SOVRA_TRAINING_WORK_DIR", "")
    try:
        requirements = EnvironmentRequirements(
            min_gpu_vram_gb=args.min_gpu_vram_gb,
            require_bf16=args.precision == "bf16",
            storage_path=work_dir,
            min_free_storage_bytes=args.min_free_storage_gb * GIB,
            object_store=probe_from_environment(environ),
            allow_development_host=args.allow_development_host,
        )
    except ValueError as exc:
        print(json.dumps({"status": "USAGE_ERROR", "detail": str(exc)}), file=sys.stderr)
        return EXIT_USAGE
    result = validate_nvidia_environment(requirements, host)
    print(json.dumps(result.to_dict(), sort_keys=True))
    return EXIT_SUPPORTED if isinstance(result, SupportedNvidiaProfile) else EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
