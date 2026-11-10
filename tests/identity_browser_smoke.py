"""Synthetic HTTPS role/session browser test. No real identity, email or cloud API.

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
        if token not in {'admin','developer','tester','pending'}:
            raise ValueError('Unknown synthetic identity')
        return {'uid':token,'email':token+'@example.test','name':token,
                'email_verified':True,'auth_time':int(time.time())}
    with patch.object(identity,'verify_provider_token',side_effect=claims),patch.object(identity,'verify_provider_session'):
        app.initialize()
        for name in ('admin','developer','tester','pending'):
            try:identity.exchange(app.DATABASE,name)
            except __import__('fastapi').HTTPException:pass
        identity.bootstrap(app.DATABASE,'admin')
        actor=identity.Principal('admin','admin','Admin')
        for name,role in (('developer','Developer'),('tester','Tester')):
            identity.change_member(app.DATABASE,actor,name,role,'active')
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
        env={k:v for k,v in os.environ.items() if k not in {'K_SERVICE','PERPERIS_QA_DATABASE_URL','FIREBASE_AUTH_EMULATOR_HOST'}}
        env.update(PERPERIS_QA_ENV='local',PERPERIS_QA_AUTH_MODE='identity',PERPERIS_QA_ORIGIN=origin,
                   PERPERIS_QA_SECRET=secrets.token_urlsafe(48),PERPERIS_QA_DB=str(Path(tmp)/'identity.sqlite3'),
                   PERPERIS_QA_IDENTITY_PROJECT='synthetic-project',PERPERIS_QA_IDENTITY_WEB_API_KEY='synthetic-public-browser-config',
                   PERPERIS_QA_ATTACHMENT_BACKEND='disabled',PORT=str(port),QA_TEST_TLS_KEY=str(key_path),QA_TEST_TLS_CERT=str(cert_path))
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
                        admin.locator('#new-ticket').click()
                        expect(admin.locator('[name=reporter]')).to_have_value('admin')
                        assert admin.locator('[name=reporter]').get_attribute('readonly') is not None
                        admin.locator('[name=title]').fill('Synthetic HTTPS role ticket')
                        admin.locator('#form-submit').click()
                        expect(admin.locator('#modal')).to_be_hidden()
                        developer_context,developer=user('developer')
                        expect(developer.locator('#workspace')).to_be_visible()
                        developer.locator('#ticket-rows tr').click()
                        assert developer.locator('#edit-status option').all_text_contents()==['Triaged','In progress','Fixed','Deployed']
                        developer.locator('#edit-status').select_option('fixed')
                        developer.locator('#form-submit').click()
                        expect(developer.locator('#modal')).to_be_hidden()
                        tester_context,tester=user('tester')
                        expect(tester.locator('#workspace')).to_be_visible()
                        tester.locator('#ticket-rows tr').click()
                        assert tester.locator('#edit-status option').all_text_contents()==['Verified','Closed','Reopened']
                        tester.locator('#edit-status').select_option('verified')
                        tester.locator('#form-submit').click()
                        expect(tester.locator('#modal')).to_be_hidden()
                        expect(tester.locator('#k-verified')).to_have_text('1')
                        cookies=admin_context.cookies()
                        session=next(c for c in cookies if c['name']=='qa_session')
                        assert session['secure'] and session['httpOnly'] and session['sameSite']=='Strict'
                        admin.locator('#manage-members').click()
                        row=admin.locator('.member-row').filter(has_text='tester@example.test')
                        row.locator('select').nth(1).select_option('disabled')
                        row.locator('button').click()
                        expect(admin.locator('#members-error')).to_contain_text('Membership saved')
                        tester.reload()
                        expect(tester.locator('#login')).to_be_visible()
                        pending_context,pending=user('pending')
                        expect(pending.locator('#login-error')).to_contain_text('pending approval')
                        print('HTTPS identity browser smoke passed: Secure cookies, Admin/Developer/Tester workflow, membership disable and pending denial.')
                    finally:
                        browser.close()
            finally:
                server.terminate();server.wait(timeout=10)


if __name__=='__main__':
    serve() if len(sys.argv)>1 and sys.argv[1]=='--serve' else main()
