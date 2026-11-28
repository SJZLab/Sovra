#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,method-assign,misc,union-attr,index"
"""FTF-0705 negative-test proof: NVIDIA environment validation (no GPU, database or network).

V  `validate_nvidia_environment` over a test-only `HostAccess` that returns recorded probe output
   (nvidia-smi rows, the PyTorch probe's JSON, docker info, /dev listing, disk numbers): the
   all-good machine gives a SUPPORTED profile, and every single fault gives BLOCKED naming the
   exact probe and code, with the boundary values of every bound. These doubles exist only here;
   the product path always uses `RealHostAccess`.
P  The strict parsers (`parse_nvidia_smi`, `parse_driver_version`, `parse_torch_report`) and the
   PyTorch probe script itself, executed against a stand-in `torch` module.
S  `workers/training/object_store_probe.py` over a stand-in S3 client: every S3 / network failure
   maps to its code; configuration from the environment is all-or-nothing.
R  REAL machine: `RealHostAccess` primitives, and the validator plus the worker entry
   (`python -m workers.training validate-environment`) run on this machine. On a machine with no
   NVIDIA GPU (CI, this container) the result is BLOCKED -- never SUPPORTED -- and the real-GPU
   qualification script reports BLOCKED_EXTERNAL with exit 2, never a pass.

The real SeaweedFS proof of the object-store probe is
`tests/integration/test_ftf_0705_object_store_s3.py`; the real-GPU qualification is
`tests/integration/test_ftf_0705_real_gpu.py`.

Run: `uv run python3 tests/tasks/test_ftf_0705.py`
"""

# ruff: noqa: E501
from __future__ import annotations

import contextlib
import dataclasses
import http.client
import http.server
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import workers.training.__main__ as entry  # noqa: E402
from workers.training import nvidia_environment_validation as nv  # noqa: E402
from workers.training import object_store_probe as osp  # noqa: E402

CID = "FTF-0705"
FAILURES: list[str] = []
CHECKS = [0]
GIB = 1024**3
MIB = 1024**2


def check(tag: str, description: str, condition: object) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID}/{tag}] {description}")


# --------------------------------------------------------------------------------------------
# Test-only host: recorded probe output
# --------------------------------------------------------------------------------------------

SMI_A100 = "0, NVIDIA A100-SXM4-80GB, GPU-5f1c2d3e-aaaa-bbbb-cccc-0123456789ab, 535.104.05, 81920, 81000, 8.0\n"


def torch_report(**overrides: Any) -> str:
    report: dict[str, Any] = {
        "torch_importable": True,
        "torch_version": "2.5.1+cu124",
        "cuda_version": "12.4",
        "cuda_available": True,
        "devices": [
            {
                "index": 0,
                "name": "NVIDIA A100-SXM4-80GB",
                "capability": [8, 0],
                "total_bytes": 80 * GIB,
                "free_bytes": 79 * GIB,
            }
        ],
        "bf16_native": True,
        "bf16_matmul_ok": True,
    }
    report.update(overrides)
    return json.dumps(report) + "\n"


class Store:
    bucket = "sovra-training"

    def __init__(self, failure: nv.ObjectStoreFailure | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, bytes]] = []

    def round_trip(self, key: str, body: bytes) -> None:
        self.calls.append((key, body))
        if self.failure is not None:
            raise self.failure


class Host:
    def __init__(self) -> None:
        self.system_name = "Linux"
        self.files: dict[str, str] = {
            "/proc/sys/kernel/osrelease": "6.8.0-45-generic\n",
            "/proc/1/cgroup": "0::/\n",
        }
        self.existing: set[str] = {"/.dockerenv"}
        self.dirs: dict[str, list[str]] = {
            "/dev": ["null", "nvidia-uvm", "nvidia0", "nvidiactl", "zero"]
        }
        self.work_dirs: set[str] = {"/work"}
        self.free = 500 * GIB
        self.write_error: OSError | None = None
        self.free_error: OSError | None = None
        self.commands: dict[str, nv.CommandResult] = {
            "nvidia-smi": nv.CommandResult(0, SMI_A100, ""),
            "docker": nv.CommandResult(
                0, '{"nvidia":{"path":"nvidia-container-runtime"},"runc":{}}\n', ""
            ),
            sys.executable: nv.CommandResult(0, torch_report(), ""),
        }
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, argv, timeout):
        self.calls.append((tuple(argv), timeout))
        return self.commands.get(argv[0], nv.CommandResult(None, "", "not found"))

    def system(self):
        return self.system_name

    def read_text(self, path):
        return self.files.get(path)

    def exists(self, path):
        return path in self.existing

    def list_dir(self, path):
        return list(self.dirs.get(path, []))

    def is_dir(self, path):
        return path in self.work_dirs

    def free_bytes(self, path):
        if self.free_error is not None:
            raise self.free_error
        return self.free

    def write_probe_file(self, directory):
        if self.write_error is not None:
            raise self.write_error


def req(**overrides: Any) -> nv.EnvironmentRequirements:
    values: dict[str, Any] = {
        "min_gpu_vram_gb": 24,
        "require_bf16": True,
        "storage_path": "/work",
        "min_free_storage_bytes": 100 * GIB,
        "object_store": Store(),
    }
    values.update(overrides)
    return nv.EnvironmentRequirements(**values)


def run(host: Host | None = None, **overrides: Any):
    return nv.validate_nvidia_environment(req(**overrides), host or Host())


def probe(result, name: str) -> nv.ProbeOutcome:
    return next(p for p in result.probes if p.probe == name)


def expect_blocked(tag: str, result, failed_probe: str, code: str) -> None:
    check(tag, f"BLOCKED (got {type(result).__name__})", isinstance(result, nv.BlockedEnvironment))
    if isinstance(result, nv.BlockedEnvironment):
        check(
            tag,
            f"failed_probe {failed_probe} (got {result.failed_probe})",
            result.failed_probe == failed_probe,
        )
        check(tag, f"code {code} (got {result.code})", result.code == code)
        check(tag, "status BLOCKED", result.status == nv.BLOCKED)
        check(
            tag, "failed probe outcome carries the code", probe(result, failed_probe).code == code
        )
        check(tag, "failed probe status failed", probe(result, failed_probe).status == "failed")
        check(
            tag,
            "detail equals the probe's detail",
            result.detail == probe(result, failed_probe).detail,
        )
        check(tag, "failed_probes lists it", failed_probe in result.failed_probes)
        check(tag, "probes in fixed order", tuple(p.probe for p in result.probes) == nv.PROBE_ORDER)


def expect_supported(tag: str, result) -> None:
    check(
        tag,
        f"SUPPORTED (got {getattr(result, 'code', result)!r})",
        isinstance(result, nv.SupportedNvidiaProfile),
    )
    if isinstance(result, nv.SupportedNvidiaProfile):
        check(tag, "every probe passed", all(p.passed and p.code == "ok" for p in result.probes))


# --------------------------------------------------------------------------------------------
# V: validator
# --------------------------------------------------------------------------------------------


def part_v_supported() -> None:
    host = Host()
    store = Store()
    result = nv.validate_nvidia_environment(req(object_store=store), host)
    expect_supported("V1", result)
    assert isinstance(result, nv.SupportedNvidiaProfile)
    check("V1", "status", result.status == nv.SUPPORTED)
    check("V1", "host profile linux", result.host_profile == "linux")
    check("V1", "in container", result.in_container is True)
    check("V1", "driver", result.driver_version == "535.104.05")
    check("V1", "cuda", result.cuda_version == "12.4")
    check("V1", "torch", result.torch_version == "2.5.1+cu124")
    check("V1", "one gpu", len(result.gpus) == 1)
    g = result.gpus[0]
    check(
        "V1",
        "gpu fields",
        (
            g.index,
            g.name,
            g.uuid,
            g.driver_version,
            g.memory_total_bytes,
            g.memory_free_bytes,
            g.compute_capability,
        )
        == (
            0,
            "NVIDIA A100-SXM4-80GB",
            "GPU-5f1c2d3e-aaaa-bbbb-cccc-0123456789ab",
            "535.104.05",
            81920 * MIB,
            81000 * MIB,
            (8, 0),
        ),
    )
    check("V1", "qualified devices", result.qualified_device_indices == (0,))
    check("V1", "bf16", result.bf16_supported is True)
    check("V1", "storage", (result.storage_path, result.storage_free_bytes) == ("/work", 500 * GIB))
    check("V1", "bucket", result.object_store_bucket == "sovra-training")
    check("V1", "probe order", tuple(p.probe for p in result.probes) == nv.PROBE_ORDER)
    check("V1", "object store round trip called once", len(store.calls) == 1)
    key, body = store.calls[0]
    check(
        "V1",
        "probe key under sovra-env-probe/",
        key.startswith("sovra-env-probe/") and len(key) == len("sovra-env-probe/") + 32,
    )
    check("V1", "probe body non-empty", body.startswith(b"sovra nvidia environment probe "))
    d = result.to_dict()
    check("V1", "to_dict is JSON", json.loads(json.dumps(d)) == d)
    check("V1", "to_dict status", d["status"] == "SUPPORTED")
    check(
        "V1",
        "to_dict gpus",
        d["gpus"][0]["compute_capability"] == [8, 0]
        and d["gpus"][0]["memory_total_bytes"] == 81920 * MIB
        and d["gpus"][0]["uuid"] == g.uuid
        and d["gpus"][0]["name"] == g.name
        and d["gpus"][0]["index"] == 0
        and d["gpus"][0]["driver_version"] == "535.104.05"
        and d["gpus"][0]["memory_free_bytes"] == 81000 * MIB,
    )
    check(
        "V1",
        "to_dict probes",
        d["probes"][0] == {"probe": "host_os", "status": "passed", "code": "ok", "detail": "linux"},
    )
    check("V1", "to_dict qualified list", d["qualified_device_indices"] == [0])
    check(
        "V1",
        "to_dict keys",
        set(d) == {f.name for f in dataclasses.fields(nv.SupportedNvidiaProfile)},
    )
    smi_calls = [c for c in host.calls if c[0][0] == "nvidia-smi"]
    check(
        "V1",
        "nvidia-smi query argv",
        smi_calls == [(nv.NVIDIA_SMI_QUERY, nv.NVIDIA_SMI_TIMEOUT_SECONDS)],
    )
    torch_calls = [c for c in host.calls if c[0][0] == sys.executable]
    check(
        "V1",
        "torch probe runs isolated child",
        torch_calls
        == [((sys.executable, "-I", "-c", nv.TORCH_PROBE_SCRIPT), nv.TORCH_PROBE_TIMEOUT_SECONDS)],
    )
    check(
        "V1",
        "in-container path does not call docker",
        not [c for c in host.calls if c[0][0] == "docker"],
    )
    check(
        "V1",
        "probe details",
        probe(result, "nvidia_smi").detail == "1 device(s)"
        and probe(result, "driver").detail == "535.104.05"
        and probe(result, "gpu_memory").detail == "devices [0]"
        and probe(result, "bf16").detail == "supported"
        and probe(result, "object_store").detail == "bucket sovra-training",
    )


