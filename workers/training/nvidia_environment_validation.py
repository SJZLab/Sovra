"""NVIDIA environment validation for the training worker (FTF-0705; FINE_TUNING_FACTORY_RELEASE_1.md
Section 13 `min_gpu_vram_gb` / `recommended_precision`, Section 19 "Validation checks", Sections
68, 70, 77, 81; CAP-09; Q10, Q13).

`validate_nvidia_environment` probes the REAL machine the worker runs on and returns either a typed
`SupportedNvidiaProfile` or a typed `BlockedEnvironment` that names the exact probe that failed and
its stable code. There is no simulated GPU, no default-to-supported and no CPU fallback: a profile
is built only when every probe passed (Laws 11 and 17). The probes run in this fixed order:

1. `host_os`        Linux; WSL2 is a development profile and is refused unless explicitly allowed.
2. `nvidia_smi`     the real `nvidia-smi --query-gpu` enumerates at least one NVIDIA device.
3. `driver`         every device reports one driver version, at or above the Release 1 floor.
4. `container_gpu`  inside a container: the NVIDIA device nodes are exposed to it; on a host: the
                    container runtime has the NVIDIA runtime or an NVIDIA CDI spec registered.
5. `cuda_runtime`   a separate Python process imports PyTorch and reports a CUDA build with
                    `torch.cuda.is_available()` and at least one device; the driver must meet the
                    floor for that CUDA major version. A CPU-only build is refused (CPU simulation
                    is not GPU qualification).
6. `bf16`           (when the requirement asks for bf16) compute capability >= 8.0 on a visible
                    device, native support without emulation, and a real bf16 matmul on the device.
7. `gpu_memory`     a visible device has total and free memory >= the capability's minimum.
8. `storage`        the work directory exists, is writable and has the required free bytes.
9. `object_store`   a probe object is written to, read back from and deleted in the configured
                    bucket of the real S3-compatible store.

A probe whose prerequisite failed is recorded `not_run` (`prerequisite_failed`); independent probes
still run so the operator sees every failure at once. The first failure in the order above is the
`failed_probe` of the blocked result.

PyTorch and the NVIDIA libraries are never imported by this module (they come with the pinned
Axolotl worker image, docs/architecture/REFERENCE_REPOSITORIES.md): the CUDA probe runs them in a
child interpreter, so importing this module costs nothing on a machine without them. Every outside
effect goes through `HostAccess`, whose default implementation touches the real machine; tests
substitute their own `HostAccess` and never reach the product path with it.

Not claimed here: model artifact reachability (FTF-0706), Temporal task-queue join and the local
provider UI (FTF-0803), GPU utilization reporting (FTF-1406).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

__all__ = [
    "BLOCKED",
    "CUDA_DRIVER_FLOORS",
    "PROBE_ORDER",
    "RELEASE_DRIVER_FLOOR",
    "SUPPORTED",
    "BlockedEnvironment",
    "CommandResult",
    "EnvironmentRequirements",
    "GpuDevice",
    "HostAccess",
    "ObjectStoreFailure",
    "ObjectStoreProbe",
    "ProbeOutcome",
    "RealHostAccess",
    "SupportedNvidiaProfile",
    "blocked_by_gpu_absence",
    "TorchDevice",
    "parse_driver_version",
    "parse_nvidia_smi",
    "parse_torch_report",
    "validate_nvidia_environment",
]

SUPPORTED = "SUPPORTED"
BLOCKED = "BLOCKED"

PROBE_ORDER: tuple[str, ...] = (
    "host_os",
    "nvidia_smi",
    "driver",
    "container_gpu",
    "cuda_runtime",
    "bf16",
    "gpu_memory",
    "storage",
    "object_store",
)

# NVIDIA's documented minimum Linux driver for each CUDA major version under minor-version
# compatibility (CUDA Toolkit release notes, "CUDA Driver" table). Release 1 trains on CUDA 12 or
# newer builds of PyTorch; an older or unknown CUDA major is refused rather than guessed.
CUDA_DRIVER_FLOORS: Mapping[int, tuple[int, int, int]] = {
    12: (525, 60, 13),
    13: (580, 65, 6),
}
RELEASE_DRIVER_FLOOR: tuple[int, int, int] = CUDA_DRIVER_FLOORS[12]
BF16_MIN_COMPUTE_CAPABILITY: tuple[int, int] = (8, 0)
MIB = 1024 * 1024

NVIDIA_SMI_QUERY: tuple[str, ...] = (
    "nvidia-smi",
    "--query-gpu=index,name,uuid,driver_version,memory.total,memory.free,compute_cap",
    "--format=csv,noheader,nounits",
)
NVIDIA_SMI_TIMEOUT_SECONDS = 30.0
TORCH_PROBE_TIMEOUT_SECONDS = 180.0
DOCKER_INFO_TIMEOUT_SECONDS = 30.0
MAX_PROBE_OUTPUT_CHARS = 65536

# Runs in a separate interpreter (`python -I -c`), so PyTorch never loads into the caller. It prints
# exactly one JSON object; anything else is `cuda_probe_unparseable`.
TORCH_PROBE_SCRIPT = r"""
import json
report = {"torch_importable": False}
try:
    import torch
