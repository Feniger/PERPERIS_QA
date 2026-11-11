"""Lean identity: member list + signed cookie, no QA storage (decision 4.2)."""
import base64
import json
import logging
import os
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import identity

ORIGIN = 'https://qa.example.test'
MEMBERS = 'admin@example.test=Admin, Creator@Example.test=Admin, dev@example.test=Developer, tester@example.test=Tester'


class MemberList(unittest.TestCase):
    def test_parses_pairs_case_insensitively(self):
        self.assertEqual(identity.members({'PERPERIS_QA_MEMBERS': MEMBERS}), {
            'admin@example.test': 'Admin', 'creator@example.test': 'Admin',
            'dev@example.test': 'Developer', 'tester@example.test': 'Tester'})
        self.assertEqual(identity.members({}), {})

    def test_optional_display_names(self):
        entries = identity.member_entries({'PERPERIS_QA_MEMBERS': 'erik@example.test=Admin:Erik  K, dev@example.test=Developer'})
        self.assertEqual(entries, {'erik@example.test': ('Admin', 'Erik K'), 'dev@example.test': ('Developer', '')})
        with patch.dict(os.environ, {'PERPERIS_QA_MEMBERS': 'erik@example.test=Admin:Erik,dev@example.test=Developer'}):
            self.assertEqual(identity.display_name('Erik@Example.test', 'erik@example.test'), 'Erik')  # list wins
            self.assertEqual(identity.display_name('dev@example.test', 'Dana Dev'), 'Dana Dev')       # provider name
            self.assertEqual(identity.display_name('dev@example.test', 'dev@example.test'), 'dev')    # never the address
            self.assertEqual(identity.display_name('dev@example.test'), 'dev')

    def test_rejects_bad_entries_without_echoing_them(self):
        for raw in ('secret-person@example.test', 'x@example.test=Owner', 'notanemail=Admin',
                    'a@example.test=Admin,a@example.test=Tester', 'b@example.test=Admin:Name=Other'):
            with self.assertRaises(RuntimeError) as caught:
                identity.members({'PERPERIS_QA_MEMBERS': raw})
            self.assertNotIn('example.test', str(caught.exception))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            'PERPERIS_QA_AUTH_MODE': 'identity', 'PERPERIS_QA_ENV': 'local', 'PERPERIS_QA_TICKET_SOURCE': 'memory',
            'PERPERIS_QA_ORIGIN': ORIGIN, 'PERPERIS_QA_IDENTITY_PROJECT': 'synthetic-qa-project',
            'PERPERIS_QA_SECRET': 'synthetic-session-secret-' * 3, 'PERPERIS_QA_MEMBERS': MEMBERS}))
        identity._attempts.clear()
        self.enterContext(patch.object(identity, 'verify_provider_token', side_effect=self.claims))
        self.provider_session = self.enterContext(patch.object(identity, 'verify_provider_session'))
        self.client = self.enterContext(TestClient(app.APP, base_url=ORIGIN))
        self.headers = {'X-QA-Request': '1', 'Origin': ORIGIN}

    @staticmethod
    def claims(token):
        if token == 'invalid':
            raise ValueError('private raw token must not be reflected')
        user, _, domain = token.partition('@')
        return {'uid': 'uid-' + user, 'email': token, 'name': user.title(),
                'email_verified': token != 'unverified@example.test', 'auth_time': time.time(), 'role': 'Admin'}

    def exchange(self, email, headers=None):
        self.client.cookies.clear()
        return self.client.post('/api/session', headers=self.headers if headers is None else headers,
                                json={'id_token': email})

    def create(self, **extra):
        return self.client.post('/api/tickets', headers=self.headers, json={
            'title': 'Synthetic identity ticket', 'reporter': 'Spoofed Reporter', 'testing_round': 'R01', **extra})

    def test_listed_members_get_their_listed_role(self):
        for email, role in (('admin@example.test', 'Admin'), ('creator@example.test', 'Admin'),
                            ('dev@example.test', 'Developer'), ('tester@example.test', 'Tester')):
            self.assertEqual(self.exchange(email).status_code, 200)
            self.assertEqual(self.client.get('/api/me').json()['role'], role)

    def test_unlisted_unverified_and_invalid_are_refused(self):
        response = self.exchange('stranger@example.test')
        self.assertEqual(response.status_code, 403)
        self.assertIn('not on the QA member list', response.json()['detail'])
        self.assertNotIn('qa_session', response.cookies)
        self.assertEqual(self.exchange('unverified@example.test').status_code, 403)
        invalid = self.exchange('invalid')
        self.assertEqual(invalid.status_code, 401)
        self.assertNotIn('private raw token', invalid.text)

    def test_cookie_is_secure_signed_and_expiring(self):
        response = self.exchange('dev@example.test')
        header = response.headers['set-cookie'].lower()
        for flag in ('httponly', 'secure', 'samesite=strict', f'max-age={identity.SESSION_SECONDS}'):
            self.assertIn(flag, header)
        raw = self.client.cookies['qa_session']
        payload, signature = raw.split('.')
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        self.assertNotIn('role', data, 'the role comes from the member list, not the cookie')
        forged = dict(data, email='admin@example.test')
        forged_payload = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b'=').decode()
        self.client.cookies.clear()
        me = lambda value: self.client.get('/api/me', headers={'Cookie': f'qa_session={value}'}).status_code
        self.assertEqual(me(raw), 200)
        for value in (forged_payload + '.' + signature, payload + '.' + '0' * 64, 'garbage', ''):
            self.assertEqual(me(value), 401)
        expired = identity.issue_session('uid-dev', 'dev@example.test', 'Dev', int(time.time()),
                                         now=time.time() - identity.SESSION_SECONDS - 1)
        self.assertEqual(me(expired), 401)

    def test_removal_from_list_and_secret_rotation_end_sessions(self):
        self.assertEqual(self.exchange('tester@example.test').status_code, 200)
        self.assertEqual(self.client.get('/api/me').status_code, 200)
        with patch.dict(os.environ, {'PERPERIS_QA_MEMBERS': 'admin@example.test=Admin'}):
            self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertEqual(self.client.get('/api/me').status_code, 200)
        with patch.dict(os.environ, {'PERPERIS_QA_SECRET': 'rotated-session-secret-' * 3}):
            self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_provider_revocation_ends_sessions(self):
        self.assertEqual(self.exchange('dev@example.test').status_code, 200)
        from fastapi import HTTPException
        self.provider_session.side_effect = HTTPException(401, 'Sign in again')
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_reports_never_carry_an_email_address(self):
        # Provider without a display name (like a fresh Identity Platform account): name falls back to the local part.
        with patch.object(identity, 'verify_provider_token', side_effect=lambda token: {
                'uid': 'uid-x', 'email': 'tester@example.test', 'name': None, 'email_verified': True, 'auth_time': time.time()}):
            self.assertEqual(self.exchange('anything').status_code, 200)
        self.assertEqual(self.client.get('/api/me').json()['name'], 'tester')
        self.assertEqual(self.create().json()['ticket']['reporter'], 'tester')
        with patch.dict(os.environ, {'PERPERIS_QA_MEMBERS': MEMBERS.replace('tester@example.test=Tester', 'tester@example.test=Tester:Tess T')}):
            self.assertEqual(self.client.get('/api/me').json()['name'], 'Tess T')

    def test_reporter_is_the_signed_in_member_and_tester_cannot_assign(self):
        self.assertEqual(self.exchange('tester@example.test').status_code, 200)
        self.assertEqual(self.create(assigned_to='Someone').status_code, 403)
        created = self.create()
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['ticket']['reporter'], 'Tester')

    def test_status_changes_follow_the_role_matrix(self):
        self.assertEqual(self.exchange('admin@example.test').status_code, 200)
        number = self.create().json()['ticket']['id']
        path = f'/api/tickets/{number}'

        def change(status):
            return self.client.patch(path, headers=self.headers, json={'status': status}).status_code

        self.assertEqual(self.exchange('dev@example.test').status_code, 200)
        for status in ('verified', 'closed', 'reopened'):
            self.assertEqual(change(status), 403)
        self.assertEqual(change('fixed'), 200)
        self.assertEqual(self.exchange('tester@example.test').status_code, 200)
        for status in ('triaged', 'in_progress', 'fixed', 'deployed'):
            self.assertEqual(change(status), 403)
        self.assertEqual(change('verified'), 200)
        self.assertEqual(app.APP.state.tickets.comments[number][-1], '**Verified** by Tester via PERPERIS QA')
        self.assertEqual(self.exchange('creator@example.test').status_code, 200)
        self.assertEqual(change('reopened'), 200)  # Admin: everything

    def test_origin_and_header_are_required(self):
        self.assertEqual(self.exchange('admin@example.test', headers={'X-QA-Request': '1'}).status_code, 403)
        self.assertEqual(self.exchange('admin@example.test', headers={'X-QA-Request': '1', 'Origin': 'https://evil.test'}).status_code, 403)
        self.assertEqual(self.exchange('admin@example.test').status_code, 200)
        self.assertEqual(self.client.post('/api/tickets', headers={'X-QA-Request': '1'}, json={
            'title': 'No origin', 'reporter': 'X', 'testing_round': 'R01'}).status_code, 403)

    def test_throttle_limits_session_exchange(self):
        for _ in range(identity.THROTTLE_LIMIT):
            self.exchange('stranger@example.test')
        response = self.exchange('admin@example.test')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], str(identity.THROTTLE_WINDOW))

    def test_security_events_are_logged_without_emails(self):
        with self.assertLogs('perperis_qa.security', level=logging.INFO) as logs:
            self.exchange('dev@example.test')
            self.exchange('stranger@example.test')
        text = '\n'.join(logs.output)
        self.assertIn('session_created', text)
        self.assertIn('access_denied_not_listed', text)
        self.assertNotIn('@example.test', text)

    def test_configuration_requires_an_admin_on_the_list(self):
        for members in ('', 'dev@example.test=Developer'):
            with patch.dict(os.environ, {'PERPERIS_QA_MEMBERS': members}):
                with self.assertRaises(RuntimeError):
                    identity.check_configuration()


if __name__ == '__main__':
    unittest.main()
