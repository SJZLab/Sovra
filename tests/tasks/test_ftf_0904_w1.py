#!/usr/bin/env python3
"""FTF-0904-W1 -- log redaction installed on the live API process.

FTF-0904 certified `packages.security.log_redaction` with no caller: the served
app never installed it, so uvicorn's access log printed request lines verbatim,
query-string secrets included. This suite proves the wiring through the real
path a deployment uses -- `uvicorn app.main:create_app --factory` with uvicorn's
default logging config, over a real socket -- with no database:

1. W1-LEAK: harmless FTF-0905 canaries sent as secret query parameters
   (`token`, `access_token`, `api_key`, `password`, `client_secret`,
   `refresh_token`) on a real /api/v1 route (401) and an unknown path (404)
   never appear in the process's stdout or stderr.
2. W1-ACCESS: each of those requests still produces its access line, with the
   method, path, `<param>=[REDACTED]` and the status code -- redaction must not
   turn the access log into failure markers or "--- Logging error ---" blocks.
3. W1-STARTUP: uvicorn's own lifecycle lines still print through the sink.
   W1-BOOT-FAIL: a secret-shaped bad setting (`password=<canary>` as the
   training budget, `token=<canary>` as the privacy policy, `api_key=<canary>`
   as TLS verify) makes the factory raise during startup; the traceback that
   `sys.excepthook` prints still appears, redacted, and the canary never does.
   This also pins "install first": an install placed after settings are read
   would miss these exceptions. W1-BOOT-FAIL-MP repeats it under `--workers 2`
   and `--reload`, where the factory runs in a spawned child and the exception
   is printed by multiprocessing via `traceback.print_exc()`.
4. W1-INSTALL: after `create_app()` the record factory and `Logger.makeRecord`
   scrub at ingress, every uvicorn handler and `logging.lastResort` carry the
   redacting filter and formatter, and no handler still renders through
   uvicorn's `AccessFormatter` (which needs the raw args ingress removes).
   W1-HOOKS: in the same process, an exception escaping a thread, one raised
   in `__del__` (unraisable), a `warnings.warn` and direct
   `traceback.print_exception` / `print_exc` calls carrying canaries print
   their usual headers with the canary redacted.
   W1-FALLBACK (FTF-0904-W1-F1): on Python 3.13 the interpreter's own fallback
   printer calls `traceback.print_exception(..., colorize=...)` whenever
   `sys.excepthook` was reset to `sys.__excepthook__`, raises, or is deleted; the
   replacement must accept (and ignore) the extra keyword and still print only
   redacted text. Where that stdlib path does not exist (3.11/3.12) the check
   prints a notice instead of passing silently.
5. Fault injection (Law 1): each mutation is applied to a *copy* of
   apps/api/app, served from that copy, and must turn the checks RED.

Disclosed (not covered here): FTF-0904's rules do not treat the OAuth `code`
and `state` query parameters on /auth/callback as secrets, so they still print
in the access log; that is a rule-set follow-up on FTF-0904, not this wiring.

Run directly: python3 tests/tasks/test_ftf_0904_w1.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))

from packages.security.canary_secret_generator import generate_canary  # noqa: E402
from packages.security.log_redaction import REDACTED, REDACTION_FAILED_MARKER  # noqa: E402

CRITERION = "FTF-0904"
SECRET_PARAMS = ("token", "access_token", "api_key", "password", "client_secret", "refresh_token")
FAILURES: list[str] = []


def _full_app_env() -> dict[str, str]:
    """A complete env for `create_app()`; the database URL is never connected to."""
    return {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "https://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "https://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "https://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "https://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "https://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "https://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }


def _env(pythonpath: str, **overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SOVRA_")}
    env.update(_full_app_env())
    env.update(overrides)
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, proc: subprocess.Popen[bytes], deadline: float) -> bool:
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _serve_and_probe(app_dir: Path, fails: list[str]) -> None:
    """Boot the real factory under uvicorn's default logging, send secret-bearing requests."""
    port = _free_port()
    canaries = {p: generate_canary().value for p in SECRET_PARAMS}
    org = uuid.uuid4()
    requests: list[tuple[str, str, int]] = []
    for param, value in canaries.items():
        requests.append((f"/api/v1/organizations/{org}/projects", f"{param}={value}", 401))
        requests.append((f"/no-such-route-{param}", f"{param}={value}", 404))

    with tempfile.TemporaryDirectory() as tmp:
        out_path, err_path = Path(tmp) / "stdout.log", Path(tmp) / "stderr.log"
        with out_path.open("wb") as out, err_path.open("wb") as err:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--app-dir",
                    str(app_dir),
                ],
                cwd=str(ROOT),
                env=_env(str(ROOT)),
                stdout=out,
                stderr=err,
            )
            statuses: list[int | None] = []
            try:
                if not _wait_for_port(port, proc, time.monotonic() + 60):
                    fails.append(f"[{CRITERION}-W1-STARTUP] server never listened on port {port}")
                else:
                    for path, query, _ in requests:
                        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                        try:
                            conn.request("GET", f"{path}?{query}")
                            resp = conn.getresponse()
                            resp.read()
                            statuses.append(resp.status)
                        finally:
                            conn.close()
                    time.sleep(0.5)
            finally:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
        stdout = out_path.read_text(encoding="utf-8", errors="replace")
        stderr = err_path.read_text(encoding="utf-8", errors="replace")

    combined = stdout + stderr
    for param, value in canaries.items():
        if value in combined:
            fails.append(f"[{CRITERION}-W1-LEAK] the `{param}` canary reached the process output")
    if not statuses:
        return
    for (path, query, expected), status in zip(requests, statuses, strict=True):
        if status != expected:
            fails.append(f"[{CRITERION}-W1-ACCESS] GET {path} returned {status}, not {expected}")
        param = query.split("=", 1)[0]
        line = f'"GET {path}?{param}={REDACTED} HTTP/1.1" {expected}'
        if line not in stdout:
            fails.append(f"[{CRITERION}-W1-ACCESS] no redacted access line {line!r} on stdout")
    if REDACTION_FAILED_MARKER in combined:
        fails.append(f"[{CRITERION}-W1-ACCESS] {REDACTION_FAILED_MARKER} printed")
    if "--- Logging error ---" in combined:
        fails.append(f"[{CRITERION}-W1-ACCESS] a handler raised while formatting (Logging error)")
    if "Application startup complete." not in stderr:
        fails.append(f"[{CRITERION}-W1-STARTUP] uvicorn's lifecycle lines no longer print")


