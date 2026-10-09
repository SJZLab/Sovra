#!/usr/bin/env python3
"""FTF-0501 -- Direct object-storage upload (presigned multipart + finalize
verification). No-DB, no-container suite; the live SeaweedFS evidence is
`tests/integration/test_ftf_0501_direct_upload_s3.py` (CI job
`direct-upload-s3-integration`).

Acceptance: "Use direct short-lived private multipart upload with finalize
byte/hash/type checks and resumable UI." Negative: "Wrong object key,
oversize/expired grant and partial upload cannot create accepted Dataset
version."

Without any container this suite proves, against an in-process S3 server that
speaks the multipart protocol and verifies presigned signatures:

P  SigV4 query presigning: the AWS documentation vector, the 5-minute ceiling,
   and that method / key / part / content-length are bound into the signature;
N  the part plan and the content sniffing table;
E  the storage settings from the environment;
G  the part grants (lifetime, session expiry, bounds) and a real PUT with them;
V  finalize verification: every accepted path and every refusal (wrong hash,
   wrong size, oversize / extra / missing part, wrong type, wrong key, expired,
   storage outage), with the object deleted on every rejection;
R  the five routes on a real uvicorn server over the real store (SQLite):
   roles, held-out hiding, bodies, error codes, and that no Dataset exists
   after any refusal;
C  the interface contract and the app wiring.

Run directly: python3 tests/tasks/test_ftf_0501.py
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth.rbac_middleware import AuthenticatedPrincipal, Role  # noqa: E402
from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.datasets import direct_object_storage_upload_routes as routes  # noqa: E402
from app.datasets import upload_sweeper as sweeper  # noqa: E402
from app.datasets.dataset_source_connection_crud import (  # noqa: E402
    DatasetSourceAuthorizationError,
    DatasetSourceConnectionNotFoundError,
)
from app.services import storage_semantics_client as s3  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0501"
MIB = 1024**2
GIB = 1024**3
MAIN_PATH = ROOT / "apps" / "api" / "app" / "main.py"
INTERFACE_PATH = ROOT / "packages" / "contracts" / "openapi" / "05-interface.yaml"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(fn: Any, error: type[BaseException]) -> bool:
    try:
        fn()
    except error:
        return True
    except Exception:  # noqa: BLE001 - a different error is not the expected refusal
        return False
    return False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl(size: int) -> bytes:
    """`size` bytes of valid-looking JSONL (every line starts with `{`)."""
    line = b'{"k":"' + b"x" * 1015 + b'"}\n'  # 1024 bytes
    body = (line * (size // len(line) + 1))[:size]
    return body


# -- the in-process S3 server ----------------------------------------------------


class FakeS3:
    """A small multipart-capable S3 server. It verifies presigned URLs by
    recomputing the signature from the request itself (with the same SigV4
    code the product uses), and can be told to fail an operation."""

    BUCKET = "uploads"
    ACCESS = "AKIDTESTONLY"
    SECRET = "test-only-secret-key"

    def __init__(self) -> None:
        self.uploads: dict[str, dict[str, Any]] = {}
        self.objects: dict[str, dict[str, Any]] = {}
        self.fail: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.head_lies_about_size: int | None = None
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                if "Content-Length" not in (headers or {}):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _error(self, status: int, code: str) -> None:
                xml = f"<Error><Code>{code}</Code><Message>{code}</Message></Error>"
                self._send(status, xml.encode(), {"Content-Type": "application/xml"})

            def _handle(self) -> None:
                parts = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
                path = parts.path
                prefix = f"/{fake.BUCKET}"
                key = path[len(prefix) + 1 :] if path.startswith(prefix + "/") else ""
                method = self.command
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                op = fake.operation(method, key, query)
                fake.calls.append((op, key))
                if op in fake.fail:
                    return self._error(500, "InternalError")
                if op == "put_part":
                    problem = fake.verify_presigned(method, path, query, self.headers)
                    if problem is not None:
                        return self._error(403, problem)
                    return fake.put_part(self, key, query, body)
                return getattr(fake, "op_" + op)(self, key, query, body)

            do_GET = do_PUT = do_POST = do_DELETE = do_HEAD = _handle  # noqa: N815

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )

    def __enter__(self) -> FakeS3:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def settings(self) -> dos.UploadStorageSettings:
        return dos.UploadStorageSettings(
            endpoint=s3.S3Endpoint("http", "127.0.0.1", self.port),
            credentials=s3.S3Credentials(self.ACCESS, self.SECRET),
            bucket=self.BUCKET,
        )

    @staticmethod
    def operation(method: str, key: str, query: dict[str, str]) -> str:
        if method == "POST" and "uploads" in query:
            return "create"
        if method == "GET" and not key and "uploads" in query:
            return "list_uploads"
        if method == "PUT" and "partNumber" in query:
            return "put_part"
        if method == "GET" and "uploadId" in query:
            return "list_parts"
        if method == "POST" and "uploadId" in query:
            return "complete"
        if method == "DELETE" and "uploadId" in query:
            return "abort"
        if method == "DELETE":
            return "delete"
        if method == "HEAD":
            return "head"
        return "get"

    def verify_presigned(
        self, method: str, path: str, query: dict[str, str], headers: Any
    ) -> str | None:
        query = dict(query)
        signature = query.pop("X-Amz-Signature", None)
        if signature is None:
            return "AccessDenied"
        try:
            issued = datetime.strptime(query["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            lifetime = int(query["X-Amz-Expires"])
            signed = query["X-Amz-SignedHeaders"].split(";")
        except (KeyError, ValueError):
            return "AccessDenied"
        if datetime.now(UTC) > issued + timedelta(seconds=lifetime):
            return "ExpiredToken"
        plain = {k: v for k, v in query.items() if not k.startswith("X-Amz-")}
        extra = {name: headers.get(name) or "" for name in signed if name != "host"}
        expected = s3.presign_url(
            method=method,
            endpoint=s3.S3Endpoint("http", "127.0.0.1", self.port),
            credentials=s3.S3Credentials(self.ACCESS, self.SECRET),
            canonical_uri=path,
            query=plain,
            signed_headers=extra,
            expires_seconds=lifetime,
            now=issued,
            max_expires_seconds=604800,
        )
        return (
            None if expected.endswith("X-Amz-Signature=" + signature) else "SignatureDoesNotMatch"
        )

    # -- operations -----------------------------------------------------------

    def op_create(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        upload_id = uuid.uuid4().hex
        self.uploads[upload_id] = {
            "key": key,
            "content_type": h.headers.get("Content-Type", ""),
            "parts": {},
            "initiated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        xml = (
            "<InitiateMultipartUploadResult>"
            f"<UploadId>{upload_id}</UploadId></InitiateMultipartUploadResult>"
        )
        h._send(200, xml.encode(), {"Content-Type": "application/xml"})

    def op_list_uploads(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        prefix = query.get("prefix", "")
        items = "".join(
            f"<Upload><Key>{u['key']}</Key><UploadId>{uid}</UploadId>"
            + (f"<Initiated>{u['initiated']}</Initiated>" if u.get("initiated") else "")
            + "</Upload>"
            for uid, u in self.uploads.items()
            if u["key"].startswith(prefix)
        )
        h._send(200, f"<ListMultipartUploadsResult>{items}</ListMultipartUploadsResult>".encode())

    def put_part(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        upload = self.uploads.get(query.get("uploadId", ""))
        if upload is None or upload["key"] != key:
            return h._error(404, "NoSuchUpload")
        number = int(query["partNumber"])
        upload["parts"][number] = body
        h._send(200, b"", {"ETag": '"' + hashlib.md5(body).hexdigest() + '"'})  # noqa: S324

    def op_list_parts(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        upload = self.uploads.get(query["uploadId"])
        if upload is None or upload["key"] != key:
            return h._error(404, "NoSuchUpload")
        items = "".join(
            f'<Part><PartNumber>{n}</PartNumber><ETag>"{hashlib.md5(data).hexdigest()}"</ETag>'  # noqa: S324
            f"<Size>{len(data)}</Size></Part>"
            for n, data in sorted(upload["parts"].items())
        )
        xml = f"<ListPartsResult>{items}<IsTruncated>false</IsTruncated></ListPartsResult>"
        h._send(200, xml.encode())

    def op_complete(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        upload = self.uploads.get(query["uploadId"])
        if upload is None or upload["key"] != key:
            return h._error(404, "NoSuchUpload")
        root = ET.fromstring(body)  # noqa: S314
        for element in root.iter():  # the request is namespaced; compare local names
            element.tag = element.tag.split("}", 1)[-1]
        wanted = [
            (int(p.findtext("PartNumber") or 0), (p.findtext("ETag") or "").strip('"'))
            for p in root.iter("Part")
        ]
        if not wanted:
            return h._error(400, "MalformedXML")
        data = b""
        for number, etag in sorted(wanted):
            part = upload["parts"].get(number)
            if part is None or hashlib.md5(part).hexdigest() != etag:  # noqa: S324
                return h._error(400, "InvalidPart")
            data += part
        self.objects[key] = {"body": data, "content_type": upload["content_type"]}
        del self.uploads[query["uploadId"]]
        h._send(
            200,
            b'<CompleteMultipartUploadResult><ETag>"done"</ETag></CompleteMultipartUploadResult>',
        )

    def op_abort(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        if self.uploads.pop(query["uploadId"], None) is None:
            return h._error(404, "NoSuchUpload")
        h._send(204)

    def op_delete(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        self.objects.pop(key, None)
        h._send(204)

    def op_head(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        obj = self.objects.get(key)
        if obj is None:
            return h._error(404, "NoSuchKey")
        size = (
            self.head_lies_about_size if self.head_lies_about_size is not None else len(obj["body"])
        )
        h._send(200, b"", {"Content-Length": str(size), "Content-Type": obj["content_type"]})

    def op_get(self, h: Any, key: str, query: dict[str, str], body: bytes) -> None:
        obj = self.objects.get(key)
        if obj is None:
            return h._error(404, "NoSuchKey")
        spec = h.headers.get("Range")
        if spec:
            first_text, _, last_text = spec.removeprefix("bytes=").partition("-")
            first, last = int(first_text), min(int(last_text), len(obj["body"]) - 1)
            return h._send(
                206, obj["body"][first : last + 1], {"Content-Type": obj["content_type"]}
            )
        h._send(200, obj["body"], {"Content-Type": obj["content_type"]})

    # -- test helpers -----------------------------------------------------------

    def inject_part(self, upload_id: str, number: int, data: bytes) -> None:
        self.uploads[upload_id]["parts"][number] = data

    def reset_calls(self) -> None:
        self.calls.clear()

    def called(self, op: str) -> bool:
        return any(name == op for name, _ in self.calls)


@contextlib.contextmanager
def _small_parts():
    """Parts of 5 MiB (S3's minimum) so a 12 MiB object has three parts."""
    saved = dos.DEFAULT_PART_BYTES
    dos.DEFAULT_PART_BYTES = dos.MIN_PART_BYTES
    try:
        yield
    finally:
        dos.DEFAULT_PART_BYTES = saved


_FAR = datetime(2099, 1, 1, tzinfo=UTC)


def _target(
    body: bytes,
    *,
    key: str | None = None,
    content_type: str = "application/jsonl",
    sha: str | None = None,
    size: int | None = None,
    expires_at: datetime | None = None,
) -> dos.UploadTarget:
    return dos.UploadTarget(
        object_key=key
        or f"tenants/{uuid.uuid4()}/projects/{uuid.uuid4()}/uploads/{uuid.uuid4()}/source",
        declared_size_bytes=len(body) if size is None else size,
        declared_sha256=sha or _sha(body),
        declared_content_type=content_type,
        expires_at=expires_at or _FAR,
    )


def _put(url: str, data: bytes) -> int:
    request = urllib.request.Request(url, data=data, method="PUT")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _open_and_upload(
    fake: FakeS3, target: dos.UploadTarget, body: bytes, *, only: list[int] | None = None
) -> str:
    """Opens the multipart upload and PUTs the parts through real presigned URLs."""
    settings = fake.settings()
    upload_id = dos.start_multipart(settings.client(), settings, target)
    plan = dos.plan_parts(target.declared_size_bytes)
    numbers = only if only is not None else list(range(1, plan.part_count + 1))
    grants = dos.presign_part_urls(settings, target, upload_id, numbers)
    for grant in grants:
        start = (grant.part_number - 1) * plan.part_size_bytes
        status = _put(grant.url, body[start : start + grant.content_length])
        if status != 200:
            raise AssertionError(f"part {grant.part_number} PUT answered {status}")
    return upload_id


def _verify(fake: FakeS3, target: dos.UploadTarget, **kw: Any) -> dos.VerifiedObject:
    settings = fake.settings()
    return dos.verify_and_complete(settings.client(), settings, target, **kw)


def _outcome(fake: FakeS3, target: dos.UploadTarget, **kw: Any) -> Any:
    try:
        return _verify(fake, target, **kw)
    except Exception as exc:  # noqa: BLE001 - the type is the result under test
        return exc


# -- P: presigning ---------------------------------------------------------------

AWS_DOC_ACCESS = "AKIAIOSFODNN7EXAMPLE"
AWS_DOC_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_DOC_SIGNATURE = "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
_EP = s3.S3Endpoint("http", "storage.internal", 9000)
_CR = s3.S3Credentials("AK", "SK")
# Anchored to the real clock when the file loads, never to a calendar date: the
# in-process server checks presigned URLs against the real clock, and the
# product refuses to presign for a session whose expiry has already passed.
# `_T0` is one hour ahead so `expires_at=_T0` is still in the future when a test
# presigns with the real clock; the tests that use it only compare it to other
# values derived from it.
_T0 = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)


def _presign(**over: Any) -> str:
    args: dict[str, Any] = {
        "method": "PUT",
        "endpoint": _EP,
        "credentials": _CR,
        "canonical_uri": "/bucket/tenants/a/source",
        "expires_seconds": 300,
        "query": {"partNumber": "1", "uploadId": "u1"},
        "signed_headers": {"content-length": "100"},
        "now": _T0,
    }
    args.update(over)
    return s3.presign_url(**args)


def _signature(url: str) -> str:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["X-Amz-Signature"]


