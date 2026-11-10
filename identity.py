"""Verified managed identity, application membership, and revocable QA sessions.

No passwords, service account keys, raw session tokens or provider tokens are
stored. No identity or infrastructure is provisioned by this module.
"""
from dataclasses import dataclass
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException


@dataclass(frozen=True)
class Principal:
    name: str
    subject: str | None = None
    role: str = 'Admin'
    session_hash: str | None = None


def mode():
    value = os.getenv('PERPERIS_QA_AUTH_MODE', 'local_shared')
    if value not in {'local_shared', 'identity'}:
        raise RuntimeError('Unknown authentication mode')
    return value


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
            raise RuntimeError('Identity project and private 32+ character rate-limit secret are required')


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
    # Roles in the token/client are deliberately ignored; DB membership is authoritative.
    return subject, email, str(claims.get('name') or email)[:100], int(auth_time)


def lock_members(db):
    if db.backend == 'postgres':
        db.execute('SELECT pg_advisory_xact_lock(728194003)')


def authorize_member(db, user):
    if user.subject is None:
        return  # Local-only shared prototype; cloud startup remains blocked.
    lock_members(db)
    row = db.execute('SELECT role,state FROM qa_members WHERE subject=?', (user.subject,)).fetchone()
    if row is None or row['state'] != 'active' or row['role'] != user.role:
        raise HTTPException(403, 'Membership changed; sign in again')
    if user.session_hash and db.execute('SELECT token_hash FROM qa_sessions WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?', (user.session_hash, int(time.time()))).fetchone() is None:
        raise HTTPException(401, 'Sign in again')


def security_event(db, actor, target, action):
    db.execute('INSERT INTO qa_security_events(actor_subject,target_subject,action,created_at) VALUES(?,?,?,?)',
               (actor, target, action, int(time.time())))


def throttle(database, request):
    """Shared fixed-window throttle, persisted across instances/restarts; HMAC IP only."""
    address = request.client.host if request.client else 'unknown'
    bucket = hmac.new(os.environ['PERPERIS_QA_SECRET'].encode(), address.encode(), hashlib.sha256).hexdigest()
    current = int(time.time())
    with database.connection(write=True) as db:
        # Upsert is atomic for concurrent new/existing buckets on both backends.
        row = db.execute('''INSERT INTO qa_auth_throttle(bucket,window_start,attempts) VALUES(?,?,1)
            ON CONFLICT(bucket) DO UPDATE SET
                attempts=CASE WHEN qa_auth_throttle.window_start<=? THEN 1 ELSE qa_auth_throttle.attempts+1 END,
                window_start=CASE WHEN qa_auth_throttle.window_start<=? THEN ? ELSE qa_auth_throttle.window_start END
            RETURNING attempts''', (bucket, current, current-300, current-300, current)).fetchone()
        limited = row['attempts'] > 20
    if limited:
        raise HTTPException(429, 'Too many sign-in attempts; try again later', headers={'Retry-After': '300'})


def exchange(database, token):
    subject, email, name, auth_time = verified_claims(token)
    current = int(time.time())
    pending = False
    with database.connection(write=True) as db:
        lock_members(db)
        row = db.execute('SELECT * FROM qa_members WHERE subject=?' + db.lock_clause, (subject,)).fetchone()
        if row is None:
            db.execute("INSERT INTO qa_members(subject,email,display_name,created_at) VALUES(?,?,?,?)", (subject, email, name, current))
            security_event(db, subject, subject, 'membership_requested')
            pending = True
        elif row['state'] != 'active':
            pending = True
        else:
            raw = secrets.token_urlsafe(32)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            # A fresh sign-in rotates all previous sessions for this account.
            db.execute('UPDATE qa_sessions SET revoked_at=? WHERE subject=? AND revoked_at IS NULL', (current, subject))
            db.execute('INSERT INTO qa_sessions(token_hash,subject,created_at,authenticated_at,expires_at) VALUES(?,?,?,?,?)',
                       (digest, subject, current, auth_time, current+3600))
            security_event(db, subject, subject, 'session_created')
    # Denied membership must still commit the pending request above.
    if pending:
        raise HTTPException(403, 'QA membership is pending approval or inactive')
    return raw


