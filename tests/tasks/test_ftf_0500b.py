#!/usr/bin/env python3
"""FTF-0500B -- S3/MinIO source validation + metadata preview (no-DB unit/
fault tests).

Runnable without any database. Drives a real local, plain-HTTP, path-style
S3-shaped fixture server that independently re-derives the expected AWS
Signature Version 4 `Authorization` header (reusing `sign_request`, the
same real signer under test) and rejects a request whose signature does
not match -- a genuine signed round trip, not a stub that accepts any
request. Serves a real `ListObjectVersions`-shaped XML response, including
real pagination (`IsTruncated`/`NextKeyMarker`/`NextVersionIdMarker`).

Covers this task's own negative_acceptance ("S3 ETag is not assumed SHA;
source changes, cross-tenant prefix and overly broad grants are denied")
as four independently-triggered real checks, plus a real destination-
denied policy check, a real wrong-credentials rejection, real pagination
across two real pages, and two disclosed fault injections (Law 1).

Run directly: python3 tests/tasks/test_ftf_0500b.py
"""

from __future__ import annotations

import atexit
import importlib
import ipaddress
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError  # noqa: E402

import app.datasets.s3_minio_source_validation_metadata_preview as s3_preview  # noqa: E402
from app.services.storage_semantics_client import (  # noqa: E402
    S3Credentials,
    S3Endpoint,
    sign_request,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local plain-HTTP path-style S3-shaped fixture ----------------------

_ACCESS_KEY = "AKIA_TEST_ACCESS_KEY"
_SECRET_KEY = "test-secret-key-do-not-use-in-production"
_CREDENTIALS = S3Credentials(access_key=_ACCESS_KEY, secret_key=_SECRET_KEY)
_WRONG_CREDENTIALS = S3Credentials(access_key=_ACCESS_KEY, secret_key="wrong-secret-key")
_REGION = "us-east-1"

_SINGLE_PART_ETAG = "9e107d9d372bb6826bd81d3542a419d6"  # 32 hex chars
_MULTIPART_ETAG = f"{_SINGLE_PART_ETAG}-3"  # never a valid hash of anything

REQUEST_LOG: list[str] = []

# bucket -> list of (query-marker-state, page) response specs, consumed by
# whichever handler function is currently installed for the test in
# progress (see `_install_pages` below).
_CURRENT_PAGES: list[bytes] = []
_PAGE_INDEX = {"value": 0}


def _version_entry(
    key: str, version_id: str, *, is_latest: bool, size: int, etag: str, deleted: bool = False
) -> str:
    tag = "DeleteMarker" if deleted else "Version"
    etag_xml = "" if deleted else f"<ETag>&quot;{etag}&quot;</ETag><Size>{size}</Size>"
    return (
        f"<{tag}><Key>{key}</Key><VersionId>{version_id}</VersionId>"
        f"<IsLatest>{'true' if is_latest else 'false'}</IsLatest>"
        f"<LastModified>2026-01-01T00:00:00.000Z</LastModified>{etag_xml}</{tag}>"
    )


def _version_entry_custom_version_id(
    key: str, version_id_xml: str, *, is_latest: bool, size: int, etag: str
) -> str:
    """Like `_version_entry`, but takes the raw `<VersionId>...</VersionId>`
    XML fragment (or `""` to omit the element entirely) instead of a plain
    version id string -- needed to construct the missing/blank/case/
    whitespace `VersionId` edge cases `_version_entry` cannot express."""
    return (
        f"<Version><Key>{key}</Key>{version_id_xml}"
        f"<IsLatest>{'true' if is_latest else 'false'}</IsLatest>"
        f"<LastModified>2026-01-01T00:00:00.000Z</LastModified>"
        f"<ETag>&quot;{etag}&quot;</ETag><Size>{size}</Size></Version>"
    )


def _list_versions_xml(
    *,
    entries_xml: str,
    is_truncated: bool = False,
    next_key_marker: str = "",
    next_version_id_marker: str = "",
) -> bytes:
    truncation_xml = ""
    if is_truncated:
        truncation_xml = (
            f"<NextKeyMarker>{next_key_marker}</NextKeyMarker>"
            f"<NextVersionIdMarker>{next_version_id_marker}</NextVersionIdMarker>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<Name>acme-bucket</Name>"
        f"<IsTruncated>{'true' if is_truncated else 'false'}</IsTruncated>"
        f"{truncation_xml}{entries_xml}"
        "</ListVersionsResult>"
    ).encode()


def _list_versions_xml_raw_is_truncated(*, entries_xml: str, is_truncated_xml: str) -> bytes:
    """Like `_list_versions_xml`, but takes the raw `<IsTruncated>...`
    XML fragment (or `""` to omit the element entirely) instead of a plain
    bool -- needed to construct the garbage/case/whitespace/missing
    `IsTruncated` edge cases `_list_versions_xml` cannot express."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<Name>acme-bucket</Name>"
        f"{is_truncated_xml}{entries_xml}"
        "</ListVersionsResult>"
    ).encode()


def _install_pages(pages: list[bytes]) -> None:
    global _CURRENT_PAGES
    _CURRENT_PAGES = pages
    _PAGE_INDEX["value"] = 0


def _expected_authorization(method: str, path: str, query: dict[str, str], amz_date: str) -> str:
    parsed = urlsplit(f"http://placeholder{path}")
    endpoint = S3Endpoint(scheme="http", host="127.0.0.1", port=_PORT, region=_REGION)
    now = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    signed = sign_request(
        method=method,
        endpoint=endpoint,
        credentials=_CREDENTIALS,
        canonical_uri=parsed.path,
        query=query,
        now=now,
    )
    return signed.headers["authorization"]


class _S3Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        REQUEST_LOG.append(self.path)
        parsed = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}

        amz_date = self.headers.get("x-amz-date", "")
        actual_auth = self.headers.get("authorization", "")
        expected_auth = (
            _expected_authorization(self.command, parsed.path, query, amz_date)
            if amz_date
            else None
        )
        if expected_auth is None or actual_auth != expected_auth:
            self._respond(403, b'{"Code":"SignatureDoesNotMatch"}')
            return

        if parsed.path != "/acme-bucket":
            self._respond(404, b'{"Code":"NoSuchBucket"}')
            return

        index = _PAGE_INDEX["value"]
        if index >= len(_CURRENT_PAGES):
            self._respond(500, b'{"Code":"UnexpectedExtraRequest"}')
            return
        body = _CURRENT_PAGES[index]
        _PAGE_INDEX["value"] += 1
        self._respond(200, body)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def _start_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _S3Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


_SERVER = _start_server()
_PORT = _SERVER.server_address[1]


def _endpoint() -> S3Endpoint:
    return S3Endpoint(scheme="http", host="127.0.0.1", port=_PORT, region=_REGION)


def _config(**overrides: object) -> s3_preview.S3MinioSourceConfig:
    fields: dict[str, object] = {
        "endpoint": _endpoint(),
        "credentials": _CREDENTIALS,
        "bucket": "acme-bucket",
        "tenant_key_prefix": "org-42/",
    }
    fields.update(overrides)
    return s3_preview.S3MinioSourceConfig(**fields)  # type: ignore[arg-type]


def _policy() -> ConnectionPolicy:
    return ConnectionPolicy(
        name="test-s3-minio-preview",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({_PORT}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


# -- Tests --------------------------------------------------------------


def test_happy_path_lists_and_previews_real_objects() -> None:
    entries = _version_entry(
        "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
    ) + _version_entry("org-42/b.jsonl", "v1", is_latest=True, size=200, etag=_MULTIPART_ETAG)
    _install_pages([_list_versions_xml(entries_xml=entries)])
    result = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    check(
        "FTF-0500B",
        "acceptance: bucket/prefix are the real resolved values",
        (result.bucket == "acme-bucket" and result.tenant_key_prefix == "org-42/"),
    )
    check(
        "FTF-0500B",
        "acceptance: both real objects are returned in the preview",
        len(result.objects) == 2,
    )
    check(
        "FTF-0500B",
        "acceptance: the listing is not falsely marked truncated when it is complete",
        result.truncated is False,
    )
    check(
        "FTF-0500B",
        "acceptance: an immutable manifest_hash is produced (real sha256 hex digest)",
        isinstance(result.manifest_hash, str) and len(result.manifest_hash) == 64,
    )
    check(
        "FTF-0500B",
        "a plain single-part ETag is disclosed as looking like a content hash (informational only)",
        result.objects[0].etag_looks_like_content_hash is True,
    )
    check(
        "FTF-0500B",
        "a multipart-shaped ETag is disclosed as NOT looking like a content hash",
        result.objects[1].etag_looks_like_content_hash is False,
    )


def test_manifest_hash_is_stable_for_an_identical_repeated_listing() -> None:
    entries = _version_entry(
        "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
    )
    _install_pages([_list_versions_xml(entries_xml=entries)])
    first = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    _install_pages([_list_versions_xml(entries_xml=entries)])
    second = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    check(
        "FTF-0500B",
        "acceptance: an identical real listing produces an identical immutable manifest_hash",
        first.manifest_hash == second.manifest_hash,
    )


def test_manifest_hash_changes_when_the_real_listing_differs() -> None:
    _install_pages(
        [
            _list_versions_xml(
                entries_xml=_version_entry(
                    "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
                )
            )
        ]
    )
    first = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    _install_pages(
        [
            _list_versions_xml(
                entries_xml=_version_entry(
                    "org-42/a.jsonl", "v1", is_latest=True, size=999, etag=_SINGLE_PART_ETAG
                )
            )
        ]
    )
    second = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    check(
        "FTF-0500B",
        "a genuinely different real listing (different Size) produces a different manifest_hash",
        first.manifest_hash != second.manifest_hash,
    )


def test_manifest_hash_changes_when_only_the_version_id_differs() -> None:
    """Same key, same size, same ETag -- only VersionId differs. The
    manifest must still change, or it is not really pinning an exact
    version (this task's own negative_acceptance)."""
    _install_pages(
        [
            _list_versions_xml(
                entries_xml=_version_entry(
                    "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
                )
            )
        ]
    )
    first = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    _install_pages(
        [
            _list_versions_xml(
                entries_xml=_version_entry(
                    "org-42/a.jsonl", "v2", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
                )
            )
        ]
    )
    second = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    check(
        "FTF-0500B",
        "negative_acceptance: an identical key/size/ETag listing whose VersionId alone differs "
        "produces a different manifest_hash -- the manifest is pinned to the exact version, not "
        "merely to key/size/ETag",
        first.manifest_hash != second.manifest_hash,
    )


def test_fault_injection_manifest_hash_is_load_bearing_on_version_id() -> None:
    """Dropping `version_id` from the `manifest_hash` input must make the
    test above genuinely fail, not merely look correct."""
    original = _MODULE_PATH.read_bytes()
    target = b'            "version_id": entry.version_id,\n'
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(target, b"", 1)
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)

        _install_pages(
            [
                _list_versions_xml(
                    entries_xml=_version_entry(
                        "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
                    )
                )
            ]
        )
        first = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        _install_pages(
            [
                _list_versions_xml(
                    entries_xml=_version_entry(
                        "org-42/a.jsonl", "v2", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
                    )
                )
            ]
        )
        second = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        check(
            "FTF-0500B",
            "fault injection: dropping version_id from the manifest_hash input genuinely lets a "
            "different-version listing produce an identical (unpinned) manifest_hash",
            first.manifest_hash == second.manifest_hash,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages(
            [
                _list_versions_xml(
                    entries_xml=_version_entry(
                        "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
                    )
                )
            ]
        )
        first = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        _install_pages(
            [
                _list_versions_xml(
                    entries_xml=_version_entry(
                        "org-42/a.jsonl", "v2", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
                    )
                )
            ]
        )
        second = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        check(
            "FTF-0500B",
            "after restoring the file, a VersionId-only difference genuinely changes the "
            "manifest_hash again",
            first.manifest_hash != second.manifest_hash,
        )


def test_versioning_disabled_source_is_refused() -> None:
    """A real S3/MinIO source that never had object versioning enabled
    reports the literal string VersionId `null` for every entry -- this
    task's own 'exact version manifest' cannot be made real against such a
    source, so it must be refused, not silently accepted."""
    entries = _version_entry(
        "org-42/a.jsonl", "null", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
    )
    _install_pages([_list_versions_xml(entries_xml=entries)])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    except s3_preview.VersioningDisabledError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a real literal VersionId of 'null' (versioning disabled or "
        "suspended on the source bucket) is refused",
        raised,
    )


def test_missing_or_blank_version_id_is_refused_as_versioning_disabled() -> None:
    """A real `ListObjectVersions` entry with no `<VersionId>` element at
    all, or an empty one, is exactly as unversioned as the literal `"null"`
    sentinel -- both must be refused, not silently accepted into the
    'exact version' manifest."""
    for label, version_id_xml in (
        ("omitted entirely", ""),
        ("present but empty", "<VersionId></VersionId>"),
    ):
        entries = _version_entry_custom_version_id(
            "org-42/a.jsonl", version_id_xml, is_latest=True, size=100, etag=_SINGLE_PART_ETAG
        )
        _install_pages([_list_versions_xml(entries_xml=entries)])
        raised = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.VersioningDisabledError:
            raised = True
        check(
            "FTF-0500B",
            f"negative_acceptance: a VersionId {label} is refused as versioning-disabled, "
            "not accepted into the manifest",
            raised,
        )


def test_version_id_case_and_whitespace_variants_of_null_are_refused() -> None:
    """The real sentinel is the literal `'null'`, but a non-compliant
    server (or one with trailing whitespace in its XML text node) must not
    slip past a bare `==` check -- refused case/whitespace-insensitively."""
    for variant in ("NULL", "Null", " null ", "\tnull\n"):
        entries = _version_entry_custom_version_id(
            "org-42/a.jsonl",
            f"<VersionId>{variant}</VersionId>",
            is_latest=True,
            size=100,
            etag=_SINGLE_PART_ETAG,
        )
        _install_pages([_list_versions_xml(entries_xml=entries)])
        raised = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.VersioningDisabledError:
            raised = True
        check(
            "FTF-0500B",
            f"negative_acceptance: a VersionId of {variant!r} (case/whitespace variant of the "
            "null sentinel) is refused",
            raised,
        )


def test_error_root_response_is_refused_not_treated_as_empty_listing() -> None:
    """A real S3/MinIO-compatible endpoint can return HTTP 200 with an
    `<Error>` root instead of a `<ListVersionsResult>` -- this must be
    refused, never silently parsed as a valid, empty, complete listing."""
    error_body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<Error><Code>InternalError</Code>"
        b"<Message>synthetic fault for FTF-0500B</Message></Error>"
    )
    _install_pages([error_body])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: an HTTP-200 response with an <Error> root is refused, never "
        "treated as a successful empty listing",
        raised,
    )


