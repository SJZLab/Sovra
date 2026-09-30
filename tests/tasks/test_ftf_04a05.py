#!/usr/bin/env python3
"""FTF-04A05 -- GitHub source snapshot capture (connector-level, no-DB
unit/fault tests).

Same real-local-HTTPS-fixture-with-a-self-signed-CA-in-the-system-trust-
store pattern `tests/tasks/test_ftf_04a04_connector.py` already established
(this sandbox has no route to the public internet) -- this suite builds
and drives a real local HTTPS server implementing the exact subset of the
real GitHub REST API v3 `github_source_snapshot_resolver.py` calls
(`GET .../commits/{ref}` reused from FTF-04A04, `GET .../git/commits/{sha}`,
`GET .../git/trees/{tree_sha}?recursive=1`, `GET .../git/blobs/{sha}`), so
every request goes through `packages/security/outbound_guard.py`'s own
real, unmodified TLS/socket path -- never a mocked `guarded_fetch`.

Covers this task's own real acceptance ("Snapshot exact commit tree and
file hashes, recording LFS/submodules/truncated listing completion") and
both negative_acceptance halves ("missing critical objects" -- a real
`truncated: true` response -- and "unsafe archives" -- a real tree entry
whose path escapes the repository root -- each genuinely blocks rather
than proceeding with partial evidence), plus a disclosed fault injection
(Law 1) proving the GET-only structural guarantee is real and observable.

Run directly: python3 tests/tasks/test_ftf_04a05.py
"""

from __future__ import annotations

import atexit
import base64
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

import app.target_connectors.github_source_snapshot_resolver as capture_module  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local HTTPS GitHub-shaped fixture ---------------------------------

_REPO = "acme/agent-repo"

_MAIN_COMMIT_SHA = "9" * 40
_MAIN_TREE_SHA = "8" * 40
_GITATTRIBUTES_BLOB_SHA = "7" * 40
_README_BLOB_SHA = "1" * 40
_SUBMODULE_COMMIT_SHA = "2" * 40
_LFS_BLOB_SHA = "3" * 40

_TRUNCATED_COMMIT_SHA = "a" * 40
_TRUNCATED_TREE_SHA = "b" * 40

_UNSAFE_COMMIT_SHA = "c" * 40
_UNSAFE_TREE_SHA = "d" * 40

_NO_TREE_COMMIT_SHA = "e" * 40

_GITATTRIBUTES_CONTENT = b"*.bin filter=lfs diff=lfs merge=lfs -text\n# a comment\nREADME.md text\n"

_MAIN_TREE_ENTRIES = [
    {"path": "README.md", "type": "blob", "mode": "100644", "sha": _README_BLOB_SHA, "size": 123},
    {
        "path": "vendor/lib",
        "type": "commit",
        "mode": "160000",
        "sha": _SUBMODULE_COMMIT_SHA,
    },
    {
        "path": ".gitattributes",
        "type": "blob",
        "mode": "100644",
        "sha": _GITATTRIBUTES_BLOB_SHA,
        "size": len(_GITATTRIBUTES_CONTENT),
    },
    {
        "path": "models/weights.bin",
        "type": "blob",
        "mode": "100644",
        "sha": _LFS_BLOB_SHA,
        "size": 999,
    },
]

_COMMIT_TO_REF = {
    "main": _MAIN_COMMIT_SHA,
    "truncated-ref": _TRUNCATED_COMMIT_SHA,
    "unsafe-ref": _UNSAFE_COMMIT_SHA,
    "no-tree-ref": _NO_TREE_COMMIT_SHA,
}

_COMMIT_TO_TREE = {
    _MAIN_COMMIT_SHA: _MAIN_TREE_SHA,
    _TRUNCATED_COMMIT_SHA: _TRUNCATED_TREE_SHA,
    _UNSAFE_COMMIT_SHA: _UNSAFE_TREE_SHA,
    # _NO_TREE_COMMIT_SHA deliberately absent -- fixture returns no tree key
}