BOOT_FAILURES = (
    ("SOVRA_MAX_TRAINING_BUDGET_USD", "password={}"),
    ("SOVRA_PRIVACY_POLICY", "token={}"),
    ("SOVRA_TLS_VERIFY", "api_key={}"),
)


def _check_boot_failures(app_dir: Path, fails: list[str]) -> None:
    """A startup exception is printed by sys.excepthook, outside logging."""
    for var, template in BOOT_FAILURES:
        canary = generate_canary().value
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(_free_port()),
                "--app-dir",
                str(app_dir),
            ],
            cwd=str(ROOT),
            env=_env(str(ROOT), **{var: template.format(canary)}),
            capture_output=True,
            text=True,
            timeout=120,
        )
        tag = f"[{CRITERION}-W1-BOOT-FAIL] {var}"
        if proc.returncode == 0:
            fails.append(f"{tag}: a bad setting did not stop startup")
        if canary in proc.stdout + proc.stderr:
            fails.append(f"{tag}: the canary in the setting reached the process output")
        if "Traceback (most recent call last):" not in proc.stderr or REDACTED not in proc.stderr:
            fails.append(f"{tag}: no redacted traceback on stderr")


MP_MODES = (("--workers", "2"), ("--reload",))


def _check_multiprocess_boot_failures(app_dir: Path, fails: list[str]) -> None:
    """Under --workers/--reload the failing factory runs in a spawned child."""
    for mode in MP_MODES:
        canary = generate_canary().value
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "out.log"
            with out_path.open("wb") as out:
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(_free_port()),
                        "--app-dir",
                        str(app_dir),
                        *mode,
                        *(["--reload-dir", tmp] if mode == ("--reload",) else []),
                    ],
                    cwd=tmp,
                    env=_env(str(ROOT), SOVRA_MAX_TRAINING_BUDGET_USD=f"password={canary}"),
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 60
                try:
                    while time.monotonic() < deadline and proc.poll() is None:
                        if "ConfigurationError" in out_path.read_text(errors="replace"):
                            time.sleep(2)
                            break
                        time.sleep(0.3)
                finally:
                    for sig in (signal.SIGTERM, signal.SIGKILL):
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, sig)
                        try:
                            proc.wait(timeout=10)
                            break
                        except subprocess.TimeoutExpired:
                            continue
            text = out_path.read_text(encoding="utf-8", errors="replace")
        tag = f"[{CRITERION}-W1-BOOT-FAIL-MP] {' '.join(mode)}"
        if canary in text:
            fails.append(f"{tag}: the canary in the setting reached the process output")
        if "ConfigurationError" not in text or REDACTED not in text:
            fails.append(f"{tag}: no redacted startup traceback from the child process")


