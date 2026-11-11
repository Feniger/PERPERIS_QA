"""Validated runtime gate for PERPERIS QA.

It only reads environment variables and imports nothing from the web stack,
so it can be tested in isolation.

- local:   allowed only off Cloud Run (no K_SERVICE).
- staging: allowed only with verified identity sign-in, GitHub as the ticket
           source, a member list and the private screenshot bucket (whose
           privacy app.py checks against Cloud Storage at start-up). Never the shared password or the
           in-memory local ticket store.
- anything else, including production: blocked. Production needs a separate,
  owner-approved release change.

Detailed identity settings (HTTPS origin, project, 32+ character secret, the
member list format and at least one Admin) are validated by
identity.check_configuration() during startup; GitHub App settings by
github_issues.config_from_env().
"""

BLOCKED = 'PERPERIS QA cloud startup is blocked: '


class StartupBlocked(RuntimeError):
    pass


def _block(reason):
    raise StartupBlocked(BLOCKED + reason)


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
    if env.get('PERPERIS_QA_TICKET_SOURCE', 'memory') != 'github':
        _block('staging requires GitHub issues as the ticket source (PERPERIS_QA_TICKET_SOURCE=github); '
               'the in-memory store is for local development only.')
    if not env.get('PERPERIS_QA_MEMBERS', '').strip():
        # Never echo the list: it contains email addresses.
        _block('staging requires the QA member list (PERPERIS_QA_MEMBERS).')
    if not env.get('PERPERIS_QA_SCREENSHOT_BUCKET', '').strip():
        _block('staging requires the private screenshot bucket (PERPERIS_QA_SCREENSHOT_BUCKET).')
    if env.get('PERPERIS_QA_SCREENSHOTS'):
        _block('staging stores screenshots only in the private bucket, never in memory.')
    return 'staging'
