"""Synthetic browser smoke (shared-password local mode, in-memory tickets).

pip install -r requirements-browser.txt, then run from the repository root.
No GitHub, identity provider or cloud API is contacted; provider calls in the
managed sign-in part are intercepted.
"""
import base64
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAIAAADRv8uKAAAAO0lEQVR4Xu3QUQ0AMAyE0K5CqnMSJ2upCe6HZ4CEM/dVQieiyzDG1RhXY1yNcTXG1RhXY1yNcTUmtvoDGEsBmhUNWGEAAAAASUVORK5CYII=')


def shot(name='synthetic.png'):
    return {'name': name, 'mimeType': 'image/png', 'buffer': PNG}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        password = secrets.token_urlsafe(24)
        env = {k: v for k, v in os.environ.items() if k not in {'K_SERVICE', 'PERPERIS_QA_MEMBERS'}}
        env.update(PERPERIS_QA_ENV='local', PERPERIS_QA_AUTH_MODE='local_shared', PERPERIS_QA_TICKET_SOURCE='memory',
                   PERPERIS_QA_PASSWORD=password, PERPERIS_QA_SECRET=secrets.token_urlsafe(48),
                   PERPERIS_QA_SCREENSHOTS='memory')
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        with open(Path(tmp) / 'server.log', 'w') as log:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:APP', '--host', '127.0.0.1', '--port', str(port)],
                                      env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Synthetic test server failed to start')
                    try:
                        with urlopen(origin, timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Synthetic test server startup timed out')
                with sync_playwright() as p:
                    options = {'headless': True}
                    if os.getenv('PERPERIS_QA_BROWSER_EXECUTABLE'):
                        options['executable_path'] = os.environ['PERPERIS_QA_BROWSER_EXECUTABLE']
                    browser = p.chromium.launch(**options)
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(origin + '#tickets')
                        page.locator('#password').fill(password)
                        page.locator('#login-form button.primary').click()
                        page.locator('#workspace').wait_for(state='visible')
                        expect(page.locator('#manage-members')).to_have_count(0)
                        # Full report dialog.
                        page.locator('#new-ticket').click()
                        expect(page.locator('[name=issue_url]')).to_have_count(0)
                        expect(page.locator('#screenshot-controls')).to_be_visible()
                        page.locator('[name=title]').fill('Synthetic browser report')
                        # A description is required (GitHub readers cannot see screenshots).
                        page.locator('#form-submit').click()
                        expect(page.locator('#modal')).to_be_visible()
                        page.locator('[name=description]').fill('Disposable smoke-test data')
                        page.locator('[name=area]').fill('Library')
                        page.locator('#screenshot-input').set_input_files([shot()])
                        expect(page.locator('#screenshot-queue .screenshot-item')).to_have_count(1)
                        # The issue is created first, then the screenshot uploads; success closes the dialog.
                        page.locator('#form-submit').click()
                        page.locator('#modal').wait_for(state='hidden')
                        expect(page.locator('#ticket-rows tr')).to_have_count(1)
                        expect(page.locator('#ticket-rows .row-id')).to_have_text('#1')
                        expect(page.locator('#k-total')).to_have_text('1')
                        # Ticket detail is visible and status changes are written back (milestone 3).
                        page.locator('#ticket-rows tr').click()
                        expect(page.locator('#ticket-detail')).to_be_visible()
                        expect(page.locator('#ticket-detail')).to_contain_text('Reported by Product Owner')
                        expect(page.locator('#edit-status')).to_have_value('')
                        expect(page.locator('#screenshot-existing .screenshot-item')).to_have_count(1)
                        expect(page.locator('#screenshot-count')).to_have_text('1 of 10 uploaded')
                        page.wait_for_function("() => { const i = document.querySelector('#screenshot-existing img'); "
                                               "return i && i.complete && i.naturalWidth > 0; }")
                        # Add screenshot on an existing ticket uploads at once; a failed upload offers Retry.
                        page.route('**/api/tickets/1/screenshots', lambda route: route.fulfill(status=503, json={
                            'detail': 'Screenshot storage is temporarily unavailable; retry'}) if route.request.method == 'POST' else route.continue_())
                        page.locator('#screenshot-input').set_input_files([shot('second.png')])
                        expect(page.locator('#screenshot-retry')).to_be_visible()
                        expect(page.locator('#screenshot-status')).to_contain_text('1 screenshot not uploaded')
                        page.unroute('**/api/tickets/1/screenshots')
                        page.locator('#screenshot-retry').click()
                        expect(page.locator('#screenshot-existing .screenshot-item')).to_have_count(2)
                        expect(page.locator('#screenshot-retry')).to_be_hidden()
                        page.locator('#screenshot-existing .screenshot-item button').first.click()
                        expect(page.locator('#screenshot-existing .screenshot-item')).to_have_count(1)
                        page.locator('#edit-status').select_option('fixed')
                        page.locator('[name=comment]').fill('Fixed in the synthetic build')
                        page.locator('#form-submit').click()
                        page.locator('#modal').wait_for(state='hidden')
                        expect(page.locator('#k-fixed')).to_have_text('1')
                        expect(page.locator('#ticket-rows .chip')).to_have_text('Fixed')
                        # The link in QA's GitHub comment (#ticket-N) opens that ticket.
                        page.goto(origin + '#ticket-1')   # same page: the hash change opens it
                        expect(page.locator('#modal')).to_be_visible()
                        expect(page.locator('#modal-title')).to_have_text('Synthetic browser report')
                        expect(page.locator('#screenshot-existing .screenshot-item')).to_have_count(1)
                        page.locator('#modal-close').click()
                        # Quick report on Today, defaulting to the open round.
                        page.goto(origin + '#today')
                        expect(page.locator('#quick-note')).to_contain_text('Local test data')
                        expect(page.locator('#q-round')).to_have_value('R01')
                        page.locator('#q-title').fill('Synthetic quick report')
                        page.locator('#q-description').fill('Expected a list, saw nothing')
                        page.locator('#quick-submit').click()
                        expect(page.locator('#quick-error')).to_have_text('Reported #2.')
                        expect(page.locator('#k-total')).to_have_text('2')
                        page.locator('#logout').click()
                        page.locator('#login').wait_for(state='visible')
                        # Browser-only managed provider exercise; no real provider requests.
                        calls = []
                        page.route('**/api/auth/config', lambda route: route.fulfill(json={
                            'mode': 'identity', 'web_api_key': 'synthetic-public-config', 'tickets_source': 'github'}))

                        no_account = []

                        def provider(route):
                            action = route.request.url.split('accounts:')[1].split('?')[0]
                            calls.append(action)
                            if action == 'signInWithPassword' and no_account:
                                route.fulfill(status=400, json={'error': {'code': 400, 'message': 'INVALID_LOGIN_CREDENTIALS'}})
                                return
                            route.fulfill(json={'idToken': 'synthetic-in-memory-token'})
                        page.route('https://identitytoolkit.googleapis.com/**', provider)
                        page.route('**/api/session', lambda route: route.fulfill(status=403, json={
                            'detail': 'Your email is not on the QA member list. Ask the owner to add it.'}))
                        page.reload()
                        page.locator('#email').fill('synthetic@example.test')
                        page.locator('#password').fill('synthetic-registration-password')
                        page.locator('#register').click()
                        expect(page.locator('#login-error')).to_contain_text('Account created')
                        assert calls == ['signUp', 'sendOobCode']
                        # Google's reason is shown in plain words, not a generic failure.
                        no_account.append(True)
                        page.locator('#password').fill('synthetic-registration-password')
                        page.locator('#login-form button.primary').click()
                        expect(page.locator('#login-error')).to_contain_text('Create verified account')
                        no_account.clear()
                        page.locator('#password').fill('synthetic-registration-password')
                        page.locator('#login-form button.primary').click()
                        expect(page.locator('#login-error')).to_contain_text('not on the QA member list')
                        assert page.evaluate('Object.keys(localStorage).length') == 0
                        assert page.locator('#password').input_value() == ''
                        assert not errors, errors
                        print('Browser smoke passed: reports via dialog and quick form as issues (#N), visible detail and a '
                              'status change, screenshots (queued on a new report, added on a ticket, retry, delete), no members screen, '
                              'managed signup instructions and member-list denial.')
                    finally:
                        browser.close()
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    main()
