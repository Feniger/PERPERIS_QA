"""Explicit, private SQLite -> PostgreSQL rehearsal; dry-run is the default.

Run against an empty, explicitly selected QA database after schema migrations.
Never copy private snapshots into the repository or print row/credential data.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from database import Database

TICKET_COLUMNS = ('id', 'title', 'description', 'reporter', 'category', 'severity', 'status',
                  'testing_round', 'area', 'environment', 'deploy_version', 'assigned_to',
                  'issue_url', 'resolution_notes', 'created_at', 'updated_at', 'fixed_at',
                  'deployed_at', 'verified_at', 'closed_at', 'fixed_round', 'verified_round')
EVENT_COLUMNS = ('id', 'ticket_id', 'old_status', 'new_status', 'actor', 'created_at')
STATUSES = {'reported', 'triaged', 'in_progress', 'fixed', 'deployed', 'verified', 'closed', 'reopened'}


class ImportRejected(ValueError):
    pass


class _DryRunRollback(Exception):
    pass


def snapshot(source):
    source = Path(source).resolve(strict=True)
    # mode=ro fails for a missing source and cannot create/write the operator DB.
    with closing(sqlite3.connect(f'file:{quote(str(source))}?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(':memory:')) as snap:
            original.backup(snap)
            snap.row_factory = sqlite3.Row
            if snap.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ImportRejected('Source integrity check failed')
            if snap.execute('PRAGMA foreign_key_check').fetchall():
                raise ImportRejected('Source foreign key check failed')
            result = {}
            if snap.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='qa_members'").fetchone() and snap.execute('SELECT COUNT(*) FROM qa_members').fetchone()[0]:
                raise ImportRejected('Identity-bearing SQLite databases require a separate reviewed migration')
            sequences = dict(snap.execute('SELECT name,seq FROM sqlite_sequence').fetchall())
            for table, required in (('tickets', TICKET_COLUMNS), ('ticket_events', EVENT_COLUMNS)):
                columns = {row['name'] for row in snap.execute(f'PRAGMA table_info({table})')}
                additive = ('version', 'reporter_subject') if table == 'tickets' else ('actor_subject',)
                allowed = set(required) | set(additive)
                if not set(required) <= columns or columns - allowed:
                    raise ImportRejected('Unsupported source columns; review mapping before import')
                rows = [dict(row) for row in snap.execute(f'SELECT * FROM {table} ORDER BY id')]
                high_water = max([0, sequences.get(table, 0)] + [r['id'] for r in rows])
                if high_water >= 9223372036854775807:
                    raise ImportRejected('Source sequence is exhausted')
                result[table] = {'rows': rows, 'columns': tuple(required) + tuple(c for c in additive if c in columns),
                                 'next_id': high_water + 1}
            validate(result)
            return result


def validate(data):
    for table, info in data.items():
        for row in info['rows']:
            if not isinstance(row['id'], int) or row['id'] <= 0:
                raise ImportRejected('Source requires positive integer identifiers')
            if row.get('actor_subject') or row.get('reporter_subject'):
                raise ImportRejected('Verified subjects require an identity-aware migration')
            if table == 'tickets':
                if row['status'] not in STATUSES or row['severity'] not in {'critical', 'high', 'medium', 'low'}:
                    raise ImportRejected('Source enum values require review')
                if row['category'] not in {'ui_ux', 'workout', 'coach_ai', 'data', 'performance', 'mobile', 'auth', 'deployment', 'other'}:
                    raise ImportRejected('Source enum values require review')
                if 'version' in row and (not isinstance(row['version'], int) or row['version'] < 1):
                    raise ImportRejected('Source version requires review')
            elif row['new_status'] not in STATUSES or (row['old_status'] is not None and row['old_status'] not in STATUSES):
                raise ImportRejected('Source event status requires review')
            for column, value in row.items():
                if column not in {'id', 'ticket_id', 'version'} and value is not None and not isinstance(value, str):
                    raise ImportRejected('Source field types require review')


def import_snapshot(source, target, *, apply=False):
    if target.backend != 'postgres':
        raise ImportRejected('Target must be an explicitly selected PostgreSQL QA database')
    data = snapshot(source)
    # Internal fingerprint only; CLI emits counts, never row data or this digest.
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    summary = {'tickets': len(data['tickets']['rows']), 'events': len(data['ticket_events']['rows']),
               'applied': apply, 'parity': True}
    target.check_schema()
    try:
        with target.connection(write=True) as db:
            db.execute('LOCK TABLE tickets, ticket_events, data_imports IN ACCESS EXCLUSIVE MODE')
            if db.execute('SELECT COUNT(*) AS n FROM data_imports').fetchone()['n']:
                raise ImportRejected('Target has an import ledger; repeat import refused')
            if any(db.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'] for table in data):
                raise ImportRejected('Target must be empty; import refused')
            for table, info in data.items():
                columns = info['columns']
                for row in info['rows']:
                    db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                               tuple(row[c] for c in columns))
                imported = [dict(r) for r in db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY id").fetchall()]
                if imported != info['rows']:
                    raise ImportRejected('Full field parity check failed')
                # Transactional identity restart, unlike setval (which survives rollback).
                db.execute(f"ALTER TABLE {table} ALTER COLUMN id RESTART WITH {info['next_id']}")
            db.execute('INSERT INTO data_imports(fingerprint,ticket_count,event_count) VALUES(?,?,?)',
                       (fingerprint, summary['tickets'], summary['events']))
            if not apply:
                raise _DryRunRollback()
    except _DryRunRollback:
        pass
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Explicit private SQLite backup path')
    parser.add_argument('--apply', action='store_true', help='Commit import; default validates then rolls back')
    args = parser.parse_args()
    url = os.getenv('PERPERIS_QA_DATABASE_URL', '')
    if not url:
        parser.error('PERPERIS_QA_DATABASE_URL must explicitly select the target')
    target = Database(url)
    try:
        target.start()
        print(json.dumps(import_snapshot(args.source, target, apply=args.apply)))
    except Exception:
        parser.exit(1, 'Import failed; inspect source integrity, target emptiness and private configuration. No row details are logged.\n')
    finally:
        target.close()


if __name__ == '__main__':
    main()
