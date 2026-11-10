# QA-CLOUD-02 — versioned persistence and migration rehearsal

Stacked on QA-CLOUD-01; code only, no provisioning, merge or deployment.
The Cloud Run/nonlocal startup block stays in force. PostgreSQL is now an opt-in
local development backend; SQLite remains the default. No operator data was used.

## Persistence decisions

`database.py` owns a small transaction adapter, SQLite foreign keys and write
serialization, and a bounded Psycopg pool (default 5, configurable 1–20 via
`PERPERIS_QA_POOL_SIZE`). SQL parameters stay bound. Only repository-authored SQL
uses this adapter; do not pass user-authored SQL through it. PostgreSQL statements
and lock waits have timeouts. Lifespan closes the pool even when startup fails.
Budget the eventual Cloud Run instance cap against the SQL connection limit.

Migration files are ordered, checksummed, transactional DDL. Applied checksums or
unknown/newer schema versions fail closed. PostgreSQL migrations take an advisory
transaction lock; SQLite uses BEGIN IMMEDIATE. The first migration adopts the
original schema, the second adds a default-1 ticket version and event FK index,
and the third adds an import ledger. SQLite upgrades locally during lifespan;
PostgreSQL requires an explicit migration command and runtime only checks versions.
A separate migration identity is still required for a future deployment.

Ticket IDs are PostgreSQL BIGINT identities. Timestamps remain exact ISO text in
this compatibility phase: converting time formats would break byte-level legacy
parity. A later typed timestamp migration needs explicit data validation. No fake
verified user mapping or actor rewriting is done. The identity Story can add
subject fields while preserving the original actor/reporter/assignee text.

Ticket updates lock the PostgreSQL row before reading old status and writing
history. SQLite writers acquire the database write lock before reading. The
frontend sends `expected_version`; conflicting saves return 409 and the form
stays open with an error. API callers omitting the field retain the old serialized
last-writer behavior for compatibility. Each successful update increments version,
including same-status note/assignment edits; status-only history remains unchanged.

## Commands (local/private development only)

Install `requirements-dev.txt`. Supply PostgreSQL connection settings privately in
`PERPERIS_QA_DATABASE_URL`; never paste credentials into PRs, files or logs. No
command creates a cloud project or database instance. Runtime guard still blocks
Cloud Run even when PostgreSQL is configured.

```sh
python -m database check
python -m database migrate --apply
python -m migrate_sqlite --source /private/operator-approved-backup.sqlite3
# Only after rehearsing and reviewing parity, during a write freeze:
python -m migrate_sqlite --source /private/operator-approved-backup.sqlite3 --apply
```

The importer defaults to dry-run: it opens the explicit source read-only, takes
an in-memory SQLite backup snapshot, checks integrity/FKs/schema/enums, imports into
an empty migrated PostgreSQL target under exclusive table locks, compares every
source field and row, restarts identities above SQLite AUTOINCREMENT high-water
marks, writes an internal import ledger, then **rolls the transaction back**.
It never emits source rows, credential values, fingerprint or paths; only counts,
parity and whether applied. Unknown source columns are rejected, not discarded.
`--apply` commits the same validated operation. Nonempty or previously imported
targets are refused. Identity restart uses transactional ALTER, not nontransactional
setval, so even a rehearsal does not change the destination sequences.

This is intended for the small current application: source rows/snapshot live in
memory and exclusive locks block writes during import. Large datasets require a
streaming design. No operator import was attempted. Import tools need elevated
schema privileges; the eventual runtime identity should not have them.

## Validation

16 tests passed on Python 3.12 with disposable PostgreSQL 16 and synthetic SQLite.
Existing tests still cover the static UI, authentication guard, metrics and first
milestones. New checks cover legacy schema adoption, checksum rejection, idempotent
migrations, source immutability, dry-run data/ledger/sequence rollback, complete
field parity, high-water sequences, repeat/nonempty target refusal, invalid input,
FK transaction rollback, API analytics parity and concurrent stale updates (one
200, one 409, exactly one consistent history event). The PostgreSQL tests use a
random schema and remove only that schema afterward. Without an explicit
`PERPERIS_QA_TEST_POSTGRES_URL`, they are skipped; SQLite tests still run.

```sh
python -m unittest discover -s tests -v
# With a disposable server URL supplied privately:
PERPERIS_QA_TEST_POSTGRES_URL="$qa_disposable_test_url" python -m unittest discover -s tests -v
```

The local server used for validation listens only on loopback and stores synthetic
data in an ephemeral container. No GCP API or live database was used. Dependency
ranges remain unlocked and the TestClient HTTPX deprecation remains a build-story
follow-up. This Story does not remove the public-release gates.

## Migration and rollback

Back up SQLite using its backup API before an approved upgrade. Schema changes are
additive: previous local code ignores the version column and added bookkeeping
tables. Reverting application code can keep those additions; do not edit/delete
applied migration files. PostgreSQL startup refuses newer schema definitions.
Before cloud writes, a future cutover can return to the untouched read-only source.
After cloud writes, rollback must reconcile/export new rows or restore forward;
never switch back silently to an outdated SQLite copy. Full backup/PITR recovery
policy still needs a later infrastructure decision and rehearsal.

Next: verified managed identity, pending membership, Admin/Developer/Tester RBAC,
expiring/revocable sessions, and immutable audit subject attribution. Keep all
infrastructure disabled and the cloud guard intact.