def test_stuck_pagination_without_advancing_marker_is_refused() -> None:
    """IsTruncated=true with no real advance in either pagination marker
    is a protocol defect this module must refuse outright -- never loop
    forever re-requesting the identical page, and never silently return
    duplicate copies of it as the bounded preview."""
    entries = _version_entry(
        "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
    )
    _install_pages(
        [
            _list_versions_xml(
                entries_xml=entries,
                is_truncated=True,
                next_key_marker="",
                next_version_id_marker="",
            )
        ]
    )
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(max_preview_records=20), policy=_policy()
        )
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: IsTruncated=true without an advancing pagination marker is "
        "refused, rather than looped forever or returned as duplicate preview rows",
        raised,
    )
    check(
        "FTF-0500B",
        "the stuck-pagination check fires immediately after the first page -- exactly one "
        "request is made, never an unbounded retry loop",
        len(REQUEST_LOG) - before == 1,
    )


def test_fault_injection_error_root_check_is_load_bearing_additive() -> None:
    """Additive-fault variant required by the 2026-10-04 relay DoD rule: a
    careless future edit that adds an early `return` for the `<Error>`
    branch (not merely a negated condition) must still be caught by this
    test."""
    original = _MODULE_PATH.read_bytes()
    target = b'    if root_tag == "Error":\n'
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    error_body = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<Error><Code>InternalError</Code>"
        b"<Message>synthetic fault for FTF-0500B</Message></Error>"
    )
    try:
        additive_fault = target + b"        return [], False, None, None\n"
        mutated = original.replace(target, additive_fault, 1)
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        _install_pages([error_body])
        no_longer_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
            no_longer_raises = True
        except s3_preview.SourceUnavailableError:
            no_longer_raises = False
        check(
            "FTF-0500B",
            "fault injection: an additive early-return bypass of the <Error>-root check "
            "genuinely lets an HTTP-200 <Error> response through as a fabricated, "
            "empty-but-successful listing",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages([error_body])
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.SourceUnavailableError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the <Error>-root check genuinely fires again",
            restored_raises,
        )


