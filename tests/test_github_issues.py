"""Milestone 1: read-only GitHub issues adapter.

All GitHub responses are recorded/synthetic fixtures served by httpx.MockTransport.
These tests never contact GitHub and never use a real App key.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import github_issues as gh

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
PUBLIC = KEY.public_key()
REPO = 'krummb/kattens-pt'
CONFIG = gh.Config(app_id='5711212', installation_id='180847731', private_key=PEM, repo=REPO)


def label(name):
    return {'name': name}


def issue(number, title, *, state='open', labels=(), milestone='R05', body='', state_reason=None,
          created='2026-11-12T09:00:00Z', updated='2026-11-12T10:00:00Z', assignees=(), pr=False):
    data = {'number': number, 'title': title, 'state': state, 'state_reason': state_reason,
            'labels': [label(n) for n in labels], 'milestone': {'title': milestone} if milestone else None,
            'body': body, 'created_at': created, 'updated_at': updated, 'user': {'login': 'krummb'},
            'assignees': [{'login': a} for a in assignees],
            'html_url': f'https://github.com/{REPO}/issues/{number}'}
    if pr:
        data['pull_request'] = {'url': 'x'}
    return data


def ev(kind, when, *, name=None, reason=None, actor='perperis-qa[bot]', id_=0):
    e = {'id': id_, 'event': kind, 'created_at': when, 'actor': {'login': actor}}
    if name:
        e['label'] = {'name': name}
    if kind == 'closed':
        e['state_reason'] = reason
    return e


MILESTONES = [
    {'number': 1, 'state': 'open', 'title': 'R05', 'created_at': '2026-11-11T08:00:00Z'},
    {'number': 2, 'state': 'open', 'title': 'R06', 'created_at': '2026-11-20T08:00:00Z'},
    {'number': 3, 'state': 'open', 'title': 'Backlog', 'created_at': '2026-11-01T08:00:00Z'},
    {'number': 4, 'state': 'closed', 'title': 'R04', 'created_at': '2026-10-01T08:00:00Z'},
]
ROUNDS = gh.testing_rounds(MILESTONES)


class Mapping(unittest.TestCase):
    def test_open_issue_fields_from_labels_milestone_and_body(self):
        body = ('### Steps\nOpen library\n\n### Environment\niPhone 15, Safari\n\n### Deploy\nDeploy #24\n\n'
                'Reported by Anna Tester via PERPERIS QA\n')
        t = gh.to_ticket(issue(12, 'Picker stuck', labels=['qa', 'bug', 'sev:high', 'area:library', 'status:triaged'],
                               body=body, assignees=['Feniger']),
                         [ev('labeled', '2026-11-12T09:05:00Z', name='status:triaged')], ROUNDS)
        self.assertEqual(t['id'], 12)
        self.assertEqual(t['status'], 'triaged')
        self.assertEqual(t['severity'], 'high')
        self.assertEqual(t['area'], 'library')
        self.assertEqual(t['category'], 'bug')
        self.assertEqual(t['testing_round'], 'R05')
        self.assertEqual(t['reporter'], 'Anna Tester')
        self.assertEqual(t['environment'], 'iPhone 15, Safari')
        self.assertEqual(t['deploy_version'], 'Deploy #24')
        self.assertEqual(t['assigned_to'], 'Feniger')
        self.assertEqual(t['issue_url'], f'https://github.com/{REPO}/issues/12')
        self.assertIsNone(t['fixed_at'])

    def test_unlabelled_issue_is_reported_without_invented_severity_or_round(self):
        t = gh.to_ticket(issue(3, 'Typo', labels=['qa'], milestone=None), [], ROUNDS)
        self.assertEqual(t['status'], 'reported')
        self.assertEqual(t['severity'], 'unset')
        self.assertEqual(t['testing_round'], gh.NO_ROUND)
        self.assertEqual(t['reporter'], 'krummb')
        self.assertEqual(t['category'], 'other')

    def test_closed_by_pr_counts_as_fixed_not_verified(self):
        t = gh.to_ticket(issue(5, 'Crash', state='closed', state_reason='completed', labels=['qa', 'bug']),
                         [ev('closed', '2026-11-13T12:00:00Z', reason='completed')], ROUNDS)
        self.assertEqual(t['status'], 'fixed')
        self.assertEqual(t['fixed_at'], '2026-11-13T12:00:00+00:00')
        self.assertEqual(t['fixed_round'], 'R05')
        self.assertIsNone(t['verified_at'])

    def test_deployed_then_verified(self):
        events = [ev('closed', '2026-11-13T12:00:00Z', reason='completed', id_=1),
                  ev('labeled', '2026-11-13T12:10:00Z', name='status:deployed', id_=2),
                  ev('labeled', '2026-11-21T09:00:00Z', name='status:verified', id_=3)]
        base = dict(state='closed', state_reason='completed')
        deployed = gh.to_ticket(issue(6, 'X', labels=['qa', 'status:deployed'], **base), events[:2], ROUNDS)
        self.assertEqual(deployed['status'], 'deployed')
        verified = gh.to_ticket(issue(6, 'X', labels=['qa', 'status:deployed', 'status:verified'], **base), events, ROUNDS)
        self.assertEqual(verified['status'], 'verified')
        self.assertEqual(verified['verified_round'], 'R06')  # verified after R06 opened
        self.assertEqual(verified['fixed_round'], 'R05')

    def test_closed_not_planned_is_closed_and_never_fixed(self):
        t = gh.to_ticket(issue(7, 'Dup', state='closed', state_reason='duplicate', labels=['qa']),
                         [ev('closed', '2026-11-13T12:00:00Z', reason='duplicate')], ROUNDS)
        self.assertEqual(t['status'], 'closed')
        self.assertIsNone(t['fixed_at'])
        self.assertEqual(t['closed_at'], '2026-11-13T12:00:00+00:00')

    def test_reopen_after_fix_keeps_first_fix_milestone(self):
        events = [ev('labeled', '2026-11-12T10:00:00Z', name='status:fixed', id_=1),
                  ev('closed', '2026-11-12T11:00:00Z', reason='completed', id_=2),
                  ev('reopened', '2026-11-14T08:00:00Z', id_=3)]
        t = gh.to_ticket(issue(8, 'Back again', labels=['qa', 'status:fixed']), events, ROUNDS)
        self.assertEqual(t['status'], 'reopened')
        self.assertEqual(t['fixed_at'], '2026-11-12T10:00:00+00:00')

    def test_newest_status_label_wins_after_reopen(self):
        # Review scenario 1: fixed -> deployed -> reopened -> in progress again. Old labels stay on the issue.
        events = [ev('labeled', '2026-11-12T10:00:00Z', name='status:fixed', id_=1),
                  ev('closed', '2026-11-12T10:05:00Z', reason='completed', id_=2),
                  ev('labeled', '2026-11-12T11:00:00Z', name='status:deployed', id_=3),
                  ev('reopened', '2026-11-13T09:00:00Z', id_=4),
                  ev('labeled', '2026-11-13T09:30:00Z', name='status:in-progress', id_=5)]
        t = gh.to_ticket(issue(20, 'Back in progress', labels=['qa', 'status:fixed', 'status:deployed', 'status:in-progress']),
                         events, ROUNDS)
        self.assertEqual(t['status'], 'in_progress')
        self.assertEqual(t['fixed_at'], '2026-11-12T10:00:00+00:00')  # first milestones survive a reopen

    def test_old_verified_label_does_not_make_a_new_fix_verified(self):
        # Review scenario 2: verified -> reopened -> closed again by a new PR: fixed, not verified.
        events = [ev('closed', '2026-11-12T10:00:00Z', reason='completed', id_=1),
                  ev('labeled', '2026-11-12T12:00:00Z', name='status:verified', id_=2),
                  ev('reopened', '2026-11-20T09:00:00Z', id_=3),
                  ev('closed', '2026-11-21T09:00:00Z', reason='completed', id_=4)]
        base = dict(state='closed', state_reason='completed')
        t = gh.to_ticket(issue(21, 'Regressed', labels=['qa', 'status:verified'], **base), events, ROUNDS)
        self.assertEqual(t['status'], 'fixed')
        # Deployed again in the new cycle: deployed, still not verified.
        later = events + [ev('labeled', '2026-11-21T10:00:00Z', name='status:deployed', id_=5)]
        t = gh.to_ticket(issue(21, 'Regressed', labels=['qa', 'status:verified', 'status:deployed'], **base), later, ROUNDS)
        self.assertEqual(t['status'], 'deployed')
        # Verified again in the new cycle (Verify re-applies the label): verified.
        again = later + [ev('labeled', '2026-11-22T10:00:00Z', name='status:verified', id_=6)]
        t = gh.to_ticket(issue(21, 'Regressed', labels=['qa', 'status:verified', 'status:deployed'], **base), again, ROUNDS)
        self.assertEqual(t['status'], 'verified')

    def test_reopened_on_github_without_new_labels_shows_reopened(self):
        events = [ev('labeled', '2026-11-12T10:00:00Z', name='status:verified', id_=1),
                  ev('reopened', '2026-11-13T09:00:00Z', id_=2)]
        t = gh.to_ticket(issue(22, 'Reopened directly on GitHub', labels=['qa', 'status:verified']), events, ROUNDS)
        self.assertEqual(t['status'], 'reopened')

    def test_history_replays_status_changes(self):
        events = [ev('labeled', '2026-11-12T10:00:00Z', name='status:triaged', id_=1),
                  ev('labeled', '2026-11-12T10:01:00Z', name='area:today', id_=2),
                  ev('closed', '2026-11-13T12:00:00Z', reason='completed', id_=3),
                  ev('reopened', '2026-11-14T08:00:00Z', id_=4)]
        steps = [(h['old_status'], h['new_status']) for h in gh.history(issue(9, 'H'), events)]
        self.assertEqual(steps, [(None, 'reported'), ('reported', 'triaged'), ('triaged', 'fixed'), ('fixed', 'reopened')])

    def test_round_at_uses_newest_round_opened_before_the_event(self):
        self.assertEqual([r[1] for r in ROUNDS], ['R04', 'R05', 'R06'])  # closed rounds still count as history
        self.assertEqual(gh.round_at(gh.parse_time('2026-11-10T00:00:00Z'), ROUNDS, 'R04'), 'R04')
        self.assertEqual(gh.round_at(gh.parse_time('2026-11-25T00:00:00Z'), ROUNDS, 'R05'), 'R06')


class Config(unittest.TestCase):
    def test_memory_is_the_local_default_and_database_is_gone(self):
        self.assertIsNone(gh.config_from_env({}))
        self.assertIsInstance(gh.from_env({}), gh.MemoryIssues)
        with self.assertRaises(RuntimeError):
            gh.config_from_env({'PERPERIS_QA_TICKET_SOURCE': 'database'})

    def test_github_mode_requires_ids_and_key(self):
        with self.assertRaises(RuntimeError):
            gh.config_from_env({'PERPERIS_QA_TICKET_SOURCE': 'github'})
        with self.assertRaises(RuntimeError):
            gh.config_from_env({'PERPERIS_QA_TICKET_SOURCE': 'github', 'PERPERIS_QA_GITHUB_APP_ID': '1',
                                'PERPERIS_QA_GITHUB_INSTALLATION_ID': '2', 'PERPERIS_QA_GITHUB_PRIVATE_KEY': 'nope'})
        with self.assertRaises(RuntimeError):
            gh.config_from_env({'PERPERIS_QA_TICKET_SOURCE': 'githubb'})

    def test_key_from_file_and_default_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'key.pem'
            path.write_text(PEM)
            cfg = gh.config_from_env({'PERPERIS_QA_TICKET_SOURCE': 'github', 'PERPERIS_QA_GITHUB_APP_ID': '5711212',
                                      'PERPERIS_QA_GITHUB_INSTALLATION_ID': '180847731',
                                      'PERPERIS_QA_GITHUB_PRIVATE_KEY_FILE': str(path)})
        self.assertEqual(cfg.repo, 'krummb/kattens-pt')

    def test_errors_never_contain_the_key(self):
        try:
            gh.config_from_env({'PERPERIS_QA_TICKET_SOURCE': 'github', 'PERPERIS_QA_GITHUB_APP_ID': 'x',
                                'PERPERIS_QA_GITHUB_INSTALLATION_ID': '2', 'PERPERIS_QA_GITHUB_PRIVATE_KEY': PEM})
        except RuntimeError as exc:
            self.assertNotIn('PRIVATE KEY-----\n', str(exc))
            self.assertNotIn(PEM[40:80], str(exc))


class FakeGitHub:
    """Serves recorded responses; records every request for assertions."""

    def __init__(self, issues, events, milestones=MILESTONES, page_size=None):
        self.issues, self.events, self.milestones, self.page_size = issues, events, milestones, page_size
        self.requests = []
        self.token_calls = 0
        self.created = []
        self.patched = []
        self.comments = []
        self.thread = {}             # issue number -> list of comment dicts (as GitHub lists them)
        self.comment_edits = []
        self.reads_down = False      # simulate GitHub outages for reads
        self.use_etags = False       # answer If-None-Match with 304 like GitHub

    def respond(self, request, data, headers=None):
        headers = dict(headers or {})
        if self.use_etags:
            etag = '"%s"' % hash(json.dumps(data, sort_keys=True))
            headers['etag'] = etag
            if request.headers.get('if-none-match') == etag:
                return httpx.Response(304, headers=headers)
        return httpx.Response(200, json=data, headers=headers)

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        path = request.url.path
        if path == f'/app/installations/{CONFIG.installation_id}/access_tokens':
            self.token_calls += 1
            assert request.method == 'POST'
            token = request.headers['authorization'].removeprefix('Bearer ')
            claims = jwt.decode(token, PUBLIC, algorithms=['RS256'])
            assert claims['iss'] == CONFIG.app_id
            return httpx.Response(201, json={'token': 'ghs_test', 'expires_at': '2099-01-01T00:00:00Z'})
        assert request.headers['authorization'] == 'token ghs_test', 'reads must use the installation token'
        if self.reads_down and request.method == 'GET':
            return httpx.Response(503)
        if path == f'/repos/{REPO}/issues' and request.method == 'POST':
            payload = json.loads(request.content)
            self.created.append(payload)
            new = issue(100 + len(self.created), payload['title'], labels=payload['labels'], body=payload['body'],
                        milestone=next(m['title'] for m in self.milestones if m['number'] == payload['milestone']))
            self.issues = [new] + self.issues
            return httpx.Response(201, json=new)
        if path == f'/repos/{REPO}/issues':
            assert request.url.params.get('labels') == 'qa' or request.url.params.get('page')
            if self.page_size:
                page = int(request.url.params.get('page', '1'))
                chunk = self.issues[(page - 1) * self.page_size: page * self.page_size]
                headers = {}
                if page * self.page_size < len(self.issues):
                    headers['link'] = f'<https://api.github.com/repos/{REPO}/issues?page={page + 1}>; rel="next"'
                return self.respond(request, chunk, headers)
            return self.respond(request, self.issues)
        if path == f'/repos/{REPO}/milestones':
            return self.respond(request, self.milestones)
        if path.startswith(f'/repos/{REPO}/issues/') and path.endswith('/events'):
            number = int(path.split('/')[-2])
            return httpx.Response(200, json=self.events.get(number, []))
        if path.startswith(f'/repos/{REPO}/issues/comments/') and request.method == 'PATCH':
            comment_id = int(path.rsplit('/', 1)[-1])
            body = json.loads(request.content)['body']
            self.comment_edits.append((comment_id, body))
            for thread in self.thread.values():
                for comment in thread:
                    if comment['id'] == comment_id:
                        comment['body'] = body
            return httpx.Response(200, json={'id': comment_id, 'body': body})
        if path.startswith(f'/repos/{REPO}/issues/') and path.endswith('/comments') and request.method == 'GET':
            return httpx.Response(200, json=self.thread.get(int(path.split('/')[-2]), []))
        if path.startswith(f'/repos/{REPO}/issues/') and path.endswith('/comments') and request.method == 'POST':
            number, body = int(path.split('/')[-2]), json.loads(request.content)['body']
            self.comments.append((number, body))
            self.thread.setdefault(number, []).append({'id': 1000 + len(self.comments), 'body': body, 'user': {
                'login': 'perperis-qa[bot]', 'type': 'Bot'}, 'performed_via_github_app': {'id': int(CONFIG.app_id)}})
            return httpx.Response(201, json={'id': 1000 + len(self.comments)})
        if path.startswith(f'/repos/{REPO}/issues/') and path.rsplit('/', 1)[-1].isdigit():
            number = int(path.rsplit('/', 1)[-1])
            found = next((i for i in self.issues if i['number'] == number), None)
            if found is None:
                return httpx.Response(404, json={'message': 'Not Found'})
            if request.method == 'GET':
                return httpx.Response(200, json=found)
            if request.method == 'PATCH':
                patch = json.loads(request.content)
                self.patched.append((number, patch))
                found['labels'] = [{'name': n} for n in patch.get('labels', [l['name'] for l in found['labels']])]
                found['state'] = patch.get('state', found['state'])
                found['state_reason'] = patch.get('state_reason', found.get('state_reason'))
                found['updated_at'] = '2026-11-15T09:00:%02dZ' % len(self.patched)
                return httpx.Response(200, json=found)
        return httpx.Response(404, json={'message': 'Not Found'})


ISSUES = [
    issue(1, 'Critical crash', labels=['qa', 'bug', 'sev:critical']),
    issue(2, 'Fixed by PR', state='closed', state_reason='completed', labels=['qa', 'bug', 'sev:medium']),
    issue(3, 'Verified', state='closed', state_reason='completed',
          labels=['qa', 'bug', 'sev:low', 'status:deployed', 'status:verified']),
    issue(99, 'A pull request, not an issue', labels=['qa'], pr=True),
]
EVENTS = {
    2: [ev('closed', '2026-11-13T12:00:00Z', reason='completed')],
    3: [ev('closed', '2026-11-13T12:00:00Z', reason='completed', id_=1),
        ev('labeled', '2026-11-13T13:00:00Z', name='status:deployed', id_=2),
        ev('labeled', '2026-11-14T09:00:00Z', name='status:verified', id_=3)],
}


class Adapter(unittest.TestCase):
    def make(self, fake, clock=time.time):
        return gh.GitHubIssues(CONFIG, transport=httpx.MockTransport(fake), clock=clock)

    def test_reads_issues_with_app_token_and_skips_pull_requests(self):
        fake = FakeGitHub(ISSUES, EVENTS)
        adapter = self.make(fake)
        tickets = adapter.tickets()
        self.assertEqual([t['id'] for t in tickets], [1, 2, 3])
        self.assertEqual([t['status'] for t in tickets], ['reported', 'fixed', 'verified'])
        self.assertEqual(fake.token_calls, 1)
        self.assertNotIn('/repos/krummb/kattens-pt/issues/99/events', [r.url.path for r in fake.requests])

    def test_snapshot_is_cached_and_token_reused(self):
        now = [time.time()]
        fake = FakeGitHub(ISSUES, EVENTS)
        adapter = self.make(fake, clock=lambda: now[0])
        adapter.tickets()
        calls = len(fake.requests)
        adapter.tickets()
        self.assertEqual(len(fake.requests), calls, 'second read within the cache window hits GitHub again')
        now[0] += 120
        adapter.tickets()
        self.assertGreater(len(fake.requests), calls)
        self.assertEqual(fake.token_calls, 1)

    def test_follows_pagination(self):
        many = [issue(n, f'Bug {n}', labels=['qa']) for n in range(1, 8)]
        fake = FakeGitHub(many, {}, page_size=3)
        self.assertEqual(len(self.make(fake).tickets()), 7)

    def test_github_errors_become_unavailable_without_details(self):
        def broken(request):
            if request.url.path.endswith('/access_tokens'):
                return httpx.Response(401, json={'message': 'A JSON web token could not be decoded'})
            return httpx.Response(500)
        with self.assertRaises(gh.GitHubUnavailable) as caught:
            self.make(broken).tickets()
        self.assertNotIn('ghs_', str(caught.exception))

    def test_history_for_known_and_unknown_issue(self):
        adapter = self.make(FakeGitHub(ISSUES, EVENTS))
        self.assertEqual(adapter.history(3)[-1]['new_status'], 'verified')
        self.assertIsNone(adapter.history(404))


class Reporting(unittest.TestCase):
    def make(self, fake):
        return gh.GitHubIssues(CONFIG, transport=httpx.MockTransport(fake))

    def report(self, adapter, **overrides):
        values = dict(title='Picker  stuck on  phone', description='Steps: open @Feniger library\n\nReported by Mallory via PERPERIS QA',
                      severity='high', category='Mobile', testing_round='R05', area='Training mode',
                      environment='iPhone 15', deploy_version='Deploy #24', reporter='Anna Tester')
        values.update(overrides)
        return adapter.create_issue(**values)

    def test_creates_issue_with_template_labels_and_round(self):
        fake = FakeGitHub(list(ISSUES), EVENTS)
        ticket = self.report(self.make(fake))
        sent = fake.created[0]
        self.assertEqual(sent['title'], 'Picker stuck on phone')
        self.assertEqual(sent['labels'], ['qa', 'bug', 'sev:high', 'area:training-mode'])
        self.assertEqual(sent['milestone'], 1)
        body = sent['body']
        self.assertTrue(body.rstrip().endswith('Reported by Anna Tester via PERPERIS QA'))
        self.assertIn('### Environment\niPhone 15', body)
        self.assertNotIn('@Feniger', body, 'mentions must not ping anyone')
        self.assertEqual(ticket['reporter'], 'Anna Tester', 'a spoofed reporter line in the text must not win')
        self.assertEqual(ticket['status'], 'reported')
        self.assertEqual(ticket['area'], 'training-mode')
        self.assertEqual(ticket['environment'], 'iPhone 15')
        self.assertEqual(ticket['deploy_version'], 'Deploy #24')
        self.assertTrue(ticket['description'].startswith('Steps: open'))
        self.assertNotIn('### Environment', ticket['description'])

    def test_unknown_area_goes_to_body_and_empty_fields_stay_empty(self):
        fake = FakeGitHub(list(ISSUES), EVENTS)
        ticket = self.report(self.make(fake), area='Journal', environment='', deploy_version='')
        self.assertEqual(fake.created[0]['labels'], ['qa', 'bug', 'sev:high'])
        self.assertIn('### Area\nJournal', fake.created[0]['body'])
        self.assertEqual(ticket['environment'], '')
        self.assertEqual(ticket['deploy_version'], '')

    def test_round_must_be_an_open_round_milestone(self):
        fake = FakeGitHub(list(ISSUES), EVENTS)
        adapter = self.make(fake)
        for bad in ('R04', 'R99', 'Backlog'):
            with self.assertRaises(gh.InvalidReport) as caught:
                self.report(adapter, testing_round=bad)
            self.assertIn('R05, R06', str(caught.exception))
        self.assertEqual(fake.created, [])
        self.assertEqual(adapter.open_rounds(), ['R05', 'R06'])

    def test_new_issue_shows_up_without_waiting_for_the_cache(self):
        fake = FakeGitHub(list(ISSUES), EVENTS)
        adapter = self.make(fake)
        before = len(adapter.tickets())
        self.report(adapter)
        self.assertEqual(len(adapter.tickets()), before + 1)


class Caching(unittest.TestCase):
    """Review note 2: rate limits, conditional requests and outages."""

    def setUp(self):
        self.now = [1_000.0]
        self.fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        self.adapter = gh.GitHubIssues(CONFIG, transport=httpx.MockTransport(self.fake), clock=lambda: self.now[0],
                                       cache_seconds=60, backoff_seconds=60)
        self.fake_time = mock.patch.object(gh.GitHubIssues, '_app_jwt', return_value=jwt.encode(
            {'iat': int(time.time()) - 60, 'exp': int(time.time()) + 540, 'iss': CONFIG.app_id}, PEM, algorithm='RS256'))
        self.fake_time.start()
        self.addCleanup(self.fake_time.stop)

    def event_reads(self):
        return [r.url.path for r in self.fake.requests if r.url.path.endswith('/events')]

    def test_events_are_refetched_only_for_issues_whose_updated_at_changed(self):
        self.adapter.tickets()
        self.assertEqual(len(self.event_reads()), 3)
        self.now[0] += 120
        self.adapter.tickets()
        self.assertEqual(len(self.event_reads()), 3, 'unchanged issues must reuse their cached events')
        self.fake.issues[1]['updated_at'] = '2026-11-14T08:00:00Z'  # issue #2 labelled/closed/reopened
        self.now[0] += 120
        self.adapter.tickets()
        self.assertEqual(self.event_reads()[3:], [f'/repos/{REPO}/issues/2/events'])

    def test_unchanged_lists_are_conditional_requests(self):
        self.fake.use_etags = True
        first = self.adapter.tickets()
        self.now[0] += 120
        before = len(self.fake.requests)
        second = self.adapter.tickets()
        later = self.fake.requests[before:]
        self.assertTrue(later and all(r.headers.get('if-none-match') for r in later))
        self.assertEqual(second, first)

    def test_outage_serves_last_good_data_marked_stale_with_back_off(self):
        good = self.adapter.tickets()
        self.assertFalse(self.adapter.freshness()['stale'])
        self.fake.reads_down = True
        self.now[0] += 120
        self.assertEqual(self.adapter.tickets(), good)
        self.assertTrue(self.adapter.freshness()['stale'])
        calls = len(self.fake.requests)
        self.adapter.tickets()
        self.adapter.freshness()
        self.assertEqual(len(self.fake.requests), calls, 'no retries during the back-off')
        self.now[0] += 61
        self.adapter.tickets()
        self.assertGreater(len(self.fake.requests), calls, 'retries after the back-off')
        self.fake.reads_down = False
        self.now[0] += 61
        self.adapter.tickets()
        self.assertFalse(self.adapter.freshness()['stale'])

    def test_first_load_failure_raises_without_hammering_github(self):
        self.fake.reads_down = True
        with self.assertRaises(gh.GitHubUnavailable):
            self.adapter.tickets()
        calls = len(self.fake.requests)
        with self.assertRaises(gh.GitHubUnavailable):
            self.adapter.tickets()
        self.assertEqual(len(self.fake.requests), calls)

    def test_created_issue_is_returned_even_if_the_refresh_then_fails(self):
        self.adapter.tickets()
        self.fake.reads_down = True
        ticket = self.adapter.create_issue(title='Created during an outage', description='', severity='low',
                                           category='Other', testing_round='R05', area='', environment='',
                                           deploy_version='', reporter='Anna')
        self.assertEqual(ticket['reporter'], 'Anna')
        self.assertEqual(len(self.fake.created), 1)


class ScreenshotComment(unittest.TestCase):
    """Decision 4.1: one bot comment per issue, edited rather than reposted."""

    def make(self, fake):
        return gh.GitHubIssues(CONFIG, transport=httpx.MockTransport(fake))

    def test_posts_once_then_edits_and_ignores_other_comments(self):
        fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        fake.thread[1] = [
            {'id': 1, 'body': gh.SCREENSHOT_MARKER + ' copied by a person', 'user': {'login': 'mallory', 'type': 'User'}},
            {'id': 2, 'body': gh.SCREENSHOT_MARKER + ' another app', 'user': {'login': 'x[bot]', 'type': 'Bot'},
             'performed_via_github_app': {'id': 1}}]
        adapter = self.make(fake)
        link = 'https://qa.example.test/#ticket-1'
        self.assertEqual(adapter.upsert_screenshot_comment(1, 2, link), 'posted')
        self.assertEqual(fake.comments, [(1, gh.SCREENSHOT_MARKER + '\n📎 2 screenshots in PERPERIS QA: ' + link)])
        self.assertEqual(adapter.upsert_screenshot_comment(1, 1, link), 'edited')
        self.assertEqual(adapter.upsert_screenshot_comment(1, 1, link), 'none')     # unchanged: no write
        self.assertEqual(adapter.upsert_screenshot_comment(1, 0, link), 'edited')
        self.assertEqual(len(fake.comments), 1)
        self.assertEqual([body.splitlines()[1] for _, body in fake.comment_edits],
                         ['📎 1 screenshot in PERPERIS QA: ' + link, '📎 No screenshots in PERPERIS QA any more: ' + link])
        self.assertEqual([c['body'] for c in fake.thread[1][:2]],
                         [gh.SCREENSHOT_MARKER + ' copied by a person', gh.SCREENSHOT_MARKER + ' another app'])

    def test_nothing_is_posted_for_zero_and_errors_surface(self):
        fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        adapter = self.make(fake)
        self.assertEqual(adapter.upsert_screenshot_comment(1, 0, 'https://qa.example.test/#ticket-1'), 'none')
        self.assertEqual(fake.comments, [])
        fake.reads_down = True
        with self.assertRaises(gh.GitHubUnavailable):
            adapter.upsert_screenshot_comment(1, 1, 'https://qa.example.test/#ticket-1')

    def test_memory_stand_in_bumps_updated_at(self):
        memory = gh.MemoryIssues()
        ticket = memory.create_issue(title='Memory', description='Expected x, saw y', severity='low', category='Other',
                                     testing_round='R01', area='', environment='local', deploy_version='', reporter='T')
        before = memory.tickets()[0]['updated_at']
        self.assertEqual(memory.upsert_screenshot_comment(ticket['id'], 1, 'http://x/#ticket-1'), 'posted')
        self.assertNotEqual(memory.tickets()[0]['updated_at'], before)
        self.assertEqual(memory.upsert_screenshot_comment(ticket['id'], 1, 'http://x/#ticket-1'), 'none')


class TicketLookup(unittest.TestCase):
    """A ticket created moments ago must be found even when the cached list does not have it yet."""

    def make(self, fake):
        return gh.GitHubIssues(CONFIG, transport=httpx.MockTransport(fake))

    def test_unknown_numbers_are_looked_up_directly(self):
        fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        adapter = self.make(fake)
        self.assertTrue(adapter.has_ticket(1))                      # in the cached list
        fake.issues = [issue(7, 'Just created', labels=['qa', 'bug', 'sev:low']),
                       issue(8, 'Not a QA ticket', labels=['bug'])] + fake.issues
        self.assertFalse(any(t['id'] == 7 for t in adapter.tickets()))   # the cache is still the old snapshot
        self.assertFalse(adapter.has_ticket(8))                     # looked up, but it has no `qa` label
        self.assertFalse(adapter.has_ticket(99))                    # a pull request
        self.assertFalse(adapter.has_ticket(12345))                 # does not exist
        self.assertTrue(adapter.has_ticket(7))                      # found by asking GitHub for that issue
        self.assertTrue(any(t['id'] == 7 for t in adapter.tickets()))    # and the list refreshes afterwards

    def test_github_errors_are_not_reported_as_missing(self):
        fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        adapter = self.make(fake)
        adapter.snapshot()
        fake.reads_down = True
        with self.assertRaises(gh.GitHubUnavailable):
            adapter.has_ticket(555)

    def test_memory_stand_in(self):
        memory = gh.MemoryIssues()
        ticket = memory.create_issue(title='Memory', description='Expected x, saw y', severity='low', category='Other',
                                     testing_round='R01', area='', environment='local', deploy_version='', reporter='T')
        self.assertTrue(memory.has_ticket(ticket['id']))
        self.assertFalse(memory.has_ticket(ticket['id'] + 50))


class ReportKinds(unittest.TestCase):
    """A report is a bug, a request (change or feature) or a question; the label decides how it is handled."""

    def payload(self, **extra):
        return gh.report_payload(milestone=1, title='A title', description='Expected x, saw y', severity='low',
                                 category='Other', area='', environment='production', deploy_version='',
                                 reporter='Tester', **extra)

    def test_labels_follow_the_type(self):
        self.assertEqual(self.payload()['labels'][:2], ['qa', 'bug'])
        self.assertEqual(self.payload(kind='request')['labels'][:2], ['qa', 'task'])
        self.assertEqual(self.payload(kind='question')['labels'][:2], ['qa', 'question'])
        with self.assertRaises(gh.InvalidReport):
            self.payload(kind='feature')

    def test_ticket_category_follows_the_label(self):
        for labels, expected in ((['qa', 'task'], 'task'), (['qa', 'question'], 'question'), (['qa', 'bug'], 'bug')):
            self.assertEqual(gh.to_ticket(issue(5, 'x', labels=labels), [], ROUNDS)['category'], expected)


class Conversation(unittest.TestCase):
    """Comments shown in QA, plain comments without a status change, and the Admin-only Go."""

    def make(self, fake):
        return gh.GitHubIssues(CONFIG, transport=httpx.MockTransport(fake))

    def test_view_hides_markers_and_the_screenshot_comment(self):
        self.assertIsNone(gh.comment_view({'body': gh.SCREENSHOT_MARKER + '\n📎 1 screenshot'}))
        self.assertIsNone(gh.comment_view({'body': '<!-- qa-autopilot:triaged -->'}))
        view = gh.comment_view({'id': 1, 'body': '<!-- hidden -->\nHello', 'user': {'login': 'krummb', 'type': 'User'},
                                'created_at': '2026-11-12T09:00:00Z'})
        self.assertEqual((view['author'], view['body'], view['bot']), ('krummb', 'Hello', False))

    def test_list_and_post(self):
        fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        fake.thread[1] = [
            {'id': 1, 'body': 'Please check', 'user': {'login': 'krummb', 'type': 'User'}, 'created_at': '2026-11-12T09:00:00Z'},
            {'id': 2, 'body': gh.SCREENSHOT_MARKER + '\nx', 'user': {'login': 'perperis-qa[bot]', 'type': 'Bot'}}]
        adapter = self.make(fake)
        self.assertEqual([c['body'] for c in adapter.comment_list(1)], ['Please check'])
        adapter.add_comment(1, 'Robert', 'Looks good')
        self.assertEqual(fake.comments[-1], (1, '**Comment** by Robert via PERPERIS QA\n\nLooks good'))
        adapter.add_comment(1, 'Robert', approve=True)
        self.assertEqual(fake.comments[-1], (1, '**Go: approved** by Robert via PERPERIS QA'))

    def test_memory_stand_in(self):
        memory = gh.MemoryIssues()
        ticket = memory.create_issue(title='Memory', description='Expected x, saw y', severity='low', category='Other',
                                     testing_round='R01', area='', environment='local', deploy_version='', reporter='T',
                                     kind='request')
        self.assertEqual(memory.tickets()[0]['category'], 'task')
        before = memory.tickets()[0]['updated_at']
        memory.add_comment(ticket['id'], 'Erik', 'hello')
        memory.add_comment(ticket['id'], 'Erik', approve=True)
        self.assertEqual([c['body'].splitlines()[0] for c in memory.comment_list(ticket['id'])],
                         ['**Comment** by Erik via PERPERIS QA', '**Go: approved** by Erik via PERPERIS QA'])
        self.assertNotEqual(memory.tickets()[0]['updated_at'], before)


class StatusChanges(unittest.TestCase):
    """Milestone 3: Triage / Verify / Reopen written to GitHub."""

    def labels(self, patch):
        return patch['labels']

    def test_status_patch_keeps_one_status_label(self):
        base = issue(1, 'X', labels=['qa', 'bug', 'sev:high', 'status:fixed', 'status:deployed'])
        self.assertEqual(gh.status_patch(base, 'verified'),
                         {'state': 'closed', 'state_reason': 'completed', 'labels': ['qa', 'bug', 'sev:high', 'status:verified']})
        self.assertEqual(gh.status_patch(base, 'reopened'), {'state': 'open', 'labels': ['qa', 'bug', 'sev:high']})
        self.assertEqual(gh.status_patch(base, 'closed'),
                         {'state': 'closed', 'state_reason': 'not_planned', 'labels': ['qa', 'bug', 'sev:high']})
        self.assertEqual(gh.status_patch(base, 'fixed'), {'labels': ['qa', 'bug', 'sev:high', 'status:fixed']})
        self.assertEqual(gh.status_patch(base, 'in_progress'),
                         {'state': 'open', 'labels': ['qa', 'bug', 'sev:high', 'status:in-progress']})
        with self.assertRaises(gh.InvalidReport):
            gh.status_patch(base, 'reported')

    def make(self, fake):
        return gh.GitHubIssues(CONFIG, transport=httpx.MockTransport(fake))

    def test_verify_closes_the_issue_and_comments_who_did_it(self):
        fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        adapter = self.make(fake)
        ticket = adapter.change_status(2, 'verified', actor='Anna Tester', comment='Checked on @phone',
                                       expected_updated_at='2026-11-12T10:00:00Z')
        self.assertEqual(fake.patched, [(2, {'state': 'closed', 'state_reason': 'completed',
                                             'labels': ['qa', 'bug', 'sev:medium', 'status:verified']})])
        self.assertEqual(fake.comments, [(2, '**Verified** by Anna Tester via PERPERIS QA\n\nChecked on @\u200bphone')])
        self.assertEqual(ticket['status'], 'verified')

    def test_reopen_removes_old_status_labels(self):
        fake = FakeGitHub([dict(i) for i in ISSUES], EVENTS)
        self.make(fake).change_status(3, 'reopened', actor='Creator')
        self.assertEqual(fake.patched, [(3, {'state': 'open', 'labels': ['qa', 'bug', 'sev:low']})])

    def test_conflicts_and_non_qa_issues_are_refused_without_changes(self):
        fake = FakeGitHub([dict(i) for i in ISSUES] + [issue(7, 'Not a QA issue', labels=['bug'])], EVENTS)
        adapter = self.make(fake)
        with self.assertRaises(gh.TicketConflict):
            adapter.change_status(1, 'triaged', actor='X', expected_updated_at='2020-01-01T00:00:00Z')
        for number in (7, 404, 99):  # not labelled qa, missing, a pull request
            with self.assertRaises(gh.TicketNotFound):
                adapter.change_status(number, 'triaged', actor='X')
        self.assertEqual((fake.patched, fake.comments), ([], []))


class StubAdapter(gh.GitHubIssues):  # counts as the GitHub source; no HTTP client
    def __init__(self, tickets):
        self._tickets = tickets
        self.config = CONFIG

    def tickets(self):
        return self._tickets

    def history(self, number):
        return [{'old_status': None, 'new_status': 'reported', 'actor': 'krummb', 'created_at': 'x'}] if number == 1 else None

    def close(self):
        pass

    def open_rounds(self):
        return ['R05']

    def freshness(self):
        return {'stale': True, 'as_of': '2026-11-11T10:00:00+00:00'}

    def change_status(self, number, status, *, actor, comment='', expected_updated_at=None):
        self.changed = (number, status, actor, comment)
        return {'id': number, 'status': status}

    def create_issue(self, **values):
        if values['testing_round'] != 'R05':
            raise gh.InvalidReport('Round is not open (open: R05)')
        self.created = values
        return {'id': 101, 'title': values['title'], 'status': 'reported', 'reporter': values['reporter']}


class AppWiring(unittest.TestCase):
    """The existing API shape serves GitHub-derived tickets; writes are refused (read-only milestone)."""

    def test_api_in_github_mode(self):
        import app as qa_app
        from fastapi.testclient import TestClient
        tickets = [gh.to_ticket(i, EVENTS.get(i['number'], []), ROUNDS) for i in ISSUES if 'pull_request' not in i]
        env = {'PERPERIS_QA_ENV': 'local', 'PERPERIS_QA_AUTH_MODE': 'local_shared',
               'PERPERIS_QA_TICKET_SOURCE': 'memory'}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(qa_app, 'ADMIN_PASSWORD', 'wiring-password-long'), \
                mock.patch.object(qa_app, 'SECRET', 's' * 40), \
                TestClient(qa_app.APP) as client:
            qa_app.APP.state.tickets = StubAdapter(tickets)
            try:
                self.assertEqual(client.post('/api/login', json={'password': 'wiring-password-long'}).status_code, 200)
                config = client.get('/api/auth/config').json()
                self.assertEqual(config['tickets_source'], 'github')
                self.assertEqual(config['tickets_repo'], REPO)
                self.assertFalse(config['attachments_enabled'])
                listed = client.get('/api/tickets').json()['tickets']
                self.assertEqual([t['id'] for t in listed], [1, 2, 3])
                a = client.get('/api/analytics').json()
                self.assertEqual((a['total'], a['open'], a['fixed'], a['verified'], a['critical']), (3, 2, 2, 1, 1))
                self.assertEqual(a['backlog'], 2)
                headers = {'X-QA-Request': '1'}
                body = {'title': 'New bug', 'reporter': 'X', 'testing_round': 'R05', 'description': 'Expected it to work, it did not'}
                self.assertEqual(a['current_round'], 'R05')
                self.assertEqual((a['data_stale'], a['data_as_of']), (True, '2026-11-11T10:00:00+00:00'))
                created = client.post('/api/tickets', headers=headers, json=body)
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(created.json()['ticket']['id'], 101)
                self.assertEqual(qa_app.APP.state.tickets.created['reporter'], 'X')
                self.assertEqual(qa_app.APP.state.tickets.created['category'], 'UI / UX')
                wrong = client.post('/api/tickets', headers=headers, json={**body, 'testing_round': 'R09'})
                self.assertEqual(wrong.status_code, 422)
                self.assertIn('R05', wrong.json()['detail'])
                changed = client.patch('/api/tickets/1', headers=headers, json={'status': 'triaged', 'comment': 'ok'})
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(qa_app.APP.state.tickets.changed, (1, 'triaged', 'Admin', 'ok'))
                self.assertEqual(client.get('/api/tickets/1/history').status_code, 200)
                self.assertEqual(client.get('/api/tickets/2/history').status_code, 404)
                self.assertEqual(client.get('/api/tickets/1/attachments').status_code, 404)
            finally:
                qa_app.APP.state.tickets = None


if __name__ == '__main__':
    unittest.main()
