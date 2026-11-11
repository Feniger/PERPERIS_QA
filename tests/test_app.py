"""Local shared-password mode with the in-memory ticket store (no QA storage)."""
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient
import app

ENV = {'PERPERIS_QA_ENV': 'local', 'PERPERIS_QA_AUTH_MODE': 'local_shared', 'PERPERIS_QA_TICKET_SOURCE': 'memory'}
HEADERS = {'X-QA-Request': '1'}


class QATests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, ENV))
        self.enterContext(mock.patch.object(app, 'ADMIN_PASSWORD', 'test-password-long-unique'))
        self.enterContext(mock.patch.object(app, 'SECRET', 'test-secret-0123456789-0123456789-0123456789'))
        self.client = self.enterContext(TestClient(app.APP))

    def login(self):
        self.assertEqual(self.client.post('/api/login', json={'password': 'test-password-long-unique'}).status_code, 200)

    def test_secure_default_and_login(self):
        self.assertEqual(self.client.get('/api/tickets').status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={'password': 'wrong'}).status_code, 401)
        self.login()
        self.assertEqual(self.client.get('/api/me').json(), {'name': 'Admin', 'role': 'Admin'})
        config = self.client.get('/api/auth/config').json()
        self.assertEqual((config['tickets_source'], config['attachments_enabled']), ('memory', False))

    def test_report_becomes_an_issue_and_counts(self):
        self.login()
        invalid = {'title': 'A', 'reporter': 'Product Owner', 'testing_round': 'R01'}
        self.assertEqual(self.client.post('/api/tickets', headers=HEADERS, json=invalid).status_code, 422)
        body = {'title': 'Replace Exercise stuck on phone', 'description': 'Search unusable', 'reporter': 'Product Owner',
                'testing_round': 'R01', 'category': 'mobile', 'severity': 'critical', 'deploy_version': 'Deploy #21',
                'area': 'Library'}
        r = self.client.post('/api/tickets', headers=HEADERS, json=body)
        self.assertEqual(r.status_code, 201, r.text)
        ticket = r.json()['ticket']
        self.assertEqual((ticket['id'], ticket['status'], ticket['severity'], ticket['area']), (1, 'reported', 'critical', 'library'))
        self.assertEqual(ticket['reporter'], 'Product Owner')
        self.assertEqual(ticket['deploy_version'], 'Deploy #21')
        a = self.client.get('/api/analytics').json()
        self.assertEqual((a['total'], a['open'], a['critical'], a['current_round']), (1, 1, 1, 'R01'))
        self.assertEqual(a['rounds'][0], {'round': 'R01', 'found': 1, 'fixed': 0, 'verified': 0})
        self.assertEqual([t['id'] for t in self.client.get('/api/tickets').json()['tickets']], [1])
        self.assertEqual(self.client.get('/api/tickets/1/history').json()['events'][0]['new_status'], 'reported')
        self.assertEqual(self.client.get('/api/tickets/9/history').status_code, 404)

    def test_rounds_mutations_and_status_changes(self):
        self.login()
        body = {'title': 'Valid title', 'reporter': 'Tester', 'testing_round': 'R09', 'description': 'Expected it to work, it did not'}
        wrong = self.client.post('/api/tickets', headers=HEADERS, json=body)
        self.assertEqual(wrong.status_code, 422)
        self.assertIn('R01', wrong.json()['detail'])
        self.assertEqual(self.client.post('/api/tickets', json={**body, 'testing_round': 'R01'}).status_code, 403)
        self.assertEqual(self.client.post('/api/tickets', headers={**HEADERS, 'Origin': 'https://other.invalid'},
                                          json={**body, 'testing_round': 'R01'}).status_code, 403)
        self.assertEqual(self.client.patch('/api/tickets/1', headers=HEADERS, json={'status': 'fixed'}).status_code, 404)

    def test_status_lifecycle_triage_fix_deploy_verify_reopen(self):
        self.login()
        created = self.client.post('/api/tickets', headers=HEADERS, json={
            'title': 'Lifecycle', 'reporter': 'X', 'testing_round': 'R01', 'severity': 'high', 'description': 'Expected it to work, it did not'}).json()['ticket']
        seen = created['updated_at']

        def change(status, **extra):
            nonlocal seen
            r = self.client.patch(f'/api/tickets/{created["id"]}', headers=HEADERS,
                                  json={'status': status, 'expected_updated_at': seen, **extra})
            self.assertEqual(r.status_code, 200, r.text)
            seen = r.json()['ticket']['updated_at']
            return r.json()['ticket']

        for status in ('triaged', 'in_progress', 'fixed', 'deployed', 'verified'):
            self.assertEqual(change(status)['status'], status)
        a = self.client.get('/api/analytics').json()
        self.assertEqual((a['open'], a['fixed'], a['verified'], a['backlog']), (0, 1, 1, 0))
        reopened = change('reopened', comment='Came back on @someone phone')
        self.assertEqual(reopened['status'], 'reopened')
        a = self.client.get('/api/analytics').json()
        # First fix/verification milestones are kept; the issue is open again.
        self.assertEqual((a['open'], a['fixed'], a['verified']), (1, 1, 1))
        store = app.APP.state.tickets
        self.assertEqual(store.comments[created['id']][-1], '**Reopened** by Admin via PERPERIS QA\n\nCame back on @\u200bsomeone phone')
        self.assertEqual([h['new_status'] for h in self.client.get(f'/api/tickets/{created["id"]}/history').json()['events']],
                         ['reported', 'triaged', 'in_progress', 'fixed', 'deployed', 'verified', 'reopened'])
        self.assertEqual(change('closed')['status'], 'closed')

    def test_status_change_conflicts_and_validation(self):
        self.login()
        created = self.client.post('/api/tickets', headers=HEADERS, json={
            'title': 'Conflict', 'reporter': 'X', 'testing_round': 'R01', 'description': 'Expected it to work, it did not'}).json()['ticket']
        path = f'/api/tickets/{created["id"]}'
        self.assertEqual(self.client.patch(path, headers=HEADERS, json={'status': 'triaged', 'expected_updated_at': created['updated_at']}).status_code, 200)
        stale = self.client.patch(path, headers=HEADERS, json={'status': 'fixed', 'expected_updated_at': created['updated_at']})
        self.assertEqual(stale.status_code, 409)
        for bad in ('reported', '', 'done'):
            self.assertEqual(self.client.patch(path, headers=HEADERS, json={'status': bad}).status_code, 422)

    def test_webhook_is_off_without_a_secret_and_verifies_signatures(self):
        import hashlib, hmac
        body = b'{"action":"labeled"}'
        self.assertEqual(self.client.post('/api/github/webhook', content=body).status_code, 404)
        secret = 'synthetic-webhook-secret-123'
        with mock.patch.dict(os.environ, {'PERPERIS_QA_GITHUB_WEBHOOK_SECRET': secret}):
            signature = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            calls = []
            with mock.patch.object(app.APP.state.tickets, 'invalidate', side_effect=lambda: calls.append(1)):
                bad = self.client.post('/api/github/webhook', content=body, headers={'X-Hub-Signature-256': 'sha256=' + '0' * 64, 'X-GitHub-Event': 'issues'})
                self.assertEqual(bad.status_code, 401)
                ok = self.client.post('/api/github/webhook', content=body, headers={'X-Hub-Signature-256': signature, 'X-GitHub-Event': 'issues'})
                self.assertEqual(ok.status_code, 204)
                ignored = self.client.post('/api/github/webhook', content=body, headers={'X-Hub-Signature-256': signature, 'X-GitHub-Event': 'star'})
                self.assertEqual(ignored.status_code, 204)
            self.assertEqual(calls, [1])

    def test_app_icon_and_manifest_are_served_from_a_fixed_list(self):
        page = self.client.get('/').text
        for href in ('/static/icon-180.png', '/static/icon-32.png', '/static/manifest.webmanifest'):
            self.assertIn(href, page)
        for name, kind in (('icon.svg', 'image/svg+xml'), ('icon-16.png', 'image/png'), ('icon-32.png', 'image/png'),
                           ('icon-180.png', 'image/png'), ('icon-192.png', 'image/png'), ('icon-512.png', 'image/png'),
                           ('manifest.webmanifest', 'application/manifest+json')):
            response = self.client.get(f'/static/{name}')
            self.assertEqual(response.status_code, 200, name)
            self.assertTrue(response.headers['content-type'].startswith(kind), name)
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        manifest = self.client.get('/static/manifest.webmanifest').json()
        self.assertEqual((manifest['name'], manifest['short_name']), ('PERPERIS QA', 'QA'))
        self.assertTrue(all(self.client.get(icon['src']).status_code == 200 for icon in manifest['icons']))
        self.assertEqual(self.client.get('/static/index.html').status_code, 404)
        self.assertEqual(self.client.get('/static/..%2Fapp.py').status_code, 404)

    def test_store_starts_empty_on_each_start(self):
        self.login()
        self.client.post('/api/tickets', headers=HEADERS, json={'title': 'Kept only in memory', 'reporter': 'X', 'testing_round': 'R01', 'description': 'Expected it to work, it did not'})
        with TestClient(app.APP) as restarted:
            restarted.post('/api/login', json={'password': 'test-password-long-unique'})
            self.assertEqual(restarted.get('/api/tickets').json()['tickets'], [])


if __name__ == '__main__':
    unittest.main()
