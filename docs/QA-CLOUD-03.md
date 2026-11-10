# QA-CLOUD-03 — verified identity and roles

Stacked on QA-CLOUD-02. No identity project, email configuration, cloud service,
DNS or paid resource is created. The Cloud Run/nonlocal startup block remains.
The existing shared-password mode is still local-only. Identity integration is
code-complete for offline tests, not a live-provider deployment validation.

## Runtime contract

Default `PERPERIS_QA_AUTH_MODE=local_shared` keeps the prototype workflow.
`identity` disables `/api/login` and requires a dedicated
`PERPERIS_QA_IDENTITY_PROJECT`, exact canonical HTTPS `PERPERIS_QA_ORIGIN`
(no trailing slash/path), and private 32+ character `PERPERIS_QA_SECRET`
for HMAC rate-limit buckets. Unknown modes or incomplete settings fail startup.
Google Admin SDK uses Application Default Credentials, never a checked-in key.
The proposed Cloud Run identity will need only the necessary identity read
permissions; no permission binding is created here.

`PERPERIS_QA_IDENTITY_WEB_API_KEY` is optional public browser configuration for
Identity Platform's REST API; it is not an Admin credential. With no web key,
managed browser sign-in is visibly unavailable and cannot fall back to shared
password. Restrict the eventual web key to the intended API/domain. No actual
key or credential value has been populated in this work.

Browser registration/sign-in/reset sends passwords directly to Identity Platform,
never through the QA server. Signup sends a verification email only when the user
clicks the registration control in a configured application. No email was sent
in this development task. Provider tokens stay in memory for exchange, never
localStorage. Production password policy, account enumeration protections, authorized
domains and email templates still require provider configuration and validation.

## Authorization and session behavior

The Admin SDK verifies ID-token signature, issuer, audience, expiry and revocation.
Exchange requires verified email and a sign-in within five minutes. Token/client
roles are ignored. First verified sign-in creates only a pending Tester request;
it grants no ticket access. An Admin must activate membership. There is no
first-registrant Admin or client role selection.

First Admin bootstrap is an explicit private operator action against an already
verified pending member, with `python -m identity --subject <provider-subject>
--apply`. It refuses when an active Admin exists. Do not copy real subjects into
PRs/logs. Role/state changes require an active verified Admin and are audited.
The final active Admin cannot be demoted or disabled; membership writes are
serialized with the same advisory lock (or SQLite write transaction).

| Action | Admin | Developer | Tester |
| --- | --- | --- | --- |
| Read tickets, analytics and ticket history | Yes | Yes | Yes |
| Create tickets (verified reporter attribution) | Yes | Yes | Yes |
| Assign; mark triaged/in progress/fixed/deployed | Yes | Yes | No |
| Verify/close/reopen | Yes | No | Yes, without assignment changes |
| Approve/disable members and change roles | Yes | No | No |

Successful exchange creates a random 256-bit opaque cookie and stores only its
SHA-256 hash. Cookie is Secure, HttpOnly, SameSite Strict; server expires it in one
hour. Fresh sign-in rotates prior sessions. Logout and membership changes revoke
sessions. Every authenticated identity request checks active DB membership and
provider disable/email-verification/revocation state; provider/network errors deny
access. This adds provider latency intentionally until a reviewed caching/revocation
design is justified. Mutation transactions recheck membership under the member
lock to prevent an already-authorized stale role from writing after a role change.

Exchange and mutations require the custom request header and exact configured
Origin; no client Host/forwarded header determines the trusted origin. A DB-backed
fixed-window throttle allows 20 exchange attempts per client IP per five minutes,
shared across instances. IP buckets are HMACs, not raw addresses. Before release,
validate trusted proxy handling so all clients do not share a bucket or spoof
addresses. This is additional to provider quotas/password protections.

New tickets record verified reporter subject and use verified display name rather
than a client-supplied reporter label. New status events record actor subject;
legacy text/history remains untouched and subjects nullable. `ticket_changes`
additionally records changed status, assignment and notes in the same transaction.
`GET /api/tickets/{id}/history` exposes authenticated status and change history.
`qa_security_events` records membership/session actions. No audit modification API
exists; database runtime permissions must restrict direct audit edits later.
API responses use no-store and pages get nosniff/referrer/frame protections.

The frontend provides email signup/verification instructions, managed login/reset,
role-filtered status options and an Admin membership panel. Server authorization
is authoritative. The public API config endpoint exposes only mode and public
browser key, never provider credentials or server secrets.

## Migration and rollback

Migration 004 adds membership, session, throttle and security/change audit tables
and nullable ticket/event subject fields. It does not rewrite historic actors or
infer verified identities. Apply the explicit PostgreSQL migration before runtime.
The legacy importer accepts the new nullable columns but rejects any populated
verified subjects or identity-bearing SQLite source. Such data requires a separate
reviewed identity-aware migration rather than silently dropping memberships.

Never roll an identity deployment back into shared-password public access. Keep
the cloud guard. A code rollback leaves additive fields/tables intact; previous
PostgreSQL revisions reject newer schemas as designed, so use a compatible forward
fix or privately restore a matching backup. Do not delete audit records to roll back.

## Validation and remaining release gates

30 tests passed with SQLite and disposable PostgreSQL 16. Identity tests use a
mocked provider boundary and synthetic verified/unverified claims; no real identity
or email service was contacted. Both database backends exercise pending/role
spoofing denial, explicit bootstrap, expiry/rotation/logout replay, revoked provider,
CSRF, role permissions, verified actor attribution, note-change history, member
revocation, final Admin protection and persistent throttling. JavaScript syntax
checks passed. The existing HTTPX TestClient deprecation remains.

Live project issuer/audience rejection, ADC/IAM, mail delivery, password reset and
revocation, trusted proxy behavior, browser HTTPS cookies and ingress must be
validated in approved private staging before removing the startup block. This PR
is not approval to configure or expose that environment. Next: private screenshot
storage and one paste/drop/file upload flow, with bounded image validation and
object/metadata cleanup tests.

Browser smoke also passed in Chromium: synthetic local login, ticket creation,
status update and metrics; intercepted provider signup/verification instructions
and pending-membership denial. No real signup or mail request occurred. The test
found and fixed hidden required create fields blocking edit-form submission:
hidden form sections are now disabled. Run `tests/browser_smoke.py` after installing
`requirements-browser.txt` and Chromium (or set `PERPERIS_QA_BROWSER_EXECUTABLE`).
