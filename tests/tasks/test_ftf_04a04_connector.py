#!/usr/bin/env python3
"""FTF-04A04 -- GitHub branch listing + immutable ref resolution
(connector-level, no-DB unit/fault tests).

Same real-local-HTTPS-fixture-with-a-self-signed-CA-in-the-system-trust-
store pattern `tests/tasks/test_ftf_04a03_connector.py` already established
(this sandbox has no route to the public internet, confirmed directly
there) -- this suite builds and drives a real local HTTPS server
implementing the exact subset of the real GitHub REST API v3
`github_branch_ref_resolver.py` calls (`GET /repos/{owner}/{repo}/branches`,
`GET /repos/{owner}/{repo}/commits/{ref}`), so every request goes through
`packages/security/outbound_guard.py`'s own real, unmodified TLS/socket
path -- never a mocked `guarded_fetch`.

Covers this task's own real pagination contract for branch listing (a
`Link: ...rel="next"` response header), the real "immutable SHA resolver"
contract (a fresh `GET .../commits/{ref}` call per resolution, proven to
return the *current* tip rather than a cached list-time value), GitHub's
own documented 401/403/404/422 semantics, and disclosed fault injections
(Law 1) proving the terminal non-200 guard and the GET-only structural
guarantee are each load-bearing.

Run directly: python3 tests/tasks/test_ftf_04a04_connector.py
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

import app.target_connectors.github_branch_ref_resolver as resolver  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local HTTPS `branches`/`commits/{ref}`-shaped fixture -------------

REQUEST_LOG: list[dict[str, object]] = []

# The branch's own current tip is deliberately *different* from what the
# branch-listing fixture reports for `main`, so a test that mistakenly
# trusted the list-time SHA as the resolved identity would genuinely fail.
_MAIN_LIST_TIME_SHA = "1111111111111111111111111111111111111111"
_MAIN_CURRENT_SHA = "2222222222222222222222222222222222222222"

_BRANCHES_PAGE_1 = [
    {"name": "main", "commit": {"sha": _MAIN_LIST_TIME_SHA}, "protected": True},
    {"name": "develop", "commit": {"sha": "3" * 40}, "protected": False},
]
_BRANCHES_PAGE_2 = [
    {"name": "release/1.0", "commit": {"sha": "4" * 40}, "protected": False},
]


class _GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        auth = self.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        parsed = urllib.parse.urlsplit(self.path)
        REQUEST_LOG.append(
            {
                "method": "GET",
                "path": self.path,
                "token": token,
                "content_length": self.headers.get("Content-Length"),
            }
        )

        if token == "revoked-token":
            # A real GitHub 401 body is just `{"message": "Bad credentials"}` --
            # this fixture deliberately also includes a plausible-looking
            # `sha` alongside the real 401 status (the same deliberate
            # fault-injection fixture design `test_ftf_04a03_connector.py`
            # already established), so this suite's own fault-injection
            # test can prove that the status check itself -- not the
            # response-shape checks -- is what denies access here.
            self._respond(
                401,
                _json.dumps({"message": "Bad credentials", "sha": "f" * 40}).encode(),
            )
            return
        if token == "ratelimited-token":
            self._respond(
                403,
                _json.dumps({"message": "API rate limit exceeded"}).encode(),
                extra_headers={"X-RateLimit-Remaining": "0"},
            )
            return

        if parsed.path == "/repos/acme/agent-repo/branches":
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("page") == ["2"]:
                self._respond(200, _json.dumps(_BRANCHES_PAGE_2).encode())
                return
            self._respond(
                200,
                _json.dumps(_BRANCHES_PAGE_1).encode(),
                extra_headers={
                    "Link": (
                        "<https://example.invalid/repos/acme/agent-repo/branches?page=2>; "
                        'rel="next"'
                    )
                },
            )
            return

        if parsed.path == "/repos/acme/agent-repo/commits/main":
            # The real, current tip -- distinct from the stale list-time
            # SHA above, proving `resolve_ref_to_commit` genuinely
            # re-resolves rather than trusting a cached value.
            self._respond(200, _json.dumps({"sha": _MAIN_CURRENT_SHA}).encode())
            return
        if parsed.path == "/repos/acme/agent-repo/commits/release%2F1.0":
            # Confirms the slash in a branch name is percent-encoded
            # before being sent, per GitHub's own documented requirement.
            self._respond(200, _json.dumps({"sha": "5" * 40}).encode())
            return
        if parsed.path == "/repos/acme/agent-repo/commits/does-not-exist":
            self._respond(404, _json.dumps({"message": "No commit found for the ref"}).encode())
            return
        if parsed.path == "/repos/acme/agent-repo/commits/ambiguous-ref":
            self._respond(422, _json.dumps({"message": "Unprocessable Entity"}).encode())
            return
        if parsed.path == "/repos/acme/agent-repo/commits/malformed-response":
            self._respond(200, _json.dumps({"no_sha_here": True}).encode())
            return

        self._respond(404, b'{"message": "Not Found"}')

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


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-04a04-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-04a04-test-ca.crt")
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
            "/CN=FTF-04A04 Test CA",
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
        "FTF-04A04: this sandbox has no openssl/update-ca-certificates/passwordless sudo -- the "
        "live TLS-backed checks are skipped and disclosed here; GitHub Actions' ubuntu-latest "
        "runner has all three."
    )


def _policy() -> ConnectionPolicy:
    assert _PORT is not None
    return ConnectionPolicy(
        name="test-github-branch-ref-resolver",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=False,
    )


_MODULE_PATH = ROOT / "apps" / "api" / "app" / "target_connectors" / "github_branch_ref_resolver.py"

_REPO = "acme/agent-repo"


# -- Tests, real network path -------------------------------------------


def test_first_page_reports_a_real_next_page() -> None:
    if _BASE_URL is None:
        return
    page = resolver.list_branches(
        token="valid-token",
        repository_identity=_REPO,
        page=1,
        per_page=2,
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A04",
        "acceptance: page 1 renders exactly the real branches GitHub's own fixture declared, "
        "in order",
        [b.name for b in page.branches] == ["main", "develop"],
    )
    check(
        "FTF-04A04",
        "acceptance: each branch carries its own real list-time commit sha, for orientation only",
        page.branches[0].list_time_commit_sha == _MAIN_LIST_TIME_SHA,
    )
    check(
        "FTF-04A04",
        "acceptance: has_next_page is derived from the real Link header",
        page.has_next_page is True,
    )


def test_last_page_reports_no_further_page() -> None:
    if _BASE_URL is None:
        return
    page = resolver.list_branches(
        token="valid-token",
        repository_identity=_REPO,
        page=2,
        per_page=2,
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A04",
        "acceptance: the real final page contains only the one remaining branch",
        [b.name for b in page.branches] == ["release/1.0"],
    )
    check(
        "FTF-04A04",
        "acceptance: has_next_page is real-false on the final page",
        page.has_next_page is False,
    )


def test_resolve_ref_returns_the_real_current_tip_not_the_stale_list_time_sha() -> None:
    if _BASE_URL is None:
        return
    resolved = resolver.resolve_ref_to_commit(
        token="valid-token",
        repository_identity=_REPO,
        ref="main",
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A04",
        "acceptance: resolve_ref_to_commit returns the real current tip",
        resolved.resolved_commit_sha == _MAIN_CURRENT_SHA,
    )
    check(
        "FTF-04A04",
        "negative_acceptance: the resolved SHA is never the stale list-time SHA -- 'branch "
        "advancing between list/read does not mix commits'",
        resolved.resolved_commit_sha != _MAIN_LIST_TIME_SHA,
    )


def test_resolve_ref_percent_encodes_slashes_in_branch_names() -> None:
    if _BASE_URL is None:
        return
    resolved = resolver.resolve_ref_to_commit(
        token="valid-token",
        repository_identity=_REPO,
        ref="release/1.0",
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A04",
        "acceptance: a branch name containing a slash is resolved via its real, percent-encoded "
        "ref path",
        resolved.resolved_commit_sha == "5" * 40 and resolved.ref == "release/1.0",
    )


def test_unknown_ref_is_blocked() -> None:
    if _BASE_URL is None:
        return
    try:
        resolver.resolve_ref_to_commit(
            token="valid-token",
            repository_identity=_REPO,
            ref="does-not-exist",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        raised = False
    except resolver.GitHubRepositoryAccessDeniedError:
        raised = True
    check(
        "FTF-04A04",
        "negative_acceptance: an unknown ref (real HTTP 404) is blocked, never a fabricated "
        "resolution",
        raised,
    )


def test_ambiguous_ref_is_blocked() -> None:
    if _BASE_URL is None:
        return
    try:
        resolver.resolve_ref_to_commit(
            token="valid-token",
            repository_identity=_REPO,
            ref="ambiguous-ref",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        raised = False
    except resolver.GitHubRepositoryAccessDeniedError:
        raised = True
    check(
        "FTF-04A04",
        "negative_acceptance: a real HTTP 422 (unresolvable ref) is blocked, never a fabricated "
        "resolution",
        raised,
    )


def test_revoked_token_is_denied_for_both_operations() -> None:
    if _BASE_URL is None:
        return
    try:
        resolver.list_branches(
            token="revoked-token",
            repository_identity=_REPO,
            base_url=_BASE_URL,
            policy=_policy(),
        )
        list_raised = False
    except resolver.GitHubCredentialRevokedError:
        list_raised = True
    check(
        "FTF-04A04", "negative_acceptance: a revoked credential denies branch listing", list_raised
    )

    try:
        resolver.resolve_ref_to_commit(
            token="revoked-token",
            repository_identity=_REPO,
            ref="main",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        resolve_raised = False
    except resolver.GitHubCredentialRevokedError:
        resolve_raised = True
    check(
        "FTF-04A04",
        "negative_acceptance: a revoked credential denies ref resolution",
        resolve_raised,
    )


def test_rate_limit_is_distinguished_from_other_denials() -> None:
    if _BASE_URL is None:
        return
    try:
        resolver.resolve_ref_to_commit(
            token="ratelimited-token",
            repository_identity=_REPO,
            ref="main",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        raised = False
    except resolver.GitHubRateLimitExceededError:
        raised = True
    check(
        "FTF-04A04",
        "acceptance: a real exhausted rate limit is its own distinct, transient error for ref "
        "resolution too",
        raised,
    )


def test_malformed_responses_are_rejected() -> None:
    if _BASE_URL is None:
        return
    try:
        resolver.resolve_ref_to_commit(
            token="valid-token",
            repository_identity=_REPO,
            ref="malformed-response",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        rejected = False
    except resolver.GitHubConnectionError:
        rejected = True
    check(
        "FTF-04A04",
        "a commit response missing sha is never fabricated into a successful resolution",
        rejected,
    )


def test_get_only_no_request_body() -> None:
    if _BASE_URL is None:
        return
    REQUEST_LOG.clear()
    resolver.resolve_ref_to_commit(
        token="valid-token",
        repository_identity=_REPO,
        ref="main",
        base_url=_BASE_URL,
        policy=_policy(),
    )
    last = REQUEST_LOG[-1]
    check(
        "FTF-04A04",
        "negative_acceptance: no write scope requested -- the real request was a GET with no body",
        last["method"] == "GET" and last.get("content_length") in (None, "0"),
    )


# -- Tests, pure validation (no network) ---------------------------------


def test_invalid_repository_identity_is_refused_before_any_request() -> None:
    for bad_identity in ("", "no-slash", "/leading-slash", "a/b/c", "../escape"):
        try:
            resolver.resolve_ref_to_commit(
                token="valid-token", repository_identity=bad_identity, ref="main"
            )
            rejected = False
        except resolver.InvalidRepositoryIdentityError:
            rejected = True
        check(
            "FTF-04A04",
            f"negative_acceptance: repository_identity={bad_identity!r} is refused before any "
            "network request",
            rejected,
        )


def test_invalid_ref_is_refused_before_any_request() -> None:
    for bad_ref in ("", "   ", "a" * 300, "has space", "a..b"):
        try:
            resolver.resolve_ref_to_commit(
                token="valid-token", repository_identity=_REPO, ref=bad_ref
            )
            rejected = False
        except resolver.InvalidRefError:
            rejected = True
        check(
            "FTF-04A04",
            f"negative_acceptance: ref={bad_ref!r} is refused before any network request",
            rejected,
        )


def test_invalid_pagination_is_refused_before_any_request() -> None:
    for bad_page in (0, -1, "1", True):
        try:
            resolver.list_branches(
                token="valid-token",
                repository_identity=_REPO,
                page=bad_page,  # type: ignore[arg-type]
            )
            rejected = False
        except resolver.InvalidPaginationError:
            rejected = True
        check(
            "FTF-04A04",
            f"negative_acceptance: page={bad_page!r} is refused before any network request",
            rejected,
        )


def test_missing_token_is_refused_before_any_request() -> None:
    for bad_token in ("", "   ", None):
        try:
            resolver.resolve_ref_to_commit(
                token=bad_token,  # type: ignore[arg-type]
                repository_identity=_REPO,
                ref="main",
            )
            rejected = False
        except resolver.GitHubCredentialRevokedError:
            rejected = True
        check(
            "FTF-04A04",
            f"negative_acceptance: token={bad_token!r} is refused before any network request",
            rejected,
        )


# -- Fault injections (Law 1) --------------------------------------------


def test_fault_injection_terminal_status_guard_is_load_bearing() -> None:
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    target = (
        b"    if response.status != 200:\n"
        b"        _raise_for_non_200_status(\n"
        b'            response, context=f"resolving ref {validated_ref!r} in {identity!r}"\n'
        b"        )"
    )
    check(
        "FTF-04A04",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        disabled = (
            b"    if False and response.status != 200:\n"
            b"        _raise_for_non_200_status(\n"
            b'            response, context=f"resolving ref {validated_ref!r} in {identity!r}"\n'
            b"        )"
        )
        mutated = original.replace(target, disabled, 1)
        check("FTF-04A04", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(resolver)
        fabricated_success = False
        try:
            resolver.resolve_ref_to_commit(
                token="revoked-token",
                repository_identity=_REPO,
                ref="main",
                base_url=_BASE_URL,
                policy=_policy(),
            )
            fabricated_success = True
        except resolver.GitHubConnectionError:
            fabricated_success = False
        check(
            "FTF-04A04",
            "fault injection: disabling the terminal non-200 guard genuinely lets a revoked "
            "credential's response through as a fabricated success",
            fabricated_success,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(resolver)
        check(
            "FTF-04A04",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_raises = False
        try:
            resolver.resolve_ref_to_commit(
                token="revoked-token",
                repository_identity=_REPO,
                ref="main",
                base_url=_BASE_URL,
                policy=_policy(),
            )
        except resolver.GitHubCredentialRevokedError:
            restored_raises = True
        check(
            "FTF-04A04",
            "after restoring the file, the revoked credential is genuinely denied again",
            restored_raises,
        )


def test_fault_injection_get_only_guarantee_is_load_bearing() -> None:
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    target = b'_HTTP_METHOD = "GET"'
    check(
        "FTF-04A04",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(target, b'_HTTP_METHOD = "POST"', 1)
        check("FTF-04A04", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(resolver)
        still_succeeds = False
        try:
            resolver.resolve_ref_to_commit(
                token="valid-token",
                repository_identity=_REPO,
                ref="main",
                base_url=_BASE_URL,
                policy=_policy(),
            )
            still_succeeds = True
        except resolver.GitHubConnectionError:
            still_succeeds = False
        check(
            "FTF-04A04",
            "negative_acceptance: hardcoding the real HTTP verb to a write-shaped method "
            "genuinely breaks against the real fixture server (which only ever implements "
            "do_GET), proving the GET-only guarantee is real and observable",
            not still_succeeds,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(resolver)
        check(
            "FTF-04A04",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_succeeds = False
        try:
            resolver.resolve_ref_to_commit(
                token="valid-token",
                repository_identity=_REPO,
                ref="main",
                base_url=_BASE_URL,
                policy=_policy(),
            )
            restored_succeeds = True
        except resolver.GitHubConnectionError:
            restored_succeeds = False
        check(
            "FTF-04A04",
            "after restoring the file, the real GET request succeeds again",
            restored_succeeds,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A04 GitHub branch/ref resolver (connector): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A04 GitHub branch/ref resolver (connector): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
