#!/usr/bin/env python3
"""FTF-0207 -- Alias reconciliation.

Runnable without any database and without any external network egress:
`reconcile_aliases` is a pure, synchronous, no-I/O function (this task's own
`database_changes` clause -- "No independent schema change. Consume frozen
contract.") that combines an already-computed `ModelDiscoveryResult`
(FTF-0204, CERTIFIED) with an already-computed `ModelInfoEnrichmentResult`
(FTF-0205, CERTIFIED). Most checks build both inputs directly as dataclasses
-- no HTTP server, no secret backend, no TLS fixture is needed for the
reconciliation logic in isolation. `test_real_gateway_pipeline_reconciles_
end_to_end` additionally proves the full, real, wired pipeline: a real local
HTTPS gateway (with a real self-signed CA installed into this sandbox's own
trust store) and a real local secret-backend fixture feed genuine
`discover_models`/`enrich_visible_models` calls, whose real results this
task's own `reconcile_aliases` then combines -- same no-DB/no-external-
egress local-server pattern `tests/tasks/test_ftf_0204.py`/
`tests/tasks/test_ftf_0205.py` (both already CERTIFIED) established, reused
here rather than introducing a separate `tests/integration/` file and CI job
for a task with no live route or database of its own (this task's own
`database_changes` clause; same disclosed "no live HTTP route yet" scope
FTF-0202/FTF-0205 already established -- neither has a dedicated
`tests/integration/` file either).

This task's own acceptance clause ("Left-join case-sensitive alias
identities to metadata without replacing user-facing ID") is covered by
`test_left_join_attaches_metadata_by_exact_id`,
`test_user_facing_id_is_never_replaced_by_anything_enrichment_reports` and
`test_missing_enrichment_entry_falls_back_to_limited_without_dropping_alias`.
This task's own negative_acceptance ("Colliding aliases/upstream names never
merge credentials or grant a model absent from current accessible set") is
covered by `test_case_sensitive_matching_never_cross_attaches`,
`test_enrichment_only_alias_never_grants_a_model_absent_from_discovery` and
`test_duplicate_enrichment_ids_first_one_wins_deterministically`.

Run directly: python3 tests/tasks/test_ftf_0207.py
"""

from __future__ import annotations

import atexit
import dataclasses
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    ReconciledModel,
    reconcile_aliases,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_model_info_enrichment import (  # noqa: E402
    EnrichmentState,
    ModelInfoEnrichmentResult,
    ModelMetadataEntry,
    enrich_visible_models,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
    discover_models,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []
_MODULE_PATH = ROOT / "packages" / "litellm_client" / "alias_reconciliation.py"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NOW = datetime(2026, 9, 23, tzinfo=UTC)


def _discovery(*ids: str) -> ModelDiscoveryResult:
    models = tuple(DiscoveredModel(id=i, metadata_status=ModelMetadataStatus.LIMITED) for i in ids)
    return ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=models,
        model_count=len(models),
        content_hash="irrelevant-to-this-task",
        detail="gateway reachable and authorized",
        checked_at=_NOW,
    )


def _entry(model_id: str, fields: dict[str, object] | None = None) -> ModelMetadataEntry:
    resolved_fields = MappingProxyType(dict(fields) if fields else {})
    status = ModelMetadataStatus.ENRICHED if resolved_fields else ModelMetadataStatus.LIMITED
    return ModelMetadataEntry(id=model_id, metadata_status=status, fields=resolved_fields)


def _enrichment(*entries: ModelMetadataEntry) -> ModelInfoEnrichmentResult:
    return ModelInfoEnrichmentResult(
        state=EnrichmentState.COMPLETED,
        entries=entries,
        detail="gateway reachable and authorized",
        checked_at=_NOW,
    )


# -- Real local secret-backend fixture (OpenBao KV v2 read subset), same
# convention tests/tasks/test_ftf_0204.py/test_ftf_0205.py already established
# ---------------------------------------------------------------------------

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/org1"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = "sk-real-inference-key-0207"
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


def _secret_ref() -> str:
    return f"openbao://{_SECRET_MOUNT}/{_SECRET_PATH}/{_SECRET_FIELD}"