def test_p1_presign_matches_the_aws_published_vector() -> None:
    url = s3.presign_url(
        method="GET",
        endpoint=s3.S3Endpoint("https", "examplebucket.s3.amazonaws.com", 443),
        credentials=s3.S3Credentials(AWS_DOC_ACCESS, AWS_DOC_SECRET),
        canonical_uri="/test.txt",
        expires_seconds=86400,
        now=datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC),
        max_expires_seconds=86400,
    )
    check(
        f"{CID}-P1",
        "presigned URL carries the AWS documentation signature",
        AWS_DOC_SIGNATURE in url,
    )
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    check(
        f"{CID}-P1",
        "algorithm / expires / signed headers match the vector",
        query["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
        and query["X-Amz-Expires"] == "86400"
        and query["X-Amz-SignedHeaders"] == "host",
    )


def test_p2_presign_lifetime_is_capped_at_five_minutes() -> None:
    check(f"{CID}-P2", "the ceiling constant is 300 s", s3.PRESIGN_MAX_EXPIRES_SECONDS == 300)
    check(f"{CID}-P2", "300 s is accepted", "X-Amz-Expires=300" in _presign(expires_seconds=300))
    check(f"{CID}-P2", "1 s is accepted", "X-Amz-Expires=1&" in _presign(expires_seconds=1))
    for bad in (0, -1, 301, 3600, True, "300", 300.0, None):
        check(
            f"{CID}-P2",
            f"expires_seconds={bad!r} is refused",
            _raises(lambda bad=bad: _presign(expires_seconds=bad), ValueError),
        )
    check(
        f"{CID}-P2",
        "the ceiling itself cannot be raised past 7 days",
        _raises(lambda: _presign(expires_seconds=700000, max_expires_seconds=700000), ValueError),
    )
    check(
        f"{CID}-P2",
        "a caller cannot raise the default ceiling by omission",
        _raises(lambda: _presign(expires_seconds=600), ValueError),
    )


def test_p3_presign_binds_method_key_part_upload_and_length() -> None:
    base = _signature(_presign())
    variants = {
        "method": _presign(method="GET"),
        "key": _presign(canonical_uri="/bucket/tenants/b/source"),
        "bucket": _presign(canonical_uri="/other/tenants/a/source"),
        "part number": _presign(query={"partNumber": "2", "uploadId": "u1"}),
        "upload id": _presign(query={"partNumber": "1", "uploadId": "u2"}),
        "content length": _presign(signed_headers={"content-length": "101"}),
        "expiry": _presign(expires_seconds=299),
        "time": _presign(now=_T0 + timedelta(seconds=1)),
        "secret": _presign(credentials=s3.S3Credentials("AK", "SK2")),
        "host": _presign(endpoint=s3.S3Endpoint("http", "storage.internal", 9001)),
        "region": _presign(endpoint=s3.S3Endpoint("http", "storage.internal", 9000, "eu-west-1")),
    }
    for what, url in variants.items():
        check(f"{CID}-P3", f"changing the {what} changes the signature", _signature(url) != base)
    check(f"{CID}-P3", "the same inputs sign identically", _signature(_presign()) == base)
    url = _presign()
    check(
        f"{CID}-P3",
        "the content-length header is a signed header",
        "X-Amz-SignedHeaders=content-length%3Bhost" in url,
    )
    check(
        f"{CID}-P3",
        "the URL names the endpoint, key and part",
        url.startswith("http://storage.internal:9000/bucket/tenants/a/source?")
        and "partNumber=1" in url
        and "uploadId=u1" in url,
    )


def test_p4_presign_uses_the_real_clock_by_default() -> None:
    before = datetime.now(UTC).replace(microsecond=0)
    url = _presign(now=None)
    stamp = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["X-Amz-Date"]
    issued = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    check(
        f"{CID}-P4", "an omitted clock means now", before <= issued <= before + timedelta(seconds=5)
    )


# -- N: plan and sniffing ----------------------------------------------------------


def test_n1_part_plan() -> None:
    step = dos.DEFAULT_PART_BYTES
    check(f"{CID}-N1", "default part is 64 MiB", step == 64 * MIB)
    cases = [
        (1, 1, 1),
        (step - 1, 1, step - 1),
        (step, 1, step),
        (step + 1, 2, 1),
        (2 * step, 2, step),
        (10 * GIB, 160, step),
    ]
    for size, count, last in cases:
        plan = dos.plan_parts(size)
        check(
            f"{CID}-N1",
            f"size {size}: {count} parts, last {last}",
            (plan.part_count, plan.last_part_size_bytes, plan.part_size_bytes)
            == (count, last, step),
        )
        total = sum(plan.expected_size(n) for n in range(1, plan.part_count + 1))
        check(f"{CID}-N1", f"size {size}: parts add up to the declared size", total == size)
    plan = dos.plan_parts(12 * MIB, part_size_bytes=5 * MIB)
    check(
        f"{CID}-N1",
        "12 MiB in 5 MiB parts is 5 + 5 + 2",
        [plan.expected_size(n) for n in (1, 2, 3)] == [5 * MIB, 5 * MIB, 2 * MIB],
    )
    for bad in (0, -1, True, "5", 1.5, None):
        check(
            f"{CID}-N1",
            f"declared size {bad!r} is refused",
            _raises(lambda bad=bad: dos.plan_parts(bad), ValueError),
        )
    for bad in (5 * MIB - 1, 0, True, "x"):
        check(
            f"{CID}-N1",
            f"part size {bad!r} is refused",
            _raises(lambda bad=bad: dos.plan_parts(MIB, part_size_bytes=bad), ValueError),
        )
    check(
        f"{CID}-N1",
        "exactly 10000 parts is allowed",
        dos.plan_parts(10000 * 5 * MIB, part_size_bytes=5 * MIB).part_count == 10000,
    )
    check(
        f"{CID}-N1",
        "10001 parts is refused",
        _raises(lambda: dos.plan_parts(10000 * 5 * MIB + 1, part_size_bytes=5 * MIB), ValueError),
    )
    for n in (0, 4, -1):
        check(
            f"{CID}-N1",
            f"part number {n} is outside a 3-part plan",
            _raises(
                lambda n=n: dos.plan_parts(12 * MIB, part_size_bytes=5 * MIB).expected_size(n),
                ValueError,
            ),
        )


def test_n2_normalize_content_type() -> None:
    n = dos.normalize_content_type
    check(f"{CID}-N2", "parameters are ignored", n("text/csv; charset=utf-8") == "text/csv")
    check(f"{CID}-N2", "case is ignored", n("Application/JSON") == "application/json")
    for ok in ("application/jsonl", "application/x-ndjson", dos.XLSX_CONTENT_TYPE):
        check(f"{CID}-N2", f"{ok} is supported", n(ok) == ok)
    for bad in (
        "application/pdf",
        "text/html",
        "application/zip",
        "",
        "text/csv/x",
        None,
        5,
        b"text/csv",
    ):
        check(f"{CID}-N2", f"{bad!r} is not supported", n(bad) is None)


def test_n3_content_sniffing() -> None:
    sniff = dos.sniff_content
    ok = None
    bad = "content_type_mismatch"
    table = [
        ("application/jsonl", b'{"a":1}\n{"a":2}\n', ok),
        ("application/x-ndjson", b'  \n{"a":1}\n', ok),
        ("application/jsonl", b'\xef\xbb\xbf{"a":1}\n', ok),
        ("application/jsonl", b"[1,2]\n", bad),
        ("application/jsonl", b"a,b\n1,2\n", bad),
        ("application/jsonl", b"", bad),
        ("application/json", b'[{"a":1}]', ok),
        ("application/json", b'{"a":1}', ok),
        ("application/json", b"a,b", bad),
        ("text/csv", b"a,b\n1,2\n", ok),
        ("text/csv; charset=utf-8", b"a,b\n1,2\n", ok),
        ("text/csv", b'{"a":1}\n', bad),
        ("text/csv", b"[1]\n", bad),
        ("text/csv", b"a,b\n\x00\x01", bad),
        ("text/csv", b"\xff\xfe\x00a", bad),
        ("text/csv", b"PK\x03\x04a,b", bad),
        ("text/csv", b"%PDF-1.4", bad),
        ("text/csv", b"\x1f\x8b\x08", bad),
        ("text/csv", b"\x7fELFa", bad),
        ("text/csv", b"\x89PNG", bad),
        ("text/csv", b"", bad),
        (dos.XLSX_CONTENT_TYPE, b"PK\x03\x04rest", ok),
        (dos.XLSX_CONTENT_TYPE, b"a,b\n1,2\n", bad),
        (dos.XLSX_CONTENT_TYPE, b"", bad),
        ("application/pdf", b"%PDF-1.4", "unsupported_content_type"),
        ("text/html", b"<html>", "unsupported_content_type"),
    ]
    for declared, head, expected in table:
        got = sniff(declared, head, complete=True)
        check(f"{CID}-N3", f"{declared} / {head[:12]!r} -> {expected}", got == expected)
    truncated = '{"é":1}'.encode()[:-3]
    check(
        f"{CID}-N3",
        "a UTF-8 sequence cut at the sniff window is not an error",
        sniff("application/jsonl", truncated + b"\xc3", complete=False) is None,
    )
    check(
        f"{CID}-N3",
        "the same cut in a complete object is an error",
        sniff("application/jsonl", truncated + b"\xc3", complete=True) == bad,
    )


# -- E: settings -------------------------------------------------------------------

_ENV = {
    "SOVRA_UPLOAD_S3_ENDPOINT": "https://s3.internal.example:9443",
    "SOVRA_UPLOAD_S3_BUCKET": "sovra-uploads",
    "SOVRA_UPLOAD_S3_ACCESS_KEY": "AKID",
    "SOVRA_UPLOAD_S3_SECRET_KEY": "SECRET",
}


def test_e1_settings_from_env() -> None:
    check(
        f"{CID}-E1",
        "no variables -> None (not configured)",
        dos.UploadStorageSettings.from_env({}) is None,
    )
    check(
        f"{CID}-E1",
        "blank variables -> None",
        dos.UploadStorageSettings.from_env({k: "  " for k in _ENV}) is None,
    )
    got = dos.UploadStorageSettings.from_env(_ENV)
    check(
        f"{CID}-E1",
        "a full environment parses",
        got is not None
        and got.bucket == "sovra-uploads"
        and got.endpoint.host == "s3.internal.example"
        and got.endpoint.port == 9443
        and got.endpoint.scheme == "https"
        and got.endpoint.region == "us-east-1"
        and got.credentials.access_key == "AKID"
        and got.credentials.secret_key == "SECRET",
    )
    region = dos.UploadStorageSettings.from_env({**_ENV, "SOVRA_UPLOAD_S3_REGION": "eu-west-1"})
    check(
        f"{CID}-E1",
        "the region is read",
        region is not None and region.endpoint.region == "eu-west-1",
    )
    plain = dos.UploadStorageSettings.from_env(
        {**_ENV, "SOVRA_UPLOAD_S3_ENDPOINT": "http://s3.example"}
    )
    check(
        f"{CID}-E1",
        "the scheme's default port is used",
        plain is not None and plain.endpoint.port == 80,
    )
    secure = dos.UploadStorageSettings.from_env(
        {**_ENV, "SOVRA_UPLOAD_S3_ENDPOINT": "https://s3.example"}
    )
    check(f"{CID}-E1", "https defaults to 443", secure is not None and secure.endpoint.port == 443)
    for name in _ENV:
        partial = {k: v for k, v in _ENV.items() if k != name}
        check(
            f"{CID}-E1",
            f"missing {name} is a configuration error, not 'unconfigured'",
            _raises(
                lambda partial=partial: dos.UploadStorageSettings.from_env(partial), ValueError
            ),
        )
    for bad in ("ftp://s3.example", "s3.example:9000", "http://", "file:///etc"):
        check(
            f"{CID}-E1",
            f"endpoint {bad!r} is refused",
            _raises(
                lambda bad=bad: dos.UploadStorageSettings.from_env(
                    {**_ENV, "SOVRA_UPLOAD_S3_ENDPOINT": bad}
                ),
                ValueError,
            ),
        )
    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        check(
            f"{CID}-E1",
            "the process environment is the default source",
            dos.UploadStorageSettings.from_env() is not None,
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# -- G: grants ---------------------------------------------------------------------


def test_g1_grants_lifetime_and_bounds() -> None:
    with FakeS3() as fake, _small_parts():
        settings = fake.settings()
        body = _jsonl(12 * MIB)
        target = _target(body)
        uid = dos.start_multipart(settings.client(), settings, target)
        grants = dos.presign_part_urls(settings, target, uid, [3, 1], now=_T0)
        check(
            f"{CID}-G1",
            "one grant per requested part, in request order",
            [g.part_number for g in grants] == [3, 1],
        )
        check(
            f"{CID}-G1",
            "lengths follow the plan (last part is the remainder)",
            [g.content_length for g in grants] == [2 * MIB, 5 * MIB],
        )
        check(f"{CID}-G1", "every grant is a PUT", all(g.method == "PUT" for g in grants))
        check(
            f"{CID}-G1",
            "a long session is capped at five minutes",
            all(g.expires_at == _T0 + timedelta(seconds=300) for g in grants)
            and all("X-Amz-Expires=300" in g.url for g in grants),
        )
        for g in grants:
            check(
                f"{CID}-G1",
                f"part {g.part_number} URL binds its key, number and upload id",
                f"/{fake.BUCKET}/{target.object_key}?" in g.url
                and f"partNumber={g.part_number}" in g.url
                and f"uploadId={uid}" in g.url
                and "content-length%3Bhost" in g.url,
            )

        short = _target(body, expires_at=_T0 + timedelta(seconds=120))
        grants = dos.presign_part_urls(settings, short, uid, [1], now=_T0)
        check(
            f"{CID}-G1",
            "a session ending in 120 s gets a 120 s grant",
            "X-Amz-Expires=120" in grants[0].url
            and grants[0].expires_at == _T0 + timedelta(seconds=120),
        )
        last = dos.presign_part_urls(
            settings, _target(body, expires_at=_T0 + timedelta(seconds=1)), uid, [1], now=_T0
        )
        check(
            f"{CID}-G1",
            "one second left still issues a 1 s grant",
            "X-Amz-Expires=1&" in last[0].url,
        )
        for left in (0, -1, -3600):
            check(
                f"{CID}-G1",
                f"{left} s left -> UploadExpired, nothing issued",
                _raises(
                    lambda left=left: dos.presign_part_urls(
                        settings,
                        _target(body, expires_at=_T0 + timedelta(seconds=left)),
                        uid,
                        [1],
                        now=_T0,
                    ),
                    dos.UploadExpired,
                ),
            )

        for bad in (
            [],
            [0],
            [4],
            [-1],
            [1, 1],
            [True],
            ["1"],
            [1.0],
            None,
            "1",
            (1,),
            list(range(1, 102)),
        ):
            check(
                f"{CID}-G1",
                f"part_numbers {str(bad)[:20]!r} is refused",
                _raises(
                    lambda bad=bad: dos.presign_part_urls(settings, target, uid, bad), ValueError
                ),
            )
        big = _target(_jsonl(1), size=10 * GIB)
        many = dos.presign_part_urls(settings, big, uid, list(range(1, 101)))
        check(f"{CID}-G1", "100 parts per request is the maximum", len(many) == 100)


def test_g2_a_real_put_with_a_grant_and_every_tamper() -> None:
    with FakeS3() as fake, _small_parts():
        settings = fake.settings()
        body = _jsonl(12 * MIB)
        target = _target(body)
        uid = dos.start_multipart(settings.client(), settings, target)
        grants = {g.part_number: g for g in dos.presign_part_urls(settings, target, uid, [1, 2, 3])}
        piece = lambda n: body[(n - 1) * 5 * MIB : n * 5 * MIB]  # noqa: E731
        check(f"{CID}-G2", "a correct PUT is accepted", _put(grants[1].url, piece(1)) == 200)
        check(
            f"{CID}-G2",
            "the part is stored under that upload",
            len(fake.uploads[uid]["parts"].get(1, b"")) == 5 * MIB,
        )
        check(
            f"{CID}-G2",
            "a body of the wrong length is refused",
            _put(grants[2].url, piece(2)[:-1]) == 403 and 2 not in fake.uploads[uid]["parts"],
        )
        check(f"{CID}-G2", "a longer body is refused", _put(grants[2].url, piece(2) + b"x") == 403)
        check(
            f"{CID}-G2",
            "a grant cannot be reused for another part number",
            _put(grants[2].url.replace("partNumber=2", "partNumber=3"), piece(2)) == 403,
        )
        check(
            f"{CID}-G2",
            "a grant cannot be pointed at another key",
            _put(grants[2].url.replace(target.object_key, target.object_key + "x"), piece(2))
            == 403,
        )
        check(
            f"{CID}-G2",
            "a grant cannot be pointed at another upload id",
            _put(grants[2].url.replace(uid, "0" * 32), piece(2)) == 403,
        )
        check(
            f"{CID}-G2",
            "an unsigned PUT is refused",
            _put(grants[2].url.split("?")[0] + f"?partNumber=2&uploadId={uid}", piece(2)) == 403,
        )
        check(
            f"{CID}-G2",
            "a lengthened lifetime is refused",
            _put(grants[2].url.replace("X-Amz-Expires=300", "X-Amz-Expires=900"), piece(2)) == 403,
        )
        stale = dos.presign_part_urls(
            settings, target, uid, [2], now=datetime.now(UTC) - timedelta(seconds=400)
        )
        check(
            f"{CID}-G2",
            "an expired grant is refused by storage",
            _put(stale[0].url, piece(2)) == 403,
        )
        check(
            f"{CID}-G2",
            "nothing from any refused PUT was stored",
            set(fake.uploads[uid]["parts"]) == {1},
        )
        check(
            f"{CID}-G2",
            "the right body still works afterwards",
            _put(grants[2].url, piece(2)) == 200,
        )


# -- V: verify and complete -----------------------------------------------------------


def _gone(fake: FakeS3, target: dos.UploadTarget) -> bool:
    return target.object_key not in fake.objects and not any(
        u["key"] == target.object_key for u in fake.uploads.values()
    )


def test_v1_happy_single_part() -> None:
    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body)
        _open_and_upload(fake, target, body)
        got = _verify(fake, target)
        check(
            f"{CID}-V1",
            "returns what storage holds: size, hash, declared type",
            got == dos.VerifiedObject(3000, _sha(body), "application/jsonl"),
        )
        check(
            f"{CID}-V1",
            "the object is at the session's key with the declared type",
            fake.objects[target.object_key]["body"] == body
            and fake.objects[target.object_key]["content_type"] == "application/jsonl",
        )
        check(f"{CID}-V1", "the multipart upload was completed (none left)", not fake.uploads)
        check(f"{CID}-V1", "the multipart upload was opened with the declared content type", True)


def test_v2_happy_multipart_out_of_order_and_hash_covers_every_byte() -> None:
    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        settings = fake.settings()
        uid = dos.start_multipart(settings.client(), settings, target)
        plan = dos.plan_parts(len(body))
        for number in (3, 1, 2):
            (grant,) = dos.presign_part_urls(settings, target, uid, [number])
            start = (number - 1) * plan.part_size_bytes
            _put(grant.url, body[start : start + grant.content_length])
        got = _verify(fake, target)
        check(
            f"{CID}-V2",
            "three parts uploaded out of order verify",
            got.size_bytes == 12 * MIB and got.sha256 == _sha(body),
        )
        check(
            f"{CID}-V2",
            "the stored object is the parts in order",
            fake.objects[target.object_key]["body"] == body,
        )

    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        tampered = body[:-1] + b"Z"  # only the last byte differs; the head and first MiB are equal
        target = _target(body, sha=_sha(tampered))
        _open_and_upload(fake, target, body)
        got = _outcome(fake, target)
        check(
            f"{CID}-V2",
            "a difference in the last byte alone is caught",
            isinstance(got, dos.UploadRejection) and got.reason == "sha256_mismatch",
        )


def test_v3_missing_part_is_incomplete_and_resumable() -> None:
    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        uid = _open_and_upload(fake, target, body, only=[1, 3])
        got = _outcome(fake, target)
        check(
            f"{CID}-V3",
            "a missing middle part -> UploadIncomplete naming it",
            isinstance(got, dos.UploadIncomplete) and got.missing_parts == (2,),
        )
        check(
            f"{CID}-V3",
            "nothing was completed, aborted or deleted",
            uid in fake.uploads
            and target.object_key not in fake.objects
            and not fake.called("complete")
            and not fake.called("abort")
            and not fake.called("delete"),
        )
        settings = fake.settings()
        (grant,) = dos.presign_part_urls(settings, target, uid, [2])
        _put(grant.url, body[5 * MIB : 10 * MIB])
        check(
            f"{CID}-V3",
            "after the part arrives the same session verifies",
            _verify(fake, target).sha256 == _sha(body),
        )

    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        _open_and_upload(fake, target, body, only=[]) if False else None
        settings = fake.settings()
        dos.start_multipart(settings.client(), settings, target)
        got = _outcome(fake, target)
        check(
            f"{CID}-V3",
            "no parts at all -> UploadIncomplete naming all three",
            isinstance(got, dos.UploadIncomplete) and got.missing_parts == (1, 2, 3),
        )


def test_v4_oversize_and_extra_parts_are_rejected_and_removed() -> None:
    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        settings = fake.settings()
        uid = dos.start_multipart(settings.client(), settings, target)
        fake.inject_part(uid, 1, b"{" + b"x" * (5 * MIB))  # one byte over the planned size
        fake.inject_part(uid, 2, body[5 * MIB : 10 * MIB])
        fake.inject_part(uid, 3, body[10 * MIB :])
        got = _outcome(fake, target)
        check(
            f"{CID}-V4",
            "an oversize part -> part_size_mismatch",
            isinstance(got, dos.UploadRejection) and got.reason == "part_size_mismatch",
        )
        check(f"{CID}-V4", "the upload is aborted and nothing is left", _gone(fake, target))
        check(f"{CID}-V4", "it was never completed", not fake.called("complete"))

    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        settings = fake.settings()
        uid = dos.start_multipart(settings.client(), settings, target)
        for n, start in ((1, 0), (2, 5 * MIB), (3, 10 * MIB)):
            fake.inject_part(uid, n, body[start : start + 5 * MIB])
        fake.inject_part(uid, 4, b"extra")
        got = _outcome(fake, target)
        check(
            f"{CID}-V4",
            "a part beyond the plan -> unexpected_part",
            isinstance(got, dos.UploadRejection) and got.reason == "unexpected_part",
        )
        check(
            f"{CID}-V4",
            "nothing is left and it was never completed",
            _gone(fake, target) and not fake.called("complete"),
        )

    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        settings = fake.settings()
        uid = dos.start_multipart(settings.client(), settings, target)
        fake.inject_part(uid, 0, b"zero")
        got = _outcome(fake, target)
        check(
            f"{CID}-V4",
            "part number 0 -> unexpected_part",
            isinstance(got, dos.UploadRejection) and got.reason == "unexpected_part",
        )

    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        settings = fake.settings()
        uid = dos.start_multipart(settings.client(), settings, target)
        fake.inject_part(uid, 1, body[: 5 * MIB])
        fake.inject_part(uid, 2, body[5 * MIB : 10 * MIB])
        fake.inject_part(uid, 3, body[10 * MIB :] + b"y")  # the last part is one byte long
        got = _outcome(fake, target)
        check(
            f"{CID}-V4",
            "a last part that is not the remainder -> part_size_mismatch",
            isinstance(got, dos.UploadRejection) and got.reason == "part_size_mismatch",
        )

    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        settings = fake.settings()
        uid = dos.start_multipart(settings.client(), settings, target)
        fake.inject_part(uid, 1, body[: 5 * MIB - 1])
        got = _outcome(fake, target)
        check(
            f"{CID}-V4",
            "an undersize first part -> part_size_mismatch (not 'incomplete')",
            isinstance(got, dos.UploadRejection) and got.reason == "part_size_mismatch",
        )


def test_v5_wrong_hash_wrong_size_and_lying_head() -> None:
    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body, sha=_sha(b"something else"))
        _open_and_upload(fake, target, body)
        got = _outcome(fake, target)
        check(
            f"{CID}-V5",
            "a wrong declared hash -> sha256_mismatch",
            isinstance(got, dos.UploadRejection) and got.reason == "sha256_mismatch",
        )
        check(
            f"{CID}-V5",
            "the completed object is deleted",
            _gone(fake, target) and fake.called("delete"),
        )

    for label, size in (("larger", 3001), ("smaller", 2999)):
        with FakeS3() as fake:
            body = _jsonl(3000)
            target = _target(body, size=size)
            fake.objects[target.object_key] = {"body": body, "content_type": "application/jsonl"}
            got = _outcome(fake, target)
            check(
                f"{CID}-V5",
                f"a stored object {label} than declared -> size_mismatch",
                isinstance(got, dos.UploadRejection) and got.reason == "size_mismatch",
            )
            check(f"{CID}-V5", f"({label}) the object is deleted", _gone(fake, target))

    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body)
        fake.objects[target.object_key] = {
            "body": body + b"extra",
            "content_type": "application/jsonl",
        }
        fake.head_lies_about_size = 3000
        got = _outcome(fake, target)
        check(
            f"{CID}-V5",
            "a HEAD that understates the body is caught while streaming",
            isinstance(got, dos.UploadRejection) and got.reason == "size_mismatch",
        )
        check(f"{CID}-V5", "(lying HEAD) the object is deleted", _gone(fake, target))

    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body)
        fake.objects[target.object_key] = {"body": body[:-5], "content_type": "application/jsonl"}
        fake.head_lies_about_size = 3000
        got = _outcome(fake, target)
        check(
            f"{CID}-V5",
            "a HEAD that overstates a short body is caught while streaming",
            isinstance(got, dos.UploadRejection) and got.reason == "size_mismatch",
        )


