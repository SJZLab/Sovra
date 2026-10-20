"""FTF-R006-P1 -- versioned internal Argilla review-server REST adapter
(Section 77.1, 78.4).

The one place Factory code speaks to the self-hosted Argilla server. It is
a thin, fail-closed REST v1 client, not the `argilla` SDK and not a second
review UI (a new runtime dependency would be ask-first, and Section 78.4
allows the "pinned SDK/REST surface"; this follows the FTF-R005 MLflow REST
precedent exactly). Every request goes through
`packages.security.outbound_guard.guarded_fetch` with a caller-supplied
`ConnectionPolicy` (the shared SSRF guard, no redirects, pinned address), so
the configured Argilla base URL can never be steered to an arbitrary or
cloud-metadata destination.

Route shapes, the `X-Argilla-Api-Key` header name, the user role enum and
the error-response bodies below are read directly from the pinned
`argilla-io/argilla` v2.8.0 server source
(`argilla-server/src/argilla_server/{api/handlers/v1,constants.py,enums.py,
errors/future/base_errors.py,api/errors/v1/exception_handlers.py}`), not
guessed: `NotFoundError`/`NotUniqueError`/`UnprocessableEntityError` map to
404/409/422 with `{"detail": "<message>"}` (a plain string, not a nested
object); a request-body validation failure instead returns FastAPI's own
`{"detail": [{"loc": ..., "msg": ..., "type": ...}]}` list shape. Both
shapes are handled below; neither is ever trusted as anything but a
content-free status classification (Law 17: a destination the caller's
policy denies, a malformed response, and a real 5xx are kept apart from a
real "not found"/"already exists" so a transient outage is never retried
as if it were a permanent rejection or converted into a false success).

Error classes keep the outcomes apart so a caller can never turn one into
another: `ArgillaUnavailableError` (network, timeout or 5xx -- retryable,
reported as pending, never success), `ArgillaRejectedError` (the server
understood and refused -- never retried as-is), `ArgillaNotFoundError` and
`ArgillaConflictError` (404 / 409, the two outcomes
`argilla_identity_store.py` branches on for idempotent provisioning).

Authority: this client reads and writes identity/workspace-membership
records only. It never calls a dataset/record/response endpoint -- that is
`review_mapping.py`'s own scope (FTF-R006-P2), not P1's.
"""

from __future__ import annotations

import http.client
import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

from packages.security.outbound_guard import OutboundGuardError, guarded_stream_fetch
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError

__all__ = [
    "API_KEY_HEADER_NAME",
    "ARGILLA_ROLES",
    "MAX_RESPONSE_BYTES",
    "ArgillaClient",
    "ArgillaClientError",
    "ArgillaTransport",
    "ArgillaConflictError",
    "ArgillaNotFoundError",
    "ArgillaRejectedError",
    "ArgillaUnavailableError",
    "ArgillaUser",
    "ArgillaWorkspace",
    "parse_strict_json",
]

MAX_RESPONSE_BYTES = 1 * 1024 * 1024
MAX_PAGE_ITEMS = 10_000
_READ_CHUNK = 64 * 1024
_DIGITS = re.compile(r"[0-9]+")
_CREATABLE_ROLES = frozenset({"annotator"})
# 408 / 425 / 429 are transient by definition (request timeout, too early,
# rate limited): the outcome is retryable, not a permanent refusal.
_TRANSIENT_4XX = frozenset({408, 425, 429})
_API = "/api/v1"

# `argilla_server.constants.API_KEY_HEADER_NAME`, pinned v2.8.0.
API_KEY_HEADER_NAME = "X-Argilla-Api-Key"
# `argilla_server.enums.UserRole`, pinned v2.8.0.
ARGILLA_ROLES = frozenset({"owner", "admin", "annotator"})

# Argilla usernames/workspace names are opaque identifiers to this adapter;
# bounded and ASCII-identifier-shaped so nothing control-character-bearing
# or absurdly long ever reaches a header, a path segment or a JSON body.
_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_WORKSPACE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_UUID_TEXT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# A control character here (CR/LF above all) would otherwise reach
# http.client's own header-value validation as a raw, uncaught ValueError
# that echoes the key; refused before any header is ever built.
_UNSAFE_KEY_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _redact(text: str, api_key: str | None) -> str:
    """Never let the caller's own bearer API key reach an error message, a
    log line or an audit row, even if a server or transport error echoes
    it back verbatim."""
    if api_key:
        return text.replace(api_key, "[redacted]")
    return text


class ArgillaClientError(Exception):
    """Base for every Argilla adapter failure."""


