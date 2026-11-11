# PERPERIS QA as a GitHub issues front end

Decided by the owner and the creator on 2026-11-11. This supersedes the staging
and ticket-database plan in `docs/RELEASE_PLAN.md`, which was written for the
old self-hosted ticket design and is kept only as history (see the note at its
top).

## 1. The decision

**GitHub issues in `krummb/kattens-pt` are the single source of truth for
bugs.** PERPERIS QA becomes a front end: its own design and login, but every
ticket is a GitHub issue. The app's own ticket database (`tickets`,
`ticket_events`, `ticket_changes` and the matching endpoints/queries) is
replaced by a GitHub adapter.

QA stays separate from the training app: no access to its database, Google
project, code or secrets. It touches only issues — and, per the screenshot
decision below, nothing else — through a narrowly scoped GitHub App.

Moving QA's own hosting to the creator's accounts was considered and set
aside. Issues live in `kattens-pt`; the QA code stays here, in
`Feniger/PERPERIS_QA`.

## 2. Target architecture

```
   CREATOR / TESTERS
          │  report · triage · verify · reopen
          ▼
   PERPERIS QA (own design + login)
          │        ▲
   writes │        │ reads + webhooks
          ▼        │
   GITHUB ISSUES (krummb/kattens-pt)
       ▲                    ▲
       │ "Fixes #123"       │ "Live in Deploy #24"
       │ merged → closed    │ → deployed label + comment
   PULL REQUESTS        DEPLOY WORKFLOW
```

- **GitHub App "PERPERIS QA"**, registered by the owner under `krummb`,
  installed **only on `kattens-pt`**. Permissions: Issues read/write, Metadata
  read. No Contents, code, workflow, settings or secrets access — see the
  screenshot decision below for why Contents isn't needed. The private key is
  stored as a QA secret (never in Git or chat); the server exchanges it for
  short-lived installation tokens. Actions appear as `perperis-qa[bot]`; the
  acting person is written into the issue/comment text. Webhooks (`issues`,
  `issue_comment`, `pull_request`) keep the QA view current.
- Testers and the creator log in to the QA app itself (Identity Platform
  login, verified email, admin approval, roles Admin/Developer/Tester). **No
  GitHub account is needed** to report or verify a bug.

## 3. Issue conventions

| QA concept | GitHub |
|---|---|
| Ticket | Issue (label `qa`, plus `bug` or `task`) |
| Testing round R05 | Milestone `R05` |
| Severity | Labels `sev:critical` / `sev:high` / `sev:medium` / `sev:low` |
| Area | Labels `area:<name>` (e.g. `area:library`, `area:today`) |
| Status | `status:triaged` / `status:in-progress` / `status:fixed` / `status:deployed` / `status:verified`; a PR closing the issue means fixed (decision 4.4); Verify adds `status:verified`; reopened = open again with a comment |
| Steps, expected/actual, environment, deploy # | Fixed headings in the issue body |
| Reporter / verifier | Line "Reported by `<name>` via PERPERIS QA" / "Verified by `<name>`" |
| Discussion, resolution notes | Issue comments |

Lifecycle: QA "New bug" creates the issue → triage in QA sets labels → a PR
merges referencing it → `deploy-cloud-run` in `kattens-pt` adds
`status:deployed` and comments "Live in Deploy #N" for issues fixed in that
deploy → the creator presses **Verify** in QA (closes the issue) or
**Reopen**.

Created in `kattens-pt` on 2026-11-11 (owner-approved): labels `qa`, `task`
(`bug` already existed), `sev:critical|high|medium|low`,
`status:triaged|in-progress|fixed|deployed|verified`, `area:today|coach|workouts|
library|training-mode|progress|settings|admin|auth`; milestone `R05`.

How a ticket is derived from an issue (milestone 1, `github_issues.py`):
- **Status**: closed with `status:verified` → verified; closed as not planned
  or duplicate → closed; other closed → deployed (with `status:deployed`) or
  fixed; open → reopened if a reopen is newer than its status labels, else the
  most advanced `status:*` label, else reported.
- **Milestone dates**: fixed = earliest of the first `status:fixed` /
  `status:deployed` / `status:verified` label or the first non-"not planned"
  close; deployed = first `status:deployed` (or verified); verified = first
  `status:verified`. Reopening never erases them.
