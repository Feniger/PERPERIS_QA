# QA-CLOUD-01 — Cloud foundation assessment

Status: proposed architecture, **not approved for provisioning or deployment**.
Inspected `Feniger/PERPERIS_QA` at `8e5745d105551ce2387ddc9fa7b6c28a0c6b53ec`
(2026-11-10). No training application checkout, cloud resources, operator database,
credentials, or private files were needed or accessed. Only synthetic test data was used.

## Existing implementation

| Area | Evidence and current behavior |
| --- | --- |
| Application | `app.py`: one FastAPI application (`APP`), synchronous handlers, Pydantic input models, Uvicorn local command. No service calls or training-app imports. |
| Persistence | `connection()` opens SQLite per operation, enables foreign keys, commits or rolls back, and closes. `initialize()` runs idempotent DDL. Originally executed during import; this PR moves it into lifespan after the runtime guard. Default path is repository-local `qa.sqlite3`; `PERPERIS_QA_DB` overrides it. |
| Authentication | `login()`, `current_user()`: shared environment password, HMAC of constant `admin`, cookie `qa_session`, one returned identity `Admin`. Cookie is HttpOnly/SameSite Strict; Secure depends on `PERPERIS_QA_HTTPS=1`. Missing password/short signing secret produces 503 for protected endpoints. No registration, email verification, roles, password reset, revocable sessions, or rate limiting. |
| Mutation protection | `guard_mutation()` requires `X-QA-Request: 1` and rejects a supplied Origin differing from `request.base_url`. Login does not use this guard. No CORS middleware; reverse-proxy origin/forwarded-header behavior needs deliberate testing. |
| API | Public HTML, allowlisted JS/CSS; login/logout/me; authenticated list/create/update tickets and analytics. No delete, individual ticket read, event-history read, comments, attachment, or user APIs. OpenAPI JSON is still available at `/openapi.json` despite disabled docs UI. |
| Frontend | `static/index.html`, `app.js`, `styles.css`: same-origin fetch with cookie and mutation header, create/update modal, client-side search/filters, deterministic SVG chart, responsive layout. Strings are escaped for HTML; issue links allow HTTPS only. CSS loads external Google Fonts. No paste/drop/file attachment controls or registration UI. |
| Tests | `tests/test_app.py`: four ordered tests sharing one client/database cover login, validation, metrics, milestones/reopen, and asset allowlisting. Test DB is temporary; original tests require undeclared `httpx`. No existing CI, container, deployment manifests, migration framework, or dependency lock. |
| Repository | One source commit; tracked files contain no database or credential files. Ignore rules covered main SQLite files, but not all WAL/journal/backup variants. No `AGENTS.md` found. Public repository: never commit private migration inputs or test exports. |

### Ticket and history contracts to preserve

`tickets` has an autoincrement integer ID; title/description; free-text reporter;
category/severity/status; discovery round; area/environment/deploy version;
free-text assignee; issue URL/resolution notes; created/updated timestamps; first
fixed/deployed/verified/closed timestamps; and fixed/verified rounds. Categories:
ui_ux, workout, coach_ai, data, performance, mobile, auth, deployment, other.
Severities: critical, high, medium, low. Statuses: reported, triaged, in_progress,
fixed, deployed, verified, closed, reopened. There is no enforced transition graph.

`ticket_events` has integer ID, ticket FK, old/new status, actor text, and timestamp.
Creation and status changes write an event in the ticket transaction; same-status
assignment/note changes do not. History is append-only through the current API,
not protected against database writers. Every historical actor is `Admin`, not a
verified person. No event browsing UI currently exists.

Moving directly to closed infers all preceding first milestones. Reopening retains
those milestones and rounds. Metrics count all tickets as found; first fixed and
verified timestamps count as ever fixed/verified. Open means status is neither
verified nor closed. Backlog is total minus ever verified, so a reopened issue can
be open while absent from backlog. Round attribution is discovery vs first fix /
verification round, with numeric `R` labels sorted numerically. Crossover is the
first round with discoveries > 0 and fixes >= discoveries, not a cumulative claim.
Do not silently change these meanings in the migration.

## Risks and release gates

