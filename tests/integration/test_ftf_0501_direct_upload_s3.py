#!/usr/bin/env python3
"""FTF-0501 -- direct object-storage upload against the real SeaweedFS S3 API.

Boots `infra/docker/docker-compose.yml`'s `s3` service (the live container
`tests/integration/test_storage_semantics.py` and the FTF-0500B integration
already use) and runs this task's own storage layer
(`app.datasets.direct_object_storage_upload`) against it, with real presigned
URLs sent by a plain HTTP client:

  - a presigned PUT is accepted by the real server, for one part and for three;
  - the same URL with a changed body length, part number, key, upload id,
    lifetime or no signature at all is refused by the real server;
  - finalize completes the multipart upload and verifies size, SHA-256 and
    content type of what the server actually stored;
  - a wrong hash, an oversize part, an extra part, the wrong content type and
    a partial upload never become a verified object (the stored bytes are
    deleted on every rejection; a partial upload stays resumable);
  - an object under another key never satisfies a session.

Requires a Docker daemon with container-registry egress (GitHub Actions has
it; the interactive sandbox does not). Run directly:
    python3 tests/integration/test_ftf_0501_direct_upload_s3.py
"""

from __future__ import annotations

import hashlib
import http.client
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import yaml  # noqa: E402

from app.datasets import direct_object_storage_upload as dos  # noqa: E402
from app.services.storage_semantics_client import (  # noqa: E402
    S3Client,
    S3Credentials,
    S3Endpoint,
    S3Error,
)

COMPOSE_FILE = "infra/docker/docker-compose.yml"
PROFILE_PATH = ROOT / "infra" / "internal" / "storage-profile.yaml"
MIB = 1024**2
CID = "FTF-0501"
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def run(*args: str, check_exit: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, check=check_exit, capture_output=True, text=True)


def compose(*args: str, check_exit: bool = True) -> subprocess.CompletedProcess[str]:
    return run("docker", "compose", "-f", COMPOSE_FILE, *args, check_exit=check_exit)


def _wait_for_tcp(host: str, port: int, timeout_seconds: float) -> None:
    import socket

    deadline = time.monotonic() + timeout_seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return
        except OSError as exc:  # noqa: PERF203
            last = exc
            time.sleep(1.0)
    raise TimeoutError(f"S3 endpoint {host}:{port} never became reachable: {last}")


def _wait_for_s3_http_ready(endpoint: S3Endpoint, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(f"{endpoint.base_url}/", method="GET"), timeout=3.0
            )
            return
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last = exc
            time.sleep(1.0)
    raise TimeoutError(f"S3 endpoint {endpoint.base_url} never answered over HTTP: {last}")