_INSTALL_PROBE = """
import json, logging, logging.config, sys, threading, warnings
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)
import app.main as m
m.create_app()
from packages.security.log_redaction import RedactingFilter, RedactingFormatter
from uvicorn.logging import AccessFormatter
def sunk(h):
    return [any(isinstance(f, RedactingFilter) for f in h.filters),
            isinstance(h.formatter, RedactingFormatter),
            isinstance(getattr(h.formatter, "_inner", None), AccessFormatter)
            or isinstance(h.formatter, AccessFormatter)]
print(json.dumps({
    "file": m.__file__,
    "factory": bool(getattr(logging.getLogRecordFactory(), "_ftf0904_redacting", False)),
    "make_record": bool(getattr(logging.Logger.makeRecord, "_ftf0904_redacting", False)),
    "handlers": [[n] + sunk(h) for n in ("uvicorn", "uvicorn.error", "uvicorn.access")
                 for h in logging.getLogger(n).handlers],
    "last_resort": sunk(logging.lastResort),
    "hooks": {name: bool(getattr(hook, "_ftf0904_w1_redacting", False)) for name, hook in (
        ("sys.excepthook", sys.excepthook), ("threading.excepthook", threading.excepthook),
        ("sys.unraisablehook", sys.unraisablehook),
        ("warnings.showwarning", warnings.showwarning))},
}))
import sys, threading, warnings, gc
canary = sys.argv[1]
def boom():
    raise RuntimeError("api_key=" + canary)
t = threading.Thread(target=boom, name="w1-probe")
t.start()
t.join()
class Unraisable:
    def __del__(self):
        raise ValueError("password=" + canary)
u = Unraisable()
del u
gc.collect()
warnings.warn("token=" + canary)
import io, traceback
try:
    raise KeyError("client_secret=" + canary)
except KeyError:
    traceback.print_exc()
    traceback.print_exception(*sys.exc_info())
    sink = io.StringIO()
    traceback.print_exception(*sys.exc_info(), None, sink)
    sys.stderr.write(sink.getvalue())
"""


