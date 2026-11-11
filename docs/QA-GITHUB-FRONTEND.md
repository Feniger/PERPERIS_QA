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
- **Status**: status labels are not removed when work moves on, so an issue
  can carry several. The **most recently applied** `status:*` label counts,
  and only if it was applied after the latest reopen (the current cycle).
  Closed as not planned or duplicate → closed; other closed → verified or
  deployed when that is the newest current-cycle label, otherwise fixed (a
  PR closing the issue means fixed, decision 4.4). Open → the newest
  current-cycle status label; reopened when reopened with no status label
  since; otherwise reported. So an old `status:verified` from an earlier round
  never makes a new fix look verified.
- **Milestone dates**: fixed = earliest of the first `status:fixed` /
  `status:deployed` / `status:verified` label or the first non-"not planned"
  close; deployed = first `status:deployed` (or verified); verified = first
  `status:verified`. Reopening never erases them, so the per-round
  found/fixed/verified figures count each issue's **first** fix and
  verification, while the status column shows where it stands now.
- **Rounds**: found in the issue's `R##` milestone (or "No round"); fixed and
  verified count in the round in progress at that moment, i.e. the newest
  `R##` milestone created before it.
- No severity label shows as "No severity"; nothing is guessed.

Analytics (found/fixed/verified per round, backlog, crossover) are computed
from issue timeline events (created, labeled, closed, reopened): deterministic,
no stored counters, matching the existing dashboard's "no fictional KPI data"
rule.

## 4. Settled decisions (2026-11-11)

1. **Screenshots live in QA's own private bucket, never in `kattens-pt`.**
   That is why the GitHub App needs no Contents permission (its permissions
   stay unchanged). Owner decisions of 2026-11-11, built in four PRs
   (bucket and docs; backend; UI; GitHub comment and Verify/Reopen):
   - **Where:** private bucket `perperis-qa-staging-screenshots` in QA's
     project (europe-north1, uniform access, public access prevention, soft
     delete off). Only `qa-runtime` has object access, scoped to the bucket.
     No database: the bucket *is* the record.
   - **Objects:** `issues/<issue number>/<random id>.<ext>` with custom
     metadata uploader subject (never an email), display name, sha256 and
     upload time. Listing the prefix gives an issue's images; the cap of 10
     per issue is counted from it. Writes use `if_generation_match=0`.
   - **Visibility:** images are shown only inside QA, streamed through QA with
     the session checked on every request (`Cache-Control: private, no-store`,
     `nosniff`). GitHub gets one bot comment per issue, "📎 N screenshots in
     PERPERIS QA: <link>", edited rather than reposted. No public or expiring
     links. Because GitHub readers cannot see the images, a report must have a
     written description (expected vs actual).
   - **Validation:** single-frame PNG/JPEG/WebP, ≤10 MB, ≤8192 px per side,
     decompression-bomb guard, re-encoded to strip metadata, then downscaled
     to at most 2000 px on the long side. Screenshots stay PNG; photos may be
     WebP.
   - **Who:** members upload (in-memory per-user throttle); the uploader or an
     Admin deletes.
   - **Retention:** deleted 30 days after the issue is verified; anything
     older than 12 months is deleted regardless. Verify sets each object's
     `customTime`; a lifecycle rule deletes 30 days after it, and a second
     rule deletes objects older than 365 days.
   - **Reopen (open question for the owner):** Cloud Storage does not allow
     `customTime` to be removed or moved earlier once set; the documented way
     to reset it is to rewrite the object. A rewrite also restarts the
     object's age, so the 365-day rule would count from the rewrite. Proposed:
     Reopen rewrites the issue's objects without `customTime`, and QA itself
     deletes any object whose `uploaded_at` metadata is older than 365 days
     whenever it lists an issue's images, so the 12-month limit still holds.
   - **Guard:** staging refuses to start if the bucket is not private
     (uniform access and public access prevention enforced).
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
     `PERPERIS_QA_SECRET`, 8 hours) that carries the identity but not the
     role. Every request re-checks two things, so access can be ended per
     person: the role is looked up in the member list (removing an email ends
     that person's access on their next request once the updated list is
     live), and Identity Platform is asked whether the account is still
     enabled, verified and not revoked (disabling or revoking it there ends the
     session at once). Rotating the secret signs everyone out.
   - **Throttling:** Identity Platform's own sign-in limits, plus a small
     in-memory limit per QA instance on the session exchange.
   - **Security events:** structured application logs (Cloud Logging when
     staged), not a table.
   - **Screenshots:** only in the private bucket, keyed by issue number,
     with no database records (decision 4.1).
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

Tickets come from GitHub when `PERPERIS_QA_TICKET_SOURCE=github`, which
staging requires. Without it (the default, `memory`), QA uses an in-memory
stand-in for local development and tests (see "Local development" below).
There is no database option any more.

