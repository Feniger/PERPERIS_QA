# PERPERIS QA — standalone bug tracking & engineering health

A **separate**, local-first QA web application. It does not read or write the PERPERIS training app or its database.

## What works

- Password-protected dashboard (single operator in this prototype)
- SQLite-backed tickets: report, search, filter, change status, assign, and record resolution notes
- Ticket metadata: reporter, severity, category, discovery round, affected app area, environment, deploy version, GitHub link
- Append-only status events, preserved first fixed/deployed/verified/closed timestamps
- Deterministic metrics and a scalable SVG line graph of found, fixed, and verified bugs by testing round
- Per-round crossover marker when fixes meet/exceed new discoveries; current backlog = total discovered minus ever verified
- Responsive desktop/mobile layout, with empty states and **no fictional KPI data** in the database

## Run locally

Requires Python 3.11+.

```bash
cd perperis-qa
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PERPERIS_QA_PASSWORD='a-long-unique-admin-password'
export PERPERIS_QA_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
uvicorn app:APP --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787 . The SQLite database file is created in `qa.sqlite3` and intentionally excluded from source control.

### Security / limitations

**This is a tested prototype, not a production-hardened public multi-user system.** Do not deploy it to `qa.perperis.app` as-is. Before internet access, implement real user accounts and RBAC, persistent session rotation/expiry, complete audit actor attribution, login rate limiting, secure HTTPS cookie deployment, backup/restore, and monitored upgrades. The current authentication is a shared admin password; the free-text reporter field is not a verified user identity. Keep the app on localhost or behind an authenticated private network for initial testing.

The URL `qa.perperis.app` is proposed, **not configured**. Source lives in the standalone `Feniger/PERPERIS_QA` repository.

Testing-round labels should follow `R01`, `R02`, etc. A report counts as **found** in its discovery round; the first fixed/verified transition counts in its specified resolution round (if blank, the discovery round). Later reopens do not erase the original first-fix milestone, and this prototype does not yet support multiple fix cycles per issue. Hence the trend plots **first fixes/verification milestones**, not all transitions. The chart marks the first round containing at least one discovered bug where fixes >= discoveries; its marker is descriptive rather than predictive.

## Run tests

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## Cloud foundation (QA-CLOUD-01)

See [the implementation assessment and proposed architecture](docs/QA-CLOUD-01.md)
for security risks, verified registration and roles, private screenshot planning,
architecture approvals, and migration/rollback strategy.

This prototype deliberately **refuses startup on Cloud Run** (`K_SERVICE` present)
or when `PERPERIS_QA_ENV` is anything other than `local` (the default). There is no
cloud override. Keep binding to `127.0.0.1`; the guard is not network isolation.
No cloud resources or deployment pipeline are created by this Story.

Database initialization now happens during ASGI lifespan, after the runtime check,
not on Python import. ASGI runners must keep lifespan enabled; TestClient users
must use a context manager. Existing SQLite schema and ticket semantics remain
unchanged. Do not put databases, backups, screenshots, keys or secrets in Git or
container build contexts.

## Next milestones

1. Proper multi-user auth, roles, ticket comments and attachment storage; migrate session and audit to verified identities.
2. Approved deployment pipeline from this QA repository to a dedicated QA Google Cloud project; private staging first, then separately approved production and DNS.
3. Import PERPERIS feedback rounds with duplicate review and preserve original GitHub issue/PR references; no invented statuses.
4. GitHub synchronization as a controlled integration, with ownership rules for when status is driven by code vs human verification.

### PostgreSQL rehearsal (QA-CLOUD-02)

[Persistence and importer runbook](docs/QA-CLOUD-02.md) describes versioned
migrations, the opt-in PostgreSQL backend, dry-run import and rollback tests.
SQLite remains the local default. Setting `PERPERIS_QA_DATABASE_URL` selects
PostgreSQL but does **not** permit cloud startup or make shared-password auth safe.

### Verified identity (QA-CLOUD-03)

[Identity and role runbook](docs/QA-CLOUD-03.md) covers opt-in managed registration,
verified email, pending membership, Admin/Developer/Tester permissions, revocable
sessions and explicit private Admin bootstrap. It requires a separately configured
QA identity project; no live provider or public deployment is enabled by this code.

### Private screenshots (QA-CLOUD-04)

[Screenshot storage runbook](docs/QA-CLOUD-04.md) covers the disabled-by-default
paste/drop/file workflow, local development storage, private existing-bucket GCS
adapter, image validation, quotas, permissions and reconciliation. It creates no
bucket and does not permit cloud startup.

### Build and release gates (QA-CLOUD-05)

[Build hardening](docs/QA-CLOUD-05.md) documents the hashed runtime lock,
non-root container, CSP/privacy checks and local browser/container tests.
[Remaining release plan](docs/RELEASE_PLAN.md) records the separate private staging,
budget, IAM, backup/restore and production-cutover work. Cloud startup is still
blocked; no infrastructure or deployment is enabled by building the image.