class _GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        auth = self.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        path = self.path

        if token == "revoked-token":
            self._respond(401, _json.dumps({"message": "Bad credentials"}).encode())
            return

        prefix = f"/repos/{_REPO}"

        for ref, sha in _COMMIT_TO_REF.items():
            if path == f"{prefix}/commits/{ref}":
                self._respond(200, _json.dumps({"sha": sha}).encode())
                return

        if path == f"{prefix}/commits/{_MAIN_COMMIT_SHA}":
            # GitHub's own real contract: resolving a ref that is already a
            # full commit SHA returns that same SHA.
            self._respond(200, _json.dumps({"sha": _MAIN_COMMIT_SHA}).encode())
            return

        if path.startswith(f"{prefix}/git/commits/"):
            commit_sha = path.removeprefix(f"{prefix}/git/commits/")
            if commit_sha == _NO_TREE_COMMIT_SHA:
                self._respond(200, _json.dumps({"sha": commit_sha}).encode())
                return
            tree_sha = _COMMIT_TO_TREE.get(commit_sha)
            if tree_sha is None:
                self._respond(404, _json.dumps({"message": "No commit found"}).encode())
                return
            self._respond(200, _json.dumps({"sha": commit_sha, "tree": {"sha": tree_sha}}).encode())
            return

        if path.startswith(f"{prefix}/git/trees/"):
            tree_sha = path.removeprefix(f"{prefix}/git/trees/").split("?", 1)[0]
            if tree_sha == _MAIN_TREE_SHA:
                self._respond(
                    200,
                    _json.dumps(
                        {"sha": tree_sha, "tree": _MAIN_TREE_ENTRIES, "truncated": False}
                    ).encode(),
                )
                return
            if tree_sha == _TRUNCATED_TREE_SHA:
                self._respond(
                    200,
                    _json.dumps(
                        {"sha": tree_sha, "tree": _MAIN_TREE_ENTRIES, "truncated": True}
                    ).encode(),
                )
                return
            if tree_sha == _UNSAFE_TREE_SHA:
                unsafe_entries = [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "mode": "100644",
                        "sha": "4" * 40,
                        "size": 1,
                    },
                    {
                        "path": "../../etc/passwd",
                        "type": "blob",
                        "mode": "100644",
                        "sha": "5" * 40,
                        "size": 1,
                    },
                ]
                self._respond(
                    200,
                    _json.dumps(
                        {"sha": tree_sha, "tree": unsafe_entries, "truncated": False}
                    ).encode(),
                )
                return
            self._respond(404, _json.dumps({"message": "No tree found"}).encode())
            return

        if path.startswith(f"{prefix}/git/blobs/"):
            blob_sha = path.removeprefix(f"{prefix}/git/blobs/")
            if blob_sha == _GITATTRIBUTES_BLOB_SHA:
                encoded = base64.b64encode(_GITATTRIBUTES_CONTENT).decode("ascii")
                self._respond(
                    200,
                    _json.dumps(
                        {"sha": blob_sha, "content": encoded, "encoding": "base64"}
                    ).encode(),
                )
                return
            self._respond(404, _json.dumps({"message": "No blob found"}).encode())
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


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-04a05-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-04a05-test-ca.crt")
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
            "/CN=FTF-04A05 Test CA",
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
        "FTF-04A05: this sandbox has no openssl/update-ca-certificates/passwordless sudo -- the "
        "live TLS-backed checks are skipped and disclosed here; GitHub Actions' ubuntu-latest "
        "runner has all three."
    )


def _policy() -> ConnectionPolicy:
    assert _PORT is not None
    return ConnectionPolicy(
        name="test-github-source-snapshot-resolver",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=False,
    )


_MODULE_PATH = (
    ROOT / "apps" / "api" / "app" / "target_connectors" / "github_source_snapshot_resolver.py"
)


# -- Tests, real network path -------------------------------------------


def test_capture_returns_real_commit_tree_and_file_hashes() -> None:
    if _BASE_URL is None:
        return
    capture = capture_module.capture_github_source_snapshot(
        token="valid-token",
        repository_identity=_REPO,
        ref="main",
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A05",
        "acceptance: capture_method is the real closed-set value TargetSourceSnapshot accepts",
        capture.capture_method == "github_resolved_commit",
    )
    check(
        "FTF-04A05",
        "acceptance: commit_sha is the real, resolved commit (from GET .../commits/{ref})",
        capture.commit_sha == _MAIN_COMMIT_SHA,
    )
    check(
        "FTF-04A05",
        "acceptance: tree_hash is GitHub's own real, canonical Git tree object id",
        capture.tree_hash == _MAIN_TREE_SHA,
    )
    check(
        "FTF-04A05",
        "acceptance: branch records the real display ref used to resolve this commit",
        capture.branch == "main",
    )
    check(
        "FTF-04A05",
        "acceptance: the real tree entries (file hashes) are recorded in full",
        len(capture.tree_entries) == len(_MAIN_TREE_ENTRIES),
    )
    check(
        "FTF-04A05",
        "acceptance: a real submodule (gitlink) entry is detected and recorded",
        capture.submodule_paths == ("vendor/lib",),
    )
    check(
        "FTF-04A05",
        "acceptance: a real Git-LFS-tracked path (declared via .gitattributes filter=lfs) is "
        "detected and recorded",
        capture.lfs_tracked_paths == ("models/weights.bin",),
    )
    check(
        "FTF-04A05",
        "acceptance: a genuinely complete tree listing is recorded as complete",
        capture.tree_listing_complete is True,
    )