def part_v_host() -> None:
    h = Host()
    h.system_name = "Darwin"
    expect_blocked("H1", run(h), "host_os", "host_not_linux")
    h = Host()
    h.system_name = "Windows"
    expect_blocked("H2", run(h), "host_os", "host_not_linux")
    for release in (
        "5.15.153.1-microsoft-standard-WSL2",
        "6.6.36-Microsoft-standard",
        "4.4.0-19041-wsl",
    ):
        h = Host()
        h.files["/proc/sys/kernel/osrelease"] = release
        expect_blocked(f"H3 {release}", run(h), "host_os", "wsl2_development_only")
        r = run(h, allow_development_host=True)
        expect_supported(f"H4 {release}", r)
        check(
            "H4", "wsl2 labeled development", getattr(r, "host_profile", "") == "wsl2_development"
        )
    h = Host()
    del h.files["/proc/sys/kernel/osrelease"]
    expect_supported("H5 no osrelease file", run(h))
    # a host_os failure does not stop independent probes
    h = Host()
    h.system_name = "Darwin"
    r = run(h)
    check(
        "H6", "gpu probes still ran", probe(r, "nvidia_smi").passed and probe(r, "storage").passed
    )
    check("H6", "only host failed", r.failed_probes == ("host_os",))


def part_v_nvidia_smi() -> None:
    def with_smi(result: nv.CommandResult) -> Host:
        h = Host()
        h.commands["nvidia-smi"] = result
        return h

    h = Host()
    del h.commands["nvidia-smi"]
    r = run(h)
    expect_blocked("N1 not found", r, "nvidia_smi", "nvidia_smi_not_found")
    for name in ("driver", "container_gpu", "cuda_runtime", "bf16", "gpu_memory"):
        check(
            "N1",
            f"{name} not_run",
            probe(r, name).status == "not_run" and probe(r, name).code == "prerequisite_failed",
        )
    check(
        "N1",
        "driver not_run detail",
        probe(r, "driver").detail == "needs nvidia_smi"
        and probe(r, "cuda_runtime").detail == "needs driver"
        and probe(r, "bf16").detail == "needs cuda_runtime",
    )
    check(
        "N1",
        "storage and object store still run",
        probe(r, "storage").passed and probe(r, "object_store").passed,
    )
    check("N1", "failed_probes only nvidia_smi", r.failed_probes == ("nvidia_smi",))
    check("N1", "torch probe never ran", not [c for c in h.calls if c[0][0] == sys.executable])
    check("N1", "gpu absence", nv.blocked_by_gpu_absence(r))
    expect_blocked(
        "N2 timeout",
        run(with_smi(nv.CommandResult(None, "", "", timed_out=True))),
        "nvidia_smi",
        "nvidia_smi_timeout",
    )
    r = run(with_smi(nv.CommandResult(6, "No devices were found\n", "")))
    expect_blocked("N3 no devices", r, "nvidia_smi", "gpu_absent")
    check("N3", "gpu absence", nv.blocked_by_gpu_absence(r))
    expect_blocked(
        "N3b no devices on stderr",
        run(with_smi(nv.CommandResult(6, "", "No devices were found"))),
        "nvidia_smi",
        "gpu_absent",
    )
    msg = "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver. Make sure that the latest NVIDIA driver is installed and running."
    r = run(with_smi(nv.CommandResult(9, msg, "")))
    expect_blocked("N4 driver not loaded", r, "nvidia_smi", "driver_not_loaded")
    check("N4", "gpu absence", nv.blocked_by_gpu_absence(r))
    r = run(with_smi(nv.CommandResult(15, "", "Unknown Error")))
    expect_blocked("N5 other failure", r, "nvidia_smi", "nvidia_smi_failed")
    check("N5", "detail has exit code", r.detail.startswith("exit 15:"))
    check("N5", "a failing GPU machine is not gpu absence", not nv.blocked_by_gpu_absence(r))
    expect_blocked(
        "N6 exit 0 empty",
        run(with_smi(nv.CommandResult(0, "\n\n", ""))),
        "nvidia_smi",
        "gpu_absent",
    )
    bad_rows = {
        "six fields": "0, NVIDIA A100, GPU-abc, 535.104.05, 81920, 81000\n",
        "eight fields": SMI_A100.strip() + ", x\n",
        "index text": SMI_A100.replace("0, NVIDIA", "a, NVIDIA"),
        "negative index": SMI_A100.replace("0, NVIDIA", "-1, NVIDIA"),
        "name N/A": SMI_A100.replace("NVIDIA A100-SXM4-80GB", "[N/A]"),
        "name empty": SMI_A100.replace("NVIDIA A100-SXM4-80GB", ""),
        "name too long": SMI_A100.replace("NVIDIA A100-SXM4-80GB", "N" * 201),
        "uuid": SMI_A100.replace("GPU-5f1c2d3e-aaaa-bbbb-cccc-0123456789ab", "5f1c2d3e"),
        "uuid suffix junk": SMI_A100.replace("-0123456789ab", "-0123456789ab!"),
        "driver": SMI_A100.replace("535.104.05", "535"),
        "driver junk": SMI_A100.replace("535.104.05", "535.104.05x"),
        "total N/A": SMI_A100.replace("81920", "[N/A]"),
        "free negative": SMI_A100.replace("81000", "-1"),
        "free > total": SMI_A100.replace("81000", "81921"),
        "capability": SMI_A100.replace(", 8.0", ", 8"),
        "capability N/A": SMI_A100.replace(", 8.0", ", [N/A]"),
        "duplicate index": SMI_A100 + SMI_A100.replace("GPU-5f1c", "GPU-6f1c"),
    }
    for name, stdout in bad_rows.items():
        expect_blocked(
            f"N7 {name}",
            run(with_smi(nv.CommandResult(0, stdout, ""))),
            "nvidia_smi",
            "nvidia_smi_unparseable",
        )
    expect_supported(
        "N8 name 200 chars",
        run(
            with_smi(nv.CommandResult(0, SMI_A100.replace("NVIDIA A100-SXM4-80GB", "N" * 200), ""))
        ),
    )
    expect_supported(
        "N8 free == total",
        run(with_smi(nv.CommandResult(0, SMI_A100.replace("81000", "81920"), ""))),
    )
    expect_supported(
        "N8 MIG uuid",
        run(with_smi(nv.CommandResult(0, SMI_A100.replace("GPU-5f1c", "MIG-5f1c"), ""))),
    )
    spaced = "   \n" + SMI_A100.replace(", ", " , ").replace("0 , NVIDIA", " 0 ,NVIDIA") + "\t\n"
    expect_supported(
        "N8 spaces around fields and blank lines", run(with_smi(nv.CommandResult(0, spaced, "")))
    )
    two = SMI_A100 + SMI_A100.replace("0, NVIDIA", "1, NVIDIA").replace(
        "GPU-5f1c", "GPU-6f1c"
    ).replace("535.104.05", "550.54.15")
    expect_blocked(
        "N9 mixed drivers",
        run(with_smi(nv.CommandResult(0, two, ""))),
        "driver",
        "driver_inconsistent",
    )
    two_same = SMI_A100 + SMI_A100.replace("0, NVIDIA", "1, NVIDIA").replace("GPU-5f1c", "GPU-6f1c")
    r = run(with_smi(nv.CommandResult(0, two_same, "")))
    expect_supported("N10 two GPUs", r)
    check("N10", "two gpus parsed", len(getattr(r, "gpus", ())) == 2)


def part_v_driver() -> None:
    def with_driver(version: str) -> Host:
        h = Host()
        h.commands["nvidia-smi"] = nv.CommandResult(0, SMI_A100.replace("535.104.05", version), "")
        return h

    for version in ("525.60.13", "525.61", "526.0", "535.104.05", "560.35.03"):
        expect_supported(f"D1 {version}", run(with_driver(version)))
    for version in ("525.60.12", "525.60", "525.59.99", "470.256.02", "450.80.02", "9.0"):
        r = run(with_driver(version))
        expect_blocked(f"D2 {version}", r, "driver", "driver_incompatible")
        check(
            "D2", "cuda not run after driver failure", probe(r, "cuda_runtime").status == "not_run"
        )
        check("D2", "container still probed", probe(r, "container_gpu").passed)
        check("D2", "detail names the floor", "525.60.13" in r.detail)
    check("D3", "parse 3 parts", nv.parse_driver_version("535.104.05") == (535, 104, 5))
    check("D3", "parse 2 parts", nv.parse_driver_version("525.60") == (525, 60, 0))
    for bad in ("", "535", "535.", "a.b", "535.104.05.1", " 535.104.05", "12345.1"):
        try:
            nv.parse_driver_version(bad)
            check("D4", f"{bad!r} refused", False)
        except ValueError:
            check("D4", f"{bad!r} refused", True)
    check(
        "D5",
        "release floor is the CUDA 12 floor",
        nv.RELEASE_DRIVER_FLOOR == (525, 60, 13) == nv.CUDA_DRIVER_FLOORS[12],
    )
    check("D5", "CUDA 13 floor", nv.CUDA_DRIVER_FLOORS[13] == (580, 65, 6))
    check("D5", "no CUDA 11 floor", 11 not in nv.CUDA_DRIVER_FLOORS)


