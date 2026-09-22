#!/usr/bin/env python3
"""FTF-0202 -- Secret-manager integration (no-DB, no-network unit/fault tests).

Runnable without a live OpenBao server or Postgres (the real end-to-end
store/resolve/rotate/expire/merge evidence against a real, locally-built
OpenBao server is `tests/integration/test_ftf_0202_secret_manager.py`, run
in CI's dedicated `secret-manager-integration` job -- this repo's
interactive dev sandbox has no prebuilt-binary/container-registry egress
for OpenBao either, the same documented class of gap FTF-0004/FTF-0102
already established for Postgres/Docker; that file's own docstring has the
full detail). Every real HTTP call this module ever makes goes through the
one `_send_once` choke point, so every test below fakes `urllib.request.
urlopen` directly rather than reimplementing an HTTP client -- proving the
module's own real classification/retry/merge logic, not a parallel
reimplementation of it.

This suite proves, without any network or database:

1. `parse_secret_ref`/`build_secret_ref`/`describe_secret_ref`: the exact
   `openbao://<mount>/<path>/<field>` shape is required (never a
   `vault://`/`secret://` ref from FTF-0201's own wider convention, never a
   raw value), and `describe_secret_ref` never echoes a resolved value.
2. `OpenBaoConfig.from_env`: missing address/token and a disallowed URL
   scheme (http when `require_tls=True`) are all genuinely rejected before
   any request is ever attempted.
3. `resolve_secret_ref`'s full negative_acceptance in isolation: an unknown
   path (404), an access-denied token (403), an expired secret (real
   `custom_metadata.expires_at` in the past) and a missing field each raise
   their own named exception and never return a value.
4. `_request`'s own retry policy (Section 3.3): retries only 429/5xx, up to
   three attempts total, honors a real `Retry-After` header, and never
   retries a 403/404/2xx.
5. `store_secret_ref`/`rotate_secret_ref` merge onto (never silently
   overwrite) a co-located field at the same path, and `rotate_secret_ref`
   refuses a ref with no existing secret before ever attempting a write.
6. Source-level proof this module can structurally never write to this
   repository's own Postgres or serve a browser request: no
   `sqlalchemy`/`fastapi`/`hvac`/`requests` import anywhere in it.

Run directly: python3 tests/tasks/test_ftf_0202.py
"""

from __future__ import annotations

import email.message
import io
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client import secret_manager_integration as smi  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True
    except BaseException:
        return False


# -- fake HTTP transport ------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeTransport:
    """Replaces `urllib.request.urlopen` with a scripted sequence of
    (status, body, headers) responses -- one per real call this module
    makes, in order. Records every `urllib.request.Request` it was given
    so a test can assert on method/URL/body, not just the return value."""

    def __init__(self, responses: list[tuple[int, bytes, dict[str, str]]]) -> None:
        self._responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None):
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("fake transport ran out of scripted responses")
        status, body, headers = self._responses.pop(0)
        if status >= 400:
            header_message = email.message.Message()
            for key, val in headers.items():
                header_message[key] = val
            raise urllib.error.HTTPError(
                request.full_url, status, "error", header_message, io.BytesIO(body)
            )
        return _FakeResponse(status, body, headers)


