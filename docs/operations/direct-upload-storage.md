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
upload past its deadline. Run it at least hourly with the same `SOVRA_UPLOAD_S3_*` environment
**and** `SOVRA_DATABASE_URL` set to the restricted application role (`sovra_app`):

    python -m app.datasets.upload_sweeper

SeaweedFS 3.99 returns no `Initiated` time in its multipart listing, so the sweeper dates an
upload by its upload session's own `expires_at` (read under the tenant named by the key, so row-level
security applies). Without `SOVRA_DATABASE_URL` it can only use a listing's `Initiated`; an upload
with neither is never aborted and makes the run fail. The listing is paged with key/upload-id
markers under a per-run budget of 100 pages (100,000 uploads).

Exit code 0 = swept completely, 1 = storage or database failure, or at least one upload whose age
could not be determined (visible to the scheduler), 2 = not configured, 3 = the page budget ended
before the listing did (run it again).

Status: the sweeper is implemented and tested, but no scheduler is configured in any deployment
from this repository (FTF-H013). Until one is, abandoned uploads are not aborted (UNWIRED in
deployment).
