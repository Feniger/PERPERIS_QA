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
> QA keeps **no storage of its own**: tickets are GitHub issues, members are an
> email→role list in configuration and sessions are signed cookies (decision
> 4.2). For local development it runs against an in-memory stand-in for GitHub.

## What exists today (`main`)

- PERPERIS-style app shell and Today dashboard (desktop and mobile)
- Tickets are GitHub issues labelled `qa` in `krummb/kattens-pt`, read and
  created through the "PERPERIS QA" GitHub App: list, search, filters, ticket
  detail, and "Report a bug" (quick form and dialog) creating a real issue
- Deterministic metrics and an SVG chart of found, fixed and verified bugs per
  testing round, derived from issue labels, milestones and timeline events.
  **No fictional KPI data, no stored counters.**
- Sign-in: Identity Platform (verified email) plus the member list
  `PERPERIS_QA_MEMBERS` (Admin / Developer / Tester), or a shared operator
  password for local use only
- Status changes by role (Triage / Fix / Deploy / Verify / Close / Reopen),
  written to the issue with a "<change> by <name> via PERPERIS QA" comment
- Screenshots in QA's private bucket, visible only inside QA: paste, drop or pick
  in the report dialog or on a ticket, thumbnails, one "📎 N screenshots" comment
  on the issue, deleted 30 days after Verify and after 365 days at the latest
  (see `docs/QA-GITHUB-FRONTEND.md`, decision 4.1)
- Staging is live: https://perperis-qa-staging-982639404002.europe-north1.run.app
  (members only; see `docs/QA-CLOUD-06-STAGING.md`)
- App icon and home-screen install: a Q in the PERPERIS K's style (`static/icon.svg`; PNGs rendered
  with `python tools/render_icons.py`), web manifest "PERPERIS QA" / "QA"
- CI on every pull request: unit tests, browser smoke tests, container checks

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

Open http://127.0.0.1:8787 . Without further settings, tickets live in memory
(open round `R01`) and disappear on restart; add
`PERPERIS_QA_SCREENSHOTS=memory` to try screenshots (kept in memory too). To read
and create real issues,
also set `PERPERIS_QA_TICKET_SOURCE=github` and the GitHub App settings in
[docs/QA-GITHUB-FRONTEND.md](docs/QA-GITHUB-FRONTEND.md) (the App key is a
secret; never commit it).

### Security / limitations

Local mode uses a shared operator password and is meant for localhost only.
Startup is guarded by `runtime_guard.py`: on Cloud Run, or with
`PERPERIS_QA_ENV` set to anything other than `local`, the app refuses to start
unless it is a validated `staging` configuration (managed sign-in, GitHub as
the ticket source, a member list, the screenshot bucket, which must be
private). `production` is always blocked.
Staging (milestone 4) runs on Cloud Run in QA's own Google project
`perperis-qa-staging`, set up by `infra/staging/setup.sh` and
`infra/staging/screenshots-bucket.sh` and deployed by the manual "Deploy
staging" workflow (Claude Code may run it after merging; see §6 of QA-GITHUB-FRONTEND); see
[docs/QA-CLOUD-06-STAGING.md](docs/QA-CLOUD-06-STAGING.md).

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
are history now: the GitHub front end retired ticket persistence, PostgreSQL,
the importer, database-backed sessions and members (QA-CLOUD-02 entirely).
Screenshots returned on 2026-11-11 without a database (`screenshots.py`,
reusing QA-CLOUD-04's image checks). The startup guard, CSP and container
hardening from 01, 03 and 05 still apply in updated form:

- [QA-CLOUD-01](docs/QA-CLOUD-01.md): assessment, risks, startup guard
- [QA-CLOUD-02](docs/QA-CLOUD-02.md): PostgreSQL persistence and importer
- [QA-CLOUD-03](docs/QA-CLOUD-03.md): verified identity, membership, roles
- [QA-CLOUD-04](docs/QA-CLOUD-04.md): private screenshots (database version;
  the current design is decision 4.1 in QA-GITHUB-FRONTEND)
- [QA-CLOUD-05](docs/QA-CLOUD-05.md): build hardening, CSP, container
- [QA-CLOUD-06](docs/QA-CLOUD-06-STAGING.md): **current** staging guide
  (Cloud Run, Identity Platform, screenshot bucket, webhooks; no database)
- [RELEASE_PLAN](docs/RELEASE_PLAN.md): the pre-2026-11-11 release plan, kept
  as history
- [Design system](docs/design/QA-DESIGN-SYSTEM.md): the PERPERIS-style shell