# -- Real local TLS fixture: same convention as tests/tasks/test_ftf_0204.py/
# tests/tasks/test_ftf_0205.py -------------------------------------------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0207-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0207-test-ca.crt")
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
            "/CN=FTF-0207 Test CA",
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
        "FTF-0207: this sandbox has no openssl/update-ca-certificates -- the "
        "live real-gateway end-to-end check is skipped and disclosed here; "
        "every pure reconciliation-logic check still runs and still counts. "
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


def _make_gateway_handler(*, valid_token: str = _SECRET_VALUE) -> type[BaseHTTPRequestHandler]:
    """A real, minimal LiteLLM-shaped gateway: `/v1/models` lists three
    authorized aliases (one with a same-name-different-case sibling in
    `/model/info`, proving the case-sensitive join survives a real HTTP
    round trip too), and `/model/info` additionally advertises an
    upstream-only alias the caller never listed as visible."""

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
            if auth != f"Bearer {valid_token}":
                self._respond(401, b'{"error":"invalid token"}')
                return
            if self.path == "/v1/models":
                self._respond(
                    200,
                    json.dumps(
                        {"data": [{"id": "gpt-a"}, {"id": "gpt-b"}, {"id": "Gpt-Case"}]}
                    ).encode("utf-8"),
                )
                return
            if self.path == "/model/info":
                self._respond(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {"model_name": "gpt-a", "model_info": {"mode": "chat"}},
                                {"model_name": "gpt-case", "model_info": {"mode": "wrong-case"}},
                                {
                                    "model_name": "unlisted-upstream-model",
                                    "model_info": {"mode": "chat"},
                                },
                            ]
                        }
                    ).encode("utf-8"),
                )
                return
            self._respond(404, b"")

    return _Handler


# -- Tests --------------------------------------------------------------


def test_left_join_attaches_metadata_by_exact_id() -> None:
    discovery = _discovery("gpt-a", "gpt-b")
    enrichment = _enrichment(
        _entry("gpt-a", {"mode": "chat", "max_tokens": 4096}),
        _entry("gpt-b", {}),
    )
    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    check("FTF-0207", "one reconciled model per discovered alias", result.model_count == 2)
    check("FTF-0207", "model_count matches len(models)", result.model_count == len(result.models))
    by_id = {m.id: m for m in result.models}
    check(
        "FTF-0207",
        "the enriched alias carries its real allowlisted fields",
        dict(by_id["gpt-a"].fields) == {"mode": "chat", "max_tokens": 4096}
        and by_id["gpt-a"].metadata_status == ModelMetadataStatus.ENRICHED,
    )
    gpt_b_limited = by_id["gpt-b"].metadata_status == ModelMetadataStatus.LIMITED
    check(
        "FTF-0207",
        "an authorized alias with no real metadata stays limited with empty fields",
        dict(by_id["gpt-b"].fields) == {} and gpt_b_limited,
    )
    check(
        "FTF-0207",
        "reconciled_at is a real timezone-aware timestamp",
        result.reconciled_at.tzinfo is not None,
    )


def test_missing_enrichment_entry_falls_back_to_limited_without_dropping_alias() -> None:
    """Section 3.1's "403/404 on enrichment does not invalidate a successful
    standard model list", reapplied at the reconciliation layer: an
    authorized alias absent from `enrichment.entries` entirely (e.g. the
    enrichment call itself failed) is never dropped from the reconciled
    result -- it survives as `limited`."""
    discovery = _discovery("gpt-a", "gpt-only-in-discovery")
    enrichment = _enrichment(_entry("gpt-a", {"mode": "chat"}))
    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    ids = [m.id for m in result.models]
    check(
        "FTF-0207",
        "every discovered alias survives reconciliation, in discovery order",
        ids == ["gpt-a", "gpt-only-in-discovery"],
    )
    missing = next(m for m in result.models if m.id == "gpt-only-in-discovery")
    check(
        "FTF-0207",
        "an alias missing from enrichment is retained as limited with empty fields",
        missing.metadata_status == ModelMetadataStatus.LIMITED and dict(missing.fields) == {},
    )


