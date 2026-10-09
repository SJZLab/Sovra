# Direct upload storage (FTF-0501)

The dataset upload routes (`build_direct_upload_router`, mounted in `apps/api/app/main.py`)
talk to a private S3-compatible bucket. They are inert (503 `storage_not_configured`) until
this environment is set on the API service:

| Variable | Meaning |
| --- | --- |
| `SOVRA_UPLOAD_S3_ENDPOINT` | `http(s)://host[:port]` of the S3 API |
| `SOVRA_UPLOAD_S3_BUCKET` | the private upload bucket |
| `SOVRA_UPLOAD_S3_ACCESS_KEY` / `SOVRA_UPLOAD_S3_SECRET_KEY` | credentials able to create, list, abort and read multipart uploads in that bucket |
| `SOVRA_UPLOAD_S3_REGION` | optional, default `us-east-1` |

The bucket must allow browser `PUT` of presigned parts (CORS) and must be private.

## Abandoned uploads (Section 67.5, 24 h)

An upload that is never finalized keeps its parts in the bucket. No bucket lifecycle rule is
configured by this repository. `app.datasets.upload_sweeper` aborts every Factory-owned multipart
upload older than 24 hours. Run it at least hourly with the same `SOVRA_UPLOAD_S3_*` environment:

    python -m app.datasets.upload_sweeper

Exit code 0 = swept, 1 = storage failure (visible to the scheduler), 2 = not configured.
A full 1000-upload page prints `more_may_remain=True`; run it again.

Status: the sweeper is implemented and tested, but no scheduler is configured in any deployment
from this repository. Until one is, abandoned uploads are not aborted (UNWIRED in deployment).
