#!/usr/bin/env python3
"""FTF-0501 -- Direct object-storage upload (presigned multipart + finalize
verification). No-DB, no-container suite; the live SeaweedFS evidence is
`tests/integration/test_ftf_0501_direct_upload_s3.py` (CI job
`direct-upload-s3-integration`).

Run directly: python3 tests/tasks/test_ftf_0501.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services import storage_semantics_client as s3  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0501"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- P: SigV4 query presign ----------------------------------------------------

# The worked example in the AWS S3 documentation, "Authenticating Requests:
# Using Query Parameters (AWS Signature Version 4)".
AWS_DOC_ACCESS = "AKIAIOSFODNN7EXAMPLE"
AWS_DOC_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_DOC_SIGNATURE = "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"


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


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print("\n".join(FAILURES))
        sys.exit(1)
    print("FTF-0501 direct upload tests: all checks passed")


if __name__ == "__main__":
    main()