def test_fault_injection_marker_cycle_check_is_load_bearing() -> None:
    """Renamed from `..._stuck_pagination_check_...` for round 4: the
    single-previous-marker comparison was replaced by a seen-set of every
    marker pair requested in this listing, which also catches a longer
    A-to-B-to-A cycle (round-4 finding 1), not only an immediately-repeated
    page."""
    original = _MODULE_PATH.read_bytes()
    target = b"        if marker_pair in used_marker_pairs:\n"
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    entries = _version_entry(
        "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
    )
    stuck_page = _list_versions_xml(
        entries_xml=entries, is_truncated=True, next_key_marker="", next_version_id_marker=""
    )
    try:
        mutated = original.replace(
            target,
            b"        if False and marker_pair in used_marker_pairs:\n",
            1,
        )
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        # Bounded by the fixture's own fixed page list (not by the module):
        # with the check disabled, the connector keeps requesting a "next"
        # page that never exists, instead of stopping after page one.
        _install_pages([stuck_page, stuck_page, stuck_page])
        before = len(REQUEST_LOG)
        try:
            s3_preview.validate_and_preview_s3_minio_source(
                _config(max_preview_records=20), policy=_policy()
            )
        except (s3_preview.SourceUnavailableError, s3_preview.DuplicateObjectVersionError):
            # With the cycle check disabled, the connector re-requests the
            # identical stuck page; the still-active duplicate-(Key,
            # VersionId) check fires on the second page's repeated entry
            # before the fixture's own fixed page list runs out -- either
            # way, more than one real request is made, which is what this
            # test is proving.
            pass
        check(
            "FTF-0500B",
            "fault injection: disabling the marker-cycle check genuinely lets the connector "
            "attempt further requests past the first non-advancing page, instead of refusing "
            "immediately",
            len(REQUEST_LOG) - before > 1,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages([stuck_page])
        before = len(REQUEST_LOG)
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(
                _config(max_preview_records=20), policy=_policy()
            )
        except s3_preview.SourceUnavailableError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the marker-cycle check genuinely fires again, after "
            "exactly one request",
            restored_raises and len(REQUEST_LOG) - before == 1,
        )


