"""Actual Admin SDK JWT checks using local generated keys and mocked transport."""
import json
import os
from types import SimpleNamespace
import time
import unittest
import uuid
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
import firebase_admin
from firebase_admin import credentials, _auth_client, _token_gen
from google.auth.credentials import AnonymousCredentials
from google.auth import jwt
from google.auth.crypt import RSASigner

import identity


class OfflineCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


class ProviderVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        cls.public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.signer=RSASigner.from_string(private,key_id='synthetic-key')

    def setUp(self):
        self.enterContext(patch.dict(os.environ,{'PERPERIS_QA_AUTH_MODE':'identity','PERPERIS_QA_IDENTITY_PROJECT':'synthetic-project'}))
        self.provider=firebase_admin.initialize_app(OfflineCredential(),options={'projectId':'synthetic-project'},name='synthetic-test-'+uuid.uuid4().hex)
        self.addCleanup(firebase_admin.delete_app,self.provider)
        self.enterContext(patch.object(identity,'provider_app',return_value=self.provider))
        # Only transport/credential boundaries are mocked. SDK validation remains.
        response=SimpleNamespace(status=200,data=json.dumps({'synthetic-key':self.public}).encode())
        self.enterContext(patch.object(_token_gen,'CertificateFetchRequest',return_value=lambda *args,**kwargs:response))
        self.user=SimpleNamespace(disabled=False,email_verified=True,tokens_valid_after_timestamp=0)
        self.enterContext(patch.object(_auth_client.Client,'get_user',return_value=self.user))

    def token(self,**extra):
        now=int(time.time())
        claims={'iss':'https://securetoken.google.com/synthetic-project','aud':'synthetic-project',
                'sub':'synthetic-user','iat':now,'exp':now+300,'auth_time':now,
                'email':'synthetic@example.test','email_verified':True,**extra}
        return jwt.encode(self.signer,claims).decode()

    def test_valid_signed_token_requires_correct_project_and_verified_email(self):
        subject,email,name,authenticated=identity.verified_claims(self.token())
        self.assertEqual(subject,'synthetic-user')
        self.assertEqual(email,'synthetic@example.test')
        for claims in ({'aud':'training-project'},{'iss':'https://untrusted.example.test'},
                       {'exp':int(time.time())-1},{'email_verified':False}):
            with self.subTest(claims=claims):
                with self.assertRaises(HTTPException):
                    identity.verified_claims(self.token(**claims))

    def test_forged_signature_and_provider_revocation_are_rejected(self):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        forged=RSASigner.from_string(pem,key_id='synthetic-key')
        decoded=jwt.decode(self.token(),verify=False)
        with self.assertRaises(HTTPException) as exc:
            identity.verified_claims(jwt.encode(forged,decoded).decode())
        self.assertEqual(exc.exception.status_code,401)
        self.user.tokens_valid_after_timestamp=(int(time.time())+1)*1000
        with self.assertRaises(HTTPException):
            identity.verified_claims(self.token())
        self.user.tokens_valid_after_timestamp=0
        self.user.disabled=True
        with self.assertRaises(HTTPException):
            identity.verified_claims(self.token())

    def test_emulator_bypass_is_forbidden(self):
        with patch.dict(os.environ,{'FIREBASE_AUTH_EMULATOR_HOST':'127.0.0.1:9099'}):
            with self.assertRaisesRegex(RuntimeError,'Emulator'):
                identity.check_configuration()
