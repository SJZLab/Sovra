"""OpenBao secret-manager integration (FTF-0202, Section 2-3/38/65/77/81).

Resolves the `openbao://<mount>/<path>/<field>` secret references that
`apps/api/app/litellm/connection_schema.py` (FTF-0201) and
`apps/api/app/config/configuration_system.py` (FTF-0005) already store and
validate the *shape* of, to real secret bytes -- against the actual
backend the owner selected for this repository, OpenBao (self-hosted,
Vault's open-source fork; see QUEUE.md "Owner actions -- current", FTF-0202
resolution, 2026-09-22, anchored to a live conversation). Before this task,
"No secret-manager integration exists anywhere in this repository yet"
(`connection_schema.py`'s own disclosed gap) -- this module is that
integration's first real implementation.

Path refined from the task packet's own literal suggestion
(`packages/litellm-client/secret_manager_integration.py`) to
`packages/litellm_client/secret_manager_integration.py`: a hyphenated
directory name is not a valid Python package/module segment (it cannot be
imported as `packages.litellm-client...`), so this keeps the same
`packages/<name>/<module>.py` shape `packages/storage/tenant_policy.py`
(FTF-H003) already established for a standalone, framework-free library
under `packages/`, imported the same way (`sys.path` rooted at the repo
root, then `packages.litellm_client.secret_manager_integration`).

Stdlib-only, no third-party runtime dependency (`urllib.request`) --
matching `configuration_system.py`'s own explicit precedent for exactly
this class of module, and deliberately avoiding a new runtime dependency
(e.g. `hvac`) CLAUDE.md's own "Ask owner first" list would otherwise
require asking about; OpenBao's HTTP KV v2 API is simple enough that a
hand-rolled client is the smaller, already-precedented choice.

Reference-ref shape is deliberately narrower than `vault://`/`secret://`'s
already-certified generic shape (FTF-0201/FTF-0005's own frozen
`_SECRET_REF_PATTERN`, `^[a-z]+://[A-Za-z0-9_\\-./]+$` -- no `#`/`?`/`@`
characters permitted, so this module never needs to touch, relock or
requalify either already-CERTIFIED frozen validator): `openbao://<mount>/
<path>/<field>` encodes the KV v2 mount, secret path and field name
entirely with `/`-separated path segments, resolvable by this same regex.

Every real backend interaction was verified directly against a real,
locally-built OpenBao server (`go build` from `github.com/openbao/openbao/v2`
commit `e98dc184344fe84ee4e69965ac3f4217d5b6a45d` -- no prebuilt release
binary or container image was reachable from this sandbox, the same
documented class of gap FTF-0004/FTF-0102 already established for
Postgres/Docker; CI builds the same pinned commit on the hosted runner's
own real network) before any test was written, including one genuine
surprise a guess would have missed: reading a soft-deleted *or* destroyed
KV v2 version answers real HTTP 404 (with a JSON body still attached, an
unusual but real and reproduced shape) -- not HTTP 200 with `data.data:
null` as a first, uncontrolled `curl | json.tool` pass (with no `-w
'%{http_code}'`) misleadingly suggested before this module's own explicit
`-w` re-check caught it. Both a genuinely unknown path and a rotated-away
version therefore already collapse into the same `SecretNotFoundError`
through this module's own ordinary 404 handling -- this task's own
negative_acceptance ("Unknown... secrets... never returned"), a
distinction a caller who only checked for a raised exception (not a raw
status code) would get right by construction, not by coincidence.

Security (Section 65.4): the resolved secret value is returned to the
caller and nowhere else -- never logged, never included in an exception
message, never written to this repository's own Postgres (this task's own
`database_changes`: "No independent schema change"). `describe_secret_ref`
is the one content-free representation safe to log (scheme/mount/path/
field only, matching `configuration_system.py`'s own `_redact`/`_redact_url`
precedent for exactly this shape of rule).

Expiry (`SecretExpiredError`) is a Factory-level honesty layer this module
adds on top of the backend: OpenBao's own KV v2 secrets do not expire on
their own, so `store_secret_ref`/`rotate_secret_ref` accept an optional
`expires_at` and record it as real backend `custom_metadata`, and
`resolve_secret_ref` refuses (never returns the value for) a secret whose
own recorded `expires_at` has passed -- this task's own negative_acceptance
("expired ... secrets block call") would otherwise have no other
enforcement point, since the backend alone would still serve it.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

__all__ = [
    "OpenBaoConfig",
    "ParsedSecretRef",
    "SecretAccessDeniedError",
    "SecretBackendError",
    "SecretExpiredError",
    "SecretManagerConfigurationError",
    "SecretManagerError",
    "SecretNotFoundError",
    "SecretRefFormatError",
    "SecretValueError",
    "build_secret_ref",
    "describe_secret_ref",
    "parse_secret_ref",
    "resolve_secret_ref",
    "rotate_secret_ref",
    "store_secret_ref",
]

SECRET_REF_SCHEME = "openbao"

# Deliberately a strict subset of FTF-0201/FTF-0005's own already-certified,
# frozen `^[a-z]+://[A-Za-z0-9_\-./]+$` shape -- every ref this module
# builds or accepts is also a valid `vault://`-style reference by that
# older, more general rule, never a competing format.
_REF_PATTERN = re.compile(
    r"^openbao://(?P<mount>[A-Za-z0-9_-]+)/(?P<path>[A-Za-z0-9_\-./]+)/(?P<field>[A-Za-z0-9_-]+)$"
)

_PRODUCTION_ADDR_SCHEMES = {"https"}
_NON_PRODUCTION_ADDR_SCHEMES = _PRODUCTION_ADDR_SCHEMES | {"http"}

_DEFAULT_TIMEOUT_SECONDS = 5.0
_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_SECONDS = 0.1


class SecretManagerError(Exception):
    """Base class. Every message here is content-free by construction --
    never the token, the resolved value, or the raw backend response body
    (Section observability: 'content-free errors, no secrets... in task
    reports')."""


class SecretManagerConfigurationError(SecretManagerError, ValueError):
    """`OpenBaoConfig` could not be constructed from the given/environment
    values (missing address/token, or a disallowed URL scheme)."""


class SecretRefFormatError(SecretManagerError, ValueError):
    """`secret_ref` is not a well-formed `openbao://<mount>/<path>/<field>`
    reference -- structurally incapable of being a raw secret value,
    matching FTF-0201/FTF-0005's own already-certified `validate_secret_ref`
    rejection class."""


class SecretValueError(SecretManagerError, ValueError):
    """A value passed to `store_secret_ref`/`rotate_secret_ref` is not a
    real, non-empty string."""


class SecretNotFoundError(SecretManagerError, LookupError):
    """No live data at this ref: an unknown path and a soft-deleted/
    destroyed KV v2 version both answer real backend HTTP 404 and are
    handled identically -- this task's own negative_acceptance ('Unknown...
    secrets block call and are never returned'), and a caller cannot
    distinguish 'never existed' from 'existed once, now rotated away' from
    this exception alone."""


class SecretAccessDeniedError(SecretManagerError, PermissionError):
    """The configured token was refused by the backend's own policy for
    this path (HTTP 403) -- this task's own negative_acceptance
    ('ref-denied secrets block call')."""


class SecretExpiredError(SecretManagerError, LookupError):
    """The secret's own recorded `custom_metadata.expires_at` has passed --
    this task's own negative_acceptance ('expired secrets block call'),
    enforced by this module since the backend itself does not expire a
    KV v2 static secret on its own."""


class SecretBackendError(SecretManagerError, RuntimeError):
    """Any other real backend failure: malformed response shape, an
    unexpected status code, a transient failure that exhausted its retry
    budget, or a network/timeout error. Fails closed -- never silently
    treated as success or as an empty secret."""


@dataclass(frozen=True)
class ParsedSecretRef:
    mount: str
    path: str
    field: str


def parse_secret_ref(ref: object) -> ParsedSecretRef:
    """The only place an `openbao://` ref's shape is trusted. Raises
    `SecretRefFormatError` for anything else, including a well-formed
    `vault://`/`secret://` ref from FTF-0201's own wider, older
    convention -- this backend cannot resolve those (no secret-manager
    integration for them exists), so treating one as this shape would be
    silently wrong, not merely permissive."""
    if not isinstance(ref, str) or not ref:
        raise SecretRefFormatError("secret_ref must be a non-empty string")
    match = _REF_PATTERN.match(ref)
    if not match:
        raise SecretRefFormatError(
            "secret_ref must be 'openbao://<mount>/<path>/<field>' -- never a raw "
            f"secret value or a different backend's scheme (got {ref!r})"
        )
    return ParsedSecretRef(mount=match["mount"], path=match["path"], field=match["field"])


def build_secret_ref(*, mount: str, path: str, field: str) -> str:
    """The only place an `openbao://` ref is assembled -- re-validates the
    result through `parse_secret_ref` before returning it, so a caller
    passing a field/path containing e.g. a `/` cannot silently produce a
    ref this module would later fail to parse."""
    ref = f"{SECRET_REF_SCHEME}://{mount}/{path}/{field}"
    parse_secret_ref(ref)
    return ref


def describe_secret_ref(ref: str) -> str:
    """Scheme/mount/path/field only -- safe to place in a log line or
    observability event; never the resolved value (Section observability;
    matches `configuration_system.py`'s own `_redact`/`_redact_url`
    precedent for exactly this class of rule)."""
    try:
        parsed = parse_secret_ref(ref)
    except SecretRefFormatError:
        return "<malformed-secret-ref>"
    return f"{SECRET_REF_SCHEME}://{parsed.mount}/{parsed.path}/{parsed.field}"


@dataclass(frozen=True)
class OpenBaoConfig:
    """Bootstrap credential to reach the secret backend itself -- Section
    65.4's 'short-lived workload identity is preferred' applies to the
    application secrets this module resolves, not to this credential's own
    chicken-and-egg problem (it cannot itself be an `openbao://` ref).
    Supplied directly by the caller's own environment/workload identity,
    never persisted by this module."""

    address: str
    token: str

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, require_tls: bool = True) -> OpenBaoConfig:
        address = env.get("SOVRA_OPENBAO_ADDR")
        token = env.get("SOVRA_OPENBAO_TOKEN")
        if not address:
            raise SecretManagerConfigurationError("SOVRA_OPENBAO_ADDR is required and was not set")
        if not token:
            raise SecretManagerConfigurationError("SOVRA_OPENBAO_TOKEN is required and was not set")
        allowed = _PRODUCTION_ADDR_SCHEMES if require_tls else _NON_PRODUCTION_ADDR_SCHEMES
        try:
            parsed = urlsplit(address)
        except ValueError as exc:
            raise SecretManagerConfigurationError("SOVRA_OPENBAO_ADDR is not a valid URL") from exc
        if parsed.scheme not in allowed or not parsed.netloc:
            raise SecretManagerConfigurationError(
                f"SOVRA_OPENBAO_ADDR must use one of {sorted(allowed)} with a host "
                f"(require_tls={require_tls})"
            )
        return cls(address=address.rstrip("/"), token=token)

    def __repr__(self) -> str:
        # Never echo the token -- same class of rule as Settings.__repr__.
        return f"OpenBaoConfig(address={self.address!r}, token=<redacted:{len(self.token)} chars>)"