def _check_install(app_dir: Path, fails: list[str]) -> None:
    canary = generate_canary().value
    proc = subprocess.run(
        [sys.executable, "-c", _INSTALL_PROBE, canary],
        cwd=str(ROOT),
        env=_env(f"{app_dir}{os.pathsep}{ROOT}"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        fails.append(f"[{CRITERION}-W1-INSTALL] create_app() probe failed: {proc.stderr[-400:]}")
        return
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    if not Path(got["file"]).resolve().is_relative_to(app_dir.resolve()):
        fails.append(f"[{CRITERION}-W1-INSTALL] probe imported {got['file']}, not {app_dir}")
    if not got["factory"]:
        fails.append(f"[{CRITERION}-W1-INSTALL] the log record factory does not scrub at ingress")
    if not got["make_record"]:
        fails.append(f"[{CRITERION}-W1-INSTALL] makeRecord does not scrub `extra=`")
    names = {h[0] for h in got["handlers"]}
    if not {"uvicorn", "uvicorn.access"} <= names:
        fails.append(f"[{CRITERION}-W1-INSTALL] uvicorn handlers missing: {sorted(names)}")
    for name, has_filter, has_formatter, access_formatter in got["handlers"]:
        if not (has_filter and has_formatter):
            fails.append(f"[{CRITERION}-W1-INSTALL] a `{name}` handler has no redacting sink")
        if access_formatter:
            fails.append(f"[{CRITERION}-W1-INSTALL] a `{name}` handler uses AccessFormatter")
    if not (got["last_resort"][0] and got["last_resort"][1]):
        fails.append(f"[{CRITERION}-W1-INSTALL] logging.lastResort has no redacting sink")
    tag = f"[{CRITERION}-W1-HOOKS]"
    # On Python 3.13 the stdlib's default hooks print through the (replaced)
    # traceback.print_exception, so the explicit hooks are redundant there; on 3.11/3.12
    # they are the only protection. Their presence is pinned on every interpreter.
    for hook_name, installed in got["hooks"].items():
        if not installed:
            fails.append(f"{tag} {hook_name} is not the redacting hook after create_app()")
    if canary in proc.stdout + proc.stderr:
        fails.append(f"{tag} a thread/unraisable/warning canary reached the process output")
    for header in (
        "Exception in thread w1-probe",
        "Exception ignored in",
        "UserWarning",
        "KeyError",
    ):
        if header not in proc.stderr:
            fails.append(f"{tag} {header!r} no longer prints on stderr")
    if proc.stderr.count(REDACTED) < 6:
        fails.append(f"{tag} the thread/unraisable/warning output is not redacted")


_FALLBACK_PROBE = """
import sys, traceback
if not hasattr(traceback, "_print_exception_bltin"):
    print("NO-STDLIB-FALLBACK-PATH")
    sys.exit(0)
import app.main as m
m.create_app()
canary, mode = sys.argv[1], sys.argv[2]
if mode == "reset":
    sys.excepthook = sys.__excepthook__
elif mode == "raising":
    def bad_hook(*args):
        raise ValueError("api_key=" + canary)
    sys.excepthook = bad_hook
elif mode == "deleted":
    del sys.excepthook
raise RuntimeError("password=" + canary)
"""
FALLBACK_MODES = ("reset", "raising", "deleted")


def _check_stdlib_fallback(app_dir: Path, fails: list[str]) -> None:
    for mode in FALLBACK_MODES:
        canary = generate_canary().value
        proc = subprocess.run(
            [sys.executable, "-c", _FALLBACK_PROBE, canary, mode],
            cwd=str(ROOT),
            env=_env(f"{app_dir}{os.pathsep}{ROOT}"),
            capture_output=True,
            text=True,
            timeout=120,
        )
        tag = f"[{CRITERION}-W1-FALLBACK] excepthook {mode}"
        if "NO-STDLIB-FALLBACK-PATH" in proc.stdout:
            print(f"  notice: {tag}: this Python has no traceback._print_exception_bltin; skipped")
            continue
        if canary in proc.stdout + proc.stderr:
            fails.append(
                f"{tag}: the canary reached the process output through the stdlib fallback"
            )
        if "RuntimeError" not in proc.stderr or REDACTED not in proc.stderr:
            fails.append(f"{tag}: no redacted traceback printed ({proc.stderr[-300:]!r})")
        if "TypeError" in proc.stderr:
            fails.append(f"{tag}: the redacting printer raised a TypeError on a stdlib keyword")


def run_checks(app_dir: Path) -> list[str]:
    fails: list[str] = []
    _check_install(app_dir, fails)
    _check_stdlib_fallback(app_dir, fails)
    _check_boot_failures(app_dir, fails)
    _check_multiprocess_boot_failures(app_dir, fails)
    _serve_and_probe(app_dir, fails)
    return fails


SETUP = "log_redaction_setup.py"
CALL = "    install_api_log_redaction()\n"
FAULTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("F1-no-call", "main.py", [(CALL, "")]),
    (
        "F2-keep-access-formatter",
        SETUP,
        [("if isinstance(formatter, AccessFormatter):", "if False:")],
    ),
    ("F3-no-ingress", SETUP, [("    install_redaction()\n", "")]),
    ("F4-no-uvicorn-sink", SETUP, [("            attach_sink(handler)\n", "            pass\n")]),
    ("F5-no-last-resort", SETUP, [("        attach_sink(logging.lastResort)\n", "        pass\n")]),
    ("F6-skip-access-logger", SETUP, [('"uvicorn.error", "uvicorn.access")', '"uvicorn.error")')]),
    (
        "F7-no-excepthook",
        SETUP,
        [("        sys.excepthook = _mark(_excepthook)\n", "        pass\n")],
    ),
    (
        "F8-no-thread-hook",
        SETUP,
        [("        threading.excepthook = _mark(_thread_excepthook)\n", "        pass\n")],
    ),
    (
        "F9-no-unraisable-hook",
        SETUP,
        [("        sys.unraisablehook = _mark(_unraisablehook)\n", "        pass\n")],
    ),
    (
        "F10-no-warning-hook",
        SETUP,
        [("        warnings.showwarning = _mark(_showwarning)\n", "        pass\n")],
    ),
    (
        "F11-raw-traceback",
        SETUP,
        [
            (
                "            text += redact_exception(exc)\n",
                '            text += "".join(__import__("traceback").format_exception(exc))\n',
            )
        ],
    ),
    (
        "F13-no-print-exception-hook",
        SETUP,
        [("        traceback.print_exception = _mark(_print_exception)\n", "        pass\n")],
    ),
    (
        "F14-no-traceback-hooks",
        SETUP,
        [
            ("        traceback.print_exception = _mark(_print_exception)\n", "        pass\n"),
            ("        traceback.print_exc = _mark(_print_exc)\n", "        pass\n"),
        ],
    ),
    (
        "F15-print-exception-no-extra-keywords",
        SETUP,
        [
            (
                "    chain: bool = True,\n    **_ignored: Any,\n) -> None:",
                "    chain: bool = True,\n) -> None:",
            )
        ],
    ),
    ("F12-install-late", "main.py", [(CALL, ""), ("    return app\n", CALL + "    return app\n")]),
]


def run_faults() -> None:
    for label, rel, edits in FAULTS:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "api"
            shutil.copytree(
                API_ROOT / "app", copy / "app", ignore=shutil.ignore_patterns("__pycache__")
            )
            target = copy / "app" / rel
            text = target.read_text(encoding="utf-8")
            if any(text.count(old) != 1 for old, _ in edits):
                FAILURES.append(f"[{CRITERION}-W1-FAULT] {label}: mutation site not found once")
                continue
            for old, new in edits:
                text = text.replace(old, new)
            target.write_text(text, encoding="utf-8")
            fails = run_checks(copy)
            if fails:
                print(f"  fault {label}: RED ({fails[0]})")
            else:
                FAILURES.append(f"[{CRITERION}-W1-FAULT] {label} stayed GREEN")


def main() -> None:
    FAILURES.extend(run_checks(API_ROOT))
    if not FAILURES:
        run_faults()
    if FAILURES:
        print(f"FTF-0904-W1 live log redaction tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0904-W1 live log redaction tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