def test_case_sensitive_matching_never_cross_attaches() -> None:
    """This task's own negative_acceptance: "Colliding aliases... never
    merge credentials or grant a model absent from current accessible
    set." A same-name-different-case enrichment entry is a different
    identity -- it must never attach its fields to the differently-cased
    authorized alias, and it must never appear as its own new entry
    either."""
    discovery = _discovery("Org-Agent-Small")
    enrichment = _enrichment(
        _entry("org-agent-small", {"mode": "chat", "max_tokens": 999}),
    )
    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    check(
        "FTF-0207",
        "exactly the one discovered (correctly-cased) alias is reconciled, never two",
        result.model_count == 1,
    )
    entry = result.models[0]
    check(
        "FTF-0207",
        "the differently-cased enrichment entry's fields never attach to the real alias",
        entry.id == "Org-Agent-Small" and dict(entry.fields) == {},
    )
    check(
        "FTF-0207",
        "the differently-cased alias falls back to limited, not enriched",
        entry.metadata_status == ModelMetadataStatus.LIMITED,
    )


def test_enrichment_only_alias_never_grants_a_model_absent_from_discovery() -> None:
    """This task's own negative_acceptance, the "upstream names" half:
    an id known only through enrichment (whether a genuine upstream/
    provider name or a gateway-metadata-only alias) can never newly
    appear in the reconciled result -- discovery is the sole source of
    which models exist."""
    discovery = _discovery("gpt-a")
    enrichment = _enrichment(
        _entry("gpt-a", {"mode": "chat"}),
        _entry("unlisted-upstream-model", {"mode": "chat", "max_tokens": 1}),
    )
    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    ids = {m.id for m in result.models}
    check(
        "FTF-0207",
        "an id known only through enrichment is never added to the reconciled result",
        "unlisted-upstream-model" not in ids,
    )
    check("FTF-0207", "exactly the one authorized alias is reconciled", ids == {"gpt-a"})


def test_duplicate_enrichment_ids_first_one_wins_deterministically() -> None:
    """`enrich_visible_models` never produces duplicate ids in its own
    `entries` (its own internal dedup already guarantees this), but this
    module independently guards against a hand-built/adversarial
    `ModelInfoEnrichmentResult` that does -- a real collision inside the
    enrichment data itself must resolve deterministically, never crash,
    and never silently let a later duplicate overwrite an already-
    reconciled field."""
    discovery = _discovery("gpt-a")
    enrichment = _enrichment(
        _entry("gpt-a", {"mode": "chat"}),
        _entry("gpt-a", {"mode": "completion", "max_tokens": 1}),
    )
    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    check("FTF-0207", "exactly one reconciled entry for the colliding id", result.model_count == 1)
    check(
        "FTF-0207",
        "the first occurrence wins deterministically, never the last",
        dict(result.models[0].fields) == {"mode": "chat"},
    )


def test_user_facing_id_is_never_replaced_by_anything_enrichment_reports() -> None:
    discovery = _discovery("alias-one", "alias-two")
    enrichment = _enrichment(
        _entry("alias-one", {"mode": "chat"}),
        _entry("alias-two", {"mode": "chat"}),
    )
    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    check(
        "FTF-0207",
        "every reconciled id is exactly discovery's own alias string, in discovery order",
        [m.id for m in result.models] == ["alias-one", "alias-two"],
    )


def test_empty_discovery_produces_an_empty_reconciled_result() -> None:
    """A failed/empty discovery (e.g. `DiscoveryState.INVALID_TOKEN`, which
    already returns an empty `models` tuple, per FTF-0204's own
    already-CERTIFIED fail-closed design) reconciles to an empty result,
    never an exception and never a fabricated entry."""
    discovery = ModelDiscoveryResult(
        state=DiscoveryState.INVALID_TOKEN,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway rejected the inference credential",
        checked_at=_NOW,
    )
    enrichment = _enrichment(_entry("gpt-a", {"mode": "chat"}))
    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    check("FTF-0207", "an empty discovery reconciles to an empty result", result.model_count == 0)
    check("FTF-0207", "models is an empty tuple, not None", result.models == ())


def test_reconcile_aliases_never_raises_for_ordinary_inputs() -> None:
    discovery = _discovery("gpt-a")
    enrichment = _enrichment()
    try:
        result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
        raised = False
    except Exception:  # noqa: BLE001
        result = None
        raised = True
    check("FTF-0207", "reconciling against an empty enrichment result never raises", not raised)
    check(
        "FTF-0207",
        "the sole discovered alias still survives as limited",
        result is not None
        and result.model_count == 1
        and result.models[0].metadata_status == ModelMetadataStatus.LIMITED,
    )