def test_v5b_size_is_checked_before_reading_and_outages_are_described() -> None:
    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body, size=2999)
        fake.objects[target.object_key] = {"body": body, "content_type": "application/jsonl"}
        got = _outcome(fake, target)
        check(
            f"{CID}-V5",
            "a HEAD size mismatch rejects without reading the object",
            isinstance(got, dos.UploadRejection) and not fake.called("get"),
        )
    with FakeS3() as fake:
        fake.fail.add("list_uploads")
        got = _outcome(fake, _target(_jsonl(3000)))
        check(
            f"{CID}-V9",
            "the outage error names the operation and the storage status",
            isinstance(got, dos.UploadStorageUnavailableError)
            and "list_multipart_uploads" in str(got)
            and "500" in str(got),
        )


def test_v6_content_type_is_checked_against_the_bytes() -> None:
    cases = [
        ("text/csv", _jsonl(3000), "content_type_mismatch"),
        ("application/jsonl", b"a,b\n1,2\n" * 100, "content_type_mismatch"),
        (dos.XLSX_CONTENT_TYPE, b"a,b\n1,2\n" * 100, "content_type_mismatch"),
        ("application/jsonl", b"\x00\x01\x02" * 100, "content_type_mismatch"),
        ("application/pdf", b"%PDF-1.4 " * 50, "unsupported_content_type"),
    ]
    for declared, body, reason in cases:
        with FakeS3() as fake:
            target = _target(body, content_type=declared)
            settings = fake.settings()
            dos.start_multipart(settings.client(), settings, target)
            fake.inject_part(next(iter(fake.uploads)), 1, body)
            got = _outcome(fake, target)
            check(
                f"{CID}-V6",
                f"{declared} over {body[:8]!r}... -> {reason}",
                isinstance(got, dos.UploadRejection) and got.reason == reason,
            )
            check(f"{CID}-V6", f"({declared}) nothing is left in storage", _gone(fake, target))
    with FakeS3() as fake:
        body = b"a,b\n1,2\n" * 100
        target = _target(body, content_type="text/csv; charset=utf-8")
        _open_and_upload(fake, target, body)
        got = _verify(fake, target)
        check(
            f"{CID}-V6",
            "a parameterised type verifies and is returned as declared",
            got.content_type == "text/csv; charset=utf-8",
        )
    with FakeS3() as fake:
        body = _xlsx()
        target = _target(body, content_type=dos.XLSX_CONTENT_TYPE)
        _open_and_upload(fake, target, body)
        check(
            f"{CID}-V6", "an xlsx container verifies", _verify(fake, target).size_bytes == len(body)
        )
    with FakeS3() as fake:
        big = b'{"k":"' + b"x" * (200 * 1024) + b'"}\n'
        target = _target(big)
        _open_and_upload(fake, target, big)
        check(
            f"{CID}-V6",
            "an object larger than the sniff window verifies",
            _verify(fake, target).size_bytes == len(big),
        )
    with FakeS3() as fake:
        body = ('{"é":"' + "é" * 60000 + '"}\n').encode()
        target = _target(body)
        _open_and_upload(fake, target, body)
        check(
            f"{CID}-V6",
            "multi-byte text cut by the sniff window verifies",
            _verify(fake, target).size_bytes == len(body),
        )
    with FakeS3() as fake:
        body = b'{"k":"' + b"x" * (70 * 1024) + b'"}\n' + b"\x00\x00\x00"
        target = _target(body)
        _open_and_upload(fake, target, body)
        check(
            f"{CID}-V6",
            "only the leading window is sniffed (a NUL after it does not matter)",
            _verify(fake, target).size_bytes == len(body),
        )


def test_v7_expired_session_completes_nothing() -> None:
    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body, expires_at=_T0)
        uid = _open_and_upload(fake, target, body)
        for label, now in (
            ("after", _T0 + timedelta(seconds=1)),
            ("long after", _T0 + timedelta(days=3)),
        ):
            got = _outcome(fake, target, now=now)
            check(
                f"{CID}-V7",
                f"finalize {label} expiry -> UploadExpired",
                isinstance(got, dos.UploadExpired),
            )
        check(
            f"{CID}-V7",
            "storage was not touched: still uploading, nothing completed",
            uid in fake.uploads
            and target.object_key not in fake.objects
            and not fake.called("complete"),
        )
        at = _outcome(fake, target, now=_T0)
        check(
            f"{CID}-V7",
            "exactly at the expiry instant is still allowed",
            isinstance(at, dos.VerifiedObject),
        )


def test_v8_wrong_key_creates_nothing() -> None:
    with FakeS3() as fake:
        body = _jsonl(3000)
        mine = _target(body)
        other = _target(body)
        _open_and_upload(fake, other, body)
        _verify(fake, other)  # a perfectly good object at a *different* key
        got = _outcome(fake, mine)
        check(
            f"{CID}-V8",
            "an object under another key does not satisfy this session",
            isinstance(got, dos.UploadIncomplete),
        )
        check(f"{CID}-V8", "the other key's object is untouched", other.object_key in fake.objects)
        check(
            f"{CID}-V8",
            "nothing was written under this session's key",
            mine.object_key not in fake.objects,
        )

    with FakeS3() as fake:
        body = _jsonl(3000)
        mine = _target(body)
        other = _target(body)
        settings = fake.settings()
        other_uid = dos.start_multipart(settings.client(), settings, other)
        fake.inject_part(other_uid, 1, body)
        got = _outcome(fake, mine)
        check(
            f"{CID}-V8",
            "another key's in-progress upload is neither read nor completed",
            isinstance(got, dos.UploadIncomplete)
            and other_uid in fake.uploads
            and not fake.called("complete"),
        )

    with FakeS3() as fake:
        key = "tenants/t/projects/p/uploads/s/source"
        longer = key + "-evil"
        body = _jsonl(3000)
        settings = fake.settings()
        evil_uid = dos.start_multipart(settings.client(), settings, _target(body, key=longer))
        fake.inject_part(evil_uid, 1, body)
        got = _outcome(fake, _target(body, key=key))
        check(
            f"{CID}-V8",
            "a key that merely starts with this key is not this key",
            isinstance(got, dos.UploadIncomplete),
        )
        check(
            f"{CID}-V8",
            "find_upload_id matches the exact key only",
            dos.find_upload_id(settings.client(), settings, _target(body, key=key)) is None
            and dos.find_upload_id(settings.client(), settings, _target(body, key=longer))
            == evil_uid,
        )


def test_v9_storage_failures_are_never_rejections_or_successes() -> None:
    for op in ("list_uploads", "list_parts", "complete", "head", "get"):
        with FakeS3() as fake:
            body = _jsonl(3000)
            target = _target(body)
            _open_and_upload(fake, target, body) if op in (
                "list_uploads",
                "list_parts",
                "complete",
            ) else None
            if op in ("head", "get"):
                fake.objects[target.object_key] = {
                    "body": body,
                    "content_type": "application/jsonl",
                }
            fake.fail.add(op)
            got = _outcome(fake, target)
            check(
                f"{CID}-V9",
                f"a storage 500 on {op} -> UploadStorageUnavailableError",
                isinstance(got, dos.UploadStorageUnavailableError),
            )
            check(
                f"{CID}-V9",
                f"({op}) nothing was deleted or rejected",
                not fake.called("delete") and not fake.called("abort"),
            )

    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body, sha=_sha(b"wrong"))
        _open_and_upload(fake, target, body)
        fake.fail.add("delete")
        got = _outcome(fake, target)
        check(
            f"{CID}-V9",
            "if cleanup of a bad object fails, the outage wins over the verdict",
            isinstance(got, dos.UploadStorageUnavailableError),
        )

    with FakeS3() as fake:
        settings = fake.settings()
    dead = dos.UploadStorageSettings(settings.endpoint, settings.credentials, settings.bucket)
    target = _target(b'{"a":1}\n')
    for label, call in (
        ("verify", lambda: dos.verify_and_complete(dead.client(), dead, target)),
        ("start", lambda: dos.start_multipart(dead.client(), dead, target)),
        ("parts", lambda: dos.list_uploaded_parts(dead.client(), dead, target, "u")),
        ("find", lambda: dos.find_upload_id(dead.client(), dead, target)),
        ("abort", lambda: dos.abort_upload(dead.client(), dead, target)),
    ):
        check(
            f"{CID}-V9",
            f"an unreachable server on {label} -> UploadStorageUnavailableError",
            _raises(call, dos.UploadStorageUnavailableError),
        )

    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body)
        settings = fake.settings()
        dos.start_multipart(settings.client(), settings, target)
        fake.uploads["dup"] = {"key": target.object_key, "content_type": "", "parts": {}}
        check(
            f"{CID}-V9",
            "two uploads for one key is an anomaly, not a guess",
            _raises(
                lambda: dos.find_upload_id(settings.client(), settings, target),
                dos.UploadStorageUnavailableError,
            ),
        )


def test_v10_resume_after_a_crash_between_complete_and_the_database() -> None:
    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body)
        _open_and_upload(fake, target, body)
        first = _verify(fake, target)
        fake.reset_calls()
        second = _verify(fake, target)
        check(f"{CID}-V10", "a second finalize re-verifies the stored object", second == first)
        check(f"{CID}-V10", "it did not complete anything again", not fake.called("complete"))

    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body)
        _open_and_upload(fake, target, body)
        _verify(fake, target)
        fake.objects[target.object_key]["body"] = b"{" + b"y" * 2999
        got = _outcome(fake, target)
        check(
            f"{CID}-V10",
            "an object changed after completion is re-hashed and rejected",
            isinstance(got, dos.UploadRejection) and got.reason == "sha256_mismatch",
        )


def test_v11_abort_is_idempotent_and_cleans_both_forms() -> None:
    with FakeS3() as fake:
        body = _jsonl(3000)
        target = _target(body)
        settings = fake.settings()
        dos.abort_upload(settings.client(), settings, target)
        check(f"{CID}-V11", "aborting nothing is fine", True)
        _open_and_upload(fake, target, body)
        dos.abort_upload(settings.client(), settings, target)
        check(f"{CID}-V11", "an in-progress upload is aborted", _gone(fake, target))
        fake.objects[target.object_key] = {"body": body, "content_type": "application/jsonl"}
        dos.abort_upload(settings.client(), settings, target)
        check(f"{CID}-V11", "a completed object is deleted", _gone(fake, target))
        dos.abort_upload(settings.client(), settings, target)
        check(f"{CID}-V11", "and again", True)


def test_v12_listing_parts_reports_what_storage_holds() -> None:
    with FakeS3() as fake, _small_parts():
        body = _jsonl(12 * MIB)
        target = _target(body)
        uid = _open_and_upload(fake, target, body, only=[3, 1])
        settings = fake.settings()
        parts = dos.list_uploaded_parts(settings.client(), settings, target, uid)
        check(
            f"{CID}-V12",
            "parts come back ordered with real sizes and etags",
            [(p.part_number, p.size_bytes) for p in parts] == [(1, 5 * MIB), (3, 2 * MIB)]
            and all(len(p.etag) == 32 for p in parts),
        )
        check(
            f"{CID}-V12",
            "an unknown upload id is a storage error",
            _raises(
                lambda: dos.list_uploaded_parts(settings.client(), settings, target, "nope"),
                dos.UploadStorageUnavailableError,
            ),
        )
        check(
            f"{CID}-V12",
            "find_upload_id returns the open upload",
            dos.find_upload_id(settings.client(), settings, target) == uid,
        )


# -- R: routes -----------------------------------------------------------------------

_ORG_ID = uuid.uuid4()
_PROJECT = uuid.uuid4()
_OTHER_PROJECT = uuid.uuid4()
_CONN = uuid.uuid4()
# The route tests sign URLs at `_NOW` and the in-process server checks them
# against the real clock, so it is the real time at load, not a fixed date.
_NOW = datetime.now(UTC).replace(microsecond=0)
_PRINCIPALS = {
    name: AuthenticatedPrincipal(issuer="https://issuer.example", subject=name)
    for name in ("ml", "admin", "orgadmin", "viewer", "evaluator", "approver")
}
_ROLES = {
    "ml": Role.ML_ENGINEER,
    "admin": Role.WORKSPACE_ADMIN,
    "orgadmin": Role.ORGANIZATION_ADMIN,
    "viewer": Role.VIEWER,
    "evaluator": Role.EVALUATOR,
    "approver": Role.APPROVER,
}


def _principal_resolver(request: Any) -> AuthenticatedPrincipal | None:
    return _PRINCIPALS.get(request.headers.get("x-test-principal", ""))


def _role_lookup(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return _ROLES.get(subject) if organization_id == _ORG_ID else None


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve(app: FastAPI):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class _Http:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def detail(self) -> str:
        try:
            return str(self.json().get("detail"))
        except Exception:  # noqa: BLE001
            return "<unparseable>"


def _call(
    base: str, method: str, path: str, body: Any = None, principal: str | None = "ml"
) -> _Http:
    headers = {"X-Test-Principal": principal} if principal else {}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=30)
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        return _Http(response.status, response.read())
    finally:
        conn.close()


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, session: Session, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def actions(self) -> list[str]:
        return [e["action"] for e in self.events]


class _World:
    """The FTF-0500 seams: which connection exists, its type, status, purposes."""

    def __init__(self) -> None:
        self.source_type = "upload"
        self.purposes = ["training", "validation", "held_out_evaluation"]
        self.status = "active"
        self.missing = False
        self.known_projects = {_PROJECT, _OTHER_PROJECT}

    def authorize(
        self, session: Any, context: Any, project_id: Any, connection_id: Any, purpose: str
    ) -> Any:
        self.authorize_project(session, context, project_id)
        if self.missing:
            raise DatasetSourceConnectionNotFoundError("missing")
        if self.status != "active" or purpose not in self.purposes:
            raise DatasetSourceAuthorizationError("denied")
        return SimpleNamespace(id=connection_id, source_type=self.source_type)

    def authorize_project(self, session: Any, context: Any, project_id: Any) -> None:
        if project_id not in self.known_projects:
            raise store.ProjectNotFoundError("project")


class _Env:
    def __init__(self, fake: FakeS3) -> None:
        self.fake = fake
        self.settings: dos.UploadStorageSettings | None = fake.settings()
        self.audit = _Audit()
        self.world = _World()
        self.clock = [_NOW]
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        store.DatasetUploadSession.__table__.create(self.engine)  # type: ignore[attr-defined]
        store.Dataset.__table__.create(self.engine)  # type: ignore[attr-defined]
        self.base = ""

    def sessions(self) -> list[store.DatasetUploadSession]:
        with Session(self.engine) as s:
            rows = list(s.query(store.DatasetUploadSession).all())
            s.expunge_all()
            return rows

    def datasets(self) -> list[store.Dataset]:
        with Session(self.engine) as s:
            rows = list(s.query(store.Dataset).all())
            s.expunge_all()
            return rows

    def storage_for(self, session_id: str) -> dos.UploadTarget:
        row = next(r for r in self.sessions() if str(r.id) == session_id)
        return routes._target(row)


def _passthrough_execute(session: Session, organization_id: uuid.UUID, statement: Any) -> Any:
    return session.execute(statement)


@contextlib.contextmanager
def _routes_env():
    with FakeS3() as fake:
        env = _Env(fake)

        def _tz(target: Any, *args: Any) -> None:
            for name in ("created_at", "expires_at", "finalized_at", "updated_at"):
                value = getattr(target, name, None)
                if value is not None and value.tzinfo is None:
                    set_committed_value(target, name, value.replace(tzinfo=UTC))

        listeners = []
        for model in (store.DatasetUploadSession, store.Dataset):
            for name in ("load", "refresh"):
                event.listen(model, name, _tz)
                listeners.append((model, name))

        @contextlib.contextmanager
        def _scoped(session_factory: Any, tenant_context: Any):
            with Session(env.engine, expire_on_commit=False) as session:
                try:
                    yield session
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise

        saved_store = (
            store.execute_tenant_scoped,
            store.write_audit_event,
            store.authorize_dataset_source_purpose,
            store._authorize_project_or_raise,
        )
        saved_routes = routes.tenant_scoped_session_for
        store.execute_tenant_scoped = _passthrough_execute
        store.write_audit_event = env.audit
        store.authorize_dataset_source_purpose = env.world.authorize
        store._authorize_project_or_raise = env.world.authorize_project
        routes.tenant_scoped_session_for = _scoped
        app = FastAPI()
        app.include_router(
            routes.build_direct_upload_router(
                lambda: None,
                principal_resolver=_principal_resolver,
                role_lookup=_role_lookup,
                storage_factory=lambda: env.settings,
                clock=lambda: env.clock[0],
            )
        )
        try:
            with _serve(app) as base:
                env.base = base
                yield env
        finally:
            (
                store.execute_tenant_scoped,
                store.write_audit_event,
                store.authorize_dataset_source_purpose,
                store._authorize_project_or_raise,
            ) = saved_store
            routes.tenant_scoped_session_for = saved_routes
            for model, name in listeners:
                event.remove(model, name, _tz)
            env.engine.dispose()


def _col(project: Any = _PROJECT) -> str:
    return f"/api/v1/organizations/{_ORG_ID}/projects/{project}/upload-sessions"


def _create_body(body: bytes, **over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_source_connection_id": str(_CONN),
        "purpose": "training",
        "declared_size_bytes": len(body),
        "declared_sha256": _sha(body),
        "declared_content_type": "application/jsonl",
    }
    payload.update(over)
    return payload


def _open_session(env: _Env, body: bytes, principal: str = "ml", **over: Any) -> dict[str, Any]:
    reply = _call(env.base, "POST", _col(), _create_body(body, **over), principal)
    assert reply.status == 201, (reply.status, reply.body)
    return reply.json()


def _put_all(env: _Env, session_id: str, body: bytes, principal: str = "ml") -> None:
    plan = dos.plan_parts(len(body))
    numbers = list(range(1, plan.part_count + 1))
    reply = _call(
        env.base, "POST", f"{_col()}/{session_id}/part-urls", {"part_numbers": numbers}, principal
    )
    assert reply.status == 200, (reply.status, reply.body)
    for part in reply.json()["parts"]:
        start = (part["part_number"] - 1) * plan.part_size_bytes
        status = _put(part["url"], body[start : start + part["content_length"]])
        assert status == 200, status


def _finalize_body(name: str = "support") -> dict[str, Any]:
    return {"dataset_name": name}


_SESSION_KEYS = {
    "id", "project_id", "dataset_source_connection_id", "purpose", "status",
    "declared_size_bytes", "declared_sha256", "declared_content_type", "rejection_reason",
    "created_at", "expires_at", "finalized_at", "part_size_bytes", "part_count",
}  # fmt: skip
_DATASET_KEYS = {
    "id", "organization_id", "project_id", "name", "version", "purpose", "upload_session_id",
    "verified_sha256", "verified_size_bytes", "created_by", "created_at",
}  # fmt: skip


def test_r1_roles_and_authentication() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        item = f"{_col()}/{sid}"
        calls = [
            ("POST", _col(), _create_body(body)),
            ("GET", item, None),
            ("GET", item + "/parts", None),
            ("POST", item + "/part-urls", {"part_numbers": [1]}),
            ("POST", item + "/finalize", _finalize_body()),
        ]
        for method, path, payload in calls:
            label = f"{method} {path.rsplit('/', 1)[-1][:12]}"
            check(
                f"{CID}-R1",
                f"{label}: no principal -> 401",
                _call(env.base, method, path, payload, None).status == 401,
            )
            for who in ("viewer", "evaluator", "approver"):
                check(
                    f"{CID}-R1",
                    f"{label}: {who} -> 403",
                    _call(env.base, method, path, payload, who).status == 403,
                )
            check(
                f"{CID}-R1",
                f"{label}: unknown principal -> 401/403",
                _call(env.base, method, path, payload, "nobody").status in (401, 403),
            )
        for who in ("ml", "admin", "orgadmin"):
            check(
                f"{CID}-R1",
                f"{who} may read a session",
                _call(env.base, "GET", item, None, who).status == 200,
            )
        check(
            f"{CID}-R1",
            "rejected callers changed nothing",
            len(env.sessions()) == 1 and env.sessions()[0].status == "open" and not env.datasets(),
        )
        foreign = f"/api/v1/organizations/{uuid.uuid4()}/projects/{_PROJECT}/upload-sessions/{sid}"
        check(
            f"{CID}-R1",
            "another organization's path is not served",
            _call(env.base, "GET", foreign, None, "ml").status in (401, 403, 404),
        )


