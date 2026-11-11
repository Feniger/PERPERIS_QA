"""PERPERIS QA standalone local-first prototype. Not production hardened."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from database import Database
import identity
import attachments
import runtime_guard
import github_issues
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('PERPERIS_QA_DB', str(ROOT / 'qa.sqlite3')))
ADMIN_PASSWORD = os.getenv('PERPERIS_QA_PASSWORD', '')
SECRET = os.getenv('PERPERIS_QA_SECRET', '')
DATABASE = Database(os.getenv('PERPERIS_QA_DATABASE_URL', ''), sqlite_path=DB_PATH)

def check_runtime():
    """Local runs off Cloud Run, or validated private staging only.

    Cloud Run sets K_SERVICE. Staging must use verified identity sign-in, a TLS
    PostgreSQL database and private (or disabled) screenshots; the shared-password
    and SQLite prototype stays local. Production remains blocked. See
    runtime_guard.py and docs/QA-CLOUD-06-STAGING.md.
    """
    runtime_guard.check(os.environ)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_runtime()
    try:
        identity.check_configuration()
        initialize()
        APP.state.attachments = attachments.configured_store()
        APP.state.github = github_issues.from_env(os.environ)
        yield
    finally:
        github = getattr(APP.state, 'github', None)
        if github is not None:
            github.close()
        APP.state.github = None
        DATABASE.close()


APP = FastAPI(title='PERPERIS QA', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
VALID_STATUS = {'reported','triaged','in_progress','fixed','deployed','verified','closed','reopened'}
VALID_SEVERITY = {'critical','high','medium','low'}
VALID_CATEGORY = {'ui_ux','workout','coach_ai','data','performance','mobile','auth','deployment','other'}

def connection(*, write=False):
    return DATABASE.connection(write=write)


def initialize():
    DATABASE.start()
    if DATABASE.backend == 'sqlite':
        DATABASE.migrate()
    else:
        DATABASE.check_schema()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def check_setup():
    check_runtime()
    identity.check_configuration()
    if identity.mode() == 'identity':
        return
    if not ADMIN_PASSWORD or not SECRET or len(SECRET) < 32:
        raise HTTPException(503, 'Set PERPERIS_QA_PASSWORD and PERPERIS_QA_SECRET (32+ characters) before using this app')


def session_sig(value: str):
    return hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def current_user(request: Request):
    check_setup()
    if identity.mode() == 'identity':
        return identity.current(DATABASE, request.cookies.get('qa_session', ''))
    value = request.cookies.get('qa_session','')
    if not value.startswith('admin.') or not hmac.compare_digest(value.split('.',1)[1].encode(), session_sig('admin').encode()):
        raise HTTPException(401,'Sign in required')
    return identity.Principal('Admin')


def guard_mutation(request: Request, user: identity.Principal = Depends(current_user)):
    identity.require_origin(request)
    return user


class SignIn(BaseModel):
    password: str = Field(max_length=1024)

class TicketCreate(BaseModel):
    title: str = Field(min_length=3,max_length=180)
    description: str = Field(default='',max_length=10000)
    reporter: str = Field(min_length=1,max_length=100)
    category: str = 'ui_ux'
    severity: str = 'medium'
    testing_round: str = Field(min_length=1,max_length=48)
    area: str = Field(default='',max_length=120)
    environment: str = Field(default='production',max_length=70)
    deploy_version: str = Field(default='',max_length=70)
    assigned_to: str = Field(default='',max_length=100)
    issue_url: str = Field(default='',max_length=500)

    @field_validator('title', 'reporter', 'testing_round', mode='before')
    @classmethod
    def strip_required_text(cls, value):
        return value.strip() if isinstance(value, str) else value

class TicketUpdate(BaseModel):
    status: str
    expected_version: int | None = Field(default=None, ge=1)
    assigned_to: str = Field(default='',max_length=100)
    resolution_notes: str = Field(default='',max_length=10000)
    resolution_round: str = Field(default='',max_length=48)

@APP.get('/', response_class=HTMLResponse)
def index():
    return FileResponse(ROOT/'static'/'index.html',media_type='text/html')

@APP.get('/static/{filename}')
def asset(filename: str):
    if filename not in ('app.js','styles.css','auth.js','attachments.js'):
        raise HTTPException(404)
    return FileResponse(ROOT/'static'/filename)

@APP.post('/api/login')
def login(payload: SignIn,response:Response):
    check_setup()
    if identity.mode() != 'local_shared':
        raise HTTPException(404, 'Use verified identity sign-in')
    if not hmac.compare_digest(payload.password.encode(),ADMIN_PASSWORD.encode()):
        raise HTTPException(401,'Incorrect password')
    response.set_cookie('qa_session','admin.'+session_sig('admin'),httponly=True,samesite='strict',secure=os.getenv('PERPERIS_QA_HTTPS')=='1',max_age=86400)
    return {'ok':True}

@APP.post('/api/logout')
def logout(response:Response, user:identity.Principal=Depends(guard_mutation)):
    if user.session_hash:
        with connection(write=True) as db:
            db.execute('UPDATE qa_sessions SET revoked_at=? WHERE token_hash=?', (int(time.time()), user.session_hash))
            identity.security_event(db, user.subject, user.subject, 'session_revoked')
    response.delete_cookie('qa_session')
    return {'ok':True}

@APP.get('/api/me')
def me(user:identity.Principal=Depends(current_user)):
    return {'name':user.name, 'role': user.role}


def github():
    """The GitHub issues adapter when tickets come from GitHub, else None."""
    return getattr(APP.state, 'github', None)


GITHUB_READ_ONLY = ('Tickets are GitHub issues. Reporting and status changes from QA arrive in the '
                    'next milestone; use GitHub for now.')


def github_tickets(source):
    try:
        return source.tickets()
    except github_issues.GitHubUnavailable:
        raise HTTPException(502, 'GitHub could not be read. Try again shortly.') from None


def validate_enum(value, choices, field):
    if value not in choices:
        raise HTTPException(422,f'Invalid {field}')

@APP.get('/api/tickets')
def tickets(user:identity.Principal=Depends(current_user)):
    if github():
        return {'tickets': github_tickets(github())}
    with connection() as db:
        return {'tickets':[dict(row) for row in db.execute('SELECT * FROM tickets ORDER BY created_at DESC, id DESC').fetchall()]}

@APP.post('/api/tickets',status_code=201)
def create_ticket(payload:TicketCreate,user:identity.Principal=Depends(guard_mutation)):
    if github():
        raise HTTPException(405, GITHUB_READ_ONLY)
    validate_enum(payload.category,VALID_CATEGORY,'category')
    validate_enum(payload.severity,VALID_SEVERITY,'severity')
    dt=now()
    with connection(write=True) as db:
        identity.authorize_member(db, user)
        if user.role == 'Tester' and payload.assigned_to:
            raise HTTPException(403, 'Tester cannot assign tickets')
        c=db.execute('''INSERT INTO tickets(title,description,reporter,category,severity,status,testing_round,area,environment,deploy_version,assigned_to,issue_url,created_at,updated_at,reporter_subject)
         VALUES(?,?,?,?,?,'reported',?,?,?,?,?,?,?,?,?) RETURNING id''',
         (payload.title.strip(),payload.description,(user.name if user.subject else payload.reporter.strip()),payload.category,payload.severity,payload.testing_round.strip(),payload.area,payload.environment,payload.deploy_version,payload.assigned_to,payload.issue_url,dt,dt,user.subject))
        ticket_id=c.fetchone()['id']
        db.execute('INSERT INTO ticket_events(ticket_id,old_status,new_status,actor,created_at,actor_subject) VALUES(?,NULL,?,?,?,?)',(ticket_id,'reported',user.name,dt,user.subject))
        return {'ticket':dict(db.execute('SELECT * FROM tickets WHERE id=?',(ticket_id,)).fetchone())}

@APP.patch('/api/tickets/{ticket_id}')
def update_ticket(ticket_id:int,payload:TicketUpdate,user:identity.Principal=Depends(guard_mutation)):
    if github():
        raise HTTPException(405, GITHUB_READ_ONLY)
    validate_enum(payload.status,VALID_STATUS,'status')
    with connection(write=True) as db:
        identity.authorize_member(db, user)
        old=db.execute('SELECT * FROM tickets WHERE id=?' + db.lock_clause,(ticket_id,)).fetchone()
        if old is None: raise HTTPException(404,'Ticket not found')
        if payload.expected_version is not None and payload.expected_version != old['version']:
            raise HTTPException(409, 'Ticket changed; refresh before saving')
        if user.role == 'Developer' and payload.status not in {'triaged','in_progress','fixed','deployed'}:
            raise HTTPException(403, 'Developer cannot verify, close or reopen tickets')
        if user.role == 'Tester':
            if payload.status not in {'verified','closed','reopened'} or old['assigned_to'] != payload.assigned_to:
                raise HTTPException(403, 'Tester may verify, close or reopen without changing assignment')
        dt=now()
        fixed_at=old['fixed_at'] or (dt if payload.status in ('fixed','deployed','verified','closed') else None)
        deployed_at=old['deployed_at'] or (dt if payload.status in ('deployed','verified','closed') else None)
        verified_at=old['verified_at'] or (dt if payload.status in ('verified','closed') else None)
        closed_at=old['closed_at'] or (dt if payload.status == 'closed' else None)
        fixed_round=old['fixed_round'] or (payload.resolution_round.strip() or old['testing_round'] if fixed_at and not old['fixed_at'] else None)
        verified_round=old['verified_round'] or (payload.resolution_round.strip() or old['testing_round'] if verified_at and not old['verified_at'] else None)
        db.execute('''UPDATE tickets SET status=?,assigned_to=?,resolution_notes=?,updated_at=?,fixed_at=?,deployed_at=?,verified_at=?,closed_at=?,fixed_round=?,verified_round=?,version=version+1 WHERE id=?''',
                   (payload.status,payload.assigned_to,payload.resolution_notes,dt,fixed_at,deployed_at,verified_at,closed_at,fixed_round,verified_round,ticket_id))
        if old['status']!=payload.status:
            db.execute('INSERT INTO ticket_events(ticket_id,old_status,new_status,actor,created_at,actor_subject) VALUES(?,?,?,?,?,?)',(ticket_id,old['status'],payload.status,user.name,dt,user.subject))
        changes = {key: {'old': old[key], 'new': value} for key, value in {'status': payload.status, 'assigned_to': payload.assigned_to, 'resolution_notes': payload.resolution_notes}.items() if old[key] != value}
        if changes:
            db.execute('INSERT INTO ticket_changes(ticket_id,actor,actor_subject,changes_json,created_at) VALUES(?,?,?,?,?)', (ticket_id, user.name, user.subject, json.dumps(changes), dt))
        return {'ticket':dict(db.execute('SELECT * FROM tickets WHERE id=?',(ticket_id,)).fetchone())}

@APP.get('/api/analytics')
def analytics(user:identity.Principal=Depends(current_user)):
    if github():
        return compute_analytics(github_tickets(github()))
    with connection() as db:
        rows=[dict(x) for x in db.execute('SELECT * FROM tickets').fetchall()]
    return compute_analytics(rows)


def compute_analytics(rows):
    """Deterministic figures from ticket rows (database rows or GitHub-derived tickets)."""
    categories={};severity={};reporters={};rounds={}
    closed={'verified','closed'}
    for t in rows:
        for d,k in [(categories,'category'),(severity,'severity'),(reporters,'reporter')]: d[t[k]]=d.get(t[k],0)+1
        rounds.setdefault(t['testing_round'],{'round':t['testing_round'],'found':0,'fixed':0,'verified':0})['found']+=1
        if t['fixed_at']:
            rr=t['fixed_round'] or t['testing_round']
            rounds.setdefault(rr,{'round':rr,'found':0,'fixed':0,'verified':0})['fixed']+=1
        if t['verified_at']:
            rr=t['verified_round'] or t['testing_round']
            rounds.setdefault(rr,{'round':rr,'found':0,'fixed':0,'verified':0})['verified']+=1
    found=len(rows)
    verified=sum(bool(t['verified_at']) for t in rows)
    resolved=sum(bool(t['fixed_at']) for t in rows)
    open_now=sum(t['status'] not in closed for t in rows)
    order=sorted(rounds, key=lambda x:(0,int(x[1:])) if x[1:].isdigit() and x.lower().startswith('r') else (1,x.casefold()))
    round_data=[rounds[key] for key in order]
    # Crossover is based on per-round discoveries vs fixes; not a cumulative backlog claim.
    cross=next((r['round'] for r in round_data if r['found']>0 and r['fixed']>=r['found']),None)
    return {'total':found,'open':open_now,'fixed':resolved,'verified':verified,'closed':sum(t['status']=='closed' for t in rows),
            'critical':severity.get('critical',0),'severity':severity,'categories':categories,'reporters':reporters,
            'rounds':round_data,'crossover':cross,'backlog':found-verified,
            'analytics_note':'Found is counted in its discovery round; fixed/verified in the round the status was first recorded. Backlog uses found minus verified.'}


class SessionExchange(BaseModel):
    id_token: str = Field(min_length=1, max_length=16000)


class MemberUpdate(BaseModel):
    role: Literal['Admin', 'Developer', 'Tester']
    state: Literal['pending', 'active', 'disabled']


@APP.get('/api/auth/config')
def auth_config():
    check_runtime()
    identity.check_configuration()
    result = {'mode': identity.mode(),
              'attachments_enabled': getattr(APP.state, 'attachments', None) is not None and not github(),
              'tickets_source': 'github' if github() else 'database'}
    if github():
        result['tickets_repo'] = github().config.repo
    if identity.mode() == 'identity':
        # Browser API key is public project configuration, never an Admin SDK key.
        result['web_api_key'] = os.getenv('PERPERIS_QA_IDENTITY_WEB_API_KEY', '')
    return result


@APP.post('/api/session')
def exchange_session(payload: SessionExchange, request: Request, response: Response):
    check_setup()
    if identity.mode() != 'identity':
        raise HTTPException(404, 'Identity sign-in is not enabled')
    identity.require_origin(request)
    identity.throttle(DATABASE, request)
    token = identity.exchange(DATABASE, payload.id_token)
    response.set_cookie('qa_session', token, httponly=True, secure=True, samesite='strict', max_age=3600)
    return {'ok': True}


@APP.get('/api/members')
def members(user=Depends(current_user)):
    identity.require_role(user, {'Admin'})
    if not user.subject:
        raise HTTPException(403, 'Verified Admin membership required')
    with connection() as db:
        return {'members': [dict(r) for r in db.execute('SELECT subject,email,display_name,role,state FROM qa_members ORDER BY created_at,subject').fetchall()]}


@APP.patch('/api/members/{subject}')
def update_member(subject: str, payload: MemberUpdate, user=Depends(guard_mutation)):
    identity.change_member(DATABASE, user, subject, payload.role, payload.state)
    return {'ok': True}


@APP.get('/api/tickets/{ticket_id}/history')
def ticket_history(ticket_id: int, user=Depends(current_user)):
    if github():
        try:
            events = github().history(ticket_id)
        except github_issues.GitHubUnavailable:
            raise HTTPException(502, 'GitHub could not be read. Try again shortly.') from None
        if events is None:
            raise HTTPException(404, 'Ticket not found')
        return {'events': events, 'changes': []}
    with connection() as db:
        if db.execute('SELECT id FROM tickets WHERE id=?', (ticket_id,)).fetchone() is None:
            raise HTTPException(404, 'Ticket not found')
        return {'events': [dict(r) for r in db.execute('SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY id', (ticket_id,)).fetchall()],
                'changes': [dict(r) for r in db.execute('SELECT * FROM ticket_changes WHERE ticket_id=? ORDER BY id', (ticket_id,)).fetchall()]}


@APP.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob:; font-src 'self'; connect-src 'self' https://identitytoolkit.googleapis.com; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    if identity.mode() == 'identity':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response



def attachment_service():
    store = getattr(APP.state, 'attachments', None)
    if store is None or github():
        raise HTTPException(404, 'Screenshots are not enabled')
    return attachments.Attachments(DATABASE, store)


@APP.post('/api/tickets/{ticket_id}/attachments', status_code=201)
async def upload_attachment(ticket_id: int, request: Request, user=Depends(guard_mutation)):
    service = attachment_service()
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > attachments.MAX_BYTES:
            raise HTTPException(413, 'Screenshot exceeds 10 MiB')
        content.extend(chunk)
    return await run_in_threadpool(service.upload, ticket_id, bytes(content), user)


@APP.get('/api/tickets/{ticket_id}/attachments')
def list_attachments(ticket_id: int, user=Depends(current_user)):
    return {'attachments': attachment_service().list(ticket_id)}


@APP.get('/api/attachments/{attachment_id}')
def get_attachment(attachment_id: str, user=Depends(current_user)):
    return Response(attachment_service().read(attachment_id), media_type='image/png', headers={
        'Cache-Control': 'private, no-store', 'Content-Disposition': 'inline; filename="screenshot.png"'})


@APP.delete('/api/attachments/{attachment_id}')
def delete_attachment(attachment_id: str, user=Depends(guard_mutation)):
    attachment_service().delete(attachment_id, user)
    return {'ok': True}


@APP.exception_handler(RequestValidationError)
async def private_validation_error(request: Request, exc: RequestValidationError):
    # FastAPI normally echoes invalid inputs (including password/ID-token fields).
    # Return locations and generic messages only; never log private request data.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': error['loc'], 'msg': 'Invalid input', 'type': error['type']}
        for error in exc.errors()
    ]})