def _send_once(
    config: OpenBaoConfig,
    method: str,
    url_path: str,
    body: dict[str, object] | None,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    url = f"{config.address}{url_path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("X-Vault-Token", config.token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SecretBackendError(f"secret backend request to {url_path} failed") from exc


def _retry_wait_seconds(headers: Mapping[str, str], default: float) -> float:
    retry_after = headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass
    return default


def _request(
    config: OpenBaoConfig,
    method: str,
    url_path: str,
    *,
    body: dict[str, object] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    retry_delay_seconds: float = _DEFAULT_RETRY_DELAY_SECONDS,
) -> dict[str, object]:
    """The one place every real HTTP call to OpenBao is made. Retries only
    a transient failure (429 or 5xx), up to `_MAX_ATTEMPTS` total attempts,
    honoring a real `Retry-After` header when the backend sends one
    (Section 3.3: 'Retry only transient/429/5xx failures, respecting
    Retry-After and a three-attempt ceiling'). 403/404/2xx/other 4xx never
    retry -- a denial or a missing path is not a transient condition."""
    status = 0
    raw = b""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        status, headers, raw = _send_once(config, method, url_path, body, timeout)
        if status == 429 or status >= 500:
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_retry_wait_seconds(headers, retry_delay_seconds))
                continue
            raise SecretBackendError(
                f"secret backend returned HTTP {status} for {url_path} after "
                f"{_MAX_ATTEMPTS} attempts"
            )
        break

    if status == 403:
        raise SecretAccessDeniedError(f"secret backend denied access to {url_path}")
    if status == 404:
        raise SecretNotFoundError(f"secret backend has no data at {url_path}")
    if status == 204 or not raw:
        return {}
    if status >= 400:
        raise SecretBackendError(f"secret backend returned HTTP {status} for {url_path}")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SecretBackendError("secret backend returned a malformed response") from exc
    if not isinstance(parsed, dict):
        raise SecretBackendError("secret backend returned a non-object response")
    return parsed