class ArgillaUnavailableError(ArgillaClientError):
    """Network failure, timeout or 5xx: the outcome is unknown, retry later."""


class ArgillaRejectedError(ArgillaClientError):
    """The server understood the request and refused it, or returned
    something this adapter will not trust (malformed body, wrong shape,
    a destination its own policy denies)."""


class ArgillaNotFoundError(ArgillaRejectedError):
    """HTTP 404 -- `argilla_server.errors.future.NotFoundError`."""


class ArgillaConflictError(ArgillaRejectedError):
    """HTTP 409 -- `argilla_server.errors.future.NotUniqueError` (a
    username or workspace name that already exists, or a workspace
    membership that is already present)."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ArgillaRejectedError(f"duplicate key {key!r} in Argilla response")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ArgillaRejectedError(f"non-finite constant {name} in Argilla response")


def _reject_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ArgillaRejectedError("non-finite number in Argilla response")
    return value


def parse_strict_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ArgillaRejectedError("Argilla response exceeds the size bound")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except ArgillaRejectedError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ArgillaRejectedError(f"Argilla response is not strict JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ArgillaRejectedError("Argilla response is not a JSON object")
    return parsed


def _require_username(value: object) -> str:
    if not isinstance(value, str) or not _USERNAME.fullmatch(value):
        raise ArgillaRejectedError("username is not an identifier-shaped string")
    return value


def _require_workspace_name(value: object) -> str:
    if not isinstance(value, str) or not _WORKSPACE_NAME.fullmatch(value):
        raise ArgillaRejectedError("workspace name is not an identifier-shaped string")
    return value


def _require_role(value: object, allowed: frozenset[str] = ARGILLA_ROLES) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ArgillaRejectedError(f"{value!r} is not a recognized Argilla role")
    return value


def _require_uuid_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not _UUID_TEXT.fullmatch(value.lower()):
        raise ArgillaRejectedError(f"{name} is not a UUID-shaped string")
    return value


def _error_detail_text(payload: Mapping[str, Any]) -> str:
    """Extract a short, bounded, content-free-enough classification string
    from either real `{"detail": "<message>"}}` shape (future errors) or
    FastAPI's own `{"detail": [{"loc": ..., "msg": ..., "type": ...}]}}`
    validation-error shape. Never raises; an unrecognized shape just gives
    back an empty string so the caller falls back to a static message."""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail[:200]
    if isinstance(detail, list):
        parts = [
            str(item.get("msg"))
            for item in detail
            if isinstance(item, dict) and isinstance(item.get("msg"), str)
        ]
        return "; ".join(parts)[:200]
    return ""


@dataclass(frozen=True)
class ArgillaUser:
    id: str
    username: str
    role: str
    # Never in repr(): a logged user or a list of users must not show a key.
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class ArgillaWorkspace:
    id: str
    name: str


class ArgillaTransport:
    """The one guarded request path to the Argilla server, shared by this
    client and any later Argilla module (identity store, review mapping):
    every call is SSRF-checked and address-pinned by `guarded_stream_fetch`,
    the body is read in bounded chunks (never buffered past the size bound),
    one total deadline covers connect, headers and body, and every outcome
    is an `ArgillaClientError` subclass with a message that never carries
    the API key."""

    def __init__(
        self,
        base_url: str,
        policy: ConnectionPolicy,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise ValueError("base_url must be scheme://host[:port] only")
        if (
            not isinstance(timeout, int | float)
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        self._base = f"{parts.scheme}://{parts.netloc}"
        self._policy = policy
        self._api_key = api_key
        self._timeout = float(timeout)

    def call(
        self, method: str, path: str, *, query: dict[str, str] | None = None, body: Any = None
    ) -> dict[str, Any]:
        url = self._base + _API + path
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": "application/json"}
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._api_key:
            if _UNSAFE_KEY_CHARS.search(self._api_key):
                # A control character (CRLF above all) in the key would
                # otherwise reach http.client's own header validation, whose
                # ValueError echoes the raw header value -- the key itself --
                # and is not an ArgillaClientError a caller's except can
                # catch. Refused here, before any header is ever built, with
                # a static message that never carries the key.
                raise ArgillaRejectedError("API key contains control characters")
            headers[API_KEY_HEADER_NAME] = self._api_key
        deadline = time.monotonic() + self._timeout
        failure: ArgillaClientError | None = None
        status = 0
        raw = b""
        try:
            stream = guarded_stream_fetch(
                url,
                self._policy,
                method=method,
                headers=headers,
                body=payload,
                timeout=self._timeout,
            )
            with stream:
                status = stream.status
                raw = self._read_bounded(stream, deadline)
        except OutboundPolicyError as exc:
            failure = ArgillaRejectedError(self._scrub(f"destination denied by policy: {exc}"))
        except http.client.HTTPException:
            # IncompleteRead.partial / BadStatusLine / LineTooLong carry raw
            # server bytes (which can echo the key): a static message only.
            failure = ArgillaUnavailableError("Argilla sent a malformed HTTP response")
        except (OutboundGuardError, OSError) as exc:
            failure = ArgillaUnavailableError(self._scrub(f"Argilla unreachable: {exc}"))
        except ArgillaClientError as exc:
            failure = exc
        except ValueError:
            # Defense in depth for any other transport-level ValueError
            # (e.g. a header built from something other than the key): a
            # static message, never the exception's own text, so a value
            # that embeds the key can never be echoed back.
            failure = ArgillaRejectedError("Argilla request could not be sent")
        if failure is not None:
            # Raised outside the handler so the original exception (whose
            # text could carry request data) is not chained onto this one.
            raise failure
        return self._classify(status, raw)

    def _read_bounded(self, stream: Any, deadline: float) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ArgillaUnavailableError("Argilla response exceeded the call deadline")
            sock = getattr(stream._connection, "sock", None)
            if sock is not None:
                # One total deadline, not one per recv: a server dripping a
                # byte at a time can never hold the call past `timeout`.
                sock.settimeout(remaining)
            chunk = stream.read_chunk(_READ_CHUNK)
            if not chunk:
                body = b"".join(chunks)
                declared = {k.lower(): v for k, v in stream.headers.items()}.get("content-length")
                if declared is not None and (
                    not _DIGITS.fullmatch(declared) or int(declared) != len(body)
                ):
                    # `read1` reports a short body as plain EOF: a response
                    # cut off before its declared length is an unknown
                    # outcome, never a (possibly valid-looking) success.
                    raise ArgillaUnavailableError("Argilla response was truncated")
                return body
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ArgillaRejectedError("Argilla response exceeds the size bound")
            chunks.append(chunk)

    def _classify(self, status: int, raw: bytes) -> dict[str, Any]:
        if status >= 500 or status < 200:
            raise ArgillaUnavailableError(f"Argilla returned HTTP {status}")
        if status in _TRANSIENT_4XX:
            raise ArgillaUnavailableError(f"Argilla returned HTTP {status}")
        if status not in (200, 201):
            detail = ""
            try:
                detail = self._scrub(_error_detail_text(parse_strict_json(raw)))
            except ArgillaRejectedError:
                pass
            message = f"Argilla returned HTTP {status}" + (f": {detail}" if detail else "")
            if status == 404:
                raise ArgillaNotFoundError(message)
            if status == 409:
                raise ArgillaConflictError(message)
            raise ArgillaRejectedError(message)
        failure: ArgillaClientError
        try:
            return parse_strict_json(raw)
        except ArgillaRejectedError as exc:
            failure = ArgillaRejectedError(self._scrub(str(exc)))
        raise failure

    def _scrub(self, text: str) -> str:
        return _redact(text, self._api_key)


class ArgillaClient:
    """REST v1 adapter for one internal Argilla server, authenticated with
    one caller-supplied API key (the provisioning/"owner" key for identity
    and workspace management; never a per-annotator key for this client).
    `transport` is the shared guarded request path for later Argilla
    modules."""

    def __init__(
        self,
        base_url: str,
        policy: ConnectionPolicy,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.transport = ArgillaTransport(base_url, policy, api_key=api_key, timeout=timeout)

    def _call(
        self, method: str, path: str, *, query: dict[str, str] | None = None, body: Any = None
    ) -> dict[str, Any]:
        return self.transport.call(method, path, query=query, body=body)

    # -- health --------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Raw `GET /status` payload (`version`, `search_engine`,
        `memory`). `ArgillaUnavailableError` on any network failure or
        5xx -- the caller (`compute_health`) reports that as unhealthy,
        never as a fabricated healthy result (Law 17)."""
        return self._call("GET", "/status")

    # -- workspaces ----------------------------------------------------

    def list_workspaces_me(self) -> list[ArgillaWorkspace]:
        """`GET /me/workspaces`. With the provisioning ("owner") key this
        lists every workspace on the server, per the server's own
        `list_workspaces_me` handler (`current_user.is_owner`)."""
        found = self._call("GET", "/me/workspaces")
        return _workspace_list(found)

    def create_workspace(self, name: str) -> ArgillaWorkspace:
        _require_workspace_name(name)
        created = self._call("POST", "/workspaces", body={"name": name})
        return _workspace_of(created)

    def ensure_workspace(self, name: str) -> ArgillaWorkspace:
        """Idempotent: returns the existing workspace by name, or creates
        it. A create-race (another caller wins) is resolved by one re-list,
        never by retrying the create."""
        _require_workspace_name(name)
        for workspace in self.list_workspaces_me():
            if workspace.name == name:
                return workspace
        try:
            return self.create_workspace(name)
        except ArgillaConflictError:
            for workspace in self.list_workspaces_me():
                if workspace.name == name:
                    return workspace
            raise ArgillaRejectedError(
                "workspace create conflicted but no matching workspace was found on re-list"
            ) from None

    def list_workspace_users(self, workspace_id: str) -> list[ArgillaUser]:
        _require_uuid_text(workspace_id, name="workspace_id")
        found = self._call("GET", f"/workspaces/{workspace_id}/users")
        return _user_list(found)

    def add_workspace_user(self, workspace_id: str, user_id: str) -> None:
        """`POST /workspaces/{id}/users`. `ArgillaConflictError` when the
        user is already a member (idempotent at the caller's choice, never
        silently swallowed here)."""
        _require_uuid_text(workspace_id, name="workspace_id")
        _require_uuid_text(user_id, name="user_id")
        self._call("POST", f"/workspaces/{workspace_id}/users", body={"user_id": user_id})

    def remove_workspace_user(self, workspace_id: str, user_id: str) -> None:
        _require_uuid_text(workspace_id, name="workspace_id")
        _require_uuid_text(user_id, name="user_id")
        self._call("DELETE", f"/workspaces/{workspace_id}/users/{user_id}")

    # -- users -----------------------------------------------------------

    def create_user(self, *, username: str, role: str, password: str) -> ArgillaUser:
        """`POST /users`. The response carries the new user's `api_key`
        in plaintext -- the caller (`argilla_identity_store.py`) must send
        it straight to OpenBao and never persist or log it itself."""
        _require_username(username)
        if not isinstance(role, str) or role not in _CREATABLE_ROLES:
            # Review identities are annotators only; owner/admin are never
            # created by the Factory (they would out-privilege the sandbox).
            raise ArgillaRejectedError("only the annotator role may be created")
        if not isinstance(password, str) or not (8 <= len(password) <= 100):
            raise ArgillaRejectedError("password must be 8..100 characters")
        created = self._call(
            "POST",
            "/users",
            body={"first_name": username, "username": username, "role": role, "password": password},
        )
        return _user_of(created, roles=_CREATABLE_ROLES)

    def get_user(self, user_id: str) -> ArgillaUser:
        _require_uuid_text(user_id, name="user_id")
        found = self._call("GET", f"/users/{user_id}")
        return _user_of(found)

    def delete_user(self, user_id: str) -> None:
        _require_uuid_text(user_id, name="user_id")
        self._call("DELETE", f"/users/{user_id}")