def test_r2_create_shape_audit_and_secrecy() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        reply = _call(env.base, "POST", _col(), _create_body(body), "ml")
        data = reply.json()
        check(f"{CID}-R2", "201", reply.status == 201)
        check(
            f"{CID}-R2", "exactly the frozen UploadSessionWithPlan keys", set(data) == _SESSION_KEYS
        )
        check(
            f"{CID}-R2",
            "an open session with the declared values",
            data["status"] == "open"
            and data["declared_size_bytes"] == 2000
            and data["declared_sha256"] == _sha(body)
            and data["declared_content_type"] == "application/jsonl"
            and data["purpose"] == "training"
            and data["rejection_reason"] is None
            and data["finalized_at"] is None
            and data["project_id"] == str(_PROJECT)
            and data["dataset_source_connection_id"] == str(_CONN),
        )
        check(
            f"{CID}-R2",
            "the plan is one 64 MiB part",
            data["part_size_bytes"] == 64 * MIB and data["part_count"] == 1,
        )
        check(
            f"{CID}-R2",
            "the session lives 24 hours",
            datetime.fromisoformat(data["expires_at"]) - datetime.fromisoformat(data["created_at"])
            == timedelta(hours=24),
        )
        text = reply.body.decode()
        check(
            f"{CID}-R2",
            "no object key, tenant prefix, bucket or URL in the response",
            "tenants/" not in text
            and fake_bucket(env) not in text
            and "http" not in text
            and "object_key" not in text,
        )
        (row,) = env.sessions()
        check(
            f"{CID}-R2",
            "one row, open, max_bytes equal to the declared size (no headroom)",
            row.status == "open" and row.max_bytes == 2000 and str(row.id) == data["id"],
        )
        check(
            f"{CID}-R2",
            "created_by is the authenticated subject, not client text",
            row.created_by == "ml",
        )
        check(
            f"{CID}-R2",
            "one multipart upload exists for that session's key, with the declared type",
            len(env.fake.uploads) == 1
            and next(iter(env.fake.uploads.values()))["key"] == row.object_key
            and next(iter(env.fake.uploads.values()))["content_type"] == "application/jsonl",
        )
        check(f"{CID}-R2", "audited once", env.audit.actions() == ["dataset_upload_session.create"])
        read = _call(env.base, "GET", f"{_col()}/{data['id']}", None, "ml")
        check(
            f"{CID}-R2", "GET returns the same session", read.status == 200 and read.json() == data
        )


def fake_bucket(env: _Env) -> str:
    return env.fake.BUCKET


def test_r3_create_refusals_write_nothing() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)

        def refused(label: str, status: int, detail: str | None, reply: _Http) -> None:
            check(f"{CID}-R3", f"{label}: {status}", reply.status == status)
            if detail is not None:
                check(f"{CID}-R3", f"{label}: detail {detail}", reply.detail == detail)
            check(
                f"{CID}-R3",
                f"{label}: no session row and no multipart upload",
                not env.sessions() and not env.fake.uploads,
            )

        good = _create_body(body)
        refused(
            "extra field",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "object_key": "x"}),
        )
        refused(
            "missing field",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {k: v for k, v in good.items() if k != "purpose"}),
        )
        refused("not json", 400, "invalid_body", _call(env.base, "POST", _col(), b"{nope"))
        refused("json list", 400, "invalid_body", _call(env.base, "POST", _col(), [1]))
        refused(
            "size is bool",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_size_bytes": True}),
        )
        refused(
            "size is string",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_size_bytes": "2000"}),
        )
        refused(
            "size is float",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_size_bytes": 2000.0}),
        )
        refused(
            "size zero",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_size_bytes": 0}),
        )
        refused(
            "size negative",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_size_bytes": -5}),
        )
        refused(
            "size over 10 GiB",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_size_bytes": 10 * GIB + 1}),
        )
        refused(
            "purpose is a list",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "purpose": ["training"]}),
        )
        refused(
            "unknown purpose",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "purpose": "pretraining"}),
        )
        for label, weird in (("list", []), ("dict", {}), ("int", 5), ("null", None)):
            refused(
                f"hash is a {label}",
                400,
                "invalid_body",
                _call(env.base, "POST", _col(), {**good, "declared_sha256": weird}),
            )
            refused(
                f"purpose is a {label}",
                400,
                "invalid_body",
                _call(env.base, "POST", _col(), {**good, "purpose": weird}),
            )
            refused(
                f"content type is a {label}",
                400,
                "invalid_body",
                _call(env.base, "POST", _col(), {**good, "declared_content_type": weird}),
            )
        refused(
            "hash too short",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_sha256": "ab"}),
        )
        refused(
            "hash uppercase",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_sha256": "A" * 64}),
        )
        refused(
            "connection id not a uuid",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "dataset_source_connection_id": "x"}),
        )
        refused(
            "connection id is an int",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "dataset_source_connection_id": 5}),
        )
        refused(
            "content type pdf",
            400,
            "unsupported_content_type",
            _call(env.base, "POST", _col(), {**good, "declared_content_type": "application/pdf"}),
        )
        refused(
            "content type is a list",
            400,
            "invalid_body",
            _call(env.base, "POST", _col(), {**good, "declared_content_type": ["text/csv"]}),
        )
        refused(
            "oversized request body",
            413,
            "request_too_large",
            _call(env.base, "POST", _col(), b" " * (70 * 1024)),
        )

        env.world.purposes = ["training"]
        refused(
            "purpose not granted",
            403,
            "purpose_not_granted",
            _call(env.base, "POST", _col(), {**good, "purpose": "validation"}),
        )
        env.world.purposes = ["training", "validation", "held_out_evaluation"]
        env.world.status = "revoked"
        refused(
            "revoked connection", 403, "purpose_not_granted", _call(env.base, "POST", _col(), good)
        )
        env.world.status = "active"
        env.world.source_type = "s3"
        refused(
            "not an upload connection",
            409,
            "not_an_upload_source",
            _call(env.base, "POST", _col(), good),
        )
        env.world.source_type = "upload"
        env.world.missing = True
        refused("connection missing", 404, "not_found", _call(env.base, "POST", _col(), good))
        env.world.missing = False
        refused(
            "unknown project", 404, "not_found", _call(env.base, "POST", _col(uuid.uuid4()), good)
        )
        refused(
            "ml_engineer opening held-out",
            403,
            "purpose_forbidden",
            _call(env.base, "POST", _col(), {**good, "purpose": "held_out_evaluation"}, "ml"),
        )

        env.settings = None
        refused(
            "storage not configured",
            503,
            "storage_not_configured",
            _call(env.base, "POST", _col(), good),
        )
        env.settings = env.fake.settings()
        env.fake.fail.add("create")
        refused(
            "storage refuses to open the upload",
            503,
            "storage_unavailable",
            _call(env.base, "POST", _col(), good),
        )
        env.fake.fail.discard("create")

        ok = _call(env.base, "POST", _col(), {**good, "purpose": "held_out_evaluation"}, "admin")
        check(f"{CID}-R3", "a workspace admin may open a held-out upload", ok.status == 201)
        ok = _call(
            env.base,
            "POST",
            _col(),
            {**good, "declared_content_type": "Text/CSV; charset=utf-8"},
            "ml",
        )
        check(
            f"{CID}-R3", "a parameterised, mixed-case supported type is accepted", ok.status == 201
        )


def test_r4_part_urls_and_listing() -> None:
    with _routes_env() as env, _small_parts():
        body = _jsonl(12 * MIB)
        data = _open_session(env, body)
        sid = data["id"]
        check(
            f"{CID}-R4",
            "the plan is three 5 MiB parts",
            data["part_size_bytes"] == 5 * MIB and data["part_count"] == 3,
        )
        item = f"{_col()}/{sid}"
        empty = _call(env.base, "GET", item + "/parts", None)
        check(
            f"{CID}-R4",
            "nothing uploaded yet",
            empty.status == 200
            and empty.json()["parts"] == []
            and empty.json()["part_count"] == 3
            and empty.json()["part_size_bytes"] == 5 * MIB,
        )
        reply = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [2, 1]})
        grants = reply.json()["parts"]
        check(
            f"{CID}-R4",
            "grants for the requested parts",
            reply.status == 200 and [g["part_number"] for g in grants] == [2, 1],
        )
        check(
            f"{CID}-R4",
            "each grant: PUT, planned length, signed 5-minute expiry",
            all(
                set(g) == {"part_number", "method", "url", "content_length", "expires_at"}
                for g in grants
            )
            and all(g["method"] == "PUT" and g["content_length"] == 5 * MIB for g in grants)
            and all("X-Amz-Expires=300" in g["url"] for g in grants),
        )
        check(
            f"{CID}-R4",
            "grants carry the real key only inside the URL",
            all(env.storage_for(sid).object_key in g["url"] for g in grants),
        )
        for g in grants:
            start = (g["part_number"] - 1) * 5 * MIB
            check(
                f"{CID}-R4",
                f"part {g['part_number']} uploads with its grant",
                _put(g["url"], body[start : start + 5 * MIB]) == 200,
            )
        listed = _call(env.base, "GET", item + "/parts", None).json()
        check(
            f"{CID}-R4",
            "the listing shows exactly what arrived (resume)",
            listed["parts"]
            == [
                {"part_number": 1, "size_bytes": 5 * MIB},
                {"part_number": 2, "size_bytes": 5 * MIB},
            ],
        )
        bad_lists: list[tuple[str, dict[str, Any]]] = [
            ("empty list", {"part_numbers": []}),
            ("part 0", {"part_numbers": [0]}),
            ("part 4", {"part_numbers": [4]}),
            ("duplicate", {"part_numbers": [1, 1]}),
            ("bool", {"part_numbers": [True]}),
            ("string", {"part_numbers": ["1"]}),
            ("not a list", {"part_numbers": 1}),
            ("101 parts", {"part_numbers": list(range(1, 102))}),
            ("nested list", {"part_numbers": [[1]]}),
            ("object element", {"part_numbers": [{}]}),
            ("null element", {"part_numbers": [None]}),
            ("float element", {"part_numbers": [1.0]}),
            ("object", {"part_numbers": {"1": 1}}),
        ]
        for label, payload in bad_lists:
            reply = _call(env.base, "POST", item + "/part-urls", payload)
            check(
                f"{CID}-R4",
                f"{label}: 400 invalid_part_numbers",
                reply.status == 400 and reply.detail == "invalid_part_numbers",
            )
        for label, payload in (("extra field", {"part_numbers": [1], "x": 1}), ("missing", {})):
            reply = _call(env.base, "POST", item + "/part-urls", payload)
            check(
                f"{CID}-R4",
                f"{label}: 400 invalid_body",
                reply.status == 400 and reply.detail == "invalid_body",
            )
        check(
            f"{CID}-R4",
            "unknown session: 404",
            _call(env.base, "GET", f"{_col()}/{uuid.uuid4()}/parts", None).status == 404,
        )
        check(
            f"{CID}-R4",
            "another project's id: 404 (identical)",
            _call(env.base, "GET", f"{_col(_OTHER_PROJECT)}/{sid}", None).status == 404,
        )
        check(
            f"{CID}-R4",
            "unknown session part-urls: 404",
            _call(
                env.base, "POST", f"{_col()}/{uuid.uuid4()}/part-urls", {"part_numbers": [1]}
            ).status
            == 404,
        )
        env.fake.fail.add("list_uploads")
        check(
            f"{CID}-R4",
            "storage outage on part-urls: 503",
            _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]}).status == 503,
        )
        check(
            f"{CID}-R4",
            "storage outage on listing: 503",
            _call(env.base, "GET", item + "/parts", None).status == 503,
        )
        env.fake.fail.clear()
        env.settings = None
        check(
            f"{CID}-R4",
            "not configured: part-urls 503",
            _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]}).status == 503,
        )
        check(
            f"{CID}-R4",
            "not configured: listing 503",
            _call(env.base, "GET", item + "/parts", None).status == 503,
        )
        env.settings = env.fake.settings()


def test_r5_expired_session_issues_nothing_and_is_marked_expired() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        item = f"{_col()}/{sid}"
        env.clock[0] = _NOW + timedelta(hours=24, seconds=1)
        reply = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]})
        check(
            f"{CID}-R5",
            "part-urls after expiry -> 410 upload_expired",
            reply.status == 410 and reply.detail == "upload_expired",
        )
        check(f"{CID}-R5", "no URL was issued", b"http" not in reply.body)
        check(
            f"{CID}-R5",
            "the session is now expired in the database",
            env.sessions()[0].status == "expired",
        )
        check(f"{CID}-R5", "the multipart upload was aborted", not env.fake.uploads)
        again = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]})
        check(
            f"{CID}-R5",
            "an expired session is simply not open afterwards",
            again.status == 409 and again.detail == "upload_session_not_open",
        )
        fin = _call(env.base, "POST", item + "/finalize", _finalize_body())
        check(
            f"{CID}-R5",
            "finalize on it -> 409 and no dataset",
            fin.status == 409 and not env.datasets(),
        )
        listing = _call(env.base, "GET", item + "/parts", None)
        check(f"{CID}-R5", "listing parts of a closed session -> 409", listing.status == 409)

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        env.clock[0] = _NOW + timedelta(hours=24, seconds=1)
        fin = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        check(
            f"{CID}-R5",
            "finalize after expiry -> 410 upload_expired",
            fin.status == 410 and fin.detail == "upload_expired",
        )
        check(
            f"{CID}-R5",
            "no dataset, session expired, storage cleaned",
            not env.datasets()
            and env.sessions()[0].status == "expired"
            and not env.fake.uploads
            and not env.fake.objects,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        env.clock[0] = _NOW + timedelta(hours=24)
        fin = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        check(f"{CID}-R5", "finalize exactly at expiry still succeeds", fin.status == 201)

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        env.clock[0] = _NOW + timedelta(hours=24, seconds=1)
        env.fake.fail.add("list_uploads")
        reply = _call(env.base, "POST", f"{_col()}/{sid}/part-urls", {"part_numbers": [1]})
        check(
            f"{CID}-R5",
            "an outage while cleaning up an expired session does not change the answer",
            reply.status == 503 or reply.status == 410,
        )


def test_r6_finalize_creates_the_dataset_and_versions() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body("  support  "))
        data = reply.json()
        check(f"{CID}-R6", "201", reply.status == 201)
        check(f"{CID}-R6", "exactly the Dataset keys", set(data) == _DATASET_KEYS)
        check(
            f"{CID}-R6",
            "version 1 of the trimmed name with the verified values",
            data["name"] == "support"
            and data["version"] == 1
            and data["verified_size_bytes"] == 2000
            and data["verified_sha256"] == _sha(body)
            and data["purpose"] == "training"
            and data["upload_session_id"] == sid
            and data["project_id"] == str(_PROJECT)
            and data["created_by"] == "ml",
        )
        check(
            f"{CID}-R6",
            "no storage field in the dataset",
            "tenants/" not in reply.body.decode() and "object_key" not in data,
        )
        (row,) = env.sessions()
        check(
            f"{CID}-R6",
            "the session is verified with the values storage held",
            row.status == "verified"
            and row.verified_size_bytes == 2000
            and row.verified_sha256 == _sha(body)
            and row.verified_content_type == "application/jsonl",
        )
        check(
            f"{CID}-R6",
            "the object stays in storage",
            len(env.fake.objects) == 1 and not env.fake.uploads,
        )
        check(
            f"{CID}-R6",
            "audited: session create then dataset create",
            env.audit.actions() == ["dataset_upload_session.create", "dataset.create"],
        )

        sid2 = _open_session(env, body)["id"]
        _put_all(env, sid2, body)
        second = _call(
            env.base, "POST", f"{_col()}/{sid2}/finalize", _finalize_body("support")
        ).json()
        check(f"{CID}-R6", "the same name again is version 2", second["version"] == 2)
        sid3 = _open_session(env, body)["id"]
        _put_all(env, sid3, body)
        other = _call(env.base, "POST", f"{_col()}/{sid3}/finalize", _finalize_body("other")).json()
        check(f"{CID}-R6", "another name starts at version 1", other["version"] == 1)

        again = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        check(
            f"{CID}-R6",
            "finalizing a verified session again -> 409 and no new version",
            again.status == 409
            and again.detail == "upload_session_not_open"
            and len(env.datasets()) == 3,
        )
        parts = _call(env.base, "POST", f"{_col()}/{sid}/part-urls", {"part_numbers": [1]})
        check(f"{CID}-R6", "no new URLs for a verified session", parts.status == 409)
        read = _call(env.base, "GET", f"{_col()}/{sid}", None).json()
        check(
            f"{CID}-R6",
            "the session reads back verified",
            read["status"] == "verified" and read["finalized_at"] is not None,
        )


def test_r7_finalize_multipart_end_to_end() -> None:
    with _routes_env() as env, _small_parts():
        body = _jsonl(12 * MIB)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body("big"))
        check(
            f"{CID}-R7",
            "a 3-part upload finalizes",
            reply.status == 201 and reply.json()["verified_size_bytes"] == 12 * MIB,
        )
        check(
            f"{CID}-R7",
            "the stored object is the whole upload",
            next(iter(env.fake.objects.values()))["body"] == body,
        )