- **Rounds**: found in the issue's `R##` milestone (or "No round"); fixed and
  verified count in the round in progress at that moment, i.e. the newest
  `R##` milestone created before it.
- No severity label shows as "No severity"; nothing is guessed.

Analytics (found/fixed/verified per round, backlog, crossover) are computed
from issue timeline events (created, labeled, closed, reopened): deterministic,
no stored counters, matching the existing dashboard's "no fictional KPI data"
rule.

## 4. Settled decisions (2026-11-11)

1. **Screenshots stay in QA's own private bucket.** The existing
   `attachments.py` (local/GCS backends, two-phase pending/ready, quotas) is
   kept as-is. A QA ticket page links back from the GitHub issue (a URL in the
   issue body), but the images themselves are never committed to `kattens-pt`.
   This is why the GitHub App needs no Contents permission.
2. **QA keeps no storage of its own (lean rollout).** Settled with the owner
   on 2026-11-11 after weighing Identity Platform claims, Neon and Firestore:
   - **Members and roles:** a short list in QA's configuration,
     `PERPERIS_QA_MEMBERS` (`email=Role`, comma-separated, roles Admin /
     Developer / Tester), kept in the secret store, never in Git. Identity
     Platform still verifies the password and the email address; QA then
     looks the verified email up in the list. Anyone not on it is refused.
     Adding a tester is a config change plus a redeploy; the "Manage members"
     screen goes away.
   - **Sessions:** a signed, expiring cookie (HMAC with
     `PERPERIS_QA_SECRET`, about 8 hours). Rotating the secret signs everyone
     out; there is no per-person revocation.
   - **Throttling:** Identity Platform's own sign-in limits, plus a small
     in-memory limit per QA instance on the session exchange.
   - **Security events:** structured application logs (Cloud Logging when
     staged), not a table.
   - **Screenshots:** deferred. When added, they live only in the private
     bucket, keyed by issue number, with no database records.
   - Neon/PostgreSQL, `database.py`, the SQL migrations and the importer are
     removed. Firestore was considered and set aside; it can be added later
     without changing what users see if the team outgrows the list.
3. **Existing ticket data is test/fixture data only** — PERPERIS QA was never
   deployed publicly (staging was never completed), so no real tickets exist
   outside local development. The ticket tables are dropped outright once the
   GitHub adapter lands; no import/migration step is needed.
4. **"Fixes #N" auto-close is kept as normal GitHub behavior.** PRs use the
   standard closing keywords and GitHub closes the issue on merge. PERPERIS QA
   treats an issue that is **closed by a PR but not yet labeled
   `status:deployed`+verified** as still "fixed" in its own view (not
   "resolved/verified") until the creator explicitly presses **Verify** —
   which, for an already-closed issue, just confirms/labels it rather than
   closing it again. This keeps the common-case GitHub workflow (write
   "Fixes #N", merge, done) unchanged for contributors, at the cost of QA
   needing to read "closed, un-deployed, unverified" as its own distinct
   state rather than relying on closed == verified.
5. **"Live in Deploy #N" lives in `kattens-pt`'s `deploy-cloud-run`
   workflow**, as a small added step using the workflow's own token (same
   repo, no extra secret, normal PR review there).

## Configuration (GitHub mode)

Tickets come from GitHub when `PERPERIS_QA_TICKET_SOURCE=github`; the default
(`database`) keeps the old ticket store for local development and tests.

| Variable | Value |
|---|---|
| `PERPERIS_QA_GITHUB_APP_ID` | `5711212` (not secret) |
| `PERPERIS_QA_GITHUB_INSTALLATION_ID` | `180847731` (not secret) |
| `PERPERIS_QA_GITHUB_REPO` | default `krummb/kattens-pt` |
| `PERPERIS_QA_GITHUB_PRIVATE_KEY` or `..._PRIVATE_KEY_FILE` | the App private key (**secret**: Secret Manager in staging; never in Git, logs or chat) |

Reads are cached for 60 seconds (a report clears the cache). The UI shows
issue numbers (`#12`) with a link to GitHub.

