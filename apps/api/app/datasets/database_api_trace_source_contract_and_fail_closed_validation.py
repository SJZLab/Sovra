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
    snapshot-version header differs from the first page's, rather than
    silently mixing two different underlying snapshots into one preview.

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
from urllib.parse import quote

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
    "NonUniqueStableKeyError",
    "PostgresIdentifierError",
    "PostgresRecordPreview",
    "PostgresSourceConfig",
    "PostgresSourceValidationResult",
    "RateLimitRetriesExhaustedError",
    "RestTraceSourceConfig",
    "RestTraceSourceValidationResult",
    "SnapshotUnstableError",
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


class SnapshotUnstableError(SourceUnavailableError):
    """Section 67.1: "if consistency cannot be guaranteed, show
    SNAPSHOT_UNSTABLE and block production data use." A caller declared a
    `snapshot_version_header` to track (it wants a consistency guarantee
    across pages), the listing genuinely spanned more than one real page,
    and the source never once actually sent that header on any of them --
    there is then no real signal this module could have checked, so
    consistency across the pages it already returned cannot be guaranteed
    one way or the other. Returning a quiet success here would be exactly
    the un-provable "nothing changed" claim Section 67.1 forbids."""


class NonUniqueStableKeyError(SourceUnavailableError):
    """A Postgres `stable_key_column` that is not safely unique and
    orderable for keyset pagination (`WHERE key > %s ORDER BY key ASC LIMIT
    %s`), which silently skips or drops rows in three distinct real ways:

      - two or more rows share the exact same key value and that tied
        group is split across a page boundary (the `>` resume comparison
        permanently skips every further row still carrying the
        already-returned key), or are duplicated within one page if a
        caller mistakes repeated key values for distinct records;
      - the key column contains a real SQL `NULL` -- a `NULL` key can never
        satisfy `key > %s` (NULL comparisons are never true in SQL), so a
        `NULL`-keyed row is permanently unreachable the moment any resume
        happens, whether or not it is ever tied with another row;
      - Python equality disagrees with the real connected database's own
        comparison semantics for this exact column -- a NaN in a real
        `float8` column (Postgres treats NaN as equal to itself; Python's
        `==` never does), or a non-default collation on a real `text`
        column (e.g. `b`/`B` tie under a case-insensitive ICU collation,
        which Python has no way to know about).

    Because of the last point, tie detection is never done with Python
    equality or a Python `set()` -- it is pushed into the query itself
    (`{key} = LAG({key}) OVER (ORDER BY {key} ASC)`), which inherits the
    stable key column's own real type, NaN handling and collation directly
    from the connected database, rather than trusting a Python-side
    approximation of what "the same value" means for an arbitrary real
    column. NULL keys are refused separately, by a whole-table existence
    check (`_verify_no_null_keys`) that runs before this comparison is ever
    built -- so by the time this LAG comparison runs, a plain `=` and
    `IS NOT DISTINCT FROM` are provably equivalent (no NULL operand can
    reach it), and `=` is used since it carries no unproven claim about
    NULL-vs-NULL semantics."""


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
    # 2026-10-04 L2 round-5 finding: counts the real matching rows, not
    # merely their existence -- a real concurrent INSERT of a second row at
    # exactly the watermark value (racing in after the page that produced
    # this watermark was fetched, in its own separate snapshot) would pass
    # a bare existence check while the resumed `WHERE key > %s` comparison
    # is now genuinely ambiguous about which of the two tied rows it is
    # resuming past.
    query = sql.SQL("SELECT count(*) FROM {schema}.{table} WHERE {key} = %s").format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        key=sql.Identifier(stable_key_column),
    )
    try:
        row = conn.execute(query, (resume_after_key,)).fetchone()
    except (UndefinedTable, UndefinedColumn) as exc:
        raise UnknownSourceContractError(
            f"the declared schema/table/column contract {schema}.{table} "
            f"({stable_key_column}) does not exist in the real connected database -- "
            "refusing to fabricate an empty preview for an unknown contract"
        ) from exc
    count = int(row[0]) if row is not None else 0  # type: ignore[call-overload]
    if count == 0:
        raise LostWatermarkError(
            f"resume_after_key {resume_after_key!r} no longer exists in "
            f"{schema}.{table} ({stable_key_column}) -- the watermark this capture was "
            "resuming from has been lost (the row was deleted or the source's ordering "
            "shifted); refusing to resume from an arbitrary position"
        )
    if count > 1:
        raise NonUniqueStableKeyError(
            f"resume_after_key {resume_after_key!r} now matches {count} real rows in "
            f"{schema}.{table} ({stable_key_column}) -- the stable key is no longer unique "
            "at the exact resume point (a real concurrent insert raced in after this "
            "watermark's own page was fetched); refusing to resume from an ambiguous position"
        )


