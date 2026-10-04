#!/usr/bin/env python3
"""FTF-0500C -- Database/API/trace source contract and fail-closed
validation (connector-level, no-live-Postgres unit/fault tests).

The real live-Postgres round trip (bounded/cursor pagination against a
real table, a real lost-watermark fault after a real concurrent delete, a
real least-privileged read-only role whose writes are genuinely denied by
both the database's own grants and this module's own `READ ONLY` transaction
mode) is `tests/integration/test_ftf_0500c_database_api_trace_source.py` --
same "no container-registry egress in this repo's interactive dev sandbox"
split every prior Postgres-backed task in this repository already
established (FTF-0102/FTF-0104/FTF-0500/etc.); that evidence was produced
locally against a directly-installed PostgreSQL 16 and is wired into CI's
own dedicated `database-api-trace-source-integration` job.

This suite proves, without any live database:

1. Every Postgres identifier this module accepts (`schema`/`table`/
   `columns`/`stable_key_column`) is validated *before* any connection is
   attempted -- a fault-injected `connect` callable that raises if called
   proves a malicious/malformed identifier (a SQL-injection-shaped table
   name, an empty string, a too-long identifier, a non-string value) never
   reaches a real connection attempt (`PostgresIdentifierError`), which is
   the actual mechanism behind "no user SQL," not merely a disclosed
   convention.
2. `max_preview_records < 1` and an empty `columns` tuple are refused
   before any connection attempt, identically.
3. A real connection-level failure (`OSError`, e.g. DNS/refused/timeout) is
   wrapped as `SourceUnavailableError`, never silently swallowed or
   converted into a fabricated empty result.
4. The real REST/trace connector (`validate_and_preview_rest_trace_source`)
   is exercised end-to-end against a real local HTTPS server (self-signed
   CA installed into this sandbox's own trust store, same convention
   `tests/tasks/test_ftf_04a05.py`/`test_ftf_0500b.py` already established)
   through the real, unmodified `packages/security/outbound_guard.py`
   socket/TLS path -- never a mocked `guarded_fetch`:
     - real bounded cursor pagination across multiple real pages, stopping
       at `max_preview_records`;
     - a real HTTP 429 with a real `Retry-After` header, retried a bounded
       number of times before succeeding;
     - a real HTTP 429 that never stops, exhausting the bounded retry
       budget (`RateLimitRetriesExhaustedError`);
     - this task's own negative_acceptance made real for a REST source: a
       real snapshot-version (`ETag`) header that changes between two real
       pages of the same listing is caught
       (`SourceChangedDuringListingError`), with a disclosed real fault
       injection (Law 1) proving the assertion genuinely fails when the
       comparison is disabled, then reverted byte-identical;
     - a real response missing the declared `records_field`
       (`UnknownSourceContractError` -- this task's own other
       negative_acceptance half, made real for a REST source);
     - cooperative cancellation between real page fetches;
     - a real, deliberately-endless-cursor endpoint that is bounded by
       `max_page_fetches` rather than looping forever.
5. `manifest_hash` is a real, deterministic function of the returned
   records (same content -> same hash; different content -> different
   hash) -- never a random/unstable value.

Run directly: python3 tests/tasks/test_ftf_0500c.py
"""

from __future__ import annotations

import atexit
import hashlib
import ipaddress
import json
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

import app.datasets.database_api_trace_source_contract_and_fail_closed_validation as source_module  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


MODULE_PATH = (
    ROOT
    / "apps"
    / "api"
    / "app"
    / "datasets"
    / "database_api_trace_source_contract_and_fail_closed_validation.py"
)

# -- 1/2/3. Postgres identifier/config validation and connection-failure
#    wrapping, no live database -----------------------------------------


class _AssertNeverCalledConnect:
    """A fake `connect` that fails the test if it is ever invoked -- proves
    a rejected identifier/config is refused *before* any connection
    attempt, not merely eventually caught by a real database error."""

    def __call__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "connect() was called despite an invalid identifier/config -- validation must "
            "happen before any connection attempt"
        )


def _config(**overrides: object) -> source_module.PostgresSourceConfig:
    base: dict[str, object] = {
        "conninfo": "postgresql://unused:unused@127.0.0.1:1/unused",
        "schema": "source_schema",
        "table": "records",
        "columns": ("id", "name"),
        "stable_key_column": "id",
    }
    base.update(overrides)
    return source_module.PostgresSourceConfig(**base)  # type: ignore[arg-type]