def _workspace_of(payload: object) -> ArgillaWorkspace:
    if not isinstance(payload, dict):
        raise ArgillaRejectedError("Argilla returned a malformed workspace")
    workspace_id = payload.get("id")
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise ArgillaRejectedError("Argilla returned a malformed workspace")
    _require_uuid_text(workspace_id, name="workspace id")
    assert isinstance(workspace_id, str)
    return ArgillaWorkspace(id=workspace_id, name=name)


def _workspace_list(payload: Mapping[str, Any]) -> list[ArgillaWorkspace]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ArgillaRejectedError("Argilla returned a malformed workspace list")
    if len(items) > MAX_PAGE_ITEMS:
        raise ArgillaRejectedError("Argilla workspace list exceeds the page bound")
    return [_workspace_of(item) for item in items]


def _user_of(payload: object, roles: frozenset[str] = ARGILLA_ROLES) -> ArgillaUser:
    if not isinstance(payload, dict):
        raise ArgillaRejectedError("Argilla returned a malformed user")
    username = payload.get("username")
    api_key = payload.get("api_key")
    if not isinstance(username, str) or not username:
        raise ArgillaRejectedError("Argilla returned a malformed user")
    if not isinstance(api_key, str) or not api_key:
        raise ArgillaRejectedError("Argilla returned a user without an api_key")
    user_id = _require_uuid_text(payload.get("id"), name="user id")
    role = _require_role(payload.get("role"), roles)
    return ArgillaUser(id=user_id, username=username, role=role, api_key=api_key)


def _user_list(payload: Mapping[str, Any]) -> list[ArgillaUser]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ArgillaRejectedError("Argilla returned a malformed user list")
    if len(items) > MAX_PAGE_ITEMS:
        raise ArgillaRejectedError("Argilla user list exceeds the page bound")
    return [_user_of(item) for item in items]