def _enforce_not_expired(expires_raw: str, *, now: datetime | None) -> None:
    try:
        expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SecretBackendError("secret backend returned a malformed expires_at") from exc
    current = now if now is not None else datetime.now(UTC)
    if expires_at <= current:
        raise SecretExpiredError(f"secret expired at {expires_raw}")


def resolve_secret_ref(ref: str, *, config: OpenBaoConfig, now: datetime | None = None) -> str:
    """Resolves a real `openbao://<mount>/<path>/<field>` ref to its real
    value. Never returns a value for an unknown path, a soft-deleted/
    destroyed version, an access-denied token, or an expired secret --
    each raises its own named exception instead (this task's own
    negative_acceptance in full)."""
    parsed = parse_secret_ref(ref)
    payload = _request(config, "GET", f"/v1/{parsed.mount}/data/{parsed.path}")
    data_block = payload.get("data")
    if not isinstance(data_block, dict):
        raise SecretBackendError(f"secret backend response for {ref} is missing 'data'")

    fields = data_block.get("data")
    if fields is None:
        # Defensive only: a soft-deleted/destroyed KV v2 version was
        # verified to answer HTTP 404 (raised by `_request` already,
        # before this function ever sees a payload), never HTTP 200 with a
        # null `data` -- this branch guards a response shape not actually
        # observed for KV v2, in case a differently configured mount ever
        # returns 200 with no live data instead of 404.
        raise SecretNotFoundError(
            f"secret at {parsed.mount}/{parsed.path} has no live data (deleted)"
        )
    if not isinstance(fields, dict) or parsed.field not in fields:
        raise SecretNotFoundError(
            f"field {parsed.field!r} is not present at {parsed.mount}/{parsed.path}"
        )

    metadata = data_block.get("metadata")
    custom_metadata = metadata.get("custom_metadata") if isinstance(metadata, dict) else None
    if isinstance(custom_metadata, dict):
        expires_raw = custom_metadata.get("expires_at")
        if isinstance(expires_raw, str) and expires_raw:
            _enforce_not_expired(expires_raw, now=now)

    value = fields[parsed.field]
    if not isinstance(value, str):
        raise SecretBackendError(f"field {parsed.field!r} at {ref} is not a string value")
    return value