def test_r8_finalize_refusals_create_no_dataset() -> None:
    def fresh(env: _Env, body: bytes, **over: Any) -> str:
        return _open_session(env, body, **over)["id"]

    def fin(env: _Env, sid: str, name: str = "support") -> _Http:
        return _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body(name))

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body, declared_sha256=_sha(b"not the body"))
        _put_all(env, sid, body)
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "wrong hash -> 422 sha256_mismatch",
            reply.status == 422 and reply.detail == "sha256_mismatch",
        )
        row = env.sessions()[0]
        check(
            f"{CID}-R8",
            "the session is rejected with that reason",
            row.status == "rejected" and row.rejection_reason == "sha256_mismatch",
        )
        check(
            f"{CID}-R8",
            "no dataset, and the object is deleted",
            not env.datasets() and not env.fake.objects and not env.fake.uploads,
        )
        check(
            f"{CID}-R8",
            "the rejection is audited",
            "dataset_upload_session.reject" in env.audit.actions(),
        )
        check(
            f"{CID}-R8",
            "a rejected session cannot be finalized again",
            fin(env, sid).status == 409 and not env.datasets(),
        )
        check(
            f"{CID}-R8",
            "nor given new URLs",
            _call(env.base, "POST", f"{_col()}/{sid}/part-urls", {"part_numbers": [1]}).status
            == 409,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body, declared_content_type="text/csv")
        _put_all(env, sid, body)
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "JSON bytes declared as CSV -> 422 content_type_mismatch",
            reply.status == 422
            and reply.detail == "content_type_mismatch"
            and env.sessions()[0].status == "rejected",
        )
        check(
            f"{CID}-R8",
            "(type) no dataset, object deleted",
            not env.datasets() and not env.fake.objects,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body)
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "nothing uploaded -> 409 upload_incomplete",
            reply.status == 409 and reply.detail == "upload_incomplete",
        )
        check(
            f"{CID}-R8",
            "(empty) the session stays open and resumable",
            env.sessions()[0].status == "open"
            and not env.datasets()
            and len(env.fake.uploads) == 1,
        )
        _put_all(env, sid, body)
        check(
            f"{CID}-R8",
            "(empty) after the upload completes the same session finalizes",
            fin(env, sid).status == 201,
        )

    with _routes_env() as env, _small_parts():
        body = _jsonl(12 * MIB)
        sid = fresh(env, body)
        target = env.storage_for(sid)
        uid = next(iter(env.fake.uploads))
        env.fake.inject_part(uid, 1, body[: 5 * MIB])
        env.fake.inject_part(uid, 3, body[10 * MIB :])
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "a partial upload -> 409 upload_incomplete, session open, no dataset",
            reply.status == 409
            and reply.detail == "upload_incomplete"
            and env.sessions()[0].status == "open"
            and not env.datasets()
            and target.object_key not in env.fake.objects,
        )

    with _routes_env() as env, _small_parts():
        body = _jsonl(12 * MIB)
        sid = fresh(env, body)
        uid = next(iter(env.fake.uploads))
        env.fake.inject_part(uid, 1, b"{" + b"x" * (5 * MIB))
        env.fake.inject_part(uid, 2, body[5 * MIB : 10 * MIB])
        env.fake.inject_part(uid, 3, body[10 * MIB :])
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "an oversize part -> 422 part_size_mismatch, session rejected, nothing stored",
            reply.status == 422
            and reply.detail == "part_size_mismatch"
            and env.sessions()[0].status == "rejected"
            and not env.datasets()
            and not env.fake.uploads
            and not env.fake.objects,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body)
        _put_all(env, sid, body)
        env.world.status = "revoked"
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "source revoked mid-upload -> 422 source_revoked",
            reply.status == 422 and reply.detail == "source_revoked",
        )
        check(
            f"{CID}-R8",
            "(revoked) session rejected, object deleted, no dataset",
            env.sessions()[0].status == "rejected"
            and env.sessions()[0].rejection_reason == "source_revoked"
            and not env.datasets()
            and not env.fake.objects,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body)
        _put_all(env, sid, body)
        env.fake.fail.add("head")
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "a storage outage -> 503 storage_unavailable",
            reply.status == 503 and reply.detail == "storage_unavailable",
        )
        check(
            f"{CID}-R8",
            "(outage) the session is NOT rejected and no dataset exists",
            env.sessions()[0].status == "open" and not env.datasets(),
        )
        env.fake.fail.clear()
        check(
            f"{CID}-R8",
            "(outage) once storage is back the same session finalizes",
            fin(env, sid).status == 201,
        )
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body)
        env.settings = None
        reply = fin(env, sid)
        check(
            f"{CID}-R8",
            "not configured -> 503 storage_not_configured and no dataset",
            reply.status == 503
            and reply.detail == "storage_not_configured"
            and not env.datasets()
            and env.sessions()[0].status == "open",
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body)
        _put_all(env, sid, body)
        for label, payload in (
            ("extra field", {"dataset_name": "x", "object_key": "y"}),
            ("missing name", {}),
            ("blank name", {"dataset_name": "   "}),
            ("name is a list", {"dataset_name": ["x"]}),
            ("name is a number", {"dataset_name": 5}),
        ):
            reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", payload)
            check(
                f"{CID}-R8",
                f"{label}: 400 invalid_body",
                reply.status == 400 and reply.detail == "invalid_body",
            )
        reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body("n" * 201))
        check(f"{CID}-R8", "an over-long name is a 400", reply.status == 400 and not env.datasets())
        padded = _call(
            env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body(" " + "n" * 201 + " ")
        )
        check(f"{CID}-R8", "length is measured after trimming, not before", padded.status == 400)
        check(
            f"{CID}-R8",
            "none of it touched the session or storage",
            env.sessions()[0].status == "open" and len(env.fake.uploads) == 1,
        )
        exact = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body("n" * 200))
        check(
            f"{CID}-R8",
            "a 200-character name is allowed",
            exact.status == 201 and len(exact.json()["name"]) == 200,
        )
        check(
            f"{CID}-R8",
            "finalize of an unknown session: 404",
            _call(env.base, "POST", f"{_col()}/{uuid.uuid4()}/finalize", _finalize_body()).status
            == 404,
        )
        check(
            f"{CID}-R8",
            "finalize through another project: 404",
            _call(
                env.base, "POST", f"{_col(_OTHER_PROJECT)}/{sid}/finalize", _finalize_body()
            ).status
            == 404,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body)
        _put_all(env, sid, body)
        original = routes.record_upload_verified_and_create_dataset

        def conflict(*a: Any, **k: Any) -> Any:
            raise store.DatasetVersionConflictError("race")

        routes.record_upload_verified_and_create_dataset = conflict
        try:
            reply = fin(env, sid)
        finally:
            routes.record_upload_verified_and_create_dataset = original
        check(
            f"{CID}-R8",
            "a version race -> 409 dataset_version_conflict, session still open",
            reply.status == 409
            and reply.detail == "dataset_version_conflict"
            and env.sessions()[0].status == "open",
        )
        check(
            f"{CID}-R8",
            "(race) the retry succeeds from the stored object",
            fin(env, sid).status == 201,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = fresh(env, body)
        _put_all(env, sid, body)
        original = routes.record_upload_verified_and_create_dataset

        def mismatch(*a: Any, **k: Any) -> Any:
            raise store.UploadVerificationError(("sha256",))

        routes.record_upload_verified_and_create_dataset = mismatch
        try:
            reply = fin(env, sid)
        finally:
            routes.record_upload_verified_and_create_dataset = original
        check(
            f"{CID}-R8",
            "a store-level mismatch -> 422 verification_mismatch and the session is rejected",
            reply.status == 422
            and reply.detail == "verification_mismatch"
            and env.sessions()[0].status == "rejected"
            and not env.fake.objects,
        )


def test_r9_held_out_is_hidden_from_ml_engineers() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body, principal="admin", purpose="held_out_evaluation")["id"]
        item = f"{_col()}/{sid}"
        for method, path, payload in (
            ("GET", item, None),
            ("GET", item + "/parts", None),
            ("POST", item + "/part-urls", {"part_numbers": [1]}),
            ("POST", item + "/finalize", _finalize_body()),
        ):
            hidden = _call(env.base, method, path, payload, "ml")
            missing = _call(env.base, method, path.replace(sid, str(uuid.uuid4())), payload, "ml")
            check(
                f"{CID}-R9",
                f"{method} {path.rsplit('/', 1)[-1][:10]}: ml_engineer: 404, same as a missing id",
                hidden.status == 404 and hidden.body == missing.body,
            )
        check(
            f"{CID}-R9",
            "the admin can use it",
            _call(env.base, "GET", item, None, "admin").status == 200,
        )
        _put_all(env, sid, body, "admin")
        reply = _call(env.base, "POST", item + "/finalize", _finalize_body("heldout"), "admin")
        check(
            f"{CID}-R9",
            "the admin finalizes a held-out dataset",
            reply.status == 201 and reply.json()["purpose"] == "held_out_evaluation",
        )
        check(
            f"{CID}-R9",
            "and an org admin can read the session",
            _call(env.base, "GET", item, None, "orgadmin").status == 200,
        )


def test_r10_nothing_leaks_the_storage_location() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        data = _open_session(env, body)
        sid = data["id"]
        key = env.storage_for(sid).object_key
        secret_texts: list[str] = [env.fake.SECRET, env.fake.ACCESS]
        replies = [
            _call(env.base, "GET", f"{_col()}/{sid}", None),
            _call(env.base, "GET", f"{_col()}/{sid}/parts", None),
            _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body()),  # incomplete
            _call(env.base, "POST", f"{_col()}/{sid}/part-urls", {"part_numbers": [0]}),
            _call(env.base, "POST", _col(), {"x": 1}),
        ]
        env.fake.fail.add("list_uploads")
        replies.append(_call(env.base, "POST", f"{_col()}/{sid}/part-urls", {"part_numbers": [1]}))
        replies.append(_call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body()))
        env.fake.fail.clear()
        for reply in replies:
            text = reply.body.decode()
            check(
                f"{CID}-R10",
                f"{reply.status} response carries no key, bucket or credential",
                key not in text
                and fake_bucket(env) not in text
                and not any(s in text for s in secret_texts)
                and "127.0.0.1" not in text,
            )
        grants = _call(
            env.base, "POST", f"{_col()}/{sid}/part-urls", {"part_numbers": [1]}
        ).body.decode()
        check(f"{CID}-R10", "the signing secret is never in a grant", env.fake.SECRET not in grants)
        check(
            f"{CID}-R10",
            "the access key id is the only credential material in a grant (Credential=)",
            env.fake.ACCESS in grants,
        )


# -- C: contract and wiring ----------------------------------------------------------


# -- M: pins for the behaviours the mutation gate found unpinned -----------------


class _RawS3:
    """A scripted HTTP server: every request is answered by `respond(request)`
    as `(status, headers, body)`; the requests are recorded for assertions."""

    def __init__(self, respond: Any) -> None:
        self.requests: list[SimpleNamespace] = []
        raw = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                parts = urllib.parse.urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                request = SimpleNamespace(
                    method=self.command,
                    path=parts.path,
                    query=dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True)),
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=self.rfile.read(length) if length else b"",
                )
                raw.requests.append(request)
                status, headers, body = respond(request)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = do_PUT = do_POST = do_DELETE = do_HEAD = _handle  # noqa: N815

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )

    def __enter__(self) -> _RawS3:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def client(self) -> s3.S3Client:
        return s3.S3Client(
            s3.S3Endpoint("http", "127.0.0.1", self.port),
            s3.S3Credentials("AK", "SK"),
            timeout_seconds=5.0,
        )


def _xml(body: str) -> tuple[int, dict[str, str], bytes]:
    return 200, {"Content-Type": "application/xml"}, body.encode()


def _storage_env(**over: str) -> dict[str, str]:
    env = {
        "SOVRA_UPLOAD_S3_ENDPOINT": "http://s3.example:9000",
        "SOVRA_UPLOAD_S3_BUCKET": "uploads",
        "SOVRA_UPLOAD_S3_ACCESS_KEY": "AK",
        "SOVRA_UPLOAD_S3_SECRET_KEY": "SK",
    }
    env.update(over)
    return env


def test_m1_storage_settings_are_trimmed_on_both_sides() -> None:
    padded = {k: f"  {v}  " for k, v in _storage_env(SOVRA_UPLOAD_S3_REGION="eu-west-1").items()}
    got = dos.UploadStorageSettings.from_env(padded)
    check(
        f"{CID}-M1",
        "every value is trimmed on both sides",
        got is not None
        and got.bucket == "uploads"
        and got.credentials == s3.S3Credentials("AK", "SK")
        and got.endpoint == s3.S3Endpoint("http", "s3.example", 9000, "eu-west-1"),
    )
    blank_region = dos.UploadStorageSettings.from_env(_storage_env(SOVRA_UPLOAD_S3_REGION="   \t "))
    check(
        f"{CID}-M1",
        "a blank region is the default region",
        blank_region is not None and blank_region.endpoint.region == "us-east-1",
    )
    trailing = dos.UploadStorageSettings.from_env(_storage_env(SOVRA_UPLOAD_S3_REGION="us-west-2 "))
    check(
        f"{CID}-M1",
        "trailing whitespace on the region is dropped",
        trailing is not None and trailing.endpoint.region == "us-west-2",
    )


def test_m1c_the_storage_client_is_built_from_the_settings_with_a_long_timeout() -> None:
    settings = dos.UploadStorageSettings.from_env(_storage_env())
    assert settings is not None
    client = settings.client()
    check(
        f"{CID}-M1",
        "the client uses the configured endpoint and credentials",
        client._endpoint == settings.endpoint and client._credentials == settings.credentials,
    )
    check(
        f"{CID}-M1",
        "reads of a large object get a 30 s timeout, not the 10 s client default",
        client._timeout == 30.0,
    )


def test_m1b_default_ports_follow_the_scheme() -> None:
    for endpoint, scheme, port in (
        ("https://s3.example", "https", 443),
        ("http://s3.example", "http", 80),
        ("HTTPS://s3.example", "https", 443),
        ("https://s3.example:8443", "https", 8443),
        ("http://s3.example:443", "http", 443),
    ):
        got = dos.UploadStorageSettings.from_env(_storage_env(SOVRA_UPLOAD_S3_ENDPOINT=endpoint))
        check(
            f"{CID}-M1",
            f"{endpoint} -> {scheme} port {port}",
            got is not None and got.endpoint.scheme == scheme and got.endpoint.port == port,
        )


def test_m1d_an_upload_is_found_by_its_exact_key_only() -> None:
    with FakeS3() as fake:
        settings = fake.settings()
        client = settings.client()
        body = _jsonl(3000)
        mixed = _target(body, key="tenants/AbC/projects/P/uploads/Source")
        lower = _target(body, key="tenants/abc/projects/p/uploads/source")
        mixed_id = dos.start_multipart(client, settings, mixed)
        check(
            f"{CID}-M1",
            "a key with upper-case letters finds its own upload",
            dos.find_upload_id(client, settings, mixed) == mixed_id,
        )
        check(
            f"{CID}-M1",
            "the lower-cased spelling of that key finds nothing",
            dos.find_upload_id(client, settings, lower) is None,
        )
        lower_id = dos.start_multipart(client, settings, lower)
        check(
            f"{CID}-M1",
            "two keys differing only by case keep their own uploads",
            dos.find_upload_id(client, settings, lower) == lower_id
            and dos.find_upload_id(client, settings, mixed) == mixed_id
            and lower_id != mixed_id,
        )


def test_m1e_malformed_part_entries_from_storage_are_an_outage() -> None:
    good = {"PartNumber": "1", "Size": "10", "ETag": '"abc"'}
    bad_entries = {
        "non-numeric part number": {**good, "PartNumber": "x"},
        "non-numeric size": {**good, "Size": "big"},
        "missing part number": {k: v for k, v in good.items() if k != "PartNumber"},
        "missing size": {k: v for k, v in good.items() if k != "Size"},
        "missing etag": {k: v for k, v in good.items() if k != "ETag"},
        "null size": {**good, "Size": None},
    }
    for label, entry in bad_entries.items():
        with FakeS3() as fake:
            target = _target(_jsonl(3000))
            client = _client_with(fake, list_parts=lambda b, k, u, _e=entry: [good, _e])
            settings = fake.settings()
            got = _capture(
                lambda c=client, t=target, st=settings: dos.list_uploaded_parts(c, st, t, "u")
            )
            check(
                f"{CID}-M1",
                f"a part entry with a {label} is an outage naming the problem",
                isinstance(got, dos.UploadStorageUnavailableError) and "malformed" in str(got),
            )
    with FakeS3() as fake:
        target = _target(_jsonl(3000))
        two = {"PartNumber": "2", "Size": "5", "ETag": '"b"'}
        client = _client_with(fake, list_parts=lambda b, k, u: [two, good])
        got = dos.list_uploaded_parts(client, fake.settings(), target, "u")
        check(
            f"{CID}-M1",
            "well-formed entries come back sorted by part number with the quotes stripped",
            [(p.part_number, p.size_bytes, p.etag) for p in got] == [(1, 10, "abc"), (2, 5, "b")],
        )


def test_m1f_an_unsupported_declared_type_is_rejected_before_anything_is_read() -> None:
    body = b"%PDF-1.4 " * 400
    for declared in ("application/pdf", "", "text/csvx", " "):
        with FakeS3() as fake:
            target = _target(body, content_type=declared)
            settings = fake.settings()
            dos.start_multipart(settings.client(), settings, target)
            fake.inject_part(next(iter(fake.uploads)), 1, body)
            fake.reset_calls()
            got = _outcome(fake, target)
            check(
                f"{CID}-M1",
                f"declared type {declared!r} -> unsupported_content_type",
                isinstance(got, dos.UploadRejection) and got.reason == "unsupported_content_type",
            )
            check(
                f"{CID}-M1",
                f"({declared!r}) the upload was never completed, probed or read",
                not any(fake.called(op) for op in ("complete", "head", "get", "list_parts")),
            )
            check(f"{CID}-M1", f"({declared!r}) nothing is left in storage", _gone(fake, target))


def test_m1g_a_failing_head_is_an_outage_and_a_missing_object_is_incomplete() -> None:
    body = _jsonl(3000)
    for label, error in (
        ("OSError", OSError("reset")),
        ("URLError", urllib.error.URLError("refused")),
        ("TimeoutError", TimeoutError("slow")),
        ("S3 500", s3.S3Error(500, "InternalError", "boom")),
        ("S3 403", s3.S3Error(403, "AccessDenied", "no")),
    ):
        with FakeS3() as fake:
            target = _target(body)
            _stored(fake, target, body)

            def failing_head(bucket: str, key: str, _error: Exception = error) -> dict[str, str]:
                raise _error

            got = _verify_with(fake, target, head_object=failing_head)
            check(
                f"{CID}-M1",
                f"{label} on the HEAD is an outage naming head_object",
                isinstance(got, dos.UploadStorageUnavailableError) and "head_object" in str(got),
            )
            check(f"{CID}-M1", f"({label}) the object was not read", not fake.called("get"))
            check(
                f"{CID}-M1",
                f"({label}) nothing was deleted on an outage",
                target.object_key in fake.objects,
            )
    with FakeS3() as fake:
        target = _target(body)
        got = _verify_with(fake, target)
        check(
            f"{CID}-M1",
            "no object at all (HEAD 404) -> UploadIncomplete naming part 1",
            isinstance(got, dos.UploadIncomplete) and got.missing_parts == (1,),
        )


def test_m1h_the_sniff_window_boundary_decides_whether_a_cut_character_is_an_error() -> None:
    window = dos.SNIFF_BYTES
    head = b'{"k":"'
    cases = (
        # (label, bytes, expected): the whole object is inside the window, so a
        # character cut at its very end is a real error ...
        (
            "exactly the window, cut character at the end",
            head + b"x" * (window - len(head) - 1) + b"\xc3",
            "content_type_mismatch",
        ),
        # ... but when the object goes on past the window, a character straddling
        # the window's edge is only an artefact of where we stopped reading.
        (
            "one byte past the window, character straddling the edge",
            head + b"x" * (window - len(head) - 1) + "\u00e9".encode(),
            None,
        ),
        (
            "exactly the window, whole characters",
            head + b"x" * (window - len(head) - 3) + b'"}\n',
            None,
        ),
    )
    for label, body, want in cases:
        with FakeS3() as fake:
            target = _target(body)
            _stored(fake, target, body)
            got = _outcome(fake, target)
            if want is None:
                check(f"{CID}-M1", f"{label}: verifies", isinstance(got, dos.VerifiedObject))
            else:
                check(
                    f"{CID}-M1",
                    f"{label}: {want}",
                    isinstance(got, dos.UploadRejection) and got.reason == want,
                )


def test_m11b_the_request_body_limit_is_inclusive() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        item = f"{_col()}/{sid}"
        limit = routes._MAX_BODY_BYTES

        def padded(total: int) -> bytes:
            core = json.dumps({"dataset_name": "x"}).encode()
            return core + b" " * (total - len(core))

        at = _call(env.base, "POST", item + "/finalize", padded(limit))
        over = _call(env.base, "POST", item + "/finalize", padded(limit + 1))
        check(
            f"{CID}-M11",
            "a body of exactly the limit is read, not refused as too large",
            at.status != 413 and at.detail == "upload_incomplete",
        )
        check(
            f"{CID}-M11",
            "one byte more is 413 request_too_large",
            over.status == 413 and over.detail == "request_too_large",
        )


def test_m2_content_types_are_normalized_exactly_and_sniffed_by_table() -> None:
    cases = {
        "  Application/JSONL ; charset=utf-8": "application/jsonl",
        "\ttext/csv\t": "text/csv",
        " text/csv": "text/csv",
        "text/csv ": "text/csv",
        "APPLICATION/JSON": "application/json",
        "application/x-ndjson;x=1": "application/x-ndjson",
        dos.XLSX_CONTENT_TYPE.upper(): dos.XLSX_CONTENT_TYPE,
        "text/csvx": None,
        "text/ csv": None,
        "": None,
        "application/pdf": None,
    }
    for raw, want in cases.items():
        check(
            f"{CID}-M2",
            f"normalize_content_type({raw!r}) is {want!r}",
            dos.normalize_content_type(raw) == want,
        )
    check(
        f"{CID}-M2",
        "a non-string declared type is unsupported",
        dos.normalize_content_type(None) is None
        and dos.normalize_content_type(b"text/csv") is None,
    )

    bom = b"\xef\xbb\xbf"
    sniff = [
        ("application/jsonl", b'  \r\n\t\x0b\x0c{"a":1}\n', True, None),
        ("application/jsonl", bom + b'  {"a":1}\n', True, None),
        ("application/jsonl", b"[1,2]\n", True, "content_type_mismatch"),
        ("application/x-ndjson", b'{"a":1}\n', True, None),
        ("application/x-ndjson", b"[1]\n", True, "content_type_mismatch"),
        ("application/json", b" \n[1,2]", True, None),
        ("application/json", b'{"a":1}', True, None),
        ("application/json", b"a,b\n", True, "content_type_mismatch"),
        ("text/csv", b"a,b\n1,2\n", True, None),
        ("text/csv", bom + b"  a,b\n", True, None),
        ("text/csv", b' {"a":1}\n', True, "content_type_mismatch"),
        ("text/csv", b" [1,2]\n", True, "content_type_mismatch"),
        ("text/csv", b" \n\t \r\n", True, "content_type_mismatch"),
        ("text/csv", b"", True, "content_type_mismatch"),
        ("application/jsonl", bom, True, "content_type_mismatch"),
        (dos.XLSX_CONTENT_TYPE, b"PK\x03\x04rest", False, None),
        (dos.XLSX_CONTENT_TYPE, b" PK\x03\x04", False, "content_type_mismatch"),
        ("TEXT/CSV", b"a,b\n", True, None),
        ("application/pdf", b"a,b\n", True, "unsupported_content_type"),
    ]
    for declared, head, complete, want in sniff:
        got = dos.sniff_content(declared, head, complete=complete)
        check(
            f"{CID}-M2",
            f"sniff {declared} over {head[:12]!r} is {want!r}, got {got!r}",
            got == want,
        )


