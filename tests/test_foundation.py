"""Cloud safety contracts, using disposable synthetic data only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FoundationTests(unittest.TestCase):
    def run_isolated(self, code, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k not in {'K_SERVICE', 'PERPERIS_QA_MEMBERS'}}
            env.update(PERPERIS_QA_ENV='local', PERPERIS_QA_AUTH_MODE='local_shared', PERPERIS_QA_TICKET_SOURCE='memory',
                       PERPERIS_QA_PASSWORD='synthetic-test-password',
                       PERPERIS_QA_SECRET='synthetic-test-secret-' * 3)
            env.update(overrides)
            before = sorted(p.name for p in ROOT.iterdir())
            result = subprocess.run([sys.executable, '-c', code], cwd=tmp,
                                    env={**env, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            # QA keeps no storage of its own: nothing may be written next to the code or in the working directory.
            return sorted(p.name for p in ROOT.iterdir()) != before or any(Path(tmp).iterdir())

    def test_import_and_startup_write_no_files(self):
        self.assertFalse(self.run_isolated('import app'))
        self.assertFalse(self.run_isolated('''
import os
from fastapi.testclient import TestClient
from app import APP
with TestClient(APP) as client:
    assert client.post('/api/login', json={'password': os.environ['PERPERIS_QA_PASSWORD']}).status_code == 200
    created = client.post('/api/tickets', headers={'X-QA-Request': '1'}, json={
        'title': 'Synthetic report', 'reporter': 'Tester', 'testing_round': 'R01', 'description': 'Expected it to work, it did not'})
    assert created.status_code == 201, created.text
'''))

    def test_cloud_run_cannot_start_even_with_local_mode_and_valid_secrets(self):
        code = '''
from fastapi.testclient import TestClient
from app import APP
try:
    with TestClient(APP):
        raise AssertionError('cloud startup succeeded')
except RuntimeError as exc:
    assert 'cloud startup is blocked' in str(exc)
'''
        for service in ('perperis-qa', ''):
            with self.subTest(service=service):
                self.assertFalse(self.run_isolated(code, K_SERVICE=service))

    def test_nonlocal_and_unknown_modes_cannot_start(self):
        code = '''
from fastapi.testclient import TestClient
from app import APP
try:
    with TestClient(APP):
        raise AssertionError('nonlocal startup succeeded')
except RuntimeError as exc:
    assert 'cloud startup is blocked' in str(exc)
'''
        for mode in ('production', 'staging', 'cloud', 'LOCAL', ''):
            with self.subTest(mode=mode):
                self.assertFalse(self.run_isolated(code, PERPERIS_QA_ENV=mode))

    def test_authenticated_api_guard_without_lifespan(self):
        self.assertFalse(self.run_isolated('''
from fastapi.testclient import TestClient
from app import APP
try:
    TestClient(APP).get('/api/me')
    raise AssertionError('runtime guard bypassed')
except RuntimeError as exc:
    assert 'cloud startup is blocked' in str(exc)
''', K_SERVICE='perperis-qa'))

    def test_theme_script_is_external_and_served(self):
        """The CSP allows no inline scripts, so dark mode must load from /static/theme.js and be allowlisted."""
        index = (ROOT / 'static' / 'index.html').read_text()
        self.assertIn('<script src="/static/theme.js"></script>', index)
        self.assertIn('data-theme-toggle', index)
        self.assertNotIn('<script>', index)
        self.assertTrue((ROOT / 'static' / 'theme.js').is_file())
        self.assertIn("'theme.js'", (ROOT / 'app.py').read_text())
        self.assertIn('html[data-theme="dark"]', (ROOT / 'static' / 'styles.css').read_text())