def test_fault_injection_versioning_disabled_check_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = b"if not normalized_version_id or normalized_version_id == _UNVERSIONED_SENTINEL:"
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    entries = _version_entry(
        "org-42/a.jsonl", "null", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
    )
    try:
        mutated = original.replace(
            target,
            b"if False and (not normalized_version_id or "
            b"normalized_version_id == _UNVERSIONED_SENTINEL):",
            1,
        )
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        _install_pages([_list_versions_xml(entries_xml=entries)])
        no_longer_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
            no_longer_raises = True
        except s3_preview.VersioningDisabledError:
            no_longer_raises = False
        check(
            "FTF-0500B",
            "fault injection: disabling the versioning-disabled check genuinely lets an "
            "unversioned (literal 'null' VersionId) source through as a fabricated success",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages([_list_versions_xml(entries_xml=entries)])
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.VersioningDisabledError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the versioning-disabled check genuinely fires again",
            restored_raises,
        )


def test_real_pagination_across_two_pages() -> None:
    page1 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
        ),
        is_truncated=True,
        next_key_marker="org-42/a.jsonl",
        next_version_id_marker="v1",
    )
    page2 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/b.jsonl", "v1", is_latest=True, size=200, etag=_SINGLE_PART_ETAG
        )
    )
    _install_pages([page1, page2])
    result = s3_preview.validate_and_preview_s3_minio_source(
        _config(max_preview_records=20), policy=_policy()
    )
    check(
        "FTF-0500B",
        "real pagination: both pages are actually requested and both real objects returned",
        len(result.objects) == 2
        and {o.key for o in result.objects}
        == {
            "org-42/a.jsonl",
            "org-42/b.jsonl",
        },
    )
    check(
        "FTF-0500B",
        "real pagination: exactly two real HTTP requests were made",
        len(REQUEST_LOG) >= 2,
    )


