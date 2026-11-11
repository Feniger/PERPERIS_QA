"""Read-only GitHub issues adapter (QA milestone 1).

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


class GitHubUnavailable(RuntimeError):
    """GitHub could not be read. The message never contains credentials."""


@dataclass(frozen=True)
class Config:
    app_id: str
    installation_id: str
    private_key: str
    repo: str


def config_from_env(env=os.environ) -> Config | None:
    """None means the classic database ticket store (the default)."""
    source = env.get('PERPERIS_QA_TICKET_SOURCE', 'database')
    if source == 'database':
        return None
    if source != 'github':
        raise RuntimeError('PERPERIS_QA_TICKET_SOURCE must be "database" or "github"')
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
    return match.group(1).strip()[:70] if match else ''


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

    if issue.get('state') == 'closed':
        if 'status:verified' in labels:
            status = 'verified'
        elif issue.get('state_reason') in NOT_FIXED_REASONS:
            status = 'closed'
        elif 'status:deployed' in labels:
            status = 'deployed'
        else:
            # Closed by a merged "Fixes #N" PR: fixed, not verified (decision 4.4).
            status = 'fixed'
    else:
        present = [(name, value) for name, value in STATUS_LABELS if name in labels]
        newest_status_label = max((last_label.get(name, 0) for name, _ in present), default=0)
        if reopens and reopens[-1] > newest_status_label:
            status = 'reopened'
        elif present:
            status = present[0][1]
        else:
            status = 'reported'

    severity = next((s for s in SEVERITIES if f'sev:{s}' in labels), 'unset')
    area = next((name.split(':', 1)[1] for name in sorted(labels) if name.startswith('area:')), '')
    reporter_match = REPORTED_BY.search(body)
    reporter = reporter_match.group(1).strip() if reporter_match else (issue.get('user') or {}).get('login', '')
    assignees = ', '.join(a.get('login', '') for a in issue.get('assignees') or [])
    return {
        'id': issue['number'],
        'title': issue.get('title', ''),
        'description': body,
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
        rounds = testing_rounds(self._get_all(f'/repos/{repo}/milestones', {'state': 'all', 'per_page': 100}))
        events = {i['number']: self._get_all(f'/repos/{repo}/issues/{i["number"]}/events', {'per_page': 100})
                  for i in issues}
        return {'issues': {i['number']: i for i in issues}, 'events': events,
                'tickets': [to_ticket(i, events[i['number']], rounds) for i in issues]}

    def snapshot(self) -> dict:
        with self._lock:
            if self._snapshot and self.clock() - self._snapshot[0] < self.cache_seconds:
                return self._snapshot[1]
            data = self._load()
            self._snapshot = (self.clock(), data)
            return data

    def tickets(self) -> list[dict]:
        return self.snapshot()['tickets']

    def history(self, number: int) -> list[dict] | None:
        data = self.snapshot()
        issue = data['issues'].get(number)
        if issue is None:
            return None
        return history(issue, data['events'][number])


def from_env(env=os.environ) -> GitHubIssues | None:
    config = config_from_env(env)
    return GitHubIssues(config) if config else None
