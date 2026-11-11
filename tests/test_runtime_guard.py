"""Runtime gate contract (QA-CLOUD-06). Pure Python: no web stack, network or cloud API."""
import unittest

import runtime_guard
from runtime_guard import StartupBlocked, check

TLS_URL = 'postgresql://qa_runtime:synthetic@db.example.test/qa?sslmode=require'
STAGING = {
    'K_SERVICE': 'perperis-qa-staging',
    'PERPERIS_QA_ENV': 'staging',
    'PERPERIS_QA_AUTH_MODE': 'identity',
    'PERPERIS_QA_DATABASE_URL': TLS_URL,
    'PERPERIS_QA_ATTACHMENT_BACKEND': 'gcs',
}


class RuntimeGuardTests(unittest.TestCase):
    def blocked(self, env):
        with self.assertRaises(RuntimeError) as exc:
            check(env)
        self.assertIn('cloud startup is blocked', str(exc.exception))
        return str(exc.exception)

    def test_local_off_cloud_run_is_unchanged(self):
        self.assertEqual(check({}), 'local')
        self.assertEqual(check({'PERPERIS_QA_ENV': 'local'}), 'local')

    def test_local_mode_on_cloud_run_stays_blocked(self):
        for service in ('perperis-qa', ''):
            with self.subTest(service=service):
                self.blocked({'K_SERVICE': service})
                self.blocked({'K_SERVICE': service, 'PERPERIS_QA_ENV': 'local', 'PERPERIS_QA_AUTH_MODE': 'identity',
                              'PERPERIS_QA_DATABASE_URL': TLS_URL})

    def test_production_and_unknown_stages_stay_blocked_even_when_fully_configured(self):
        for stage in ('production', 'prod', 'cloud', 'LOCAL', 'Staging', ''):
            with self.subTest(stage=stage):
                self.blocked({**STAGING, 'PERPERIS_QA_ENV': stage})

    def test_validated_staging_is_allowed(self):
        self.assertEqual(check(STAGING), 'staging')
        self.assertEqual(check({**STAGING, 'PERPERIS_QA_ATTACHMENT_BACKEND': 'disabled'}), 'staging')
        for mode in ('verify-ca', 'verify-full'):
            url = TLS_URL.replace('sslmode=require', f'sslmode={mode}&sslrootcert=system')
            self.assertEqual(check({**STAGING, 'PERPERIS_QA_DATABASE_URL': url}), 'staging')

    def test_staging_never_uses_shared_password(self):
        for auth in ('local_shared', '', 'IDENTITY'):
            with self.subTest(auth=auth):
                self.blocked({**STAGING, 'PERPERIS_QA_AUTH_MODE': auth})
        env = dict(STAGING); del env['PERPERIS_QA_AUTH_MODE']
        self.blocked(env)

    def test_staging_requires_tls_postgres(self):
        bad = ['', 'sqlite:///qa.sqlite3', 'postgresql://u:p@db.example.test/qa',
               'postgresql://u:p@db.example.test/qa?sslmode=disable', 'postgresql://u:p@db.example.test/qa?sslmode=prefer',
               'postgresql://u:p@db.example.test/qa?sslmode=allow', 'postgresql:///qa?sslmode=require',
               'postgresql://u:p@db.example.test/qa?sslmode=require&sslmode=disable', 'mysql://u:p@db.example.test/qa?sslmode=require']
        for url in bad:
            with self.subTest(url=url):
                self.blocked({**STAGING, 'PERPERIS_QA_DATABASE_URL': url})

    def test_database_credentials_never_appear_in_the_error(self):
        message = self.blocked({**STAGING, 'PERPERIS_QA_DATABASE_URL': 'postgresql://qa:supersecret@db.example.test/qa'})
        self.assertNotIn('supersecret', message)
        self.assertNotIn('db.example.test', message)

    def test_staging_screenshots_are_private_bucket_or_off(self):
        for backend in ('local', 'unknown', 'GCS'):
            with self.subTest(backend=backend):
                self.blocked({**STAGING, 'PERPERIS_QA_ATTACHMENT_BACKEND': backend})

    def test_blocked_error_is_a_runtime_error(self):
        self.assertTrue(issubclass(StartupBlocked, RuntimeError))
        self.assertTrue(runtime_guard.BLOCKED.startswith('PERPERIS QA cloud startup is blocked'))


if __name__ == '__main__':
    unittest.main()