def test_preview_bound_marks_result_truncated() -> None:
    entries = "".join(
        _version_entry(f"org-42/f{i}.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG)
        for i in range(5)
    )
    _install_pages([_list_versions_xml(entries_xml=entries)])
    result = s3_preview.validate_and_preview_s3_minio_source(
        _config(max_preview_records=3), policy=_policy()
    )
    check(
        "FTF-0500B",
        "acceptance: the preview is bounded to max_preview_records",
        len(result.objects) == 3,
    )
    check(
        "FTF-0500B",
        "acceptance: a listing with more real objects than the preview bound is disclosed as "
        "truncated, never silently presented as complete",
        result.truncated is True,
    )


def test_overly_broad_grant_fails_before_any_network_request() -> None:
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(tenant_key_prefix=""), policy=_policy()
        )
    except s3_preview.OverlyBroadGrantError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: an empty tenant_key_prefix (overly broad grant) fails",
        raised,
    )
    check(
        "FTF-0500B",
        "the overly-broad-grant check is applied before any network request is made",
        len(REQUEST_LOG) == before,
    )


def test_root_prefix_is_also_an_overly_broad_grant() -> None:
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(tenant_key_prefix="/"), policy=_policy()
        )
    except s3_preview.OverlyBroadGrantError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a bare root prefix ('/') is also refused as an overly broad grant",
        raised,
    )


def test_cross_tenant_prefix_is_denied_even_when_the_server_misbehaves() -> None:
    """A real check independent of the server's own honoring of the
    request's own `prefix` query parameter -- a non-compliant or
    compromised endpoint that returns a key outside the requested prefix
    must still be refused client-side, never trusted."""
    entries = _version_entry(
        "org-99/other-tenant.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
    )
    _install_pages([_list_versions_xml(entries_xml=entries)])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    except s3_preview.CrossTenantPrefixError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a real key outside the connection's own tenant_key_prefix is "
        "denied, even though the server actually returned it",
        raised,
    )


def test_cross_tenant_prefix_rejects_sibling_tenant_string_collision() -> None:
    entries = _version_entry("org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG)
    _install_pages([_list_versions_xml(entries_xml=entries)])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(tenant_key_prefix="org-4"), policy=_policy()
        )
    except s3_preview.CrossTenantPrefixError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a tenant-key-prefix string collision (org-4 vs org-42/...) fails "
        "at a real path-segment boundary, not a bare string-prefix match",
        raised,
    )


def test_source_changed_during_listing_is_denied() -> None:
    """Simulates a real concurrent overwrite between two real page
    fetches of the same listing: page 1 reports `a.jsonl` latest at `v1`,
    page 2 (a later, real, independent HTTP response) reports the same key
    latest at a different version `v2` -- the source genuinely changed
    mid-listing and must be refused (SNAPSHOT_UNSTABLE), never silently
    resolved."""
    page1 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
        ),
        is_truncated=True,
        next_key_marker="org-42/a.jsonl",
        next_version_id_marker="v1",
    )
    page2 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v2", is_latest=True, size=999, etag=_SINGLE_PART_ETAG
        )
    )
    _install_pages([page1, page2])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(max_preview_records=20), policy=_policy()
        )
    except s3_preview.SourceChangedDuringListingError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a real key whose latest version differs between two real pages "
        "of the same listing is denied (SNAPSHOT_UNSTABLE), never silently mixed",
        raised,
    )


def test_delete_marker_is_never_treated_as_a_conflicting_latest_version() -> None:
    """A delete marker sharing a key with a real prior Version entry must
    not itself be treated as a second, conflicting 'latest' claim -- only
    real (non-delete-marker) `IsLatest` entries participate in the
    change-detection check."""
    entries = _version_entry(
        "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
    ) + _version_entry("org-42/a.jsonl", "v2", is_latest=True, size=0, etag="", deleted=True)
    _install_pages([_list_versions_xml(entries_xml=entries)])
    result = s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    check(
        "FTF-0500B",
        "a delete marker sharing a key with a real Version entry does not falsely trigger "
        "SourceChangedDuringListingError",
        len(result.objects) == 2,
    )


def test_wrong_credentials_are_rejected() -> None:
    entries = _version_entry("org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG)
    _install_pages([_list_versions_xml(entries_xml=entries)])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(credentials=_WRONG_CREDENTIALS), policy=_policy()
        )
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "a real signature computed with the wrong secret key is genuinely rejected by the "
        "fixture's own independent signature re-derivation (proving this is a real signed "
        "request, not an unauthenticated fetch)",
        raised,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    entries = _version_entry("org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG)
    _install_pages([_list_versions_xml(entries_xml=entries)])
    denied = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(), policy=s3_preview.default_s3_minio_preview_policy()
        )
    except OutboundPolicyError:
        denied = True
    check(
        "FTF-0500B",
        "a destination not permitted by policy is denied by a real, live check (production "
        "default carries no private-address allowlist)",
        denied,
    )


# -- Fault injection (Law 1): the cross-tenant and source-changed checks are
# load-bearing, not merely present in source.

_MODULE_PATH = (
    ROOT / "apps" / "api" / "app" / "datasets" / "s3_minio_source_validation_metadata_preview.py"
)


