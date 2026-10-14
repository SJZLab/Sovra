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
4. W1-INSTALL: after `create_app()` the record factory and `Logger.makeRecord`
   scrub at ingress, every uvicorn handler and `logging.lastResort` carry the
   redacting filter and formatter, and no handler still renders through
   uvicorn's `AccessFormatter` (which needs the raw args ingress removes).
5. Fault injection (Law 1): each mutation is applied to a *copy* of
   apps/api/app, served from that copy, and must turn the checks RED.

Disclosed (not covered here): FTF-0904's rules do not treat the OAuth `code`
and `state` query parameters on /auth/callback as secrets, so they still print
in the access log; that is a rule-set follow-up on FTF-0904, not this wiring.

Run directly: python3 tests/tasks/test_ftf_0904_w1.py
"""

from __future__ import annotations

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


def _env(pythonpath: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SOVRA_")}
    env.update(_full_app_env())
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


_INSTALL_PROBE = """
import json, logging, logging.config
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
}))
"""


def _check_install(app_dir: Path, fails: list[str]) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _INSTALL_PROBE],
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


def run_checks(app_dir: Path) -> list[str]:
    fails: list[str] = []
    _check_install(app_dir, fails)
    _serve_and_probe(app_dir, fails)
    return fails


SETUP = "log_redaction_setup.py"
FAULTS: list[tuple[str, str, str, str]] = [
    ("F1-no-call", "main.py", "    install_api_log_redaction()\n", ""),
    ("F2-keep-access-formatter", SETUP, "if isinstance(formatter, AccessFormatter):", "if False:"),
    ("F3-no-ingress", SETUP, "    install_redaction()\n", ""),
    ("F4-no-uvicorn-sink", SETUP, "            attach_sink(handler)\n", "            pass\n"),
    ("F5-no-last-resort", SETUP, "        attach_sink(logging.lastResort)\n", "        pass\n"),
    ("F6-skip-access-logger", SETUP, '"uvicorn.error", "uvicorn.access")', '"uvicorn.error")'),
]


def run_faults() -> None:
    for label, rel, old, new in FAULTS:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "api"
            shutil.copytree(
                API_ROOT / "app", copy / "app", ignore=shutil.ignore_patterns("__pycache__")
            )
            target = copy / "app" / rel
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                FAILURES.append(f"[{CRITERION}-W1-FAULT] {label}: mutation site not found once")
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
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
