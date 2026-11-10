import concurrent.futures
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid

from database import Database
from migrate_sqlite import ImportRejected, import_snapshot, snapshot


class SQLiteMigrationTests(unittest.TestCase):
    def test_upgrade_existing_legacy_database_and_checksum_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.sqlite3'
            with sqlite3.connect(path) as con:
                con.executescript(Path('migrations/sqlite/001_tickets.sql').read_text())
                con.execute("INSERT INTO tickets(title,reporter,category,severity,testing_round,created_at,updated_at) VALUES('Original','Legacy','auth','high','R01','time','time')")
            db = Database(sqlite_path=path)
            db.migrate()
            db.migrate()
            db.check_schema()
            with db.connection(write=True) as con:
                row = con.execute('SELECT * FROM tickets').fetchone()
                self.assertEqual((row['title'], row['version']), ('Original', 1))
                con.execute("UPDATE schema_migrations SET checksum='invalid' WHERE version='001_tickets.sql'")
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                db.migrate()

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing.sqlite3'
            with self.assertRaises(FileNotFoundError):
                snapshot(path)
            self.assertFalse(path.exists())


@unittest.skipUnless(os.getenv('PERPERIS_QA_TEST_POSTGRES_URL'), 'Set explicit disposable PostgreSQL test URL')
class PostgreSQLTests(unittest.TestCase):
    """Creates an isolated schema, never drops/truncates existing user tables."""
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.psycopg = psycopg
        self.sql = sql
        self.url = os.environ['PERPERIS_QA_TEST_POSTGRES_URL']
        self.schema = 'qa_test_' + uuid.uuid4().hex
        with psycopg.connect(self.url) as con:
            con.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.db = Database(self.url)
        # Isolate every test in a random schema on the explicit disposable server.
        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row
        self.db.pool = ConnectionPool(self.url, min_size=0, max_size=5, open=True, kwargs={
            'row_factory': dict_row,
            'options': f'-c search_path={self.schema} -c statement_timeout=15000 -c lock_timeout=10000'})
        self.db.migrate()
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / 'source.sqlite3'
        source_db = Database(sqlite_path=self.source)
        source_db.migrate()
        with source_db.connection(write=True) as con:
            con.execute("INSERT INTO tickets(id,title,reporter,category,severity,status,testing_round,created_at,updated_at,fixed_at,fixed_round) VALUES(7,'Synthetic ünicode','Legacy Reporter','auth','critical','reopened','R01','2026-01-01T00:00:00+00:00','2026-01-03T00:00:00+00:00','2026-01-02T00:00:00+00:00','R02')")
            for eid, old, new in [(11, None, 'reported'), (12, 'reported', 'fixed'), (13, 'fixed', 'reopened')]:
                con.execute('INSERT INTO ticket_events(id,ticket_id,old_status,new_status,actor,created_at) VALUES(?,7,?,?,?,?)', (eid, old, new, 'Admin', '2026-01-01T00:00:00+00:00'))
            con.execute("UPDATE sqlite_sequence SET seq=90 WHERE name='tickets'")
            con.execute("UPDATE sqlite_sequence SET seq=120 WHERE name='ticket_events'")

    def tearDown(self):
        self.db.close()
        with self.psycopg.connect(self.url) as con:
            con.execute(self.sql.SQL('DROP SCHEMA {} CASCADE').format(self.sql.Identifier(self.schema)))
        self.tmp.cleanup()

    def test_dry_run_rolls_back_data_ledger_and_sequences_then_apply_preserves_all_fields(self):
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        summary = import_snapshot(self.source, self.db)
        self.assertEqual(summary, {'tickets': 1, 'events': 3, 'applied': False, 'parity': True})
        with self.db.connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM tickets').fetchone()['n'], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM data_imports').fetchone()['n'], 0)
            self.assertEqual(con.execute('SELECT last_value,is_called FROM tickets_id_seq').fetchone(), {'last_value': 1, 'is_called': False})
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        self.assertTrue(import_snapshot(self.source, self.db, apply=True)['applied'])
        with self.db.connection(write=True) as con:
            self.assertEqual(con.execute('SELECT * FROM tickets').fetchone(), snapshot(self.source)['tickets']['rows'][0])
            row = con.execute("INSERT INTO tickets(title,reporter,category,severity,testing_round,created_at,updated_at) VALUES('New','Tester','auth','high','R03','time','time') RETURNING id").fetchone()
            self.assertEqual(row['id'], 91)
            self.assertEqual(con.execute("INSERT INTO ticket_events(ticket_id,new_status,actor,created_at) VALUES(91,'reported','Admin','time') RETURNING id").fetchone()['id'], 121)
        with self.assertRaises(ImportRejected):
            import_snapshot(self.source, self.db, apply=True)

    def test_nonempty_target_refused_without_changes(self):
        with self.db.connection(write=True) as con:
            con.execute("INSERT INTO tickets(title,reporter,category,severity,testing_round,created_at,updated_at) VALUES('Already here','Tester','auth','high','R03','time','time')")
        with self.assertRaisesRegex(ImportRejected, 'empty'):
            import_snapshot(self.source, self.db)
        with self.db.connection() as con:
            self.assertEqual(con.execute('SELECT title FROM tickets').fetchone()['title'], 'Already here')

    def test_invalid_source_never_leaves_partial_rows(self):
        with sqlite3.connect(self.source) as con:
            con.execute("UPDATE ticket_events SET new_status='invented'")
        with self.assertRaises(ImportRejected):
            import_snapshot(self.source, self.db, apply=True)
        with self.db.connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM tickets').fetchone()['n'], 0)

    def test_migrations_are_idempotent_and_transactions_rollback(self):
        self.db.migrate()
        self.db.check_schema()
        with self.assertRaises(self.psycopg.errors.ForeignKeyViolation):
            with self.db.connection(write=True) as con:
                con.execute("INSERT INTO tickets(title,reporter,category,severity,testing_round,created_at,updated_at) VALUES('Roll back','Tester','auth','high','R01','time','time')")
                con.execute("INSERT INTO ticket_events(ticket_id,new_status,actor,created_at) VALUES(9999,'reported','Admin','time')")
        with self.db.connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM tickets').fetchone()['n'], 0)

    def test_api_parity_stale_writes_and_concurrent_history_chain(self):
        import app
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from database import Database as DB
        sqlite_db = DB(sqlite_path=self.source)
        password = 'synthetic-api-parity-password'
        self.enterContext(patch.object(app, 'ADMIN_PASSWORD', password))
        self.enterContext(patch.object(app, 'SECRET', 'synthetic-secret-' * 4))
        with patch.object(app, 'DATABASE', sqlite_db), TestClient(app.APP) as client:
            client.post('/api/login', json={'password': password})
            before = client.get('/api/analytics').json()
        import_snapshot(self.source, self.db, apply=True)
        # Client lifespan closes the DB pool; it can be recreated after the test.
        with patch.object(app, 'DATABASE', self.db), TestClient(app.APP) as client:
            client.post('/api/login', json={'password': password})
            self.assertEqual(client.get('/api/analytics').json(), before)
            barrier = threading.Barrier(2)
            def save(status):
                barrier.wait(timeout=5)
                return client.patch('/api/tickets/7', headers={'X-QA-Request': '1'}, json={'status': status, 'expected_version': 1}).status_code
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(save, ['triaged', 'in_progress']))
            self.assertEqual(sorted(results), [200, 409])
            with self.db.connection() as con:
                events = con.execute('SELECT * FROM ticket_events WHERE ticket_id=7 ORDER BY id').fetchall()
                self.assertEqual(len(events), 4)
                self.assertEqual(events[-1]['old_status'], 'reopened')
                self.assertEqual(con.execute('SELECT version FROM tickets WHERE id=7').fetchone()['version'], 2)