def test_sql_injection_shaped_table_name_is_rejected_before_any_connection() -> None:
    try:
        source_module.validate_and_preview_postgres_source(
            _config(table="records; DROP TABLE records; --"),
            connect=_AssertNeverCalledConnect(),  # type: ignore[arg-type]
        )
        check("FTF-0500C", "negative_acceptance: injection-shaped table name is rejected", False)
    except source_module.PostgresIdentifierError:
        check("FTF-0500C", "negative_acceptance: injection-shaped table name is rejected", True)


def test_injection_shaped_column_and_schema_and_key_are_rejected() -> None:
    for field_name, kwargs in (
        ("columns", {"columns": ("id", "name; --")}),
        ("schema", {"schema": "public; --"}),
        ("stable_key_column", {"stable_key_column": "id; --"}),
        ("schema (empty)", {"schema": ""}),
        ("schema (too long)", {"schema": "a" * 64}),
        ("schema (non-str)", {"schema": 123}),
    ):
        try:
            source_module.validate_and_preview_postgres_source(
                _config(**kwargs),
                connect=_AssertNeverCalledConnect(),  # type: ignore[arg-type]
            )
            check("FTF-0500C", f"negative_acceptance: bad {field_name} is rejected", False)
        except source_module.PostgresIdentifierError:
            check("FTF-0500C", f"negative_acceptance: bad {field_name} is rejected", True)


def test_empty_columns_and_bad_max_preview_records_are_rejected() -> None:
    try:
        source_module.validate_and_preview_postgres_source(
            _config(columns=()),
            connect=_AssertNeverCalledConnect(),  # type: ignore[arg-type]
        )
        check("FTF-0500C", "negative_acceptance: empty columns tuple is rejected", False)
    except source_module.PostgresIdentifierError:
        check("FTF-0500C", "negative_acceptance: empty columns tuple is rejected", True)

    try:
        source_module.validate_and_preview_postgres_source(
            _config(max_preview_records=0),
            connect=_AssertNeverCalledConnect(),  # type: ignore[arg-type]
        )
        check("FTF-0500C", "negative_acceptance: max_preview_records < 1 is rejected", False)
    except source_module.SourceUnavailableError:
        check("FTF-0500C", "negative_acceptance: max_preview_records < 1 is rejected", True)


def test_real_connection_failure_is_wrapped_not_swallowed() -> None:
    def _raising_connect(*args: object, **kwargs: object) -> None:
        raise OSError("connection refused")

    try:
        source_module.validate_and_preview_postgres_source(
            _config(),
            connect=_raising_connect,  # type: ignore[arg-type]
        )
        check("FTF-0500C", "a real connection failure is raised, never a fabricated result", False)
    except source_module.SourceUnavailableError:
        check("FTF-0500C", "a real connection failure is raised, never a fabricated result", True)


# -- 4. REST/trace connector, real local HTTPS fixture ----------------------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0500c-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0500c-test-ca.crt")
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
            "/CN=FTF-0500C Test CA",
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


# Fixture state, keyed by request path so different tests use independent
# scenarios on the same server.
_TOTAL_RECORDS = 25
_ALL_RECORDS = [{"id": i, "value": f"row-{i}"} for i in range(1, _TOTAL_RECORDS + 1)]

_RATE_LIMIT_TRIP_COUNTS: dict[str, int] = {}


