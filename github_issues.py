"""GitHub issues adapter: tickets are GitHub issues (QA milestones 1 and 2).

Tickets are GitHub issues labelled `qa` in one repository (default
`krummb/kattens-pt`), read through the "PERPERIS QA" GitHub App. See
docs/QA-GITHUB-FRONTEND.md for the conventions this module implements.

Everything shown is derived from GitHub data on each read (issue fields,
labels, milestones and the issue event timeline): no counters are stored and
nothing is invented. The App private key is read from the environment (a
secret store in staging) and is never logged or returned.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import jwt

API = 'https://api.github.com'
REPO_PATTERN = re.compile(r'^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$')
ROUND_PATTERN = re.compile(r'^R\d+$')
NO_ROUND = 'No round'
SEVERITIES = ('critical', 'high', 'medium', 'low')
# Highest first: an open issue shows the most advanced status label it carries.
STATUS_LABELS = (
    ('status:verified', 'verified'),
    ('status:deployed', 'deployed'),
    ('status:fixed', 'fixed'),
    ('status:in-progress', 'in_progress'),
    ('status:triaged', 'triaged'),
)
NOT_FIXED_REASONS = {'not_planned', 'duplicate'}
REPORTED_BY = re.compile(r'^Reported by (.{1,100}?) via PERPERIS QA\s*$', re.MULTILINE)
MAX_PAGES = 20
# Area labels created in the repository (owner-approved list, 2026-11-11).
AREAS = ('today', 'coach', 'workouts', 'library', 'training-mode', 'progress', 'settings', 'admin', 'auth')
EMPTY = '—'
DESCRIPTION = re.compile(r'^### Description[ \t]*\n(.*?)(?=\n---\nReported by |\Z)', re.MULTILINE | re.DOTALL)


class GitHubUnavailable(RuntimeError):
    """GitHub could not be read. The message never contains credentials."""


class InvalidReport(ValueError):
    """A report that cannot become an issue as given (message is safe to show)."""


@dataclass(frozen=True)
class Config:
    app_id: str
    installation_id: str
    private_key: str
    repo: str


def config_from_env(env=os.environ) -> Config | None:
    """GitHub App settings, or None for the in-memory local store (the default)."""
    source = env.get('PERPERIS_QA_TICKET_SOURCE', 'memory')
    if source == 'memory':
        return None
    if source != 'github':
        raise RuntimeError('PERPERIS_QA_TICKET_SOURCE must be "github" (or "memory" for local development)')
    app_id = env.get('PERPERIS_QA_GITHUB_APP_ID', '')
    installation_id = env.get('PERPERIS_QA_GITHUB_INSTALLATION_ID', '')
    repo = env.get('PERPERIS_QA_GITHUB_REPO', 'krummb/kattens-pt')
    key = env.get('PERPERIS_QA_GITHUB_PRIVATE_KEY', '')
    key_file = env.get('PERPERIS_QA_GITHUB_PRIVATE_KEY_FILE', '')
    if not key and key_file:
        try:
            with open(key_file, encoding='utf-8') as handle:
                key = handle.read()
        except OSError:
            raise RuntimeError('PERPERIS_QA_GITHUB_PRIVATE_KEY_FILE could not be read') from None
    if not app_id.isdigit() or not installation_id.isdigit():
        raise RuntimeError('Set PERPERIS_QA_GITHUB_APP_ID and PERPERIS_QA_GITHUB_INSTALLATION_ID (numbers)')
    if 'PRIVATE KEY' not in key:
        raise RuntimeError('Set PERPERIS_QA_GITHUB_PRIVATE_KEY (PEM) or PERPERIS_QA_GITHUB_PRIVATE_KEY_FILE')
    if not REPO_PATTERN.match(repo):
        raise RuntimeError('PERPERIS_QA_GITHUB_REPO must look like owner/name')
    return Config(app_id, installation_id, key, repo)


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds')


def testing_rounds(milestones: list[dict]) -> list[tuple[float, str]]:
    """Testing-round milestones (R01, R02, ...) ordered by when they were opened."""
    rounds = [(parse_time(m.get('created_at')), m['title']) for m in milestones if ROUND_PATTERN.match(m.get('title', ''))]
    return sorted((ts, title) for ts, title in rounds if ts is not None)


def round_at(ts: float | None, rounds: list[tuple[float, str]], fallback: str) -> str:
    """The round in progress at `ts`: the newest round milestone opened before it.

    A testing round starts when its milestone is created. Without any round
    milestone opened before `ts`, the issue's own discovery round is used.
    """
    if ts is None:
        return fallback
    current = None
    for started, title in rounds:
        if started <= ts:
            current = title
    return current or fallback


def _section(body: str, heading: str) -> str:
    match = re.search(rf'^#+\s*{heading}\s*$\n+(.+)$', body, re.MULTILINE | re.IGNORECASE)
    value = match.group(1).strip()[:70] if match else ''
    return '' if value == EMPTY else value


def _description(body: str) -> str:
    """The free-text part of a QA-created issue; the whole body for issues made elsewhere."""
    match = DESCRIPTION.search(body)
    return match.group(1).strip() if match else body


def neutralize(text: str) -> str:
    """Stop user text from pinging GitHub users or teams (@name) when the App posts it."""
    return text.replace('@', '@\u200b')


def area_label(area: str) -> str | None:
    slug = re.sub(r'[\s_]+', '-', area.strip().lower())
    return f'area:{slug}' if slug in AREAS else None


def issue_body(*, description: str, environment: str, deploy_version: str, category: str,
               area: str, reporter: str) -> str:
    """The fixed issue template. Structured fields come first so free text cannot shadow them."""
    one_line = lambda value: neutralize(' '.join(value.split())) or EMPTY
    parts = [f'### Environment\n{one_line(environment)}', f'### Deploy\n{one_line(deploy_version)}',
             f'### Category\n{one_line(category)}']
    if area and not area_label(area):
        parts.append(f'### Area\n{one_line(area)}')
    parts.append(f'### Description\n{neutralize(description.strip()) or "_No description._"}')
    parts.append(f'---\nReported by {one_line(reporter)} via PERPERIS QA')
    return '\n\n'.join(parts) + '\n'


def _timeline(events: list[dict]):
    """First time each label was added, closes and reopens, in time order."""
    first_label: dict[str, float] = {}
    last_label: dict[str, float] = {}
    closes: list[tuple[float, str | None]] = []
    reopens: list[float] = []
    for event in sorted(events, key=lambda e: (parse_time(e.get('created_at')) or 0, e.get('id') or 0)):
        ts = parse_time(event.get('created_at'))
        if ts is None:
            continue
        kind = event.get('event')
        if kind == 'labeled':
            name = (event.get('label') or {}).get('name', '')
            first_label.setdefault(name, ts)
            last_label[name] = ts
        elif kind == 'closed':
            closes.append((ts, event.get('state_reason')))
        elif kind == 'reopened':
            reopens.append(ts)
    return first_label, last_label, closes, reopens


def current_status(issue: dict, labels: set[str], last_label: dict[str, float], reopens: list[float]) -> str:
    """The status now, from the most recently applied status label (not the highest-ranked one).

    Status labels are not removed when work moves on, so an issue can carry
    several. Only labels applied after the latest reopen count for the current
    cycle; a reopen newer than every status label shows as "reopened".
    """
    rank = {name: index for index, (name, _) in enumerate(STATUS_LABELS)}  # 0 = most advanced
    last_reopen = reopens[-1] if reopens else None
    present = [name for name, _ in STATUS_LABELS if name in labels]
    # Newest application wins; labels without a recorded event fall back to the most advanced.
    current_cycle = [name for name in present if last_reopen is None or last_label.get(name, 0) > last_reopen]
    newest = max(current_cycle, key=lambda name: (last_label.get(name, 0), -rank[name]), default=None)
    value = dict(STATUS_LABELS).get(newest) if newest else None
    if issue.get('state') == 'closed':
        if issue.get('state_reason') in NOT_FIXED_REASONS:
            return 'closed'
        # Closed by a merged "Fixes #N" PR counts as fixed until deployed/verified in this cycle (decision 4.4).
        return value if value in ('verified', 'deployed') else 'fixed'
    if last_reopen is not None and newest is None:
        return 'reopened'
    return value or 'reported'


def to_ticket(issue: dict, events: list[dict], rounds: list[tuple[float, str]]) -> dict:
    """Map one GitHub issue (plus its events) to the ticket shape the QA UI uses."""
    labels = {label['name'] for label in issue.get('labels', []) if isinstance(label, dict)}
    body = issue.get('body') or ''
    first_label, last_label, closes, reopens = _timeline(events)
    milestone = (issue.get('milestone') or {}).get('title') or ''
    discovery_round = milestone or NO_ROUND
    created = parse_time(issue.get('created_at'))

    fixing_closes = [ts for ts, reason in closes if reason not in NOT_FIXED_REASONS]
    candidates = [first_label.get(name) for name in ('status:fixed', 'status:deployed', 'status:verified')]
    candidates += fixing_closes[:1]
    candidates = [ts for ts in candidates if ts is not None]
    fixed_at = min(candidates) if candidates else None
    verified_at = first_label.get('status:verified')
    deployed_at = first_label.get('status:deployed') or verified_at
    closed_at = closes[0][0] if closes else None

    status = current_status(issue, labels, last_label, reopens)

    severity = next((s for s in SEVERITIES if f'sev:{s}' in labels), 'unset')
    area = next((name.split(':', 1)[1] for name in sorted(labels) if name.startswith('area:')), '')
    # The App appends the reporter line last; the last match wins so text above cannot spoof it.
    reporter_matches = REPORTED_BY.findall(body)
    reporter = reporter_matches[-1].strip() if reporter_matches else (issue.get('user') or {}).get('login', '')
    assignees = ', '.join(a.get('login', '') for a in issue.get('assignees') or [])
    return {
        'id': issue['number'],
        'title': issue.get('title', ''),
        'description': _description(body),
        'reporter': reporter,
        'category': 'task' if 'task' in labels else 'bug' if 'bug' in labels else 'other',
        'severity': severity,
        'status': status,
        'testing_round': discovery_round,
        'area': area,
        'environment': _section(body, 'Environment'),
        'deploy_version': _section(body, 'Deploy'),
        'assigned_to': assignees,
        'issue_url': issue.get('html_url', ''),
        'resolution_notes': '',
        'created_at': iso(created),
        'updated_at': issue.get('updated_at'),
        'fixed_at': iso(fixed_at),
        'deployed_at': iso(deployed_at),
        'verified_at': iso(verified_at),
        'closed_at': iso(closed_at),
        'fixed_round': round_at(fixed_at, rounds, discovery_round) if fixed_at else None,
        'verified_round': round_at(verified_at, rounds, discovery_round) if verified_at else None,
        'version': None,
        'source': 'github',
    }


def history(issue: dict, events: list[dict]) -> list[dict]:
    """Status transitions replayed from the event timeline, oldest first."""
    out = [{'old_status': None, 'new_status': 'reported', 'actor': (issue.get('user') or {}).get('login', ''),
            'created_at': issue.get('created_at')}]
    current = 'reported'
    by_label = dict(STATUS_LABELS)
    for event in sorted(events, key=lambda e: (parse_time(e.get('created_at')) or 0, e.get('id') or 0)):
        kind = event.get('event')
        new = None
        if kind == 'labeled':
            new = by_label.get((event.get('label') or {}).get('name', ''))
        elif kind == 'closed':
            new = 'closed' if event.get('state_reason') in NOT_FIXED_REASONS else (
                current if current in ('deployed', 'verified') else 'fixed')
        elif kind == 'reopened':
            new = 'reopened'
        if new and new != current:
            out.append({'old_status': current, 'new_status': new,
                        'actor': (event.get('actor') or {}).get('login', ''), 'created_at': event.get('created_at')})
            current = new
    return out


def unknown_round(testing_round: str, open_rounds: list[str]) -> InvalidReport:
    known = ', '.join(open_rounds) or 'none yet'
    return InvalidReport(f'Round "{testing_round.strip()}" is not an open testing round (open: {known})')


def report_payload(*, milestone: int, title: str, description: str, severity: str, category: str, area: str,
                   environment: str, deploy_version: str, reporter: str) -> dict:
    """The GitHub "create issue" request for one QA report."""
    if severity not in SEVERITIES:
        raise InvalidReport('Choose a severity: critical, high, medium or low')
    labels = ['qa', 'bug', f'sev:{severity}']
    if area_label(area):
        labels.append(area_label(area))
    return {'title': neutralize(' '.join(title.split())), 'labels': labels, 'milestone': milestone,
            'body': issue_body(description=description, environment=environment, deploy_version=deploy_version,
                               category=category, area=area, reporter=reporter)}


class GitHubIssues:
    def __init__(self, config: Config, *, transport: httpx.BaseTransport | None = None,
                 clock=time.time, cache_seconds: float = 60):
        self.config = config
        self.clock = clock
        self.cache_seconds = cache_seconds
        self._client = httpx.Client(base_url=API, transport=transport, timeout=15, headers={
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'perperis-qa'})
        self._lock = threading.Lock()
        self._token: tuple[str, float] | None = None
        self._snapshot: tuple[float, dict] | None = None

    def close(self):
        self._client.close()

    def _app_jwt(self) -> str:
        now = int(self.clock())
        return jwt.encode({'iat': now - 60, 'exp': now + 540, 'iss': self.config.app_id},
                          self.config.private_key, algorithm='RS256')

    def _installation_token(self) -> str:
        if self._token and self._token[1] - 60 > self.clock():
            return self._token[0]
        try:
            response = self._client.post(f'/app/installations/{self.config.installation_id}/access_tokens',
                                         headers={'Authorization': f'Bearer {self._app_jwt()}'})
        except httpx.HTTPError:
            raise GitHubUnavailable('GitHub could not be reached') from None
        if response.status_code != 201:
            raise GitHubUnavailable(f'GitHub App token request failed ({response.status_code})')
        data = response.json()
        self._token = (data['token'], parse_time(data.get('expires_at')) or self.clock() + 3000)
        return self._token[0]

    def _get_all(self, path: str, params: dict) -> list[dict]:
        headers = {'Authorization': f'token {self._installation_token()}'}
        items: list[dict] = []
        url, query = path, params
        for _ in range(MAX_PAGES):
            try:
                response = self._client.get(url, params=query, headers=headers)
            except httpx.HTTPError:
                raise GitHubUnavailable('GitHub could not be reached') from None
            if response.status_code != 200:
                raise GitHubUnavailable(f'GitHub read failed ({response.status_code})')
            items.extend(response.json())
            nxt = response.links.get('next', {}).get('url')
            if not nxt:
                return items
            url, query = nxt, None
        raise GitHubUnavailable('Too many pages from GitHub')

    def _load(self) -> dict:
        repo = self.config.repo
        issues = [i for i in self._get_all(f'/repos/{repo}/issues', {
            'labels': 'qa', 'state': 'all', 'per_page': 100, 'sort': 'created', 'direction': 'desc'})
            if 'pull_request' not in i]
        milestones = self._get_all(f'/repos/{repo}/milestones', {'state': 'all', 'per_page': 100})
        rounds = testing_rounds(milestones)
        events = {i['number']: self._get_all(f'/repos/{repo}/issues/{i["number"]}/events', {'per_page': 100})
                  for i in issues}
        return {'issues': {i['number']: i for i in issues}, 'events': events, 'milestones': milestones,
                'rounds': rounds,
                'tickets': [to_ticket(i, events[i['number']], rounds) for i in issues]}

    def snapshot(self) -> dict:
        with self._lock:
            if self._snapshot and self.clock() - self._snapshot[0] < self.cache_seconds:
                return self._snapshot[1]
            data = self._load()
            self._snapshot = (self.clock(), data)
            return data

    def invalidate(self):
        with self._lock:
            self._snapshot = None

    def open_rounds(self) -> list[str]:
        """Open testing-round milestones, oldest first (the last one is the current round)."""
        opened = [m for m in self.snapshot()['milestones'] if m.get('state') == 'open' and ROUND_PATTERN.match(m.get('title', ''))]
        return [m['title'] for m in sorted(opened, key=lambda m: parse_time(m.get('created_at')) or 0)]

    def _milestone_number(self, title: str) -> int | None:
        for attempt in range(2):
            for m in self.snapshot()['milestones']:
                if m.get('title') == title and m.get('state') == 'open' and ROUND_PATTERN.match(title):
                    return m['number']
            if attempt == 0:
                self.invalidate()  # a round opened in the last minute is not in the cache yet
        return None

    def create_issue(self, *, testing_round: str, **fields) -> dict:
        """Create one QA issue (label `qa`, `bug`, `sev:*`, optional `area:*`, round milestone)."""
        number = self._milestone_number(testing_round.strip())
        if number is None:
            raise unknown_round(testing_round, self.open_rounds())
        payload = report_payload(milestone=number, **fields)
        headers = {'Authorization': f'token {self._installation_token()}'}
        try:
            response = self._client.post(f'/repos/{self.config.repo}/issues', json=payload, headers=headers)
        except httpx.HTTPError:
            raise GitHubUnavailable('GitHub could not be reached') from None
        if response.status_code != 201:
            raise GitHubUnavailable(f'GitHub issue creation failed ({response.status_code})')
        self.invalidate()
        created = response.json()
        return to_ticket(created, [], testing_rounds(self.snapshot()['milestones']))

    def tickets(self) -> list[dict]:
        return self.snapshot()['tickets']

    def history(self, number: int) -> list[dict] | None:
        data = self.snapshot()
        issue = data['issues'].get(number)
        if issue is None:
            return None
        return history(issue, data['events'][number])


class MemoryIssues:
    """Local development and tests only: an in-memory stand-in for the GitHub repository.

    Reports go through the same request builder and issue mapping as GitHub, so
    the UI behaves the same; everything is lost on restart. Staging refuses it
    (runtime_guard requires PERPERIS_QA_TICKET_SOURCE=github).
    """

    def __init__(self, rounds=('R01',), clock=time.time):
        self.clock = clock
        self.config = Config(app_id='0', installation_id='0', private_key='', repo='local test data (not GitHub)')
        opened = iso(clock() - 3600)
        self._milestones = [{'number': n, 'state': 'open', 'title': title, 'created_at': opened}
                            for n, title in enumerate(rounds, start=1)]
        self._issues: dict[int, dict] = {}
        self._lock = threading.Lock()

    def close(self):
        pass

    def invalidate(self):
        pass

    def open_rounds(self) -> list[str]:
        return [m['title'] for m in self._milestones if m['state'] == 'open']

    def create_issue(self, *, testing_round: str, **fields) -> dict:
        milestone = next((m for m in self._milestones if m['title'] == testing_round.strip() and m['state'] == 'open'), None)
        if milestone is None:
            raise unknown_round(testing_round, self.open_rounds())
        payload = report_payload(milestone=milestone['number'], **fields)
        with self._lock:
            number = len(self._issues) + 1
            now = iso(self.clock())
            issue = {'number': number, 'title': payload['title'], 'state': 'open', 'state_reason': None,
                     'labels': [{'name': name} for name in payload['labels']], 'milestone': {'title': milestone['title']},
                     'body': payload['body'], 'created_at': now, 'updated_at': now,
                     'user': {'login': 'perperis-qa[bot]'}, 'assignees': [], 'html_url': ''}
            self._issues[number] = issue
        return to_ticket(issue, [], testing_rounds(self._milestones))

    def tickets(self) -> list[dict]:
        with self._lock:
            issues = sorted(self._issues.values(), key=lambda i: i['number'], reverse=True)
        return [to_ticket(i, [], testing_rounds(self._milestones)) for i in issues]

    def history(self, number: int) -> list[dict] | None:
        issue = self._issues.get(number)
        return history(issue, []) if issue else None


def from_env(env=os.environ) -> GitHubIssues | MemoryIssues:
    config = config_from_env(env)
    return GitHubIssues(config) if config else MemoryIssues()