def _ok(body: bytes, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    return (200, body, headers or {})


def _no_content() -> tuple[int, bytes, dict[str, str]]:
    return (204, b"", {})


def _status(code: int, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    return (code, b'{"errors":["denied or missing"]}', headers or {})


_CONFIG = smi.OpenBaoConfig(address="https://openbao.internal:8200", token="test-token")


def _kv_body(value: str, *, field: str = "api_key", custom_metadata: dict | None = None) -> bytes:
    return json.dumps(
        {
            "data": {
                "data": {field: value},
                "metadata": {
                    "created_time": "2026-09-22T00:00:00Z",
                    "custom_metadata": custom_metadata,
                    "deletion_time": "",
                    "destroyed": False,
                    "version": 1,
                },
            }
        }
    ).encode()


# -- 1. parse/build/describe --------------------------------------------------


def test_parse_secret_ref_accepts_well_formed_ref() -> None:
    parsed = smi.parse_secret_ref("openbao://secret/litellm/conn1/api_key")
    check(
        "FTF-0202",
        "parse_secret_ref splits mount/path/field correctly",
        parsed == smi.ParsedSecretRef(mount="secret", path="litellm/conn1", field="api_key"),
    )


def test_parse_secret_ref_rejects_non_openbao_scheme() -> None:
    check(
        "FTF-0202",
        "parse_secret_ref rejects a vault:// ref -- this backend cannot resolve it",
        _raises(
            smi.SecretRefFormatError, smi.parse_secret_ref, "vault://secret/litellm/conn1/api_key"
        ),
    )


def test_parse_secret_ref_rejects_raw_value() -> None:
    check(
        "FTF-0202",
        "parse_secret_ref rejects a raw secret value (no scheme at all)",
        _raises(smi.SecretRefFormatError, smi.parse_secret_ref, "sk-live-abcdef123456"),
    )


def test_parse_secret_ref_rejects_missing_field_segment() -> None:
    check(
        "FTF-0202",
        "parse_secret_ref rejects a ref with no field segment",
        _raises(smi.SecretRefFormatError, smi.parse_secret_ref, "openbao://secret/litellm"),
    )


def test_parse_secret_ref_rejects_non_string() -> None:
    check(
        "FTF-0202",
        "parse_secret_ref rejects a non-string ref",
        _raises(smi.SecretRefFormatError, smi.parse_secret_ref, None),
    )


def test_build_secret_ref_roundtrips_through_parse() -> None:
    ref = smi.build_secret_ref(mount="secret", path="litellm/conn1", field="api_key")
    check(
        "FTF-0202",
        "build_secret_ref produces a ref parse_secret_ref accepts, correct fields",
        ref == "openbao://secret/litellm/conn1/api_key"
        and smi.parse_secret_ref(ref).field == "api_key",
    )


def test_describe_secret_ref_never_echoes_a_value() -> None:
    ref = smi.build_secret_ref(mount="secret", path="litellm/conn1", field="api_key")
    described = smi.describe_secret_ref(ref)
    check(
        "FTF-0202",
        "describe_secret_ref returns scheme/mount/path/field only, identical to the ref itself "
        "(there is no value in scope to leak)",
        described == ref,
    )


def test_describe_secret_ref_handles_malformed_input_safely() -> None:
    check(
        "FTF-0202",
        "describe_secret_ref never raises on a malformed ref -- safe to call from a log line "
        "without its own try/except",
        smi.describe_secret_ref("not-a-ref-at-all") == "<malformed-secret-ref>",
    )


# -- 2. OpenBaoConfig.from_env -------------------------------------------------


def test_config_from_env_requires_address() -> None:
    check(
        "FTF-0202",
        "OpenBaoConfig.from_env rejects a missing SOVRA_OPENBAO_ADDR",
        _raises(
            smi.SecretManagerConfigurationError,
            smi.OpenBaoConfig.from_env,
            {"SOVRA_OPENBAO_TOKEN": "t"},
        ),
    )


def test_config_from_env_requires_token() -> None:
    check(
        "FTF-0202",
        "OpenBaoConfig.from_env rejects a missing SOVRA_OPENBAO_TOKEN",
        _raises(
            smi.SecretManagerConfigurationError,
            smi.OpenBaoConfig.from_env,
            {"SOVRA_OPENBAO_ADDR": "https://openbao.internal:8200"},
        ),
    )


def test_config_from_env_rejects_http_when_tls_required() -> None:
    check(
        "FTF-0202",
        "OpenBaoConfig.from_env rejects http:// when require_tls=True (production default)",
        _raises(
            smi.SecretManagerConfigurationError,
            smi.OpenBaoConfig.from_env,
            {"SOVRA_OPENBAO_ADDR": "http://openbao.internal:8200", "SOVRA_OPENBAO_TOKEN": "t"},
        ),
    )


def test_config_from_env_accepts_http_when_tls_not_required() -> None:
    config = smi.OpenBaoConfig.from_env(
        {"SOVRA_OPENBAO_ADDR": "http://127.0.0.1:8210", "SOVRA_OPENBAO_TOKEN": "t"},
        require_tls=False,
    )
    check(
        "FTF-0202",
        "OpenBaoConfig.from_env accepts http:// for a real local dev instance when "
        "require_tls=False is explicitly chosen by the caller",
        config.address == "http://127.0.0.1:8210",
    )


def test_config_repr_never_echoes_the_token() -> None:
    config = smi.OpenBaoConfig(address="https://openbao.internal:8200", token="super-secret-token")
    check(
        "FTF-0202",
        "OpenBaoConfig.__repr__ never includes the real token value",
        "super-secret-token" not in repr(config) and "redacted" in repr(config),
    )


# -- 3. resolve_secret_ref negative_acceptance --------------------------------


def test_resolve_secret_ref_success() -> None:
    transport = _FakeTransport([_ok(_kv_body("sk-real-value"))])
    with patch.object(urllib.request, "urlopen", transport):
        value = smi.resolve_secret_ref("openbao://secret/litellm/conn1/api_key", config=_CONFIG)
    check(
        "FTF-0202",
        "resolve_secret_ref returns the real field value on success",
        value == "sk-real-value",
    )


def test_resolve_secret_ref_unknown_path_raises_not_found() -> None:
    transport = _FakeTransport([_status(404)])
    with patch.object(urllib.request, "urlopen", transport):
        raised = _raises(
            smi.SecretNotFoundError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/gone/api_key",
            config=_CONFIG,
        )
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretNotFoundError for an unknown/deleted path (404) -- "
        "this task's own negative_acceptance ('Unknown... secrets block call')",
        raised,
    )


def test_resolve_secret_ref_denied_raises_access_denied() -> None:
    transport = _FakeTransport([_status(403)])
    with patch.object(urllib.request, "urlopen", transport):
        raised = _raises(
            smi.SecretAccessDeniedError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/conn1/api_key",
            config=_CONFIG,
        )
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretAccessDeniedError for a denied token (403) -- "
        "this task's own negative_acceptance ('ref-denied secrets block call')",
        raised,
    )


