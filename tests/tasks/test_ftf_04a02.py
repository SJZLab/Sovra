#!/usr/bin/env python3
"""FTF-04A02 -- GitHub App/token read-only secret integration (no-DB
unit/fault tests).

Runnable without any database and without real egress to the public
internet -- this suite builds and drives a real local HTTPS server itself,
matching the no-DB/no-external-egress local-server pattern
`tests/tasks/test_ftf_0302.py` already established: a real self-signed CA
this suite generates and installs into the sandbox's own system trust
store for the duration of the run (removed again in `atexit`), so every
request this suite makes goes through
`packages/security/outbound_guard.py`'s own real, completely unmodified
`ssl.create_default_context()` certificate validation and real DNS/socket
connection path -- never a mocked `guarded_fetch`, never a relaxed TLS
context. The server implements the exact subset of the real GitHub REST
API v3 `github_app_token_read_only_secret_integration.py` calls (`GET
/repos/{owner}/{repo}`) -- this sandboxed CI/dev environment has no route
to the public internet at all (confirmed directly: a plain `curl` to
`api.github.com` from this sandbox fails at the outbound proxy), so a real
local double is this repository's own established way to prove the real
request/response/verification logic without depending on a live
third-party service's availability inside an automated gate.

Covers this task's own negative_acceptance ("Private repo outside
installation and revoked token deny access; no write scopes requested for
analysis") as three independently-triggered real checks, plus real
full_name/default_branch response-integrity checks, a real rate-limit
distinction, a real destination-denied policy check, and three disclosed
fault injections (Law 1) proving the revoked-token check, the
access-denied check, and the GET-only structural guarantee are each
load-bearing.

Run directly: python3 tests/tasks/test_ftf_04a02.py
"""

from __future__ import annotations

import atexit
import importlib
import ipaddress
import json as _json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.security.url_policy import ConnectionPolicy  # noqa: E402

import app.target_connectors.github_app_token_read_only_secret_integration as github_connector  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local HTTPS GitHub-REST-API-v3-shaped fixture ----------------------

REQUEST_LOG: list[dict[str, object]] = []

_ROUTES: dict[str, dict[str, object]] = {
    "/repos/acme/good-repo": {
        "status": 200,
        "json": {"full_name": "acme/good-repo", "private": False, "default_branch": "main"},
    },
    "/repos/acme/private-good-repo": {
        "status": 200,
        "json": {
            "full_name": "acme/private-good-repo",
            "private": True,
            "default_branch": "trunk",
        },
    },
    "/repos/acme/revoked-repo": {
        # A real GitHub 401 body is just `{"message": "Bad credentials"}` --
        # this fixture deliberately also includes a plausible-looking
        # `full_name`/`default_branch` alongside the real 401 status, so
        # this suite's own fault-injection tests can prove that the status
        # check itself (not the response-shape checks) is what denies
        # access here: without it, this body alone would otherwise parse as
        # a fully valid, complete resolution.
        "status": 401,
        "json": {
            "message": "Bad credentials",
            "full_name": "acme/revoked-repo",
            "private": False,
            "default_branch": "main",
        },
    },
    "/repos/acme/outside-installation-repo": {
        # Real GitHub also never does this (a 404 body is just `{"message":
        # "Not Found"}`) -- same deliberate fault-injection fixture design
        # as `revoked-repo` above, for the same reason.
        "status": 404,
        "json": {
            "message": "Not Found",
            "full_name": "acme/outside-installation-repo",
            "private": True,
            "default_branch": "main",
        },
    },
    "/repos/acme/ratelimited-repo": {
        "status": 403,
        "headers": {"X-RateLimit-Remaining": "0"},
        "json": {"message": "API rate limit exceeded for installation."},
    },
    "/repos/acme/forbidden-repo": {
        "status": 403,
        "headers": {"X-RateLimit-Remaining": "42"},
        "json": {"message": "Resource not accessible by integration"},
    },
    "/repos/acme/mismatch-repo": {
        "status": 200,
        "json": {
            "full_name": "acme/a-completely-different-repo",
            "private": False,
            "default_branch": "main",
        },
    },
    "/repos/acme/no-branch-repo": {
        "status": 200,
        "json": {"full_name": "acme/no-branch-repo", "private": False},
    },
}


