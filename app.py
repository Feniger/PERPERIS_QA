"""PERPERIS QA: a front end on GitHub issues (see docs/QA-GITHUB-FRONTEND.md).

Tickets are GitHub issues (or, for local development and tests, an in-memory
stand-in). QA keeps no storage of its own: sign-in uses a member list and a
signed session cookie (identity.py).
"""
from __future__ import annotations
import hashlib
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
import identity
import runtime_guard
import github_issues
import screenshots

ROOT = Path(__file__).resolve().parent
ADMIN_PASSWORD = os.getenv('PERPERIS_QA_PASSWORD', '')
SECRET = os.getenv('PERPERIS_QA_SECRET', '')


def check_runtime():
    """Local runs off Cloud Run, or validated private staging only (runtime_guard.py)."""
    runtime_guard.check(os.environ)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_runtime()
    try:
        identity.check_configuration()
        APP.state.tickets = github_issues.from_env(os.environ)
        store = screenshots.from_env(os.environ)
        if store is not None:
            store.check_private()   # staging refuses to start unless the bucket is private
        APP.state.screenshots = screenshots.Screenshots(store) if store is not None else None
        yield
    finally:
        source = getattr(APP.state, 'tickets', None)
        if source is not None:
            source.close()
        APP.state.tickets = None
        APP.state.screenshots = None