def test_fault_injection_cross_tenant_prefix_check_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = b"if not _key_within_tenant_prefix(key, prefix):"
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    try:
        mutated = original.replace(
            target, b"if False and not _key_within_tenant_prefix(key, prefix):", 1
        )
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        entries = _version_entry(
            "org-99/other-tenant.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
        )
        _install_pages([_list_versions_xml(entries_xml=entries)])
        no_longer_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
            no_longer_raises = True
        except s3_preview.CrossTenantPrefixError:
            no_longer_raises = False
        check(
            "FTF-0500B",
            "fault injection: disabling the cross-tenant-prefix check genuinely lets a "
            "different tenant's real key through as a fabricated success",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        entries = _version_entry(
            "org-99/other-tenant.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
        )
        _install_pages([_list_versions_xml(entries_xml=entries)])
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.CrossTenantPrefixError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the cross-tenant-prefix check genuinely fires again",
            restored_raises,
        )


def test_fault_injection_source_changed_check_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = b"if previously_seen is not None and previously_seen != version_id:"
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    page1 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v1", is_latest=True, size=100, etag=_SINGLE_PART_ETAG
        ),
        is_truncated=True,
        next_key_marker="org-42/a.jsonl",
        next_version_id_marker="v1",
    )
    page2 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v2", is_latest=True, size=999, etag=_SINGLE_PART_ETAG
        )
    )
    try:
        mutated = original.replace(
            target,
            b"if False and previously_seen is not None and previously_seen != version_id:",
            1,
        )
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        _install_pages([page1, page2])
        no_longer_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(
                _config(max_preview_records=20), policy=_policy()
            )
            no_longer_raises = True
        except s3_preview.SourceChangedDuringListingError:
            no_longer_raises = False
        check(
            "FTF-0500B",
            "fault injection: disabling the source-changed-during-listing check genuinely "
            "lets a mid-listing mutation through as a fabricated success",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages([page1, page2])
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(
                _config(max_preview_records=20), policy=_policy()
            )
        except s3_preview.SourceChangedDuringListingError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the source-changed-during-listing check genuinely "
            "fires again",
            restored_raises,
        )


# -- Round-4 L2 findings (2026-10-04T17:20Z): marker-cycle/page-fetch-cap,
# strict IsTruncated parsing, required ListVersionsResult root, duplicate
# (Key, VersionId) pairs, and non-integer/negative Size. --------------------


def test_longer_marker_cycle_through_distinct_pages_is_refused() -> None:
    """Round-4 finding 1: not only an immediately-repeated page, but a
    longer A-to-B-to-A cycle through several distinct pages must also be
    refused -- the old single-previous-marker comparison this module used
    before round 4 would not have caught this."""
    page_a = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
        ),
        is_truncated=True,
        next_key_marker="org-42/b.jsonl",
        next_version_id_marker="vb",
    )
    page_b = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/b.jsonl", "vb", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
        ),
        is_truncated=True,
        next_key_marker="org-42/c.jsonl",
        next_version_id_marker="vc",
    )
    page_c = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/c.jsonl", "vc", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
        ),
        is_truncated=True,
        next_key_marker="org-42/b.jsonl",
        next_version_id_marker="vb",
    )
    _install_pages([page_a, page_b, page_c])
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(max_preview_records=20), policy=_policy()
        )
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a longer A-to-B-to-A pagination marker cycle through several "
        "distinct pages is refused, not only an immediately-repeated page",
        raised,
    )
    check(
        "FTF-0500B",
        "the cycle is detected before a fourth, redundant request is made -- exactly three "
        "real requests happen",
        len(REQUEST_LOG) - before == 3,
    )


def test_marker_cycle_with_empty_pages_is_refused() -> None:
    """Round-4 finding 1, empty-page variant: the same cycle, but every
    page reports zero entries -- the cycle check must not depend on any
    entries being present to fire."""
    page_a = _list_versions_xml(
        entries_xml="",
        is_truncated=True,
        next_key_marker="org-42/b",
        next_version_id_marker="vb",
    )
    page_b = _list_versions_xml(
        entries_xml="",
        is_truncated=True,
        next_key_marker="org-42/a",
        next_version_id_marker="va",
    )
    page_c = _list_versions_xml(
        entries_xml="",
        is_truncated=True,
        next_key_marker="org-42/b",
        next_version_id_marker="vb",
    )
    _install_pages([page_a, page_b, page_c])
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(max_preview_records=20), policy=_policy()
        )
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a pagination marker cycle made entirely of empty pages is "
        "still refused",
        raised,
    )
    check(
        "FTF-0500B",
        "the cycle is detected before a fourth, redundant request is made -- exactly three "
        "real requests happen (not merely a coincidental failure from running out of the "
        "fixture's own fixed page list)",
        len(REQUEST_LOG) - before == 3,
    )