| Priority | Risk | Required mitigation / gate |
| --- | --- | --- |
| Blocker | Shared bearer cookie is deterministic, has no issued/expiry timestamp, and server accepts a copied cookie indefinitely while the secret remains unchanged. Logout only deletes the browser cookie. Password changes do not revoke it. All users have admin powers. | Replace authentication and sessions; verified identities and server-side authorization. This PR blocks Cloud Run and nonlocal configured modes. |
| Blocker | Cloud Run local filesystem is ephemeral and per-instance. SQLite loses/diverges data across restarts, revisions, or scaling; mounting object storage does not solve SQLite locking. | Managed PostgreSQL persistence and controlled migrations before removing the guard. Never ship SQLite data inside an image. |
| High | Reporter/assignee are arbitrary strings; audit actor cannot identify individuals. Status-only history omits note/assignment edits and auth/role changes. | Preserve legacy text exactly; add verified IDs for future actions and append-only expanded events. Never invent historical identity mappings. |
| High | Concurrent SQLite updates read old state before updating; no optimistic version checks, migration versions, pagination, or bounded list queries. | Add conflict control and migration discipline in PostgreSQL story; retain ticket/event atomicity. Test concurrent edits and connection limits. |
| High | Cookie Secure is optional; no brute-force protection, revocation, managed reset/verification flow, or application security header policy. | Managed identity, HTTPS sessions, origin/CSRF tests through the proxy, rate limits and appropriate CSP/security headers before release. Local guard is an accident barrier, not network isolation. Local servers must still bind loopback. |
| High | Backups, retention, PITR, recovery objectives and restore rehearsal do not exist. Initializer is DDL, not a migration system. | Approve recovery policy, enable backups, rehearse restore and migration on synthetic/staging data before cutover. |
| Medium | Pydantic length validation precedes `.strip()`, allowing whitespace-only required text; enums are application-only. Dependency ranges float and test dependency was missing. | Normalize/validate inputs and add DB constraints after profiling legacy data; pin a reviewed deployable dependency set and scan it in a later build story. |
| Medium | Private screenshots may contain personal information or secrets. No attachment authorization, validation, cleanup or quotas exist. | Private object storage; authenticated ticket access; image validation, quotas, retention and deletion policy. Never log bytes or signed URLs. |
| Medium | Runtime service permissions and logging could expose secrets or data; external fonts contact third parties. | Separate QA project/service account; least privilege; Secret Manager; log metadata only; approve self-hosted fonts/CSP policy. |

This PR does not make the local shared-password authentication production safe.
The runtime guard rejects any presence of Cloud Run's `K_SERVICE` (including empty)
and any `PERPERIS_QA_ENV` value other than `local` (default). It runs before startup
DDL and on authenticated API setup. There is no production override. Database
imports are now side-effect free; ASGI runners/tests must execute lifespan.

## Recommended simplest production architecture (approval required)

Use one **dedicated PERPERIS QA Google Cloud project**, with no training-app
service accounts, buckets, database, auth tenant, secrets, or runtime dependencies.
Keep FastAPI serving the existing static frontend and API from one Cloud Run
service and one origin. No SPA rewrite, Kubernetes, Redis, or microservice split.

- **Cloud SQL for PostgreSQL** for tickets, users/roles, events, and attachment
  metadata. Relational storage fits existing foreign keys and transactional
  ticket/event writes; Firestore would require a larger rewrite. Use Cloud SQL
  connector / supported Cloud Run connection with IAM database authentication
  where supported; otherwise use a scoped database password from Secret Manager.
  Use separate runtime and migration privileges, bounded connection pools and
  Cloud Run max instances sized to the database connection budget. No public
  database access from arbitrary addresses. Approve connector/network path and
  any private networking cost before implementation.
- **Identity Platform email/password authentication** in the QA project for
  registration, verification and password reset. Provider handles passwords;
  application never stores or logs them. Begin invite/allowlist registration,
  verified email required, with no self-service privilege selection. Verify
  provider tokens server-side (issuer, audience, signature, expiry and revocation
  policy), require `email_verified`, then check active QA membership in the DB.
  A verified account alone does not grant access. Exchange fresh authentication
  for short-lived Secure/HttpOnly/SameSite cookies with explicit server expiry,
  rotation/revocation and CSRF/origin protection on session and mutation routes.
