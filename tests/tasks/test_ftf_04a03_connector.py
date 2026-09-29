#!/usr/bin/env python3
"""FTF-04A03 -- GitHub accessible-repository listing (connector-level,
no-DB unit/fault tests).

Same real-local-HTTPS-fixture-with-a-self-signed-CA-in-the-system-trust-
store pattern `tests/tasks/test_ftf_04a02.py` already established (this
sandbox has no route to the public internet, confirmed directly there) --
this suite builds and drives a real local HTTPS server implementing the
exact subset of the real GitHub REST API v3 `github_accessible_repository_
listing.py` calls (`GET /installation/repositories`), so every request
goes through `packages/security/outbound_guard.py`'s own real,
unmodified TLS/socket path -- never a mocked `guarded_fetch`.

Covers this task's own real pagination contract (a `Link: ...rel="next"`
response header, not an inferred item count), GitHub's own documented
401/403 semantics (revoked credential / rate limit exhaustion / other
forbidden), and two disclosed fault injections (Law 1) proving the
terminal non-200 guard and the GET-only structural guarantee are each
load-bearing.

Run directly: python3 tests/tasks/test_ftf_04a03_connector.py
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
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.security.url_policy import ConnectionPolicy  # noqa: E402

import app.target_connectors.github_accessible_repository_listing as listing  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local HTTPS `GET /installation/repositories`-shaped fixture -------

REQUEST_LOG: list[dict[str, object]] = []

_GOOD_REPOS_PAGE_1 = {
    "total_count": 3,
    "repositories": [
        {"full_name": "acme/repo-one", "private": False, "default_branch": "main"},
        {"full_name": "acme/repo-two", "private": True, "default_branch": "trunk"},
    ],
}
_GOOD_REPOS_PAGE_2 = {
    "total_count": 3,
    "repositories": [
        {"full_name": "acme/repo-three", "private": False, "default_branch": "main"},
    ],
}


class _GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        auth = self.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        REQUEST_LOG.append(
            {
                "method": "GET",
                "path": self.path,
                "token": token,
                "content_length": self.headers.get("Content-Length"),
            }
        )
        if not self.path.startswith("/installation/repositories"):
            self._respond(404, b'{"message": "Not Found"}')
            return

        if token == "revoked-token":
            # A real GitHub 401 body is just `{"message": "Bad credentials"}` --
            # this fixture deliberately also includes a plausible-looking
            # `repositories` array alongside the real 401 status (the same
            # deliberate fault-injection fixture design
            # `tests/tasks/test_ftf_04a02.py` already established), so this
            # suite's own fault-injection test can prove that the status
            # check itself -- not the response-shape checks -- is what denies
            # access here.
            body = {
                "message": "Bad credentials",
                "total_count": 1,
                "repositories": [
                    {
                        "full_name": "acme/should-never-be-listed",
                        "private": True,
                        "default_branch": "main",
                    }
                ],
            }
            self._respond(401, _json.dumps(body).encode())
            return
        if token == "ratelimited-token":
            self._respond(
                403,
                _json.dumps({"message": "API rate limit exceeded"}).encode(),
                extra_headers={"X-RateLimit-Remaining": "0"},
            )
            return
        if token == "forbidden-token":
            self._respond(
                403,
                _json.dumps({"message": "Resource not accessible by integration"}).encode(),
                extra_headers={"X-RateLimit-Remaining": "42"},
            )
            return
        if token == "malformed-token":
            self._respond(200, _json.dumps({"total_count": 0}).encode())
            return
        if token == "bad-entry-token":
            body = {
                "total_count": 1,
                "repositories": [{"full_name": "acme/incomplete-repo", "private": False}],
            }
            self._respond(200, _json.dumps(body).encode())
            return

        query = urllib.parse.urlsplit(self.path).query
        params = urllib.parse.parse_qs(query)
        if params.get("page") == ["2"]:
            self._respond(200, _json.dumps(_GOOD_REPOS_PAGE_2).encode())
            return
        self._respond(
            200,
            _json.dumps(_GOOD_REPOS_PAGE_1).encode(),
            extra_headers={
                "Link": (
                    '<https://example.invalid/installation/repositories?page=2>; rel="next", '
                    '<https://example.invalid/installation/repositories?page=2>; rel="last"'
                )
            },
        )

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


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-04a03-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-04a03-test-ca.crt")
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
            "/CN=FTF-04A03 Test CA",
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
        "FTF-04A03: this sandbox has no openssl/update-ca-certificates/passwordless sudo -- the "
        "live TLS-backed checks are skipped and disclosed here; GitHub Actions' ubuntu-latest "
        "runner has all three."
    )


def _policy() -> ConnectionPolicy:
    assert _PORT is not None
    return ConnectionPolicy(
        name="test-github-repository-listing",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=False,
    )


_MODULE_PATH = (
    ROOT / "apps" / "api" / "app" / "target_connectors" / "github_accessible_repository_listing.py"
)


# -- Tests, real network path -------------------------------------------


def test_first_page_reports_a_real_next_page() -> None:
    if _BASE_URL is None:
        return
    page = listing.list_accessible_repositories(
        token="valid-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
    )
    check(
        "FTF-04A03",
        "acceptance: page 1 renders exactly the real repositories GitHub's own fixture "
        "response declared, in order",
        [r.repository_identity for r in page.repositories] == ["acme/repo-one", "acme/repo-two"],
    )
    check("FTF-04A03", "acceptance: real pagination metadata (page)", page.page == 1)
    check("FTF-04A03", "acceptance: real pagination metadata (per_page)", page.per_page == 2)
    check(
        "FTF-04A03",
        "acceptance: has_next_page is derived from the real Link header, not the item count",
        page.has_next_page is True,
    )
    check(
        "FTF-04A03",
        "acceptance: repository visibility (private) is the real field GitHub returned",
        page.repositories[1].private is True,
    )


def test_last_page_reports_no_further_page() -> None:
    if _BASE_URL is None:
        return
    page = listing.list_accessible_repositories(
        token="valid-token", page=2, per_page=2, base_url=_BASE_URL, policy=_policy()
    )
    check(
        "FTF-04A03",
        "acceptance: the real final page contains only the one remaining repository",
        [r.repository_identity for r in page.repositories] == ["acme/repo-three"],
    )
    check(
        "FTF-04A03",
        'acceptance: has_next_page is real-false on the final page (no Link: rel="next")',
        page.has_next_page is False,
    )


def test_get_only_no_request_body() -> None:
    if _BASE_URL is None:
        return
    REQUEST_LOG.clear()
    listing.list_accessible_repositories(
        token="valid-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
    )
    last = REQUEST_LOG[-1]
    check(
        "FTF-04A03",
        "negative_acceptance: no write scope requested -- the real request was a GET with no "
        "body",
        last["method"] == "GET" and last.get("content_length") in (None, "0"),
    )


def test_revoked_token_is_denied() -> None:
    if _BASE_URL is None:
        return
    try:
        listing.list_accessible_repositories(
            token="revoked-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
        )
        raised = False
    except listing.GitHubCredentialRevokedError:
        raised = True
    check(
        "FTF-04A03",
        "negative_acceptance: a real revoked credential (HTTP 401) is denied, never listed",
        raised,
    )


def test_rate_limit_is_distinguished_from_forbidden() -> None:
    if _BASE_URL is None:
        return
    try:
        listing.list_accessible_repositories(
            token="ratelimited-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
        )
        rate_limited_raised = False
    except listing.GitHubRateLimitExceededError:
        rate_limited_raised = True
    check(
        "FTF-04A03",
        "acceptance: a real exhausted rate limit (HTTP 403, X-RateLimit-Remaining: 0) is its "
        "own distinct, transient error",
        rate_limited_raised,
    )
    try:
        listing.list_accessible_repositories(
            token="forbidden-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
        )
        forbidden_raised = False
    except listing.GitHubConnectionError as exc:
        forbidden_raised = not isinstance(exc, listing.GitHubRateLimitExceededError)
    check(
        "FTF-04A03",
        "acceptance: a real non-rate-limited HTTP 403 is denied as a distinct, non-transient "
        "error",
        forbidden_raised,
    )


def test_malformed_and_incomplete_responses_are_rejected() -> None:
    if _BASE_URL is None:
        return
    try:
        listing.list_accessible_repositories(
            token="malformed-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
        )
        malformed_rejected = False
    except listing.GitHubConnectionError:
        malformed_rejected = True
    check(
        "FTF-04A03",
        "a response missing the real 'repositories' array is never fabricated into an empty "
        "success",
        malformed_rejected,
    )
    try:
        listing.list_accessible_repositories(
            token="bad-entry-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
        )
        incomplete_rejected = False
    except listing.GitHubConnectionError:
        incomplete_rejected = True
    check(
        "FTF-04A03",
        "a repository entry missing default_branch is never silently accepted",
        incomplete_rejected,
    )


# -- Tests, pure validation (no network) ---------------------------------


def test_pagination_parameters_validated_before_any_request() -> None:
    for bad_page in (0, -1, "1", True):
        try:
            listing.list_accessible_repositories(
                token="valid-token",
                page=bad_page,  # type: ignore[arg-type]
                per_page=2,
            )
            rejected = False
        except listing.InvalidPaginationError:
            rejected = True
        check(
            "FTF-04A03",
            f"negative_acceptance: page={bad_page!r} is refused before any network request",
            rejected,
        )
    for bad_per_page in (0, 101, -5, "30"):
        try:
            listing.list_accessible_repositories(
                token="valid-token",
                page=1,
                per_page=bad_per_page,  # type: ignore[arg-type]
            )
            rejected = False
        except listing.InvalidPaginationError:
            rejected = True
        check(
            "FTF-04A03",
            f"negative_acceptance: per_page={bad_per_page!r} is refused before any network "
            "request",
            rejected,
        )


def test_missing_token_is_refused_before_any_request() -> None:
    for bad_token in ("", "   ", None):
        try:
            listing.list_accessible_repositories(
                token=bad_token,  # type: ignore[arg-type]
                page=1,
                per_page=2,
            )
            rejected = False
        except listing.GitHubCredentialRevokedError:
            rejected = True
        check(
            "FTF-04A03",
            f"negative_acceptance: token={bad_token!r} is refused before any network request",
            rejected,
        )


# -- Fault injections (Law 1) --------------------------------------------


def test_fault_injection_terminal_status_guard_is_load_bearing() -> None:
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    target = b"    if response.status != 200:\n        _raise_for_non_200_status(response)"
    check(
        "FTF-04A03",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        disabled = (
            b"    if False and response.status != 200:\n        _raise_for_non_200_status(response)"
        )
        mutated = original.replace(target, disabled, 1)
        check("FTF-04A03", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(listing)
        fabricated_success = False
        try:
            listing.list_accessible_repositories(
                token="revoked-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
            )
            fabricated_success = True
        except listing.GitHubConnectionError:
            fabricated_success = False
        check(
            "FTF-04A03",
            "fault injection: disabling the terminal non-200 guard genuinely lets a revoked "
            "credential's response through as a fabricated success",
            fabricated_success,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(listing)
        check(
            "FTF-04A03",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_raises = False
        try:
            listing.list_accessible_repositories(
                token="revoked-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
            )
        except listing.GitHubCredentialRevokedError:
            restored_raises = True
        check(
            "FTF-04A03",
            "after restoring the file, the revoked credential is genuinely denied again",
            restored_raises,
        )


def test_fault_injection_get_only_guarantee_is_load_bearing() -> None:
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    target = b'_HTTP_METHOD = "GET"'
    check(
        "FTF-04A03",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(target, b'_HTTP_METHOD = "POST"', 1)
        check("FTF-04A03", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(listing)
        still_succeeds = False
        try:
            listing.list_accessible_repositories(
                token="valid-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
            )
            still_succeeds = True
        except listing.GitHubConnectionError:
            still_succeeds = False
        check(
            "FTF-04A03",
            "negative_acceptance: hardcoding the real HTTP verb to a write-shaped method "
            "genuinely breaks against the real fixture server (which only ever implements "
            "do_GET), proving the GET-only guarantee is real and observable, not decorative",
            not still_succeeds,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(listing)
        check(
            "FTF-04A03",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_succeeds = False
        try:
            listing.list_accessible_repositories(
                token="valid-token", page=1, per_page=2, base_url=_BASE_URL, policy=_policy()
            )
            restored_succeeds = True
        except listing.GitHubConnectionError:
            restored_succeeds = False
        check(
            "FTF-04A03",
            "after restoring the file, the real GET request succeeds again",
            restored_succeeds,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A03 GitHub accessible-repository listing (connector): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A03 GitHub accessible-repository listing (connector): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
