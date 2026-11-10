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

The URL `qa.perperis.app` is proposed, **not configured**. No repository has been created automatically.

Testing-round labels should follow `R01`, `R02`, etc. A report counts as **found** in its discovery round; the first fixed/verified transition counts in its specified resolution round (if blank, the discovery round). Later reopens do not erase the original first-fix milestone, and this prototype does not yet support multiple fix cycles per issue. Hence the trend plots **first fixes/verification milestones**, not all transitions. The chart marks the first round containing at least one discovered bug where fixes >= discoveries; its marker is descriptive rather than predictive.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Next milestones

1. Proper multi-user auth, roles, ticket comments and attachment storage; migrate session and audit to verified identities.
2. Real deployment pipeline to a *new* GitHub repository and a private hosting environment; only then configure DNS for `qa.perperis.app`.
3. Import PERPERIS feedback rounds with duplicate review and preserve original GitHub issue/PR references; no invented statuses.
4. GitHub synchronization as a controlled integration, with ownership rules for when status is driven by code vs human verification.
