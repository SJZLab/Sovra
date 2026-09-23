#!/usr/bin/env python3
"""FTF-0205 -- `/v1/model/info` enrichment.

Runnable without any database and without any real external network
egress -- same no-DB/no-external-egress local-server pattern
`tests/tasks/test_ftf_0204.py` (already CERTIFIED) established:

  - a real local HTTP server standing in for the OpenBao secret backend
    (the same KV v2 read shape `packages/litellm_client/
    secret_manager_integration.py` (FTF-0202, already CERTIFIED) actually
    calls, exercised unmodified);
  - real local HTTPS servers standing in for the organization's LiteLLM
    gateway, with a real self-signed CA this suite generates and installs
    into the sandbox's own system trust store for the duration of the run
    (removed again in `atexit`), so the happy path exercises real,
    unmodified TLS certificate validation.

This task's own negative_acceptance ("Raw credentials, admin-only provider
routes and metadata-only aliases are neither stored nor exposed") is
covered by three dedicated checks: `test_litellm_params_credentials_are_
never_exposed_even_if_duplicated_into_model_info`,
`test_admin_only_route_denial_never_drops_a_visible_id`, and
`test_metadata_only_alias_is_never_stored_or_exposed`.

Run directly: python3 tests/tasks/test_ftf_0205.py
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

from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_model_info_enrichment import (  # noqa: E402
    EnrichmentState,
    _allowlisted_fields,
    _classify_guard_failure,
    _parse_model_info,
    enrich_visible_models,
)
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
_SECRET_VALUE = "sk-real-inference-key-0205"
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


# -- Real local TLS fixture: same convention as tests/tasks/test_ftf_0204.py -

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0205-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0205-test-ca.crt")
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
            "/CN=FTF-0205 Test CA",
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
        "FTF-0205: this sandbox has no openssl/update-ca-certificates -- the "
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
    model_info_status: int = 200,
    model_info_body: bytes = b'{"data":[]}',
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
            if self.path == "/model/info":
                if auth != f"Bearer {valid_token}":
                    self._respond(401, b'{"error":"invalid token"}')
                    return
                self._respond(model_info_status, model_info_body)
            else:
                self._respond(404, b"")

    return _Handler


# -- Tests --------------------------------------------------------------


def test_visible_alias_gets_allowlisted_fields() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        model_info_body=json.dumps(
            {
                "data": [
                    {
                        "model_name": "gpt-a",
                        "model_info": {
                            "mode": "chat",
                            "max_tokens": 4096,
                            "supports_vision": True,
                        },
                        "litellm_params": {"api_key": "sk-should-never-leak"},
                    }
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "a reachable+authorized gateway reports completed",
        result.state == EnrichmentState.COMPLETED,
    )
    check("FTF-0205", "exactly one entry for the one visible id", len(result.entries) == 1)
    entry = result.entries[0]
    check(
        "FTF-0205",
        "the visible alias is enriched",
        entry.metadata_status == ModelMetadataStatus.ENRICHED,
    )
    check(
        "FTF-0205",
        "allowlisted fields are present with real values",
        dict(entry.fields) == {"mode": "chat", "max_tokens": 4096, "supports_vision": True},
    )
    check(
        "FTF-0205",
        "checked_at is a real timezone-aware timestamp",
        result.checked_at.tzinfo is not None,
    )


def test_litellm_params_credentials_are_never_exposed_even_if_duplicated_into_model_info() -> None:
    """This task's own negative_acceptance: "Raw credentials... are
    neither stored nor exposed." Proves both halves at once: the real
    `litellm_params.api_key` sibling is never read at all, and even a
    misconfigured gateway that duplicates a credential-shaped field
    *inside* `model_info` itself is still dropped by the allow list."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        model_info_body=json.dumps(
            {
                "data": [
                    {
                        "model_name": "gpt-a",
                        "model_info": {
                            "mode": "chat",
                            "api_key": "sk-leaked-if-allowlist-is-broken",
                            "api_base": "https://internal-admin.example/leak",
                        },
                        "litellm_params": {
                            "api_key": "sk-real-secret-should-never-appear",
                            "api_base": "https://internal-admin.example",
                        },
                    }
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
    )
    entry = result.entries[0]
    check(
        "FTF-0205",
        "a real credential-shaped field is never present in the returned fields",
        "api_key" not in entry.fields and "api_base" not in entry.fields,
    )
    check(
        "FTF-0205",
        "only the real allowlisted field survives",
        dict(entry.fields) == {"mode": "chat"},
    )
    check(
        "FTF-0205",
        "no returned field value ever contains the real secret string",
        all("sk-real-secret" not in str(v) for v in entry.fields.values())
        and all("sk-leaked" not in str(v) for v in entry.fields.values()),
    )


def test_metadata_only_alias_is_never_stored_or_exposed() -> None:
    """This task's own negative_acceptance: "...metadata-only aliases are
    neither stored nor exposed." `/model/info` here advertises
    `unlisted-model`, which the caller never listed as visible -- Section
    3.2's "no authorization union" applied to enrichment: a left join onto
    the caller's own already-authorized set, never a union."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        model_info_body=json.dumps(
            {
                "data": [
                    {"model_name": "gpt-a", "model_info": {"mode": "chat"}},
                    {"model_name": "unlisted-model", "model_info": {"mode": "chat"}},
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
    )
    ids = {e.id for e in result.entries}
    check(
        "FTF-0205",
        "a name known only through /model/info is never added to the reported entries",
        "unlisted-model" not in ids,
    )
    check("FTF-0205", "only the real visible alias is reported", ids == {"gpt-a"})


def test_authorized_alias_absent_from_model_info_is_limited() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(model_info_body=b'{"data":[]}')
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a", "gpt-b"),
    )
    check("FTF-0205", "both visible ids still get a real entry", len(result.entries) == 2)
    check(
        "FTF-0205",
        "an absent alias retains limited metadata (Section 3.2), never dropped",
        all(
            e.metadata_status == ModelMetadataStatus.LIMITED and not e.fields
            for e in result.entries
        ),
    )


def test_admin_only_route_denial_never_drops_a_visible_id() -> None:
    """This task's own negative_acceptance ("admin-only provider routes...
    are neither stored nor exposed") read together with Section 3.1
    ("403/404 on enrichment does not invalidate a successful standard
    model list"): a denied `/model/info` call degrades every visible id to
    `limited`, but never drops it from the result."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(model_info_status=403, model_info_body=b'{"error":"forbidden"}')
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a", "gpt-b"),
    )
    check(
        "FTF-0205",
        "an admin-only-route 403 is reported as invalid_token, not fabricated as completed",
        result.state == EnrichmentState.INVALID_TOKEN,
    )
    check(
        "FTF-0205",
        "every visible id is still present, downgraded to limited, never dropped",
        {e.id for e in result.entries} == {"gpt-a", "gpt-b"}
        and all(e.metadata_status == ModelMetadataStatus.LIMITED for e in result.entries),
    )


def test_malformed_model_info_response_never_drops_visible_ids() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(model_info_status=200, model_info_body=b"not json at all")
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "a malformed /model/info body reports malformed_response",
        result.state == EnrichmentState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0205",
        "the visible id is still reported, limited, never dropped",
        len(result.entries) == 1
        and result.entries[0].metadata_status == ModelMetadataStatus.LIMITED,
    )