def current(database, raw):
    if not raw or len(raw) > 100:
        raise HTTPException(401, 'Sign in required')
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with database.connection() as db:
        row = db.execute('''SELECT m.*,s.token_hash,s.authenticated_at FROM qa_sessions s JOIN qa_members m ON m.subject=s.subject
            WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND m.state='active' ''',
                         (digest, int(time.time()))).fetchone()
    if row is None:
        raise HTTPException(401, 'Sign in required')
    verify_provider_session(row['subject'], row['authenticated_at'])
    return Principal(row['display_name'], row['subject'], row['role'], digest)


def verify_provider_session(subject, authenticated_at):
    # Provider disable/password-reset/revocation must invalidate existing sessions,
    # not just block the next exchange. Fail closed on provider/network errors.
    import firebase_admin
    from firebase_admin import auth
    try:
        user = auth.get_user(subject, app=provider_app())
        if user.disabled or not user.email_verified or user.tokens_valid_after_timestamp > authenticated_at * 1000:
            raise ValueError('Revoked identity')
    except Exception:
        raise HTTPException(401, 'Sign in again') from None


def require_role(user, roles):
    if user.role not in roles:
        raise HTTPException(403, 'Your role does not permit this action')


def change_member(database, actor, subject, role, state):
    require_role(actor, {'Admin'})
    with database.connection(write=True) as db:
        lock_members(db)
        # Recheck actor under the same membership lock as a role/disable update.
        active = db.execute('SELECT role,state FROM qa_members WHERE subject=?', (actor.subject,)).fetchone()
        if active is None or active['role'] != 'Admin' or active['state'] != 'active':
            raise HTTPException(403, 'Active Admin membership required')
        target = db.execute('SELECT * FROM qa_members WHERE subject=?' + db.lock_clause, (subject,)).fetchone()
        if target is None:
            raise HTTPException(404, 'Member not found')
        if target['role'] == 'Admin' and target['state'] == 'active' and (role != 'Admin' or state != 'active'):
            count = db.execute("SELECT COUNT(*) AS n FROM qa_members WHERE role='Admin' AND state='active'").fetchone()['n']
            if count <= 1:
                raise HTTPException(409, 'The final active Admin cannot be removed')
        db.execute('UPDATE qa_members SET role=?,state=? WHERE subject=?', (role, state, subject))
        db.execute('UPDATE qa_sessions SET revoked_at=? WHERE subject=? AND revoked_at IS NULL', (int(time.time()), subject))
        security_event(db, actor.subject, subject, f'membership:{role}:{state}')


def bootstrap(database, subject):
    """Explicit private operator action, only for an already verified pending member."""
    with database.connection(write=True) as db:
        lock_members(db)
        if db.execute("SELECT COUNT(*) AS n FROM qa_members WHERE role='Admin' AND state='active'").fetchone()['n']:
            raise ValueError('An active Admin already exists')
        member = db.execute('SELECT state FROM qa_members WHERE subject=?', (subject,)).fetchone()
        if member is None or member['state'] != 'pending':
            raise ValueError('Bootstrap requires an existing verified pending membership')
        db.execute("UPDATE qa_members SET role='Admin',state='active' WHERE subject=?", (subject,))
        security_event(db, None, subject, 'admin_bootstrapped_by_operator')


def main():
    import argparse
    from database import from_env
    parser = argparse.ArgumentParser(description='One-time private QA Admin bootstrap (no provisioning).')
    parser.add_argument('--subject', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        parser.error('--apply is required to change membership')
    db = from_env()
    try:
        db.start()
        db.check_schema()
        bootstrap(db, args.subject)
        print('Admin bootstrap completed.')
    except Exception:
        parser.exit(1, 'Admin bootstrap failed; verify private membership and configuration.\n')
    finally:
        db.close()


if __name__ == '__main__':
    main()
