# QA-CLOUD-05 — reproducible build and release hardening

Stacked on QA-CLOUD-04. This is a local build/test Story, not infrastructure or
release approval. No registry push, CI execution, merge, GCP resource, DNS,
service account, identity account, email or deployment was performed.

## Build contract

`requirements.txt` remains the human-maintained dependency ranges;
`requirements.lock` pins the complete Python 3.12 runtime dependency graph with
SHA-256 hashes. The Dockerfile installs binary wheels with `--require-hashes` and
uses a pinned Python 3.12 Debian base digest. Dependency refreshes must regenerate
the lock, audit it, and rerun database, browser and image checks. Runtime lock does
not lock development/browser tools or OS packages; the base digest fixes the OS
snapshot, which still needs periodic security review.

The initial audit found advisories in the selected Pillow 12.3 release. Upgrade
to Pillow 13.0.0, and constrain Pillow Image.open to PNG/JPEG/WebP before plugin
detection. The final runtime lock audit reports **no known vulnerabilities** as of
2026-11-10. This is a dependency database result, not proof of complete security.
No OS image vulnerability scan has been claimed.

`.dockerignore` is a strict source allowlist, including explicit known migration
files. Parent-directory exceptions immediately re-exclude their contents before
allowing individual files; otherwise Docker's negation rules can admit unwanted
files. Synthetic sentinel files in root/static/migrations were excluded from the
built image. Git history, local databases/sidecars, uploads, unknown files,
credentials, tests, private keys and env files never enter the context.

The image sets explicit readable source modes and runs UID/GID 65532. No credentials
are copied or baked into layers. An optional managed-environment CA is mounted only
as a build secret, not persisted. The ASGI entrypoint enforces lifespan, honors
validated PORT, bounds concurrent requests (default four, configurable 1–32),
disables access logs and untrusted proxy-header processing. Proxy/IP behavior must
be revisited deliberately during approved private staging, not by trusting `*`.

The startup guard still rejects Cloud Run's K_SERVICE and all configured nonlocal
modes. There is no production bypass. Containerizing the prototype does not
approve public invocation. Local container examples must publish loopback only.

```sh
# Normal environment:
docker build -t perperis-qa:qa-cloud-05 .
# Managed environment requiring its supplied CA during dependency installation:
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t perperis-qa:qa-cloud-05 .
# Credentials are inherited from a private operator environment, never in argv:
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m -p 127.0.0.1:8787:8080 \
  -e PERPERIS_QA_DB=/tmp/qa.sqlite3 -e PERPERIS_QA_PASSWORD -e PERPERIS_QA_SECRET \
  perperis-qa:qa-cloud-05
```

The shown SQLite path is intentionally disposable **local development only**.
Never use it for persistent tickets or cloud deployment. PostgreSQL remains opt-in
and requires the explicit schema migration. No cloud startup is enabled here.

## Additional application hardening

- Disable public OpenAPI JSON along with docs UI. Validation errors no longer echo
  submitted inputs such as passwords, identity tokens or private ticket bodies.
- Normalize required ticket title/reporter/round before length validation; whitespace
  cannot satisfy required fields. Historical rows are not normalized or rewritten.
- Shared local password/cookie comparison handles Unicode without 500 errors;
  shared mode is still a prototype, blocked from cloud/public release.
- CSP permits same-origin scripts, PNG/blob previews and only the configured
  managed identity API network destination. It denies inline/eval scripts, frames,
  objects and external assets. Inline styles are retained for existing chart/bar
  rendering. Use system fonts instead of third-party font requests.
- Identity mode sends HSTS. Provider requests have a bounded configured HTTP timeout,
  and a cached Admin SDK app must match the configured project. Emulator variables
  cannot silently bypass verified identity or private GCS storage protections.
- Write transactions recheck that the authorizing session is still live in addition
  to current membership/role. Stale screenshot-list responses cannot populate a
  different ticket. Verified reporter UI is read-only and uses the session name.
- `python -m maintenance --apply` removes expired/revoked session records and old
  throttle buckets, never members, tickets or audit history. No scheduler is created.

## Verification

- **56 tests passed**, Python 3.12, including SQLite and disposable PostgreSQL 16.
  PostgreSQL tests use isolated schemas and synthetic rows only. The disposable
  server was removed after validation. Without the explicit test URL, PostgreSQL
  tests skip rather than falling back to any operator database.
- Actual Admin SDK JWT verification tests use generated local RSA keys and mocked
  certificate/account transport: valid signature accepted; forged signature,
  wrong issuer/project, expiry, disabled/revoked identity and emulator bypass denied.
  This supplements, rather than replaces, the earlier mocked service-boundary tests.
- Chromium local smoke: tickets/metrics, paste/drop/picker screenshots, deletion,
  failed-upload retry with no duplicate ticket, and intercepted signup/pending flow.
- Chromium HTTPS smoke with a temporary local certificate and synthetic test-only
  provider fixture: real application Secure/HttpOnly/SameSite cookies, Admin creation,
  Developer fix, Tester verification, Admin member disable and pending denial.
  No real identity, password, certificate private key or screenshot was persisted.
- Built image smoke: non-root UID, no sentinel/private env/DB/build-CA files,
  blocked Cloud Run startup, plus local HTTP/login/ticket/analytics on a read-only,
  capability-dropped/no-new-privileges container. The test publishes loopback only
  and removes its container afterward.
- Runtime dependency audit: no known vulnerabilities. JS syntax, whitespace and
  dependency consistency checks pass. The inherited Starlette/HTTPX TestClient
  deprecation remains a development-tool compatibility follow-up, not a runtime
  failure or ignored security advisory.

Commands for browser/image checks are in `tests/browser_smoke.py`,
`tests/identity_browser_smoke.py` and `tests/container_smoke.py`. Browser tools are
optional via `requirements-browser.txt`. No workflow auto-runs GitHub Actions or
incurs CI/cloud costs; commands are local and explicit.

## Remaining release work and rollback

See [the release plan](RELEASE_PLAN.md). The cloud readiness guard must not be
removed until private staging validates live Identity Platform/ADC/IAM, private
GCS policy and generation operations, Cloud SQL connectivity and restore, trusted
proxy/IP throttling, HTTPS and resource budgets. None of those live checks are
claimed by mocked/local tests.

Build/application hardening changes require no schema migration. Roll back to a
compatible revision/image digest while retaining schema and audit records. Do not
roll back past the Pillow security upgrade for any image processing release. Never
expose a prior shared-password prototype. Keep secrets in private runtime settings;
rotate/revoke if a later operational incident warrants it, not in a PR body.