APP = FastAPI(title='PERPERIS QA', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
VALID_SEVERITY = {'critical','high','medium','low'}
VALID_CATEGORY = {'ui_ux','workout','coach_ai','data','performance','mobile','auth','deployment','other'}
CATEGORY_NAMES = {'ui_ux': 'UI / UX', 'workout': 'Workout', 'coach_ai': 'Coach / AI', 'data': 'Data',
                  'performance': 'Performance', 'mobile': 'Mobile', 'auth': 'Sign-in', 'deployment': 'Deployment',
                  'other': 'Other'}
STATUS_CHANGES = {'triaged', 'in_progress', 'fixed', 'deployed', 'verified', 'closed', 'reopened'}
# Which status changes each role may make (Admin: all). Mirrored in static/app.js.
ROLE_STATUSES = {'Developer': {'triaged', 'in_progress', 'fixed', 'deployed'},
                 'Tester': {'verified', 'closed', 'reopened'}}
WEBHOOK_EVENTS = {'issues', 'issue_comment', 'pull_request', 'label', 'milestone'}


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
        return identity.current(request.cookies.get('qa_session', ''))
    value = request.cookies.get('qa_session','')
    if not value.startswith('admin.') or not hmac.compare_digest(value.split('.',1)[1].encode(), session_sig('admin').encode()):
        raise HTTPException(401,'Sign in required')
    return identity.Principal('Admin')


def guard_mutation(request: Request, user: identity.Principal = Depends(current_user)):
    identity.require_origin(request)
    return user


def tickets_source():
    source = getattr(APP.state, 'tickets', None)
    if source is None:
        raise HTTPException(503, 'QA is starting; try again shortly.')
    return source


def from_github(read):
    try:
        return read()
    except github_issues.GitHubUnavailable:
        raise HTTPException(502, 'GitHub could not be read. Try again shortly.') from None


class SignIn(BaseModel):
    password: str = Field(max_length=1024)

class TicketCreate(BaseModel):
    title: str = Field(min_length=3,max_length=180)
    # Required: GitHub readers cannot see QA's screenshots, so the words must carry the report.
    description: str = Field(min_length=10, max_length=10000)
    reporter: str = Field(min_length=1,max_length=100)
    category: str = 'ui_ux'
    severity: str = 'medium'
    testing_round: str = Field(min_length=1,max_length=48)
    area: str = Field(default='',max_length=120)
    environment: str = Field(default='production',max_length=70)
    deploy_version: str = Field(default='',max_length=70)
    assigned_to: str = Field(default='',max_length=100)

    @field_validator('title', 'description', 'reporter', 'testing_round', mode='before')
    @classmethod
    def strip_required_text(cls, value):
        return value.strip() if isinstance(value, str) else value

class TicketUpdate(BaseModel):
    status: str
    comment: str = Field(default='', max_length=10000)
    # GitHub's updated_at when the person opened the ticket: a newer change elsewhere gives 409.
    expected_updated_at: str | None = Field(default=None, max_length=40)


@APP.get('/', response_class=HTMLResponse)
def index():
    return FileResponse(ROOT/'static'/'index.html',media_type='text/html')

# Fixed allowlist: nothing else under static/ is ever served.
STATIC_FILES = {
    'app.js': None, 'styles.css': None, 'auth.js': None, 'attachments.js': None,
    'icon.svg': 'image/svg+xml', 'icon-16.png': 'image/png', 'icon-32.png': 'image/png',
    'icon-180.png': 'image/png', 'icon-192.png': 'image/png', 'icon-512.png': 'image/png',
    'manifest.webmanifest': 'application/manifest+json',
}
# Icons and the manifest change rarely; let browsers and home screens keep them for a day.
CACHEABLE = {name for name, kind in STATIC_FILES.items() if kind}


@APP.get('/static/{filename}')
def asset(filename: str):
    if filename not in STATIC_FILES:
        raise HTTPException(404)
    response = FileResponse(ROOT/'static'/filename, media_type=STATIC_FILES[filename])
    if filename in CACHEABLE:
        response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

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
    # Signed cookies cannot be revoked one by one; logging out removes it from this browser.
    if user.subject:
        identity.security_event(user.subject, user.subject, 'signed_out')
    response.delete_cookie('qa_session')
    return {'ok':True}

@APP.get('/api/me')
def me(user:identity.Principal=Depends(current_user)):
    return {'name':user.name, 'role': user.role}


def validate_enum(value, choices, field):
    if value not in choices:
        raise HTTPException(422,f'Invalid {field}')

@APP.get('/api/tickets')
def tickets(user:identity.Principal=Depends(current_user)):
    return {'tickets': from_github(tickets_source().tickets)}

@APP.post('/api/tickets',status_code=201)
def create_ticket(payload:TicketCreate,user:identity.Principal=Depends(guard_mutation)):
    """A QA report becomes an issue written by the PERPERIS QA App (milestone 2)."""
    validate_enum(payload.category,VALID_CATEGORY,'category')
    validate_enum(payload.severity,VALID_SEVERITY,'severity')
    if user.role == 'Tester' and payload.assigned_to:
        raise HTTPException(403, 'Tester cannot assign tickets')
    try:
        ticket = tickets_source().create_issue(
            title=payload.title, description=payload.description, severity=payload.severity,
            category=CATEGORY_NAMES[payload.category], testing_round=payload.testing_round, area=payload.area,
            environment=payload.environment, deploy_version=payload.deploy_version,
            reporter=user.name if user.subject else payload.reporter.strip())
    except github_issues.InvalidReport as exc:
        raise HTTPException(422, str(exc)) from None
    except github_issues.GitHubUnavailable:
        raise HTTPException(502, 'GitHub could not be reached. Your report was not created; try again.') from None
    return JSONResponse(status_code=201, content={'ticket': ticket})

@APP.patch('/api/tickets/{ticket_id}')
def update_ticket(ticket_id:int,payload:TicketUpdate,user:identity.Principal=Depends(guard_mutation)):
    """Triage / Verify / Reopen (milestone 3): one issue update plus a "<change> by <name>" comment."""
    validate_enum(payload.status, STATUS_CHANGES, 'status')
    if user.role != 'Admin' and payload.status not in ROLE_STATUSES.get(user.role, set()):
        raise HTTPException(403, 'Your role does not permit this status change')
    try:
        ticket = tickets_source().change_status(ticket_id, payload.status, actor=user.name, comment=payload.comment,
                                                expected_updated_at=payload.expected_updated_at)
    except github_issues.TicketNotFound:
        raise HTTPException(404, 'Ticket not found') from None
    except github_issues.TicketConflict:
        raise HTTPException(409, 'Ticket changed; refresh before saving') from None
    except github_issues.GitHubUnavailable:
        raise HTTPException(502, 'GitHub could not be reached. The status was not changed; try again.') from None
    apply_retention(ticket_id, payload.status, user)
    return {'ticket': ticket}


def apply_retention(ticket_id: int, status: str, user: identity.Principal):
    """Verify starts the 30-day deletion clock on the issue's screenshots; Reopen stops it.

    Best effort: the status change has already happened, and the 365-day limit still applies."""
    service = getattr(APP.state, 'screenshots', None)
    if service is None or status not in {'verified', 'reopened'}:
        return
    try:
        failed = service.mark_verified(ticket_id) if status == 'verified' else service.clear_verified(ticket_id)
    except Exception:
        failed = -1
    if failed:
        identity.security_event(user.subject, str(ticket_id), 'screenshot_retention_incomplete')


@APP.post('/api/github/webhook', status_code=204)
async def github_webhook(request: Request):
    """GitHub App webhook: refresh QA's view when issues, comments or PRs change.

    Verified with PERPERIS_QA_GITHUB_WEBHOOK_SECRET (HMAC-SHA256, X-Hub-Signature-256);
    off (404) when no secret is configured. It only clears the cache; QA then reads
    GitHub itself, so a webhook can never inject data.
    """
    secret = os.getenv('PERPERIS_QA_GITHUB_WEBHOOK_SECRET', '')
    if len(secret) < 20:
        raise HTTPException(404)
    body = await request.body()
    expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get('x-hub-signature-256', '').encode(), expected.encode()):
        raise HTTPException(401, 'Invalid signature')
    source = getattr(APP.state, 'tickets', None)
    if request.headers.get('x-github-event', '') in WEBHOOK_EVENTS and source is not None:
        source.invalidate()
    return Response(status_code=204)