def _validate_value(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SecretValueError(
            "value must be a non-empty string -- never store an empty/None secret"
        )
    return value


def _read_current_fields(config: OpenBaoConfig, mount: str, path: str) -> dict[str, object]:
    try:
        payload = _request(config, "GET", f"/v1/{mount}/data/{path}")
    except SecretNotFoundError:
        return {}
    data_block = payload.get("data")
    fields = data_block.get("data") if isinstance(data_block, dict) else None
    return dict(fields) if isinstance(fields, dict) else {}


def _read_current_version(config: OpenBaoConfig, mount: str, path: str) -> int | None:
    try:
        payload = _request(config, "GET", f"/v1/{mount}/data/{path}")
    except SecretNotFoundError:
        return None
    data_block = payload.get("data")
    fields = data_block.get("data") if isinstance(data_block, dict) else None
    if fields is None:
        return None  # the current version is itself already deleted
    metadata = data_block.get("metadata") if isinstance(data_block, dict) else None
    version = metadata.get("version") if isinstance(metadata, dict) else None
    return version if isinstance(version, int) else None


def _write_expiry_metadata(
    config: OpenBaoConfig, *, mount: str, path: str, expires_at: datetime
) -> None:
    if expires_at.tzinfo is None:
        raise SecretManagerError("expires_at must be timezone-aware")
    iso = expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    _request(
        config,
        "POST",
        f"/v1/{mount}/metadata/{path}",
        body={"custom_metadata": {"expires_at": iso}},
    )


def store_secret_ref(
    *,
    config: OpenBaoConfig,
    mount: str,
    path: str,
    field: str,
    value: str,
    expires_at: datetime | None = None,
) -> str:
    """Writes `value` under `field` at a real KV v2 path and returns the
    matching `openbao://` ref. Merges onto any fields already present at
    this path (a real KV v2 write replaces the whole path's data, not one
    field) so co-located fields at the same path are never silently
    wiped."""
    _validate_value(value)
    existing = _read_current_fields(config, mount, path)
    existing[field] = value
    _request(config, "POST", f"/v1/{mount}/data/{path}", body={"data": existing})
    if expires_at is not None:
        _write_expiry_metadata(config, mount=mount, path=path, expires_at=expires_at)
    return build_secret_ref(mount=mount, path=path, field=field)


def rotate_secret_ref(
    ref: str,
    *,
    config: OpenBaoConfig,
    new_value: str,
    expires_at: datetime | None = None,
) -> str:
    """Writes a new KV v2 version for `ref`'s own field, then soft-deletes
    the version it superseded -- a resolve pinned to the prior version (or
    a caller who cached the old value's version number) can never read the
    rotated-away value again. Refuses (`SecretNotFoundError`) to rotate a
    ref with no existing secret -- rotation presumes something to rotate;
    a caller creating one for the first time uses `store_secret_ref`."""
    parsed = parse_secret_ref(ref)
    _validate_value(new_value)
    previous_version = _read_current_version(config, parsed.mount, parsed.path)
    if previous_version is None:
        raise SecretNotFoundError(
            f"cannot rotate -- no existing secret at {parsed.mount}/{parsed.path}"
        )
    existing = _read_current_fields(config, parsed.mount, parsed.path)
    existing[parsed.field] = new_value
    _request(config, "POST", f"/v1/{parsed.mount}/data/{parsed.path}", body={"data": existing})
    if expires_at is not None:
        _write_expiry_metadata(config, mount=parsed.mount, path=parsed.path, expires_at=expires_at)
    _request(
        config,
        "POST",
        f"/v1/{parsed.mount}/delete/{parsed.path}",
        body={"versions": [previous_version]},
    )
    return ref