class _GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        REQUEST_LOG.append(
            {
                "method": "GET",
                "path": self.path,
                "headers": dict(self.headers.items()),
                "content_length": self.headers.get("Content-Length"),
            }
        )
        route = _ROUTES.get(self.path)
        if route is None:
            self._respond(404, b'{"message": "Not Found"}')
            return
        status_value = route["status"]
        assert isinstance(status_value, int)
        body = _json.dumps(route["json"]).encode("utf-8")
        self._respond(status_value, body, extra_headers=route.get("headers"))  # type: ignore[arg-type]

    def _respond(
        self, status: int, body: bytes, extra_headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-04a02-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-04a02-test-ca.crt")
_UPDATE_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _run_openssl(args: list[str]) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    ca_key = _TLS_TMP / "ca.key"
    ca_crt = _TLS_TMP / "ca.crt"
    leaf_key = _TLS_TMP / "leaf.key"
    leaf_csr = _TLS_TMP / "leaf.csr"
    leaf_crt = _TLS_TMP / "leaf.crt"
    leaf_ext = _TLS_TMP / "leaf.ext"

    _run_openssl(["genrsa", "-out", str(ca_key), "2048"])
    _run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "2",
            "-out",
            str(ca_crt),
            "-subj",
            "/CN=FTF-04A02 Test CA",
        ]
    )
    _run_openssl(["genrsa", "-out", str(leaf_key), "2048"])
    _run_openssl(
        ["req", "-new", "-key", str(leaf_key), "-out", str(leaf_csr), "-subj", "/CN=127.0.0.1"]
    )
    leaf_ext.write_text("subjectAltName=IP:127.0.0.1\n")
    _run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(leaf_crt),
            "-days",
            "2",
            "-sha256",
            "-extfile",
            str(leaf_ext),
        ]
    )

    subprocess.run(
        ["sudo", "cp", str(ca_crt), str(_CA_TRUST_TARGET)], check=True, capture_output=True
    )
    subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
    atexit.register(_remove_ca_trust)
    return leaf_crt, leaf_key


def _remove_ca_trust() -> None:
    subprocess.run(["sudo", "rm", "-f", str(_CA_TRUST_TARGET)], check=False, capture_output=True)
    subprocess.run(["sudo", "update-ca-certificates", "--fresh"], check=False, capture_output=True)


