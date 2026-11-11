"""Runtime gate contract. Pure Python: no web stack, network or cloud API."""
import unittest

from runtime_guard import StartupBlocked, check

MEMBERS = 'owner@example.test=Admin,creator@example.test=Admin'
STAGING = {
    'K_SERVICE': 'perperis-qa-staging',
    'PERPERIS_QA_ENV': 'staging',
    'PERPERIS_QA_AUTH_MODE': 'identity',
    'PERPERIS_QA_TICKET_SOURCE': 'github',
    'PERPERIS_QA_MEMBERS': MEMBERS,
    'PERPERIS_QA_SCREENSHOT_BUCKET': 'synthetic-private-bucket',
}


class RuntimeGuardTests(unittest.TestCase):
    def blocked(self, env):
        with self.assertRaises(RuntimeError) as exc:
            check(env)
        self.assertIn('cloud startup is blocked', str(exc.exception))
        return str(exc.exception)

    def test_local_off_cloud_run_is_allowed(self):
        self.assertEqual(check({}), 'local')
        self.assertEqual(check({'PERPERIS_QA_ENV': 'local'}), 'local')

    def test_local_mode_on_cloud_run_stays_blocked(self):
        for service in ('perperis-qa', ''):
            with self.subTest(service=service):
                self.blocked({'K_SERVICE': service})
                self.blocked({**STAGING, 'K_SERVICE': service, 'PERPERIS_QA_ENV': 'local'})

    def test_production_and_unknown_stages_stay_blocked_even_when_fully_configured(self):
        for stage in ('production', 'prod', 'cloud', 'LOCAL', 'Staging', ''):
            with self.subTest(stage=stage):
                self.blocked({**STAGING, 'PERPERIS_QA_ENV': stage})

    def test_validated_staging_is_allowed(self):
        self.assertEqual(check(STAGING), 'staging')
        self.assertEqual(check({k: v for k, v in STAGING.items() if k != 'K_SERVICE'}), 'staging')

    def test_staging_never_uses_the_shared_password(self):
        for mode in (None, 'local_shared', 'IDENTITY', ''):
            env = dict(STAGING)
            env.pop('PERPERIS_QA_AUTH_MODE')
            if mode is not None:
                env['PERPERIS_QA_AUTH_MODE'] = mode
            with self.subTest(mode=mode):
                self.assertIn('identity', self.blocked(env))

    def test_staging_requires_github_issues(self):
        for source in (None, 'memory', 'database', 'GitHub'):
            env = dict(STAGING)
            env.pop('PERPERIS_QA_TICKET_SOURCE')
            if source is not None:
                env['PERPERIS_QA_TICKET_SOURCE'] = source
            with self.subTest(source=source):
                self.assertIn('GitHub issues', self.blocked(env))

    def test_staging_requires_the_private_screenshot_bucket(self):
        for value in (None, '', '  '):
            env = dict(STAGING)
            env.pop('PERPERIS_QA_SCREENSHOT_BUCKET')
            if value is not None:
                env['PERPERIS_QA_SCREENSHOT_BUCKET'] = value
            with self.subTest(value=value):
                self.assertIn('screenshot bucket', self.blocked(env))
        self.assertIn('never in memory', self.blocked({**STAGING, 'PERPERIS_QA_SCREENSHOTS': 'memory'}))

    def test_staging_requires_a_member_list_and_never_echoes_it(self):
        for members in (None, '', '   '):
            env = dict(STAGING)
            env.pop('PERPERIS_QA_MEMBERS')
            if members is not None:
                env['PERPERIS_QA_MEMBERS'] = members
            with self.subTest(members=members):
                message = self.blocked(env)
                self.assertIn('member list', message)
                self.assertNotIn('example.test', message)

    def test_blocked_error_is_a_runtime_error(self):
        self.assertTrue(issubclass(StartupBlocked, RuntimeError))


if __name__ == '__main__':
    unittest.main()
