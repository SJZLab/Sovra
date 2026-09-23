#!/usr/bin/env python3
"""FTF-0206 -- `/model_group/info` optional enrichment.

Runnable without any database and without any real external network
egress -- same no-DB/no-external-egress local-server pattern
`tests/tasks/test_ftf_0205.py` (already CERTIFIED) established:

  - a real local HTTP server standing in for the OpenBao secret backend
    (the same KV v2 read shape `packages/litellm_client/
    secret_manager_integration.py` (FTF-0202, already CERTIFIED) actually
    calls, exercised unmodified);
  - real local HTTPS servers standing in for the organization's LiteLLM
    gateway, with a real self-signed CA this suite generates and installs
    into the sandbox's own system trust store for the duration of the run
    (removed again in `atexit`), so the happy path exercises real,
    unmodified TLS certificate validation.

This task's own acceptance clause ("Optional enrichment runs only with
explicit supported capability and safe permission") is covered by
`test_capability_not_declared_never_calls_the_gateway` and
`test_fault_injection_ignoring_capability_flag_is_caught`. This task's
own negative_acceptance ("403/not-supported leaves limited metadata
without failing basic discovery or gaining management rights") is
covered by `test_permission_denied_never_drops_a_visible_id` and
`test_route_not_supported_never_drops_a_visible_id`.

Run directly: python3 tests/tasks/test_ftf_0206.py
"""

from __future__ import annotations

import atexit
import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.model_group_info_optional_enrichment import (  # noqa: E402
    GroupInfoState,
    _allowlisted_fields,
    _classify_guard_failure,
    _parse_model_group_info,
    enrich_visible_model_groups,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_models_discovery import ModelMetadataStatus  # noqa: E402
from packages.security.outbound_guard import OutboundGuardError  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local secret-backend fixture (OpenBao KV v2 read subset) ----------

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/org1"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = "sk-real-inference-key-0206"
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


# -- Real local TLS fixture: same convention as tests/tasks/test_ftf_0205.py -

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0206-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0206-test-ca.crt")
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
            "/CN=FTF-0206 Test CA",
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
        "FTF-0206: this sandbox has no openssl/update-ca-certificates -- the "
        "live TLS-backed checks are skipped and disclosed here; the pure "
        "allowlist/classifier/parser unit checks still run and still count. "
        "GitHub Actions' ubuntu-latest runner has both tools."
    )