def test_resolve_secret_ref_expired_raises_and_never_returns_value() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    transport = _FakeTransport(
        [_ok(_kv_body("sk-expired-value", custom_metadata={"expires_at": past}))]
    )
    with patch.object(urllib.request, "urlopen", transport):
        try:
            smi.resolve_secret_ref("openbao://secret/litellm/conn1/api_key", config=_CONFIG)
            got_value: str | None = "NO EXCEPTION RAISED"
        except smi.SecretExpiredError:
            got_value = None
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretExpiredError (never returns the value) for a secret "
        "past its own recorded custom_metadata.expires_at -- this task's own negative_acceptance "
        "('expired secrets block call')",
        got_value is None,
    )


def test_resolve_secret_ref_not_yet_expired_still_succeeds() -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    transport = _FakeTransport(
        [_ok(_kv_body("sk-still-valid", custom_metadata={"expires_at": future}))]
    )
    with patch.object(urllib.request, "urlopen", transport):
        value = smi.resolve_secret_ref("openbao://secret/litellm/conn1/api_key", config=_CONFIG)
    check(
        "FTF-0202",
        "resolve_secret_ref still returns the value when custom_metadata.expires_at is in the "
        "future -- the expiry check is a real boundary, not a check that always fires",
        value == "sk-still-valid",
    )


def test_resolve_secret_ref_missing_field_raises_not_found() -> None:
    transport = _FakeTransport([_ok(_kv_body("sk-value", field="org_id"))])
    with patch.object(urllib.request, "urlopen", transport):
        raised = _raises(
            smi.SecretNotFoundError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/conn1/api_key",
            config=_CONFIG,
        )
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretNotFoundError when the path exists but the ref's own "
        "field is absent from it",
        raised,
    )