def part_v_container() -> None:
    def bare_host() -> Host:
        h = Host()
        h.existing = set()
        return h

    r = run(bare_host())
    expect_supported("C1 host with nvidia runtime", r)
    check("C1", "not in container", getattr(r, "in_container", True) is False)
    docker_calls = []
    h = bare_host()
    run(h)
    docker_calls = [c for c in h.calls if c[0][0] == "docker"]
    check(
        "C1",
        "docker info argv",
        docker_calls
        == [(("docker", "info", "--format", "{{json .Runtimes}}"), nv.DOCKER_INFO_TIMEOUT_SECONDS)],
    )
    h = bare_host()
    del h.commands["docker"]
    expect_blocked("C2 docker missing", run(h), "container_gpu", "container_runtime_not_found")
    h = bare_host()
    h.commands["docker"] = nv.CommandResult(1, "", "Cannot connect to the Docker daemon")
    expect_blocked(
        "C3 docker daemon down", run(h), "container_gpu", "container_runtime_unavailable"
    )
    h = bare_host()
    h.commands["docker"] = nv.CommandResult(None, "", "", timed_out=True)
    r = run(h)
    expect_blocked("C4 docker timeout", r, "container_gpu", "container_runtime_unavailable")
    check("C4", "timeout detail", r.detail == "docker info timed out")
    for name, stdout in (
        ("runc only", '{"runc":{}}'),
        ("not json", "runc"),
        ("list", '["nvidia"]'),
        ("empty", ""),
        ("blank", "  \n"),
        ("null", "null"),
    ):
        h = bare_host()
        h.commands["docker"] = nv.CommandResult(0, stdout, "")
        expect_blocked(f"C5 {name}", run(h), "container_gpu", "container_gpu_runtime_missing")
    h = bare_host()
    h.commands["docker"] = nv.CommandResult(0, '  {"nvidia": {}}  \n', "")
    expect_supported("C5b runtime JSON with surrounding whitespace", run(h))
    for spec_dir in ("/etc/cdi", "/var/run/cdi"):
        for fname in ("nvidia.yaml", "nvidia-container-toolkit.json", "nvidia.yml"):
            h = bare_host()
            h.commands["docker"] = nv.CommandResult(0, '{"runc":{}}', "")
            h.dirs[spec_dir] = ["other.yaml", fname]
            r = run(h)
            expect_supported(f"C6 CDI {spec_dir}/{fname}", r)
            check(
                "C6",
                "detail names the spec",
                probe(r, "container_gpu").detail == f"host; NVIDIA CDI spec {spec_dir}/{fname}",
            )
    for fname in ("other.yaml", "nvidia.txt", "amd.yaml", "my-nvidia.yaml"):
        h = bare_host()
        h.commands["docker"] = nv.CommandResult(0, '{"runc":{}}', "")
        h.dirs["/etc/cdi"] = [fname]
        expect_blocked(f"C7 CDI {fname}", run(h), "container_gpu", "container_gpu_runtime_missing")
    # container detection
    for marker in ("/.dockerenv", "/run/.containerenv"):
        h = bare_host()
        h.existing = {marker}
        h.dirs["/dev"] = ["null"]
        expect_blocked(f"C8 {marker}", run(h), "container_gpu", "container_gpu_not_exposed")
    for cgroup in (
        "12:devices:/docker/abc",
        "0::/kubepods/besteffort/pod1",
        "0::/system.slice/containerd.service",
        "0::/machine.slice/libpod-1",
        "0::/lxc/x",
    ):
        h = bare_host()
        h.files["/proc/1/cgroup"] = cgroup
        h.dirs["/dev"] = ["null"]
        expect_blocked(f"C9 {cgroup}", run(h), "container_gpu", "container_gpu_not_exposed")
        h.dirs["/dev"] = ["nvidiactl", "nvidia0"]
        r = run(h)
        expect_supported(f"C9b {cgroup}", r)
        check("C9b", "in container", getattr(r, "in_container", False) is True)
    h = bare_host()
    del h.files["/proc/1/cgroup"]
    expect_supported("C10 no cgroup file -> host path", run(h))
    for nodes in (
        ["nvidiactl"],
        ["nvidia0"],
        ["nvidiactl", "nvidia-uvm"],
        ["nvidiactl", "nvidia-modeset"],
        ["nvidiactl", "nvidiaX"],
        ["nvidiactl", "xnvidia0"],
    ):
        h = Host()
        h.dirs["/dev"] = nodes
        expect_blocked(f"C11 {nodes}", run(h), "container_gpu", "container_gpu_not_exposed")
    h = Host()
    h.dirs["/dev"] = ["nvidiactl", "nvidia3"]
    expect_supported("C12 nvidia3", run(h))


def part_v_cuda() -> None:
    def with_torch(
        stdout: str, rc: int | None = 0, timed_out: bool = False, stderr: str = ""
    ) -> Host:
        h = Host()
        h.commands[sys.executable] = nv.CommandResult(rc, stdout, stderr, timed_out=timed_out)
        return h

    r = run(
        with_torch(json.dumps({"torch_importable": False, "import_error": "ModuleNotFoundError"}))
    )
    expect_blocked("T1 torch missing", r, "cuda_runtime", "torch_unavailable")
    check(
        "T1",
        "bf16 and memory not run",
        probe(r, "bf16").status == "not_run" and probe(r, "gpu_memory").status == "not_run",
    )
    expect_blocked(
        "T2 cpu build",
        run(with_torch(torch_report(cuda_version=None, cuda_available=False, devices=[]))),
        "cuda_runtime",
        "torch_cpu_build",
    )
    expect_blocked(
        "T2b cpu build claiming devices",
        run(with_torch(torch_report(cuda_version=None))),
        "cuda_runtime",
        "torch_cpu_build",
    )
    expect_blocked(
        "T3 cuda unavailable",
        run(with_torch(torch_report(cuda_available=False))),
        "cuda_runtime",
        "torch_cuda_unavailable",
    )
    expect_blocked(
        "T4 no devices",
        run(with_torch(torch_report(devices=[]))),
        "cuda_runtime",
        "torch_cuda_unavailable",
    )
    r = run(with_torch("", rc=1, stderr="Segmentation fault"))
    expect_blocked("T5 probe crashed", r, "cuda_runtime", "cuda_probe_failed")
    check("T5", "detail", r.detail == "exit 1: Segmentation fault")
    expect_blocked(
        "T5b probe not found", run(with_torch("", rc=None)), "cuda_runtime", "cuda_probe_failed"
    )
    expect_blocked(
        "T6 timeout",
        run(with_torch("", rc=None, timed_out=True)),
        "cuda_runtime",
        "cuda_probe_timeout",
    )
    for name, stdout in (
        ("two lines", torch_report() + torch_report()),
        ("not json", "hello\n"),
        ("array", "[1]\n"),
        ("empty", ""),
        ("importable missing", json.dumps({"torch_version": "2"})),
        ("importable int", json.dumps({"torch_importable": 1})),
        ("version missing", torch_report(torch_version="")),
        ("version int", torch_report(torch_version=2)),
        ("cuda int", torch_report(cuda_version=12)),
        ("devices dict", torch_report(devices={})),
        ("device str", torch_report(devices=["x"])),
        (
            "capability 3",
            torch_report(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [8, 0, 0],
                        "total_bytes": 1,
                        "free_bytes": 1,
                    }
                ]
            ),
        ),
        (
            "capability str",
            torch_report(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": "8.0",
                        "total_bytes": 1,
                        "free_bytes": 1,
                    }
                ]
            ),
        ),
        (
            "capability bool",
            torch_report(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [True, 0],
                        "total_bytes": 1,
                        "free_bytes": 1,
                    }
                ]
            ),
        ),
        (
            "name int",
            torch_report(
                devices=[
                    {"index": 0, "name": 1, "capability": [8, 0], "total_bytes": 1, "free_bytes": 1}
                ]
            ),
        ),
        (
            "total float",
            torch_report(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [8, 0],
                        "total_bytes": 1.0,
                        "free_bytes": 1,
                    }
                ]
            ),
        ),
        (
            "free negative",
            torch_report(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [8, 0],
                        "total_bytes": 1,
                        "free_bytes": -1,
                    }
                ]
            ),
        ),
        (
            "free > total",
            torch_report(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [8, 0],
                        "total_bytes": 1,
                        "free_bytes": 2,
                    }
                ]
            ),
        ),
        (
            "index missing",
            torch_report(
                devices=[{"name": "x", "capability": [8, 0], "total_bytes": 1, "free_bytes": 1}]
            ),
        ),
        ("cuda_available str", torch_report(cuda_available="true")),
        (
            "cuda_available missing",
            json.dumps(
                {k: v for k, v in json.loads(torch_report()).items() if k != "cuda_available"}
            ),
        ),
        ("bf16_native str", torch_report(bf16_native="yes")),
        ("bf16_matmul int", torch_report(bf16_matmul_ok=1)),
    ):
        expect_blocked(
            f"T7 {name}", run(with_torch(stdout)), "cuda_runtime", "cuda_probe_unparseable"
        )
    for cuda in ("11.8", "10.2", "14.0", "9.0"):
        expect_blocked(
            f"T8 CUDA {cuda}",
            run(with_torch(torch_report(cuda_version=cuda))),
            "cuda_runtime",
            "cuda_version_unsupported",
        )
    for cuda in ("12", "12.x", "cuda12.1", " 12.1", "12.1.2.3"):
        expect_blocked(
            f"T9 CUDA {cuda!r}",
            run(with_torch(torch_report(cuda_version=cuda))),
            "cuda_runtime",
            "cuda_version_unsupported",
        )
    for cuda in ("12.1", "12.4.1", "12.8"):
        expect_supported(f"T10 CUDA {cuda}", run(with_torch(torch_report(cuda_version=cuda))))
    # CUDA 13 needs a newer driver than the release floor
    h = with_torch(torch_report(cuda_version="13.0"))
    r = run(h)
    expect_blocked("T11 CUDA 13 on 535", r, "cuda_runtime", "cuda_driver_mismatch")
    check("T11", "detail", r.detail == "CUDA 13.0 needs driver >= 580.65.6, found 535.104.05")
    for driver, ok in (
        ("580.65.06", True),
        ("580.65.05", False),
        ("580.82.07", True),
        ("575.57.08", False),
    ):
        h = with_torch(torch_report(cuda_version="13.0"))
        h.commands["nvidia-smi"] = nv.CommandResult(0, SMI_A100.replace("535.104.05", driver), "")
        r = run(h)
        if ok:
            expect_supported(f"T12 CUDA 13 on {driver}", r)
        else:
            expect_blocked(f"T12 CUDA 13 on {driver}", r, "cuda_runtime", "cuda_driver_mismatch")
    check(
        "T13",
        "parse torch not importable",
        nv.parse_torch_report('{"torch_importable": false}').importable is False,
    )
    rep = nv.parse_torch_report(torch_report())
    check(
        "T13",
        "parse devices",
        rep.devices == (nv.TorchDevice(0, "NVIDIA A100-SXM4-80GB", (8, 0), 80 * GIB, 79 * GIB),),
    )
    check(
        "T13",
        "parse flags",
        (
            rep.cuda_available,
            rep.bf16_native,
            rep.bf16_matmul_ok,
            rep.cuda_version,
            rep.torch_version,
        )
        == (True, True, True, "12.4", "2.5.1+cu124"),
    )
    rep = nv.parse_torch_report(torch_report(bf16_native=None, bf16_matmul_ok=None))
    check(
        "T13",
        "null bf16 flags parse as None",
        rep.bf16_native is None and rep.bf16_matmul_ok is None,
    )
    rep = nv.parse_torch_report("\n  \n" + torch_report() + "\n\t\n")
    check("T13", "blank lines around one JSON line are fine", rep.importable)