def _policy_for(port: int) -> ConnectionPolicy:
    import ipaddress

    return ConnectionPolicy(
        name="test-litellm-gateway",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


def _start_https_server(handler_cls: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    assert _TRUSTED_LEAF is not None and _TRUSTED_KEY is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(_TRUSTED_LEAF), keyfile=str(_TRUSTED_KEY))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


def _make_static_handler(
    *,
    valid_token: str = _SECRET_VALUE,
    group_info_status: int = 200,
    group_info_body: bytes = b'{"data":[]}',
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
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
            if self.path == "/model_group/info":
                if auth != f"Bearer {valid_token}":
                    self._respond(401, b'{"error":"invalid token"}')
                    return
                self._respond(group_info_status, group_info_body)
            else:
                self._respond(404, b"")

    return _Handler


# -- Tests --------------------------------------------------------------


def test_visible_group_gets_allowlisted_fields() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        group_info_body=json.dumps(
            {
                "data": [
                    {
                        "model_group": "gpt-a",
                        "mode": "chat",
                        "max_tokens": 4096,
                        "supports_vision": True,
                        "tpm": 100000,
                        "rpm": 1000,
                        "providers": ["openai"],
                        "litellm_params": {"api_key": "sk-should-never-leak"},
                    }
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "a reachable+authorized gateway reports completed",
        result.state == GroupInfoState.COMPLETED,
    )
    check("FTF-0206", "exactly one entry for the one visible id", len(result.entries) == 1)
    entry = result.entries[0]
    check(
        "FTF-0206",
        "the visible group is enriched",
        entry.metadata_status == ModelMetadataStatus.ENRICHED,
    )
    check(
        "FTF-0206",
        "allowlisted scalar fields survive with real values, including group-level tpm/rpm",
        dict(entry.fields)
        == {
            "mode": "chat",
            "max_tokens": 4096,
            "supports_vision": True,
            "tpm": 100000,
            "rpm": 1000,
        },
    )
    check(
        "FTF-0206",
        "the non-scalar providers field never appears in the result",
        "providers" not in entry.fields,
    )
    check(
        "FTF-0206",
        "checked_at is a real timezone-aware timestamp",
        result.checked_at.tzinfo is not None,
    )


def test_capability_not_declared_never_calls_the_gateway() -> None:
    """This task's own acceptance clause: "Optional enrichment runs only
    with explicit supported capability..." -- proves the network call is
    never attempted at all when the caller has not explicitly declared
    the capability supported, by pointing at a port with nothing
    listening and confirming no connection-level failure state is ever
    produced (only `capability_not_declared`, reached before any socket
    is opened)."""
    result = enrich_visible_model_groups(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(1),
        visible_model_ids=("gpt-a", "gpt-b"),
        capability_supported=False,
    )
    check(
        "FTF-0206",
        "an undeclared capability reports capability_not_declared, never attempting the "
        "gateway call",
        result.state == GroupInfoState.CAPABILITY_NOT_DECLARED,
    )
    check(
        "FTF-0206",
        "every visible id is still present, limited, never dropped",
        {e.id for e in result.entries} == {"gpt-a", "gpt-b"}
        and all(e.metadata_status == ModelMetadataStatus.LIMITED for e in result.entries),
    )


def test_litellm_params_credentials_are_never_exposed() -> None:
    """This task's own family of secret-safety rules (Section 65.4):
    proves the real `litellm_params.api_key` sibling a real gateway can
    place directly on a `/model_group/info` entry is never read at all."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        group_info_body=json.dumps(
            {
                "data": [
                    {
                        "model_group": "gpt-a",
                        "mode": "chat",
                        "api_key": "sk-leaked-if-allowlist-is-broken",
                        "litellm_params": {"api_key": "sk-also-should-never-leak"},
                    }
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "an unallowlisted api_key field is never exposed even though the gateway sent it",
        "api_key" not in result.entries[0].fields,
    )


def test_metadata_only_group_is_never_stored_or_exposed() -> None:
    """Section 3.2's "no authorization union" applied to enrichment: a
    group known only through `/model_group/info` (never listed as
    visible by the caller's own `GET /v1/models` result) is never added
    to the reported entries."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        group_info_body=json.dumps(
            {
                "data": [
                    {"model_group": "gpt-a", "mode": "chat"},
                    {"model_group": "unlisted-group", "mode": "chat"},
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    ids = {e.id for e in result.entries}
    check(
        "FTF-0206",
        "a name known only through /model_group/info is never added to the reported entries",
        "unlisted-group" not in ids,
    )
    check("FTF-0206", "only the real visible group is reported", ids == {"gpt-a"})


def test_authorized_group_absent_from_response_is_limited() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(group_info_body=b'{"data":[]}')
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a", "gpt-b"),
        capability_supported=True,
    )
    check("FTF-0206", "both visible ids still get a real entry", len(result.entries) == 2)
    check(
        "FTF-0206",
        "an absent group retains limited metadata (Section 3.2), never dropped",
        all(
            e.metadata_status == ModelMetadataStatus.LIMITED and not e.fields
            for e in result.entries
        ),
    )


def test_permission_denied_never_drops_a_visible_id() -> None:
    """This task's own negative_acceptance: "403/not-supported leaves
    limited metadata without failing basic discovery or gaining
    management rights." A 403 under the safe inference credential
    degrades every visible id to `limited`, never drops it, and there is
    no code path here that retries under any other credential."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(group_info_status=403, group_info_body=b'{"error":"forbidden"}')
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a", "gpt-b"),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "a permission-denied 403 is reported as invalid_token, not fabricated as completed",
        result.state == GroupInfoState.INVALID_TOKEN,
    )
    check(
        "FTF-0206",
        "every visible id is still present, downgraded to limited, never dropped",
        {e.id for e in result.entries} == {"gpt-a", "gpt-b"}
        and all(e.metadata_status == ModelMetadataStatus.LIMITED for e in result.entries),
    )


def test_route_not_supported_never_drops_a_visible_id() -> None:
    """The other half of the same negative_acceptance clause: a real 404
    (this gateway build does not expose `/model_group/info` at all) is
    reported distinctly from a 401/403 permission denial, and still never
    drops a visible id."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(group_info_status=404, group_info_body=b"")
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "a real 404 is reported as not_supported, distinct from invalid_token",
        result.state == GroupInfoState.NOT_SUPPORTED,
    )
    check(
        "FTF-0206",
        "the visible id is still reported, limited, never dropped",
        len(result.entries) == 1
        and result.entries[0].metadata_status == ModelMetadataStatus.LIMITED,
    )


def test_malformed_group_info_response_never_drops_visible_ids() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(group_info_status=200, group_info_body=b"not json at all")
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "a malformed /model_group/info body reports malformed_response",
        result.state == GroupInfoState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0206",
        "the visible id is still reported, limited, never dropped",
        len(result.entries) == 1
        and result.entries[0].metadata_status == ModelMetadataStatus.LIMITED,
    )


def test_duplicate_visible_ids_collapse_to_one_entry() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        group_info_body=b'{"data":[{"model_group":"gpt-a","mode":"chat"}]}'
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a", "gpt-a"),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "a duplicated visible id collapses to a single entry, in order",
        [e.id for e in result.entries] == ["gpt-a"],
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    policy = _policy_for(1)

    not_found = enrich_visible_model_groups(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref("litellm/does-not-exist"),
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "an unknown secret path reports secret_not_found",
        not_found.state == GroupInfoState.SECRET_NOT_FOUND,
    )
    check(
        "FTF-0206",
        "secret_not_found never attempts a gateway call, but still reports the visible id "
        "as limited",
        len(not_found.entries) == 1
        and not_found.entries[0].metadata_status == ModelMetadataStatus.LIMITED,
    )

    denied = enrich_visible_model_groups(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong"),
        policy=policy,
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "a real backend 403 on the resolving token reports secret_access_denied",
        denied.state == GroupInfoState.SECRET_ACCESS_DENIED,
    )

    unsupported = enrich_visible_model_groups(
        base_url="https://127.0.0.1:1",
        secret_ref="vault://team/litellm/key",
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "an older vault:// reference with no real backend reports "
        "secret_backend_unsupported, never crashes",
        unsupported.state == GroupInfoState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = enrich_visible_model_groups(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=policy,
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "no secret backend configured in this environment reports secret_backend_error, "
        "never a fabricated success",
        no_config.state == GroupInfoState.SECRET_BACKEND_ERROR,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    handler = _make_static_handler()
    if _UPDATE_CA_AVAILABLE:
        server = _start_https_server(handler)
        port = server.server_address[1]
    else:
        port = 1
    policy = ConnectionPolicy(name="test-no-allowlist", allowed_schemes=frozenset({"https"}))
    result = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "a real destination outside the connection policy's own allowlist is denied "
        "before any socket opens, reported as destination_denied",
        result.state == GroupInfoState.DESTINATION_DENIED,
    )
    check(
        "FTF-0206",
        "the visible id is still reported, limited, never dropped",
        len(result.entries) == 1
        and result.entries[0].metadata_status == ModelMetadataStatus.LIMITED,
    )


def test_empty_visible_ids_returns_no_entries_without_a_gateway_call() -> None:
    result = enrich_visible_model_groups(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(1),
        visible_model_ids=(),
        capability_supported=True,
    )
    check("FTF-0206", "no visible ids means no reported entries", result.entries == ())


def test_allowlisted_fields_drops_unknown_keys_and_non_scalar_values() -> None:
    fields = _allowlisted_fields(
        {
            "model_group": "gpt-a",
            "mode": "chat",
            "max_tokens": 4096,
            "api_key": "sk-should-be-dropped",
            "providers": ["openai"],
            "supports_vision": [True],  # allowlisted key, non-scalar value
            "input_cost_per_token": 0.0001,
            "tpm": 100000,
        }
    )
    check(
        "FTF-0206",
        "an unknown key is dropped even though its value is a plain scalar",
        "api_key" not in fields,
    )
    check(
        "FTF-0206",
        "the model_group key itself is dropped -- it is not a capability/limit field",
        "model_group" not in fields,
    )
    check(
        "FTF-0206",
        "an allowlisted key whose value is a list (non-scalar) is dropped",
        "supports_vision" not in fields,
    )
    check(
        "FTF-0206",
        "a non-scalar providers field is dropped",
        "providers" not in fields,
    )
    check(
        "FTF-0206",
        "allowlisted scalar fields survive with real values",
        dict(fields)
        == {"mode": "chat", "max_tokens": 4096, "input_cost_per_token": 0.0001, "tpm": 100000},
    )
    check(
        "FTF-0206",
        "a non-dict raw entry returns an empty mapping, never raises",
        _allowlisted_fields(None) == MappingProxyType({})
        and _allowlisted_fields("not-a-dict") == MappingProxyType({})
        and _allowlisted_fields([1, 2, 3]) == MappingProxyType({}),
    )


def test_parse_model_group_info_keeps_whole_entry_keyed_by_model_group() -> None:
    by_group = _parse_model_group_info(
        json.dumps(
            {
                "data": [
                    {"model_group": "gpt-a", "mode": "chat", "tpm": 100000},
                    {"notgroup": "ignored"},
                    {"model_group": 123, "mode": "should-not-appear"},
                ]
            }
        ).encode("utf-8")
    )
    check(
        "FTF-0206",
        "a well-formed entry maps model_group to its own whole raw entry dict",
        by_group == {"gpt-a": {"model_group": "gpt-a", "mode": "chat", "tpm": 100000}},
    )
    assert by_group is not None
    check(
        "FTF-0206",
        "a non-string model_group is dropped, never coerced",
        123 not in by_group.values() and "123" not in by_group,
    )
    check(
        "FTF-0206",
        "a non-dict top-level body is malformed (None)",
        _parse_model_group_info(b"[1,2,3]") is None,
    )
    check(
        "FTF-0206",
        "a missing data key is malformed (None)",
        _parse_model_group_info(b'{"foo":"bar"}') is None,
    )
    check(
        "FTF-0206",
        "invalid JSON is malformed (None)",
        _parse_model_group_info(b"not json") is None,
    )


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0206",
        "a real socket.gaierror classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == GroupInfoState.DNS_FAILED,
    )
    check(
        "FTF-0206",
        "a real ssl.SSLCertVerificationError classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == GroupInfoState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0206",
        "a real TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out"))) == GroupInfoState.TIMEOUT,
    )
    check(
        "FTF-0206",
        "a real ConnectionRefusedError classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == GroupInfoState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0206",
        "any other real OSError classifies as unreachable, never invented as completed",
        _classify_guard_failure(_wrapped(OSError("something else"))) == GroupInfoState.UNREACHABLE,
    )


# -- Fault injection (Law 1): deliberate regressions this suite must catch --

_MODULE_PATH = ROOT / "packages" / "litellm_client" / "model_group_info_optional_enrichment.py"


def test_fault_injection_widening_allowlist_to_all_fields_is_caught() -> None:
    """Mutates the real, on-disk module so `_allowlisted_fields` returns
    every field verbatim instead of filtering through
    `_ALLOWLISTED_MODEL_GROUP_FIELDS` -- reintroducing a credential leak.
    Runs the mutated module against a real gateway in a fresh subprocess
    and proves the secret genuinely leaks into the result -- not merely
    that the mutation was syntactically applied. Restores the file
    byte-identical afterward."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        group_info_body=json.dumps(
            {
                "data": [
                    {"model_group": "gpt-a", "mode": "chat", "api_key": "sk-fault-injection-leak"}
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]

    baseline = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
        capability_supported=True,
    )
    check(
        "FTF-0206",
        "baseline (unmutated): the credential-shaped field never appears in the real result",
        "api_key" not in baseline.entries[0].fields,
    )

    original = _MODULE_PATH.read_bytes()
    try:
        text = original.decode("utf-8")
        target = (
            "    if not isinstance(raw_entry, dict):\n"
            "        return MappingProxyType({})\n"
            "    fields = {\n"
            "        key: value\n"
            "        for key, value in raw_entry.items()\n"
            "        if key in _ALLOWLISTED_MODEL_GROUP_FIELDS and "
            "isinstance(value, _ALLOWLISTED_VALUE_TYPES)\n"
            "    }\n"
            "    return MappingProxyType(fields)"
        )
        check(
            "FTF-0206",
            "the fault-injection target block is present in the tracked module",
            target in text,
        )
        replacement = (
            "    if not isinstance(raw_entry, dict):\n"
            "        return MappingProxyType({})\n"
            "    return MappingProxyType(dict(raw_entry))"
        )
        mutated = text.replace(target, replacement)
        check("FTF-0206", "the mutation actually changed the file", mutated != text)
        _MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from packages.litellm_client.model_group_info_optional_enrichment import (
    enrich_visible_model_groups,
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
result = enrich_visible_model_groups(
    base_url="https://127.0.0.1:{port}",
    secret_ref={_secret_ref()!r},
    secret_config=OpenBaoConfig(address={_SECRET_CONFIG.address!r}, token={_SECRET_TOKEN!r}),
    policy=policy,
    visible_model_ids=("gpt-a",),
    capability_supported=True,
)
print("FIELDS=" + repr(dict(result.entries[0].fields)))
"""
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0206",
            "fault injection: dropping the allowlist genuinely lets the credential-shaped "
            f"field leak into the result (stdout={result.stdout!r} stderr={result.stderr!r})",
            result.returncode == 0 and "sk-fault-injection-leak" in result.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0206",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def test_fault_injection_ignoring_capability_flag_is_caught() -> None:
    """Mutates the real, on-disk module so `enrich_visible_model_groups`
    ignores `capability_supported` and always attempts the gateway call
    -- reintroducing exactly the violation this task's own acceptance
    clause forbids ("Optional enrichment runs only with explicit
    supported capability..."). Runs the mutated module against a real
    gateway in a fresh subprocess with `capability_supported=False` and
    proves the call genuinely goes through (state becomes `completed`
    instead of `capability_not_declared`) -- not merely that the
    mutation was syntactically applied. Restores the file byte-identical
    afterward."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        group_info_body=b'{"data":[{"model_group":"gpt-a","mode":"chat"}]}'
    )
    server = _start_https_server(handler)
    port = server.server_address[1]

    baseline = enrich_visible_model_groups(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
        capability_supported=False,
    )
    check(
        "FTF-0206",
        "baseline (unmutated): an undeclared capability never reaches the gateway",
        baseline.state == GroupInfoState.CAPABILITY_NOT_DECLARED,
    )

    original = _MODULE_PATH.read_bytes()
    try:
        text = original.decode("utf-8")
        target = (
            "    if not capability_supported:\n"
            "        return _all_limited(\n"
            "            GroupInfoState.CAPABILITY_NOT_DECLARED,\n"
            '            "model_group/info capability is not declared supported for this '
            'connection",\n'
            "        )"
        )
        check(
            "FTF-0206",
            "the fault-injection target block is present in the tracked module",
            target in text,
        )
        replacement = "    if False:\n        pass"
        mutated = text.replace(target, replacement)
        check("FTF-0206", "the mutation actually changed the file", mutated != text)
        _MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from packages.litellm_client.model_group_info_optional_enrichment import (
    enrich_visible_model_groups,
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
result = enrich_visible_model_groups(
    base_url="https://127.0.0.1:{port}",
    secret_ref={_secret_ref()!r},
    secret_config=OpenBaoConfig(address={_SECRET_CONFIG.address!r}, token={_SECRET_TOKEN!r}),
    policy=policy,
    visible_model_ids=("gpt-a",),
    capability_supported=False,
)
print("STATE=" + result.state.value)
"""
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0206",
            "fault injection: ignoring the capability flag genuinely lets an undeclared "
            f"call reach the gateway (stdout={result.stdout!r} stderr={result.stderr!r})",
            result.returncode == 0 and "STATE=completed" in result.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0206",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0206 /model_group/info optional enrichment: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0206 /model_group/info optional enrichment: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