def test_resolve_secret_ref_malformed_response_raises_backend_error() -> None:
    transport = _FakeTransport([_ok(b'{"unexpected": true}')])
    with patch.object(urllib.request, "urlopen", transport):
        raised = _raises(
            smi.SecretBackendError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/conn1/api_key",
            config=_CONFIG,
        )
    check(
        "FTF-0202",
        "resolve_secret_ref raises SecretBackendError (fails closed) for a response missing "
        "its own expected 'data' shape, never a fabricated success",
        raised,
    )


# -- 4. retry policy (Section 3.3) --------------------------------------------


def test_retry_recovers_from_a_transient_503_then_succeeds() -> None:
    transport = _FakeTransport([_status(503), _ok(_kv_body("sk-after-retry"))])
    with (
        patch.object(urllib.request, "urlopen", transport),
        patch.object(time, "sleep") as sleep_mock,
    ):
        value = smi.resolve_secret_ref("openbao://secret/litellm/conn1/api_key", config=_CONFIG)
    check(
        "FTF-0202",
        "a transient 503 is retried and the second attempt's real value is returned",
        value == "sk-after-retry" and sleep_mock.call_count == 1,
    )


def test_retry_exhausts_after_three_attempts_and_fails_closed() -> None:
    transport = _FakeTransport([_status(503), _status(503), _status(503)])
    with patch.object(urllib.request, "urlopen", transport), patch.object(time, "sleep"):
        raised = _raises(
            smi.SecretBackendError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/conn1/api_key",
            config=_CONFIG,
        )
    check(
        "FTF-0202",
        "three consecutive 503s exhaust the retry budget (Section 3.3's own three-attempt "
        "ceiling) and raise SecretBackendError -- never silently returns success",
        raised and len(transport.requests) == 3,
    )


def test_403_is_never_retried() -> None:
    transport = _FakeTransport([_status(403)])
    with (
        patch.object(urllib.request, "urlopen", transport),
        patch.object(time, "sleep") as sleep_mock,
    ):
        _raises(
            smi.SecretAccessDeniedError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/conn1/api_key",
            config=_CONFIG,
        )
    check(
        "FTF-0202",
        "a 403 (access denied) is never retried -- exactly one real request is made, no sleep",
        len(transport.requests) == 1 and sleep_mock.call_count == 0,
    )


def test_404_is_never_retried() -> None:
    transport = _FakeTransport([_status(404)])
    with (
        patch.object(urllib.request, "urlopen", transport),
        patch.object(time, "sleep") as sleep_mock,
    ):
        _raises(
            smi.SecretNotFoundError,
            smi.resolve_secret_ref,
            "openbao://secret/litellm/conn1/api_key",
            config=_CONFIG,
        )
    check(
        "FTF-0202",
        "a 404 (unknown path) is never retried -- exactly one real request is made, no sleep",
        len(transport.requests) == 1 and sleep_mock.call_count == 0,
    )


def test_retry_honors_a_real_retry_after_header() -> None:
    transport = _FakeTransport([_status(429, headers={"Retry-After": "7"}), _ok(_kv_body("sk-ok"))])
    with (
        patch.object(urllib.request, "urlopen", transport),
        patch.object(time, "sleep") as sleep_mock,
    ):
        smi.resolve_secret_ref("openbao://secret/litellm/conn1/api_key", config=_CONFIG)
    check(
        "FTF-0202",
        "a 429 with a real Retry-After header sleeps for that exact duration, not the default",
        sleep_mock.call_args[0][0] == 7.0,
    )


# -- 5. store/rotate merge and existence rules --------------------------------


def test_store_secret_ref_merges_onto_an_existing_co_located_field() -> None:
    transport = _FakeTransport(
        [_ok(_kv_body("existing-org", field="org_id")), _ok(_kv_body("new-key"))]
    )
    with patch.object(urllib.request, "urlopen", transport):
        smi.store_secret_ref(
            config=_CONFIG, mount="secret", path="litellm/conn1", field="api_key", value="new-key"
        )
    sent_data = transport.requests[1].data
    assert isinstance(sent_data, bytes)
    written_body = json.loads(sent_data)
    check(
        "FTF-0202",
        "store_secret_ref merges the new field onto the existing co-located field rather than "
        "replacing the whole path's data (a real KV v2 write replaces the entire object)",
        written_body["data"] == {"org_id": "existing-org", "api_key": "new-key"},
    )