def part_v_bf16() -> None:
    def with_(**overrides: Any) -> Host:
        h = Host()
        h.commands[sys.executable] = nv.CommandResult(0, torch_report(**overrides), "")
        return h

    turing = [
        {
            "index": 0,
            "name": "T4",
            "capability": [7, 5],
            "total_bytes": 32 * GIB,
            "free_bytes": 32 * GIB,
        }
    ]
    for name, h in (
        ("capability 7.5", with_(devices=turing)),
        ("native false", with_(bf16_native=False, bf16_matmul_ok=None)),
        ("native null", with_(bf16_native=None, bf16_matmul_ok=None)),
        ("matmul false", with_(bf16_matmul_ok=False)),
        ("matmul null", with_(bf16_matmul_ok=None)),
    ):
        r = run(h)
        expect_blocked(f"B1 {name}", r, "bf16", "bf16_unsupported")
        check("B1", "memory still probed", probe(r, "gpu_memory").passed)
        r = run(h, require_bf16=False)
        expect_supported(f"B2 {name} not required", r)
        check("B2", "profile says bf16 unsupported", getattr(r, "bf16_supported", True) is False)
        check("B2", "detail not required", probe(r, "bf16").detail == "not required")
    r = run(Host(), require_bf16=False)
    check(
        "B3",
        "bf16 reported supported even when not required",
        getattr(r, "bf16_supported", False) is True,
    )
    check("B3", "detail supported", probe(r, "bf16").detail == "supported")
    expect_supported(
        "B4 capability 8.0 exact",
        run(
            with_(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [8, 0],
                        "total_bytes": 80 * GIB,
                        "free_bytes": 80 * GIB,
                    }
                ]
            )
        ),
    )
    expect_supported(
        "B4 capability 9.0",
        run(
            with_(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [9, 0],
                        "total_bytes": 80 * GIB,
                        "free_bytes": 80 * GIB,
                    }
                ]
            )
        ),
    )
    expect_blocked(
        "B5 capability 7.9",
        run(
            with_(
                devices=[
                    {
                        "index": 0,
                        "name": "x",
                        "capability": [7, 9],
                        "total_bytes": 80 * GIB,
                        "free_bytes": 80 * GIB,
                    }
                ]
            )
        ),
        "bf16",
        "bf16_unsupported",
    )
    mixed = [
        turing[0],
        {
            "index": 1,
            "name": "A100",
            "capability": [8, 0],
            "total_bytes": 80 * GIB,
            "free_bytes": 80 * GIB,
        },
    ]
    expect_supported("B6 one capable device", run(with_(devices=mixed)))


def part_v_memory() -> None:
    def with_devices(devices: list[dict[str, Any]]) -> Host:
        h = Host()
        h.commands[sys.executable] = nv.CommandResult(0, torch_report(devices=devices), "")
        return h

    def dev(index: int, total: int, free: int, cap=(8, 0)) -> dict[str, Any]:
        return {
            "index": index,
            "name": f"gpu{index}",
            "capability": list(cap),
            "total_bytes": total,
            "free_bytes": free,
        }

    expect_supported("M1 exactly 24 GiB", run(with_devices([dev(0, 24 * GIB, 24 * GIB)])))
    r = run(with_devices([dev(0, 24 * GIB - 1, 24 * GIB - 1)]))
    expect_blocked("M2 one byte short", r, "gpu_memory", "gpu_memory_insufficient")
    check("M2", "detail", r.detail == "no visible device has 24 GiB total memory")
    r = run(with_devices([dev(0, 80 * GIB, 24 * GIB - 1)]))
    expect_blocked("M3 free one byte short", r, "gpu_memory", "gpu_memory_in_use")
    check("M3", "detail", r.detail == "no visible device has 24 GiB free")
    r = run(
        with_devices(
            [
                dev(0, 16 * GIB, 16 * GIB),
                dev(1, 48 * GIB, 40 * GIB),
                dev(2, 48 * GIB, 1 * GIB),
                dev(3, 80 * GIB, 80 * GIB),
            ]
        )
    )
    expect_supported("M4 mixed devices", r)
    check("M4", "qualified indices", getattr(r, "qualified_device_indices", None) == (1, 3))
    expect_blocked(
        "M5 80 GiB need", run(Host(), min_gpu_vram_gb=81), "gpu_memory", "gpu_memory_insufficient"
    )
    expect_supported(
        "M6 80 GiB need on 80 GiB card with 80 free",
        run(with_devices([dev(0, 80 * GIB, 80 * GIB)]), min_gpu_vram_gb=80),
    )
    expect_blocked(
        "M7 big but in use + small free",
        run(with_devices([dev(0, 80 * GIB, 1 * GIB), dev(1, 8 * GIB, 8 * GIB)])),
        "gpu_memory",
        "gpu_memory_in_use",
    )


def part_v_storage() -> None:
    h = Host()
    h.work_dirs = set()
    expect_blocked("S1 missing", run(h), "storage", "storage_path_missing")
    h = Host()
    h.write_error = PermissionError(13, "denied")
    r = run(h)
    expect_blocked("S2 read-only", r, "storage", "storage_not_writable")
    check("S2", "detail", r.detail == "PermissionError")
    h = Host()
    h.free_error = OSError(5, "io")
    expect_blocked("S2b disk_usage fails", run(h), "storage", "storage_not_writable")
    h = Host()
    h.free = 100 * GIB
    r = run(h)
    expect_supported("S3 exactly enough", r)
    check("S3", "free recorded", getattr(r, "storage_free_bytes", 0) == 100 * GIB)
    h = Host()
    h.free = 100 * GIB - 1
    r = run(h)
    expect_blocked("S4 one byte short", r, "storage", "storage_insufficient")
    check("S4", "detail", r.detail == f"{100 * GIB - 1} bytes free, {100 * GIB} required")
    check("S4", "object store still probed", probe(r, "object_store").passed)


def part_v_object_store() -> None:
    expect_blocked(
        "O1 unconfigured", run(object_store=None), "object_store", "object_store_unconfigured"
    )
    for code in (
        "object_store_denied",
        "object_store_bucket_missing",
        "object_store_unreachable",
        "object_store_readback_mismatch",
        "object_store_error",
        "object_store_delete_unverified",
    ):
        r = run(object_store=Store(nv.ObjectStoreFailure(code, "why")))
        expect_blocked(f"O2 {code}", r, "object_store", code)
        check("O2", "detail carried", r.detail == "why")
        check("O2", "not gpu absence", not nv.blocked_by_gpu_absence(r))
    s1, s2 = Store(), Store()
    run(object_store=s1)
    run(object_store=s2)
    check(
        "O3",
        "unique probe key per run",
        s1.calls[0][0] != s2.calls[0][0] and s1.calls[0][1] != s2.calls[0][1],
    )


