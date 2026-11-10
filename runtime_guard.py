"""Validated runtime gate for PERPERIS QA (QA-CLOUD-06).

Replaces the blanket cloud block with explicit configuration checks, as the
release plan requires. It only reads environment variables and imports nothing
from the web stack, so it can be tested in isolation.

- local:   allowed only off Cloud Run (no K_SERVICE). Unchanged behaviour.
- staging: allowed only with verified identity mode, a TLS PostgreSQL URL and
           private (gcs) or disabled screenshots. Never shared password, SQLite
           or local screenshot files.
- anything else, including production: blocked. Production needs a separate,
  owner-approved release change.

Detailed identity settings (HTTPS origin, project, 32+ character secret) are
still validated by identity.check_configuration() during startup.
"""
from urllib.parse import parse_qs, urlsplit

BLOCKED = 'PERPERIS QA cloud startup is blocked: '
TLS_MODES = {'require', 'verify-ca', 'verify-full'}


class StartupBlocked(RuntimeError):
    pass


def _block(reason):
    raise StartupBlocked(BLOCKED + reason)


def _database_uses_tls(url):
    parts = urlsplit(url)
    if parts.scheme not in ('postgresql', 'postgres') or not parts.hostname:
        return False
    modes = parse_qs(parts.query).get('sslmode', [])
    return len(modes) == 1 and modes[0] in TLS_MODES


def check(env):
    """Raise StartupBlocked unless this runtime configuration is allowed."""
    stage = env.get('PERPERIS_QA_ENV', 'local')
    on_cloud_run = 'K_SERVICE' in env
    if stage == 'local':
        if on_cloud_run:
            _block('local mode cannot run on Cloud Run. Run locally only, or use the validated staging configuration.')
        return 'local'
    if stage != 'staging':
        _block('only local and validated staging are supported; production needs a separate owner-approved release.')
    if env.get('PERPERIS_QA_AUTH_MODE', 'local_shared') != 'identity':
        _block('staging requires verified identity sign-in (PERPERIS_QA_AUTH_MODE=identity), never the shared password.')
    url = env.get('PERPERIS_QA_DATABASE_URL', '')
    if not url:
        _block('staging requires a persistent PostgreSQL database; SQLite is local-only.')
    if not _database_uses_tls(url):
        # Never echo the URL: it contains credentials.
        _block('the staging database URL must be PostgreSQL with sslmode=require, verify-ca or verify-full.')
    if env.get('PERPERIS_QA_ATTACHMENT_BACKEND', 'disabled') not in ('disabled', 'gcs'):
        _block('staging screenshots must use the private bucket (gcs) or be disabled.')
    return 'staging'
