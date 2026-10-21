#!/usr/bin/env python3
"""FTF-0501-F2 -- finalize admits declared non-UTF-8 text encodings.

Follow-up on certified FTF-0501 (found by the FTF-H011-P1 r3 verifier): finalize
refused a small windows-1256 `;` CSV as text/csv because the sniff decoded UTF-8
only, while FTF-0503/0504 accept five encodings; and because the sniff covers
only the first 64 KiB, the same file behind a >64 KiB ASCII preamble WAS
accepted, so the verdict depended on where the first non-ASCII byte fell.

The upload session now declares its text encoding (one of the five, default
utf-8). Finalize decodes the WHOLE object strictly with it, so:

S1  `sniff_content` decodes the head with the declared encoding and runs the
    structural checks on the decoded text; an undeclared non-UTF-8 head is refused;
V1  finalize accepts a declared cp1256 / utf-16 / utf-8-sig body and refuses the
    same bytes when undeclared, wherever the first undecodable byte falls (before,
    at or after the 64 KiB window, across stream-chunk boundaries, in the tail);
R1  the create route takes an optional `declared_encoding`, stores and returns it,
    refuses unknown values and a non-default encoding on a spreadsheet;
M1  migration 0032 adds the column with the five-value CHECK, no grant change;
D1  the declared list is the importers' CSV_ENCODINGS (no drift).

Run directly: python3 tests/tasks/test_ftf_0501_f2.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

import test_ftf_0501 as base  # noqa: E402

from app.datasets import dataset_upload_store as store  # noqa: E402
from app.datasets import direct_object_storage_upload as dos  # noqa: E402

CID = "FTF-0501-F2"
FAILURES = base.FAILURES
check = base.check

MIGRATION = ROOT / "apps" / "api" / "alembic" / "versions" / "0033_upload_declared_encoding.py"
ENCODINGS = ("utf-8", "utf-8-sig", "utf-16-le", "utf-16-be", "windows-1256")
ARABIC_HEADER = "الاسم;المدينة;الرقم"
CP1256_CSV = (ARABIC_HEADER + "\r\nعلي;الرياض;1\r\n").encode("windows-1256")
BAD = "content_type_mismatch"


def _csv_in(encoding: str, text: str = ARABIC_HEADER + "\r\nعلي;الرياض;1\r\n") -> bytes:
    return text.encode(encoding)


# -- S1: the sniff --------------------------------------------------------------


def test_s1_sniff_decodes_with_the_declared_encoding() -> None:
    sniff = dos.sniff_content
    for encoding in ENCODINGS:
        body = _csv_in(encoding)
        want_ok = None
        check(
            f"{CID}-S1",
            f"{encoding}: an Arabic CSV is accepted when declared",
            sniff("text/csv", body, complete=True, encoding=encoding) == want_ok,
        )
        if encoding not in ("utf-8", "utf-8-sig"):
            check(
                f"{CID}-S1",
                f"{encoding}: the same bytes are refused when undeclared (utf-8 default)",
                sniff("text/csv", body, complete=True) == BAD,
            )
    check(
        f"{CID}-S1",
        "a BOM-prefixed utf-16-le CSV is accepted when declared",
        sniff(
            "text/csv",
            b"\xff\xfe" + "a,b\r\n".encode("utf-16-le"),
            complete=True,
            encoding="utf-16-le",
        )
        is None,
    )
    check(
        f"{CID}-S1",
        "a BOM-prefixed utf-8-sig CSV is accepted when declared",
        sniff("text/csv", b"\xef\xbb\xbfa,b\n", complete=True, encoding="utf-8-sig") is None,
    )
    check(
        f"{CID}-S1",
        "jsonl in windows-1256 is accepted when declared",
        sniff(
            "application/jsonl",
            '{"n":"علي"}\n'.encode("windows-1256"),
            complete=True,
            encoding="windows-1256",
        )
        is None,
    )


def test_s2_the_structural_checks_still_run_on_the_decoded_text() -> None:
    sniff = dos.sniff_content
    for encoding in ("utf-16-le", "utf-16-be", "windows-1256"):
        check(
            f"{CID}-S2",
            f"{encoding}: a JSON-looking body declared as CSV is still ambiguous",
            sniff("text/csv", '{"a":1}\n'.encode(encoding), complete=True, encoding=encoding)
            == BAD,
        )
        check(
            f"{CID}-S2",
            f"{encoding}: JSONL that starts with an array is still refused",
            sniff("application/jsonl", "[1]\n".encode(encoding), complete=True, encoding=encoding)
            == BAD,
        )
        check(
            f"{CID}-S2",
            f"{encoding}: an all-whitespace body is refused",
            sniff("text/csv", " \n".encode(encoding), complete=True, encoding=encoding) == BAD,
        )
    check(
        f"{CID}-S2",
        "a NUL character in utf-16 text is binary, not text",
        sniff("text/csv", "a,b\x00\n".encode("utf-16-le"), complete=True, encoding="utf-16-le")
        == BAD,
    )
    check(
        f"{CID}-S2",
        "a NUL byte in windows-1256 is binary",
        sniff("text/csv", b"a,b\n\x00\x01", complete=True, encoding="windows-1256") == BAD,
    )
    check(
        f"{CID}-S2",
        "a lone utf-16 byte at the end of a complete object is an error",
        sniff("text/csv", "a,b\n".encode("utf-16-le") + b"x", complete=True, encoding="utf-16-le")
        == BAD,
    )
    check(
        f"{CID}-S2",
        "the same cut at the sniff window is not an error",
        sniff("text/csv", "a,b\n".encode("utf-16-le") + b"x", complete=False, encoding="utf-16-le")
        is None,
    )
    check(
        f"{CID}-S2",
        "a spreadsheet ignores the text encoding",
        sniff(dos.XLSX_CONTENT_TYPE, b"PK\x03\x04rest", complete=True, encoding="windows-1256")
        is None,
    )
    check(
        f"{CID}-S2",
        "an unknown encoding is a programming error, never a pass",
        base._raises(
            lambda: sniff("text/csv", b"a,b\n", complete=True, encoding="latin-1"), ValueError
        ),
    )
    check(
        f"{CID}-S2",
        "the unsupported-type refusal still wins",
        sniff("text/html", b"<html>", complete=True, encoding="windows-1256")
        == "unsupported_content_type",
    )


# -- V1: finalize ---------------------------------------------------------------


def _target(body: bytes, encoding: str, content_type: str = "text/csv") -> dos.UploadTarget:
    plain = base._target(body, content_type=content_type)
    return dos.UploadTarget(
        object_key=plain.object_key,
        declared_size_bytes=plain.declared_size_bytes,
        declared_sha256=plain.declared_sha256,
        declared_content_type=content_type,
        expires_at=plain.expires_at,
        declared_encoding=encoding,
    )


def _finalize(body: bytes, encoding: str, content_type: str = "text/csv", **overrides: Any) -> Any:
    with base.FakeS3() as fake:
        target = _target(body, encoding, content_type=content_type)
        base._stored(fake, target, body)
        return base._verify_with(fake, target, **overrides)


def _rejected(outcome: Any) -> bool:
    return isinstance(outcome, dos.UploadRejection) and outcome.reason == BAD


def test_v1_finalize_follows_the_declared_encoding() -> None:
    for encoding in ENCODINGS:
        body = _csv_in(encoding)
        check(
            f"{CID}-V1",
            f"{encoding}: verifies when declared",
            isinstance(_finalize(body, encoding), dos.VerifiedObject),
        )
        if encoding not in ("utf-8", "utf-8-sig"):
            check(
                f"{CID}-V1",
                f"{encoding}: refused as content_type_mismatch when undeclared",
                _rejected(_finalize(body, "utf-8")),
            )
    check(
        f"{CID}-V1",
        "a cp1256 body declared as utf-16-le is refused (wrong declaration)",
        _rejected(_finalize(CP1256_CSV, "utf-16-le")),
    )


def test_v2_the_verdict_does_not_depend_on_where_the_bad_byte_falls() -> None:
    window = dos.SNIFF_BYTES
    row = "a;b;1\r\n"
    for label, preamble in (
        ("inside the window", 10),
        ("at the window edge", window - 8),
        ("after the window", window + 5000),
    ):
        text = row * (preamble // len(row) + 1)
        body = text[:preamble].encode() + CP1256_CSV
        check(
            f"{CID}-V2",
            f"undeclared cp1256 byte {label}: refused",
            _rejected(_finalize(body, "utf-8")),
        )
        check(
            f"{CID}-V2",
            f"declared cp1256 byte {label}: verifies",
            isinstance(_finalize(body, "windows-1256"), dos.VerifiedObject),
        )
    tail = (row * ((window + 100) // len(row) + 1)).encode() + b"\xc3"
    check(
        f"{CID}-V2",
        "a truncated UTF-8 character in the tail of a large object is refused",
        _rejected(_finalize(tail, "utf-8")),
    )
    utf16_tail = ("a,b\r\n" * (window // 4)).encode("utf-16-le") + b"x"
    check(
        f"{CID}-V2",
        "a lone byte ending a large utf-16 object is refused",
        _rejected(_finalize(utf16_tail, "utf-16-le")),
    )


def test_v3_decoding_survives_chunk_boundaries() -> None:
    for encoding in ("utf-16-le", "utf-16-be", "utf-8"):
        body = _csv_in(encoding, ("الاسم;المدينة\r\n" * 40))
        chunks = [body[i : i + 3] for i in range(0, len(body), 3)]

        def stream(_bucket: str, _key: str, _chunks: list[bytes] = chunks):
            return iter(_chunks)

        check(
            f"{CID}-V3",
            f"{encoding}: characters split across 3-byte chunks still decode",
            isinstance(_finalize(body, encoding, stream_object=stream), dos.VerifiedObject),
        )


def test_v4_a_spreadsheet_is_not_decoded_and_the_declaration_is_validated() -> None:
    body = base._xlsx()
    with base.FakeS3() as fake:
        target = _target(body, "utf-8", content_type=dos.XLSX_CONTENT_TYPE)
        base._stored(fake, target, body)
        outcome = base._verify_with(fake, target)
    check(
        f"{CID}-V4",
        "a real spreadsheet (binary, not UTF-8) still verifies",
        isinstance(outcome, dos.VerifiedObject),
    )
    check(
        f"{CID}-V4",
        "an unknown declared encoding never reaches storage",
        isinstance(_finalize(b"a,b\n", "latin-1"), ValueError),
    )


# -- R1: routes and store -------------------------------------------------------


def test_r1_create_declares_and_returns_the_encoding() -> None:
    with base._routes_env() as env:
        body = CP1256_CSV
        sid = base._open_session(
            env, body, declared_content_type="text/csv", declared_encoding="windows-1256"
        )
        check(
            f"{CID}-R1",
            "the response carries the declared encoding",
            sid["declared_encoding"] == "windows-1256",
        )
        (row,) = env.sessions()
        check(
            f"{CID}-R1",
            "the row stores it and the storage target carries it",
            row.declared_encoding == "windows-1256"
            and env.storage_for(sid["id"]).declared_encoding == "windows-1256",
        )
        base._put_all(env, sid["id"], body)
        done = base._call(
            env.base, "POST", f"{base._col()}/{sid['id']}/finalize", base._finalize_body()
        )
        check(
            f"{CID}-R1",
            "finalize creates the dataset for the declared cp1256 CSV",
            done.status == 201,
        )

    with base._routes_env() as env:
        default = base._open_session(env, b"a,b\n1,2\n", declared_content_type="text/csv")
        check(
            f"{CID}-R1",
            "omitted encoding defaults to utf-8",
            default["declared_encoding"] == "utf-8",
        )

    with base._routes_env() as env:
        undeclared = base._open_session(env, CP1256_CSV, declared_content_type="text/csv")["id"]
        base._put_all(env, undeclared, CP1256_CSV)
        done = base._call(
            env.base, "POST", f"{base._col()}/{undeclared}/finalize", base._finalize_body()
        )
        check(
            f"{CID}-R1",
            "the same bytes without a declaration are refused and create no dataset",
            done.status != 201 and not env.datasets(),
        )


def test_r2_bad_declarations_are_refused_before_anything_is_written() -> None:
    cases = (
        ("unknown encoding", {"declared_encoding": "latin-1"}),
        ("empty encoding", {"declared_encoding": ""}),
        ("non-string encoding", {"declared_encoding": 7}),
        ("list encoding", {"declared_encoding": ["utf-8"]}),
        ("null encoding", {"declared_encoding": None}),
        ("cased encoding", {"declared_encoding": "UTF-8"}),
        (
            "non-default encoding on a spreadsheet",
            {"declared_content_type": dos.XLSX_CONTENT_TYPE, "declared_encoding": "windows-1256"},
        ),
    )
    for label, over in cases:
        with base._routes_env() as env:
            reply = base._call(env.base, "POST", base._col(), base._create_body(b"a,b\n", **over))
            check(
                f"{CID}-R2",
                f"{label}: 400 invalid_body",
                reply.status == 400 and reply.detail == "invalid_body",
            )
            check(
                f"{CID}-R2",
                f"{label}: no session row and nothing in storage",
                not env.sessions() and not env.fake.uploads,
            )
    with base._routes_env() as env:
        reply = base._call(
            env.base,
            "POST",
            base._col(),
            base._create_body(
                b"a,b\n",
                declared_content_type=dos.XLSX_CONTENT_TYPE,
                declared_encoding="utf-8",
            ),
        )
        check(f"{CID}-R2", "the default encoding on a spreadsheet is accepted", reply.status == 201)
    with base._routes_env() as env:
        reply = base._call(
            env.base, "POST", base._col(), {**base._create_body(b"a,b\n"), "extra": 1}
        )
        check(f"{CID}-R2", "an unknown extra field is still refused", reply.status == 400)
        missing = base._create_body(b"a,b\n")
        del missing["purpose"]
        reply = base._call(env.base, "POST", base._col(), missing)
        check(f"{CID}-R2", "a missing required field is still refused", reply.status == 400)


def test_r3_the_store_validates_the_encoding() -> None:
    check(
        f"{CID}-R3",
        "the upload module's list is the five importer encodings",
        dos.TEXT_ENCODINGS == ENCODINGS,
    )
    check(
        f"{CID}-R3",
        "the ORM column defaults to utf-8 in Python and in the database",
        store.DatasetUploadSession.__table__.c.declared_encoding.default.arg == "utf-8"
        and store.DatasetUploadSession.__table__.c.declared_encoding.server_default.arg == "utf-8",
    )
    with base._routes_env() as env:
        import uuid

        ok = base._raises(
            lambda: store.create_upload_session(
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                project_id=uuid.uuid4(),
                connection_id=uuid.uuid4(),
                purpose="training",
                declared_size_bytes=4,
                declared_sha256="0" * 64,
                declared_content_type="text/csv",
                max_bytes=4,
                declared_encoding="latin-1",
                actor=store.UploadActor(actor_type="user", actor_id="u", request_id="r"),
            ),
            ValueError,
        )
        check(f"{CID}-R3", "create_upload_session refuses an unknown encoding first", ok)
        del env


def test_s3_encoding_names_are_exact() -> None:
    for name in ("UTF-8", " utf-8", "Windows-1256", "utf8", "utf-16"):
        check(
            f"{CID}-S3",
            f"sniff refuses the non-canonical name {name!r}",
            base._raises(
                lambda n=name: dos.sniff_content("text/csv", b"a,b\n", complete=True, encoding=n),
                ValueError,
            ),
        )
        check(
            f"{CID}-S3",
            f"finalize refuses {name!r} before touching storage",
            _guard_outcome(name, "text/csv"),
        )
    check(
        f"{CID}-S3",
        "finalize refuses a non-default encoding on a spreadsheet before touching storage",
        _guard_outcome("windows-1256", dos.XLSX_CONTENT_TYPE),
    )
    check(
        f"{CID}-S3",
        "encodings_for: a spreadsheet takes only the default, text takes all five",
        dos.encodings_for(dos.XLSX_CONTENT_TYPE) == ("utf-8",)
        and dos.encodings_for("text/csv") == ENCODINGS
        and dos.encodings_for("application/jsonl") == ENCODINGS,
    )


def _guard_outcome(encoding: str, content_type: str) -> bool:
    touched: list[str] = []

    def boom(*_a: Any, **_k: Any) -> Any:
        touched.append("storage")
        raise AssertionError("storage was touched")

    body = b"a,b\n"
    with base.FakeS3() as fake:
        target = _target(body, encoding, content_type=content_type)
        base._stored(fake, target, body)
        outcome = base._verify_with(
            fake, target, head_object=boom, stream_object=boom, find_upload_id=boom
        )
    return isinstance(outcome, ValueError) and not touched


def _create_error(**over: Any) -> BaseException | None:
    import uuid
    from datetime import timedelta

    args: dict[str, Any] = {
        "project_id": uuid.uuid4(),
        "connection_id": uuid.uuid4(),
        "purpose": "training",
        "declared_size_bytes": 4,
        "declared_sha256": "0" * 64,
        "declared_content_type": "text/csv",
        "max_bytes": 4,
        "ttl": timedelta(hours=1),
        "actor": store.UploadActor(actor_type="user", actor_id="u", request_id="r"),
    }
    args.update(over)
    try:
        # Validation runs before the database is touched, so no session is needed to see
        # it refuse; an accepted value falls through to the (absent) session and fails
        # with something that is not a ValueError.
        store.create_upload_session(None, None, **args)  # type: ignore[arg-type]
    except BaseException as exc:  # noqa: BLE001 - the type is the result under test
        return exc
    return None


def _refused(**over: Any) -> bool:
    return isinstance(_create_error(**over), ValueError)


def test_r4_the_store_validates_every_declared_value_before_the_database() -> None:
    from datetime import timedelta

    check(f"{CID}-R4", "a valid request passes validation", not _refused())
    for label, over in (
        ("zero size", {"declared_size_bytes": 0}),
        ("size above max_bytes", {"declared_size_bytes": 5}),
        ("max_bytes above the 10 GiB cap", {"max_bytes": store.MAX_UPLOAD_BYTES + 1}),
        ("zero ttl", {"ttl": timedelta(0)}),
        ("ttl above 24 hours", {"ttl": store.MAX_UPLOAD_TTL + timedelta(seconds=1)}),
        ("non-timedelta ttl", {"ttl": 3600}),
        ("cased purpose", {"purpose": "Training"}),
        ("cased encoding", {"declared_encoding": "UTF-8"}),
        ("padded encoding", {"declared_encoding": " windows-1256"}),
        (
            "non-default encoding on a spreadsheet",
            {"declared_content_type": dos.XLSX_CONTENT_TYPE, "declared_encoding": "utf-16-le"},
        ),
    ):
        check(f"{CID}-R4", f"{label} is refused", _refused(**over))
    for label, over in (
        (
            "size equal to max_bytes at the 10 GiB cap",
            {"declared_size_bytes": store.MAX_UPLOAD_BYTES, "max_bytes": store.MAX_UPLOAD_BYTES},
        ),
        ("ttl of exactly 24 hours", {"ttl": store.MAX_UPLOAD_TTL}),
        ("every declared encoding on text", {"declared_encoding": "utf-16-be"}),
        (
            "the default encoding on a spreadsheet",
            {"declared_content_type": dos.XLSX_CONTENT_TYPE, "declared_encoding": "utf-8"},
        ),
    ):
        check(f"{CID}-R4", f"{label} passes validation", not _refused(**over))


def test_r5_a_non_upload_source_is_refused_exactly() -> None:
    with base._routes_env() as env:
        env.world.source_type = "Upload"
        reply = base._call(env.base, "POST", base._col(), base._create_body(b"a,b\n"))
        check(
            f"{CID}-R5",
            "a connection typed 'Upload' is not an upload connection",
            reply.status == 409 and reply.detail == "not_an_upload_source",
        )
        check(f"{CID}-R5", "nothing was written", not env.sessions() and not env.fake.uploads)


def test_v5_the_tail_decision_is_made_on_the_whole_object() -> None:
    window = dos.SNIFF_BYTES
    exact = b'{"k":"' + b"x" * (window - 7) + b"\xc3"
    check(
        f"{CID}-V5",
        "a cut character at the very end of an object exactly one window long is refused",
        len(exact) == window and _rejected(_finalize(exact, "utf-8", "application/jsonl")),
    )
    clean = b'{"k":"' + b"x" * (window - 9) + b'"}\n'
    check(
        f"{CID}-V5",
        "the same size with whole characters verifies",
        len(clean) == window - 1 + 1
        and isinstance(_finalize(clean, "utf-8", "application/jsonl"), dos.VerifiedObject),
    )
    mixed = "a,b\r\n".encode("utf-16-le") * 3 + b"\x00\xd8"
    check(
        f"{CID}-V5",
        "a lone high surrogate ending a small utf-16 object is refused",
        _rejected(_finalize(mixed, "utf-16-le")),
    )


# -- M1: migration --------------------------------------------------------------


def test_m1_the_migration_adds_a_checked_immutable_column() -> None:
    src = MIGRATION.read_text()
    check(f"{CID}-M1", "revision id", 'revision = "0033_upload_declared_encoding"' in src)
    check(f"{CID}-M1", "parent revision", 'down_revision = "0032_split_manifest_requests"' in src)
    check(
        f"{CID}-M1", "revision id fits alembic_version", len("0033_upload_declared_encoding") <= 32
    )
    check(
        f"{CID}-M1",
        "no table is created or dropped",
        "create_table" not in src and "drop_table" not in src,
    )
    check(
        f"{CID}-M1",
        "no grant, policy or trigger is touched",
        not re.search(r"GRANT|REVOKE|POLICY|TRIGGER", src),
    )
    for encoding in ENCODINGS:
        check(f"{CID}-M1", f"the CHECK admits {encoding}", f"'{encoding}'" in src)
    check(
        f"{CID}-M1",
        "NOT NULL with a utf-8 server default",
        'nullable=False, server_default="utf-8"' in src,
    )

    def sql(action: str, rng: str) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", action, rng, "--sql"],
            cwd=str(ROOT / "apps" / "api"),
            capture_output=True,
            text=True,
            env={**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"},
        )
        check(
            f"{CID}-M1",
            f"alembic {action} {rng} --sql exits 0 ({result.stderr[-200:]})",
            result.returncode == 0,
        )
        return result.stdout

    up = sql("upgrade", "0032_split_manifest_requests:0033_upload_declared_encoding")
    check(
        f"{CID}-M1",
        "upgrade adds the column",
        "ADD COLUMN declared_encoding VARCHAR(16) DEFAULT 'utf-8' NOT NULL" in up,
    )
    check(
        f"{CID}-M1",
        "upgrade adds the CHECK",
        "ck_dataset_upload_sessions_declared_encoding" in up and "'windows-1256'" in up,
    )
    down = sql("downgrade", "0033_upload_declared_encoding:0032_split_manifest_requests")
    check(
        f"{CID}-M1",
        "downgrade drops the CHECK then the column",
        "DROP CONSTRAINT ck_dataset_upload_sessions_declared_encoding" in down
        and "DROP COLUMN declared_encoding" in down
        and down.index("DROP CONSTRAINT") < down.index("DROP COLUMN"),
    )
    heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(ROOT / "apps" / "api"),
        capture_output=True,
        text=True,
        env={**os.environ, "SOVRA_DATABASE_URL": "postgresql+psycopg://x:y@localhost:5432/z"},
    )
    check(
        f"{CID}-M1",
        "a single Alembic head",
        heads.stdout.count("(head)") == 1 and "0033_upload_declared_encoding" in heads.stdout,
    )


# -- D1: no drift ---------------------------------------------------------------


def test_d1_the_list_is_the_importers_list() -> None:
    from workers.data.csv_importer import CSV_ENCODINGS

    check(
        f"{CID}-D1", "TEXT_ENCODINGS == CSV_ENCODINGS", tuple(CSV_ENCODINGS) == dos.TEXT_ENCODINGS
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and name != "test_ftf_0501" and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        print(f"FTF-0501-F2 tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0501-F2 tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
