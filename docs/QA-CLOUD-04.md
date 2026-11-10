# QA-CLOUD-04 — private screenshot attachments

Stacked on QA-CLOUD-03. Feature disabled by default. No bucket, cloud resource,
provider account, deployed service or live object is created or accessed.
Cloud Run remains blocked, including with PostgreSQL/identity/GCS configured.

## Storage and access contract

`PERPERIS_QA_ATTACHMENT_BACKEND` accepts `disabled` (default), `local` or `gcs`.
Local mode requires an explicit `PERPERIS_QA_ATTACHMENT_DIR` outside the repository
and static directory, private directory permissions (0700), and local runtime.
It uses 0600, exclusively created files with random names and no symlink following.
This adapter is only for development; it is not Cloud Run persistent storage.

GCS mode requires verified identity mode, a QA identity project and explicit
`PERPERIS_QA_ATTACHMENT_BUCKET`. ADC accesses an **existing dedicated QA bucket**;
the code does not create buckets or edit IAM. Startup checks uniform bucket-level
access and enforced public access prevention. Future infrastructure must provide
these protections and object read/write/delete plus bucket metadata read permissions
only to the QA service account. No public ACL, signed URL or bucket/object key is
returned to the browser. Bounded retries and conditional generation operations
protect against overwrites or reading/deleting a different object version.

Authenticated team members can list/read/upload screenshots on QA tickets. Only
the uploader or Admin can delete them. In local shared-password mode, the local
Admin can operate local development screenshots. Cloud storage cannot use shared
authentication. There are no tenant/private-ticket boundaries in this single-team
application; those require an explicit later design.

## Upload and lifecycle

Paste, drag/drop and the file picker use one accessible preview/queue. Frontend
file checks improve feedback; server checks are authoritative. Upload is raw binary
streamed through the authenticated API with a hard 10 MiB bound, avoiding multipart
temporary files. Server decodes only single-frame PNG/JPEG/WebP, limits dimensions
to 8192 per side and 16 million pixels, and re-encodes a fresh PNG from decoded pixels.
Metadata/EXIF/text/profile is removed. Normalized output is also limited to 10 MiB.
SVG, animated files, malformed/decompression-bomb images and oversize inputs fail.
A fixed `screenshot.png` display filename avoids publishing user filenames.

Migration 005 adds metadata with ticket FK, uploader subject/name, private random
object key, generation, pending/ready/failed/deleting state, PNG MIME, byte count,
SHA-256 checksum and creation time. Reserving a slot locks the ticket and counts
pending+ready images (limit ten). An object write happens outside the DB transaction;
only a successfully finalized ready record can be listed/read. Failed writes/finalize
attempt compensating deletion; failures retain metadata for reconciliation. Object
reads verify checksum, are served as PNG with nosniff and private/no-store headers,
and require authentication on every request.

Deletion marks metadata deleting first so reads are revoked immediately, then
removes the exact object generation and metadata. Failed deletion remains queued.
Operator cleanup `python -m attachments --apply` retries incomplete states older
than five minutes. It never deletes ready objects. A future scheduler requires
separate infrastructure approval. Upload success, delete request, membership and
session actions are audited; logs never contain screenshot bytes or object keys.

Failed frontend uploads preserve only failed/unsent images and reopen the saved
ticket for retry; saving again cannot duplicate the ticket or successful uploads.
Queues/object preview URLs are cleared when switching tickets. Storage limits and
retention are conservative development defaults, not a legal retention policy.

## Validation

47 tests passed with SQLite and disposable PostgreSQL 16, including shared identity
and persistence regressions. Screenshot tests cover allowed formats, metadata
stripping, dimension/byte limits, SVG/malformed rejection, local path safety,
unauthenticated and CSRF denial, quota, uploader/Admin delete authorization, private
responses, corruption detection, response-loss compensation, failed-compensation
reconciliation and failed-delete read revocation. Mocked cloud SDK contract tests
check existing-bucket privacy requirements and generation preconditions; they do
not claim live GCS integration validation.

Chromium smoke passed: local tickets/metrics; paste/drop/file screenshot queue,
private upload/read/deletion, and an injected upload outage followed by retry with
no duplicate ticket; intercepted managed signup and pending access denial. No real
email or identity request occurred. JS syntax/whitespace/dependency checks pass.

## Rollback and release gates

No live data migration occurred. Additive metadata can remain on code rollback;
never delete objects as part of reverting code. Disable the feature to stop new
uploads while keeping private stored data available to a compatible revision.
The legacy SQLite importer explicitly rejects populated extended audit, identity
or attachment tables so these records cannot be silently lost. A migration after
using these features needs an extended reviewed importer and object reconciliation.

Before public release: approve bucket region, retention/deletion/versioning policy,
quota/abuse limits, image workload memory/concurrency budgets, optional malware
scanning policy and recovery/cleanup ownership; validate ADC/IAM and bucket policy
in private staging. Hard-crash/process/object-store races need scheduled ongoing
reconciliation and monitoring. This PR does not enable infrastructure or deployment.
