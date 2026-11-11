import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import entrypoint


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(app,'ADMIN_PASSWORD','synthetic-password'))
        self.enterContext(patch.object(app,'SECRET','synthetic-secret-'*4))
        self.enterContext(patch.dict(os.environ,{'PERPERIS_QA_ENV':'local','PERPERIS_QA_AUTH_MODE':'local_shared','PERPERIS_QA_TICKET_SOURCE':'memory'}))
        self.client=self.enterContext(TestClient(app.APP))

    def test_validation_never_echoes_passwords_or_tokens(self):
        for path,body,private in (
            ('/api/login',{'password':'private-password-'*100},'private-password'),
            ('/api/session',{'id_token':'private-token-'*2000},'private-token')):
            response=self.client.post(path,json=body)
            self.assertEqual(response.status_code,422)
            self.assertNotIn(private,response.text)
            self.assertNotIn('input',response.json()['detail'][0])
        self.assertEqual(self.client.get('/openapi.json').status_code,404)

    def test_required_text_is_trimmed_before_length_validation(self):
        self.client.post('/api/login',json={'password':'synthetic-password'})
        body={'title':'Valid title','reporter':'Tester','testing_round':'R01','description':'Expected it to work, it did not'}
        for field in ('title','reporter','testing_round','description'):
            response=self.client.post('/api/tickets',headers={'X-QA-Request':'1'},json={**body,field:'   '})
            self.assertEqual(response.status_code,422)
        result=self.client.post('/api/tickets',headers={'X-QA-Request':'1'},json={**body,'title':'  Valid title  '})
        self.assertEqual(result.json()['ticket']['title'],'Valid title')

    def test_non_ascii_shared_password_and_malformed_cookie_are_handled(self):
        with patch.object(app,'ADMIN_PASSWORD','synthetic-åäö-password'):
            self.assertEqual(self.client.post('/api/login',json={'password':'synthetic-åäö-password'}).status_code,200)
            self.assertEqual(self.client.post('/api/login',json={'password':'synthetic-ä-wrong'}).status_code,401)
        from starlette.requests import Request
        from fastapi import HTTPException
        request=Request({'type':'http','headers':[(b'cookie','qa_session=admin.å'.encode('latin-1'))]})
        with self.assertRaises(HTTPException) as exc:
            app.current_user(request)
        self.assertEqual(exc.exception.status_code,401)

    def test_security_policy_blocks_third_party_assets_and_inline_scripts(self):
        response=self.client.get('/')
        policy=response.headers['Content-Security-Policy']
        self.assertIn("script-src 'self'",policy)
        self.assertNotIn('unsafe-eval',policy)
        self.assertIn("object-src 'none'",policy)
        self.assertNotIn('fonts.googleapis.com',self.client.get('/static/styles.css').text)
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')

    def test_entrypoint_validates_resource_bounds(self):
        with patch.dict(os.environ,{'PORT':'8787','PERPERIS_QA_CONCURRENCY':'2'}):
            self.assertEqual(entrypoint.settings(),(8787,2))
        for values in ({'PORT':'0'},{'PORT':'invalid'},{'PERPERIS_QA_CONCURRENCY':'100'}):
            with patch.dict(os.environ,values):
                with self.assertRaises(ValueError):
                    entrypoint.settings()