def part_v_order() -> None:
    h = Host()
    h.work_dirs = set()
    r = run(h, object_store=None)
    expect_blocked("R1 storage before object store", r, "storage", "storage_path_missing")
    check("R1", "both listed", r.failed_probes == ("storage", "object_store"))
    h = Host()
    h.system_name = "Darwin"
    del h.commands["nvidia-smi"]
    r = run(h, object_store=None)
    expect_blocked("R2 host first", r, "host_os", "host_not_linux")
    check(
        "R2", "every failure listed", r.failed_probes == ("host_os", "nvidia_smi", "object_store")
    )
    check("R2", "not gpu absence when host is first", not nv.blocked_by_gpu_absence(r))
    d = r.to_dict()
    check(
        "R3",
        "blocked to_dict",
        d["status"] == "BLOCKED"
        and d["failed_probe"] == "host_os"
        and d["failed_probes"] == ["host_os", "nvidia_smi", "object_store"]
        and len(d["probes"]) == 9,
    )
    check(
        "R3",
        "blocked to_dict keys",
        set(d) == {f.name for f in dataclasses.fields(nv.BlockedEnvironment)},
    )
    check(
        "R4",
        "probe order constant",
        nv.PROBE_ORDER
        == (
            "host_os",
            "nvidia_smi",
            "driver",
            "container_gpu",
            "cuda_runtime",
            "bf16",
            "gpu_memory",
            "storage",
            "object_store",
        ),
    )
    check("R5", "supported result is not gpu absence", not nv.blocked_by_gpu_absence(run()))
    # no default-to-supported: probes that report "passed" without their facts cannot build a profile
    for name, fake in (
        ("_probe_memory", lambda req, st: nv.ProbeOutcome("gpu_memory", "passed", "ok", "")),
        ("_probe_object_store", lambda req: nv.ProbeOutcome("object_store", "passed", "ok", "")),
    ):
        real_fn = getattr(nv, name)
        setattr(nv, name, fake)
        try:
            try:
                run(object_store=None) if name == "_probe_object_store" else run()
                check("R6", f"{name}: a passed probe without facts raises", False)
            except AssertionError as exc:
                check(
                    "R6",
                    f"{name}: a passed probe without facts raises",
                    str(exc) == "every probe passed without its facts",
                )
        finally:
            setattr(nv, name, real_fn)
    # a probe status that is not exactly "passed" is never a pass
    real_storage = nv._probe_storage
    for status in ("PASSED", "passed ", "Passed", "FAILED", "failed ", "NOT_RUN", " not_run"):
        nv._probe_storage = lambda host, req, st, status=status: nv.ProbeOutcome(
            "storage", status, "ok", ""
        )
        try:
            r = run()
        finally:
            nv._probe_storage = real_storage
        expect_blocked_status = isinstance(r, nv.BlockedEnvironment) and r.failed_probe == "storage"
        check("R7", f"status {status!r} blocks", expect_blocked_status)
        check(
            "R7",
            f"status {status!r} listed as failed",
            getattr(r, "failed_probes", None) == ("storage",),
        )
    check(
        "R8",
        "ProbeOutcome.passed is exact",
        not nv.ProbeOutcome("x", "PASSED", "ok").passed
        and not nv.ProbeOutcome("x", " passed", "ok").passed,
    )
    for code in ("GPU_ABSENT", " gpu_absent", "Nvidia_Smi_Not_Found", "driver_not_loaded "):
        fake = nv.BlockedEnvironment("BLOCKED", "nvidia_smi", code, "", ("nvidia_smi",), ())
        check("R9", f"{code!r} is not gpu absence", not nv.blocked_by_gpu_absence(fake))
    for code in ("gpu_absent", "nvidia_smi_not_found", "driver_not_loaded"):
        fake = nv.BlockedEnvironment("BLOCKED", "nvidia_smi", code, "", ("nvidia_smi",), ())
        check("R9", f"{code} is gpu absence", nv.blocked_by_gpu_absence(fake))
    for code in (
        "nvidia_smi_failed",
        "nvidia_smi_timeout",
        "nvidia_smi_unparseable",
        "driver_incompatible",
    ):
        fake = nv.BlockedEnvironment("BLOCKED", "nvidia_smi", code, "", ("nvidia_smi",), ())
        check(
            "R9", f"{code} is a real failure, not gpu absence", not nv.blocked_by_gpu_absence(fake)
        )


def part_v_requirements() -> None:
    for name, overrides in (
        ("vram 0", {"min_gpu_vram_gb": 0}),
        ("vram 4097", {"min_gpu_vram_gb": 4097}),
        ("vram bool", {"min_gpu_vram_gb": True}),
        ("vram str", {"min_gpu_vram_gb": "24"}),
        ("vram float", {"min_gpu_vram_gb": 24.0}),
        ("bf16 int", {"require_bf16": 1}),
        ("dev host int", {"allow_development_host": 1}),
        ("storage 0", {"min_free_storage_bytes": 0}),
        ("storage bool", {"min_free_storage_bytes": True}),
        ("path empty", {"storage_path": ""}),
        ("path blank", {"storage_path": "  "}),
        ("path relative", {"storage_path": "work"}),
        ("path dot", {"storage_path": "./work"}),
        ("path bytes", {"storage_path": b"/work"}),
    ):
        try:
            req(**overrides)
            check("Q1", f"{name} refused", False)
        except ValueError:
            check("Q1", f"{name} refused", True)
    for vram in (1, 4096):
        check("Q2", f"vram {vram} accepted", req(min_gpu_vram_gb=vram).min_gpu_vram_gb == vram)
    check("Q2", "storage 1 accepted", req(min_free_storage_bytes=1).min_free_storage_bytes == 1)
    check("Q3", "development host off by default", req().allow_development_host is False)
    check(
        "Q4",
        "ProbeOutcome.passed",
        nv.ProbeOutcome("x", "passed", "ok").passed
        and not nv.ProbeOutcome("x", "failed", "c").passed
        and not nv.ProbeOutcome("x", "not_run", "c").passed,
    )
    check(
        "Q5",
        "ObjectStoreFailure fields",
        nv.ObjectStoreFailure("c", "d").code == "c"
        and nv.ObjectStoreFailure("c", "d").detail == "d"
        and str(nv.ObjectStoreFailure("c")) == "c",
    )
    check(
        "Q6",
        "default host is real",
        isinstance(
            nv.validate_nvidia_environment(
                req(object_store=None, storage_path=tempfile.gettempdir(), min_free_storage_bytes=1)
            ),
            nv.BlockedEnvironment | nv.SupportedNvidiaProfile,
        ),
    )


# --------------------------------------------------------------------------------------------
# P: the PyTorch probe script against a stand-in torch module
# --------------------------------------------------------------------------------------------


def _fake_torch(
    *,
    cuda: str | None = "12.4",
    available: bool = True,
    caps=((8, 0),),
    native=True,
    emulation_kw=True,
    matmul_value=512.0,
    matmul_raises=False,
):
    torch = types.ModuleType("torch")
    torch.__version__ = "2.5.1"
    torch.version = types.SimpleNamespace(cuda=cuda)
    torch.bfloat16 = "bf16"
    calls: list[Any] = []

    def is_bf16_supported(*args, **kwargs):
        calls.append(kwargs)
        if not emulation_kw and kwargs:
            raise TypeError("unexpected keyword")
        return native

    class Tensor:
        def __matmul__(self, other):
            if matmul_raises:
                raise RuntimeError("no kernel")
            return self

        def float(self):
            return self

        def sum(self):
            return self

        def item(self):
            return matmul_value

    torch.ones = lambda shape, dtype=None, device=None: Tensor()
    torch.cuda = types.SimpleNamespace(
        is_available=lambda: available,
        device_count=lambda: len(caps),
        get_device_capability=lambda i: caps[i],
        mem_get_info=lambda i: (10 * GIB, 40 * GIB),
        get_device_name=lambda i: f"GPU{i}",
        is_bf16_supported=is_bf16_supported,
        synchronize=lambda i: None,
    )
    return torch, calls


def _exec_probe(torch_module) -> dict[str, Any]:
    saved = sys.modules.get("torch")
    if torch_module is None:
        sys.modules["torch"] = None  # import raises ImportError
    else:
        sys.modules["torch"] = torch_module
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                exec(
                    compile(nv.TORCH_PROBE_SCRIPT, "<torch-probe>", "exec"),
                    {"__name__": "__main__"},
                )
            except SystemExit:
                pass
    finally:
        if saved is None:
            sys.modules.pop("torch", None)
        else:
            sys.modules["torch"] = saved
    return json.loads(buf.getvalue())


def part_p_script() -> None:
    out = _exec_probe(None)
    check(
        "P1",
        "import failure reported",
        out == {"torch_importable": False, "import_error": "ModuleNotFoundError"},
    )
    torch, calls = _fake_torch()
    out = _exec_probe(torch)
    check(
        "P2",
        "good report",
        out["torch_importable"]
        and out["cuda_version"] == "12.4"
        and out["cuda_available"]
        and out["bf16_native"] is True
        and out["bf16_matmul_ok"] is True,
    )
    check(
        "P2",
        "device report",
        out["devices"]
        == [
            {
                "index": 0,
                "name": "GPU0",
                "capability": [8, 0],
                "total_bytes": 40 * GIB,
                "free_bytes": 10 * GIB,
            }
        ],
    )
    check("P2", "emulation excluded", calls == [{"including_emulation": False}])
    check("P2", "parses", nv.parse_torch_report(json.dumps(out)).bf16_matmul_ok is True)
    torch, calls = _fake_torch(emulation_kw=False)
    out = _exec_probe(torch)
    check(
        "P3",
        "old torch fallback",
        out["bf16_native"] is True and calls == [{"including_emulation": False}, {}],
    )
    out = _exec_probe(_fake_torch(available=False)[0])
    check(
        "P4",
        "no cuda -> no devices, no bf16",
        out["devices"] == [] and out["bf16_native"] is None and out["bf16_matmul_ok"] is None,
    )
    out = _exec_probe(_fake_torch(native=False)[0])
    check(
        "P5",
        "native false skips matmul",
        out["bf16_native"] is False and out["bf16_matmul_ok"] is None,
    )
    out = _exec_probe(_fake_torch(matmul_value=511.0)[0])
    check("P6", "wrong matmul value", out["bf16_matmul_ok"] is False)
    out = _exec_probe(_fake_torch(matmul_raises=True)[0])
    check(
        "P7", "matmul error", out["bf16_matmul_ok"] is False and out["bf16_error"] == "RuntimeError"
    )
    out = _exec_probe(_fake_torch(cuda=None, available=False)[0])
    check("P8", "cpu build reports null cuda", out["cuda_version"] is None)
    out = _exec_probe(_fake_torch(caps=((8, 0), (9, 0)))[0])
    check("P9", "two devices", [d["capability"] for d in out["devices"]] == [[8, 0], [9, 0]])
    check("P10", "script never imported torch into this process", "torch" not in sys.modules)