@APP.get('/api/analytics')
def analytics(user:identity.Principal=Depends(current_user)):
    source = tickets_source()
    result = compute_analytics(from_github(source.tickets))
    rounds = from_github(source.open_rounds)
    result['current_round'] = rounds[-1] if rounds else None
    freshness = from_github(source.freshness)
    # When GitHub is unreachable, the last good data is shown marked stale (see GitHubIssues.snapshot).
    result['data_stale'], result['data_as_of'] = freshness['stale'], freshness['as_of']
    return result


def compute_analytics(rows):
    """Deterministic figures from ticket rows derived from GitHub issues."""
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
            'analytics_note':'Found is counted in its discovery round; fixed/verified in the round in progress when that first happened. Backlog uses found minus verified.'}


class SessionExchange(BaseModel):
    id_token: str = Field(min_length=1, max_length=16000)


@APP.get('/api/auth/config')
def auth_config():
    check_runtime()
    identity.check_configuration()
    source = getattr(APP.state, 'tickets', None)
    # attachments_enabled is the retired database-backed flag; the UI reads screenshots_enabled.
    result = {'mode': identity.mode(), 'attachments_enabled': False,
              'screenshots_enabled': getattr(APP.state, 'screenshots', None) is not None,
              'screenshot_limit': screenshots.MAX_PER_ISSUE,
              'tickets_source': 'github' if isinstance(source, github_issues.GitHubIssues) else 'memory',
              'tickets_repo': source.config.repo if source is not None else ''}
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
    identity.throttle(request)
    token = identity.exchange(payload.id_token)
    response.set_cookie('qa_session', token, httponly=True, secure=True, samesite='strict',
                        max_age=identity.SESSION_SECONDS)
    return {'ok': True}


@APP.get('/api/tickets/{ticket_id}/history')
def ticket_history(ticket_id: int, user=Depends(current_user)):
    events = from_github(lambda: tickets_source().history(ticket_id))
    if events is None:
        raise HTTPException(404, 'Ticket not found')
    return {'events': events, 'changes': []}


def screenshot_service():
    service = getattr(APP.state, 'screenshots', None)
    if service is None:
        raise HTTPException(404, 'Screenshots are not enabled')
    return service


def require_ticket(ticket_id: int):
    """Screenshots attach only to QA tickets (issues labeled qa) that QA can see."""
    if not any(t['id'] == ticket_id for t in from_github(tickets_source().tickets)):
        raise HTTPException(404, 'Ticket not found')


def qa_origin(request: Request) -> str:
    return os.getenv('PERPERIS_QA_ORIGIN', '') if identity.mode() == 'identity' else str(request.base_url).rstrip('/')


def update_screenshot_comment(ticket_id: int, origin: str, user: identity.Principal):
    """One bot comment per issue, "📎 N screenshots in PERPERIS QA: <link>", edited rather than reposted.

    Best effort: the screenshot change has already happened; a later change corrects the count."""
    try:
        count = APP.state.screenshots.count(ticket_id)
        tickets_source().upsert_screenshot_comment(ticket_id, count, f'{origin}/#ticket-{ticket_id}')
    except Exception:
        identity.security_event(user.subject, str(ticket_id), 'screenshot_comment_failed')