def _verify_no_null_keys(
    conn: psycopg.Connection[tuple[object, ...]],
    *,
    schema: str,
    table: str,
    stable_key_column: str,
) -> None:
    """2026-10-04 L2 round-5 finding: a real NULL stable-key value outside
    the current call's own fetched-plus-lookahead window was silently
    dropped -- `ORDER BY key ASC` places NULLs last by default, so a
    resumed `WHERE key > %s` can never reach a NULL-keyed row (`NULL > %s`
    is never true in SQL), and once the capture's own window has moved
    past where a NULL would have sorted, nothing in that call's own result
    set ever looks at it again. Checking only the current window's own
    lookahead row (as the main query below still does, for a defect
    actually visible within one call) cannot catch a NULL that never enters
    any call's window at all. This check runs unconditionally, on every
    call (not only a resumed one), against the WHOLE table, inside the
    same `REPEATABLE READ` transaction as the paginated `SELECT` that
    follows it -- so it sees the identical real snapshot, and a NULL that
    exists anywhere in the source is always caught before a single row is
    returned, never only when it happens to land in one call's own
    window."""
    query = sql.SQL("SELECT EXISTS (SELECT 1 FROM {schema}.{table} WHERE {key} IS NULL)").format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        key=sql.Identifier(stable_key_column),
    )
    try:
        row = conn.execute(query).fetchone()
    except (UndefinedTable, UndefinedColumn) as exc:
        raise UnknownSourceContractError(
            f"the declared schema/table/column contract {schema}.{table} "
            f"({stable_key_column}) does not exist in the real connected database -- "
            "refusing to fabricate an empty preview for an unknown contract"
        ) from exc
    if row is not None and bool(row[0]):
        raise NonUniqueStableKeyError(
            f"stable_key_column {stable_key_column!r} contains at least one real NULL "
            f"value in {schema}.{table} -- a NULL key can never satisfy `key > %s` and "
            "would become permanently unreachable on some resumed capture, however far "
            "from the current window; refusing the whole source rather than risk "
            "silently losing rows beyond the page this call happens to fetch"
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
    # `isinstance(True, int)` is `True` in Python -- checked first and
    # separately, otherwise a real `bool` would silently pass an `isinstance
    # (..., int)` check and could reach `SET statement_timeout = {}` below.
    if (
        isinstance(config.statement_timeout_ms, bool)
        or not isinstance(config.statement_timeout_ms, int)
        or config.statement_timeout_ms <= 0
    ):
        raise SourceUnavailableError(
            "statement_timeout_ms must be a real positive int, never a bool or a "
            f"non-integer value: {config.statement_timeout_ms!r}"
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
                observed_timeout_value: object = (
                    observed_timeout[0] if observed_timeout is not None else None
                )
                if isinstance(observed_timeout_value, bytes):
                    # 2026-10-04 L2 round-4 finding: on a SQL_ASCII (or any
                    # non-UTF8) server encoding, psycopg can hand this value
                    # back as raw `bytes` rather than `str` -- comparing it
                    # to a plain string literal without decoding first would
                    # silently never match "0" on such a cluster (this
                    # repo's own CI test image is UTF8, so this path was
                    # never exercised there), letting a real
                    # statement_timeout=0 slip through undetected.
                    observed_timeout_value = observed_timeout_value.decode(
                        "ascii", errors="replace"
                    )
                if observed_timeout_value is None or str(observed_timeout_value).strip() == "0":
                    raise SourceUnavailableError(
                        "the connected Postgres session did not accept a real "
                        "statement_timeout -- refusing to run an unbounded query"
                    )

                # 2026-10-04 L2 round-5 finding: runs on every call, not only
                # a resumed one, and over the whole table, not only the
                # current window -- `_verify_no_null_keys`'s own docstring
                # has the full reasoning for why the lookahead-row check
                # below cannot substitute for this.
                _verify_no_null_keys(
                    conn,
                    schema=schema,
                    table=table,
                    stable_key_column=stable_key_column,
                )

                if resume_after_key is not None:
                    _verify_watermark_present(
                        conn,
                        schema=schema,
                        table=table,
                        stable_key_column=stable_key_column,
                        resume_after_key=resume_after_key,
                    )

                # Fetch one extra lookahead row past the real page boundary
                # (`max_preview_records + 1`): the only way to detect a
                # non-unique `stable_key_column` whose tied rows straddle
                # the LIMIT cutoff, which a single-row-over-the-line
                # `>`-comparison resume would otherwise silently skip on the
                # next call. `NonUniqueStableKeyError`'s own docstring has
                # the full reasoning, including why the tie check below is
                # pushed into the query itself rather than done with Python
                # equality or a Python `set()`; NULL keys are already
                # refused above by `_verify_no_null_keys`, so no NULL
                # operand can ever reach this comparison.
                fetch_limit = config.max_preview_records + 1
                select_columns = sql.SQL(", ").join(sql.Identifier(c) for c in all_columns)
                tied_with_prev = sql.SQL("({key} = LAG({key}) OVER (ORDER BY {key} ASC))").format(
                    key=sql.Identifier(stable_key_column)
                )
                if resume_after_key is None:
                    query = sql.SQL(
                        "SELECT {cols}, {tied} AS __tied_with_prev FROM {schema}.{table} "
                        "ORDER BY {key} ASC LIMIT %s"
                    ).format(
                        cols=select_columns,
                        tied=tied_with_prev,
                        schema=sql.Identifier(schema),
                        table=sql.Identifier(table),
                        key=sql.Identifier(stable_key_column),
                    )
                    params: tuple[object, ...] = (fetch_limit,)
                else:
                    query = sql.SQL(
                        "SELECT {cols}, {tied} AS __tied_with_prev FROM {schema}.{table} "
                        "WHERE {key} > %s ORDER BY {key} ASC LIMIT %s"
                    ).format(
                        cols=select_columns,
                        tied=tied_with_prev,
                        schema=sql.Identifier(schema),
                        table=sql.Identifier(table),
                        key=sql.Identifier(stable_key_column),
                    )
                    params = (resume_after_key, fetch_limit)

                try:
                    fetched_with_tie = conn.execute(query, params).fetchall()
                except (UndefinedTable, UndefinedColumn) as exc:
                    raise UnknownSourceContractError(
                        f"the declared schema/table/column contract {schema}.{table} "
                        f"{all_columns} does not exist in the real connected database -- "
                        "refusing to fabricate an empty preview for an unknown contract"
                    ) from exc

                extra_fetched = len(fetched_with_tie) > config.max_preview_records
                boundary_with_tie = fetched_with_tie[: config.max_preview_records]
                lookahead_with_tie = (
                    fetched_with_tie[config.max_preview_records] if extra_fetched else None
                )
                # `stable_key_column` is always column 0 of `all_columns`
                # (see its construction above), so index 0 of every fetched
                # row is always the real key value; `__tied_with_prev` is
                # always the trailing column this query itself appended.
                any_null_key = any(row[0] is None for row in boundary_with_tie) or (
                    lookahead_with_tie is not None and lookahead_with_tie[0] is None
                )
                intra_page_tie = any(bool(row[-1]) for row in boundary_with_tie)
                boundary_tie = lookahead_with_tie is not None and bool(lookahead_with_tie[-1])
                if any_null_key or intra_page_tie or boundary_tie:
                    raise NonUniqueStableKeyError(
                        f"stable_key_column {stable_key_column!r} is not safely unique and "
                        f"orderable within {schema}.{table} -- a real NULL key value, a "
                        "duplicate key tied to an adjacent row, or a key tied across the page "
                        "boundary would risk silently skipping or dropping rows across a "
                        "resumed capture; refusing to paginate on this key"
                    )
                # Drop the trailing `__tied_with_prev` column -- every
                # caller below expects exactly the row shape `all_columns`
                # already describes.
                fetched = [row[:-1] for row in boundary_with_tie]
    except OSError as exc:
        raise SourceUnavailableError(f"could not reach the Postgres source: {exc}") from exc
    except TypeError as exc:
        raise SourceUnavailableError(
            f"an unexpected type error occurred processing the Postgres source: {exc}"
        ) from exc
    except psycopg.Error as exc:
        raise SourceUnavailableError(f"a real Postgres error occurred: {exc}") from exc

    rows = tuple(PostgresRecordPreview(values=row) for row in fetched)
    truncated = extra_fetched
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
    # `quote(..., safe="")` -- a cursor/query-parameter value is real,
    # source-supplied data (never a caller-supplied expression; see this
    # module's own docstring on "no arbitrary scripting"), so it is never
    # assumed to already be URL-safe. An unescaped `&`/`=`/space/non-ASCII
    # byte inside a cursor would otherwise silently corrupt the next
    # request's own query-string structure.
    query = "&".join(
        f"{quote(str(key), safe='')}={quote(str(value), safe='')}" for key, value in params.items()
    )
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
    # Tracks every cursor this listing has already used, not merely the one
    # immediately before it -- a longer cycle (A -> B -> A) used to run
    # completely undetected all the way to the generic `max_page_fetches`
    # bound instead of being caught as the same real repeated-cursor defect
    # a one-step repeat (A -> A) already catches.
    used_cursors: set[str] = set()
    snapshot_version: str | None = None
    # Distinct from `snapshot_version is None` -- that's also the real,
    # legitimate value when page one genuinely had no header at all.
    # Without this separate flag, a page-one absence followed by a
    # page-two header appearing would look identical to "first value seen"
    # and be silently accepted as this listing's version.
    snapshot_version_initialized = False
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
        if cursor is not None:
            # Seeded before this cursor's own fetch -- so the very next
            # `next_cursor` this page returns is checked against a set that
            # already includes the cursor used to fetch it.
            used_cursors.add(cursor)
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
            if not snapshot_version_initialized:
                # Page one fixes this listing's own baseline -- `None` is a
                # real, legitimate baseline (the header is simply absent)
                # and is tracked exactly like any other observed value.
                snapshot_version = observed
                snapshot_version_initialized = True
            elif observed != snapshot_version:
                # Catches a changed value, a later page that drops the
                # header entirely after an earlier page carried one, and a
                # later page where the header appears for the first time
                # after page one had none -- all three are equally real
                # proof the source's own declared version is not a stable
                # signal across this listing, and silently falling back to
                # "no check" for any of them would defeat the
                # lost-watermark guarantee for a source whose header is
                # merely intermittent.
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
        if not next_cursor:
            # The source itself says this listing has genuinely ended --
            # not truncated.
            break
        next_cursor_str = str(next_cursor)
        if next_cursor_str in used_cursors:
            raise SourceUnavailableError(
                f"the REST/trace source returned cursor {next_cursor_str!r} again, having "
                "already been used earlier in this same listing (not merely the immediately "
                "preceding page) -- refusing to refetch an already-seen page and duplicate its "
                "records into the preview"
            )
        if len(collected) >= config.max_preview_records:
            # Stopped only because of this module's own cap, with the
            # source itself reporting further pages still exist.
            truncated = True
            break
        cursor = next_cursor_str

    if config.snapshot_version_header and snapshot_version is None and page_fetches > 1:
        raise SnapshotUnstableError(
            f"the declared snapshot_version_header {config.snapshot_version_header!r} never "
            f"appeared on any of the {page_fetches} real pages fetched for this multi-page "
            "listing -- consistency across those pages cannot be guaranteed one way or the "
            "other; refusing to return a possibly-inconsistent multi-page preview "
            "(SNAPSHOT_UNSTABLE, Section 67.1)"
        )

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