except Exception as exc:
    report["import_error"] = type(exc).__name__
    print(json.dumps(report))
    raise SystemExit(0)
report["torch_importable"] = True
report["torch_version"] = str(torch.__version__)
report["cuda_version"] = torch.version.cuda
report["cuda_available"] = bool(torch.cuda.is_available())
devices = []
if report["cuda_available"]:
    for index in range(torch.cuda.device_count()):
        major, minor = torch.cuda.get_device_capability(index)
        free, total = torch.cuda.mem_get_info(index)
        devices.append({"index": index, "name": torch.cuda.get_device_name(index),
                        "capability": [int(major), int(minor)],
                        "total_bytes": int(total), "free_bytes": int(free)})
report["devices"] = devices
native = None
if report["cuda_available"] and devices:
    try:
        native = bool(torch.cuda.is_bf16_supported(including_emulation=False))
    except TypeError:
        native = bool(torch.cuda.is_bf16_supported())
report["bf16_native"] = native
matmul = None
if native:
    try:
        a = torch.ones((8, 8), dtype=torch.bfloat16, device="cuda:0")
        value = float((a @ a).float().sum().item())
        torch.cuda.synchronize(0)
        matmul = value == 512.0
    except Exception as exc:
        report["bf16_error"] = type(exc).__name__
        matmul = False
report["bf16_matmul_ok"] = matmul
print(json.dumps(report))
"""

_DRIVER_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?$")
_CAPABILITY_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")
_CUDA_VERSION_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})(?:\.\d{1,4})?$")
_UUID_RE = re.compile(r"^(GPU|MIG)-[0-9A-Za-z-]{1,80}$")
_INT_RE = re.compile(r"^\d{1,12}$")
_CONTAINER_CGROUP_MARKERS = ("docker", "kubepods", "containerd", "libpod", "lxc")


# --------------------------------------------------------------------------------------------
# Typed results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeOutcome:
    """One probe's result. `code` is `ok` exactly when `status` is `passed`."""

    probe: str
    status: Literal["passed", "failed", "not_run"]
    code: str
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(frozen=True)
class GpuDevice:
    index: int
    name: str
    uuid: str
    driver_version: str
    memory_total_bytes: int
    memory_free_bytes: int
    compute_capability: tuple[int, int]


@dataclass(frozen=True)
class TorchDevice:
    index: int
    name: str
    capability: tuple[int, int]
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class SupportedNvidiaProfile:
    status: Literal["SUPPORTED"]
    host_profile: Literal["linux", "wsl2_development"]
    in_container: bool
    driver_version: str
    cuda_version: str
    torch_version: str
    gpus: tuple[GpuDevice, ...]
    qualified_device_indices: tuple[int, ...]
    bf16_supported: bool
    storage_path: str
    storage_free_bytes: int
    object_store_bucket: str
    probes: tuple[ProbeOutcome, ...]

    def to_dict(self) -> dict[str, object]:
        return _result_dict(self)


@dataclass(frozen=True)
class BlockedEnvironment:
    status: Literal["BLOCKED"]
    failed_probe: str
    code: str
    detail: str
    failed_probes: tuple[str, ...]
    probes: tuple[ProbeOutcome, ...]

    def to_dict(self) -> dict[str, object]:
        return _result_dict(self)