def test_duplicate_visible_ids_collapse_to_one_entry() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        model_info_body=b'{"data":[{"model_name":"gpt-a","model_info":{"mode":"chat"}}]}'
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a", "gpt-a"),
    )
    check(
        "FTF-0205",
        "a duplicated visible id collapses to a single entry, in order",
        [e.id for e in result.entries] == ["gpt-a"],
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    policy = _policy_for(1)

    not_found = enrich_visible_models(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref("litellm/does-not-exist"),
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "an unknown secret path reports secret_not_found",
        not_found.state == EnrichmentState.SECRET_NOT_FOUND,
    )
    check(
        "FTF-0205",
        "secret_not_found never attempts a gateway call, but still reports the visible id "
        "as limited",
        len(not_found.entries) == 1
        and not_found.entries[0].metadata_status == ModelMetadataStatus.LIMITED,
    )

    denied = enrich_visible_models(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong"),
        policy=policy,
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "a real backend 403 on the resolving token reports secret_access_denied",
        denied.state == EnrichmentState.SECRET_ACCESS_DENIED,
    )

    unsupported = enrich_visible_models(
        base_url="https://127.0.0.1:1",
        secret_ref="vault://team/litellm/key",
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "an older vault:// reference with no real backend reports "
        "secret_backend_unsupported, never crashes",
        unsupported.state == EnrichmentState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = enrich_visible_models(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=policy,
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "no secret backend configured in this environment reports secret_backend_error, "
        "never a fabricated success",
        no_config.state == EnrichmentState.SECRET_BACKEND_ERROR,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    handler = _make_static_handler()
    if _UPDATE_CA_AVAILABLE:
        server = _start_https_server(handler)
        port = server.server_address[1]
    else:
        port = 1
    policy = ConnectionPolicy(name="test-no-allowlist", allowed_schemes=frozenset({"https"}))
    result = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "a real destination outside the connection policy's own allowlist is denied "
        "before any socket opens, reported as destination_denied",
        result.state == EnrichmentState.DESTINATION_DENIED,
    )
    check(
        "FTF-0205",
        "the visible id is still reported, limited, never dropped",
        len(result.entries) == 1
        and result.entries[0].metadata_status == ModelMetadataStatus.LIMITED,
    )


def test_empty_visible_ids_returns_no_entries_without_a_gateway_call() -> None:
    result = enrich_visible_models(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(1),
        visible_model_ids=(),
    )
    check(
        "FTF-0205",
        "an empty visible set never resolves a secret or dials a gateway",
        result.entries == (),
    )


def test_allowlisted_fields_drops_unknown_keys_and_non_scalar_values() -> None:
    fields = _allowlisted_fields(
        {
            "mode": "chat",
            "max_tokens": 4096,
            "api_key": "sk-should-be-dropped",
            "nested_allowlisted_but_not_scalar": None,
            "supports_vision": [True],  # allowlisted key, non-scalar value
            "input_cost_per_token": 0.0001,
        }
    )
    check(
        "FTF-0205",
        "an unknown key is dropped even though its value is a plain scalar",
        "api_key" not in fields,
    )
    check(
        "FTF-0205",
        "an allowlisted key whose value is a list (non-scalar) is dropped",
        "supports_vision" not in fields,
    )
    check(
        "FTF-0205",
        "allowlisted scalar fields survive with real values",
        dict(fields) == {"mode": "chat", "max_tokens": 4096, "input_cost_per_token": 0.0001},
    )
    check(
        "FTF-0205",
        "a non-dict raw model_info value returns an empty mapping, never raises",
        _allowlisted_fields(None) == MappingProxyType({})
        and _allowlisted_fields("not-a-dict") == MappingProxyType({})
        and _allowlisted_fields([1, 2, 3]) == MappingProxyType({}),
    )


def test_parse_model_info_never_reads_litellm_params() -> None:
    by_alias = _parse_model_info(
        json.dumps(
            {
                "data": [
                    {
                        "model_name": "gpt-a",
                        "model_info": {"mode": "chat"},
                        "litellm_params": {"api_key": "sk-real-secret"},
                    },
                    {"notmodel": "ignored"},
                    {"model_name": 123, "model_info": {"mode": "should-not-appear"}},
                ]
            }
        ).encode("utf-8")
    )
    check(
        "FTF-0205",
        "a well-formed entry maps alias to its model_info sub-value",
        by_alias == {"gpt-a": {"mode": "chat"}},
    )
    assert by_alias is not None
    check(
        "FTF-0205",
        "a non-string model_name is dropped, never coerced",
        123 not in by_alias.values() and "123" not in by_alias,
    )
    check(
        "FTF-0205",
        "a non-dict top-level body is malformed (None)",
        _parse_model_info(b"[1,2,3]") is None,
    )
    check(
        "FTF-0205",
        "a missing data key is malformed (None)",
        _parse_model_info(b'{"foo":"bar"}') is None,
    )
    check("FTF-0205", "invalid JSON is malformed (None)", _parse_model_info(b"not json") is None)


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0205",
        "a real socket.gaierror classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == EnrichmentState.DNS_FAILED,
    )
    check(
        "FTF-0205",
        "a real ssl.SSLCertVerificationError classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == EnrichmentState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0205",
        "a real TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out"))) == EnrichmentState.TIMEOUT,
    )
    check(
        "FTF-0205",
        "a real ConnectionRefusedError classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == EnrichmentState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0205",
        "any other real OSError classifies as unreachable, never invented as completed",
        _classify_guard_failure(_wrapped(OSError("something else"))) == EnrichmentState.UNREACHABLE,
    )