# --------------------------------------------------------------------------------------------
# S: S3 probe adapter
# --------------------------------------------------------------------------------------------


class FakeS3:
    """Stand-in for `S3ProbeClient.request` (test-only)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail: dict[str, BaseException] = {}
        self.corrupt: Any = None
        self.keep_after_delete = False
        self.log: list[tuple[str, str, str]] = []

    def request(self, method, bucket, key, body=b""):
        self.log.append((method, bucket, key))
        if method in self.fail:
            raise self.fail[method]
        if method == "PUT":
            self.objects[(bucket, key)] = body
            return b""
        if method == "GET":
            stored = self.objects[(bucket, key)]
            return self.corrupt(stored) if self.corrupt else stored
        if method == "DELETE":
            if not self.keep_after_delete:
                self.objects.pop((bucket, key), None)
            return b""
        if (bucket, key) not in self.objects:
            raise osp.S3ProbeError(404, "Unknown")
        return b""


def _s3_round_trip(fake: FakeS3, body: bytes = b"body") -> str:
    probe_obj = osp.S3RoundTripProbe(client=fake, bucket="b")
    try:
        probe_obj.round_trip("k", body)
    except nv.ObjectStoreFailure as exc:
        return exc.code
    return "ok"


def _detail(fake: FakeS3) -> str:
    try:
        osp.S3RoundTripProbe(client=fake, bucket="b").round_trip("k", b"x")
    except nv.ObjectStoreFailure as exc:
        return exc.detail
    return "<none>"


def part_s_probe() -> None:
    fake = FakeS3()
    check("S3-1", "round trip ok", _s3_round_trip(fake) == "ok")
    check(
        "S3-1",
        "PUT/GET/DELETE/HEAD in order on the bucket and key",
        fake.log
        == [("PUT", "b", "k"), ("GET", "b", "k"), ("DELETE", "b", "k"), ("HEAD", "b", "k")],
    )
    check("S3-1", "nothing left behind", fake.objects == {})
    E = osp.S3ProbeError
    cases = [
        ("put 403", "PUT", E(403, "AccessDenied"), "object_store_denied"),
        ("put 401", "PUT", E(401, "Unauthorized"), "object_store_denied"),
        ("put 402", "PUT", E(402, "PaymentRequired"), "object_store_error"),
        ("put 404 bucket", "PUT", E(404, "NoSuchBucket"), "object_store_bucket_missing"),
        ("put 404 other", "PUT", E(404, "NoSuchKey"), "object_store_error"),
        ("put 403 NoSuchBucket code", "PUT", E(403, "NoSuchBucket"), "object_store_denied"),
        ("put 400 NoSuchBucket code", "PUT", E(400, "NoSuchBucket"), "object_store_error"),
        ("put 404 nosuchbucket lower", "PUT", E(404, "nosuchbucket"), "object_store_error"),
        ("put 500", "PUT", E(500, "InternalError"), "object_store_error"),
        (
            "put refused",
            "PUT",
            urllib.error.URLError(ConnectionRefusedError(111, "refused")),
            "object_store_unreachable",
        ),
        ("put reset", "PUT", ConnectionResetError(104, "reset"), "object_store_unreachable"),
        ("put timeout", "PUT", TimeoutError("t"), "object_store_unreachable"),
        ("put bad status line", "PUT", http.client.BadStatusLine("x"), "object_store_unreachable"),
        ("get 403", "GET", E(403, "AccessDenied"), "object_store_denied"),
        ("get 404", "GET", E(404, "NoSuchKey"), "object_store_error"),
        ("get refused", "GET", OSError(111, "refused"), "object_store_unreachable"),
        ("delete 403", "DELETE", E(403, "AccessDenied"), "object_store_denied"),
        ("delete refused", "DELETE", OSError(111, "refused"), "object_store_unreachable"),
        ("head 403", "HEAD", E(403, "AccessDenied"), "object_store_denied"),
        ("head 500", "HEAD", E(500, "InternalError"), "object_store_error"),
        ("head 404 NoSuchBucket is gone", "HEAD", E(404, "NoSuchBucket"), "ok"),
        ("head refused", "HEAD", urllib.error.URLError("refused"), "object_store_unreachable"),
        ("head timeout", "HEAD", TimeoutError("t"), "object_store_unreachable"),
    ]
    for name, op, exc, code in cases:
        fake = FakeS3()
        fake.fail[op] = exc
        check("S3-2", f"{name} -> {code}", _s3_round_trip(fake) == code)
    for name, corrupt in (
        ("extra byte", lambda b: b + b"x"),
        ("trailing space", lambda b: b + b" "),
        ("case", lambda b: b.upper()),
        ("empty", lambda b: b""),
    ):
        fake = FakeS3()
        fake.corrupt = corrupt
        check(
            "S3-3",
            f"readback mismatch ({name})",
            _s3_round_trip(fake, b"Body") == "object_store_readback_mismatch",
        )
        check(
            "S3-3",
            "mismatch stops before delete",
            [entry[0] for entry in fake.log] == ["PUT", "GET"],
        )
    for body, returned in ((b"body", b" BODY\n"), (b" Body\t", b"body")):
        fake = FakeS3()
        fake.corrupt = lambda _b, returned=returned: returned
        check(
            "S3-3",
            f"readback {returned!r} for {body!r} is a mismatch",
            _s3_round_trip(fake, body) == "object_store_readback_mismatch",
        )
    fake = FakeS3()
    fake.keep_after_delete = True
    check("S3-4", "delete unverified", _s3_round_trip(fake) == "object_store_delete_unverified")
    for op, exc, detail in (
        ("PUT", E(403, "AccessDenied"), "403 AccessDenied"),
        ("PUT", E(404, "NoSuchBucket"), "404 NoSuchBucket"),
        ("PUT", E(500, "InternalError"), "500 InternalError"),
        ("PUT", ConnectionResetError(104, "reset"), "ConnectionResetError"),
        ("HEAD", OSError(5, "x"), "OSError"),
        ("HEAD", E(403, "AccessDenied"), "403 AccessDenied"),
    ):
        fake = FakeS3()
        fake.fail[op] = exc
        check("S3-5", f"detail {detail}", _detail(fake) == detail)
    err = osp.S3ProbeError(403, "AccessDenied")
    check(
        "S3-6",
        "S3ProbeError fields",
        (err.status_code, err.code, str(err)) == (403, "AccessDenied", "403 AccessDenied"),
    )


class _StubS3(http.server.BaseHTTPRequestHandler):
    """A local HTTP server answering like S3 (test-only), to exercise the real urllib path."""

    responses: dict[str, tuple[int, bytes]] = {}
    seen: list[tuple[str, str, dict[str, str], bytes]] = []

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        _StubS3.seen.append(
            (self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body)
        )
        status, payload = _StubS3.responses.get(self.command, (200, b""))
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_PUT = do_GET = do_DELETE = do_HEAD = _answer

    def log_message(self, *args: Any) -> None:
        return None


def part_s_client() -> None:
    # Signature parity with the certified FTF-R017 signer, for every method the probe sends.
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.services.storage_semantics_client import S3Credentials, S3Endpoint, sign_request

    when = datetime(2026, 11, 28, 4, 5, 6, tzinfo=UTC)
    for scheme, host, port, region in (
        ("http", "localhost", 8333, "us-east-1"),
        ("https", "s3.example", 443, "eu-west-1"),
        ("http", "10.0.0.5", 80, "us-east-1"),
        ("https", "s3.example", 8443, "me-central-1"),
    ):
        client = osp.S3ProbeClient(
            scheme, host, port, region, "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
        )
        for method, key, body in (
            ("PUT", "sovra-env-probe/abc", b"hello"),
            ("GET", "sovra-env-probe/abc", b""),
            ("DELETE", "a b/c+d", b""),
            ("HEAD", "k", b""),
        ):
            url, headers = client.signed_headers(method, f"/bkt/{key}", body, when)
            ref = sign_request(
                method=method,
                endpoint=S3Endpoint(scheme, host, port, region),
                credentials=S3Credentials(
                    "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
                ),
                canonical_uri=f"/bkt/{key}",
                body=body,
                now=when,
            )
            check(
                "SIG1",
                f"{scheme}:{port} {method} {key} headers equal the certified signer",
                headers == ref.headers,
            )
            check(
                "SIG1",
                f"{scheme}:{port} {method} url",
                url == f"{scheme}://{host}:{port}/bkt/{key}",
            )
    client = osp.S3ProbeClient("http", "h", 1, "r", "AK", "SK")
    _, a = client.signed_headers("PUT", "/b/k", b"x", when)
    _, b = osp.S3ProbeClient("http", "h", 1, "r", "AK", "SK2").signed_headers(
        "PUT", "/b/k", b"x", when
    )
    check("SIG2", "secret changes the signature", a["authorization"] != b["authorization"])
    check("SIG2", "secret never in headers", all("SK" not in v for v in a.values()))

    # The real urllib path against a local stub server.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubS3)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        client = osp.S3ProbeClient(
            "http", "127.0.0.1", port, "us-east-1", "AK", "SK", timeout_seconds=5.0
        )
        _StubS3.seen.clear()
        _StubS3.responses = {"GET": (200, b"payload")}
        check(
            "HTTP1",
            "PUT returns body bytes",
            client.request("PUT", "bkt", "k1", b"data", clock=lambda: when) == b"",
        )
        check("HTTP1", "GET returns the body", client.request("GET", "bkt", "k1") == b"payload")
        method, path, headers, sent = _StubS3.seen[0]
        check(
            "HTTP1", "PUT sent path and body", (method, path, sent) == ("PUT", "/bkt/k1", b"data")
        )
        check(
            "HTTP1",
            "PUT signed with the clock",
            headers.get("x-amz-date") == "20261128T040506Z"
            and headers.get("authorization", "").startswith(
                "AWS4-HMAC-SHA256 Credential=AK/20261128/us-east-1/s3/aws4_request"
            ),
        )
        check(
            "HTTP1",
            "GET sends no body",
            _StubS3.seen[1][3] == b"" and "content-length" not in _StubS3.seen[1][2],
        )
        check(
            "HTTP1",
            "default clock is now",
            _StubS3.seen[1][2].get("x-amz-date", "")[:8] == datetime.now(UTC).strftime("%Y%m%d"),
        )
        for xml, code in (
            (
                b"<?xml version='1.0'?><Error><Code>AccessDenied</Code><Message>no</Message></Error>",
                "AccessDenied",
            ),
            (
                b"<Error xmlns='http://s3.amazonaws.com/doc/2006-03-01/'><Code> NoSuchBucket </Code></Error>",
                "NoSuchBucket",
            ),
            (b"<Error><Message>m</Message></Error>", "Unknown"),
            (b"<Error><Code></Code></Error>", "Unknown"),
            (b"not xml", "Unknown"),
            (b"", "Unknown"),
            (b"<Error><Code>" + b"C" * 100 + b"</Code></Error>", "C" * 64),
            (b"<Error><Code>First</Code><Code>Second</Code></Error>", "First"),
        ):
            _StubS3.responses = {"PUT": (403, xml)}
            try:
                client.request("PUT", "bkt", "k", b"x")
                check("HTTP2", f"{xml[:30]!r} raised", False)
            except osp.S3ProbeError as exc:
                check(
                    "HTTP2", f"{xml[:30]!r} -> {code}", (exc.status_code, exc.code) == (403, code)
                )
        _StubS3.responses = {"HEAD": (404, b"")}
        try:
            client.request("HEAD", "bkt", "k")
            check("HTTP3", "HEAD 404 raises", False)
        except osp.S3ProbeError as exc:
            check(
                "HTTP3", "HEAD 404 raises Unknown", (exc.status_code, exc.code) == (404, "Unknown")
            )
        _StubS3.responses = {
            "PUT": (200, b""),
            "GET": (200, b"different"),
            "DELETE": (204, b""),
            "HEAD": (404, b""),
        }
        check(
            "HTTP4",
            "real readback mismatch over HTTP",
            _s3_round_trip(client, b"orig") == "object_store_readback_mismatch",
        )
        _StubS3.responses = {
            "PUT": (200, b""),
            "GET": (200, b"orig"),
            "DELETE": (204, b""),
            "HEAD": (404, b""),
        }
        check("HTTP4", "real round trip over HTTP", _s3_round_trip(client, b"orig") == "ok")
        _StubS3.responses = {
            "PUT": (200, b""),
            "GET": (200, b"orig"),
            "DELETE": (204, b""),
            "HEAD": (200, b""),
        }
        check(
            "HTTP4",
            "real HEAD 200 after delete",
            _s3_round_trip(client, b"orig") == "object_store_delete_unverified",
        )
    finally:
        server.shutdown()
        server.server_close()
    dead = osp.S3ProbeClient(
        "http", "127.0.0.1", port, "us-east-1", "AK", "SK", timeout_seconds=2.0
    )
    check("HTTP5", "closed port -> unreachable", _s3_round_trip(dead) == "object_store_unreachable")


GOOD_ENV = {
    "SOVRA_STORAGE_S3_SCHEME": "http",
    "SOVRA_STORAGE_S3_HOST": "s3.internal",
    "SOVRA_STORAGE_S3_PORT": "8333",
    "SOVRA_STORAGE_S3_REGION": "us-east-1",
    "SOVRA_STORAGE_S3_ACCESS_KEY": "AK",
    "SOVRA_STORAGE_S3_SECRET_KEY": "SK",
    "SOVRA_TRAINING_OBJECT_STORE_BUCKET": "sovra-training",
}


def part_s_env() -> None:
    p = osp.probe_from_environment(GOOD_ENV, timeout_seconds=4.5)
    check("E1", "built", p is not None)
    assert p is not None
    c = p.client
    check(
        "E1",
        "client fields",
        (c.scheme, c.host, c.port, c.region, c.access_key, c.secret_key, c.timeout_seconds)
        == ("http", "s3.internal", 8333, "us-east-1", "AK", "SK", 4.5),
    )
    check("E1", "bucket", p.bucket == "sovra-training")
    check(
        "E1", "default timeout", osp.probe_from_environment(GOOD_ENV).client.timeout_seconds == 10.0
    )
    padded = {k: f"  {v} " for k, v in GOOD_ENV.items()}
    pp = osp.probe_from_environment(padded)
    check(
        "E2",
        "values stripped",
        pp is not None
        and pp.bucket == "sovra-training"
        and pp.client.host == "s3.internal"
        and pp.client.secret_key == "SK",
    )
    check(
        "E3",
        "env var names",
        dict(osp.ENV_VARS)
        == {
            "scheme": "SOVRA_STORAGE_S3_SCHEME",
            "host": "SOVRA_STORAGE_S3_HOST",
            "port": "SOVRA_STORAGE_S3_PORT",
            "region": "SOVRA_STORAGE_S3_REGION",
            "access_key": "SOVRA_STORAGE_S3_ACCESS_KEY",
            "secret_key": "SOVRA_STORAGE_S3_SECRET_KEY",
            "bucket": "SOVRA_TRAINING_OBJECT_STORE_BUCKET",
        },
    )
    for var in GOOD_ENV:
        missing = {k: v for k, v in GOOD_ENV.items() if k != var}
        check("E4", f"{var} missing -> None", osp.probe_from_environment(missing) is None)
        blank = dict(GOOD_ENV, **{var: "  "})
        check("E4", f"{var} blank -> None", osp.probe_from_environment(blank) is None)
    for scheme, ok in (("https", True), ("ftp", False), ("HTTP", False)):
        check(
            "E5",
            f"scheme {scheme}",
            (osp.probe_from_environment(dict(GOOD_ENV, SOVRA_STORAGE_S3_SCHEME=scheme)) is not None)
            == ok,
        )
    for port, ok in (
        ("1", True),
        ("65535", True),
        ("0", False),
        ("65536", False),
        ("abc", False),
        ("-1", False),
        ("80.0", False),
    ):
        check(
            "E6",
            f"port {port}",
            (osp.probe_from_environment(dict(GOOD_ENV, SOVRA_STORAGE_S3_PORT=port)) is not None)
            == ok,
        )


# --------------------------------------------------------------------------------------------
# R: the real machine
# --------------------------------------------------------------------------------------------


def part_r_real_host() -> None:
    real = nv.RealHostAccess()
    check("RH1", "missing binary", real.run(("sovra-no-such-binary-0705",), 5).returncode is None)
    done = real.run(("sh", "-c", "echo out; echo err >&2; exit 3"), 10)
    check(
        "RH2",
        "real exit code and streams",
        (done.returncode, done.stdout, done.stderr, done.timed_out) == (3, "out\n", "err\n", False),
    )
    done = real.run(("sleep", "5"), 0.2)
    check("RH3", "timeout", done.timed_out and done.returncode is None)
    done = real.run(
        ("sh", "-c", f"head -c {nv.MAX_PROBE_OUTPUT_CHARS + 50} /dev/zero | tr '\\0' 'a'"), 10
    )
    check("RH4", "output capped", len(done.stdout) == nv.MAX_PROBE_OUTPUT_CHARS)
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "noexec"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o644)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{tmp}:{old_path}"
        try:
            done = real.run((str(exe),), 5)
        finally:
            os.environ["PATH"] = old_path
        check(
            "RH5",
            "unexecutable binary -> OSError code 126 or not found",
            done.returncode in (126, None),
        )
        check("RH6", "system", real.system() == platform.system())
        f = Path(tmp) / "f.txt"
        f.write_text("hello")
        check("RH7", "read_text", real.read_text(str(f)) == "hello")
        check("RH7", "read_text missing", real.read_text(str(Path(tmp) / "nope")) is None)
        check("RH8", "exists", real.exists(str(f)) and not real.exists(str(Path(tmp) / "nope")))
        (Path(tmp) / "b").mkdir()
        check("RH9", "list_dir sorted", real.list_dir(tmp) == sorted(["b", "f.txt", "noexec"]))
        check("RH9", "list_dir missing", real.list_dir(str(Path(tmp) / "nope")) == [])
        check("RH10", "is_dir", real.is_dir(tmp) and not real.is_dir(str(f)))
        check(
            "RH11",
            "free bytes",
            real.free_bytes(tmp) == shutil.disk_usage(tmp).free or real.free_bytes(tmp) > 0,
        )
        before = sorted(os.listdir(tmp))
        real.write_probe_file(tmp)
        check("RH12", "probe file removed", sorted(os.listdir(tmp)) == before)
        try:
            real.write_probe_file(str(Path(tmp) / "nope"))
            check("RH13", "write in missing dir raises OSError", False)
        except OSError:
            check("RH13", "write in missing dir raises OSError", True)
        if os.geteuid() != 0:
            ro = Path(tmp) / "ro"
            ro.mkdir()
            ro.chmod(0o555)
            r = nv.validate_nvidia_environment(
                req(storage_path=str(ro), min_free_storage_bytes=1, object_store=None)
            )
            check(
                "RH14",
                "read-only dir really refused",
                probe(r, "storage").code == "storage_not_writable",
            )
            ro.chmod(0o755)


def _no_nvidia_here() -> bool:
    return shutil.which("nvidia-smi") is None


def part_r_real_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = nv.validate_nvidia_environment(
            req(storage_path=tmp, min_free_storage_bytes=1, object_store=Store())
        )
        check(
            "RV1",
            "the real machine is never SUPPORTED without a real GPU",
            not (_no_nvidia_here() and isinstance(result, nv.SupportedNvidiaProfile)),
        )
        if _no_nvidia_here():
            expect_blocked(
                "RV2 real machine without nvidia-smi", result, "nvidia_smi", "nvidia_smi_not_found"
            )
            check("RV2", "gpu absence", nv.blocked_by_gpu_absence(result))
            check("RV2", "real storage probe passed", probe(result, "storage").passed)
        else:
            print(
                f"NOTE: nvidia-smi is present on this machine; real result {result.status} {getattr(result, 'code', '')}"
            )


def part_r_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        argv = [
            "validate-environment",
            "--min-gpu-vram-gb",
            "24",
            "--precision",
            "bf16",
            "--min-free-storage-gb",
            "1",
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = entry.main(argv, {"SOVRA_TRAINING_WORK_DIR": "/work"}, Host())
        doc = json.loads(out.getvalue())
        check(
            "CLI1",
            "unconfigured object store blocks",
            rc == entry.EXIT_BLOCKED == 3 and doc["code"] == "object_store_unconfigured",
        )
        real_probe = entry.probe_from_environment
        seen: list[Any] = []

        def fake_probe(env):
            seen.append(env)
            return Store()

        entry.probe_from_environment = fake_probe
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = entry.main(argv, {"SOVRA_TRAINING_WORK_DIR": "/work"}, Host())
            doc = json.loads(out.getvalue())
            check(
                "CLI2",
                "all-good host -> exit 0 SUPPORTED",
                rc == entry.EXIT_SUPPORTED == 0 and doc["status"] == "SUPPORTED",
            )
            check(
                "CLI2",
                "probe built from the given env",
                seen == [{"SOVRA_TRAINING_WORK_DIR": "/work"}],
            )
            check(
                "CLI2",
                "sorted keys JSON",
                out.getvalue().strip() == json.dumps(doc, sort_keys=True),
            )
            out = io.StringIO()
            h = Host()
            h.work_dirs = {"/other"}
            with contextlib.redirect_stdout(out):
                rc = entry.main(
                    [*argv, "--work-dir", "/other"], {"SOVRA_TRAINING_WORK_DIR": "/work"}, h
                )
            check("CLI3", "--work-dir overrides env", rc == 0)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = entry.main(
                    [
                        "validate-environment",
                        "--min-gpu-vram-gb",
                        "81",
                        "--precision",
                        "bf16",
                        "--min-free-storage-gb",
                        "1",
                    ],
                    {"SOVRA_TRAINING_WORK_DIR": "/work"},
                    Host(),
                )
            check(
                "CLI4",
                "vram from argv",
                rc == 3 and json.loads(out.getvalue())["code"] == "gpu_memory_insufficient",
            )
            h = Host()
            h.commands[sys.executable] = nv.CommandResult(
                0, torch_report(bf16_native=False, bf16_matmul_ok=None), ""
            )
            for precision, want in (("bf16", 3), ("fp16", 0)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = entry.main(
                        [
                            "validate-environment",
                            "--min-gpu-vram-gb",
                            "24",
                            "--precision",
                            precision,
                            "--min-free-storage-gb",
                            "1",
                        ],
                        {"SOVRA_TRAINING_WORK_DIR": "/work"},
                        h,
                    )
                check("CLI5", f"precision {precision} -> {want}", rc == want)
            h = Host()
            h.free = 2 * GIB - 1
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = entry.main(
                    [
                        "validate-environment",
                        "--min-gpu-vram-gb",
                        "24",
                        "--precision",
                        "bf16",
                        "--min-free-storage-gb",
                        "2",
                    ],
                    {"SOVRA_TRAINING_WORK_DIR": "/work"},
                    h,
                )
            check(
                "CLI6",
                "storage GiB from argv",
                rc == 3 and json.loads(out.getvalue())["code"] == "storage_insufficient",
            )
            h = Host()
            h.files["/proc/sys/kernel/osrelease"] = "5.15-microsoft-standard-WSL2"
            for extra, want in (([], 3), (["--allow-development-host"], 0)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = entry.main([*argv, *extra], {"SOVRA_TRAINING_WORK_DIR": "/work"}, h)
                check("CLI7", f"development host flag {extra}", rc == want)
        finally:
            entry.probe_from_environment = real_probe
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            check(
                "CLI8",
                "missing args -> 2",
                entry.main(["validate-environment"], {}, Host()) == entry.EXIT_USAGE == 2,
            )
            check(
                "CLI8",
                "bad precision -> 2",
                entry.main([*argv[:4], "fp32", *argv[5:]], {}, Host()) == 2,
            )
            check("CLI8", "no command -> 2", entry.main([], {}, Host()) == 2)
            check("CLI8", "help -> 0", entry.main(["--help"], {}, Host()) == 0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = entry.main(argv, {}, Host())
        check(
            "CLI9",
            "no work dir -> usage error",
            rc == 2 and json.loads(err.getvalue())["status"] == "USAGE_ERROR",
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = entry.main(
                [
                    "validate-environment",
                    "--min-gpu-vram-gb",
                    "0",
                    "--precision",
                    "bf16",
                    "--min-free-storage-gb",
                    "1",
                ],
                {"SOVRA_TRAINING_WORK_DIR": tmp},
                Host(),
            )
        check("CLI9", "vram 0 -> usage error", rc == 2)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = entry.main(
                [
                    "validate-environment",
                    "--min-gpu-vram-gb",
                    "24",
                    "--precision",
                    "bf16",
                    "--min-free-storage-gb",
                    "0",
                ],
                {"SOVRA_TRAINING_WORK_DIR": tmp},
                Host(),
            )
        check("CLI9", "storage 0 -> usage error", rc == 2)
        # The real entry as a process on this real machine.
        canary = "sovra-canary-secret-0705-do-not-print"
        env = {
            **os.environ,
            **GOOD_ENV,
            "SOVRA_STORAGE_S3_HOST": "127.0.0.1",
            "SOVRA_STORAGE_S3_PORT": "1",
            "SOVRA_STORAGE_S3_SECRET_KEY": canary,
            "SOVRA_TRAINING_WORK_DIR": tmp,
        }
        done = subprocess.run(
            [sys.executable, "-m", "workers.training", *argv],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        doc = json.loads(done.stdout)
        check(
            "CLI10",
            "real process exit 3 on this machine" if _no_nvidia_here() else "real process ran",
            done.returncode == 3 or not _no_nvidia_here(),
        )
        check("CLI10", "real process BLOCKED", doc["status"] == "BLOCKED" or not _no_nvidia_here())
        if _no_nvidia_here():
            check(
                "CLI10",
                "real process names nvidia_smi",
                doc["failed_probe"] == "nvidia_smi" and doc["code"] == "nvidia_smi_not_found",
            )
        objp = next(p for p in doc["probes"] if p["probe"] == "object_store")
        check("CLI10", "real unreachable object store", objp["code"] == "object_store_unreachable")
        check(
            "CLI10", "secret never printed", canary not in done.stdout and canary not in done.stderr
        )


def part_r_real_gpu_script() -> None:
    script = ROOT / "tests" / "integration" / "test_ftf_0705_real_gpu.py"
    check("RG0", "real-GPU qualification script exists", script.is_file())
    if not _no_nvidia_here():
        print("NOTE: nvidia-smi present; the real-GPU script is run by its own CI step, not here")
        return
    done = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    check("RG1", f"no GPU -> exit 2 (got {done.returncode})", done.returncode == 2)
    check(
        "RG2",
        "BLOCKED_EXTERNAL reported loudly",
        "BLOCKED_EXTERNAL" in done.stderr and "NVIDIA GPU" in done.stderr,
    )
    check("RG3", "never reported as a pass", "PASS" not in done.stdout and "OK " not in done.stdout)


def main() -> None:
    part_v_supported()
    part_v_host()
    part_v_nvidia_smi()
    part_v_driver()
    part_v_container()
    part_v_cuda()
    part_v_bf16()
    part_v_memory()
    part_v_storage()
    part_v_object_store()
    part_v_order()
    part_v_requirements()
    part_p_script()
    part_s_probe()
    part_s_client()
    part_s_env()
    part_r_real_host()
    part_r_real_validation()
    part_r_entry()
    part_r_real_gpu_script()
    if FAILURES:
        for failure in FAILURES[:80]:
            print(failure, file=sys.stderr)
        print(f"{len(FAILURES)} failure(s) of {CHECKS[0]} checks", file=sys.stderr)
        sys.exit(1)
    print(f"OK {CID}: {CHECKS[0]} checks")


if __name__ == "__main__":
    main()