def test_ever_advancing_pagination_hits_the_page_fetch_cap() -> None:
    """Round-4 finding 1, cap variant: markers that keep advancing and
    never repeat would defeat the cycle check alone -- an independent
    hard page-fetch cap must still refuse the listing rather than follow
    it forever."""
    pages = [
        _list_versions_xml(
            entries_xml="",
            is_truncated=True,
            next_key_marker=f"org-42/k{i}",
            next_version_id_marker=f"v{i}",
        )
        for i in range(10)
    ]
    _install_pages(pages)
    before = len(REQUEST_LOG)
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(max_preview_records=20, max_page_fetches=5), policy=_policy()
        )
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a listing with ever-advancing, never-repeating pagination "
        "markers is refused once it exceeds max_page_fetches, rather than followed forever",
        raised,
    )
    check(
        "FTF-0500B",
        "the page-fetch cap fires after exactly max_page_fetches real requests",
        len(REQUEST_LOG) - before == 5,
    )


def test_is_truncated_strict_values_are_each_correctly_interpreted() -> None:
    """Round-4 finding 2: `<IsTruncated>` must be parsed strictly --
    "TRUE"/"1"/" true " (case/whitespace-insensitive true) and
    "false"/"0" must each be read correctly, not merely accepted or
    rejected as a blanket default."""
    entry = _version_entry("org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG)
    for raw_value, expected in (
        ("TRUE", True),
        ("1", True),
        (" true ", True),
        ("false", False),
        ("0", False),
    ):
        _install_pages(
            [
                _list_versions_xml_raw_is_truncated(
                    entries_xml=entry,
                    is_truncated_xml=f"<IsTruncated>{raw_value}</IsTruncated>",
                )
            ]
        )
        result = s3_preview.validate_and_preview_s3_minio_source(
            _config(max_preview_records=1), policy=_policy()
        )
        check(
            "FTF-0500B",
            f"negative_acceptance: <IsTruncated>{raw_value!r}</IsTruncated> is strictly "
            f"interpreted as truncated={expected}",
            result.truncated is expected,
        )


def test_is_truncated_garbage_or_missing_value_is_refused() -> None:
    """Round-4 finding 2: anything other than "true"/"1"/"false"/"0"
    (case/whitespace-insensitive), including a missing element entirely,
    is refused rather than silently treated as "not truncated"."""
    entry = _version_entry("org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG)
    for is_truncated_xml in (
        "<IsTruncated>maybe</IsTruncated>",
        "<IsTruncated></IsTruncated>",
        "",
    ):
        _install_pages(
            [
                _list_versions_xml_raw_is_truncated(
                    entries_xml=entry, is_truncated_xml=is_truncated_xml
                )
            ]
        )
        raised = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(
                _config(max_preview_records=20), policy=_policy()
            )
        except s3_preview.SourceUnavailableError:
            raised = True
        check(
            "FTF-0500B",
            f"negative_acceptance: an unrecognized/missing <IsTruncated> value "
            f"({is_truncated_xml!r}) is refused, never silently treated as not truncated",
            raised,
        )


def test_unexpected_root_element_is_refused() -> None:
    """Round-4 finding 3: only a real <ListVersionsResult> root is ever
    treated as a listing -- any other root, not only a non-namespaced
    <Error>, must be refused the same way."""
    bodies = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<ns:Error xmlns:ns="urn:example"><Code>X</Code></ns:Error>',
        b'<?xml version="1.0" encoding="UTF-8"?><Foo><Error/></Foo>',
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"/>',
        b"<html><body>not xml-s3 at all</body></html>",
    )
    for body in bodies:
        _install_pages([body])
        raised = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.SourceUnavailableError:
            raised = True
        check(
            "FTF-0500B",
            f"negative_acceptance: an HTTP-200 response whose root is not a real "
            f"<ListVersionsResult> ({body!r}) is refused",
            raised,
        )


def test_duplicate_key_version_pair_within_one_page_is_refused() -> None:
    """Round-4 finding 4: the same (Key, VersionId) pair appearing twice
    within one page is refused."""
    entries = _version_entry(
        "org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
    ) + _version_entry("org-42/a.jsonl", "v1", is_latest=False, size=1, etag=_SINGLE_PART_ETAG)
    _install_pages([_list_versions_xml(entries_xml=entries)])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    except s3_preview.DuplicateObjectVersionError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: the same (Key, VersionId) pair appearing twice within one page "
        "is refused",
        raised,
    )


def test_duplicate_key_version_pair_across_pages_is_refused() -> None:
    """Round-4 finding 4: the same (Key, VersionId) pair reappearing on a
    later page of the same listing is refused."""
    page1 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
        ),
        is_truncated=True,
        next_key_marker="org-42/a.jsonl",
        next_version_id_marker="v1",
    )
    page2 = _list_versions_xml(
        entries_xml=_version_entry(
            "org-42/a.jsonl", "v1", is_latest=False, size=1, etag=_SINGLE_PART_ETAG
        )
    )
    _install_pages([page1, page2])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(
            _config(max_preview_records=20), policy=_policy()
        )
    except s3_preview.DuplicateObjectVersionError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: the same (Key, VersionId) pair reappearing on a later page of "
        "the same listing is refused",
        raised,
    )


def test_non_integer_size_is_refused() -> None:
    """Round-4 finding 5: a non-integer <Size> is refused with this
    module's own error class, never a bare uncategorized ValueError."""
    entries_xml = (
        "<Version><Key>org-42/a.jsonl</Key><VersionId>v1</VersionId>"
        "<IsLatest>true</IsLatest><LastModified>2026-01-01T00:00:00.000Z</LastModified>"
        f"<ETag>&quot;{_SINGLE_PART_ETAG}&quot;</ETag><Size>not-a-number</Size></Version>"
    )
    _install_pages([_list_versions_xml(entries_xml=entries_xml)])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a non-integer <Size> is refused with this module's own error class",
        raised,
    )