def test_m3_the_declared_hash_is_compared_exactly() -> None:
    body = _jsonl(3000)
    for label, declared in (
        ("upper-case", _sha(body).upper()),
        ("padded", f" {_sha(body)} "),
        ("trailing newline", _sha(body) + "\n"),
    ):
        with FakeS3() as fake:
            target = _target(body, sha=declared)
            _open_and_upload(fake, target, body)
            got = _outcome(fake, target)
            check(
                f"{CID}-M3",
                f"a {label} declared hash is a mismatch, never a normalized match",
                isinstance(got, dos.UploadRejection) and got.reason == "sha256_mismatch",
            )
            check(f"{CID}-M3", f"({label}) nothing is left in storage", _gone(fake, target))
    with FakeS3() as fake:
        target = _target(body)
        _open_and_upload(fake, target, body)
        got = _verify(fake, target)
        check(
            f"{CID}-M3",
            "the verified hash is the hash storage holds",
            got.sha256 == _sha(body) and got.size_bytes == len(body),
        )


def _client_with(fake: FakeS3, **overrides: Any) -> s3.S3Client:
    client = fake.settings().client()
    for name, fn in overrides.items():
        setattr(client, name, fn)
    return client


def _stored(fake: FakeS3, target: dos.UploadTarget, body: bytes) -> None:
    fake.objects[target.object_key] = {"body": body, "content_type": target.declared_content_type}


def _verify_with(fake: FakeS3, target: dos.UploadTarget, **overrides: Any) -> Any:
    settings = fake.settings()
    client = _client_with(fake, **overrides)
    try:
        return dos.verify_and_complete(client, settings, target)
    except Exception as exc:  # noqa: BLE001 - the type is the result under test
        return exc


def test_m4_a_failed_cleanup_is_an_outage_never_a_rejection() -> None:
    body = _jsonl(3000)
    wrong = _sha(b"not the body")
    for label, error in (
        ("OSError", OSError("reset")),
        ("URLError", urllib.error.URLError("refused")),
        ("TimeoutError", TimeoutError("slow")),
        ("S3 500", s3.S3Error(500, "InternalError", "boom")),
    ):
        with FakeS3() as fake:
            target = _target(body, sha=wrong)
            _stored(fake, target, body)

            def failing_delete(bucket: str, key: str, _error: Exception = error) -> None:
                raise _error

            got = _verify_with(fake, target, delete_object=failing_delete)
            check(
                f"{CID}-M4",
                f"{label} while deleting a rejected object is an outage",
                isinstance(got, dos.UploadStorageUnavailableError) and "delete_object" in str(got),
            )
    with FakeS3() as fake:
        target = _target(body, sha=wrong)
        _stored(fake, target, body)

        def missing_delete(bucket: str, key: str) -> None:
            raise s3.S3Error(404, "NoSuchKey", "gone already")

        got = _verify_with(fake, target, delete_object=missing_delete)
        check(
            f"{CID}-M4",
            "a 404 while deleting is fine: the rejection stands",
            isinstance(got, dos.UploadRejection) and got.reason == "sha256_mismatch",
        )


def test_m5_head_and_stream_failures_are_outages_and_reading_stops_at_the_declared_size() -> None:
    body = _jsonl(3000)
    for label, head in (
        ("non-numeric", {"content-length": "abc"}),
        ("missing", {}),
        ("empty", {"content-length": ""}),
    ):
        with FakeS3() as fake:
            target = _target(body)
            _stored(fake, target, body)
            got = _verify_with(fake, target, head_object=lambda b, k, _h=head: _h)
            check(
                f"{CID}-M5",
                f"a {label} content-length is an outage naming head_object",
                isinstance(got, dos.UploadStorageUnavailableError) and "head_object" in str(got),
            )
            check(f"{CID}-M5", f"({label}) the object was not read", not fake.called("get"))

    for label, error in (
        ("OSError", OSError("reset")),
        ("URLError", urllib.error.URLError("refused")),
        ("TimeoutError", TimeoutError("slow")),
        ("S3Error", s3.S3Error(500, "InternalError", "boom")),
    ):
        with FakeS3() as fake:
            target = _target(body)
            _stored(fake, target, body)

            def dropping_stream(bucket: str, key: str, _error: Exception = error, _body=body):
                yield _body[:1000]
                raise _error

            got = _verify_with(fake, target, stream_object=dropping_stream)
            check(
                f"{CID}-M5",
                f"{label} while reading is an outage naming get_object",
                isinstance(got, dos.UploadStorageUnavailableError) and "get_object" in str(got),
            )
            check(f"{CID}-M5", f"({label}) the object is kept", target.object_key in fake.objects)

    with FakeS3() as fake:
        target = _target(body, size=3000)
        _stored(fake, target, body)
        pulled: list[int] = []

        def endless_stream(bucket: str, key: str):
            for _ in range(300):
                pulled.append(1)
                yield b"x" * 1000

        got = _verify_with(
            fake,
            target,
            head_object=lambda b, k: {"content-length": "3000"},
            stream_object=endless_stream,
        )
        check(
            f"{CID}-M5",
            "an object longer than declared is rejected as size_mismatch",
            isinstance(got, dos.UploadRejection) and got.reason == "size_mismatch",
        )
        check(
            f"{CID}-M5",
            f"reading stops right after the declared size ({len(pulled)} chunks pulled)",
            len(pulled) <= 4,
        )
        check(f"{CID}-M5", "the oversize object was removed", target.object_key not in fake.objects)


def test_m6_the_sniff_window_is_assembled_from_the_leading_chunks_only() -> None:
    def verify_chunks(content_type: str, chunks: list[bytes]) -> Any:
        whole = b"".join(chunks)
        with FakeS3() as fake:
            target = _target(whole, content_type=content_type)
            _stored(fake, target, whole)
            return _verify_with(fake, target, stream_object=lambda b, k: iter(chunks))

    got = verify_chunks("application/jsonl", [b"   ", b'{"k":1}\n', b'{"k":2}\n'])
    check(
        f"{CID}-M6",
        "a leading chunk of only whitespace is completed from the next chunk",
        isinstance(got, dos.VerifiedObject),
    )
    got = verify_chunks("text/csv", [b"a,b\n", b'{"looks":"like json"}\n', b"3,4\n"])
    check(
        f"{CID}-M6",
        "only the leading bytes decide: a later chunk starting with { does not",
        isinstance(got, dos.VerifiedObject),
    )
    got = verify_chunks("application/jsonl", [b"a,b\n", b'{"k":1}\n'])
    check(
        f"{CID}-M6",
        "a later chunk that looks right cannot rescue a wrong start",
        isinstance(got, dos.UploadRejection) and got.reason == "content_type_mismatch",
    )
    window = dos.SNIFF_BYTES
    got = verify_chunks("application/jsonl", [b" " * (window - 1), b'{"k":1}\n'])
    check(
        f"{CID}-M6",
        "the last byte of the window still counts",
        isinstance(got, dos.VerifiedObject),
    )
    got = verify_chunks("application/jsonl", [b" " * window, b'{"k":1}\n'])
    check(
        f"{CID}-M6",
        "a { just beyond the window does not count",
        isinstance(got, dos.UploadRejection) and got.reason == "content_type_mismatch",
    )
    big = _jsonl(2_500_000)
    with FakeS3() as fake:
        target = _target(b"c" + big[1:])
        _stored(fake, target, b"c" + big[1:])
        got = _outcome(fake, target)
        check(
            f"{CID}-M6",
            "on a real multi-chunk read the first chunk decides the type",
            isinstance(got, dos.UploadRejection) and got.reason == "content_type_mismatch",
        )


_STORAGE_ENV_NAMES = (
    "SOVRA_UPLOAD_S3_ENDPOINT",
    "SOVRA_UPLOAD_S3_BUCKET",
    "SOVRA_UPLOAD_S3_ACCESS_KEY",
    "SOVRA_UPLOAD_S3_SECRET_KEY",
    "SOVRA_UPLOAD_S3_REGION",
)


def test_m7_default_storage_factory_and_the_audit_actor() -> None:
    saved = {name: os.environ.get(name) for name in _STORAGE_ENV_NAMES}
    try:
        for name in _STORAGE_ENV_NAMES:
            os.environ.pop(name, None)
        check(
            f"{CID}-M7",
            "nothing configured: the default factory says so with None",
            routes._default_storage_factory() is None,
        )
        os.environ["SOVRA_UPLOAD_S3_BUCKET"] = "uploads"
        try:
            routes._default_storage_factory()
            partly: HTTPException | None = None
        except HTTPException as exc:
            partly = exc
        check(
            f"{CID}-M7",
            "partly configured: 503 storage_not_configured, never a half-working client",
            partly is not None
            and partly.status_code == 503
            and partly.detail == "storage_not_configured",
        )
        for name, value in _storage_env().items():
            os.environ[name] = value
        full = routes._default_storage_factory()
        check(
            f"{CID}-M7",
            "fully configured: the settings from the environment",
            full is not None and full.bucket == "uploads",
        )
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        check(f"{CID}-M7", "finalized", reply.status == 201)
        events = env.audit.events
        check(f"{CID}-M7", "two audit events were written", len(events) == 2)
        check(
            f"{CID}-M7",
            "every event is a user action by the authenticated subject",
            all(e["actor_type"] == "user" and e["actor_id"] == "ml" for e in events),
        )
        ids = [e["request_id"] for e in events]
        check(
            f"{CID}-M7",
            "each request carries its own 32-hex request id",
            all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids) and len(set(ids)) == len(ids),
        )


@contextlib.contextmanager
def _tampered(**fields: Any):
    """Serves the real record with some fields replaced -- values the database
    constraints would never hold, to show the route trusts only exact values."""
    real = routes.get_upload_session

    def patched(session: Any, context: Any, project_id: Any, upload_session_id: Any) -> Any:
        record = real(session, context, project_id, upload_session_id)
        for name, value in fields.items():
            set_committed_value(record, name, value)
        return record

    routes.get_upload_session = patched
    try:
        yield
    finally:
        routes.get_upload_session = real


def test_m8_only_an_exact_open_status_is_open_and_held_out_fails_closed() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        item = f"{_col()}/{sid}"
        for status in (" open", "open ", "OPEN", "Open", "opened", ""):
            with _tampered(status=status):
                env.fake.reset_calls()
                urls = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]})
                listing = _call(env.base, "GET", item + "/parts", None)
                fin = _call(env.base, "POST", item + "/finalize", _finalize_body())
            check(
                f"{CID}-M8",
                f"status {status!r} is not open: part-urls, listing and finalize -> 409",
                urls.status == 409
                and listing.status == 409
                and fin.status == 409
                and urls.detail == listing.detail == fin.detail == "upload_session_not_open",
            )
            check(f"{CID}-M8", f"status {status!r}: storage was not touched", not env.fake.calls)
            check(f"{CID}-M8", f"status {status!r}: no dataset", not env.datasets())

        for purpose in (" held_out_evaluation", "held_out_evaluation ", "HELD_OUT_EVALUATION"):
            with _tampered(purpose=purpose):
                hidden = _call(env.base, "GET", item, None, "ml")
                shown = _call(env.base, "GET", item, None, "admin")
            check(
                f"{CID}-M8",
                f"purpose {purpose!r} reads as held-out: hidden from an ML engineer",
                hidden.status == 404 and hidden.detail == "not_found",
            )
            check(
                f"{CID}-M8", f"purpose {purpose!r} stays visible to an admin", shown.status == 200
            )
        with _tampered(purpose="training"):
            check(
                f"{CID}-M8",
                "an ordinary purpose stays visible to an ML engineer",
                _call(env.base, "GET", item, None, "ml").status == 200,
            )

        for purpose in (" held_out_evaluation", "held_out_evaluation ", "HELD_OUT_EVALUATION"):
            env.fake.reset_calls()
            reply = _call(env.base, "POST", _col(), _create_body(body, purpose=purpose), "ml")
            check(
                f"{CID}-M8",
                f"creating with purpose {purpose!r} as an ML engineer -> 403 purpose_forbidden",
                reply.status == 403 and reply.detail == "purpose_forbidden",
            )
            check(f"{CID}-M8", f"({purpose!r}) nothing reached storage", not env.fake.calls)
        check(f"{CID}-M8", "no session was created by those refusals", len(env.sessions()) == 1)


def test_m9_dataset_names_are_trimmed_once_and_bounded() -> None:
    longest = store.DATASET_NAME_MAX_LENGTH
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        item = f"{_col()}/{sid}"
        bad_names: list[Any] = [
            "",
            "   ",
            "\t\n ",
            "x" * (longest + 1),
            "  " + "x" * (longest + 1) + "  ",
            123,
            None,
            ["a"],
            {"a": 1},
            True,
        ]
        for name in bad_names:
            env.fake.reset_calls()
            reply = _call(env.base, "POST", item + "/finalize", {"dataset_name": name})
            check(
                f"{CID}-M9",
                f"dataset_name {str(name)[:12]!r}.. -> 400 invalid_body before any storage work",
                reply.status == 400 and reply.detail == "invalid_body" and not env.fake.calls,
            )
        check(f"{CID}-M9", "no dataset after the refusals", not env.datasets())

        _put_all(env, sid, body)
        padded = "  " + "x" * longest + "  "
        reply = _call(env.base, "POST", item + "/finalize", {"dataset_name": padded})
        check(
            f"{CID}-M9",
            "a name of the longest length survives padding on both sides, trimmed",
            reply.status == 201 and reply.json()["name"] == "x" * longest,
        )
    for raw, want in ((" lead", "lead"), ("trail ", "trail"), ("\tboth\n", "both")):
        with _routes_env() as env:
            body = _jsonl(2000)
            sid = _open_session(env, body)["id"]
            _put_all(env, sid, body)
            reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", {"dataset_name": raw})
            check(
                f"{CID}-M9",
                f"{raw!r} is stored as {want!r}",
                reply.status == 201 and reply.json()["name"] == want,
            )


def test_m10_a_closed_session_never_reaches_storage() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        item = f"{_col()}/{sid}"
        check(
            f"{CID}-M10",
            "finalized once",
            _call(env.base, "POST", item + "/finalize", _finalize_body()).status == 201,
        )
        env.fake.reset_calls()
        urls = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]})
        listing = _call(env.base, "GET", item + "/parts", None)
        again = _call(env.base, "POST", item + "/finalize", _finalize_body("second"))
        check(
            f"{CID}-M10",
            "a verified session: part-urls, listing and a second finalize -> 409",
            urls.status == listing.status == again.status == 409,
        )
        check(f"{CID}-M10", "none of them touched storage", not env.fake.calls)
        check(f"{CID}-M10", "still exactly one dataset", len(env.datasets()) == 1)

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        item = f"{_col()}/{sid}"
        env.clock[0] = _NOW + timedelta(hours=25)
        first = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]})
        check(f"{CID}-M10", "the first request after expiry answers 410", first.status == 410)
        env.fake.reset_calls()
        urls = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]})
        fin = _call(env.base, "POST", item + "/finalize", _finalize_body())
        check(
            f"{CID}-M10",
            "an expired session: part-urls and finalize -> 409 without touching storage",
            urls.status == fin.status == 409 and not env.fake.calls,
        )


def test_m10b_no_url_is_issued_when_storage_has_lost_the_multipart_upload() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        item = f"{_col()}/{sid}"
        env.fake.uploads.clear()  # the upload vanished from storage; the session row is still open
        env.fake.reset_calls()
        reply = _call(env.base, "POST", item + "/part-urls", {"part_numbers": [1]})
        check(
            f"{CID}-M10",
            "an open session with no multipart upload in storage -> 409 upload_session_not_open",
            reply.status == 409 and reply.detail == "upload_session_not_open",
        )
        check(
            f"{CID}-M10",
            "no URL (and no placeholder upload id) was issued",
            b"http" not in reply.body and b"None" not in reply.body,
        )
        check(f"{CID}-M10", "nothing was written to storage", not env.fake.called("put_part"))
        check(f"{CID}-M10", "the session row is untouched", env.sessions()[0].status == "open")


def test_m11_the_route_refuses_bad_sizes_before_the_store_is_called() -> None:
    created: list[dict[str, Any]] = []
    real = routes.create_upload_session

    def spy(*args: Any, **kwargs: Any) -> Any:
        created.append(kwargs)
        return real(*args, **kwargs)

    routes.create_upload_session = spy
    try:
        with _routes_env() as env:
            body = _jsonl(2000)
            limit = routes.MAX_UPLOAD_BYTES
            for label, size in (
                ("zero", 0),
                ("negative", -1),
                ("one past the limit", limit + 1),
                ("a bool", True),
                ("a string", "2000"),
                ("a float", 2000.0),
            ):
                env.fake.reset_calls()
                reply = _call(
                    env.base, "POST", _col(), _create_body(body, declared_size_bytes=size)
                )
                check(
                    f"{CID}-M11",
                    f"declared size {label} -> 400 invalid_body",
                    reply.status == 400 and reply.detail == "invalid_body",
                )
                check(f"{CID}-M11", f"({label}) the store was not called", not created)
                check(f"{CID}-M11", f"({label}) storage was not touched", not env.fake.calls)
            exact = _call(env.base, "POST", _col(), _create_body(body, declared_size_bytes=limit))
            check(
                f"{CID}-M11",
                "exactly the limit is accepted",
                exact.status == 201 and len(created) == 1,
            )
            one = _call(env.base, "POST", _col(), _create_body(b"x", declared_size_bytes=1))
            check(f"{CID}-M11", "one byte is accepted", one.status == 201)
    finally:
        routes.create_upload_session = real


class _SeqClock:
    """`env.clock[0]` answers each value in turn, then repeats the last one."""

    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def __getitem__(self, index: int) -> datetime:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_m12_finalize_maps_store_outcomes_after_storage_has_verified() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        env.clock = _SeqClock(_NOW, _NOW + timedelta(hours=25))
        reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        check(
            f"{CID}-M12",
            "expiring between the storage check and the database write -> 410",
            reply.status == 410 and reply.detail == "upload_expired",
        )
        check(
            f"{CID}-M12",
            "no dataset, the session is expired and storage is cleaned",
            not env.datasets()
            and env.sessions()[0].status == "expired"
            and not env.fake.uploads
            and not env.fake.objects,
        )

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        real = routes.verify_and_complete

        def closing_after_verify(*args: Any, **kwargs: Any) -> Any:
            verified = real(*args, **kwargs)
            with Session(env.engine) as other:
                other.query(store.DatasetUploadSession).update({"status": "rejected"})
                other.commit()
            return verified

        routes.verify_and_complete = closing_after_verify
        try:
            reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        finally:
            routes.verify_and_complete = real
        check(
            f"{CID}-M12",
            "another request closing the session first -> 409 upload_session_not_open",
            reply.status == 409 and reply.detail == "upload_session_not_open",
        )
        check(f"{CID}-M12", "and no dataset appears", not env.datasets())

    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        real_store = routes.record_upload_verified_and_create_dataset

        def refusing(*args: Any, **kwargs: Any) -> Any:
            raise ValueError("refused by the store")

        routes.record_upload_verified_and_create_dataset = refusing
        try:
            reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        finally:
            routes.record_upload_verified_and_create_dataset = real_store
        check(
            f"{CID}-M12",
            "a value the store refuses -> 400 invalid_body, never a 500",
            reply.status == 400 and reply.detail == "invalid_body" and not env.datasets(),
        )