def test_reconciled_model_dataclass_shape() -> None:
    model = ReconciledModel(
        id="gpt-a", metadata_status=ModelMetadataStatus.LIMITED, fields=MappingProxyType({})
    )
    try:
        model.id = "mutated"  # type: ignore[misc]
        mutated = True
    except dataclasses.FrozenInstanceError:
        mutated = False
    check("FTF-0207", "ReconciledModel is frozen (immutable)", not mutated)


def test_fault_injection_reintroducing_an_authorization_union_is_caught() -> None:
    """Law 1: demonstrates the negative_acceptance actually fails when the
    left-join discipline is removed. Mutates the real on-disk module so the
    result is built by iterating `enrichment.entries` (a union) instead of
    `discovery.models` (a left join), runs the mutated module in a fresh
    subprocess, and proves the exact "grant a model absent from current
    accessible set" failure mode this task forbids actually reproduces.
    Reverts the file byte-identical afterward."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    target = "    models = []\n    for discovered in discovery.models:\n"
    check(
        "FTF-0207",
        "the fault-injection target block is present in the tracked module",
        target in text,
    )
    replacement = "    models = []\n    for discovered in enrichment.entries:\n"
    mutated = text.replace(target, replacement, 1)
    check("FTF-0207", "the mutation actually changed the file", mutated != text)

    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from packages.litellm_client.alias_reconciliation import reconcile_aliases
from packages.litellm_client.v1_model_info_enrichment import (
    EnrichmentState, ModelInfoEnrichmentResult, ModelMetadataEntry,
)
from packages.litellm_client.v1_models_discovery import (
    DiscoveredModel, DiscoveryState, ModelDiscoveryResult, ModelMetadataStatus,
)
from types import MappingProxyType
from datetime import datetime, UTC

discovery = ModelDiscoveryResult(
    state=DiscoveryState.DISCOVERED,
    models=(DiscoveredModel(id="gpt-a", metadata_status=ModelMetadataStatus.LIMITED),),
    model_count=1, content_hash="x", detail="d", checked_at=datetime.now(UTC),
)
enrichment = ModelInfoEnrichmentResult(
    state=EnrichmentState.COMPLETED,
    entries=(
        ModelMetadataEntry(
            id="unlisted-upstream-model",
            metadata_status=ModelMetadataStatus.ENRICHED,
            fields=MappingProxyType({{"mode": "chat"}}),
        ),
    ),
    detail="d", checked_at=datetime.now(UTC),
)
result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
print("IDS=" + repr(sorted(m.id for m in result.models)))
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0207",
            "fault injection: replacing the left join with a union genuinely grants a model "
            f"absent from the accessible set (stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r})",
            completed.returncode == 0 and "unlisted-upstream-model" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0207",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def test_fault_injection_case_insensitive_matching_is_caught() -> None:
    """A second, independent fault injection (Law 1): mutates the real
    on-disk module so the join key is case-folded on both sides (a
    plausible "helpful" bug -- treating `gpt-a` and `GPT-A` as the same
    identity), runs the mutated module in a fresh subprocess, and proves
    this task's own case-sensitivity negative test genuinely fails when
    that discipline is removed. Reverts the file byte-identical afterward."""
    original = _MODULE_PATH.read_bytes()
    text = original.decode("utf-8")
    build_target = "        enrichment_by_id[entry.id] = entry"
    lookup_target = "        matched = enrichment_by_id.get(discovered.id)"
    check("FTF-0207", "the build-side fault-injection target is present", build_target in text)
    check("FTF-0207", "the lookup-side fault-injection target is present", lookup_target in text)
    mutated = text.replace(
        build_target, "        enrichment_by_id[entry.id.casefold()] = entry", 1
    ).replace(lookup_target, "        matched = enrichment_by_id.get(discovered.id.casefold())", 1)
    check("FTF-0207", "the mutation actually changed the file", mutated != text)

    try:
        _MODULE_PATH.write_text(mutated, encoding="utf-8")
        probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from packages.litellm_client.alias_reconciliation import reconcile_aliases
from packages.litellm_client.v1_model_info_enrichment import (
    EnrichmentState, ModelInfoEnrichmentResult, ModelMetadataEntry,
)
from packages.litellm_client.v1_models_discovery import (
    DiscoveredModel, DiscoveryState, ModelDiscoveryResult, ModelMetadataStatus,
)
from types import MappingProxyType
from datetime import datetime, UTC

discovery = ModelDiscoveryResult(
    state=DiscoveryState.DISCOVERED,
    models=(DiscoveredModel(id="Org-Agent-Small", metadata_status=ModelMetadataStatus.LIMITED),),
    model_count=1, content_hash="x", detail="d", checked_at=datetime.now(UTC),
)
enrichment = ModelInfoEnrichmentResult(
    state=EnrichmentState.COMPLETED,
    entries=(
        ModelMetadataEntry(
            id="org-agent-small",
            metadata_status=ModelMetadataStatus.ENRICHED,
            fields=MappingProxyType({{"mode": "chat", "max_tokens": 999}}),
        ),
    ),
    detail="d", checked_at=datetime.now(UTC),
)
result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
print("FIELDS=" + repr(dict(result.models[0].fields)))
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0207",
            "fault injection: case-folding the join key genuinely cross-attaches a "
            f"differently-cased entry's fields (stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r})",
            completed.returncode == 0 and "max_tokens" in completed.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0207",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def test_real_gateway_pipeline_reconciles_end_to_end() -> None:
    """This task's own acceptance clause's "real relevant integration...
    evidence recorded": a real local HTTPS gateway (real TLS, real
    self-signed CA trusted by this sandbox for the duration of the run) and
    a real local OpenBao-shaped secret backend feed genuine, unmodified
    `discover_models`/`enrich_visible_models` calls -- never a mock of
    either -- and this task's own `reconcile_aliases` combines their real
    results. Proves the full pipeline's real acceptance/negative_acceptance
    behavior together, not just each half in isolation: `gpt-a` is enriched
    for real; `gpt-b` is authorized but has no real `/model/info` row and
    stays limited; `Gpt-Case` (authorized) is never cross-attached to
    `/model/info`'s differently-cased `gpt-case` row; and the real gateway's
    `unlisted-upstream-model` row never appears in the reconciled result."""
    if not _UPDATE_CA_AVAILABLE:
        return
    server = _start_https_server(_make_gateway_handler())
    port = server.server_address[1]
    base_url = f"https://127.0.0.1:{port}"
    policy = _policy_for(port)

    discovery = discover_models(
        base_url=base_url, secret_ref=_secret_ref(), secret_config=_SECRET_CONFIG, policy=policy
    )
    check(
        "FTF-0207",
        "the real gateway's /v1/models call is genuinely authorized",
        discovery.state == DiscoveryState.DISCOVERED,
    )
    check(
        "FTF-0207",
        "the real gateway reports exactly the three authorized aliases",
        [m.id for m in discovery.models] == ["gpt-a", "gpt-b", "Gpt-Case"],
    )

    enrichment = enrich_visible_models(
        base_url=base_url,
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=tuple(m.id for m in discovery.models),
    )
    check(
        "FTF-0207",
        "the real gateway's /model/info call genuinely completes",
        enrichment.state == EnrichmentState.COMPLETED,
    )

    result = reconcile_aliases(discovery=discovery, enrichment=enrichment)
    by_id = {m.id: m for m in result.models}
    check(
        "FTF-0207",
        "reconciliation against real gateway results still returns exactly the three "
        "authorized aliases",
        set(by_id) == {"gpt-a", "gpt-b", "Gpt-Case"},
    )
    check(
        "FTF-0207",
        "the real, correctly-cased alias is genuinely enriched end-to-end",
        by_id["gpt-a"].metadata_status == ModelMetadataStatus.ENRICHED
        and dict(by_id["gpt-a"].fields) == {"mode": "chat"},
    )
    check(
        "FTF-0207",
        "an authorized alias absent from the real /model/info response stays limited",
        by_id["gpt-b"].metadata_status == ModelMetadataStatus.LIMITED
        and dict(by_id["gpt-b"].fields) == {},
    )
    check(
        "FTF-0207",
        "the real gateway's differently-cased /model/info row never cross-attaches",
        by_id["Gpt-Case"].metadata_status == ModelMetadataStatus.LIMITED
        and dict(by_id["Gpt-Case"].fields) == {},
    )
    check(
        "FTF-0207",
        "the real gateway's upstream-only alias never appears in the reconciled result",
        "unlisted-upstream-model" not in by_id,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0207 alias reconciliation: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0207 alias reconciliation: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