**Reporting (milestone 2).** "Report a bug" creates the issue as
`perperis-qa[bot]` with labels `qa`, `bug`, `sev:<severity>` and `area:<name>`
when the area matches an area label, and the chosen round's milestone. The
round must be an **open** `R##` milestone; anything else is refused with the
list of open rounds, and the form defaults to the newest open round. The body
uses a fixed template, structured fields first so free text cannot shadow them:

```
### Environment
<environment or —>

### Deploy
<deploy number or —>

### Category
<category>

### Area            (only when the area is not an area: label)
<area>

### Description
<free text>

---
Reported by <member name> via PERPERIS QA
```

`@` in user text is neutralised so a report cannot ping GitHub users, and the
last "Reported by" line wins, so text above it cannot impersonate a reporter.
In managed sign-in the reporter is the signed-in member; in local mode, the
typed name. Status changes from QA still return 405 until milestone 3, and
screenshots stay off in GitHub mode (see milestone 2 below). Sign-in, roles and members are
unchanged (still database-backed until the identity rework).

## 5. Suggested milestones

Each is its own PR in `Feniger/PERPERIS_QA` unless noted. One milestone per
PR; merging follows the rule in section 6.

0. **Decisions + docs** (this document; also a decision entry in
   `kattens-pt`'s `docs/control/DECISION-LOG.md`). Fix the stale README and
   this release plan's framing. Branch cleanup (merged Cloud-Foundation
   branches) is owner/creator housekeeping, not part of this PR.
1. **GitHub adapter, read-only** (built 2026-11-11; App registered and
   installed, labels and `R05` created): replace ticket reads and analytics with
   issue reads (labels, milestones, timeline). Dashboard and ticket list show
   real `kattens-pt` issues labeled `qa`. Owner registers and installs the
   GitHub App; labels and milestones get created in `kattens-pt`.
2. **Report** (built 2026-11-11): "New bug" creates issues (title, body
   template, labels, milestone, reporter line). **Screenshots moved to the
   no-database rework:** their records live in database rows tied to the old
   ticket table. With decision 4.2 they come back later as bucket-only
   objects keyed by issue number (private bucket, linked from the issue).
2b. **Lean identity, no QA storage** (decision 4.2): email→role list,
   signed session cookie, logs for security events; GitHub becomes the only
   ticket source; the database layer, SQL migrations, importer and member
   screen are removed. This is what makes staging small.
3. **Close the loop**: Triage / Verify / Reopen actions in QA; a separate PR
   in `kattens-pt` adds the `status:deployed` + "Live in Deploy #N" step to
   `deploy-cloud-run`; webhooks for live updates.
4. **Staging**: revise QA-CLOUD-06 for the no-storage design (Cloud Run,
   Identity Platform, secrets: GitHub App key, session secret, member list;
   no database, no bucket), then deploy after owner approval (own Google
   project, budget alert, public vs. locked sign-in decided).
5. **Later**: an "Ask QA" chat/AI feature on the same adapter — needs an AI
   provider key, a separate decision, not started.

Acceptance for each milestone: QA CI green (plus new adapter tests against
recorded/mocked GitHub responses — never against the real repo in CI), no
secrets in Git, statistics derived only from GitHub data, existing
login/roles/security behavior preserved or deliberately and visibly replaced
per decision 4.2 (lean identity).

## 6. Merge and deploy rule (owner decision, 2026-11-11)

- **Claude Code may merge its own pull requests in this repository as soon as
  all CI checks are green**, unless the owner or the creator has objected on
  the PR (a "request changes" review or an objecting comment). It leaves a
  short comment on the PR saying it merged under this rule. The creator can
  review after the merge and ask for a follow-up or a revert.
- Still need the owner's **explicit OK every time**: any deploy (staging or
  production), creating or changing cloud infrastructure, changing the GitHub
  App's permissions or installation, adding or rotating secrets, and anything
  irreversible (deleting data, history or resources).
- Pull requests from anyone else (including the creator's own agents) are
  merged by their author or the owner, not by Claude Code.
- In `krummb/kattens-pt` the existing rules apply (every change through a PR;
  merging to `main` deploys automatically; Claude Code asks before merging
  feature, product or deploy-workflow changes).