def screenshot_errors(action):
    try:
        return action()
    except screenshots.TooLarge as error:
        raise HTTPException(413, str(error)) from None
    except screenshots.InvalidImage as error:
        raise HTTPException(422, str(error)) from None
    except screenshots.LimitReached as error:
        raise HTTPException(409, str(error)) from None
    except screenshots.NotFound:
        raise HTTPException(404, 'Screenshot not found') from None
    except PermissionError as error:
        raise HTTPException(403, str(error)) from None
    except FileExistsError:
        raise HTTPException(503, 'Screenshot upload failed; retry') from None
    except HTTPException:
        raise
    except Exception:
        # Storage errors can contain bucket or object details; never reflect them.
        raise HTTPException(503, 'Screenshot storage is temporarily unavailable; retry') from None


@APP.get('/api/tickets/{ticket_id}/screenshots')
def list_screenshots(ticket_id: int, user: identity.Principal = Depends(current_user)):
    service = screenshot_service()
    require_ticket(ticket_id)
    items = screenshot_errors(lambda: service.list(ticket_id, subject=user.subject, is_admin=user.role == 'Admin'))
    return {'screenshots': items, 'limit': screenshots.MAX_PER_ISSUE}


@APP.post('/api/tickets/{ticket_id}/screenshots', status_code=201)
async def upload_screenshot(ticket_id: int, request: Request, user: identity.Principal = Depends(guard_mutation)):
    """Body: the raw image bytes. Any member may add screenshots to a QA ticket."""
    service = screenshot_service()
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > screenshots.MAX_BYTES:
        raise HTTPException(413, 'A screenshot must be at most 10 MB')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > screenshots.MAX_BYTES:
            raise HTTPException(413, 'A screenshot must be at most 10 MB')
    try:
        screenshots.throttle(user.subject or 'local')
    except screenshots.LimitReached as error:
        raise HTTPException(429, str(error), headers={'Retry-After': str(screenshots.UPLOAD_WINDOW)}) from None
    await run_in_threadpool(require_ticket, ticket_id)
    item = await run_in_threadpool(screenshot_errors, lambda: service.upload(
        ticket_id, bytes(body), subject=user.subject, name=user.name, is_admin=user.role == 'Admin'))
    identity.security_event(user.subject, str(ticket_id), 'screenshot_uploaded')
    await run_in_threadpool(update_screenshot_comment, ticket_id, qa_origin(request), user)
    return {'screenshot': item}


@APP.get('/api/tickets/{ticket_id}/screenshots/{name}')
def view_screenshot(ticket_id: int, name: str, thumb: bool = False, user: identity.Principal = Depends(current_user)):
    service = screenshot_service()
    data, content_type = screenshot_errors(lambda: service.read(ticket_id, name, thumb=thumb))
    return Response(content=data, media_type=content_type, headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': 'inline', 'Content-Security-Policy': "default-src 'none'; sandbox"})


@APP.delete('/api/tickets/{ticket_id}/screenshots/{name}', status_code=204)
def delete_screenshot(ticket_id: int, name: str, request: Request, user: identity.Principal = Depends(guard_mutation)):
    service = screenshot_service()
    screenshot_errors(lambda: service.delete(ticket_id, name, subject=user.subject, is_admin=user.role == 'Admin'))
    identity.security_event(user.subject, str(ticket_id), 'screenshot_deleted')
    update_screenshot_comment(ticket_id, qa_origin(request), user)
    return Response(status_code=204)


@APP.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers.setdefault('Content-Security-Policy', (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob:; font-src 'self'; connect-src 'self' https://identitytoolkit.googleapis.com; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ))
    if identity.mode() == 'identity':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response


@APP.exception_handler(RequestValidationError)
async def private_validation_error(request: Request, exc: RequestValidationError):
    # FastAPI normally echoes invalid inputs (including password/ID-token fields).
    # Return locations and generic messages only; never log private request data.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': error['loc'], 'msg': 'Invalid input', 'type': error['type']}
        for error in exc.errors()
    ]})