def test_m12b_a_rejection_for_a_session_someone_else_already_closed_is_a_conflict() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body, declared_sha256=_sha(b"not the body"))["id"]
        _put_all(env, sid, body)
        real = routes.verify_and_complete

        def closing_first(*args: Any, **kwargs: Any) -> Any:
            with Session(env.engine) as other:
                other.query(store.DatasetUploadSession).update({"status": "rejected"})
                other.commit()
            return real(*args, **kwargs)  # then raises the wrong-hash rejection

        routes.verify_and_complete = closing_first
        try:
            reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        finally:
            routes.verify_and_complete = real
        check(
            f"{CID}-M12",
            "a rejection that cannot be recorded because the session is already closed -> 409",
            reply.status == 409 and reply.detail == "upload_session_not_open",
        )
        check(f"{CID}-M12", "no dataset appears", not env.datasets())
        check(
            f"{CID}-M12",
            "the other request's verdict stands",
            env.sessions()[0].status == "rejected" and env.sessions()[0].rejection_reason is None,
        )


def test_m12c_a_session_that_vanishes_before_the_database_write_is_not_found() -> None:
    with _routes_env() as env:
        body = _jsonl(2000)
        sid = _open_session(env, body)["id"]
        _put_all(env, sid, body)
        real = routes.verify_and_complete

        def deleting_after_verify(*args: Any, **kwargs: Any) -> Any:
            verified = real(*args, **kwargs)
            with Session(env.engine) as other:
                other.query(store.DatasetUploadSession).delete()
                other.commit()
            return verified

        routes.verify_and_complete = deleting_after_verify
        try:
            reply = _call(env.base, "POST", f"{_col()}/{sid}/finalize", _finalize_body())
        finally:
            routes.verify_and_complete = real
        check(
            f"{CID}-M12",
            "a session gone before the write -> 404 not_found, never a 500",
            reply.status == 404 and reply.detail == "not_found",
        )
        check(f"{CID}-M12", "and no dataset appears", not env.datasets())


def test_m13_presign_bounds_and_signed_header_values_are_trimmed() -> None:
    check(
        f"{CID}-M13",
        "the 7-day maximum is allowed at its boundary",
        _signature(_presign(expires_seconds=604800, max_expires_seconds=604800)) != "",
    )
    for label, kw in (
        ("a ceiling above 7 days", {"expires_seconds": 1, "max_expires_seconds": 604801}),
        ("a zero ceiling", {"expires_seconds": 1, "max_expires_seconds": 0}),
        ("a zero lifetime", {"expires_seconds": 0, "max_expires_seconds": 300}),
        ("a lifetime above the ceiling", {"expires_seconds": 301, "max_expires_seconds": 300}),
        ("a negative ceiling", {"expires_seconds": 1, "max_expires_seconds": -1}),
        ("a bool lifetime", {"expires_seconds": True, "max_expires_seconds": 300}),
        ("a bool ceiling", {"expires_seconds": 1, "max_expires_seconds": True}),
    ):
        check(
            f"{CID}-M13", f"{label} is refused", _raises(lambda kw=kw: _presign(**kw), ValueError)
        )
    check(
        f"{CID}-M13",
        "the lifetime may equal the ceiling",
        _signature(_presign(expires_seconds=300, max_expires_seconds=300)) != "",
    )
    plain = _signature(_presign())
    for value in (" 100", "100 ", "\t100\t", "  100  "):
        check(
            f"{CID}-M13",
            f"a signed header value {value!r} signs as '100'",
            _signature(_presign(signed_headers={"content-length": value})) == plain,
        )
    check(
        f"{CID}-M13",
        "a signed header name is case-insensitive",
        _signature(_presign(signed_headers={"Content-Length": "100"})) == plain,
    )
    check(
        f"{CID}-M13",
        "a different value signs differently",
        _signature(_presign(signed_headers={"content-length": "101"})) != plain,
    )


def _is_malformed(exc: Any) -> bool:
    return isinstance(exc, s3.S3Error) and exc.code == "MalformedResponse"


def _capture(fn: Any) -> Any:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - the type is the result under test
        return exc
    return None


def test_m14_client_response_shapes_ranges_and_streaming() -> None:
    shapes = {
        "no UploadId": (
            "<InitiateMultipartUploadResult><Other>1</Other></InitiateMultipartUploadResult>"
        ),
        "text only": "<InitiateMultipartUploadResult>nope</InitiateMultipartUploadResult>",
        "empty": "<InitiateMultipartUploadResult/>",
    }
    for label, xml in shapes.items():
        with _RawS3(lambda r, xml=xml: _xml(xml)) as raw:
            got = _capture(lambda: raw.client().create_multipart_upload("b", "k"))
        check(
            f"{CID}-M14", f"a create answer with {label} is a MalformedResponse", _is_malformed(got)
        )
    with _RawS3(
        lambda r: _xml(
            "<InitiateMultipartUploadResult><UploadId>u-1</UploadId></InitiateMultipartUploadResult>"
        )
    ) as raw:
        check(
            f"{CID}-M14",
            "a well-formed answer gives the upload id",
            raw.client().create_multipart_upload("b", "k", content_type="text/csv") == "u-1"
            and raw.requests[0].headers.get("content-type") == "text/csv",
        )

    with _RawS3(lambda r: (200, {}, b"cdef")) as raw:
        client = raw.client()
        got = client.get_object_range("b", "k", 2, 5)
        check(
            f"{CID}-M14",
            "a range read returns the bytes and asks for exactly that range",
            got == b"cdef" and raw.requests[-1].headers.get("range") == "bytes=2-5",
        )
        client.get_object_range("b", "k", 0, 0)
        check(
            f"{CID}-M14",
            "the first byte alone is a valid range",
            raw.requests[-1].headers.get("range") == "bytes=0-0",
        )
        before = len(raw.requests)
        for first, last in ((3, 2), (-1, 4), (-5, -1)):
            check(
                f"{CID}-M14",
                f"range {first}-{last} is refused before any request",
                _raises(
                    lambda f=first, la=last: client.get_object_range("b", "k", f, la), ValueError
                ),
            )
        check(f"{CID}-M14", "no request was sent for those", len(raw.requests) == before)

    with _RawS3(lambda r: (200, {}, b"0123456789")) as raw:
        client = raw.client()
        check(
            f"{CID}-M14",
            "streaming yields chunk_size pieces and a short tail",
            list(client.stream_object("b", "k", chunk_size=4)) == [b"0123", b"4567", b"89"],
        )
        check(
            f"{CID}-M14",
            "a chunk size of one is allowed",
            len(list(client.stream_object("b", "k", chunk_size=1))) == 10,
        )
        for size in (0, -1):
            check(
                f"{CID}-M14",
                f"chunk_size {size} is refused, never an empty stream",
                _raises(
                    lambda n=size: list(client.stream_object("b", "k", chunk_size=n)), ValueError
                ),
            )


def test_m14b_a_failed_stream_carries_the_status_and_the_reason() -> None:
    for status, reason in ((404, "Not Found"), (500, "Internal Server Error"), (403, "Forbidden")):
        with _RawS3(lambda r, st=status: (st, {}, b"")) as raw:
            got = _capture(lambda: list(raw.client().stream_object("b", "k")))
        check(
            f"{CID}-M14",
            f"a {status} while streaming is an S3Error with that status and reason {reason!r}",
            isinstance(got, s3.S3Error) and got.status_code == status and got.message == reason,
        )


def _parts_xml(numbers: list[int], *, truncated: Any = None, marker: Any = None) -> str:
    items = "".join(
        f'<Part><PartNumber>{n}</PartNumber><ETag>"e{n}"</ETag><Size>5</Size></Part>'
        for n in numbers
    )
    tail = ""
    if truncated is not None:
        tail += f"<IsTruncated>{truncated}</IsTruncated>"
    if marker is not None:
        tail += f"<NextPartNumberMarker>{marker}</NextPartNumberMarker>"
    return f"<ListPartsResult>{items}{tail}</ListPartsResult>"


def test_m15_list_parts_pages_forward_only() -> None:
    with _RawS3(lambda r: _xml("<ListPartsResult/>")) as raw:
        check(
            f"{CID}-M15",
            "a list answer with no parts is an empty list",
            raw.client().list_parts("b", "k", "u") == [],
        )

    def paged(request: SimpleNamespace) -> Any:
        marker = request.query.get("part-number-marker")
        if marker == "0":
            return _xml(_parts_xml([1, 2], truncated="true", marker=2))
        return _xml(_parts_xml([3], truncated="false"))

    with _RawS3(paged) as raw:
        got = raw.client().list_parts("b", "k", "u-9")
        check(
            f"{CID}-M15",
            "two pages are joined in order",
            [p["PartNumber"] for p in got] == ["1", "2", "3"],
        )
        check(
            f"{CID}-M15",
            "the second page is requested from the marker the first gave",
            [r.query.get("part-number-marker") for r in raw.requests] == ["0", "2"]
            and all(r.query.get("uploadId") == "u-9" for r in raw.requests)
            and all(r.query.get("max-parts") == "1000" for r in raw.requests),
        )

    with _RawS3(
        lambda r: _xml(_parts_xml([1], truncated="True", marker=1))
        if r.query.get("part-number-marker") == "0"
        else _xml(_parts_xml([2], truncated="FALSE", marker=2))
    ) as raw:
        got = raw.client().list_parts("b", "k", "u")
        check(
            f"{CID}-M15",
            "IsTruncated is read case-insensitively and a final page ends the walk",
            [p["PartNumber"] for p in got] == ["1", "2"] and len(raw.requests) == 2,
        )

    bad_markers = {
        "repeats the start": ("0", None),
        "missing": (None, None),
        "empty": ("", None),
        "not a number": ("abc", None),
        "negative": ("-3", None),
    }
    for label, (marker, _) in bad_markers.items():
        seen: list[int] = []

        def never_ending(
            request: SimpleNamespace, marker: Any = marker, seen: list[int] = seen
        ) -> Any:
            seen.append(1)
            if len(seen) > 6:  # a client that keeps paging is stopped here, and fails the test
                return _xml(_parts_xml([9], truncated="false"))
            return _xml(_parts_xml([1], truncated="true", marker=marker))

        with _RawS3(never_ending) as raw:
            got = _capture(lambda: raw.client().list_parts("b", "k", "u"))
            check(
                f"{CID}-M15", f"a marker that is {label} is a MalformedResponse", _is_malformed(got)
            )
            check(
                f"{CID}-M15",
                f"({label}) the walk stopped after the first page",
                len(raw.requests) == 1,
            )

    def rewinding(request: SimpleNamespace) -> Any:
        if request.query.get("part-number-marker") == "0":
            return _xml(_parts_xml([1], truncated="true", marker=5))
        if len(rewound) > 5:
            return _xml(_parts_xml([9], truncated="false"))
        rewound.append(1)
        return _xml(_parts_xml([2], truncated="true", marker=3))

    rewound: list[int] = []
    with _RawS3(rewinding) as raw:
        got = _capture(lambda: raw.client().list_parts("b", "k", "u"))
        check(
            f"{CID}-M15", "a marker that goes backwards is a MalformedResponse", _is_malformed(got)
        )
        check(f"{CID}-M15", "the walk stopped on the backwards marker", len(raw.requests) == 2)


# -- X: XLSX container check (Section 8.1 steps 3-4, 67.3) ---------------------------

_XLSX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_MACRO_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"


def _content_types(part: str = "/xl/workbook.xml", content_type: str = _XLSX_MAIN) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="{part}" ContentType="{content_type}"/></Types>'
    ).encode()


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def _xlsx(*, content_types: bytes | None = None, extra: list[tuple[str, bytes]] | None = None):
    return _zip(
        [
            ("[Content_Types].xml", content_types or _content_types()),
            ("xl/workbook.xml", b"<workbook/>"),
            *(extra or []),
        ]
    )


def _xlsx_outcome(body: bytes) -> Any:
    with FakeS3() as fake:
        target = _target(body, content_type=dos.XLSX_CONTENT_TYPE)
        _open_and_upload(fake, target, body)
        got = _outcome(fake, target)
        gone = _gone(fake, target)
    return got, gone