def test_capture_resolving_a_sha_directly_has_no_branch() -> None:
    if _BASE_URL is None:
        return
    capture = capture_module.capture_github_source_snapshot(
        token="valid-token",
        repository_identity=_REPO,
        ref=_MAIN_COMMIT_SHA,
        base_url=_BASE_URL,
        policy=_policy(),
    )
    check(
        "FTF-04A05",
        "acceptance: resolving a raw commit SHA directly records no branch display name",
        capture.branch is None,
    )


def test_truncated_tree_listing_is_blocked() -> None:
    if _BASE_URL is None:
        return
    try:
        capture_module.capture_github_source_snapshot(
            token="valid-token",
            repository_identity=_REPO,
            ref="truncated-ref",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        raised = False
    except capture_module.IncompleteSourceTreeError:
        raised = True
    check(
        "FTF-04A05",
        "negative_acceptance: a real truncated tree listing (missing critical objects) blocks "
        "the snapshot rather than proceeding with partial evidence",
        raised,
    )


def test_unsafe_tree_entry_path_is_blocked() -> None:
    if _BASE_URL is None:
        return
    try:
        capture_module.capture_github_source_snapshot(
            token="valid-token",
            repository_identity=_REPO,
            ref="unsafe-ref",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        raised = False
    except capture_module.UnsafeSourceTreeEntryError:
        raised = True
    check(
        "FTF-04A05",
        "negative_acceptance: a real tree entry whose path escapes the repository root (an "
        "unsafe archive member) blocks the snapshot",
        raised,
    )


def test_missing_commit_tree_is_blocked() -> None:
    if _BASE_URL is None:
        return
    try:
        capture_module.capture_github_source_snapshot(
            token="valid-token",
            repository_identity=_REPO,
            ref="no-tree-ref",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        raised = False
    except capture_module.MissingCommitTreeError:
        raised = True
    check(
        "FTF-04A05",
        "negative_acceptance: a real commit response with no usable tree.sha is refused, never "
        "fabricated",
        raised,
    )


def test_revoked_token_is_denied() -> None:
    if _BASE_URL is None:
        return
    try:
        capture_module.capture_github_source_snapshot(
            token="revoked-token",
            repository_identity=_REPO,
            ref="main",
            base_url=_BASE_URL,
            policy=_policy(),
        )
        raised = False
    except capture_module.GitHubCredentialRevokedError:
        raised = True
    check(
        "FTF-04A05",
        "negative_acceptance: a real revoked credential (HTTP 401) is denied before any tree "
        "data is trusted",
        raised,
    )


def test_fault_injection_get_only_guarantee_is_load_bearing() -> None:
    if _BASE_URL is None:
        return
    original = _MODULE_PATH.read_bytes()
    target = b'_HTTP_METHOD = "GET"'
    check(
        "FTF-04A05",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(target, b'_HTTP_METHOD = "POST"', 1)
        check("FTF-04A05", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(capture_module)
        still_succeeds = False
        try:
            capture_module.capture_github_source_snapshot(
                token="valid-token",
                repository_identity=_REPO,
                ref="main",
                base_url=_BASE_URL,
                policy=_policy(),
            )
            still_succeeds = True
        except capture_module.GitHubConnectionError:
            still_succeeds = False
        check(
            "FTF-04A05",
            "negative_acceptance: hardcoding the real HTTP verb to a write-shaped method "
            "genuinely breaks against the real fixture server (which only ever implements "
            "do_GET), proving the GET-only guarantee is real and observable",
            not still_succeeds,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(capture_module)
        check(
            "FTF-04A05",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        restored_succeeds = False
        try:
            capture_module.capture_github_source_snapshot(
                token="valid-token",
                repository_identity=_REPO,
                ref="main",
                base_url=_BASE_URL,
                policy=_policy(),
            )
            restored_succeeds = True
        except capture_module.GitHubConnectionError:
            restored_succeeds = False
        check(
            "FTF-04A05",
            "after restoring the file, the real GET request succeeds again",
            restored_succeeds,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A05 GitHub source snapshot capture (connector): {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A05 GitHub source snapshot capture (connector): all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