def _result_dict(result: SupportedNvidiaProfile | BlockedEnvironment) -> dict[str, object]:
    out: dict[str, object] = {}
    for name in result.__dataclass_fields__:
        value = getattr(result, name)
        if name == "probes":
            value = [
                {"probe": p.probe, "status": p.status, "code": p.code, "detail": p.detail}
                for p in value
            ]
        elif name == "gpus":
            value = [
                {
                    "index": g.index,
                    "name": g.name,
                    "uuid": g.uuid,
                    "driver_version": g.driver_version,
                    "memory_total_bytes": g.memory_total_bytes,
                    "memory_free_bytes": g.memory_free_bytes,
                    "compute_capability": list(g.compute_capability),
                }
                for g in value
            ]
        elif isinstance(value, tuple):
            value = list(value)
        out[name] = value
    return out


# --------------------------------------------------------------------------------------------
# Requirements and host access
# --------------------------------------------------------------------------------------------


class ObjectStoreFailure(Exception):
    """Raised by an `ObjectStoreProbe` with one of the object-store codes."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class ObjectStoreProbe(Protocol):
    bucket: str

    def round_trip(self, key: str, body: bytes) -> None:
        """Write `body` at `key`, read it back byte-identically and delete it, or raise
        `ObjectStoreFailure`."""


@dataclass(frozen=True)
class EnvironmentRequirements:
    """What the training job needs. `min_gpu_vram_gb` and `require_bf16` come from the model's
    Section 13 `TrainingCapability` (`min_gpu_vram_gb`, `recommended_precision == "bf16"`)."""

    min_gpu_vram_gb: int
    require_bf16: bool
    storage_path: str
    min_free_storage_bytes: int
    object_store: ObjectStoreProbe | None
    allow_development_host: bool = False

    def __post_init__(self) -> None:
        if type(self.min_gpu_vram_gb) is not int or not 1 <= self.min_gpu_vram_gb <= 4096:
            raise ValueError("min_gpu_vram_gb must be an int in 1..4096")
        if type(self.require_bf16) is not bool or type(self.allow_development_host) is not bool:
            raise ValueError("require_bf16 and allow_development_host must be bool")
        if type(self.min_free_storage_bytes) is not int or self.min_free_storage_bytes < 1:
            raise ValueError("min_free_storage_bytes must be a positive int")
        if not isinstance(self.storage_path, str) or not self.storage_path.strip():
            raise ValueError("storage_path must be a non-empty path")


@dataclass(frozen=True)
class CommandResult:
    """`returncode` is None when the binary was not found; `timed_out` when it overran."""

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


class HostAccess(Protocol):
    def run(self, argv: Sequence[str], timeout: float) -> CommandResult: ...

    def system(self) -> str: ...

    def read_text(self, path: str) -> str | None: ...

    def exists(self, path: str) -> bool: ...

    def list_dir(self, path: str) -> list[str]: ...

    def is_dir(self, path: str) -> bool: ...

    def free_bytes(self, path: str) -> int: ...

    def write_probe_file(self, directory: str) -> None: ...


class RealHostAccess:
    """The real machine. The only `HostAccess` the product path uses."""

    def run(self, argv: Sequence[str], timeout: float) -> CommandResult:
        if shutil.which(argv[0]) is None:
            return CommandResult(None, "", f"{argv[0]} not found on PATH")
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, never a shell
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return CommandResult(None, "", "", timed_out=True)
        except OSError as exc:
            return CommandResult(126, "", type(exc).__name__)
        return CommandResult(
            done.returncode,
            done.stdout[:MAX_PROBE_OUTPUT_CHARS],
            done.stderr[:MAX_PROBE_OUTPUT_CHARS],
        )

    def system(self) -> str:
        return platform.system()

    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")[:MAX_PROBE_OUTPUT_CHARS]
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def list_dir(self, path: str) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def free_bytes(self, path: str) -> int:
        return int(shutil.disk_usage(path).free)

    def write_probe_file(self, directory: str) -> None:
        fd, name = tempfile.mkstemp(prefix=".sovra-env-probe-", dir=directory)
        try:
            os.write(fd, b"sovra")
            os.fsync(fd)
        finally:
            os.close(fd)
            os.unlink(name)


# --------------------------------------------------------------------------------------------
# Parsers (pure)
# --------------------------------------------------------------------------------------------


class _ParseError(ValueError):
    pass


def parse_driver_version(text: str) -> tuple[int, int, int]:
    match = _DRIVER_RE.match(text)
    if match is None:
        raise _ParseError("driver version is not N.N or N.N.N")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _parse_capability(text: str) -> tuple[int, int]:
    match = _CAPABILITY_RE.match(text)
    if match is None:
        raise _ParseError("compute capability is not N.N")
    return (int(match.group(1)), int(match.group(2)))


def _parse_mib(text: str) -> int:
    if _INT_RE.match(text) is None:
        raise _ParseError("memory value is not an integer MiB")
    return int(text) * MIB


def parse_nvidia_smi(stdout: str) -> tuple[GpuDevice, ...]:
    """Strictly parse `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` output."""
    devices: list[GpuDevice] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        fields = [part.strip() for part in line.split(",")]
        if len(fields) != 7:
            raise _ParseError("expected 7 comma-separated fields per device")
        index_text, name, gpu_uuid, driver, total, free, capability = fields
        if _INT_RE.match(index_text) is None:
            raise _ParseError("device index is not an integer")
        if not name or name.startswith("[") or len(name) > 200:
            raise _ParseError("device name is missing or not supported")
        if _UUID_RE.match(gpu_uuid) is None:
            raise _ParseError("device uuid is not a GPU-/MIG- identifier")
        parse_driver_version(driver)
        total_bytes = _parse_mib(total)
        free_bytes = _parse_mib(free)
        if free_bytes > total_bytes:
            raise _ParseError("free memory exceeds total memory")
        devices.append(
            GpuDevice(
                index=int(index_text),
                name=name,
                uuid=gpu_uuid,
                driver_version=driver,
                memory_total_bytes=total_bytes,
                memory_free_bytes=free_bytes,
                compute_capability=_parse_capability(capability),
            )
        )
    if len({d.index for d in devices}) != len(devices):
        raise _ParseError("duplicate device index")
    return tuple(devices)


@dataclass(frozen=True)
class _TorchReport:
    importable: bool
    torch_version: str
    cuda_version: str | None
    cuda_available: bool
    devices: tuple[TorchDevice, ...]
    bf16_native: bool | None
    bf16_matmul_ok: bool | None


def _strict_bool(value: object, *, optional: bool = False) -> bool | None:
    if value is None and optional:
        return None
    if type(value) is not bool:
        raise _ParseError("expected a boolean")
    return value


def _strict_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise _ParseError("expected a non-negative integer")
    return value


def parse_torch_report(stdout: str) -> _TorchReport:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise _ParseError("expected exactly one JSON line")
    try:
        raw = json.loads(lines[0])
    except ValueError as exc:
        raise _ParseError("not JSON") from exc
    if not isinstance(raw, dict):
        raise _ParseError("not a JSON object")
    importable = _strict_bool(raw.get("torch_importable"))
    if not importable:
        return _TorchReport(False, "", None, False, (), None, None)
    version = raw.get("torch_version")
    if not isinstance(version, str) or not version:
        raise _ParseError("torch_version missing")
    cuda_version = raw.get("cuda_version")
    if cuda_version is not None and not isinstance(cuda_version, str):
        raise _ParseError("cuda_version must be a string or null")
    devices_raw = raw.get("devices")
    if not isinstance(devices_raw, list):
        raise _ParseError("devices must be a list")
    devices: list[TorchDevice] = []
    for item in devices_raw:
        if not isinstance(item, dict):
            raise _ParseError("device entry must be an object")
        capability = item.get("capability")
        if not (isinstance(capability, list) and len(capability) == 2):
            raise _ParseError("capability must be [major, minor]")
        name = item.get("name")
        if not isinstance(name, str):
            raise _ParseError("device name must be a string")
        total = _strict_int(item.get("total_bytes"))
        free = _strict_int(item.get("free_bytes"))
        if free > total:
            raise _ParseError("free memory exceeds total memory")
        devices.append(
            TorchDevice(
                index=_strict_int(item.get("index")),
                name=name,
                capability=(_strict_int(capability[0]), _strict_int(capability[1])),
                total_bytes=total,
                free_bytes=free,
            )
        )
    return _TorchReport(
        importable=True,
        torch_version=version,
        cuda_version=cuda_version,
        cuda_available=bool(_strict_bool(raw.get("cuda_available"))),
        devices=tuple(devices),
        bf16_native=_strict_bool(raw.get("bf16_native"), optional=True),
        bf16_matmul_ok=_strict_bool(raw.get("bf16_matmul_ok"), optional=True),
    )


def _short(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat[:limit]


# --------------------------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------------------------


@dataclass
class _State:
    host_profile: Literal["linux", "wsl2_development"] = "linux"
    in_container: bool = False
    gpus: tuple[GpuDevice, ...] = ()
    driver: tuple[int, int, int] | None = None
    driver_text: str = ""
    torch: _TorchReport | None = None
    qualified: tuple[int, ...] = ()
    storage_free: int = 0
    outcomes: dict[str, ProbeOutcome] = field(default_factory=dict)


def _ok(probe: str, detail: str = "") -> ProbeOutcome:
    return ProbeOutcome(probe, "passed", "ok", detail)


def _fail(probe: str, code: str, detail: str = "") -> ProbeOutcome:
    return ProbeOutcome(probe, "failed", code, detail)


def _not_run(probe: str, needs: str) -> ProbeOutcome:
    return ProbeOutcome(probe, "not_run", "prerequisite_failed", f"needs {needs}")


def _probe_host(host: HostAccess, req: EnvironmentRequirements, st: _State) -> ProbeOutcome:
    system = host.system()
    if system != "Linux":
        return _fail("host_os", "host_not_linux", f"platform.system() is {system!r}")
    release = (host.read_text("/proc/sys/kernel/osrelease") or "").lower()
    if "microsoft" in release or "wsl" in release:
        st.host_profile = "wsl2_development"
        if not req.allow_development_host:
            return _fail(
                "host_os",
                "wsl2_development_only",
                "WSL2 is a development profile, not production local qualification",
            )
    return _ok("host_os", st.host_profile)


def _probe_nvidia_smi(host: HostAccess, st: _State) -> ProbeOutcome:
    result = host.run(NVIDIA_SMI_QUERY, NVIDIA_SMI_TIMEOUT_SECONDS)
    if result.timed_out:
        return _fail("nvidia_smi", "nvidia_smi_timeout", "nvidia-smi did not answer")
    if result.returncode is None:
        return _fail("nvidia_smi", "nvidia_smi_not_found", "nvidia-smi is not on PATH")
    combined = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        if "no devices were found" in combined.lower():
            return _fail("nvidia_smi", "gpu_absent", "nvidia-smi found no devices")
        if "communicate with the nvidia driver" in combined.lower():
            return _fail("nvidia_smi", "driver_not_loaded", _short(combined))
        return _fail(
            "nvidia_smi",
            "nvidia_smi_failed",
            f"exit {result.returncode}: {_short(combined)}",
        )
    try:
        gpus = parse_nvidia_smi(result.stdout)
    except _ParseError as exc:
        return _fail("nvidia_smi", "nvidia_smi_unparseable", str(exc))
    if not gpus:
        return _fail("nvidia_smi", "gpu_absent", "nvidia-smi listed no devices")
    st.gpus = gpus
    return _ok("nvidia_smi", f"{len(gpus)} device(s)")


def _probe_driver(st: _State) -> ProbeOutcome:
    versions = {g.driver_version for g in st.gpus}
    if len(versions) != 1:
        return _fail("driver", "driver_inconsistent", "devices report different drivers")
    text = versions.pop()
    parsed = parse_driver_version(text)
    if parsed < RELEASE_DRIVER_FLOOR:
        floor = ".".join(str(p) for p in RELEASE_DRIVER_FLOOR)
        return _fail("driver", "driver_incompatible", f"driver {text} is below {floor}")
    st.driver = parsed
    st.driver_text = text
    return _ok("driver", text)


def _detect_container(host: HostAccess) -> bool:
    if host.exists("/.dockerenv") or host.exists("/run/.containerenv"):
        return True
    cgroup = (host.read_text("/proc/1/cgroup") or "").lower()
    return any(marker in cgroup for marker in _CONTAINER_CGROUP_MARKERS)


def _probe_container(host: HostAccess, st: _State) -> ProbeOutcome:
    st.in_container = _detect_container(host)
    if st.in_container:
        nodes = host.list_dir("/dev")
        has_ctl = "nvidiactl" in nodes
        has_device = any(re.fullmatch(r"nvidia\d+", n) for n in nodes)
        if not (has_ctl and has_device):
            return _fail(
                "container_gpu",
                "container_gpu_not_exposed",
                "this container has no /dev/nvidiactl and /dev/nvidiaN device nodes",
            )
        return _ok("container_gpu", "in container; NVIDIA device nodes exposed")
    result = host.run(
        ("docker", "info", "--format", "{{json .Runtimes}}"), DOCKER_INFO_TIMEOUT_SECONDS
    )
    if result.returncode is None and not result.timed_out:
        return _fail("container_gpu", "container_runtime_not_found", "docker is not on PATH")
    if result.timed_out or result.returncode != 0:
        return _fail(
            "container_gpu",
            "container_runtime_unavailable",
            _short(f"{result.stdout} {result.stderr}") or "docker info timed out",
        )
    try:
        runtimes = json.loads(result.stdout.strip() or "null")
    except ValueError:
        runtimes = None
    if isinstance(runtimes, dict) and "nvidia" in runtimes:
        return _ok("container_gpu", "host; docker has the nvidia runtime")
    for spec_dir in ("/etc/cdi", "/var/run/cdi"):
        for name in host.list_dir(spec_dir):
            if name.startswith("nvidia") and name.endswith((".yaml", ".yml", ".json")):
                return _ok("container_gpu", f"host; NVIDIA CDI spec {spec_dir}/{name}")
    return _fail(
        "container_gpu",
        "container_gpu_runtime_missing",
        "docker has no nvidia runtime and no NVIDIA CDI spec is installed",
    )


def _probe_cuda(host: HostAccess, st: _State) -> ProbeOutcome:
    result = host.run((sys.executable, "-I", "-c", TORCH_PROBE_SCRIPT), TORCH_PROBE_TIMEOUT_SECONDS)
    if result.timed_out:
        return _fail("cuda_runtime", "cuda_probe_timeout", "the PyTorch CUDA probe overran")
    if result.returncode != 0:
        return _fail(
            "cuda_runtime",
            "cuda_probe_failed",
            f"exit {result.returncode}: {_short(result.stderr)}",
        )
    try:
        report = parse_torch_report(result.stdout)
    except _ParseError as exc:
        return _fail("cuda_runtime", "cuda_probe_unparseable", str(exc))
    if not report.importable:
        return _fail("cuda_runtime", "torch_unavailable", "PyTorch is not importable")
    if report.cuda_version is None:
        return _fail("cuda_runtime", "torch_cpu_build", f"torch {report.torch_version} is CPU-only")
    if not report.cuda_available or not report.devices:
        return _fail("cuda_runtime", "torch_cuda_unavailable", "torch.cuda sees no device")
    match = _CUDA_VERSION_RE.match(report.cuda_version)
    if match is None:
        return _fail("cuda_runtime", "cuda_version_unsupported", report.cuda_version[:40])
    floor = CUDA_DRIVER_FLOORS.get(int(match.group(1)))
    if floor is None:
        return _fail("cuda_runtime", "cuda_version_unsupported", f"CUDA {report.cuda_version}")
    if st.driver is None or st.driver < floor:
        need = ".".join(str(p) for p in floor)
        return _fail(
            "cuda_runtime",
            "cuda_driver_mismatch",
            f"CUDA {report.cuda_version} needs driver >= {need}, found {st.driver_text}",
        )
    st.torch = report
    return _ok("cuda_runtime", f"torch {report.torch_version}, CUDA {report.cuda_version}")


def _bf16_ok(st: _State) -> bool:
    torch = st.torch
    assert torch is not None
    capable = any(d.capability >= BF16_MIN_COMPUTE_CAPABILITY for d in torch.devices)
    return capable and torch.bf16_native is True and torch.bf16_matmul_ok is True


def _probe_bf16(req: EnvironmentRequirements, st: _State) -> ProbeOutcome:
    supported = _bf16_ok(st)
    if req.require_bf16 and not supported:
        return _fail(
            "bf16",
            "bf16_unsupported",
            "bf16 needs compute capability >= 8.0, native support and a real bf16 matmul",
        )
    return _ok("bf16", "supported" if supported else "not required")


def _probe_memory(req: EnvironmentRequirements, st: _State) -> ProbeOutcome:
    torch = st.torch
    assert torch is not None
    need = req.min_gpu_vram_gb * 1024 * MIB
    big_enough = [d for d in torch.devices if d.total_bytes >= need]
    if not big_enough:
        return _fail(
            "gpu_memory",
            "gpu_memory_insufficient",
            f"no visible device has {req.min_gpu_vram_gb} GiB total memory",
        )
    qualified = tuple(d.index for d in big_enough if d.free_bytes >= need)
    if not qualified:
        return _fail(
            "gpu_memory",
            "gpu_memory_in_use",
            f"no visible device has {req.min_gpu_vram_gb} GiB free",
        )
    st.qualified = qualified
    return _ok("gpu_memory", f"devices {list(qualified)}")


def _probe_storage(host: HostAccess, req: EnvironmentRequirements, st: _State) -> ProbeOutcome:
    path = req.storage_path
    if not host.is_dir(path):
        return _fail("storage", "storage_path_missing", "the work directory does not exist")
    try:
        host.write_probe_file(path)
    except OSError as exc:
        return _fail("storage", "storage_not_writable", type(exc).__name__)
    try:
        free = host.free_bytes(path)
    except OSError as exc:
        return _fail("storage", "storage_not_writable", type(exc).__name__)
    if free < req.min_free_storage_bytes:
        return _fail(
            "storage",
            "storage_insufficient",
            f"{free} bytes free, {req.min_free_storage_bytes} required",
        )
    st.storage_free = free
    return _ok("storage", f"{free} bytes free")


def _probe_object_store(req: EnvironmentRequirements) -> ProbeOutcome:
    store = req.object_store
    if store is None:
        return _fail("object_store", "object_store_unconfigured", "no object store configured")
    key = f"sovra-env-probe/{uuid.uuid4().hex}"
    body = f"sovra nvidia environment probe {uuid.uuid4().hex}".encode()
    try:
        store.round_trip(key, body)
    except ObjectStoreFailure as exc:
        return _fail("object_store", exc.code, exc.detail)
    return _ok("object_store", f"bucket {store.bucket}")


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def validate_nvidia_environment(
    requirements: EnvironmentRequirements, host: HostAccess | None = None
) -> SupportedNvidiaProfile | BlockedEnvironment:
    """Probe the machine and return a supported profile, or BLOCKED naming the failed probe."""
    access: HostAccess = host if host is not None else RealHostAccess()
    st = _State()
    out = st.outcomes

    def record(outcome: ProbeOutcome) -> bool:
        out[outcome.probe] = outcome
        return outcome.passed

    record(_probe_host(access, requirements, st))
    smi_ok = record(_probe_nvidia_smi(access, st))
    if smi_ok:
        driver_ok = record(_probe_driver(st))
        record(_probe_container(access, st))
    else:
        driver_ok = record(_not_run("driver", "nvidia_smi"))
        record(_not_run("container_gpu", "nvidia_smi"))
    if driver_ok:
        cuda_ok = record(_probe_cuda(access, st))
    else:
        cuda_ok = record(_not_run("cuda_runtime", "driver"))
    if cuda_ok:
        record(_probe_bf16(requirements, st))
        record(_probe_memory(requirements, st))
    else:
        record(_not_run("bf16", "cuda_runtime"))
        record(_not_run("gpu_memory", "cuda_runtime"))
    record(_probe_storage(access, requirements, st))
    record(_probe_object_store(requirements))

    probes = tuple(out[name] for name in PROBE_ORDER)
    failed = tuple(p.probe for p in probes if p.status == "failed")
    not_run = tuple(p.probe for p in probes if p.status == "not_run")
    if failed or not_run:
        first = next(p for p in probes if not p.passed)
        return BlockedEnvironment(
            status="BLOCKED",
            failed_probe=first.probe,
            code=first.code,
            detail=first.detail,
            failed_probes=failed,
            probes=probes,
        )
    torch = st.torch
    store = requirements.object_store
    if torch is None or store is None or torch.cuda_version is None or not st.qualified:
        raise AssertionError("every probe passed without its facts")  # unreachable by design
    return SupportedNvidiaProfile(
        status="SUPPORTED",
        host_profile=st.host_profile,
        in_container=st.in_container,
        driver_version=st.driver_text,
        cuda_version=torch.cuda_version,
        torch_version=torch.torch_version,
        gpus=st.gpus,
        qualified_device_indices=st.qualified,
        bf16_supported=_bf16_ok(st),
        storage_path=requirements.storage_path,
        storage_free_bytes=st.storage_free,
        object_store_bucket=store.bucket,
        probes=probes,
    )


def blocked_by_gpu_absence(result: SupportedNvidiaProfile | BlockedEnvironment) -> bool:
    """True when the machine simply has no usable NVIDIA GPU stack (the BLOCKED_EXTERNAL case
    for real-GPU qualification), as opposed to a GPU machine that failed a probe."""
    return isinstance(result, BlockedEnvironment) and result.code in _NO_GPU_CODES


_NO_GPU_CODES = frozenset({"nvidia_smi_not_found", "gpu_absent", "driver_not_loaded"})