def test_x1_xlsx_must_be_a_spreadsheet_package() -> None:
    import io
    import zipfile

    docx = _zip(
        [
            ("[Content_Types].xml", _content_types("/word/document.xml", _DOCX_MAIN)),
            ("word/document.xml", b"<document/>"),
        ]
    )
    only_word = _zip([("word/document.xml", b"<document/>")])
    plain_zip = _zip([("a.txt", b"hello")])
    no_workbook_part = _zip([("[Content_Types].xml", _content_types()), ("xl/other.xml", b"<a/>")])
    wrong_declared = _xlsx(content_types=_content_types(content_type=_DOCX_MAIN))
    undeclared = _xlsx(content_types=_content_types(part="/xl/other.xml"))
    not_xml = _xlsx(content_types=b"this is not xml")
    good = _xlsx()
    cases = [
        ("a docx", docx, "content_type_mismatch"),
        ("a zip holding only word/document.xml", only_word, "content_type_mismatch"),
        ("a plain zip", plain_zip, "content_type_mismatch"),
        ("PK-prefixed zero bytes", b"PK\x03\x04" + b"\x00" * 200, "content_type_mismatch"),
        ("PK-prefixed junk", b"PK\x03\x04" + b"junk" * 100, "content_type_mismatch"),
        ("a truncated xlsx", good[: len(good) // 2], "content_type_mismatch"),
        ("an xlsx cut before its directory", good[:-30], "content_type_mismatch"),
        (
            "[Content_Types].xml without the workbook part",
            no_workbook_part,
            "content_type_mismatch",
        ),
        ("a workbook declared as another type", wrong_declared, "content_type_mismatch"),
        ("a workbook part never declared", undeclared, "content_type_mismatch"),
        ("[Content_Types].xml that is not XML", not_xml, "content_type_mismatch"),
        (
            "a macro-enabled workbook",
            _xlsx(content_types=_content_types(content_type=_MACRO_MAIN)),
            "unsafe_archive",
        ),
        (
            "an entity declaration",
            _xlsx(content_types=b'<!DOCTYPE t [<!ENTITY a "aaaa">]>' + _content_types()),
            "unsafe_archive",
        ),
        ("a path-traversal member", _xlsx(extra=[("../evil.xml", b"x")]), "unsafe_archive"),
        ("an absolute member", _xlsx(extra=[("/abs.xml", b"x")]), "unsafe_archive"),
        ("a backslash member", _xlsx(extra=[("a\\..\\b.xml", b"x")]), "unsafe_archive"),
    ]
    for label, body, reason in cases:
        got, gone = _xlsx_outcome(body)
        check(
            f"{CID}-X1",
            f"{label} is refused as {reason}",
            isinstance(got, dos.UploadRejection) and got.reason == reason,
        )
        check(f"{CID}-X1", f"({label}) nothing is left in storage", gone)
    got, _ = _xlsx_outcome(good)
    check(
        f"{CID}-X1",
        "a real spreadsheet package verifies",
        isinstance(got, dos.VerifiedObject) and got.size_bytes == len(good),
    )
    # A multi-sheet package with many members still verifies.
    many = _xlsx(extra=[(f"xl/worksheets/sheet{i}.xml", b"<w/>") for i in range(200)])
    got, _ = _xlsx_outcome(many)
    check(f"{CID}-X1", "a package with 200 sheets verifies", isinstance(got, dos.VerifiedObject))
    # Duplicate member names are refused (the reader would use the last one).
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", _content_types())
        archive.writestr("xl/workbook.xml", b"<workbook/>")
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            archive.writestr("[Content_Types].xml", _content_types(content_type=_DOCX_MAIN))
    got, _ = _xlsx_outcome(buffer.getvalue())
    check(
        f"{CID}-X1",
        "duplicate member names are refused",
        isinstance(got, dos.UploadRejection) and got.reason == "unsafe_archive",
    )


def test_x2_xlsx_bounds_and_storage_outage() -> None:
    saved = (
        dos.MAX_XLSX_ENTRIES,
        dos.MAX_XLSX_UNCOMPRESSED_BYTES,
        dos.MAX_XLSX_CONTENT_TYPES_BYTES,
    )
    try:
        dos.MAX_XLSX_ENTRIES = 5
        got, _ = _xlsx_outcome(_xlsx(extra=[(f"xl/s{i}.xml", b"<w/>") for i in range(4)]))
        check(
            f"{CID}-X2",
            "more members than the bound is refused",
            isinstance(got, dos.UploadRejection) and got.reason == "unsafe_archive",
        )
        got, _ = _xlsx_outcome(_xlsx(extra=[(f"xl/s{i}.xml", b"<w/>") for i in range(3)]))
        check(
            f"{CID}-X2",
            "exactly the bound verifies",
            isinstance(got, dos.VerifiedObject),
        )
        dos.MAX_XLSX_ENTRIES = saved[0]
        dos.MAX_XLSX_UNCOMPRESSED_BYTES = 100
        got, _ = _xlsx_outcome(_xlsx(extra=[("xl/big.xml", b"a" * 5000)]))
        check(
            f"{CID}-X2",
            "a decompressed size over the bound is refused",
            isinstance(got, dos.UploadRejection) and got.reason == "unsafe_archive",
        )
        dos.MAX_XLSX_UNCOMPRESSED_BYTES = saved[1]
        dos.MAX_XLSX_CONTENT_TYPES_BYTES = 100
        got, _ = _xlsx_outcome(_xlsx())
        check(
            f"{CID}-X2",
            "an oversized [Content_Types].xml is refused",
            isinstance(got, dos.UploadRejection) and got.reason == "unsafe_archive",
        )
    finally:
        (
            dos.MAX_XLSX_ENTRIES,
            dos.MAX_XLSX_UNCOMPRESSED_BYTES,
            dos.MAX_XLSX_CONTENT_TYPES_BYTES,
        ) = saved
    # A ranged read failing is an outage, never a rejection or a success.
    body = _xlsx()
    with FakeS3() as fake:
        target = _target(body, content_type=dos.XLSX_CONTENT_TYPE)
        _open_and_upload(fake, target, body)
        settings = fake.settings()
        client = settings.client()
        original = client.get_object_range

        def failing(*args: Any, **kwargs: Any) -> bytes:
            raise s3.S3Error(503, "SlowDown", "slow down")

        client.get_object_range = failing  # type: ignore[method-assign]
        try:
            dos.verify_and_complete(client, settings, target)
            outage: Any = None
        except Exception as exc:  # noqa: BLE001
            outage = exc
        client.get_object_range = original  # type: ignore[method-assign]
        check(
            f"{CID}-X2",
            "a failing ranged read is a storage outage",
            isinstance(outage, dos.UploadStorageUnavailableError),
        )
        check(
            f"{CID}-X2",
            "the object is kept on an outage (the session stays open)",
            target.object_key in fake.objects,
        )


def test_x3_the_unsigned_refusal_check_accepts_only_400_403_or_a_drop() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ftf0501_s3_integration",
        ROOT / "tests" / "integration" / "test_ftf_0501_direct_upload_s3.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for status in (400, 403, module.DROPPED):
        check(
            f"{CID}-X3",
            f"an unsigned request answered {status} is a refusal",
            module._refused_unsigned(status),
        )
    for status in (200, 204, 404, 500, 503):
        check(
            f"{CID}-X3",
            f"an unsigned request answered {status} is not a refusal",
            not module._refused_unsigned(status),
        )
    check(f"{CID}-X3", "every other refusal stays strict: 400 is not one", not module._refused(400))
    check(
        f"{CID}-X3",
        "403 and a dropped connection stay refusals",
        module._refused(403) and module._refused(module.DROPPED),
    )


# -- S: abandoned multipart upload sweeper (Section 67.5, 24 h) -----------------------


def _open_aged(fake: FakeS3, initiated: str | None, *, key: str | None = None) -> str:
    target = _target(b"x", key=key)
    settings = fake.settings()
    upload_id = dos.start_multipart(settings.client(), settings, target)
    if initiated is None:
        fake.uploads[upload_id].pop("initiated", None)
    else:
        fake.uploads[upload_id]["initiated"] = initiated
    return upload_id


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _listing_of(count: int) -> Any:
    def listing(bucket: str, prefix: str) -> list[dict[str, str]]:
        return [{"Key": f"x/{i}", "UploadId": str(i)} for i in range(count)]

    return listing


def test_s1_the_sweeper_aborts_only_uploads_older_than_24_hours() -> None:
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    with FakeS3() as fake:
        old = _open_aged(fake, _iso(now - timedelta(hours=25)))
        very_old = _open_aged(fake, _iso(now - timedelta(days=30)))
        edge = _open_aged(fake, _iso(now - timedelta(hours=24)))
        fresh = _open_aged(fake, _iso(now - timedelta(hours=23, minutes=59)))
        undated = _open_aged(fake, None)
        garbage = _open_aged(fake, "yesterday-ish")
        naive = _open_aged(fake, "2026-10-01T00:00:00")
        foreign = _open_aged(fake, _iso(now - timedelta(days=9)), key="tenants/not-a-session/x")
        other = _open_aged(
            fake, _iso(now - timedelta(days=9)), key=f"tenants/{uuid.uuid4()}/projects/p/source"
        )
        settings = fake.settings()
        result = sweeper.sweep_abandoned_uploads(settings.client(), settings, now=now)
        check(f"{CID}-S1", "two stale uploads are aborted", result.aborted == 2)
        check(
            f"{CID}-S1",
            "the report counts every other upload exactly",
            (result.listed, result.kept, result.undated, result.foreign) == (9, 2, 3, 2)
            and not result.more_may_remain,
        )
        left = set(fake.uploads)
        check(
            f"{CID}-S1",
            "the 25 h and 30 day uploads are gone, all others remain",
            old not in left
            and very_old not in left
            and {edge, fresh, undated, garbage, naive, foreign, other} <= left,
        )
        check(
            f"{CID}-S1",
            "exactly 24 h old is kept (older than, not at)",
            edge in left,
        )
        again = sweeper.sweep_abandoned_uploads(settings.client(), settings, now=now)
        check(f"{CID}-S1", "a second sweep has nothing left to abort", again.aborted == 0)
        smaller = sweeper.sweep_abandoned_uploads(
            settings.client(), settings, now=now, max_age=timedelta(hours=1)
        )
        check(
            f"{CID}-S1",
            "a shorter max_age ages out the uploads inside 24 h",
            smaller.aborted == 2 and edge not in fake.uploads and fresh not in fake.uploads,
        )
    # Negative on the real default clock: an upload that was just started survives.
    with FakeS3() as fake:
        started = _open_aged(fake, _iso(datetime.now(UTC)))
        settings = fake.settings()
        result = sweeper.sweep_abandoned_uploads(settings.client(), settings)
        check(
            f"{CID}-S1",
            "with the default clock a brand-new upload is never aborted",
            result.aborted == 0 and started in fake.uploads,
        )


def test_s2_the_sweeper_validates_arguments_and_never_hides_an_outage() -> None:
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    with FakeS3() as fake:
        settings = fake.settings()
        client = settings.client()
        for label, kwargs, error in (
            ("a naive now", {"now": datetime(2026, 10, 8, 12, 0, 0)}, ValueError),
            ("a zero max_age", {"now": now, "max_age": timedelta(0)}, ValueError),
            ("a negative max_age", {"now": now, "max_age": timedelta(hours=-1)}, ValueError),
            ("a max_age over 24 h", {"now": now, "max_age": timedelta(hours=25)}, ValueError),
        ):
            try:
                sweeper.sweep_abandoned_uploads(client, settings, **kwargs)
                got: Any = None
            except Exception as exc:  # noqa: BLE001
                got = exc
            check(f"{CID}-S2", f"{label} is refused", isinstance(got, error))
        # max_age of exactly 24 h is allowed.
        sweeper.sweep_abandoned_uploads(client, settings, now=now, max_age=timedelta(hours=24))

        _open_aged(fake, _iso(now - timedelta(days=2)))
        fake.fail.add("list_uploads")
        try:
            sweeper.sweep_abandoned_uploads(client, settings, now=now)
            got = None
        except Exception as exc:  # noqa: BLE001
            got = exc
        check(
            f"{CID}-S2",
            "a failing listing is a storage outage, not an empty sweep",
            isinstance(got, dos.UploadStorageUnavailableError),
        )
        fake.fail.clear()
        fake.fail.add("abort")
        try:
            sweeper.sweep_abandoned_uploads(client, settings, now=now)
            got = None
        except Exception as exc:  # noqa: BLE001
            got = exc
        check(
            f"{CID}-S2",
            "a failing abort is a storage outage, never counted as aborted",
            isinstance(got, dos.UploadStorageUnavailableError) and len(fake.uploads) == 1,
        )
        fake.fail.clear()

        def vanished(*args: Any, **kwargs: Any) -> None:
            raise s3.S3Error(404, "NoSuchUpload", "gone")

        client.abort_multipart_upload = vanished  # type: ignore[method-assign]
        result = sweeper.sweep_abandoned_uploads(client, settings, now=now)
        check(
            f"{CID}-S2",
            "an upload that vanished before its abort (404) counts as already gone",
            result.aborted == 1,
        )

        def unreachable(*args: Any, **kwargs: Any) -> None:
            raise ConnectionResetError("reset")

        client.abort_multipart_upload = unreachable  # type: ignore[method-assign]
        try:
            sweeper.sweep_abandoned_uploads(client, settings, now=now)
            got = None
        except Exception as exc:  # noqa: BLE001
            got = exc
        check(
            f"{CID}-S2",
            "a connection failure during an abort is a storage outage",
            isinstance(got, dos.UploadStorageUnavailableError),
        )
        for count, expected in ((999, False), (1000, True)):
            client.list_multipart_uploads = _listing_of(count)  # type: ignore[method-assign]
            result = sweeper.sweep_abandoned_uploads(client, settings, now=now)
            check(
                f"{CID}-S2",
                f"{count} listed uploads -> more_may_remain is {expected}",
                result.more_may_remain is expected and result.foreign == count,
            )


def test_s3_the_sweeper_entry_point_reports_through_its_exit_code() -> None:
    now_old = _iso(datetime.now(UTC) - timedelta(days=3))
    names = (
        "SOVRA_UPLOAD_S3_ENDPOINT",
        "SOVRA_UPLOAD_S3_BUCKET",
        "SOVRA_UPLOAD_S3_ACCESS_KEY",
        "SOVRA_UPLOAD_S3_SECRET_KEY",
        "SOVRA_UPLOAD_S3_REGION",
    )
    saved = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        check(f"{CID}-S3", "an unconfigured sweeper exits 2", sweeper.main() == 2)
        with FakeS3() as fake:
            os.environ["SOVRA_UPLOAD_S3_ENDPOINT"] = f"http://127.0.0.1:{fake.port}"
            os.environ["SOVRA_UPLOAD_S3_BUCKET"] = fake.BUCKET
            os.environ["SOVRA_UPLOAD_S3_ACCESS_KEY"] = fake.ACCESS
            os.environ["SOVRA_UPLOAD_S3_SECRET_KEY"] = fake.SECRET
            stale = _open_aged(fake, now_old)
            fake.fail.add("list_uploads")
            check(f"{CID}-S3", "a storage failure exits 1", sweeper.main() == 1)
            fake.fail.clear()
            check(
                f"{CID}-S3",
                "a completed sweep exits 0 and aborts the stale upload",
                sweeper.main() == 0 and stale not in fake.uploads,
            )
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_x4_stored_object_file_and_xlsx_boundaries() -> None:
    body = _xlsx()
    with FakeS3() as fake:
        target = _target(body, content_type=dos.XLSX_CONTENT_TYPE)
        _open_and_upload(fake, target, body)
        _verify(fake, target)  # completes the multipart upload
        settings = fake.settings()
        client = settings.client()
        fetches: list[tuple[int, int]] = []
        original = client.get_object_range

        def counting(bucket: str, key: str, first: int, last: int) -> bytes:
            fetches.append((first, last))
            return original(bucket, key, first, last)

        client.get_object_range = counting  # type: ignore[method-assign]
        saved_window = dos._XLSX_RANGE_READ_BYTES
        dos._XLSX_RANGE_READ_BYTES = 16
        try:
            stored = dos._StoredObjectFile(client, settings, target.object_key, len(body))
            check(
                f"{CID}-X4",
                "the stored view is readable and seekable",
                stored.readable() and stored.seekable(),
            )
            check(f"{CID}-X4", "a new view starts at 0", stored.tell() == 0)
            closed_view = dos._StoredObjectFile(client, settings, target.object_key, len(body))
            closed_view.close()
            for name in ("readable", "seekable"):
                try:
                    getattr(closed_view, name)()
                    closed_refused = False
                except ValueError:
                    closed_refused = True
                check(f"{CID}-X4", f"{name}() on a closed view raises ValueError", closed_refused)
            check(
                f"{CID}-X4",
                "read(0) is empty and fetches nothing",
                stored.read(0) == b"" and fetches == [],
            )
            check(
                f"{CID}-X4",
                "read(4) returns the first 4 bytes",
                stored.read(4) == body[:4] and stored.tell() == 4,
            )
            check(f"{CID}-X4", "one 16-byte window was fetched", fetches == [(0, 15)])
            stored.seek(0)
            check(
                f"{CID}-X4",
                "re-reading inside the window does not refetch",
                stored.read(8) == body[:8] and len(fetches) == 1,
            )
            stored.seek(10)
            check(
                f"{CID}-X4",
                "a read across the window end is exact",
                stored.read(20) == body[10:30] and stored.tell() == 30,
            )
            stored.seek(16)
            check(
                f"{CID}-X4",
                "a read starting exactly at the window end refetches",
                stored.read(3) == body[16:19],
            )
            stored.seek(0)
            check(f"{CID}-X4", "read(-1) reads to the end", stored.read(-1) == body)
            stored.seek(0)
            check(f"{CID}-X4", "read(None) reads to the end", stored.read(None) == body)  # type: ignore[arg-type]
            check(
                f"{CID}-X4",
                "a read at the end is empty",
                stored.read(5) == b"" and stored.seek(0, 2) == len(body) and stored.read() == b"",
            )
            stored.seek(-3, 2)
            check(f"{CID}-X4", "seek from the end", stored.read() == body[-3:])
            stored.seek(2)
            stored.seek(3, 1)
            check(f"{CID}-X4", "seek relative to the position", stored.tell() == 5)
            check(
                f"{CID}-X4",
                "a read past the end is clipped",
                dos._StoredObjectFile(client, settings, target.object_key, len(body)).read(
                    len(body) + 100
                )
                == body,
            )
        finally:
            dos._XLSX_RANGE_READ_BYTES = saved_window

    import io as _io
    import zipfile as _zf

    sizes = sum(i.file_size for i in _zf.ZipFile(_io.BytesIO(body)).infolist())
    ct_len = len(_content_types())
    saved = (dos.MAX_XLSX_UNCOMPRESSED_BYTES, dos.MAX_XLSX_CONTENT_TYPES_BYTES)
    try:
        dos.MAX_XLSX_UNCOMPRESSED_BYTES = sizes
        got, _ = _xlsx_outcome(body)
        check(
            f"{CID}-X4",
            "exactly the uncompressed bound verifies",
            isinstance(got, dos.VerifiedObject),
        )
        dos.MAX_XLSX_UNCOMPRESSED_BYTES = sizes - 1
        got, _ = _xlsx_outcome(body)
        check(
            f"{CID}-X4",
            "one byte over the uncompressed bound is refused",
            isinstance(got, dos.UploadRejection) and got.reason == "unsafe_archive",
        )
        dos.MAX_XLSX_UNCOMPRESSED_BYTES = saved[0]
        dos.MAX_XLSX_CONTENT_TYPES_BYTES = ct_len
        got, _ = _xlsx_outcome(body)
        check(
            f"{CID}-X4",
            "a [Content_Types].xml exactly at its bound verifies",
            isinstance(got, dos.VerifiedObject),
        )
        dos.MAX_XLSX_CONTENT_TYPES_BYTES = ct_len - 1
        got, _ = _xlsx_outcome(body)
        check(
            f"{CID}-X4",
            "one byte over the [Content_Types].xml bound is refused",
            isinstance(got, dos.UploadRejection) and got.reason == "unsafe_archive",
        )
    finally:
        dos.MAX_XLSX_UNCOMPRESSED_BYTES, dos.MAX_XLSX_CONTENT_TYPES_BYTES = saved
    for label, prefix in (
        ("a DOCTYPE alone", b"<!DOCTYPE t>"),
        ("an ENTITY alone", b'<!ENTITY a "b">'),
    ):
        got, _ = _xlsx_outcome(_xlsx(content_types=prefix + _content_types()))
        check(
            f"{CID}-X4",
            f"{label} is refused as unsafe_archive",
            isinstance(got, dos.UploadRejection) and got.reason == "unsafe_archive",
        )
    for label, declared in (
        ("a trailing space", _XLSX_MAIN + " "),
        ("an upper-cased type", _XLSX_MAIN.upper()),
        ("a leading space", " " + _XLSX_MAIN),
    ):
        got, _ = _xlsx_outcome(_xlsx(content_types=_content_types(content_type=declared)))
        check(
            f"{CID}-X4",
            f"{label} on the workbook content type is refused",
            isinstance(got, dos.UploadRejection) and got.reason == "content_type_mismatch",
        )


def test_s4_the_sweeper_parses_a_padded_initiation_time() -> None:
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    stale = _iso(now - timedelta(days=2))
    for label, text in (("a leading space", " " + stale), ("a trailing space", stale + " ")):
        with FakeS3() as fake:
            upload = _open_aged(fake, text)
            settings = fake.settings()
            result = sweeper.sweep_abandoned_uploads(settings.client(), settings, now=now)
            check(
                f"{CID}-S4",
                f"an initiation time with {label} is dated and aborted",
                result.aborted == 1 and result.undated == 0 and upload not in fake.uploads,
            )


def test_c1_interface_contract() -> None:
    spec = yaml.safe_load(INTERFACE_PATH.read_text())
    paths = spec["paths"]
    base = "/api/v1/organizations/{organization_id}/projects/{project_id}/upload-sessions"
    expected = {
        base: {"post"},
        base + "/{upload_session_id}": {"get"},
        base + "/{upload_session_id}/parts": {"get"},
        base + "/{upload_session_id}/part-urls": {"post"},
        base + "/{upload_session_id}/finalize": {"post"},
    }
    for path, verbs in expected.items():
        check(
            f"{CID}-C1",
            f"{path.rsplit('/', 1)[-1]} is declared with exactly {sorted(verbs)}",
            path in paths and set(paths[path]) - {"parameters"} == verbs,
        )
    schemas = spec["components"]["schemas"]
    for name in ("UploadSessionWithPlan", "UploadedParts", "PartUrlsRequest", "PartUrls"):
        check(
            f"{CID}-C1",
            f"{name} is a closed schema",
            name in schemas and schemas[name]["additionalProperties"] is False,
        )
    check(
        f"{CID}-C1",
        "UploadSessionWithPlan has exactly the keys the route returns",
        set(schemas["UploadSessionWithPlan"]["properties"]) == _SESSION_KEYS
        and set(schemas["UploadSessionWithPlan"]["required"]) == _SESSION_KEYS,
    )
    check(
        f"{CID}-C1",
        "no object key is ever declared in a response",
        all(
            "object_key" not in schemas[n]["properties"]
            for n in ("UploadSessionWithPlan", "UploadSession", "Dataset")
        ),
    )
    check(
        f"{CID}-C1",
        "part numbers are bounded to 100 per request",
        schemas["PartUrlsRequest"]["properties"]["part_numbers"]["maxItems"] == 100,
    )
    finalize = paths[base + "/{upload_session_id}/finalize"]["post"]["responses"]
    check(
        f"{CID}-C1",
        "finalize documents every outcome",
        {"201", "400", "401", "403", "404", "409", "410", "422", "503"} <= set(finalize),
    )
    check(
        f"{CID}-C1",
        "finalize requestBody is UploadFinalizeRequest",
        paths[base + "/{upload_session_id}/finalize"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"].endswith("/UploadFinalizeRequest"),
    )
    check(
        f"{CID}-C1",
        "create documents the held-out refusal and the outage",
        {"201", "400", "401", "403", "404", "409", "413", "503"}
        <= set(paths[base]["post"]["responses"]),
    )


def test_c2_router_is_wired_into_the_app() -> None:
    main = MAIN_PATH.read_text()
    check(
        f"{CID}-C2",
        "main.py imports build_direct_upload_router",
        "from app.datasets.direct_object_storage_upload_routes import build_direct_upload_router"
        in main,
    )
    check(
        f"{CID}-C2",
        "main.py includes it with the shared principal resolver and role lookup",
        re.search(
            r"app\.include_router\(\s*build_direct_upload_router\(\s*session_factory, principal_resolver=principal_resolver, role_lookup=role_lookup\s*\)\s*\)",  # noqa: E501
            main,
        )
        is not None,
    )
    env = _full_app_env()
    added = set(env) - set(os.environ)
    old = {k: os.environ[k] for k in env if k in os.environ}
    os.environ.update(env)
    try:
        import app.main as main_module

        app = main_module.create_app()
    finally:
        for key in added:
            os.environ.pop(key, None)
        os.environ.update(old)
    with _serve(app) as base:
        item = f"/api/v1/organizations/{_ORG_ID}/projects/{_PROJECT}/upload-sessions"
        for method, path in (
            ("POST", item),
            ("GET", f"{item}/{uuid.uuid4()}"),
            ("GET", f"{item}/{uuid.uuid4()}/parts"),
            ("POST", f"{item}/{uuid.uuid4()}/part-urls"),
            ("POST", f"{item}/{uuid.uuid4()}/finalize"),
        ):
            reply = _call(base, method, path, {} if method == "POST" else None, None)
            check(
                f"{CID}-C2",
                f"{method} {path.rsplit('/', 1)[-1][:12]} is mounted (401, not 404)",
                reply.status == 401,
            )


def _full_app_env() -> dict[str, str]:
    return {
        "SOVRA_ENVIRONMENT": "staging",
        "SOVRA_DATABASE_URL": "postgresql+psycopg://sovra_app:s3cr3t@db.internal:5432/sovra",
        "SOVRA_LITELLM_GATEWAY_URL": "https://litellm.internal.example",
        "SOVRA_PRIVACY_POLICY": "standard",
        "SOVRA_TLS_VERIFY": "true",
        "SOVRA_MAX_TRAINING_BUDGET_USD": "500.00",
        "SOVRA_LITELLM_API_KEY_SECRET_REF": "vault://sovra/litellm-api-key",
        "SOVRA_DATABASE_PASSWORD_SECRET_REF": "openbao://sovra/db-password",
        "SOVRA_OIDC_ISSUER": "http://idp.internal.example",
        "SOVRA_OIDC_CLIENT_ID": "sovra-factory",
        "SOVRA_OIDC_CLIENT_SECRET_REF": "vault://sovra/oidc-client-secret",
        "SOVRA_OIDC_REDIRECT_URI": "http://factory.internal.example/auth/callback",
        "SOVRA_OIDC_AUTHORIZATION_ENDPOINT": "http://idp.internal.example/authorize",
        "SOVRA_OIDC_TOKEN_ENDPOINT": "http://idp.internal.example/token",
        "SOVRA_OIDC_JWKS_URI": "http://idp.internal.example/jwks",
        "SOVRA_SESSION_SIGNING_KEY_SECRET_REF": "vault://sovra/session-signing-key",
        "SOVRA_OIDC_ALLOWED_ORIGIN": "http://factory.internal.example",
        "SOVRA_OIDC_CLIENT_SECRET_VALUE": "not-a-real-secret",
        "SOVRA_SESSION_SIGNING_KEY_VALUE": "not-a-real-signing-key",
    }


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")

    if FAILURES:
        print(f"FTF-0501 direct upload tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0501 direct upload tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