class _RestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send_json(
        self,
        payload: dict[str, object],
        *,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlsplit

        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        try:
            cursor = int(query.get("cursor", ["0"])[0])
        except ValueError:
            # Non-integer cursors (e.g. /repeating-cursor's "stuck",
            # /url-unsafe-cursor's own value) are handled by their own path
            # branch below, which never reads this integer form.
            cursor = 0
        limit = int(query.get("limit", ["5"])[0])

        if parsed.path == "/pages":
            page = _ALL_RECORDS[cursor : cursor + limit]
            next_cursor = cursor + limit if cursor + limit < len(_ALL_RECORDS) else None
            self._send_json(
                {"records": page, "next_cursor": next_cursor},
                extra_headers={"ETag": "snapshot-v1"},
            )
            return

        if parsed.path == "/changing-snapshot":
            page = _ALL_RECORDS[cursor : cursor + limit]
            next_cursor = cursor + limit if cursor + limit < len(_ALL_RECORDS) else None
            etag = "snapshot-v1" if cursor == 0 else "snapshot-v2"
            self._send_json(
                {"records": page, "next_cursor": next_cursor}, extra_headers={"ETag": etag}
            )
            return

        if parsed.path == "/snapshot-then-missing":
            page = _ALL_RECORDS[cursor : cursor + limit]
            next_cursor = cursor + limit if cursor + limit < len(_ALL_RECORDS) else None
            if cursor == 0:
                self._send_json(
                    {"records": page, "next_cursor": next_cursor},
                    extra_headers={"ETag": "snapshot-v1"},
                )
            else:
                # A later page that simply stops sending the header
                # entirely, rather than sending a changed value -- the
                # 2026-10-01 L2 review's own finding: the original
                # `if observed is not None:` guard silently accepted this.
                self._send_json({"records": page, "next_cursor": next_cursor})
            return

        if parsed.path == "/missing-records-field":
            self._send_json({"items": [], "next_cursor": None})
            return

        if parsed.path == "/header-appears-late":
            # 2026-10-04 L2 finding: the inverse of /snapshot-then-missing --
            # page one carries no ETag at all, and a later page starts
            # sending one. Equally inconsistent as a changed value or a
            # dropped header; the original tri-state-less tracking silently
            # accepted this and labelled the whole listing with the late
            # page's own version.
            page = _ALL_RECORDS[cursor : cursor + limit]
            next_cursor = cursor + limit if cursor + limit < len(_ALL_RECORDS) else None
            if cursor == 0:
                self._send_json({"records": page, "next_cursor": next_cursor})
            else:
                self._send_json(
                    {"records": page, "next_cursor": next_cursor},
                    extra_headers={"ETag": "snapshot-v1"},
                )
            return

        if parsed.path == "/multipage-no-snapshot-header":
            # 2026-10-04 L2 finding: a declared snapshot_version_header that
            # never appears on any page of a genuinely multi-page listing
            # used to return a quiet success with snapshot_version=None --
            # Section 67.1 requires SNAPSHOT_UNSTABLE/a block, not silence,
            # when consistency across pages cannot be verified.
            page = _ALL_RECORDS[cursor : cursor + limit]
            next_cursor = cursor + limit if cursor + limit < len(_ALL_RECORDS) else None
            self._send_json({"records": page, "next_cursor": next_cursor})
            return

        if parsed.path == "/repeating-cursor":
            # 2026-10-04 L2 finding: a misbehaving endpoint that returns the
            # identical next_cursor forever -- unbounded only by
            # max_page_fetches, and every repeated fetch re-adds duplicate
            # records into the preview.
            self._send_json(
                {"records": [{"id": 1, "value": "x"}], "next_cursor": "stuck"},
                extra_headers={"ETag": "snapshot-v1"},
            )
            return

        if parsed.path == "/url-unsafe-cursor":
            raw_cursor = query.get("cursor", [None])[0]
            if raw_cursor is None:
                self._send_json({"records": [{"id": 1}], "next_cursor": "a&b=c d#e"})
                return
            if raw_cursor == "a&b=c d#e":
                self._send_json({"records": [{"id": 2}], "next_cursor": None})
                return
            # The cursor arrived corrupted (not URL-encoded/decoded
            # correctly) -- a distinguishable third record proves the
            # mismatch rather than silently returning an empty page.
            self._send_json({"records": [{"id": 999}], "next_cursor": None})
            return

        if parsed.path == "/rate-limited-then-ok":
            key = "trip"
            _RATE_LIMIT_TRIP_COUNTS[key] = _RATE_LIMIT_TRIP_COUNTS.get(key, 0) + 1
            if _RATE_LIMIT_TRIP_COUNTS[key] <= 2:
                self._send_json({}, status=429, extra_headers={"Retry-After": "0"})
                return
            self._send_json({"records": _ALL_RECORDS[:3], "next_cursor": None})
            return

        if parsed.path == "/always-rate-limited":
            self._send_json({}, status=429, extra_headers={"Retry-After": "0"})
            return

        if parsed.path == "/endless-tiny-pages":
            # Always one record, always a next_cursor -- a real endpoint
            # that never ends; only `max_page_fetches` can bound this.
            self._send_json({"records": [{"id": cursor, "value": "x"}], "next_cursor": cursor + 1})
            return

        self._send_json({"error": "not found"}, status=404)


def _start_https_server(certfile: Path, keyfile: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RestHandler)
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
        "FTF-0500C: this sandbox has no openssl/update-ca-certificates/passwordless sudo -- the "
        "live TLS-backed REST/trace connector checks are skipped and disclosed here; GitHub "
        "Actions' ubuntu-latest runner has all three."
    )