- **Private Cloud Storage bucket** for screenshots, with uniform bucket-level
  access and public access prevention. Initially proxy bounded upload/download
  through authenticated FastAPI routes to keep access checks simple; revisit
  short-lived signed URLs only if volume justifies the extra lifecycle. Never
  expose the bucket or accept arbitrary local paths/remote fetch URLs.
- **Artifact Registry**, **Secret Manager**, and a dedicated least-privilege
  Cloud Run service account; platform logs/monitoring with no ticket bodies,
  passwords, tokens, emails, screenshot data or signed URLs. CI uses federated
  short-lived credentials rather than service-account keys. No infrastructure
  or deployment workflow is introduced by this Story.

Before launch, approved verified-user routes need to be reachable from clients;
Cloud Run IAM by itself does not validate Identity Platform tokens. Decide ingress
and any load balancer/IAP policy explicitly. Restrict staging invokers while
building, and never allow public invocation of the shared-password revision.
Scale-to-zero can reduce application costs, but Cloud SQL has ongoing costs.
Do not promise a free-tier production system.

### Proposed roles and registration policy

All authorization must be checked server-side on every route; hiding UI is not
an access control. Proposed initial workspace is a single QA team, not multiple
tenants. Future tenant isolation requires an additional explicit design.

| Capability | Admin | Developer | Tester |
| --- | --- | --- | --- |
| Read tickets, metrics, history and authorized screenshots | Yes | Yes | Yes |
| Report tickets and add screenshots | Yes | Yes | Yes |
| Triage, assign, mark in progress / fixed / deployed | Yes | Yes | No |
| Verify, close, reopen | Yes | No | Yes |
| Approve membership, change roles, disable users | Yes | No | No |

Approve this matrix before changing current workflows. New verified registrations
remain pending until an Admin accepts them (initial assigned role Tester). Store
roles in the application database, deny inactive users immediately, audit role
changes, prevent removal of the final Admin, and bootstrap the first Admin through
a one-time private operator procedure. Never use first registrant wins or a
client-supplied role. New event actors use immutable provider subject IDs; preserve
legacy display text separately. Identity changes must not rewrite old history.

### Screenshot attachment plan

Paste, drag-and-drop and file picker all feed the same frontend preview/upload
queue after ticket creation, with keyboard-accessible picker and visible errors.
API checks verified membership and ticket authorization for creation, read and
delete. Proposed initial policy: PNG/JPEG/WebP only, 10 MiB/file, at most 10 files
per ticket, bounded dimensions/pixel count and decoded size; approve limits and
retention before implementation. Validate actual decoded content rather than
extension/MIME, reject SVG/HTML and malformed images, re-encode safely to remove
metadata, use random object names and sanitized display filenames. Keep uploads
outside `static/` and never store bytes/base64 in ticket rows or the Git repository.

Metadata records ticket, uploader subject, object key/generation, MIME, size,
checksum, creation time and upload state. Object storage and SQL are not one
transaction: use pending/ready states and compensating cleanup; only ready objects
are readable. Deletion must revoke app access and schedule orphan cleanup under
an approved retention policy. Serve a safe fixed Content-Type with nosniff and
private/no-store caching. Log attachment IDs and outcomes, never content. Add
authorization, invalid-image, quota, failed-upload and orphan-cleanup tests.

### Decisions needing explicit approval before infrastructure work

1. Dedicated project/billing owner, region and residency, monthly budget/alerts,
   Cloud SQL tier/availability, connection/network approach and instance cap.
2. Identity Platform, registration invite/allowlist and approval policy, role matrix,
   Admin bootstrap, session lifetime/revocation and email delivery configuration.
3. Screenshot formats/limits, retention/deletion, bucket location, who may delete,
   privacy obligations, and whether additional malware scanning is required.
4. Database backup retention/PITR, recovery time/data loss objectives, downtime
   window, authoritative legacy database and migration operator.
5. Initial staging/production ingress, domain/DNS ownership, CI deployment identity,
   and the later explicit approval to create resources and deploy.

These are proposals for review, not requests to provision as part of this PR.

## This Story's changes and verification

- `app.py`: refuse Cloud Run / nonlocal runtime, initialize DB during lifespan
  only, and check the guard on protected API setup. Ticket SQL, API payloads,
  milestone/analytics calculations and static UI are unchanged.