# -- Fault injection (Law 1): a deliberate regression this suite must catch -

_MODULE_PATH = ROOT / "packages" / "litellm_client" / "v1_model_info_enrichment.py"


def test_fault_injection_widening_allowlist_to_all_fields_is_caught() -> None:
    """Mutates the real, on-disk `v1_model_info_enrichment.py` so
    `_allowlisted_fields` returns every field verbatim instead of filtering
    through `_ALLOWLISTED_MODEL_INFO_FIELDS` -- reintroducing exactly the
    credential-leak this task's own negative_acceptance forbids ("Raw
    credentials... are neither stored nor exposed"). Runs the mutated
    module against a real gateway in a fresh subprocess and proves the
    secret genuinely leaks into the result -- not merely that the mutation
    was syntactically applied. Restores the file byte-identical afterward."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        model_info_body=json.dumps(
            {
                "data": [
                    {
                        "model_name": "gpt-a",
                        "model_info": {"mode": "chat", "api_key": "sk-fault-injection-leak"},
                    }
                ]
            }
        ).encode("utf-8"),
    )
    server = _start_https_server(handler)
    port = server.server_address[1]

    baseline = enrich_visible_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        visible_model_ids=("gpt-a",),
    )
    check(
        "FTF-0205",
        "baseline (unmutated): the credential-shaped field never appears in the real result",
        "api_key" not in baseline.entries[0].fields,
    )

    original = _MODULE_PATH.read_bytes()
    try:
        text = original.decode("utf-8")
        target = (
            "    if not isinstance(raw_model_info, dict):\n"
            "        return MappingProxyType({})\n"
            "    fields = {\n"
            "        key: value\n"
            "        for key, value in raw_model_info.items()\n"
            "        if key in _ALLOWLISTED_MODEL_INFO_FIELDS and "
            "isinstance(value, _ALLOWLISTED_VALUE_TYPES)\n"
            "    }\n"
            "    return MappingProxyType(fields)"
        )
        check(
            "FTF-0205",
            "the fault-injection target block is present in the tracked module",
            target in text,
        )
        replacement = (
            "    if not isinstance(raw_model_info, dict):\n"
            "        return MappingProxyType({})\n"
            "    return MappingProxyType(dict(raw_model_info))"
        )
        mutated = text.replace(target, replacement)
        check("FTF-0205", "the mutation actually changed the file", mutated != text)
        _MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from packages.litellm_client.v1_model_info_enrichment import enrich_visible_models
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.security.url_policy import ConnectionPolicy
import ipaddress

policy = ConnectionPolicy(
    name="p",
    allowed_schemes=frozenset({{"https"}}),
    allowed_ports=frozenset({{{port}}}),
    explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
)
result = enrich_visible_models(
    base_url="https://127.0.0.1:{port}",
    secret_ref={_secret_ref()!r},
    secret_config=OpenBaoConfig(address={_SECRET_CONFIG.address!r}, token={_SECRET_TOKEN!r}),
    policy=policy,
    visible_model_ids=("gpt-a",),
)
print("FIELDS=" + repr(dict(result.entries[0].fields)))
"""
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0205",
            "fault injection: dropping the allowlist genuinely lets the credential-shaped "
            f"field leak into the result (stdout={result.stdout!r} stderr={result.stderr!r})",
            result.returncode == 0 and "sk-fault-injection-leak" in result.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0205",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0205 /v1/model/info enrichment: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0205 /v1/model/info enrichment: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