def _policy() -> ConnectionPolicy:
    assert _PORT is not None
    return ConnectionPolicy(
        name="test-database-api-trace-source",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=False,
    )


def _rest_config(path: str, **overrides: object) -> source_module.RestTraceSourceConfig:
    assert _BASE_URL is not None
    base: dict[str, object] = {
        "base_url": f"{_BASE_URL}{path}",
        "records_field": "records",
        "cursor_response_field": "next_cursor",
        "cursor_query_param": "cursor",
        "page_size": 5,
        "max_preview_records": source_module.DEFAULT_MAX_PREVIEW_RECORDS,
    }
    base.update(overrides)
    return source_module.RestTraceSourceConfig(**base)  # type: ignore[arg-type]


def test_rest_connector_paginates_across_real_pages_bounded_by_max_preview_records() -> None:
    if _BASE_URL is None:
        return
    result = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/pages", max_preview_records=12), policy=_policy()
    )
    check(
        "FTF-0500C",
        "acceptance: bounded pagination returns exactly max_preview_records",
        len(result.records) == 12,
    )
    check(
        "FTF-0500C",
        "acceptance: records are returned in real, undisturbed source order",
        [r["id"] for r in result.records] == list(range(1, 13)),
    )
    check(
        "FTF-0500C",
        "acceptance: truncated is True when more real records remain",
        result.truncated is True,
    )
    check(
        "FTF-0500C",
        "acceptance: a real, stable snapshot-version header is recorded",
        result.snapshot_version == "snapshot-v1",
    )


def test_rest_connector_marks_untruncated_when_listing_genuinely_ends() -> None:
    if _BASE_URL is None:
        return
    result = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/pages", max_preview_records=1000), policy=_policy()
    )
    check(
        "FTF-0500C",
        "acceptance: a real listing that genuinely ends is not falsely marked truncated",
        result.truncated is False and len(result.records) == _TOTAL_RECORDS,
    )


def test_rest_connector_retries_bounded_on_real_429_then_succeeds() -> None:
    if _BASE_URL is None:
        return
    _RATE_LIMIT_TRIP_COUNTS.clear()
    result = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/rate-limited-then-ok", max_preview_records=3),
        policy=_policy(),
        sleep=lambda _seconds: None,
    )
    check(
        "FTF-0500C",
        "acceptance: a real bounded 429 retry (honoring Retry-After) eventually succeeds",
        len(result.records) == 3,
    )


def test_rest_connector_exhausts_bounded_retries_on_persistent_429() -> None:
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config("/always-rate-limited", max_rate_limit_retries=2),
            policy=_policy(),
            sleep=lambda _seconds: None,
        )
        check("FTF-0500C", "a persistently-429 source exhausts the bounded retry budget", False)
    except source_module.RateLimitRetriesExhaustedError:
        check("FTF-0500C", "a persistently-429 source exhausts the bounded retry budget", True)


def test_rest_connector_catches_a_real_snapshot_version_change_mid_listing() -> None:
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config("/changing-snapshot", max_preview_records=1000), policy=_policy()
        )
        check(
            "FTF-0500C",
            "negative_acceptance: a real snapshot-version change mid-listing is caught",
            False,
        )
    except source_module.SourceChangedDuringListingError:
        check(
            "FTF-0500C",
            "negative_acceptance: a real snapshot-version change mid-listing is caught",
            True,
        )


def test_rest_connector_catches_a_missing_snapshot_header_after_one_was_seen() -> None:
    """2026-10-01 L2 finding: the original `if observed is not None:` guard
    silently accepted a later page that simply stopped sending the
    snapshot-version header after an earlier page carried one -- treated as
    "nothing to compare" instead of the same mid-listing inconsistency a
    changed value is. `/snapshot-then-missing` sends `ETag: snapshot-v1` on
    page one and no `ETag` header at all on page two."""
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config("/snapshot-then-missing", max_preview_records=1000), policy=_policy()
        )
        check(
            "FTF-0500C",
            "negative_acceptance: a real later page that drops the snapshot-version header "
            "after an earlier page carried one is caught, not silently accepted",
            False,
        )
    except source_module.SourceChangedDuringListingError:
        check(
            "FTF-0500C",
            "negative_acceptance: a real later page that drops the snapshot-version header "
            "after an earlier page carried one is caught, not silently accepted",
            True,
        )


