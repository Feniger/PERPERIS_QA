"""Cloud safety and persistence contracts, using disposable synthetic data only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FoundationTests(unittest.TestCase):
    def run_isolated(self, code, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'nested' / 'qa.sqlite3'
            env = {k: v for k, v in os.environ.items() if k not in {'K_SERVICE', 'PERPERIS_QA_DATABASE_URL'}}
            env.update(PERPERIS_QA_DB=str(db_path), PERPERIS_QA_ENV='local', PERPERIS_QA_AUTH_MODE='local_shared',
                       PERPERIS_QA_PASSWORD='synthetic-test-password',
                       PERPERIS_QA_SECRET='synthetic-test-secret-' * 3)
            env.update(overrides)
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return db_path.exists()

    def test_import_does_not_create_database(self):
        self.assertFalse(self.run_isolated('import app'))

    def test_cloud_run_cannot_start_even_with_local_mode_and_valid_secrets(self):
        code = '''
from fastapi.testclient import TestClient
from app import APP
try:
    with TestClient(APP):
        raise AssertionError('cloud startup succeeded')
except RuntimeError as exc:
    assert 'cloud startup is blocked' in str(exc)
'''
        for service in ('perperis-qa', ''):
            with self.subTest(service=service):
                self.assertFalse(self.run_isolated(code, K_SERVICE=service))

    def test_nonlocal_and_unknown_modes_cannot_start(self):
        code = '''
from fastapi.testclient import TestClient
from app import APP
try:
    with TestClient(APP):
        raise AssertionError('nonlocal startup succeeded')
except RuntimeError as exc:
    assert 'cloud startup is blocked' in str(exc)
'''
        for mode in ('production', 'staging', 'cloud', 'LOCAL', ''):
            with self.subTest(mode=mode):
                self.assertFalse(self.run_isolated(code, PERPERIS_QA_ENV=mode))

    def test_authenticated_api_guard_without_lifespan(self):
        self.assertFalse(self.run_isolated('''
from fastapi.testclient import TestClient
from app import APP
try:
    TestClient(APP).get('/api/me')
    raise AssertionError('runtime guard bypassed')
except RuntimeError as exc:
    assert 'cloud startup is blocked' in str(exc)
''', K_SERVICE='perperis-qa'))

    def test_restart_preserves_tickets_milestones_metrics_and_audit(self):
        self.assertTrue(self.run_isolated('''
import os
from fastapi.testclient import TestClient
from app import APP, connection
headers = {'X-QA-Request': '1'}
with TestClient(APP) as client:
    assert client.post('/api/login', json={'password': os.environ['PERPERIS_QA_PASSWORD']}).status_code == 200
    result = client.post('/api/tickets', headers=headers, json={
        'title': 'Synthetic audit regression', 'reporter': 'Legacy reporter',
        'testing_round': 'R01', 'severity': 'high', 'category': 'auth'})
    assert result.status_code == 201, result.text
    tid = result.json()['ticket']['id']
    def update(status, round='R02'):
        response = client.patch(f'/api/tickets/{tid}', headers=headers, json={
            'status': status, 'assigned_to': 'Legacy assignee',
            'resolution_notes': 'Synthetic notes', 'resolution_round': round})
        assert response.status_code == 200, response.text
        return response.json()['ticket']
    closed = update('closed')
    reopened = update('reopened', 'R03')
    unchanged = update('reopened', 'R04')
    for key in ('fixed_at', 'deployed_at', 'verified_at', 'closed_at', 'fixed_round', 'verified_round'):
        assert closed[key] is not None
        assert closed[key] == reopened[key] == unchanged[key]
    assert closed['fixed_round'] == closed['verified_round'] == 'R02'
    for path in ('/api/tickets', '/api/analytics'):
        assert client.get(path, headers={'Cookie': ''}).status_code == 401
    assert client.patch(f'/api/tickets/{tid}', json={'status': 'fixed'}).status_code == 403
    assert client.patch(f'/api/tickets/{tid}', headers={**headers, 'Origin': 'https://other.invalid'}, json={'status': 'fixed'}).status_code == 403
    with connection() as db:
        events = [dict(row) for row in db.execute('SELECT * FROM ticket_events ORDER BY id')]
    assert [e['new_status'] for e in events] == ['reported', 'closed', 'reopened']
    assert [e['old_status'] for e in events] == [None, 'reported', 'closed']
    assert all(e['actor'] == 'Admin' and e['ticket_id'] == tid for e in events)
    before = client.get('/api/analytics').json()
    assert (before['total'], before['open'], before['fixed'], before['verified'], before['backlog']) == (1, 1, 1, 1, 0)
    assert before['rounds'] == [
        {'round': 'R01', 'found': 1, 'fixed': 0, 'verified': 0},
        {'round': 'R02', 'found': 0, 'fixed': 1, 'verified': 1}]
# Re-entering lifespan must initialize idempotently without replacing history.
with TestClient(APP) as restarted:
    assert restarted.post('/api/login', json={'password': os.environ['PERPERIS_QA_PASSWORD']}).status_code == 200
    assert restarted.get('/api/tickets').json()['tickets'] == [unchanged]
    assert restarted.get('/api/analytics').json() == before
    with connection() as db:
        assert [dict(row) for row in db.execute('SELECT * FROM ticket_events ORDER BY id')] == events
# Existing transaction contract must roll back ticket and event together.
try:
    with connection() as db:
        db.execute("UPDATE tickets SET title='Should roll back' WHERE id=?", (tid,))
        db.execute("INSERT INTO ticket_events(ticket_id,new_status,actor,created_at) VALUES(999999,'fixed','Admin','synthetic')")
except Exception:
    pass
else:
    raise AssertionError('foreign key violation did not fail')
with connection() as db:
    assert db.execute('SELECT title FROM tickets WHERE id=?', (tid,)).fetchone()[0] == unchanged['title']
    assert [dict(row) for row in db.execute('SELECT * FROM ticket_events ORDER BY id')] == events
'''))
