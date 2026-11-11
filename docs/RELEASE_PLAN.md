# PERPERIS QA — remaining private staging and release plan

> **Historical — superseded on 2026-11-11.** QA-CLOUD-01 to 05 were merged on
> 2026-11-10 (PRs #1–#5) and QA-CLOUD-06 (#7) was merged as code but never set
> up. On 2026-11-11 the owner and creator decided that PERPERIS QA becomes a
> front end on GitHub issues in `krummb/kattens-pt`, with no ticket database
> and no storage of its own (members as an email→role list in configuration,
> signed session cookies; decision 4.2). The current plan is [QA-GITHUB-FRONTEND.md](QA-GITHUB-FRONTEND.md).
> The text below describes the earlier self-hosted ticket design; its
> staging-evidence and secrets guidance may still inform milestone 4 there, but
> its Cloud SQL, importer and data-cutover sections no longer apply.

Code Stories QA-CLOUD-01 through 05 are reviewable stacked PRs, not merged or
deployed. Only the QA repository is in scope. No paid infrastructure exists from
this work and no cloud account access was attempted. Broad authorization to keep
coding does not select a billing account, region or a safe production cutover.

## Review order

1. QA-CLOUD-01: assessment, deployment risks and cloud startup guard.
2. QA-CLOUD-02: PostgreSQL migrations, parity-preserving dry-run importer and conflicts.
3. QA-CLOUD-03: verified identity, membership/RBAC, expiring sessions and audit attribution.
4. QA-CLOUD-04: private screenshots, bounded image handling and reconciliation.
5. QA-CLOUD-05: locked/audited build, hardened runtime and end-to-end local checks.

Each PR targets its predecessor to keep the Story diff separate. Retarget/rebase
following PRs onto main only after the preceding Story is reviewed/merged by the
owner (a squash merge may require rebasing to avoid repeating the parent diff). This
work does not merge any PR. Review the full stack together for security and schema
compatibility before adopting opt-in identity or screenshot features. Pillow 13
security fixes in Story 05 are required before using image handling in a release.

## Concrete next Story: QA-CLOUD-06 — approved private staging infrastructure

This is the next external-action boundary; do not execute it from this plan.
First record the QA-specific project/billing owner, region/residency, budget and
alerts, SQL tier/availability/network choice and connection cap, domain/ingress,
backup/PITR retention and recovery objectives, attachment retention/versioning and
cleanup ownership. No training project, database, bucket, auth audience, principal
or secret may be reused. Do not create service-account keys.

Proposed initial configuration to price and review (not a promise of free usage):

| Component | Starting decision to review |
| --- | --- |
| Cloud Run | Dedicated QA service/account; min instances 0, max 2 initially; application concurrency 4; private staging invokers; no public prototype revision |
| Cloud SQL | Small managed PostgreSQL instance; tier/HA and region priced against recovery requirements; connector/Unix socket or approved private path; migration user separate from runtime |
| Pool budget | 5 connections per process x 2 max instances = 10, plus migrations, maintenance and recovery headroom; validate database limit before adopting |
| Image memory | Start by measuring 16MP decode/re-encode peak at concurrency 4; budget memory and reduce concurrency before choosing instance memory/price |
| Identity Platform | Dedicated project, email/password policy, verified email, domain and template configuration; app membership stays pending until Admin approval |
| Cloud Storage | Dedicated same-residency private bucket; uniform access and enforced public access prevention; no public ACL/signed URL; approve versioning, retention/deletion and quota policies |
| Secrets | Secret Manager references, least-privilege runtime reads; no secrets in source/build arguments or service-account keys |
| Build/CI | Artifact Registry plus federated short-lived GitHub identity restricted to QA repo/branch/environment; immutable image digest; no auto-deploy on untrusted PRs |
| Logging | Operational metadata only; redact passwords/tokens/DB URLs, ticket bodies, identities, screenshots/object keys and signed URLs; alerts for failed auth, upload cleanup, DB/pool failures |

Infrastructure implementation should be a separate reviewed, budgeted change.
A deployment must use a future revision whose cloud guard has been deliberately
replaced with validated production configuration checks; never edit around it to
expose the shared-password revision. Staging may need a locally executed synthetic
smoke before that replacement is reviewable. Production must remain blocked until
the owner explicitly authorizes the release and its traffic/ingress configuration.

## Private staging evidence required

- Database: actual connector/TLS/IAM and runtime privilege boundaries; explicit
  migrations using a different identity; old-schema/unknown-schema startup refusal;
  real backup/restore/PITR rehearsal and recorded recovery times. Restrict update/
  delete privileges on status/security/change audit tables; app should only append.
- Identity: real issuer/audience and revoked/disabled behavior, verification/reset
  email delivery, password policy and enumeration protections; explicit operator
  bootstrap; pending, Developer, Tester and Admin workflows. No production email
  should be sent during synthetic rehearsal without owner authorization.
- Sessions/network: canonical HTTPS origin, Secure cookie, CSRF and logout replay,
  proxy trust/IP throttling behavior, membership disable during concurrent requests,
  API no-store and CSP; no identity token/password in logs. Validate database-backed
  throttling across two instances rather than relying on a single process.
- Screenshots: actual bucket public access denial, upload generation precondition,
  private reads/deletes, invalid images, quota concurrency, killed uploads and
  orphan/deleting reconciliation. Establish scheduled cleanup and recovery alerts.
  Validate memory/concurrency under authorized, bounded image workloads.
- Build: dependency and base OS image scans; source provenance/digest, least-privilege
  deploy identity, explicit staging-only invokers, no database/credentials/private
  files in image/context or artifacts. Separate test data from operator inputs.

No live GCP integration or performance claim can be inferred from the local suite.

## Operator data cutover (separate Story and approval)

Use only an explicit owner-approved authoritative SQLite backup. Stop writes,
check integrity, rehearse restore and run the importer in dry-run first. Compare
all fields/counts/sequences/status events and every dashboard metric privately;
record only aggregate success/failure in review. Do not export private rows to PRs.
The legacy importer refuses identity/attachment/extended-audit-bearing sources;
if local use has accumulated those features, extend and review their migration
and object checksums first. Never discard them to make an import pass.

After final parity, import once into the empty target under a write freeze and
verify again before routing any users. Retain encrypted/private original backup
read-only under the approved retention policy. Initially keep traffic/invokers
private. Separately approve domain/DNS/public ingress after verified auth, backups
and monitoring are demonstrated.

## Rollback boundaries

Before new cloud writes, restore the original local/private application and
unchanged authoritative database. After new cloud writes, an old SQLite copy is
stale: stop writes, reconcile a reviewed reverse export or restore forward using
PostgreSQL backup/PITR. Do not silently switch back and lose new tickets/history.
Roll back application image only against a compatible schema; additive migrations
and object metadata stay intact. Never delete audit/history or private objects to
revert code. Revoke sessions after any security-sensitive identity change. Rehearse
this procedure before real cutover, including the last point where rollback is
lossless and who owns each decision.
