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
        body = {'title': 'Valid title', 'reporter': 'Tester', 'testing_round': 'R09'}
        wrong = self.client.post('/api/tickets', headers=HEADERS, json=body)
        self.assertEqual(wrong.status_code, 422)
        self.assertIn('R01', wrong.json()['detail'])
        self.assertEqual(self.client.post('/api/tickets', json={**body, 'testing_round': 'R01'}).status_code, 403)
        self.assertEqual(self.client.post('/api/tickets', headers={**HEADERS, 'Origin': 'https://other.invalid'},
                                          json={**body, 'testing_round': 'R01'}).status_code, 403)
        self.assertEqual(self.client.patch('/api/tickets/1', headers=HEADERS, json={'status': 'fixed'}).status_code, 405)

    def test_store_starts_empty_on_each_start(self):
        self.login()
        self.client.post('/api/tickets', headers=HEADERS, json={'title': 'Kept only in memory', 'reporter': 'X', 'testing_round': 'R01'})
        with TestClient(app.APP) as restarted:
            restarted.post('/api/login', json={'password': 'test-password-long-unique'})
            self.assertEqual(restarted.get('/api/tickets').json()['tickets'], [])


if __name__ == '__main__':
    unittest.main()
