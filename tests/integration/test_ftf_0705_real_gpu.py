#!/usr/bin/env python3
"""FTF-0705 real-GPU qualification: the positive clause "Real NVIDIA/driver/CUDA/container/bf16/
memory/storage tests return supported profile", on a REAL NVIDIA machine.

Selected by the machine itself: the real validator runs with `RealHostAccess` (no doubles).

- No NVIDIA GPU stack here (`nvidia_smi_not_found`, `gpu_absent`, `driver_not_loaded`): prints
  BLOCKED_EXTERNAL with the exact need on stderr and exits 2. Never a skip, never a pass.
- A GPU machine whose probes do not all pass: prints the BLOCKED result and exits 1 (a real
  failure on real hardware).
- SUPPORTED: checks the profile against an independent `nvidia-smi -L` listing and exits 0.

Inputs (all optional except on a GPU runner, where the object store must be configured):
`SOVRA_FTF0705_MIN_GPU_VRAM_GB` (default 8), `SOVRA_FTF0705_PRECISION` (`bf16` default, or
`fp16`), `SOVRA_TRAINING_WORK_DIR` (default a fresh temporary directory) and the
`SOVRA_STORAGE_S3_*` / `SOVRA_TRAINING_OBJECT_STORE_BUCKET` variables.

Run: `uv run python3 tests/integration/test_ftf_0705_real_gpu.py`
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from workers.training import nvidia_environment_validation as nv  # noqa: E402
from workers.training.object_store_probe import probe_from_environment  # noqa: E402

NEED = "one machine or runner with an NVIDIA GPU, driver and CUDA; owner supplies"
EXIT_BLOCKED_EXTERNAL = 2


def main() -> int:
    vram = int(os.environ.get("SOVRA_FTF0705_MIN_GPU_VRAM_GB", "8"))
    precision = os.environ.get("SOVRA_FTF0705_PRECISION", "bf16")
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = os.environ.get("SOVRA_TRAINING_WORK_DIR") or tmp
        requirements = nv.EnvironmentRequirements(
            min_gpu_vram_gb=vram,
            require_bf16=precision == "bf16",
            storage_path=work_dir,
            min_free_storage_bytes=1024**3,
            object_store=probe_from_environment(os.environ),
        )
        result = nv.validate_nvidia_environment(requirements)
    print(json.dumps(result.to_dict(), sort_keys=True))
    if nv.blocked_by_gpu_absence(result):
        assert isinstance(result, nv.BlockedEnvironment)
        message = (
            f"BLOCKED_EXTERNAL: FTF-0705 real-GPU qualification needs {NEED}. "
            f"This machine: {result.failed_probe} -> {result.code} ({result.detail}). "
            "Not a skip and not a pass: the positive clause stays unproven until this runs on "
            "a real NVIDIA GPU."
        )
        print(f"::warning::{message}")
        print(message, file=sys.stderr)
        return EXIT_BLOCKED_EXTERNAL
    if isinstance(result, nv.BlockedEnvironment):
        print(
            f"FAIL [FTF-0705/GPU] GPU machine not supported: {result.failed_probe} -> "
            f"{result.code} ({result.detail}); all failed: {list(result.failed_probes)}",
            file=sys.stderr,
        )
        return 1
    listing = subprocess.run(
        ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30, check=False
    )
    listed = [line for line in listing.stdout.splitlines() if line.startswith("GPU ")]
    failures = []
    if listing.returncode != 0 or len(listed) != len(result.gpus):
        failures.append("independent nvidia-smi -L disagrees with the profile's device count")
    if not all(g.uuid in listing.stdout for g in result.gpus):
        failures.append("a profile GPU uuid is not in nvidia-smi -L")
    if precision == "bf16" and not result.bf16_supported:
        failures.append("bf16 required but the profile does not report it")
    if not result.qualified_device_indices:
        failures.append("no qualified device in a supported profile")
    if failures:
        for failure in failures:
            print(f"FAIL [FTF-0705/GPU] {failure}", file=sys.stderr)
        return 1
    print(
        f"PASS FTF-0705 real-GPU qualification: {len(result.gpus)} GPU(s), driver "
        f"{result.driver_version}, CUDA {result.cuda_version}, torch {result.torch_version}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