def test_disabling_the_snapshot_version_check_genuinely_fails_the_assertion() -> None:
    """Law 1, disclosed real fault injection: temporarily disables the
    snapshot-version comparison directly in the module under test, proves
    both negative-test assertions above genuinely fail without it, then
    reverts the file byte-identical."""
    if _BASE_URL is None:
        return
    original = MODULE_PATH.read_text()
    original_hash = hashlib.md5(original.encode("utf-8")).hexdigest()
    disabled = original.replace(
        "elif observed != snapshot_version:",
        "elif False:  # FTF-0500C fault injection -- disabled for this check",
        1,
    )
    if disabled == original:
        check(
            "FTF-0500C",
            "fault injection: the snapshot-version comparison line was found to disable",
            False,
        )
        return
    try:
        MODULE_PATH.write_text(disabled)
        import importlib

        importlib.reload(source_module)
        try:
            result = source_module.validate_and_preview_rest_trace_source(
                _rest_config("/changing-snapshot", max_preview_records=1000), policy=_policy()
            )
            genuinely_failed_without_the_check = True
            _ = result
        except source_module.SourceChangedDuringListingError:
            genuinely_failed_without_the_check = False
        check(
            "FTF-0500C",
            "fault injection: disabling the real comparison genuinely breaks the changed-value "
            "assertion above (proves it was testing something real, not vacuously passing)",
            genuinely_failed_without_the_check,
        )
        try:
            result = source_module.validate_and_preview_rest_trace_source(
                _rest_config("/snapshot-then-missing", max_preview_records=1000), policy=_policy()
            )
            missing_header_genuinely_failed_without_the_check = True
            _ = result
        except source_module.SourceChangedDuringListingError:
            missing_header_genuinely_failed_without_the_check = False
        check(
            "FTF-0500C",
            "fault injection: disabling the real comparison genuinely breaks the "
            "missing-header-after-seen assertion above too (proves it was testing something "
            "real, not vacuously passing)",
            missing_header_genuinely_failed_without_the_check,
        )
    finally:
        MODULE_PATH.write_text(original)
        import importlib

        importlib.reload(source_module)
        restored_hash = hashlib.md5(MODULE_PATH.read_text().encode("utf-8")).hexdigest()
        check(
            "FTF-0500C",
            "fault injection: the module file was reverted byte-identical after the injection",
            restored_hash == original_hash,
        )


def test_rest_connector_refuses_a_response_missing_the_declared_records_field() -> None:
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config("/missing-records-field"), policy=_policy()
        )
        check(
            "FTF-0500C",
            "negative_acceptance: an unknown REST contract (missing records_field) is refused, "
            "not fabricated as an empty preview",
            False,
        )
    except source_module.UnknownSourceContractError:
        check(
            "FTF-0500C",
            "negative_acceptance: an unknown REST contract (missing records_field) is refused, "
            "not fabricated as an empty preview",
            True,
        )


def test_rest_connector_cooperative_cancellation_stops_between_real_pages() -> None:
    if _BASE_URL is None:
        return
    calls = {"n": 0}

    def _cancel_after_first_page() -> bool:
        calls["n"] += 1
        return calls["n"] >= 1

    result = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/pages", max_preview_records=1000),
        policy=_policy(),
        cancellation_check=_cancel_after_first_page,
    )
    check(
        "FTF-0500C",
        "acceptance: cooperative cancellation stops pagination between real page fetches",
        result.cancelled is True and len(result.records) == 5,
    )


def test_rest_connector_bounded_by_max_page_fetches_against_a_real_endless_endpoint() -> None:
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config(
                "/endless-tiny-pages",
                max_preview_records=1000,
                max_page_fetches=5,
                page_size_query_param=None,
            ),
            policy=_policy(),
        )
        check(
            "FTF-0500C",
            "acceptance: a real endpoint that never ends is bounded by max_page_fetches",
            False,
        )
    except source_module.SourceUnavailableError:
        check(
            "FTF-0500C",
            "acceptance: a real endpoint that never ends is bounded by max_page_fetches",
            True,
        )