- `tests/test_app.py`: enter/exit lifespan in existing test client.
- `tests/test_foundation.py`: disposable subprocess tests for import safety,
  fail-closed startup before any DB file is created, guarded API without lifespan,
  restart preservation, exact historical event sequence, same-status event
  behavior, milestone/round invariants, metrics, CSRF rejection and rollback.
- `requirements-dev.txt`: declare the HTTP test client required by the suite.
- `.gitignore`: add database sidecars, backups/dumps, local attachments and common
  private key/credential files. Ignore rules do not replace staged-file review.
- `README.md` and this document: runtime contract, design, risks and next steps.

Baseline: 4 tests passed. After changes: 9 tests passed using Python 3.12.14,
both in the supplied environment and a fresh project-local venv. Fresh venv:
FastAPI 0.162.2, Starlette 1.10.0, HTTPX 0.28.1, Uvicorn 0.54.0;
`pip check` passed. Starlette emits an HTTPX TestClient deprecation warning;
the tests pass, but the eventual locked build should review client compatibility.
Repeat in a project-local venv with `pip install -r requirements-dev.txt`, then
`python -m unittest discover -s tests -v`. Dependency ranges are still unlocked;
this PR does not claim a reproducible release image. No cloud integration,
PostgreSQL migration, managed auth, attachment or load testing is claimed.

## Migration and rollback strategy

**This PR:** no schema/data migration, cloud resource, or credential change. Existing
SQLite files remain in place. Initializer runs at successful local startup rather
than import. Existing events are not edited. Rollback is reverting the source
commit; never expose the reverted prototype publicly. Test runners that previously
relied on import-time initialization must enter lifespan or explicitly initialize
an isolated test DB.

**Future migration, after approval:**

1. Identify the operator-owned authoritative SQLite DB privately. Do not discover
   or copy unrelated local files. Inventory schema, ticket/event IDs, row counts,
   orphan events, distinct enum values, timestamps, nulls, rounds and sequence
   high-water marks without putting private rows in logs or PRs. Establish a
   consistent SQLite backup using the backup API (not a raw live-file copy), check
   integrity and rehearse restoration. Encrypt and restrict backup access.
2. Introduce versioned PostgreSQL migrations and a repeatable dry-run importer
   in a separate Story. Preserve every original ID, text, timestamp, first
   milestone and event ordering; reseed sequences above existing high-water
   marks, including SQLite AUTOINCREMENT history. Map legacy actor `Admin` to an
   explicit legacy identity marker, not a guessed user. Keep reporter/assignee
   labels; future verified IDs are additive. Normalize only through a reviewed
   mapping and abort on unresolved constraints rather than dropping rows.
3. Rehearse on a private staging copy, compare counts and complete field-level
   checksums securely, FK integrity, event ordering, milestone invariants and all
   analytics results. Test rollback, authorization and concurrent updates. Keep
   attachment migration separate (none exist in the inspected schema).
4. Use a maintenance/read-only window: stop local writes, take final consistent
   backup, import once with an import ledger/transaction, validate, and only then
   switch the approved revision. Avoid dual writes. Retain the original database
   read-only with approved retention; never commit it or bake it into an image.
5. Before accepting cloud writes, rollback can switch back to the unchanged local
   database and previous local revision. After cloud writes start, returning to
   old SQLite would lose new data: stop writes and perform an approved verified
   reverse export/reconciliation or restore forward in PostgreSQL. Application
   rollback uses a compatible prior revision and additive schema changes; do not
   drop new schema/history to roll back code. Revoke new sessions if identity
   configuration changes. Rehearse Cloud SQL restore/PITR against approved RPO/RTO.

## Recommended next Story

**QA-CLOUD-02 — versioned persistence and migration rehearsal.** After approval of
the architecture and data contracts, introduce a PostgreSQL persistence layer,
versioned migrations and a dry-run SQLite importer, tested against disposable
PostgreSQL with synthetic data. Preserve tickets, audit history and exact metrics;
add concurrency and migration parity/rollback tests. Keep the cloud startup guard
and do not provision paid resources or deploy as part of that code-only Story.

Then implement verified identity/RBAC and session lifecycle, then private screenshot
attachments, and finally approved infrastructure, private staging and a separately
approved production cutover. Remove the startup block only after persistent storage,
verified-user authorization and release gates have all been demonstrated.
