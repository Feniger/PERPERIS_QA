"""Sign-in for PERPERIS QA without any QA storage (decision 4.2, lean rollout).

- Identity Platform verifies the password and the email address.
- Membership and role come from PERPERIS_QA_MEMBERS (`email=Role` pairs), kept
  in the secret store, never in Git. Roles in tokens are ignored.
- The session is a signed, expiring cookie. Rotating PERPERIS_QA_SECRET signs
  everyone out; removing an email from the list takes effect on the next request.
- Security events go to the application log (subjects only, no emails).

No passwords, raw provider tokens or service account keys are stored or logged.
"""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException

ROLES = ('Admin', 'Developer', 'Tester')
SESSION_SECONDS = 8 * 3600
THROTTLE_WINDOW = 300
THROTTLE_LIMIT = 20
LOG = logging.getLogger('perperis_qa.security')
MEMBERS_FORMAT = ('PERPERIS_QA_MEMBERS must be email=Role pairs separated by commas, '
                  'with roles Admin, Developer or Tester and at least one Admin')


@dataclass(frozen=True)
class Principal:
    name: str
    subject: str | None = None
    role: str = 'Admin'
    email: str | None = None


def mode():
    value = os.getenv('PERPERIS_QA_AUTH_MODE', 'local_shared')
    if value not in {'local_shared', 'identity'}:
        raise RuntimeError('Unknown authentication mode')
    return value


def members(env=None) -> dict[str, str]:
    """Verified email (lower case) -> role. Errors never echo the configured value."""
    raw = (os.environ if env is None else env).get('PERPERIS_QA_MEMBERS', '')
    result: dict[str, str] = {}
    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue
        email, separator, role = item.rpartition('=')
        email = email.strip().lower()
        role = role.strip()
        if not separator or '@' not in email or role not in ROLES or email in result:
            raise RuntimeError(MEMBERS_FORMAT)
        result[email] = role
    return result


