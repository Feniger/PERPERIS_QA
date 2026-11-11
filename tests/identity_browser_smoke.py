"""Synthetic HTTPS member-list/session browser test. No real identity, email or cloud API.

The --serve path is a test-only fixture, excluded from the container build.
"""
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def serve():
    import app
    import identity
    import uvicorn
    from unittest.mock import patch
    def claims(token):
        if token not in {'admin','creator','developer','tester','outsider'}:
            raise ValueError('Unknown synthetic identity')
        return {'uid':token,'email':token+'@example.test','name':token,
                'email_verified':True,'auth_time':int(time.time())}
    with patch.object(identity,'verify_provider_token',side_effect=claims),patch.object(identity,'verify_provider_session'):
        uvicorn.run(app.APP,host='127.0.0.1',port=int(os.environ['PORT']),
                    ssl_keyfile=os.environ['QA_TEST_TLS_KEY'],ssl_certfile=os.environ['QA_TEST_TLS_CERT'],access_log=False)


def main():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory() as tmp:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).sign(key,hashes.SHA256()))
        key_path=Path(tmp)/'synthetic.key';cert_path=Path(tmp)/'synthetic.pem'
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        with socket.socket() as available:
            available.bind(('127.0.0.1',0));port=available.getsockname()[1]
        origin=f'https://127.0.0.1:{port}'
        env={k:v for k,v in os.environ.items() if k not in {'K_SERVICE','FIREBASE_AUTH_EMULATOR_HOST'}}
        env.update(PERPERIS_QA_ENV='local',PERPERIS_QA_AUTH_MODE='identity',PERPERIS_QA_ORIGIN=origin,
                   PERPERIS_QA_SECRET=secrets.token_urlsafe(48),PERPERIS_QA_TICKET_SOURCE='memory',
                   PERPERIS_QA_MEMBERS='admin@example.test=Admin,creator@example.test=Admin,developer@example.test=Developer,tester@example.test=Tester',
                   PERPERIS_QA_IDENTITY_PROJECT='synthetic-project',PERPERIS_QA_IDENTITY_WEB_API_KEY='synthetic-public-browser-config',
                   PORT=str(port),QA_TEST_TLS_KEY=str(key_path),QA_TEST_TLS_CERT=str(cert_path))
        with open(Path(tmp)/'server.log','w') as log:
            server=subprocess.Popen([sys.executable,__file__,'--serve'],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                tls=ssl.create_default_context(cafile=str(cert_path))
                for _ in range(100):
                    if server.poll() is not None:raise RuntimeError('Synthetic HTTPS server failed')
                    try:
                        with urlopen(origin,context=tls,timeout=1):break
                    except OSError:time.sleep(.1)
                else:raise RuntimeError('Synthetic HTTPS startup timed out')
                with sync_playwright() as p:
                    options={'headless':True}
                    if os.getenv('PERPERIS_QA_BROWSER_EXECUTABLE'):options['executable_path']=os.environ['PERPERIS_QA_BROWSER_EXECUTABLE']
                    browser=p.chromium.launch(**options)
                    try:
                        contexts=[]
                        def user(name):
                            context=browser.new_context(ignore_https_errors=True);contexts.append(context)
                            page=context.new_page()
                            page.route('https://identitytoolkit.googleapis.com/**',lambda route:route.fulfill(json={'idToken':name}))
                            page.goto(origin+'#tickets')  # the redesign puts the ticket rows on the Tickets view
                            page.locator('#email').fill(name+'@example.test')
                            page.locator('#password').fill('synthetic-password')
                            page.locator('#login-form button.primary').click()
                            return context,page
                        admin_context,admin=user('admin')
                        expect(admin.locator('#workspace')).to_be_visible()
                        expect(admin.locator('#who')).to_have_text('admin · Admin')
                        expect(admin.locator('#manage-members')).to_have_count(0)
                        admin.locator('#new-ticket').click()
                        expect(admin.locator('[name=reporter]')).to_have_value('admin')
                        assert admin.locator('[name=reporter]').get_attribute('readonly') is not None
                        expect(admin.locator('[name=title]')).to_be_focused()  # the dialog focuses the title after 20 ms
                        admin.locator('[name=title]').fill('Synthetic HTTPS member ticket')
                        admin.locator('[name=description]').fill('Expected a list, saw nothing')
                        admin.locator('#form-submit').click()
                        expect(admin.locator('#modal')).to_be_hidden()
                        creator_context,creator=user('creator')
                        expect(creator.locator('#who')).to_have_text('creator · Admin')
                        expect(creator.locator('#ticket-rows .row-id')).to_have_text('#1')
                        developer_context,developer=user('developer')
                        expect(developer.locator('#who')).to_have_text('developer · Developer')
                        developer.locator('#ticket-rows tr').click()
                        expect(developer.locator('#ticket-detail')).to_be_visible()
                        expect(developer.locator('#ticket-detail')).to_contain_text('Reported by admin')
                        assert developer.locator('#edit-status option').all_text_contents()==['Change status…','Triaged','In progress','Fixed','Deployed']
                        developer.locator('#edit-status').select_option('fixed')
                        developer.locator('#form-submit').click()
                        expect(developer.locator('#modal')).to_be_hidden()
                        tester_context,tester=user('tester')
                        expect(tester.locator('#who')).to_have_text('tester · Tester')
                        tester.locator('#ticket-rows tr').click()
                        assert tester.locator('#edit-status option').all_text_contents()==['Change status…','Verified','Closed','Reopened']
                        tester.locator('#edit-status').select_option('verified')
                        tester.locator('[name=comment]').fill('Checked on the synthetic phone')
                        tester.locator('#form-submit').click()
                        expect(tester.locator('#modal')).to_be_hidden()
                        expect(tester.locator('#k-verified')).to_have_text('1')
                        expect(tester.locator('#ticket-rows .chip')).to_have_text('Verified')
                        cookies=admin_context.cookies()
                        session=next(c for c in cookies if c['name']=='qa_session')
                        assert session['secure'] and session['httpOnly'] and session['sameSite']=='Strict'
                        outsider_context,outsider=user('outsider')
                        expect(outsider.locator('#login-error')).to_contain_text('not on the QA member list')
                        expect(outsider.locator('#workspace')).to_be_hidden()
                        print('HTTPS identity browser smoke passed: Secure signed cookies, member list with Admin/Developer/Tester roles, '
                              'reporter from sign-in, Developer fixed and Tester verified by role, denial for unlisted emails.')
                    finally:
                        browser.close()
            finally:
                server.terminate();server.wait(timeout=10)


if __name__=='__main__':
    serve() if len(sys.argv)>1 and sys.argv[1]=='--serve' else main()