def test_store_secret_ref_rejects_empty_value_before_any_request() -> None:
    check(
        "FTF-0202",
        "store_secret_ref rejects an empty value before making any real network call",
        _raises(
            smi.SecretValueError,
            smi.store_secret_ref,
            config=_CONFIG,
            mount="secret",
            path="litellm/conn1",
            field="api_key",
            value="",
        ),
    )


def test_rotate_secret_ref_refuses_when_nothing_exists_yet() -> None:
    transport = _FakeTransport([_status(404)])
    with patch.object(urllib.request, "urlopen", transport):
        raised = _raises(
            smi.SecretNotFoundError,
            smi.rotate_secret_ref,
            "openbao://secret/litellm/never/api_key",
            config=_CONFIG,
            new_value="v2",
        )
    check(
        "FTF-0202",
        "rotate_secret_ref refuses a ref with no existing secret (SecretNotFoundError) and "
        "makes no write/delete call at all -- rotation presumes something to rotate",
        raised and len(transport.requests) == 1,
    )


def test_rotate_secret_ref_deletes_exactly_the_superseded_version() -> None:
    transport = _FakeTransport(
        [
            _ok(_kv_body("v1-value")),  # _read_current_version
            _ok(_kv_body("v1-value")),  # _read_current_fields
            _ok(_kv_body("v2-value")),  # the write itself
            _no_content(),  # the delete-old-version call
        ]
    )
    with patch.object(urllib.request, "urlopen", transport):
        smi.rotate_secret_ref(
            "openbao://secret/litellm/conn1/api_key", config=_CONFIG, new_value="v2-value"
        )
    delete_request = transport.requests[-1]
    sent_delete_data = delete_request.data
    assert isinstance(sent_delete_data, bytes)
    delete_body = json.loads(sent_delete_data)
    check(
        "FTF-0202",
        "rotate_secret_ref's own final call deletes exactly the version the read step observed "
        "(version 1, from this fixture's own frozen fake body), never a guessed/hard-coded number",
        delete_request.full_url.endswith("/delete/litellm/conn1")
        and delete_body == {"versions": [1]},
    )


def test_rotate_secret_ref_rejects_empty_new_value() -> None:
    transport = _FakeTransport([_ok(_kv_body("v1"))])
    with patch.object(urllib.request, "urlopen", transport):
        raised = _raises(
            smi.SecretValueError,
            smi.rotate_secret_ref,
            "openbao://secret/litellm/conn1/api_key",
            config=_CONFIG,
            new_value="",
        )
    check("FTF-0202", "rotate_secret_ref rejects an empty new_value", raised)


# -- 6. source-level structural proof -----------------------------------------


def test_module_never_imports_a_db_or_web_framework() -> None:
    source = (ROOT / "packages" / "litellm_client" / "secret_manager_integration.py").read_text()
    lines = source.splitlines()
    import_lines = [line for line in lines if line.startswith(("import ", "from "))]
    forbidden_modules = ("sqlalchemy", "fastapi", "hvac", "requests")
    offending = [
        line
        for line in import_lines
        if any(
            line == f"import {mod}" or line.startswith(f"from {mod}") for mod in forbidden_modules
        )
    ]
    check(
        "FTF-0202",
        "secret_manager_integration.py's own import statements (not its prose, which explains "
        "why hvac/requests were deliberately not used) contain none of "
        "sqlalchemy/fastapi/hvac/requests -- structurally incapable of writing this repository's "
        "own Postgres or serving a browser route (this task's own negative_acceptance: 'never "
        "returned to browser or plaintext DB'), and never adds a new third-party runtime "
        "dependency",
        not offending,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0202 Secret-manager integration tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0202 Secret-manager integration tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