def test_manifest_hash_is_real_and_deterministic() -> None:
    if _BASE_URL is None:
        return
    first = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/pages", max_preview_records=5), policy=_policy()
    )
    second = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/pages", max_preview_records=5), policy=_policy()
    )
    third = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/pages", max_preview_records=6), policy=_policy()
    )
    check(
        "FTF-0500C",
        "acceptance: manifest_hash is deterministic over identical content",
        first.manifest_hash == second.manifest_hash,
    )
    check(
        "FTF-0500C",
        "acceptance: manifest_hash differs for genuinely different content",
        first.manifest_hash != third.manifest_hash,
    )


def test_rest_connector_catches_a_header_that_appears_only_on_a_later_page() -> None:
    """2026-10-04 L2 finding: the inverse of the already-caught
    missing-after-seen case -- page one has no snapshot-version header at
    all, and a later page starts sending one. Equally inconsistent; the
    original code only ever compared against a value, so "no value yet"
    never triggered a comparison at all."""
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config("/header-appears-late", max_preview_records=1000), policy=_policy()
        )
        check(
            "FTF-0500C",
            "negative_acceptance: a header that appears only on a later page (after page one "
            "had none) is caught, not silently adopted as the listing's version",
            False,
        )
    except source_module.SourceChangedDuringListingError:
        check(
            "FTF-0500C",
            "negative_acceptance: a header that appears only on a later page (after page one "
            "had none) is caught, not silently adopted as the listing's version",
            True,
        )


def test_rest_connector_blocks_a_multipage_listing_that_never_sends_the_declared_header() -> None:
    """2026-10-04 L2 finding: Section 67.1 -- "if consistency cannot be
    guaranteed, show SNAPSHOT_UNSTABLE and block production data use." A
    declared snapshot_version_header that never once appears across a real
    multi-page listing used to return a quiet, successful
    snapshot_version=None instead."""
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config("/multipage-no-snapshot-header", max_preview_records=1000),
            policy=_policy(),
        )
        check(
            "FTF-0500C",
            "negative_acceptance: a multi-page listing whose declared snapshot header never "
            "once appears is blocked (SNAPSHOT_UNSTABLE), not silently returned as a success",
            False,
        )
    except source_module.SnapshotUnstableError:
        check(
            "FTF-0500C",
            "negative_acceptance: a multi-page listing whose declared snapshot header never "
            "once appears is blocked (SNAPSHOT_UNSTABLE), not silently returned as a success",
            True,
        )


def test_disabling_the_snapshot_unstable_check_genuinely_fails_the_assertion() -> None:
    """Law 1, disclosed real fault injection for the new SNAPSHOT_UNSTABLE
    check: disables it directly in the module under test, proves the
    assertion above genuinely fails (a silent, wrongly-successful result)
    without it, then reverts the file byte-identical."""
    if _BASE_URL is None:
        return
    original = MODULE_PATH.read_text()
    original_hash = hashlib.md5(original.encode("utf-8")).hexdigest()
    disabled = original.replace(
        "if config.snapshot_version_header and snapshot_version is None and page_fetches > 1:",
        "if False:  # FTF-0500C fault injection -- disabled for this check",
        1,
    )
    if disabled == original:
        check(
            "FTF-0500C",
            "fault injection: the SNAPSHOT_UNSTABLE check line was found to disable",
            False,
        )
        return
    try:
        MODULE_PATH.write_text(disabled)
        import importlib

        importlib.reload(source_module)
        try:
            result = source_module.validate_and_preview_rest_trace_source(
                _rest_config("/multipage-no-snapshot-header", max_preview_records=1000),
                policy=_policy(),
            )
            genuinely_failed_without_the_check = True
            _ = result
        except source_module.SnapshotUnstableError:
            genuinely_failed_without_the_check = False
        check(
            "FTF-0500C",
            "fault injection: disabling the real SNAPSHOT_UNSTABLE check genuinely breaks the "
            "assertion above (proves it was testing something real, not vacuously passing)",
            genuinely_failed_without_the_check,
        )
    finally:
        MODULE_PATH.write_text(original)
        import importlib

        importlib.reload(source_module)
        restored_hash = hashlib.md5(MODULE_PATH.read_text().encode("utf-8")).hexdigest()
        check(
            "FTF-0500C",
            "fault injection: the module file was reverted byte-identical after the injection",
            restored_hash == original_hash,
        )