def _start_https_server(certfile: Path, keyfile: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GitHubHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


_SERVER: ThreadingHTTPServer | None = None
_BASE_URL: str | None = None
_PORT: int | None = None
if _UPDATE_CA_AVAILABLE:
    _leaf, _key = _generate_trusted_server_cert()
    _SERVER = _start_https_server(_leaf, _key)
    _PORT = _SERVER.server_address[1]
    _BASE_URL = f"https://127.0.0.1:{_PORT}"
else:
    print(
        "FTF-04A02: this sandbox has no openssl/update-ca-certificates/passwordless sudo -- the "
        "live TLS-backed checks are skipped and disclosed here; GitHub Actions' ubuntu-latest "
        "runner has all three."
    )


def _policy() -> ConnectionPolicy:
    assert _PORT is not None
    return ConnectionPolicy(
        name="test-github-connector",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=False,
    )


_MODULE_PATH = (
    ROOT
    / "apps"
    / "api"
    / "app"
    / "target_connectors"
    / "github_app_token_read_only_secret_integration.py"
)


# -- Tests --------------------------------------------------------------


def test_happy_path_public_repo() -> None:
    if _BASE_URL is None:
        return
    result = github_connector.validate_github_repository_access(
        "acme/good-repo",
        access_mode="read_only",
        token="valid-token-abc",
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A02",
        "acceptance: a real accessible public repository resolves with the exact fields "
        "GitHub's own response declared",
        result.repository_identity == "acme/good-repo"
        and result.resolved_full_name == "acme/good-repo"
        and result.private is False
        and result.default_branch == "main",
    )
    last_request = REQUEST_LOG[-1]
    last_headers = last_request["headers"]
    assert isinstance(last_headers, dict)
    check(
        "FTF-04A02",
        "acceptance: the real request carried a Bearer authorization header with the exact "
        "supplied token",
        last_headers.get("Authorization") == "Bearer valid-token-abc",
    )
    check(
        "FTF-04A02",
        "negative_acceptance: no write scope requested for analysis -- the real request that "
        "reached GitHub was a GET with no request body",
        last_request["method"] == "GET" and last_request.get("content_length") in (None, "0"),
    )


def test_happy_path_private_repo_within_installation() -> None:
    if _BASE_URL is None:
        return
    result = github_connector.validate_github_repository_access(
        "acme/private-good-repo",
        access_mode="read_only",
        token="valid-token-abc",
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A02",
        "acceptance: a real private repository within the installation/token's own grant "
        "resolves with private=True",
        result.private is True and result.default_branch == "trunk",
    )


def test_revoked_token_denies_access() -> None:
    if _BASE_URL is None:
        return
    denied = False
    try:
        github_connector.validate_github_repository_access(
            "acme/revoked-repo",
            access_mode="read_only",
            token="revoked-token-xyz",
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except github_connector.GitHubCredentialRevokedError:
        denied = True
    check(
        "FTF-04A02",
        "negative_acceptance: a revoked/invalid token (real HTTP 401) is denied, never treated "
        "as a valid connection",
        denied,
    )


def test_private_repo_outside_installation_denies_access() -> None:
    if _BASE_URL is None:
        return
    denied = False
    try:
        github_connector.validate_github_repository_access(
            "acme/outside-installation-repo",
            access_mode="read_only",
            token="valid-token-abc",
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except github_connector.GitHubRepositoryAccessDeniedError:
        denied = True
    check(
        "FTF-04A02",
        "negative_acceptance: a private repository outside the installation/token's own grant "
        "(real HTTP 404) is denied, never treated as a valid connection",
        denied,
    )


def test_generic_forbidden_denies_access_distinct_from_rate_limit() -> None:
    if _BASE_URL is None:
        return
    denied = False
    try:
        github_connector.validate_github_repository_access(
            "acme/forbidden-repo",
            access_mode="read_only",
            token="valid-token-abc",
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except github_connector.GitHubRepositoryAccessDeniedError:
        denied = True
    check(
        "FTF-04A02",
        "a genuine HTTP 403 that is not rate-limit exhaustion is a real access denial",
        denied,
    )


def test_rate_limit_exceeded_is_distinct_from_access_denied() -> None:
    if _BASE_URL is None:
        return
    rate_limited = False
    try:
        github_connector.validate_github_repository_access(
            "acme/ratelimited-repo",
            access_mode="read_only",
            token="valid-token-abc",
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except github_connector.GitHubRateLimitExceededError:
        rate_limited = True
    check(
        "FTF-04A02",
        "a real HTTP 403 with X-RateLimit-Remaining: 0 raises the distinct rate-limit error, "
        "not the generic access-denied error",
        rate_limited,
    )


def test_full_name_mismatch_is_rejected() -> None:
    if _BASE_URL is None:
        return
    rejected = False
    try:
        github_connector.validate_github_repository_access(
            "acme/mismatch-repo",
            access_mode="read_only",
            token="valid-token-abc",
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except github_connector.GitHubConnectionError:
        rejected = True
    check(
        "FTF-04A02",
        "a response whose own full_name does not match the requested repository identity is "
        "never accepted as a valid resolution",
        rejected,
    )


def test_missing_default_branch_is_rejected() -> None:
    if _BASE_URL is None:
        return
    rejected = False
    try:
        github_connector.validate_github_repository_access(
            "acme/no-branch-repo",
            access_mode="read_only",
            token="valid-token-abc",
            base_url=_BASE_URL,
            policy=_policy(),
        )
    except github_connector.GitHubConnectionError:
        rejected = True
    check(
        "FTF-04A02",
        "a response missing a real default_branch is never accepted as a valid resolution",
        rejected,
    )


def test_invalid_repository_identity_refused_before_any_request() -> None:
    before = len(REQUEST_LOG)
    refused = False
    try:
        github_connector.validate_github_repository_access(
            "not-a-valid-identity",
            access_mode="read_only",
            token="valid-token-abc",
            base_url=_BASE_URL or "https://127.0.0.1:1",
            policy=_policy() if _BASE_URL else None,
        )
    except github_connector.InvalidRepositoryIdentityError:
        refused = True
    check(
        "FTF-04A02",
        "a malformed repository_identity (no 'owner/repo' shape) is refused before any network "
        "request is made",
        refused and len(REQUEST_LOG) == before,
    )


def test_unsupported_access_mode_refused_before_any_request() -> None:
    before = len(REQUEST_LOG)
    refused = False
    try:
        github_connector.validate_github_repository_access(
            "acme/good-repo",
            access_mode="read_write",
            token="valid-token-abc",
            base_url=_BASE_URL or "https://127.0.0.1:1",
            policy=_policy() if _BASE_URL else None,
        )
    except github_connector.UnsupportedAccessModeError:
        refused = True
    check(
        "FTF-04A02",
        "an access_mode other than 'read_only' is refused before any network request is made",
        refused and len(REQUEST_LOG) == before,
    )


def test_blank_token_refused_before_any_request() -> None:
    before = len(REQUEST_LOG)
    refused = False
    try:
        github_connector.validate_github_repository_access(
            "acme/good-repo",
            access_mode="read_only",
            token="   ",
            base_url=_BASE_URL or "https://127.0.0.1:1",
            policy=_policy() if _BASE_URL else None,
        )
    except github_connector.GitHubCredentialRevokedError:
        refused = True
    check(
        "FTF-04A02",
        "a blank/missing token is refused before any network request is made",
        refused and len(REQUEST_LOG) == before,
    )


def test_destination_denied_by_policy() -> None:
    if _BASE_URL is None:
        return
    from packages.security.url_policy import OutboundPolicyError

    restrictive_policy = ConnectionPolicy(
        name="test-github-connector-deny-all",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset(),  # no port is ever allowed
        allow_redirects=False,
    )
    denied = False
    try:
        github_connector.validate_github_repository_access(
            "acme/good-repo",
            access_mode="read_only",
            token="valid-token-abc",
            base_url=_BASE_URL,
            policy=restrictive_policy,
        )
    except OutboundPolicyError:
        denied = True
    check(
        "FTF-04A02",
        "a destination outside the caller's own connection policy is denied by the shared "
        "outbound guard, never silently allowed",
        denied,
    )


_STATUS_CATCH_ALL_TARGET = b"    if response.status != 200:"


def _run_status_guard_fault_injection(
    *, criterion_label: str, repository_identity: str, token: str, expected_error_name: str
) -> None:
    """Shared fault-injection body: disables the one terminal
    `if response.status != 200:` guard that every earlier, more specific
    status branch (401/403/404) still funnels into if it does not itself
    already return/raise. This is the actual single load-bearing check for
    both "revoked token" and "private repo outside installation" deny
    access: each fixture response above deliberately also carries a
    plausible-looking `full_name`/`default_branch` alongside its own real
    non-200 status, so with this one guard removed, the response would
    otherwise parse as a fully valid, complete resolution -- a genuine
    fabricated success, not merely a different exception type."""
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    check(
        "FTF-04A02",
        "fault injection target snippet is present in the real source",
        _STATUS_CATCH_ALL_TARGET in original,
    )
    try:
        mutated = original.replace(
            _STATUS_CATCH_ALL_TARGET, b"    if False and response.status != 200:", 1
        )
        check("FTF-04A02", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(github_connector)
        fabricated_success = False
        try:
            github_connector.validate_github_repository_access(
                repository_identity,
                access_mode="read_only",
                token=token,
                base_url=_BASE_URL,
                policy=_policy(),
            )
            fabricated_success = True
        except getattr(github_connector, expected_error_name):
            fabricated_success = False
        except github_connector.GitHubConnectionError:
            fabricated_success = False
        check(
            "FTF-04A02",
            f"fault injection: disabling the terminal non-200 guard genuinely lets "
            f"{criterion_label} through as a fabricated success",
            fabricated_success,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(github_connector)
        check(
            "FTF-04A02",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_raises = False
        try:
            github_connector.validate_github_repository_access(
                repository_identity,
                access_mode="read_only",
                token=token,
                base_url=_BASE_URL,
                policy=_policy(),
            )
        except getattr(github_connector, expected_error_name):
            restored_raises = True
        check(
            "FTF-04A02",
            f"after restoring the file, {criterion_label} is genuinely denied again",
            restored_raises,
        )


def test_fault_injection_revoked_token_check_is_load_bearing() -> None:
    _run_status_guard_fault_injection(
        criterion_label="a revoked token",
        repository_identity="acme/revoked-repo",
        token="revoked-token-xyz",
        expected_error_name="GitHubCredentialRevokedError",
    )


def test_fault_injection_access_denied_check_is_load_bearing() -> None:
    _run_status_guard_fault_injection(
        criterion_label="a private repo outside installation",
        repository_identity="acme/outside-installation-repo",
        token="valid-token-abc",
        expected_error_name="GitHubRepositoryAccessDeniedError",
    )


def test_fault_injection_get_only_guarantee_is_load_bearing() -> None:
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    target = b'_HTTP_METHOD = "GET"'
    check(
        "FTF-04A02",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(target, b'_HTTP_METHOD = "POST"', 1)
        check("FTF-04A02", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(github_connector)
        still_succeeds = False
        try:
            github_connector.validate_github_repository_access(
                "acme/good-repo",
                access_mode="read_only",
                token="valid-token-abc",
                base_url=_BASE_URL,
                policy=_policy(),
            )
            still_succeeds = True
        except github_connector.GitHubConnectionError:
            still_succeeds = False
        check(
            "FTF-04A02",
            "negative_acceptance: hardcoding the real HTTP verb to a write-shaped method "
            "genuinely breaks against the real fixture server (which only ever implements "
            "do_GET), proving the GET-only guarantee is real and observable, not decorative",
            not still_succeeds,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(github_connector)
        check(
            "FTF-04A02",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_succeeds = False
        try:
            github_connector.validate_github_repository_access(
                "acme/good-repo",
                access_mode="read_only",
                token="valid-token-abc",
                base_url=_BASE_URL,
                policy=_policy(),
            )
            restored_succeeds = True
        except github_connector.GitHubConnectionError:
            restored_succeeds = False
        check(
            "FTF-04A02",
            "after restoring the file, the real GET request succeeds again",
            restored_succeeds,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A02 GitHub App/token read-only secret integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A02 GitHub App/token read-only secret integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
