"""Opt-in synthetic browser smoke: pip install -r requirements-browser.txt.

Uses a temporary DB/server; managed-provider network calls are intercepted and
never send email or create users. Run from repository root.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory() as tmp:
        password = secrets.token_urlsafe(24)
        env = {k:v for k,v in os.environ.items() if k not in {'K_SERVICE','PERPERIS_QA_DATABASE_URL'}}
        env.update(PERPERIS_QA_ENV='local', PERPERIS_QA_AUTH_MODE='local_shared',
                   PERPERIS_QA_DB=str(Path(tmp)/'browser.sqlite3'), PERPERIS_QA_PASSWORD=password,
                   PERPERIS_QA_SECRET=secrets.token_urlsafe(48))
        with socket.socket() as available:
            available.bind(('127.0.0.1',0))
            port=available.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        with open(Path(tmp)/'server.log','w') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','app:APP','--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Synthetic test server failed to start')
                    try:
                        with urlopen(origin,timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Synthetic test server startup timed out')
                with sync_playwright() as p:
                    options={'headless':True}
                    if os.getenv('PERPERIS_QA_BROWSER_EXECUTABLE'):
                        options['executable_path']=os.environ['PERPERIS_QA_BROWSER_EXECUTABLE']
                    browser=p.chromium.launch(**options)
                    try:
                        page=browser.new_page()
                        errors=[]
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.route('https://fonts.googleapis.com/**',lambda route: route.abort())
                        page.route('https://fonts.gstatic.com/**',lambda route: route.abort())
                        page.goto(origin)
                        page.locator('#password').fill(password)
                        page.locator('#login-form button.primary').click()
                        page.locator('#workspace').wait_for(state='visible')
                        page.locator('#new-ticket').click()
                        page.locator('[name=title]').fill('Synthetic browser report')
                        page.locator('[name=description]').fill('Disposable smoke-test data')
                        page.locator('#form-submit').click()
                        page.locator('#ticket-rows tr').wait_for()
                        assert page.locator('#k-total').inner_text()=='1'
                        page.locator('#search').fill('Synthetic browser')
                        page.locator('#ticket-rows tr').click()
                        page.locator('#edit-status').select_option('fixed')
                        page.locator('[name=resolution_round]').fill('R02')
                        page.locator('#form-submit').click()
                        page.wait_for_function("document.querySelector('#k-fixed').textContent==='1'")
                        page.locator('#logout').click()
                        page.locator('#login').wait_for(state='visible')
                        # Browser-only managed provider exercise; no real provider requests.
                        calls=[]
                        page.route('**/api/auth/config',lambda route:route.fulfill(json={'mode':'identity','web_api_key':'synthetic-public-config'}))
                        def provider(route):
                            calls.append(route.request.url.split('accounts:')[1].split('?')[0])
                            route.fulfill(json={'idToken':'synthetic-in-memory-token'})
                        page.route('https://identitytoolkit.googleapis.com/**',provider)
                        page.route('**/api/session',lambda route:route.fulfill(status=403,json={'detail':'QA membership is pending approval or inactive'}))
                        page.reload()
                        page.locator('#email').fill('synthetic@example.test')
                        page.locator('#password').fill('synthetic-registration-password')
                        page.locator('#register').click()
                        page.wait_for_function("document.querySelector('#login-error').textContent.includes('Check your email')")
                        assert calls==['signUp','sendOobCode']
                        page.locator('#password').fill('synthetic-registration-password')
                        page.locator('#login-form button.primary').click()
                        page.wait_for_function("document.querySelector('#login-error').textContent.includes('pending approval')")
                        assert page.evaluate('Object.keys(localStorage).length')==0
                        assert page.locator('#password').input_value()==''
                        assert not errors, errors
                        print('Browser smoke passed: local tickets/metrics, managed signup/verification instructions and pending access denial.')
                    finally:
                        browser.close()
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__=='__main__':
    main()
