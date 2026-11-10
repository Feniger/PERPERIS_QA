import os
import tempfile
import unittest
from pathlib import Path

# Isolate test DB/config before import, never touch operator data.
TMP = tempfile.TemporaryDirectory()
os.environ['PERPERIS_QA_DB'] = str(Path(TMP.name)/'test.sqlite3')
os.environ['PERPERIS_QA_PASSWORD'] = 'test-password-long-unique'
os.environ['PERPERIS_QA_SECRET'] = 'test-secret-0123456789-0123456789-0123456789'
os.environ['PERPERIS_QA_ENV'] = 'local'
os.environ['PERPERIS_QA_AUTH_MODE'] = 'local_shared'
os.environ['PERPERIS_QA_ATTACHMENT_BACKEND'] = 'disabled'
os.environ.pop('PERPERIS_QA_DATABASE_URL', None)

from fastapi.testclient import TestClient
from app import APP

class QATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(APP)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_01_secure_default_and_login(self):
        self.assertEqual(self.client.get('/api/tickets').status_code,401)
        self.assertEqual(self.client.post('/api/login',json={'password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/api/login',json={'password':os.environ['PERPERIS_QA_PASSWORD']}).status_code,200)
        self.assertEqual(self.client.get('/api/me').json()['name'],'Admin')

    def test_02_validated_ticket_and_analytics(self):
        headers={'X-QA-Request':'1'}
        invalid={'title':'A','reporter':'Product Owner','testing_round':'R04'}
        self.assertEqual(self.client.post('/api/tickets',headers=headers,json=invalid).status_code,422)
        body={'title':'Replace Exercise stuck on phone','description':'Search unusable','reporter':'Product Owner','testing_round':'R04','category':'mobile','severity':'critical','deploy_version':'Deploy #21'}
        r=self.client.post('/api/tickets',headers=headers,json=body)
        self.assertEqual(r.status_code,201,r.text)
        self.ticket_id=r.json()['ticket']['id']
        self.assertEqual(self.client.get('/api/analytics').json()['total'],1)
        self.assertEqual(self.client.get('/api/analytics').json()['open'],1)
        self.assertEqual(self.client.get('/api/analytics').json()['rounds'][0]['found'],1)
        self.assertEqual(self.client.post('/api/tickets',json=body).status_code,403)

    def test_03_status_milestones_and_reopen(self):
        headers={'X-QA-Request':'1'}
        tid=self.client.get('/api/tickets').json()['tickets'][0]['id']
        base={'assigned_to':'Engineering','resolution_notes':'Replaced narrow picker','resolution_round':'R05'}
        for status in ['triaged','in_progress','fixed','deployed','verified','closed']:
            r=self.client.patch(f'/api/tickets/{tid}',headers=headers,json={**base,'status':status})
            self.assertEqual(r.status_code,200,r.text)
        a=self.client.get('/api/analytics').json()
        self.assertEqual(a['fixed'],1)
        self.assertEqual(a['verified'],1)
        self.assertEqual(a['open'],0)
        self.assertEqual(a['closed'],1)
        self.assertEqual(next(row for row in a['rounds'] if row['round']=='R05')['fixed'],1)
        r=self.client.patch(f'/api/tickets/{tid}',headers=headers,json={**base,'status':'reopened'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/analytics').json()['open'],1)
        self.assertEqual(self.client.get('/api/analytics').json()['fixed'],1)

    def test_04_ui_and_static_assets(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertIn('PERPERIS',self.client.get('/').text)
        self.assertEqual(self.client.get('/static/app.js').status_code,200)
        self.assertEqual(self.client.get('/static/styles.css').status_code,200)
        self.assertEqual(self.client.get('/static/nope').status_code,404)

if __name__ == '__main__': unittest.main()
