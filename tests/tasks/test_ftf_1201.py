#!/usr/bin/env python3
"""FTF-1201 -- LiteLLM registration capability detection.

Runnable without any database and without any real external network
egress -- this suite builds and drives real local servers itself, matching
the no-DB/no-external-egress local-server pattern `tests/tasks/
test_ftf_0203.py` already established:

  - a real local HTTP server standing in for the OpenBao secret backend
    (the same KV v2 read shape `packages/litellm_client/
    secret_manager_integration.py` (FTF-0202, already CERTIFIED) actually
    calls);
  - real local HTTPS servers standing in for the organization's LiteLLM
    gateway, with a real self-signed CA this suite generates and installs
    into the sandbox's own system trust store for the duration of the run
    (removed again in `atexit`), so the "management_capable"/
    "inference_only" happy paths exercise real, unmodified TLS certificate
    validation.

This task's own acceptance clause ("Detect actual organization-approved
gateway management capability separately from inference key") and
negative_acceptance ("No assumption every virtual key can register
routes; lack of bootstrap permission is explicit blocker") are covered by
real, live socket round trips: a `proxy_admin` role reports
`management_capable`, every other real, documented LiteLLM proxy role
(including the read-only `proxy_admin_viewer`) reports `inference_only`,
and a denied/malformed/unreachable probe reports its own distinct
non-capable state -- never a silent `management_capable` default.

Run directly: python3 tests/tasks/test_ftf_1201.py
"""

from __future__ import annotations

import atexit
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.litellm_registration_capability_detection import (  # noqa: E402
    RegistrationCapabilityState,
    _extract_observed_role,
    default_capability_detection_policy,
    detect_registration_capability,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local secret-backend fixture (OpenBao KV v2 read subset) ----------

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/org1"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = "sk-real-inference-key-1201"
_SECRET_TOKEN = "test-vault-token"


class _SecretBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _json(self, status: int, payload: dict[str, object] | None) -> None:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        token = self.headers.get("X-Vault-Token")
        if token != _SECRET_TOKEN:
            self._json(403, {"errors": ["permission denied"]})
            return
        if self.path == f"/v1/{_SECRET_MOUNT}/data/{_SECRET_PATH}":
            self._json(
                200,
                {"data": {"data": {_SECRET_FIELD: _SECRET_VALUE}, "metadata": {"version": 1}}},
            )
        else:
            self._json(404, {"errors": []})


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
_SECRET_PORT = _SECRET_SERVER.server_address[1]
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)

_SECRET_CONFIG = OpenBaoConfig(address=f"http://127.0.0.1:{_SECRET_PORT}", token=_SECRET_TOKEN)


def _secret_ref(path: str = _SECRET_PATH) -> str:
    return f"openbao://{_SECRET_MOUNT}/{path}/{_SECRET_FIELD}"


# -- Real local TLS fixture: a genuine CA installed into the sandbox's own
#    system trust store -----------------------------------------------------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-1201-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-1201-test-ca.crt")
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
            "/CN=FTF-1201 Test CA",
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


_TRUSTED_LEAF: Path | None = None
_TRUSTED_KEY: Path | None = None
if _UPDATE_CA_AVAILABLE:
    _TRUSTED_LEAF, _TRUSTED_KEY = _generate_trusted_server_cert()
else:
    print(
        "FTF-1201: this sandbox has no openssl/update-ca-certificates -- the "
        "live TLS-backed checks are skipped and disclosed here; the pure "
        "role-extraction unit checks still run and still count. GitHub "
        "Actions' ubuntu-latest runner has both tools."
    )


def _start_https_server(
    handler_cls: type[BaseHTTPRequestHandler], certfile: Path | None, keyfile: Path | None
) -> ThreadingHTTPServer:
    import ssl

    assert certfile is not None and keyfile is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


