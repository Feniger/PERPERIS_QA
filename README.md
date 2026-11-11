# PERPERIS QA — bug tracking & engineering health for PERPERIS

A **separate** QA web application for the PERPERIS training app. It does not
read or write the training app, its database, its Google project or its
secrets.

> **Direction (decided 2026-11-11):** PERPERIS QA is becoming a **front end on
> GitHub issues in `krummb/kattens-pt`**, through a narrowly scoped GitHub App.
> GitHub issues become the single source of truth for bugs; this app keeps its
> own design, login and dashboard. The plan, issue conventions, settled
> decisions and milestones are in
> [docs/QA-GITHUB-FRONTEND.md](docs/QA-GITHUB-FRONTEND.md).
>
> Until the GitHub adapter lands (milestones 1–3 there), the code below still
> stores tickets in its own SQLite/PostgreSQL database. That storage is
> transitional and will be removed.

## What exists today (`main`)

- PERPERIS-style app shell and Today dashboard (sidebar, oversized headings,
  thin dividers, square buttons; desktop and mobile)
- Tickets: report, search, filter, change status, assign, resolution notes,
  with optimistic-concurrency protection (a stale edit gets a conflict, not a
  silent overwrite)
- Ticket metadata: reporter, severity, category, discovery round, area,
  environment, deploy version, GitHub link
- Append-only status events; preserved first fixed/deployed/verified/closed
  timestamps
- Deterministic metrics and an SVG chart of found, fixed and verified bugs per
  testing round, with a per-round crossover marker; backlog = discovered minus
  ever verified. **No fictional KPI data.**
- Sign-in: shared operator password for local use, or opt-in managed sign-in
  (Identity Platform, verified email, admin-approved membership, roles
  Admin/Developer/Tester) — see QA-CLOUD-03
- Opt-in private screenshots (paste/drop/upload, local or private GCS bucket)
  — see QA-CLOUD-04
- CI on every pull request: unit tests, PostgreSQL 16 and 18, browser smoke
  tests, container checks

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
export PERPERIS_QA_PASSWORD='a-long-unique-admin-password'
export PERPERIS_QA_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
uvicorn app:APP --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787 . The SQLite database file is created as
`qa.sqlite3` and is excluded from source control.

### Security / limitations

Local mode uses a shared operator password and is meant for localhost only.
Startup is guarded by `runtime_guard.py`: on Cloud Run, or with
`PERPERIS_QA_ENV` set to anything other than `local`, the app refuses to start
unless it is a validated `staging` configuration (managed sign-in, TLS
PostgreSQL, private or disabled screenshots). `production` is always blocked.
Nothing is deployed: the staging setup in QA-CLOUD-06 was written for the old
ticket-database design, was never run, and will be revised for the GitHub
front end (milestone 4 in docs/QA-GITHUB-FRONTEND.md).

Testing-round labels follow `R01`, `R02`, etc. A report counts as **found** in
its discovery round; the first fixed/verified transition counts in its
resolution round (if blank, the discovery round). Later reopens do not erase
the original first-fix milestone, so the trend plots **first fix/verification
milestones**, not all transitions. The crossover marker is descriptive, not
predictive.

## Run tests

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## History and runbooks

The Cloud Foundation Stories (QA-CLOUD-01 to 05) were merged on 2026-11-10;
QA-CLOUD-06 (staging) was merged as code but never set up. Their runbooks
remain accurate for the current code, and parts of them (ticket persistence,
the PostgreSQL importer, database-backed sessions) will be retired by the
GitHub front end:

- [QA-CLOUD-01](docs/QA-CLOUD-01.md): assessment, risks, startup guard
- [QA-CLOUD-02](docs/QA-CLOUD-02.md): PostgreSQL persistence and importer
- [QA-CLOUD-03](docs/QA-CLOUD-03.md): verified identity, membership, roles
- [QA-CLOUD-04](docs/QA-CLOUD-04.md): private screenshots
- [QA-CLOUD-05](docs/QA-CLOUD-05.md): build hardening, CSP, container
- [QA-CLOUD-06](docs/QA-CLOUD-06-STAGING.md): free private staging (Cloud Run
  + Neon) — superseded in part, see above
- [RELEASE_PLAN](docs/RELEASE_PLAN.md): the pre-2026-11-11 release plan, kept
  as history
- [Design system](docs/design/QA-DESIGN-SYSTEM.md): the PERPERIS-style shell
