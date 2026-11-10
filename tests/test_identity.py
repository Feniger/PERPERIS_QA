import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
from database import Database
import identity


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = self.make_database()
        self.enterContext(patch.object(app, 'DATABASE', self.db))
        self.enterContext(patch.dict(os.environ, {
            'PERPERIS_QA_AUTH_MODE': 'identity', 'PERPERIS_QA_ENV': 'local',
            'PERPERIS_QA_ORIGIN': 'https://qa.example.test',
            'PERPERIS_QA_IDENTITY_PROJECT': 'synthetic-qa-project',
            'PERPERIS_QA_SECRET': 'synthetic-throttle-secret-' * 3}))
        self.verifier = self.enterContext(patch.object(identity, 'verify_provider_token', side_effect=self.claims))
        self.provider_session = self.enterContext(patch.object(identity, 'verify_provider_session'))
        self.client = self.enterContext(TestClient(app.APP, base_url='https://qa.example.test'))
        self.headers = {'X-QA-Request': '1', 'Origin': 'https://qa.example.test'}

    def make_database(self):
        return Database(sqlite_path=Path(self.tmp.name)/'identity.sqlite3')

    @staticmethod
    def claims(token):
        if token == 'invalid':
            raise ValueError('private raw token must not be reflected')
        return {'uid': token, 'email': token+'@example.test', 'name': token,
                'email_verified': True, 'auth_time': time.time(), 'role': 'Admin'}

    def exchange(self, name, headers=None):
        return self.client.post('/api/session', headers=self.headers if headers is None else headers,
                                json={'id_token': name})

    def admin(self):
        self.assertEqual(self.exchange('admin').status_code, 403)
        identity.bootstrap(self.db, 'admin')
        self.assertEqual(self.exchange('admin').status_code, 200)

    def add_member(self, name, role):
        self.assertEqual(self.exchange(name).status_code, 403)
        self.exchange('admin')
        response = self.client.patch('/api/members/'+name, headers=self.headers,
                                     json={'role': role, 'state': 'active'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.exchange(name).status_code, 200)

    def create(self, **extra):
        return self.client.post('/api/tickets', headers=self.headers, json={
            'title': 'Synthetic identity ticket', 'reporter': 'Spoofed Reporter', 'testing_round': 'R01', **extra})

    def test_unverified_invalid_stale_tokens_and_csrf_fail_closed(self):
        for headers in ({}, {'X-QA-Request':'1'}, {**self.headers, 'Origin':'https://other.example.test'}):
            self.assertEqual(self.exchange('admin', headers).status_code, 403)
        self.assertFalse(self.verifier.called)
        self.assertEqual(self.exchange('invalid').status_code, 401)
        with patch.object(identity, 'verify_provider_token', return_value={**self.claims('new'), 'email_verified': False}):
            self.assertEqual(self.exchange('new').status_code, 403)
        with patch.object(identity, 'verify_provider_token', return_value={**self.claims('new'), 'auth_time': time.time()-301}):
            self.assertEqual(self.exchange('new').status_code, 401)
        with self.db.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM qa_members').fetchone()['n'], 0)
        self.assertEqual(self.client.post('/api/login', json={'password':'anything'}).status_code, 404)

    def test_verified_registration_stays_pending_ignores_claimed_role_and_bootstrap_is_explicit(self):
        self.assertEqual(self.exchange('first').status_code, 403)
        self.assertEqual(self.client.get('/api/tickets').status_code, 401)
        with self.db.connection() as db:
            member = db.execute('SELECT * FROM qa_members').fetchone()
            self.assertEqual((member['role'], member['state']), ('Tester', 'pending'))
        identity.bootstrap(self.db, 'first')
        self.assertEqual(self.exchange('first').status_code, 200)
        with self.assertRaises(ValueError):
            identity.bootstrap(self.db, 'first')
        self.assertEqual(self.client.get('/api/me').json(), {'name':'first','role':'Admin'})

    def test_cookie_expiry_rotation_logout_and_provider_revocation(self):
        self.admin()
        first = self.client.cookies.get('qa_session')
        response = self.exchange('admin')
        cookie = response.headers['set-cookie']
        self.assertIn('Secure', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=strict', cookie)
        self.assertNotEqual(first, self.client.cookies.get('qa_session'))
        self.assertEqual(self.client.get('/api/me', headers={'Cookie':'qa_session='+first}).status_code, 401)
        with patch.object(identity, 'verify_provider_session', side_effect=__import__('fastapi').HTTPException(401, 'Sign in again')):
            self.assertEqual(self.client.get('/api/tickets').status_code, 401)
        raw = self.client.cookies.get('qa_session')
        with self.db.connection(write=True) as db:
            stored = db.execute('SELECT * FROM qa_sessions WHERE token_hash=?', (hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
            self.assertNotIn(raw, str(dict(stored)))
            db.execute('UPDATE qa_sessions SET expires_at=0')
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.exchange('admin')
        raw = self.client.cookies.get('qa_session')
        self.assertEqual(self.client.post('/api/logout', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/me', headers={'Cookie':'qa_session='+raw}).status_code, 401)

    def test_role_matrix_subject_attribution_and_complete_change_history(self):
        self.admin()
        self.add_member('tester', 'Tester')
        self.assertEqual(self.create(assigned_to='Someone').status_code, 403)
        ticket = self.create().json()['ticket']
        self.assertEqual((ticket['reporter'],ticket['reporter_subject']), ('tester','tester'))
        tid = ticket['id']
        self.assertEqual(self.client.patch(f'/api/tickets/{tid}', headers=self.headers, json={'status':'fixed'}).status_code,403)
        self.assertEqual(self.client.get('/api/members').status_code,403)
        self.add_member('developer','Developer')
        self.assertEqual(self.client.patch(f'/api/tickets/{tid}', headers=self.headers, json={'status':'verified'}).status_code,403)
        result=self.client.patch(f'/api/tickets/{tid}',headers=self.headers,json={'status':'fixed','assigned_to':'developer','resolution_notes':'Fix A','resolution_round':'R02'})
        self.assertEqual(result.status_code,200,result.text)
        result=self.client.patch(f'/api/tickets/{tid}',headers=self.headers,json={'status':'fixed','assigned_to':'developer','resolution_notes':'Fix B'})
        self.assertEqual(result.status_code,200,result.text)
        self.exchange('tester')
        result=self.client.patch(f'/api/tickets/{tid}',headers=self.headers,json={'status':'verified','assigned_to':'developer','resolution_notes':'Verified','resolution_round':'R03'})
        self.assertEqual(result.status_code,200,result.text)
        history=self.client.get(f'/api/tickets/{tid}/history').json()
        self.assertEqual([e['actor_subject'] for e in history['events']],['tester','developer','tester'])
        self.assertEqual(len(history['changes']),3)
        self.assertEqual(history['events'][1]['new_status'],'fixed')
        self.assertEqual(self.client.get('/api/tickets/999/history').status_code,404)

    def test_member_change_revokes_sessions_and_final_admin_guard(self):
        self.admin()
        self.assertEqual(self.client.patch('/api/members/admin',headers=self.headers,json={'role':'Tester','state':'active'}).status_code,409)
        self.add_member('tester','Tester')
        raw=self.client.cookies.get('qa_session')
        self.exchange('admin')
        self.assertEqual(self.client.patch('/api/members/tester',headers=self.headers,json={'role':'Tester','state':'disabled'}).status_code,200)
        self.assertEqual(self.client.get('/api/me',headers={'Cookie':'qa_session='+raw}).status_code,401)
        self.assertEqual(self.exchange('tester').status_code,403)
        with self.db.connection() as db:
            actions=[r['action'] for r in db.execute('SELECT * FROM qa_security_events ORDER BY id').fetchall()]
        self.assertIn('membership:Tester:disabled',actions)

    def test_distributed_signin_throttle_and_safe_error_headers(self):
        for _ in range(20):
            self.assertEqual(self.exchange('invalid').status_code,401)
        response=self.exchange('invalid')
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['Retry-After'],'300')
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertNotIn('private raw token', response.text)
        with self.db.connection() as db:
            row=db.execute('SELECT * FROM qa_auth_throttle').fetchone()
            self.assertEqual(row['attempts'],21)
            self.assertNotIn('testclient',row['bucket'])

    def test_identity_configuration_and_production_guard_remain_fail_closed(self):
        for origin in ('http://qa.example.test','https://qa.example.test/','https://user:pass@qa.example.test',''):
            with patch.dict(os.environ, {'PERPERIS_QA_ORIGIN': origin}):
                with self.assertRaises(RuntimeError):
                    identity.check_configuration()
        with patch.dict(os.environ, {'PERPERIS_QA_AUTH_MODE':'typo'}):
            with self.assertRaises(RuntimeError):
                identity.check_configuration()
        with patch.dict(os.environ, {'K_SERVICE':'qa'}):
            with self.assertRaises(RuntimeError):
                app.check_runtime()


@unittest.skipUnless(os.getenv('PERPERIS_QA_TEST_POSTGRES_URL'), 'Set explicit disposable PostgreSQL test URL')
class PostgreSQLIdentityTests(IdentityTests):
    def make_database(self):
        import uuid
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        url = os.environ['PERPERIS_QA_TEST_POSTGRES_URL']
        schema = 'qa_identity_test_' + uuid.uuid4().hex
        with psycopg.connect(url) as con:
            con.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        db = Database(url)
        db.pool = ConnectionPool(url, min_size=0, max_size=5, open=True,
            kwargs={'row_factory': dict_row, 'options': f'-c search_path={schema}'})
        def cleanup():
            db.close()
            with psycopg.connect(url) as con:
                con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        self.addCleanup(cleanup)
        db.migrate()
        return db