def test_rest_connector_refuses_a_repeating_cursor_rather_than_duplicating_records() -> None:
    """2026-10-04 L2 finding: a misbehaving endpoint that returns the
    identical next_cursor forever used to be bounded only by
    max_page_fetches, re-adding duplicate records into the preview on every
    repeated fetch in the meantime."""
    if _BASE_URL is None:
        return
    try:
        source_module.validate_and_preview_rest_trace_source(
            _rest_config("/repeating-cursor", max_preview_records=20), policy=_policy()
        )
        check(
            "FTF-0500C",
            "negative_acceptance: a real endpoint repeating the identical cursor is refused, "
            "not silently refetched into duplicate preview records",
            False,
        )
    except source_module.SourceUnavailableError:
        check(
            "FTF-0500C",
            "negative_acceptance: a real endpoint repeating the identical cursor is refused, "
            "not silently refetched into duplicate preview records",
            True,
        )


def test_disabling_the_repeated_cursor_check_genuinely_produces_duplicates() -> None:
    """Law 1, disclosed real fault injection: disables the repeated-cursor
    check directly in the module under test, proves the real endpoint above
    genuinely succeeds with duplicated records (the actual defect) without
    it, then reverts the file byte-identical."""
    if _BASE_URL is None:
        return
    original = MODULE_PATH.read_text()
    original_hash = hashlib.md5(original.encode("utf-8")).hexdigest()
    disabled = original.replace(
        "if cursor is not None and str(next_cursor) == cursor:",
        "if False:  # FTF-0500C fault injection -- disabled for this check",
        1,
    )
    if disabled == original:
        check(
            "FTF-0500C",
            "fault injection: the repeated-cursor check line was found to disable",
            False,
        )
        return
    try:
        MODULE_PATH.write_text(disabled)
        import importlib

        importlib.reload(source_module)
        try:
            result = source_module.validate_and_preview_rest_trace_source(
                _rest_config("/repeating-cursor", max_preview_records=20), policy=_policy()
            )
            genuinely_duplicated = len(result.records) == 20 and all(
                r["id"] == 1 for r in result.records
            )
        except source_module.SourceUnavailableError:
            genuinely_duplicated = False
        check(
            "FTF-0500C",
            "fault injection: disabling the real repeated-cursor check genuinely reproduces the "
            "defect (20 duplicate copies of the same record), proving the check was load-bearing",
            genuinely_duplicated,
        )
    finally:
        MODULE_PATH.write_text(original)
        import importlib

        importlib.reload(source_module)
        restored_hash = hashlib.md5(MODULE_PATH.read_text().encode("utf-8")).hexdigest()
        check(
            "FTF-0500C",
            "fault injection: the module file was reverted byte-identical after the injection",
            restored_hash == original_hash,
        )


def test_rest_connector_url_encodes_an_unsafe_cursor_value() -> None:
    """2026-10-04 L2 finding ("recommended"): an unescaped &, =, space or #
    inside a real cursor value used to corrupt the next request's own query
    string. A real round trip through the local HTTPS fixture with a
    deliberately unsafe cursor value proves the encoding survives."""
    if _BASE_URL is None:
        return
    result = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/url-unsafe-cursor", max_preview_records=10, snapshot_version_header=None),
        policy=_policy(),
    )
    check(
        "FTF-0500C",
        "acceptance: a cursor value containing &, =, space and # round-trips correctly through "
        "a real URL-encoded request, rather than corrupting the query string",
        [r["id"] for r in result.records] == [1, 2],
    )


def test_rest_connector_reports_truncated_when_the_cap_exactly_fills_a_page() -> None:
    """2026-10-04 L2 finding ("recommended"): truncated used to stay False
    whenever len(collected) landed exactly on max_preview_records at the
    natural end of a page's own records (never triggering the inner
    per-record cap-break), even though the source's own next_cursor proved
    more real data remained."""
    if _BASE_URL is None:
        return
    result = source_module.validate_and_preview_rest_trace_source(
        _rest_config("/pages", max_preview_records=10), policy=_policy()
    )
    check(
        "FTF-0500C",
        "acceptance: truncated is True when the cap exactly coincides with a real page "
        "boundary and the source reports further pages remain",
        result.truncated is True and len(result.records) == 10,
    )


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