def _policy_for(port: int) -> ConnectionPolicy:
    import ipaddress

    return ConnectionPolicy(
        name="test-litellm-gateway",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


def _make_gateway_handler(
    *,
    valid_token: str,
    user_info_status: int = 200,
    user_info_body: bytes = b'{"user_role":"proxy_admin"}',
) -> type[BaseHTTPRequestHandler]:
    class _GatewayHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            auth = self.headers.get("Authorization", "")
            if self.path == "/user/info":
                if auth != f"Bearer {valid_token}":
                    self._respond(401, b'{"error":"invalid token"}')
                    return
                self._respond(user_info_status, user_info_body)
            else:
                self._respond(404, b"")

    return _GatewayHandler


# -- Tests --------------------------------------------------------------


def test_proxy_admin_role_is_management_capable() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(
        valid_token=_SECRET_VALUE, user_info_body=b'{"user_role":"proxy_admin"}'
    )
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = detect_registration_capability(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-1201",
        "a real proxy_admin role reports management_capable",
        result.state == RegistrationCapabilityState.MANAGEMENT_CAPABLE,
    )
    check("FTF-1201", "observed_role is recorded verbatim", result.observed_role == "proxy_admin")
    check(
        "FTF-1201",
        "checked_at is a real timezone-aware timestamp",
        result.checked_at.tzinfo is not None,
    )


def test_non_admin_roles_never_report_capable() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    # proxy_admin_viewer is LiteLLM's own explicitly read-only admin role --
    # this is this task's own central negative_acceptance case: a role that
    # sounds admin-adjacent must never be treated as registration-capable.
    for role in ("proxy_admin_viewer", "internal_user", "internal_user_viewer", "team", "customer"):
        handler = _make_gateway_handler(
            valid_token=_SECRET_VALUE,
            user_info_body=json.dumps({"user_role": role}).encode("utf-8"),
        )
        server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
        port = server.server_address[1]
        result = detect_registration_capability(
            base_url=f"https://127.0.0.1:{port}",
            secret_ref=_secret_ref(),
            secret_config=_SECRET_CONFIG,
            policy=_policy_for(port),
        )
        check(
            "FTF-1201",
            f"role {role!r} never reports management_capable",
            result.state == RegistrationCapabilityState.INFERENCE_ONLY,
        )
        check(
            "FTF-1201", f"role {role!r} is recorded as observed_role", result.observed_role == role
        )


def test_forbidden_probe_is_explicit_blocker() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(valid_token="a-completely-different-key")
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = detect_registration_capability(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-1201",
        "a real gateway 401 on the probe reports forbidden, never management_capable",
        result.state == RegistrationCapabilityState.FORBIDDEN,
    )
    check("FTF-1201", "forbidden never carries an observed_role", result.observed_role is None)


def test_malformed_response_never_invents_capability() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(valid_token=_SECRET_VALUE, user_info_body=b"not json at all")
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = detect_registration_capability(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-1201",
        "a malformed /user/info body reports malformed_response, never management_capable",
        result.state == RegistrationCapabilityState.MALFORMED_RESPONSE,
    )

    handler2 = _make_gateway_handler(valid_token=_SECRET_VALUE, user_info_body=b'{"spend": 12.5}')
    server2 = _start_https_server(handler2, _TRUSTED_LEAF, _TRUSTED_KEY)
    port2 = server2.server_address[1]
    result2 = detect_registration_capability(
        base_url=f"https://127.0.0.1:{port2}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port2),
    )
    check(
        "FTF-1201",
        "a response with no recognizable user_role field reports malformed_response, "
        "never management_capable",
        result2.state == RegistrationCapabilityState.MALFORMED_RESPONSE,
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    policy = _policy_for(1)

    not_found = detect_registration_capability(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref("litellm/does-not-exist"),
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-1201",
        "an unknown secret path reports secret_not_found, never attempts the gateway",
        not_found.state == RegistrationCapabilityState.SECRET_NOT_FOUND,
    )

    unsupported = detect_registration_capability(
        base_url="https://127.0.0.1:1",
        secret_ref="vault://team/litellm/key",
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-1201",
        "an older vault:// reference with no real backend reports "
        "secret_backend_unsupported, never crashes",
        unsupported.state == RegistrationCapabilityState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = detect_registration_capability(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=policy,
    )
    check(
        "FTF-1201",
        "no secret backend configured in this environment reports secret_backend_error, "
        "never a fabricated capability",
        no_config.state == RegistrationCapabilityState.SECRET_BACKEND_ERROR,
    )


def test_unreachable_gateway_never_reports_capable() -> None:
    result = detect_registration_capability(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(1),
        timeout=1.0,
    )
    check(
        "FTF-1201",
        "a real closed/unreachable destination reports unavailable, never management_capable",
        result.state == RegistrationCapabilityState.UNAVAILABLE,
    )


def test_default_policy_is_https_only_no_redirects() -> None:
    policy = default_capability_detection_policy()
    check(
        "FTF-1201",
        "the default capability-detection policy allows only https",
        policy.allowed_schemes == frozenset({"https"}),
    )
    check(
        "FTF-1201",
        "the default capability-detection policy does not follow redirects",
        policy.allow_redirects is False,
    )


def test_extract_observed_role_accepts_documented_shapes_only() -> None:
    check(
        "FTF-1201",
        "a bare top-level user_role is extracted",
        _extract_observed_role({"user_role": "proxy_admin"}) == "proxy_admin",
    )
    check(
        "FTF-1201",
        "a user_role nested under user_info is extracted",
        _extract_observed_role({"user_info": {"user_role": "internal_user"}}) == "internal_user",
    )
    check(
        "FTF-1201",
        "a user_role nested under data is extracted",
        _extract_observed_role({"data": {"user_role": "team"}}) == "team",
    )
    check(
        "FTF-1201",
        "a non-dict payload extracts no role",
        _extract_observed_role([1, 2, 3]) is None,
    )
    check(
        "FTF-1201",
        "a dict with no recognizable role field extracts none",
        _extract_observed_role({"spend": 1}) is None,
    )
    check(
        "FTF-1201",
        "a non-string user_role value is never returned as a role",
        _extract_observed_role({"user_role": 123}) is None,
    )


# -- Fault injection (Law 1): a deliberate regression this suite must catch -

_MODULE_PATH = ROOT / "packages" / "litellm_client" / "litellm_registration_capability_detection.py"


def test_fault_injection_treating_viewer_as_capable_is_caught() -> None:
    """Mutates the real, on-disk module so `proxy_admin_viewer` is folded
    into the capable-roles set, proves the mutated module genuinely
    reports `management_capable` for a real viewer-role response against a
    real gateway, then restores the file byte-identical whether or not the
    probe succeeded -- never merely that the mutation was syntactically
    applied."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(
        valid_token=_SECRET_VALUE, user_info_body=b'{"user_role":"proxy_admin_viewer"}'
    )
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]

    baseline = detect_registration_capability(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-1201",
        "baseline (unmutated): a real proxy_admin_viewer role never reports management_capable",
        baseline.state == RegistrationCapabilityState.INFERENCE_ONLY,
    )

    original = _MODULE_PATH.read_bytes()
    try:
        text = original.decode("utf-8")
        pattern = re.compile(r'_MANAGEMENT_CAPABLE_ROLES = frozenset\(\{"proxy_admin"\}\)')
        match = pattern.search(text)
        check(
            "FTF-1201",
            "the fault-injection target line is present in the tracked module",
            match is not None,
        )
        mutated = pattern.sub(
            '_MANAGEMENT_CAPABLE_ROLES = frozenset({"proxy_admin", "proxy_admin_viewer"})', text
        )
        check("FTF-1201", "the mutation actually changed the file", mutated != text)
        _MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from packages.litellm_client.litellm_registration_capability_detection import (
    detect_registration_capability, RegistrationCapabilityState,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.security.url_policy import ConnectionPolicy
import ipaddress

policy = ConnectionPolicy(
    name="p",
    allowed_schemes=frozenset({{"https"}}),
    allowed_ports=frozenset({{{port}}}),
    explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
)
result = detect_registration_capability(
    base_url="https://127.0.0.1:{port}",
    secret_ref="openbao://secret/litellm/org1/api_key",
    secret_config=OpenBaoConfig(address="http://127.0.0.1:{_SECRET_PORT}", token={_SECRET_TOKEN!r}),
    policy=policy,
)
print("STATE=" + result.state.value)
"""
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-1201",
            "fault injection: widening the capable-roles set genuinely lets a real "
            f"proxy_admin_viewer response report management_capable "
            f"(stdout={result.stdout!r} stderr={result.stderr!r})",
            result.returncode == 0 and "STATE=management_capable" in result.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-1201",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1201 registration capability detection: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1201 registration capability detection: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