def _load_endpoint_and_credentials() -> tuple[S3Endpoint, S3Credentials]:
    import os

    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    env = profile["endpoint"]
    defaults = env["local_fixture_defaults"]

    def _get(var_key: str, default_key: str) -> str:
        return os.environ.get(env[var_key], str(defaults[default_key]))

    return (
        S3Endpoint(
            scheme=_get("scheme_env_var", "scheme"),
            host=_get("host_env_var", "host"),
            port=int(_get("port_env_var", "port")),
            region=_get("region_env_var", "region"),
        ),
        S3Credentials(
            access_key=_get("access_key_env_var", "access_key"),
            secret_key=_get("secret_key_env_var", "secret_key"),
        ),
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl(size: int) -> bytes:
    line = b'{"k":"' + b"x" * 1015 + b'"}\n'
    return (line * (size // len(line) + 1))[:size]


DROPPED = -1


def _put(url: str, data: bytes) -> int:
    """The HTTP status of a PUT, or `DROPPED` when the server closed the
    connection while the body was still being sent. A server that refuses a
    tampered request from its headers alone (the signature covers them) may
    answer 403 and close before reading a multi-megabyte body, so the client
    sees a reset instead of the 403. Only a refusal check may accept that, and
    each such check is backed by "nothing was stored"."""
    request = urllib.request.Request(url, data=data, method="PUT")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, ConnectionError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, BrokenPipeError | ConnectionResetError | ConnectionAbortedError):
            return DROPPED
        raise  # e.g. connection refused: an outage is never a refusal


def _refused(status: int) -> bool:
    return status in (403, DROPPED)


def _refused_unsigned(status: int) -> bool:
    """A request with no signature at all is refused with 403 or, on some
    SeaweedFS builds, 400 `MissingFields` (3 of 9 local runs saw 400). Only this
    check accepts 400; every other refusal stays 403 or a dropped connection,
    and "nothing was stored" is asserted separately."""
    return status in (400, 403, DROPPED)


def _target(
    body: bytes, *, content_type: str = "application/jsonl", sha: str | None = None
) -> dos.UploadTarget:
    return dos.UploadTarget(
        object_key=(
            f"tenants/{uuid.uuid4()}/projects/{uuid.uuid4()}/uploads/{uuid.uuid4()}/source"
        ),
        declared_size_bytes=len(body),
        declared_sha256=sha or _sha(body),
        declared_content_type=content_type,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


class Lab:
    def __init__(self, endpoint: S3Endpoint, credentials: S3Credentials) -> None:
        self.bucket = f"sovra-ftf0501-{uuid.uuid4().hex[:12]}"
        self.settings = dos.UploadStorageSettings(endpoint, credentials, self.bucket)
        self.client = S3Client(endpoint, credentials, timeout_seconds=60.0)
        self.client.create_bucket(self.bucket)

    def open(self, target: dos.UploadTarget) -> str:
        return dos.start_multipart(self.client, self.settings, target)

    def upload(
        self, target: dos.UploadTarget, body: bytes, upload_id: str, numbers: list[int]
    ) -> list[int]:
        plan = dos.plan_parts(target.declared_size_bytes)
        statuses = []
        for grant in dos.presign_part_urls(self.settings, target, upload_id, numbers):
            start = (grant.part_number - 1) * plan.part_size_bytes
            statuses.append(_put(grant.url, body[start : start + grant.content_length]))
        return statuses

    def verify(self, target: dos.UploadTarget) -> object:
        try:
            return dos.verify_and_complete(self.client, self.settings, target)
        except Exception as exc:  # noqa: BLE001 - the type is the result under test
            return exc

    def object_exists(self, target: dos.UploadTarget) -> bool:
        try:
            self.client.head_object(self.bucket, target.object_key)
        except S3Error as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    def upload_in_progress(self, target: dos.UploadTarget) -> bool:
        return dos.find_upload_id(self.client, self.settings, target) is not None

    def gone(self, target: dos.UploadTarget) -> bool:
        return not self.object_exists(target) and not self.upload_in_progress(target)


def test_single_part_presigned_upload_verifies(lab: Lab) -> None:
    body = _jsonl(200_000)
    target = _target(body)
    upload_id = lab.open(target)
    check(CID, "a real multipart upload opens", bool(upload_id))
    check(CID, "it is found again by its exact key", lab.upload_in_progress(target))
    statuses = lab.upload(target, body, upload_id, [1])
    check(CID, "the real server accepts the presigned PUT", statuses == [200])
    parts = dos.list_uploaded_parts(lab.client, lab.settings, target, upload_id)
    check(
        CID,
        "ListParts reports the stored part",
        [(p.part_number, p.size_bytes) for p in parts] == [(1, len(body))],
    )
    got = lab.verify(target)
    check(
        CID,
        "finalize verifies the stored object",
        got == dos.VerifiedObject(len(body), _sha(body), "application/jsonl"),
    )
    stored, headers = lab.client.get_object(lab.bucket, target.object_key)
    check(CID, "the stored bytes are the uploaded bytes", stored == body)
    check(
        CID,
        "the declared content type was stored with the object",
        headers.get("content-type", "").startswith("application/jsonl"),
    )
    check(CID, "no multipart upload is left in progress", not lab.upload_in_progress(target))
    again = lab.verify(target)
    check(CID, "finalize is repeatable from the stored object", again == got)


def test_three_part_upload_out_of_order(lab: Lab) -> None:
    saved = dos.DEFAULT_PART_BYTES
    dos.DEFAULT_PART_BYTES = dos.MIN_PART_BYTES
    try:
        body = _jsonl(12 * MIB)
        target = _target(body)
        upload_id = lab.open(target)
        statuses = lab.upload(target, body, upload_id, [3, 1, 2])
        check(CID, "all three parts are accepted", statuses == [200, 200, 200])
        got = lab.verify(target)
        check(
            CID,
            "three parts finalize to the exact object",
            isinstance(got, dos.VerifiedObject) and got.sha256 == _sha(body),
        )
        stored, _ = lab.client.get_object(lab.bucket, target.object_key)
        check(CID, "the stored object is the parts in order", stored == body)
    finally:
        dos.DEFAULT_PART_BYTES = saved


def test_a_presigned_url_is_bound_to_what_it_was_issued_for(lab: Lab) -> None:
    saved = dos.DEFAULT_PART_BYTES
    dos.DEFAULT_PART_BYTES = dos.MIN_PART_BYTES
    try:
        body = _jsonl(12 * MIB)
        target = _target(body)
        upload_id = lab.open(target)
        grants = {
            g.part_number: g
            for g in dos.presign_part_urls(lab.settings, target, upload_id, [1, 2, 3])
        }

        def piece(n: int) -> bytes:
            return body[(n - 1) * 5 * MIB : n * 5 * MIB]

        check(
            CID,
            "a part body one byte short is refused",
            _refused(_put(grants[2].url, piece(2)[:-1])),
        )
        check(
            CID,
            "a part body one byte long is refused",
            _refused(_put(grants[2].url, piece(2) + b"x")),
        )
        check(
            CID,
            "another part number is refused",
            _refused(_put(grants[2].url.replace("partNumber=2", "partNumber=3"), piece(2))),
        )
        check(
            CID,
            "another key is refused",
            _refused(
                _put(grants[2].url.replace(target.object_key, target.object_key + "x"), piece(2))
            ),
        )
        check(
            CID,
            "a longer lifetime is refused",
            _refused(
                _put(grants[2].url.replace("X-Amz-Expires=300", "X-Amz-Expires=900"), piece(2))
            ),
        )
        check(
            CID,
            "an unsigned request is refused",
            _refused_unsigned(
                _put(grants[2].url.split("X-Amz-Signature=")[0].rstrip("&"), piece(2))
            ),
        )
        stale = dos.presign_part_urls(
            lab.settings, target, upload_id, [2], now=datetime.now(UTC) - timedelta(seconds=600)
        )
        check(CID, "an expired grant is refused", _refused(_put(stale[0].url, piece(2))))
        parts = dos.list_uploaded_parts(lab.client, lab.settings, target, upload_id)
        check(CID, "none of the refused requests stored anything", parts == [])
        check(CID, "the untouched grant still works", _put(grants[2].url, piece(2)) == 200)
        lab.verify(target)
        dos.abort_upload(lab.client, lab.settings, target)
    finally:
        dos.DEFAULT_PART_BYTES = saved


def test_a_grant_for_one_session_cannot_write_another(lab: Lab) -> None:
    body = _jsonl(50_000)
    mine = _target(body)
    other = _target(body)
    mine_id = lab.open(mine)
    other_id = lab.open(other)
    (grant,) = dos.presign_part_urls(lab.settings, mine, mine_id, [1])
    swapped = grant.url.replace(mine.object_key, other.object_key).replace(mine_id, other_id)
    check(CID, "a grant for key A cannot write key B", _refused(_put(swapped, body)))
    check(
        CID,
        "key B received nothing",
        dos.list_uploaded_parts(lab.client, lab.settings, other, other_id) == [],
    )
    got = lab.verify(mine)
    check(
        CID,
        "key A has nothing yet: incomplete, not a verdict",
        isinstance(got, dos.UploadIncomplete),
    )
    check(
        CID, "an object under another key never satisfies this session", not lab.object_exists(mine)
    )
    dos.abort_upload(lab.client, lab.settings, mine)
    dos.abort_upload(lab.client, lab.settings, other)


def test_wrong_hash_is_rejected_and_deleted(lab: Lab) -> None:
    body = _jsonl(100_000)
    target = _target(body, sha=_sha(b"not these bytes"))
    upload_id = lab.open(target)
    lab.upload(target, body, upload_id, [1])
    got = lab.verify(target)
    check(
        CID,
        "wrong declared hash -> sha256_mismatch",
        isinstance(got, dos.UploadRejection) and got.reason == "sha256_mismatch",
    )
    check(CID, "the real object is deleted, nothing in progress", lab.gone(target))


def _xlsx_bytes(members: list[tuple[str, bytes]]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def _types_xml(part: str, content_type: str) -> bytes:
    return (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'content-types"><Override PartName="{part}" ContentType="{content_type}"/></Types>'
    ).encode()


def test_xlsx_must_be_a_spreadsheet_package(lab: Lab) -> None:
    """The ranged reads of the zip directory work against the real server."""
    sheet = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    word = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    good = _xlsx_bytes(
        [
            ("[Content_Types].xml", _types_xml("/xl/workbook.xml", sheet)),
            ("xl/workbook.xml", b"<workbook/>"),
        ]
    )
    cases = [
        (
            "a docx",
            _xlsx_bytes(
                [
                    ("[Content_Types].xml", _types_xml("/word/document.xml", word)),
                    ("word/document.xml", b"<document/>"),
                ]
            ),
            "content_type_mismatch",
        ),
        ("a plain zip", _xlsx_bytes([("a.txt", b"hello")]), "content_type_mismatch"),
        ("PK-prefixed zero bytes", b"PK\x03\x04" + b"\x00" * 300, "content_type_mismatch"),
        ("a truncated xlsx", good[: len(good) // 2], "content_type_mismatch"),
    ]
    xlsx_type = dos.XLSX_CONTENT_TYPE
    for label, body, reason in cases:
        target = _target(body, content_type=xlsx_type)
        upload_id = lab.open(target)
        lab.upload(target, body, upload_id, [1])
        got = lab.verify(target)
        check(
            CID,
            f"{label} declared as XLSX -> {reason}",
            isinstance(got, dos.UploadRejection) and got.reason == reason,
        )
        check(CID, f"({label}) the real object is deleted", lab.gone(target))
    target = _target(good, content_type=xlsx_type)
    upload_id = lab.open(target)
    lab.upload(target, good, upload_id, [1])
    check(
        CID,
        "a real spreadsheet package verifies on the real server",
        lab.verify(target) == dos.VerifiedObject(len(good), _sha(good), xlsx_type),
    )


def test_wrong_content_type_is_rejected_and_deleted(lab: Lab) -> None:
    body = _jsonl(100_000)
    target = _target(body, content_type="text/csv")
    upload_id = lab.open(target)
    lab.upload(target, body, upload_id, [1])
    got = lab.verify(target)
    check(
        CID,
        "JSON bytes declared as CSV -> content_type_mismatch",
        isinstance(got, dos.UploadRejection) and got.reason == "content_type_mismatch",
    )
    check(CID, "the real object is deleted", lab.gone(target))

    pdf = b"%PDF-1.4 " + b"x" * 2000
    target = _target(pdf, content_type="application/pdf")
    upload_id = lab.open(target)
    lab.client.upload_part(lab.bucket, target.object_key, upload_id, 1, pdf)
    got = lab.verify(target)
    check(
        CID,
        "an unsupported declared type is rejected",
        isinstance(got, dos.UploadRejection) and got.reason == "unsupported_content_type",
    )
    check(CID, "and nothing is left", lab.gone(target))


def test_oversize_and_extra_parts_are_rejected(lab: Lab) -> None:
    saved = dos.DEFAULT_PART_BYTES
    dos.DEFAULT_PART_BYTES = dos.MIN_PART_BYTES
    try:
        body = _jsonl(12 * MIB)
        target = _target(body)
        upload_id = lab.open(target)
        # Parts written with the product's own credentials, bypassing the signed
        # content-length, as a holder of a leaked key would.
        lab.client.upload_part(lab.bucket, target.object_key, upload_id, 1, b"{" + b"x" * (5 * MIB))
        lab.client.upload_part(
            lab.bucket, target.object_key, upload_id, 2, body[5 * MIB : 10 * MIB]
        )
        lab.client.upload_part(lab.bucket, target.object_key, upload_id, 3, body[10 * MIB :])
        got = lab.verify(target)
        check(
            CID,
            "an oversize part -> part_size_mismatch",
            isinstance(got, dos.UploadRejection) and got.reason == "part_size_mismatch",
        )
        check(CID, "the upload is aborted and nothing is stored", lab.gone(target))

        target = _target(body)
        upload_id = lab.open(target)
        for number, start in ((1, 0), (2, 5 * MIB), (3, 10 * MIB)):
            lab.client.upload_part(
                lab.bucket, target.object_key, upload_id, number, body[start : start + 5 * MIB]
            )
        lab.client.upload_part(lab.bucket, target.object_key, upload_id, 4, b"extra")
        got = lab.verify(target)
        check(
            CID,
            "a part beyond the plan -> unexpected_part",
            isinstance(got, dos.UploadRejection) and got.reason == "unexpected_part",
        )
        check(CID, "and nothing is stored", lab.gone(target))
    finally:
        dos.DEFAULT_PART_BYTES = saved


def test_partial_upload_is_incomplete_then_resumes(lab: Lab) -> None:
    saved = dos.DEFAULT_PART_BYTES
    dos.DEFAULT_PART_BYTES = dos.MIN_PART_BYTES
    try:
        body = _jsonl(12 * MIB)
        target = _target(body)
        upload_id = lab.open(target)
        lab.upload(target, body, upload_id, [1, 3])
        got = lab.verify(target)
        check(
            CID,
            "a missing middle part -> UploadIncomplete naming it",
            isinstance(got, dos.UploadIncomplete) and got.missing_parts == (2,),
        )
        check(
            CID,
            "no object exists and the upload is still open",
            not lab.object_exists(target) and lab.upload_in_progress(target),
        )
        resumed = dos.list_uploaded_parts(lab.client, lab.settings, target, upload_id)
        check(CID, "resume sees parts 1 and 3", [p.part_number for p in resumed] == [1, 3])
        lab.upload(target, body, upload_id, [2])
        done = lab.verify(target)
        check(
            CID,
            "after the missing part arrives the session verifies",
            isinstance(done, dos.VerifiedObject),
        )
    finally:
        dos.DEFAULT_PART_BYTES = saved


def test_expired_session_completes_nothing(lab: Lab) -> None:
    body = _jsonl(50_000)
    target = dos.UploadTarget(
        object_key=_target(body).object_key,
        declared_size_bytes=len(body),
        declared_sha256=_sha(body),
        declared_content_type="application/jsonl",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    upload_id = lab.open(target)
    lab.client.upload_part(lab.bucket, target.object_key, upload_id, 1, body)
    got = lab.verify(target)
    check(CID, "finalize after expiry -> UploadExpired", isinstance(got, dos.UploadExpired))
    check(
        CID,
        "nothing was completed",
        not lab.object_exists(target) and lab.upload_in_progress(target),
    )
    check(
        CID,
        "no grant is issued for an expired session",
        isinstance(
            _try(lambda: dos.presign_part_urls(lab.settings, target, upload_id, [1])),
            dos.UploadExpired,
        ),
    )
    dos.abort_upload(lab.client, lab.settings, target)
    check(CID, "abort removes the in-progress upload", lab.gone(target))


def _try(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return exc


def main() -> None:
    endpoint, credentials = _load_endpoint_and_credentials()

    booted_here = False
    try:
        _wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=3.0)
    except TimeoutError:
        print("S3 fixture not yet reachable -- booting it via docker compose", flush=True)
        compose("up", "-d", "s3")
        booted_here = True
        _wait_for_tcp(endpoint.host, endpoint.port, timeout_seconds=90.0)
    _wait_for_s3_http_ready(endpoint, timeout_seconds=90.0)

    lab = Lab(endpoint, credentials)
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        print(f"-- running {name}", flush=True)
        try:
            fn(lab)
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"FAIL [{name}] unexpected exception: {exc!r}")

    if FAILURES:
        print(f"FTF-0501 direct upload S3 integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        print("-- s3 container logs (diagnosis, not a canned pass) --", flush=True)
        logs = compose("logs", "--no-color", "s3", check_exit=False)
        print(logs.stdout)
        print(logs.stderr)
        if booted_here:
            compose("stop", "s3", check_exit=False)
        sys.exit(1)

    if booted_here:
        compose("stop", "s3", check_exit=False)
    print("FTF-0501 direct upload S3 integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