def check_configuration():
    if mode() == 'identity':
        if 'FIREBASE_AUTH_EMULATOR_HOST' in os.environ:
            raise RuntimeError('Emulator authentication is not permitted')
        origin = os.getenv('PERPERIS_QA_ORIGIN', '')
        parsed = urlsplit(origin)
        if (not parsed.hostname or parsed.scheme != 'https' or parsed.path or
                parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise RuntimeError('Identity mode requires a canonical HTTPS PERPERIS_QA_ORIGIN')
        if not os.getenv('PERPERIS_QA_IDENTITY_PROJECT') or len(os.getenv('PERPERIS_QA_SECRET', '')) < 32:
            raise RuntimeError('Identity project and private 32+ character session secret are required')
        if 'Admin' not in members().values():
            raise RuntimeError(MEMBERS_FORMAT)


def require_origin(request):
    if request.headers.get('x-qa-request') != '1':
        raise HTTPException(403, 'Missing request header')
    expected = os.getenv('PERPERIS_QA_ORIGIN', '') if mode() == 'identity' else str(request.base_url).rstrip('/')
    supplied = request.headers.get('origin')
    if (mode() == 'identity' and not supplied) or (supplied and supplied != expected):
        raise HTTPException(403, 'Cross-origin request blocked')


def provider_app():
    import firebase_admin
    try:
        provider = firebase_admin.get_app('perperis-qa')
    except ValueError:
        # Called lazily after explicit identity configuration, using ADC only.
        try:
            provider = firebase_admin.initialize_app(options={
                'projectId': os.environ['PERPERIS_QA_IDENTITY_PROJECT'], 'httpTimeout': 10}, name='perperis-qa')
        except ValueError:
            provider = firebase_admin.get_app('perperis-qa')
    if provider.project_id != os.environ['PERPERIS_QA_IDENTITY_PROJECT']:
        raise ValueError('Identity project mismatch')
    return provider


def verify_provider_token(token):
    """SDK validates signature/issuer/audience/expiry; revocation check is mandatory."""
    from firebase_admin import auth
    return auth.verify_id_token(token, app=provider_app(), check_revoked=True)


def verified_claims(token):
    if not token or len(token) > 16000:
        raise HTTPException(401, 'Invalid identity token')
    try:
        claims = verify_provider_token(token)
    except Exception:
        # Provider exceptions can contain tokens or account details; never reflect them.
        raise HTTPException(401, 'Identity could not be verified') from None
    subject = claims.get('uid')
    email = claims.get('email')
    auth_time = claims.get('auth_time')
    if (not isinstance(subject, str) or not 1 <= len(subject) <= 128 or
            not isinstance(email, str) or not 1 <= len(email) <= 320 or
            claims.get('email_verified') is not True):
        raise HTTPException(403, 'Verify your email before requesting QA access')
    if (not isinstance(auth_time, (int, float)) or isinstance(auth_time, bool) or
            not time.time() - 300 <= auth_time <= time.time() + 30):
        raise HTTPException(401, 'Sign in again before creating a QA session')
    # Roles in the token/client are deliberately ignored; the member list is authoritative.
    return subject, email, str(claims.get('name') or email)[:100], int(auth_time)


def security_event(actor, target, action):
    LOG.info(json.dumps({'event': 'qa_security', 'actor': actor, 'target': target, 'action': action}))


_throttle_lock = threading.Lock()
_attempts: dict[str, tuple[float, int]] = {}


def throttle(request):
    """In-memory fixed window per instance (Identity Platform has its own limits too); HMAC IP only."""
    address = request.client.host if request.client else 'unknown'
    bucket = hmac.new(os.environ['PERPERIS_QA_SECRET'].encode(), address.encode(), hashlib.sha256).hexdigest()
    now = time.time()
    with _throttle_lock:
        for key, (start, _) in list(_attempts.items()):
            if start <= now - THROTTLE_WINDOW:
                del _attempts[key]
        start, count = _attempts.get(bucket, (now, 0))
        _attempts[bucket] = (start, count + 1)
        limited = count + 1 > THROTTLE_LIMIT
    if limited:
        raise HTTPException(429, 'Too many sign-in attempts; try again later', headers={'Retry-After': str(THROTTLE_WINDOW)})


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _signature(payload: str) -> str:
    return hmac.new(os.environ['PERPERIS_QA_SECRET'].encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_session(subject, email, name, auth_time, now=None):
    now = int(time.time() if now is None else now)
    payload = _b64(json.dumps({'v': 1, 'sub': subject, 'email': email, 'name': name, 'auth': auth_time,
                               'exp': now + SESSION_SECONDS}, separators=(',', ':')).encode())
    return payload + '.' + _signature(payload)


def exchange(token):
    subject, email, name, auth_time = verified_claims(token)
    if members().get(email.lower()) is None:
        security_event(subject, subject, 'access_denied_not_listed')
        raise HTTPException(403, 'Your email is not on the QA member list. Ask the owner to add it.')
    security_event(subject, subject, 'session_created')
    return issue_session(subject, email, name, auth_time)


def current(raw):
    if not raw or len(raw) > 2000 or raw.count('.') != 1:
        raise HTTPException(401, 'Sign in required')
    payload, signature = raw.split('.')
    if not hmac.compare_digest(signature.encode(), _signature(payload).encode()):
        raise HTTPException(401, 'Sign in required')
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except ValueError:
        raise HTTPException(401, 'Sign in required') from None
    if (not isinstance(data, dict) or data.get('v') != 1 or not isinstance(data.get('exp'), int)
            or data['exp'] <= time.time() or not isinstance(data.get('email'), str)):
        raise HTTPException(401, 'Sign in required')
    role = members().get(data['email'].lower())
    if role is None:
        raise HTTPException(401, 'Sign in required')
    verify_provider_session(data.get('sub'), data.get('auth'))
    return Principal(str(data.get('name') or data['email'])[:100], data.get('sub'), role, data['email'])


def verify_provider_session(subject, authenticated_at):
    # Provider disable/password-reset/revocation must invalidate existing sessions,
    # not just block the next exchange. Fail closed on provider/network errors.
    from firebase_admin import auth
    try:
        if not isinstance(subject, str) or not isinstance(authenticated_at, int):
            raise ValueError('Malformed session')
        user = auth.get_user(subject, app=provider_app())
        if user.disabled or not user.email_verified or user.tokens_valid_after_timestamp > authenticated_at * 1000:
            raise ValueError('Revoked identity')
    except Exception:
        raise HTTPException(401, 'Sign in again') from None


def require_role(user, roles):
    if user.role not in roles:
        raise HTTPException(403, 'Your role does not permit this action')