def test_negative_size_is_refused() -> None:
    """Round-4 finding 5: a negative <Size> is refused, never silently
    accepted into the manifest."""
    entries_xml = (
        "<Version><Key>org-42/a.jsonl</Key><VersionId>v1</VersionId>"
        "<IsLatest>true</IsLatest><LastModified>2026-01-01T00:00:00.000Z</LastModified>"
        f"<ETag>&quot;{_SINGLE_PART_ETAG}&quot;</ETag><Size>-5</Size></Version>"
    )
    _install_pages([_list_versions_xml(entries_xml=entries_xml)])
    raised = False
    try:
        s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
    except s3_preview.SourceUnavailableError:
        raised = True
    check(
        "FTF-0500B",
        "negative_acceptance: a negative <Size> is refused, never silently accepted into the "
        "manifest",
        raised,
    )


def test_fault_injection_duplicate_version_check_is_load_bearing_additive() -> None:
    """Additive-fault variant required by the 2026-10-04 relay DoD rule: a
    careless future edit that adds an early `continue` for the
    duplicate-pair branch (not merely a negated condition) must still be
    caught by this test."""
    original = _MODULE_PATH.read_bytes()
    target = b"            if key_version_pair in seen_key_version_pairs:\n"
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    entries = _version_entry(
        "org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG
    ) + _version_entry("org-42/a.jsonl", "v1", is_latest=False, size=1, etag=_SINGLE_PART_ETAG)
    try:
        additive_fault = target + b"                continue\n"
        mutated = original.replace(target, additive_fault, 1)
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        _install_pages([_list_versions_xml(entries_xml=entries)])
        no_longer_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
            no_longer_raises = True
        except s3_preview.DuplicateObjectVersionError:
            no_longer_raises = False
        check(
            "FTF-0500B",
            "fault injection: an additive early-continue bypass of the duplicate-pair check "
            "genuinely lets a repeated (Key, VersionId) pair through as a fabricated success",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages([_list_versions_xml(entries_xml=entries)])
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.DuplicateObjectVersionError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the duplicate-pair check genuinely fires again",
            restored_raises,
        )


def test_fault_injection_root_tag_required_check_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = b'    if root_tag != "ListVersionsResult":\n'
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    bogus_body = (
        b'<?xml version="1.0" encoding="UTF-8"?><Foo><IsTruncated>false</IsTruncated></Foo>'
    )
    try:
        mutated = original.replace(
            target, b'    if False and root_tag != "ListVersionsResult":\n', 1
        )
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        _install_pages([bogus_body])
        no_longer_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
            no_longer_raises = True
        except s3_preview.SourceUnavailableError:
            no_longer_raises = False
        check(
            "FTF-0500B",
            "fault injection: disabling the root-tag-required check genuinely lets an "
            "unexpected root element through as a fabricated, empty-but-successful listing",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages([bogus_body])
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(_config(), policy=_policy())
        except s3_preview.SourceUnavailableError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the root-tag-required check genuinely fires again",
            restored_raises,
        )


def test_fault_injection_is_truncated_strict_parsing_is_load_bearing() -> None:
    original = _MODULE_PATH.read_bytes()
    target = b'    if normalized in ("true", "1"):\n'
    check(
        "FTF-0500B",
        "fault injection target snippet is present in the real source",
        target in original,
    )
    entry = _version_entry("org-42/a.jsonl", "v1", is_latest=True, size=1, etag=_SINGLE_PART_ETAG)
    garbage_body = _list_versions_xml_raw_is_truncated(
        entries_xml=entry, is_truncated_xml="<IsTruncated>maybe</IsTruncated>"
    )
    try:
        mutated = original.replace(target, b'    if True or normalized in ("true", "1"):\n', 1)
        check("FTF-0500B", "fault injection actually changed the file", mutated != original)
        _MODULE_PATH.write_bytes(mutated)
        importlib.reload(s3_preview)
        _install_pages([garbage_body])
        no_longer_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(
                _config(max_preview_records=1), policy=_policy()
            )
            no_longer_raises = True
        except s3_preview.SourceUnavailableError:
            no_longer_raises = False
        check(
            "FTF-0500B",
            "fault injection: widening the strict IsTruncated check genuinely lets a garbage "
            "value through as a fabricated result instead of being refused",
            no_longer_raises,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        importlib.reload(s3_preview)
        check(
            "FTF-0500B",
            "the module file is restored byte-identical after fault injection",
            _MODULE_PATH.read_bytes() == original,
        )
        _install_pages([garbage_body])
        restored_raises = False
        try:
            s3_preview.validate_and_preview_s3_minio_source(
                _config(max_preview_records=1), policy=_policy()
            )
        except s3_preview.SourceUnavailableError:
            restored_raises = True
        check(
            "FTF-0500B",
            "after restoring the file, the strict IsTruncated check genuinely fires again",
            restored_raises,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0500B S3/MinIO source validation + metadata preview: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0500B S3/MinIO source validation + metadata preview: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