| Variable | Value |
|---|---|
| `PERPERIS_QA_GITHUB_APP_ID` | `5711212` (not secret) |
| `PERPERIS_QA_GITHUB_INSTALLATION_ID` | `180847731` (not secret) |
| `PERPERIS_QA_GITHUB_REPO` | default `krummb/kattens-pt` |
| `PERPERIS_QA_GITHUB_PRIVATE_KEY` or `..._PRIVATE_KEY_FILE` | the App private key (**secret**: Secret Manager in staging; never in Git, logs or chat) |
| `PERPERIS_QA_GITHUB_WEBHOOK_SECRET` | webhook signing secret, 20+ characters (**secret**; same value in the App's webhook settings) |

Reads are cached for 60 seconds (a report triggers a refresh). The UI shows
issue numbers (`#12`) with a link to GitHub. To stay far below the App's
5,000 requests an hour and keep pages fast:
- an issue's event history is fetched again only when its `updated_at`
  changes (labelling, closing, reopening and comments all change it);
- the issue and milestone lists are conditional requests (`If-None-Match`);
  GitHub answers "not modified" without counting it against the limit;
- only one request refreshes at a time and GitHub is never called while
  holding the lock; others keep getting the previous data meanwhile;
- if a refresh fails, the last good data is served **marked stale** (the
  dashboard says "GitHub can't be reached right now. Showing data from
  HH:MM") and GitHub is not asked again for 60 seconds. With no data yet, the
  error is shown, also without retrying on every request.

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
typed name. Screenshots are off (see milestone 2 below).

**Status changes (milestone 3).** In the ticket dialog, a member chooses a
change their role allows (Developer: triaged, in progress, fixed, deployed;
Tester: verified, closed, reopened; Admin: all) plus an optional comment.
QA makes **one** issue update and then posts a comment
"**<change>** by <name> via PERPERIS QA" (with the comment, `@` neutralised),
so GitHub shows who did what even though the App writes it:

| QA change | Issue update |
|---|---|
| Triaged / In progress | label `status:triaged` / `status:in-progress`; reopens a closed issue |
| Fixed / Deployed | label `status:fixed` / `status:deployed`; open/closed state unchanged |
| Verified | label `status:verified`, closed as completed |
| Closed | closed as not planned |
| Reopened | reopened, all `status:*` labels removed |

The new status label replaces any other `status:*` label, so an issue
carries at most one (status is still derived from the newest label, so
changes made directly on GitHub are read correctly too). The dialog sends the
issue's `updated_at` from when it was opened; if the issue changed since, the
save is refused with "someone else changed this ticket" (409). The status menu
starts on "Change status…", so opening and saving a ticket never changes it by
accident.

**Webhooks (milestone 3).** `POST /api/github/webhook` accepts the App's
`issues`, `issue_comment`, `pull_request`, `label` and `milestone` events,
verified with `PERPERIS_QA_GITHUB_WEBHOOK_SECRET` (HMAC-SHA256,
`X-Hub-Signature-256`; the endpoint answers 404 while no secret is set). A
webhook only clears QA's cache; QA then reads GitHub itself, so a webhook can
never inject data. The App's webhook URL and secret are set at staging
(milestone 4), when QA has a public address.

**Local development (milestone 2b).** `PERPERIS_QA_TICKET_SOURCE=memory` (the
default) is an in-memory stand-in for the repository with one open round,
`R01`. It uses the same request builder and issue mapping as GitHub, so the UI
behaves the same; data disappears on restart. Staging refuses it.

**Sign-in (milestone 2b).** `PERPERIS_QA_MEMBERS` is `email=Role` or
`email=Role:Name` entries separated by commas (Admin, Developer, Tester; at
least one Admin), kept in the secret store. The optional name is what QA shows,
for example in "Reported by <name>"; without it QA uses the account's display
name or the part before the @, so email addresses never appear in issues. Identity Platform verifies password and email; an email not on
the list is refused ("not on the QA member list"). The session cookie
(`qa_session`, Secure, HttpOnly, SameSite=Strict, 8 hours) carries the signed
identity but not the role: the role is looked up on every request, so removing
someone from the list ends their access on their next request once the
updated list is live (a config change plus redeploy). Identity Platform is
checked on every request too, so disabling or revoking an account there ends
its session at once. Rotating `PERPERIS_QA_SECRET` signs everyone out. Nothing
about members or sessions is stored by QA.

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
2b. **Lean identity, no QA storage** (built 2026-11-11; decision 4.2): email→role list,
   signed session cookie, logs for security events; GitHub becomes the only
   ticket source; the database layer, SQL migrations, importer and member
   screen are removed. This is what makes staging small.
3. **Close the loop** (QA side built 2026-11-11): Triage / Verify / Reopen
   actions in QA and the webhook endpoint (both described above); a separate
   PR in `kattens-pt` adds the `status:deployed` + "Live in Deploy #N" step to
   `deploy-cloud-run`.
4. **Staging** (setup and deploy workflow rewritten 2026-11-11, see
   [QA-CLOUD-06-STAGING.md](QA-CLOUD-06-STAGING.md)): revise QA-CLOUD-06 for the no-storage design (Cloud Run,
   Identity Platform, secrets: GitHub App key, session secret, member list;
   no database), then deploy after owner approval (own Google
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
