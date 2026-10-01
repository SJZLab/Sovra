"""Database/API/trace source contract and fail-closed validation (FTF-0500C,
Section 7A.10/8/9/45/65/67/77/81).

Section 67.1's own connector table names two further real behaviors this
task implements for a `database`/`api`/`agent_traces`/
`production_conversations` `DatasetSourceConnection` (FTF-0500, CERTIFIED),
the same "pure connector, given an already-authorized connection's own
resolved endpoint/credentials" shape `FTF-0303`'s own
`private_s3_artifact_resolver.py` and `FTF-0500B`'s own
`s3_minio_source_validation_metadata_preview.py` already established for the
S3/MinIO sibling source type:

  - **PostgreSQL** -- "Read-only connection, allowlisted schema/table/view
    and columns... Generated parameterized SELECT, repeatable snapshot/
    export strategy, statement timeout and backpressure; no user SQL or
    writes." `validate_and_preview_postgres_source` never accepts a raw SQL
    fragment: `schema`/`table`/every column name and the stable key column
    are validated as real Postgres identifiers (`_IDENTIFIER_PATTERN`)
    before being embedded via `psycopg.sql.Identifier` (never Python string
    interpolation) into a single generated, fully parameterized
    `SELECT ... WHERE <key> > %s ORDER BY <key> ASC LIMIT %s`. The session
    is placed into `READ ONLY, REPEATABLE READ` (both this task's own
    "no... writes" acceptance and its own "repeatable snapshot" acceptance,
    made real by one Postgres transaction-mode setting, not two separate
    mechanisms) and a real `statement_timeout` is set and independently
    re-read back (`SHOW statement_timeout`) before any row is fetched --
    this task's own "backpressure" clause. This module never opens a
    connection with elevated/writer credentials itself: the caller supplies
    an already-resolved `conninfo` bound to a real, separately-provisioned,
    `SELECT`-only Postgres role (see `tests/tasks/test_ftf_0500c.py`'s own
    `sovra_ftf0500c_ro` fixture role), the same "caller resolves the
    credential, this module only ever sees an already-resolved connection"
    boundary FTF-0500B/FTF-0303 already established for their own configs.

  - **REST JSON/API and agent-trace/production-conversation sources**
    (Section 67.1's own table: "one of the above transports" for the trace
    row) -- "Bounded pagination/cursors, 429 backoff, version/ETag or
    content snapshot, cancellation; no arbitrary scripting."
    `validate_and_preview_rest_trace_source` paginates a real, approved
    endpoint through a declared cursor field, retries a real HTTP 429 a
    bounded number of times honoring the endpoint's own `Retry-After`
    (never an unbounded retry loop), tracks a declared snapshot-version
    response header across pages the same way `FTF-0500B`'s own
    `SourceChangedDuringListingError` tracks `IsLatest` across S3 pages, and
    accepts a cooperative `cancellation_check` callback checked between page
    fetches. "No arbitrary scripting" is structural, not merely disclosed:
    this module builds exactly one fixed HTTP GET per page from typed
    config fields (a declared cursor query-parameter name and a fixed,
    caller-approved extra-query-parameter dict); it never evaluates,
    formats-in, or executes any caller-supplied expression, template, or
    code. Every real HTTP request goes through
    `packages/security/outbound_guard.py`'s `guarded_fetch` (FTF-H006) --
    `packages/security/url_policy.py`'s own module docstring already names
    this exact task (`FTF-0500C`) as one of its intended future callers,
    identical to how it already names `FTF-0500B`.

Both connectors share this task's own negative_acceptance in full:

  - **"Unknown database/API contract is unavailable, not a placeholder"**
    -- a Postgres `UndefinedTable`/`UndefinedColumn` error (the schema/table
    the caller named does not actually exist in the real, connected
    database) is caught and re-raised as `UnknownSourceContractError`, never
    silently treated as an empty result. A REST response whose parsed JSON
    body is missing the declared `records_field`, or where that field is
    not a JSON array, is likewise refused as `UnknownSourceContractError`
    -- this module never guesses a records shape or returns a fabricated
    empty preview for a contract it cannot actually parse.
  - **"Lost watermark/snapshot blocks consistent import"** -- for Postgres,
    resuming a prior capture supplies `resume_after_key` (the last stable
    key value a previous call returned); this module re-verifies, inside
    the *same* `REPEATABLE READ` transaction as the subsequent `SELECT` (so
    the check and the read see one identical, real database snapshot -- a
    concurrent delete between two separate transactions could never race
    this check), that a row at exactly that key still exists. If it does
    not, the watermark the caller is resuming from has been lost --
    something in the ordering the caller depended on may have shifted --
    and this module refuses to resume (`LostWatermarkError`), rather than
    silently starting from an arbitrary position and risking a duplicate or
    skipped import. For the REST/trace connector, the identical shape
    applies to a mid-listing snapshot-version change:
    `SourceChangedDuringListingError` (a subclass of the same
    `LostWatermarkError` family) is raised the instant a later page's
    snapshot-version header differs from the first page's -- including a
    later page that drops the header entirely once an earlier page carried
    one, which is just as much a lost snapshot as a changed value -- rather
    than silently mixing two different underlying snapshots into one
    preview.

This module does not implement Section 8's own file-content import pipeline
(canonical-schema normalization, PII/duplicate detection) -- the same
disclosed, dependency-gated boundary `FTF-0500B`'s own docstring already
established for its identical-shaped sibling task. It does not read or
write `DatasetSourceConnection` rows, resolve `credential_secret_ref`/
`connection_config_encrypted_ref` via OpenBao, or expose an HTTP route --
those remain a separately dependency-gated composition task. No new
database field or entity is added here (`database_changes: "No independent
schema change"`).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg
from packages.security.outbound_guard import GuardedResponse, OutboundGuardError, guarded_fetch
from packages.security.url_policy import ConnectionPolicy
from psycopg import sql
from psycopg.errors import UndefinedColumn, UndefinedTable

__all__ = [
    "DEFAULT_MAX_PAGE_FETCHES",
    "DEFAULT_MAX_PREVIEW_RECORDS",
    "DEFAULT_MAX_RATE_LIMIT_RETRIES",
    "DEFAULT_STATEMENT_TIMEOUT_MS",
    "LostWatermarkError",
    "PostgresIdentifierError",
    "PostgresRecordPreview",
    "PostgresSourceConfig",
    "PostgresSourceValidationResult",
    "RateLimitRetriesExhaustedError",
    "RestTraceSourceConfig",
    "RestTraceSourceValidationResult",
    "SourceChangedDuringListingError",
    "SourceUnavailableError",
    "UnknownSourceContractError",
    "default_rest_trace_source_policy",
    "validate_and_preview_postgres_source",
    "validate_and_preview_rest_trace_source",
]

# Section 67.2: "preview at most 20 records inside the authorized private
# boundary."
DEFAULT_MAX_PREVIEW_RECORDS = 20
DEFAULT_STATEMENT_TIMEOUT_MS = 5_000
# Bounds a malicious/misbehaving REST endpoint that returns a valid
# next-cursor forever without ever reaching `max_preview_records` -- this
# task's own "bounded pagination" acceptance, enforced structurally rather
# than only by the preview-record cap.
DEFAULT_MAX_PAGE_FETCHES = 50
DEFAULT_MAX_RATE_LIMIT_RETRIES = 3
# Never sleep longer than this for one real `Retry-After`, however large the
# endpoint's own header value -- an endpoint cannot stall this module
# indefinitely by naming an enormous retry delay.
_MAX_RETRY_AFTER_SECONDS = 30.0
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Real Postgres identifier grammar (unquoted, ordinary identifiers): starts
# with a letter/underscore, then letters/digits/underscores, bounded to
# Postgres's own real 63-byte `NAMEDATALEN` limit. Anything else -- in
# particular any value containing a quote, semicolon, whitespace or SQL
# keyword-shaped payload -- is refused before this module ever builds a
# query, which is what makes "no user SQL" real rather than merely a
# parameterization convention that could be bypassed by an unvalidated
# identifier.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

GuardedFetchFn = Callable[..., GuardedResponse]
ConnectFn = Callable[..., "psycopg.Connection[tuple[object, ...]]"]


class SourceUnavailableError(ValueError):
    """Base class for every real defect this module raises validating/
    previewing a `database`/`api`/`agent_traces`/`production_conversations`
    dataset source -- never caught and silently converted to a fabricated
    success anywhere in this module."""


class UnknownSourceContractError(SourceUnavailableError):
    """This task's own negative_acceptance, first half: the declared
    schema/table/column contract (Postgres) or the declared records-field
    contract (REST) does not actually match the real connected source."""


class LostWatermarkError(SourceUnavailableError):
    """This task's own negative_acceptance, second half: a resumed capture's
    watermark row no longer exists in the real source, or a real REST
    snapshot-version header changed mid-listing -- refused rather than
    silently resuming from an arbitrary, possibly inconsistent position."""


class SourceChangedDuringListingError(LostWatermarkError):
    """A real REST source's own declared snapshot-version header differed
    between two pages of the same paginated listing."""


class PostgresIdentifierError(SourceUnavailableError):
    """A schema/table/column/stable-key name is not a real, well-formed
    Postgres identifier -- refused before any query is built or any
    connection is opened. This is the actual mechanism behind "no user
    SQL": every identifier this module ever embeds in a query is checked
    against `_IDENTIFIER_PATTERN` first."""


class RateLimitRetriesExhaustedError(SourceUnavailableError):
    """A real REST endpoint kept answering HTTP 429 past this module's own
    bounded retry budget."""


def _validate_identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.match(value):
        raise PostgresIdentifierError(
            f"{field_name} must be a real Postgres identifier "
            f"(letters/digits/underscore, starting with a letter or underscore, at most "
            f"63 characters), never a raw SQL fragment: {value!r}"
        )
    return value


@dataclass(frozen=True)
class PostgresSourceConfig:
    """The real, explicit, already-resolved trust boundary a caller
    supplies for one bounded Postgres preview/capture call. `conninfo` is
    an already-resolved connection string bound to a real, least-privileged,
    `SELECT`-only role -- this module never receives a raw credential to
    resolve itself (the same boundary `S3MinioSourceConfig`/
    `PrivateS3ConnectionConfig` already established for their own source
    types), and never issues a mutating statement regardless of what the
    connected role could technically do."""

    conninfo: str
    schema: str
    table: str
    columns: tuple[str, ...]
    stable_key_column: str
    max_preview_records: int = DEFAULT_MAX_PREVIEW_RECORDS
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS


@dataclass(frozen=True)
class PostgresRecordPreview:
    values: tuple[object, ...]


@dataclass(frozen=True)
class PostgresSourceValidationResult:
    schema: str
    table: str
    columns: tuple[str, ...]
    stable_key_column: str
    rows: tuple[PostgresRecordPreview, ...]
    truncated: bool
    next_watermark: object
    manifest_hash: str
    captured_at: str


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _verify_watermark_present(
    conn: psycopg.Connection[tuple[object, ...]],
    *,
    schema: str,
    table: str,
    stable_key_column: str,
    resume_after_key: object,
) -> None:
    query = sql.SQL("SELECT 1 FROM {schema}.{table} WHERE {key} = %s").format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        key=sql.Identifier(stable_key_column),
    )
    row = conn.execute(query, (resume_after_key,)).fetchone()
    if row is None:
        raise LostWatermarkError(
            f"resume_after_key {resume_after_key!r} no longer exists in "
            f"{schema}.{table} ({stable_key_column}) -- the watermark this capture was "
            "resuming from has been lost (the row was deleted or the source's ordering "
            "shifted); refusing to resume from an arbitrary position"
        )


def validate_and_preview_postgres_source(
    config: PostgresSourceConfig,
    *,
    resume_after_key: object = None,
    connect: ConnectFn = psycopg.connect,
) -> PostgresSourceValidationResult:
    """Validates and previews at most `config.max_preview_records` rows of
    exactly one real, allowlisted Postgres schema/table/column set, ordered
    by `stable_key_column` ascending, optionally resuming after
    `resume_after_key`. Raises one of this module's own error classes for
    every negative_acceptance case; never returns a partial or fabricated
    result."""
    if not isinstance(config.columns, tuple) or not config.columns:
        raise PostgresIdentifierError("columns must be a non-empty tuple of column names")
    if config.max_preview_records < 1:
        raise SourceUnavailableError(
            f"max_preview_records must be a positive integer: {config.max_preview_records!r}"
        )

    schema = _validate_identifier(config.schema, field_name="schema")
    table = _validate_identifier(config.table, field_name="table")
    stable_key_column = _validate_identifier(
        config.stable_key_column, field_name="stable_key_column"
    )
    columns = tuple(_validate_identifier(column, field_name="columns") for column in config.columns)
    all_columns = (stable_key_column, *(c for c in columns if c != stable_key_column))

    try:
        with connect(config.conninfo, autocommit=False) as conn:
            with conn.transaction():
                # Section 67.1: "no user SQL or writes" -- and, at the exact
                # same time, "repeatable snapshot/export strategy": one
                # Postgres transaction-mode setting gives both real
                # guarantees for the rest of this transaction.
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                conn.execute(
                    sql.SQL("SET statement_timeout = {}").format(
                        sql.Literal(config.statement_timeout_ms)
                    )
                )
                # This task's own "backpressure" clause is not merely
                # requested -- it is independently read back from the real
                # session before any row is fetched, so a silently-ignored
                # `SET` (e.g. a role lacking permission to change it) is
                # itself a real, caught defect rather than an unproven claim.
                observed_timeout = conn.execute("SHOW statement_timeout").fetchone()
                if observed_timeout is None or observed_timeout[0] == "0":
                    raise SourceUnavailableError(
                        "the connected Postgres session did not accept a real "
                        "statement_timeout -- refusing to run an unbounded query"
                    )

                if resume_after_key is not None:
                    _verify_watermark_present(
                        conn,
                        schema=schema,
                        table=table,
                        stable_key_column=stable_key_column,
                        resume_after_key=resume_after_key,
                    )

                select_columns = sql.SQL(", ").join(sql.Identifier(c) for c in all_columns)
                if resume_after_key is None:
                    query = sql.SQL(
                        "SELECT {cols} FROM {schema}.{table} ORDER BY {key} ASC LIMIT %s"
                    ).format(
                        cols=select_columns,
                        schema=sql.Identifier(schema),
                        table=sql.Identifier(table),
                        key=sql.Identifier(stable_key_column),
                    )
                    params: tuple[object, ...] = (config.max_preview_records,)
                else:
                    query = sql.SQL(
                        "SELECT {cols} FROM {schema}.{table} WHERE {key} > %s "
                        "ORDER BY {key} ASC LIMIT %s"
                    ).format(
                        cols=select_columns,
                        schema=sql.Identifier(schema),
                        table=sql.Identifier(table),
                        key=sql.Identifier(stable_key_column),
                    )
                    params = (resume_after_key, config.max_preview_records)

                try:
                    fetched = conn.execute(query, params).fetchall()
                except (UndefinedTable, UndefinedColumn) as exc:
                    raise UnknownSourceContractError(
                        f"the declared schema/table/column contract {schema}.{table} "
                        f"{all_columns} does not exist in the real connected database -- "
                        "refusing to fabricate an empty preview for an unknown contract"
                    ) from exc
    except OSError as exc:
        raise SourceUnavailableError(f"could not reach the Postgres source: {exc}") from exc

    rows = tuple(PostgresRecordPreview(values=row) for row in fetched)
    truncated = len(rows) >= config.max_preview_records
    key_index = all_columns.index(stable_key_column)
    next_watermark = rows[-1].values[key_index] if rows else resume_after_key

    manifest_source = [[_json_safe(v) for v in row.values] for row in rows]
    manifest_hash = hashlib.sha256(
        json.dumps(manifest_source, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()

    return PostgresSourceValidationResult(
        schema=schema,
        table=table,
        columns=all_columns,
        stable_key_column=stable_key_column,
        rows=rows,
        truncated=truncated,
        next_watermark=next_watermark,
        manifest_hash=manifest_hash,
        captured_at=datetime.now(UTC).isoformat(),
    )


@dataclass(frozen=True)
class RestTraceSourceConfig:
    """The real, explicit, already-resolved trust boundary for one bounded
    REST/agent-trace preview call. `headers` is already-resolved (bearer
    token or similar), never a `credential_secret_ref` -- the same
    "caller resolves the secret" boundary every sibling connector in this
    repository already established. `extra_query_params` is a fixed,
    caller-approved dict, never a template or expression -- this is what
    makes "no arbitrary scripting" structural."""

    base_url: str
    records_field: str
    cursor_response_field: str
    cursor_query_param: str
    headers: Mapping[str, str] = field(default_factory=dict)
    extra_query_params: Mapping[str, str] = field(default_factory=dict)
    snapshot_version_header: str | None = "ETag"
    page_size_query_param: str | None = "limit"
    page_size: int = 20
    max_preview_records: int = DEFAULT_MAX_PREVIEW_RECORDS
    max_page_fetches: int = DEFAULT_MAX_PAGE_FETCHES
    max_rate_limit_retries: int = DEFAULT_MAX_RATE_LIMIT_RETRIES


@dataclass(frozen=True)
class RestTraceSourceValidationResult:
    base_url: str
    records: tuple[Mapping[str, object], ...]
    truncated: bool
    cancelled: bool
    snapshot_version: str | None
    manifest_hash: str
    captured_at: str


def default_rest_trace_source_policy() -> ConnectionPolicy:
    """Production default: no redirects, no private-address allowlist -- an
    operator who genuinely needs a private-network REST/trace endpoint
    supplies an explicit `ConnectionPolicy` naming it, the same deliberate
    "caller supplies it" precedent `default_s3_minio_preview_policy`/
    `default_private_s3_resolver_policy` already established."""
    return ConnectionPolicy(name="database_api_trace_source_contract_and_fail_closed_validation")


def _header_lookup(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _build_page_url(config: RestTraceSourceConfig, *, cursor: str | None) -> str:
    params: dict[str, str] = dict(config.extra_query_params)
    if config.page_size_query_param:
        params[config.page_size_query_param] = str(config.page_size)
    if cursor is not None:
        params[config.cursor_query_param] = cursor
    if not params:
        return config.base_url
    separator = "&" if "?" in config.base_url else "?"
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{config.base_url}{separator}{query}"


def _retry_after_seconds(response: GuardedResponse) -> float:
    raw = _header_lookup(response.headers, "Retry-After")
    if raw is None:
        return 1.0
    try:
        seconds = float(raw)
    except ValueError:
        return 1.0
    return max(0.0, min(seconds, _MAX_RETRY_AFTER_SECONDS))


def validate_and_preview_rest_trace_source(
    config: RestTraceSourceConfig,
    *,
    policy: ConnectionPolicy | None = None,
    fetch: GuardedFetchFn = guarded_fetch,
    cancellation_check: Callable[[], bool] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> RestTraceSourceValidationResult:
    """Validates and previews at most `config.max_preview_records` records
    of exactly one real, cursor-paginated REST or agent-trace/production-
    conversation source. Raises one of this module's own error classes for
    every negative_acceptance case; never returns a partial or fabricated
    result."""
    if not isinstance(config.base_url, str) or not config.base_url:
        raise SourceUnavailableError("base_url must be a non-empty string")
    if config.max_preview_records < 1:
        raise SourceUnavailableError(
            f"max_preview_records must be a positive integer: {config.max_preview_records!r}"
        )

    active_policy = policy or default_rest_trace_source_policy()
    collected: list[Mapping[str, object]] = []
    cursor: str | None = None
    snapshot_version: str | None = None
    snapshot_version_seen = False
    truncated = False
    cancelled = False
    page_fetches = 0

    while len(collected) < config.max_preview_records:
        if page_fetches >= config.max_page_fetches:
            raise SourceUnavailableError(
                f"exceeded max_page_fetches ({config.max_page_fetches}) without reaching "
                "max_preview_records or the end of the listing -- refusing to paginate "
                "unboundedly"
            )
        page_fetches += 1
        url = _build_page_url(config, cursor=cursor)

        attempt = 0
        while True:
            try:
                response = fetch(
                    url, active_policy, method="GET", headers=dict(config.headers), timeout=timeout
                )
            except OutboundGuardError as exc:
                raise SourceUnavailableError(
                    f"could not reach the REST/trace source at {config.base_url!r}: {exc}"
                ) from exc
            if response.status == 429:
                attempt += 1
                if attempt > config.max_rate_limit_retries:
                    raise RateLimitRetriesExhaustedError(
                        f"the REST/trace source at {config.base_url!r} kept answering HTTP "
                        f"429 past {config.max_rate_limit_retries} bounded retries"
                    )
                sleep(_retry_after_seconds(response))
                continue
            break

        if response.status != 200:
            raise SourceUnavailableError(
                f"unexpected HTTP {response.status} fetching {config.base_url!r}"
            )

        try:
            body = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise UnknownSourceContractError(
                f"the REST/trace source's response was not valid JSON: {exc}"
            ) from exc
        if not isinstance(body, dict):
            raise UnknownSourceContractError(
                "the REST/trace source's response was not a JSON object"
            )
        records = body.get(config.records_field)
        if not isinstance(records, list):
            raise UnknownSourceContractError(
                f"the REST/trace source's response is missing a real JSON array at declared "
                f"records_field {config.records_field!r} -- refusing to fabricate an empty "
                "preview for an unknown contract"
            )

        if config.snapshot_version_header:
            observed = _header_lookup(response.headers, config.snapshot_version_header)
            if not snapshot_version_seen:
                if observed is not None:
                    snapshot_version = observed
                    snapshot_version_seen = True
            elif observed != snapshot_version:
                # Once a real snapshot-version has been seen on an earlier
                # page, a later page that drops the header entirely
                # (`observed is None`) is just as much a lost snapshot as one
                # that carries a genuinely different value -- both mean this
                # listing can no longer be trusted to be one consistent
                # snapshot, so both raise the same way.
                raise SourceChangedDuringListingError(
                    f"the source's own {config.snapshot_version_header!r} header changed "
                    f"from {snapshot_version!r} to {observed!r} between two pages of the "
                    "same listing -- the source was mutated mid-listing; refusing to "
                    "silently mix two different snapshots into one preview"
                )

        for record in records:
            if len(collected) >= config.max_preview_records:
                truncated = True
                break
            if not isinstance(record, dict):
                raise UnknownSourceContractError(
                    "the REST/trace source's records array contained a non-object entry"
                )
            collected.append(record)

        if cancellation_check is not None and cancellation_check():
            cancelled = True
            break

        next_cursor = body.get(config.cursor_response_field)
        if not next_cursor or len(collected) >= config.max_preview_records:
            if len(records) == config.page_size and len(collected) < config.max_preview_records:
                truncated = truncated or bool(next_cursor)
            break
        cursor = str(next_cursor)

    manifest_hash = hashlib.sha256(
        json.dumps(list(collected), sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()

    return RestTraceSourceValidationResult(
        base_url=config.base_url,
        records=tuple(collected),
        truncated=truncated,
        cancelled=cancelled,
        snapshot_version=snapshot_version,
        manifest_hash=manifest_hash,
        captured_at=datetime.now(UTC).isoformat(),
    )
