"""Small transactional SQLite/PostgreSQL adapter and checksummed schema migrations.

Application SQL is authored by this repository, never accepted from requests.
PostgreSQL uses a bounded pool; schema changes use a separate explicit CLI.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3

MIGRATIONS = Path(__file__).resolve().parent / 'migrations'


class Session:
    def __init__(self, connection, backend):
        self.raw = connection
        self.backend = backend
        self.lock_clause = ' FOR UPDATE' if backend == 'postgres' else ''

    def execute(self, sql, params=()):
        # Only repository-authored SQL uses this adapter's question-mark markers.
        if self.backend == 'postgres':
            sql = sql.replace('?', '%s')
        return self.raw.execute(sql, params)


class Database:
    def __init__(self, url='', *, sqlite_path=None):
        if url and not url.startswith(('postgresql://', 'postgres://')):
            raise ValueError('Only PostgreSQL database URLs are supported')
        self.url = url  # Never include in exceptions, repr, CLI output or logs.
        self.backend = 'postgres' if url else 'sqlite'
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None
        self.pool = None

    def start(self):
        if self.backend == 'postgres' and self.pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            # start() is called during lifespan, never import.
            size = int(os.getenv('PERPERIS_QA_POOL_SIZE', '5'))
            if not 1 <= size <= 20:
                raise ValueError('Pool size must be between 1 and 20')
            self.pool = ConnectionPool(self.url, min_size=0, max_size=size,
                                       timeout=10, open=True,
                                       kwargs={'row_factory': dict_row,
                                               'connect_timeout': 10,
                                               'options': '-c statement_timeout=15000 -c lock_timeout=10000'})

    def close(self):
        if self.pool:
            self.pool.close()
            self.pool = None

    @contextmanager
    def connection(self, *, write=False):
        if self.backend == 'postgres':
            if self.pool is None:
                raise RuntimeError('Database lifespan has not started')
            with self.pool.connection() as con:
                with con.transaction():
                    yield Session(con, self.backend)
        else:
            if self.sqlite_path is None:
                raise RuntimeError('SQLite path is required')
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.sqlite_path, timeout=10)
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            try:
                # Serialize read/modify/write operations before reading old state.
                if write:
                    con.execute('BEGIN IMMEDIATE')
                yield Session(con, self.backend)
                con.commit()
            except BaseException:
                con.rollback()
                raise
            finally:
                con.close()

    def migration_files(self):
        return sorted((MIGRATIONS / self.backend).glob('*.sql'))

    def migrate(self):
        with self.connection(write=True) as db:
            if self.backend == 'postgres':
                db.execute('SELECT pg_advisory_xact_lock(728194001)')
            db.execute('''CREATE TABLE IF NOT EXISTS schema_migrations(
                version TEXT PRIMARY KEY, checksum TEXT NOT NULL)''')
            applied = {r['version']: r['checksum'] for r in db.execute('SELECT * FROM schema_migrations').fetchall()}
            known = {f.name for f in self.migration_files()}
            if applied.keys() - known:
                raise RuntimeError('Database schema is newer than this application')
            for path in self.migration_files():
                content = path.read_text()
                checksum = hashlib.sha256(content.encode()).hexdigest()
                if path.name in applied:
                    if applied[path.name] != checksum:
                        raise RuntimeError('Applied migration checksum mismatch')
                    continue
                # Migration files contain plain DDL, no SQL procedures or strings with semicolons.
                for statement in content.split(';'):
                    if statement.strip():
                        db.execute(statement)
                db.execute('INSERT INTO schema_migrations(version,checksum) VALUES(?,?)', (path.name, checksum))

    def check_schema(self):
        with self.connection() as db:
            applied = {r['version']: r['checksum'] for r in db.execute('SELECT * FROM schema_migrations').fetchall()}
        expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.migration_files()}
        if applied != expected:
            raise RuntimeError('Run the explicit schema migration command before startup')


def from_env():
    return Database(os.getenv('PERPERIS_QA_DATABASE_URL', ''),
                    sqlite_path=os.getenv('PERPERIS_QA_DB', 'qa.sqlite3'))


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Explicit PERPERIS QA schema operations; no cloud provisioning.')
    parser.add_argument('action', choices=('check', 'migrate'))
    parser.add_argument('--apply', action='store_true', help='Required to change schema')
    args = parser.parse_args()
    if args.action == 'migrate' and not args.apply:
        parser.error('migrate requires --apply; use check for read-only validation')
    db = from_env()
    try:
        db.start()
        if args.action == 'migrate':
            db.migrate()
        else:
            db.check_schema()
        print('Schema operation completed.')
    except Exception:
        # Drivers may include connection credentials or data in exception strings.
        parser.exit(1, 'Schema operation failed; verify private connection settings and migration state.\n')
    finally:
        db.close()


if __name__ == '__main__':
    main()
