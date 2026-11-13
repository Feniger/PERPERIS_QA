"""UI previews of the training app's pull requests (kattens-pt DEC-526).

The app's CI photographs the main screens with sample data and uploads them as the
`ui-previews` artifact (PNG files plus manifest.json) for every pull request and every push to
main. QA lists the open pull requests, fetches a set the first time someone opens it, keeps it in
QA's private bucket under `previews/<commit sha>/`, and streams the images to signed-in members.
"Before" is the latest set from main, "after" is the pull request's newest commit.

- Only sets that belong to an open pull request's head commit or to the latest main commit are
  fetched or served; nothing else can be requested.
- Stored sets that are no longer referenced (merged or closed PRs, older main) are deleted on a
  later listing; the bucket's 365-day lifecycle rule is the backstop.
- Archives are checked before anything is stored: names on an allowlist, sizes capped, PNGs
  verified.
"""
from __future__ import annotations

from io import BytesIO
import json
import re
import threading
import time
import zipfile

import httpx
from PIL import Image

ARTIFACT = 'ui-previews'
PREFIX = 'previews/'
SHA = re.compile(r'^[0-9a-f]{40}$')
IMAGE = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}-(light|dark)\.png$')
BRANCH_ISSUE = re.compile(r'^qa-(?:fix|feature)/(\d{1,7})-')
MAX_ARCHIVE = 80 * 1024 * 1024
MAX_FILE = 10 * 1024 * 1024
MAX_FILES = 80
MAX_SIDE = 16384
LIST_SECONDS = 60
CLEANUP_SECONDS = 600


class PreviewsUnavailable(RuntimeError):
    """GitHub or storage could not be reached; the message is safe to show."""


class PreviewNotFound(LookupError):
    pass


class GitHubPreviewSource:
    """Reads pull requests and CI artifacts with QA's GitHub App (needs Actions: read)."""

    def __init__(self, github):
        self.github = github
        self.repo = github.config.repo

    def _get(self, path: str, params: dict | None = None):
        headers = {'Authorization': f'token {self.github._installation_token()}'}
        try:
            response = self.github._client.get(path, params=params, headers=headers)
        except httpx.HTTPError:
            raise PreviewsUnavailable('GitHub could not be reached') from None
        if response.status_code == 403:
            raise PreviewsUnavailable('QA cannot read CI results yet (the GitHub App needs Actions: read)')
        if response.status_code != 200:
            raise PreviewsUnavailable(f'GitHub read failed ({response.status_code})')
        return response.json()

    def open_pulls(self) -> list[dict]:
        return self._get(f'/repos/{self.repo}/pulls', {'state': 'open', 'per_page': 50})

    def artifacts(self) -> list[dict]:
        data = self._get(f'/repos/{self.repo}/actions/artifacts', {'name': ARTIFACT, 'per_page': 100})
        return data.get('artifacts', [])

    def download(self, artifact_id: int) -> bytes:
        headers = {'Authorization': f'token {self.github._installation_token()}'}
        try:
            # GitHub answers with a redirect to short-lived blob storage; httpx drops the
            # Authorization header when the redirect leaves api.github.com.
            with self.github._client.stream('GET', f'/repos/{self.repo}/actions/artifacts/{artifact_id}/zip',
                                            headers=headers, follow_redirects=True, timeout=60) as response:
                if response.status_code != 200:
                    raise PreviewsUnavailable(f'Preview download failed ({response.status_code})')
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise PreviewsUnavailable('Preview archive is too large')
                    chunks.append(chunk)
        except httpx.HTTPError:
            raise PreviewsUnavailable('GitHub could not be reached') from None
        return b''.join(chunks)


def unpack(archive: bytes) -> tuple[dict, dict[str, bytes]]:
    """Validated (manifest, {file name: png bytes}) from an artifact zip. Raises ValueError."""
    try:
        bundle = zipfile.ZipFile(BytesIO(archive))
    except zipfile.BadZipFile:
        raise ValueError('not a zip archive') from None
    entries = [i for i in bundle.infolist() if not i.is_dir()]
    if len(entries) > MAX_FILES:
        raise ValueError('too many files')
    manifest, images = None, {}
    for info in entries:
        name = info.filename
        if info.file_size > MAX_FILE:
            raise ValueError(f'{name} is too large')
        if name == 'manifest.json':
            manifest = json.loads(bundle.read(info).decode('utf-8'))
        elif IMAGE.match(name):
            data = bundle.read(info)
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError(f'{name} is not a PNG')
            with Image.open(BytesIO(data), formats=('PNG',)) as probe:
                width, height = probe.size
                if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
                    raise ValueError(f'{name} has an unexpected size')
                probe.verify()
            images[name] = data
        # Anything else in the archive is ignored, never stored.
    if not isinstance(manifest, dict):
        raise ValueError('manifest.json is missing')
    screens = []
    for screen in manifest.get('screens') or []:
        if not isinstance(screen, dict):
            continue
        file = str(screen.get('file', ''))
        if file in images and screen.get('theme') in ('light', 'dark'):
            screens.append({'key': str(screen.get('key', ''))[:40], 'label': str(screen.get('label', ''))[:60],
                            'theme': screen['theme'], 'file': file})
    clean = {'sha': str(manifest.get('sha', ''))[:40], 'pr': manifest.get('pr') if isinstance(manifest.get('pr'), int) else None,
             'created_at': str(manifest.get('created_at', ''))[:40], 'screens': screens,
             'notes': [str(n)[:200] for n in (manifest.get('notes') or [])][:40]}
    return clean, images


class Previews:
    def __init__(self, source, store, clock=time.time):
        self.source = source
        self.store = store
        self.clock = clock
        self._lock = threading.Lock()
        self._fetching: dict[str, threading.Lock] = {}
        self._listing: tuple[float, dict] | None = None
        self._cleaned_at = 0.0
        self._allowed: dict[str, int] = {}

    # ---------- listing ----------
    def listing(self) -> dict:
        with self._lock:
            if self._listing and self.clock() - self._listing[0] < LIST_SECONDS:
                return self._listing[1]
        pulls = self.source.open_pulls()
        artifacts = [a for a in self.source.artifacts() if not a.get('expired')]
        newest: dict[str, dict] = {}
        for artifact in sorted(artifacts, key=lambda a: a.get('created_at') or ''):
            sha = (artifact.get('workflow_run') or {}).get('head_sha') or ''
            if SHA.match(sha):
                newest[sha] = artifact
        main = next((a for a in sorted(artifacts, key=lambda a: a.get('created_at') or '', reverse=True)
                     if (a.get('workflow_run') or {}).get('head_branch') == 'main'), None)
        data = {
            'main': ({'sha': main['workflow_run']['head_sha'], 'created_at': main.get('created_at'), 'ready': True}
                     if main else None),
            'pulls': [],
        }
        for pull in pulls:
            head = pull.get('head') or {}
            sha = head.get('sha') or ''
            branch = head.get('ref') or ''
            issue = BRANCH_ISSUE.match(branch)
            data['pulls'].append({
                'number': pull.get('number'), 'title': pull.get('title') or '', 'branch': branch,
                'issue': int(issue.group(1)) if issue else None, 'sha': sha,
                'draft': bool(pull.get('draft')), 'url': pull.get('html_url') or '',
                'updated_at': pull.get('updated_at'), 'ready': sha in newest,
            })
        allowed = {p['sha']: newest[p['sha']]['id'] for p in data['pulls'] if p['ready']}
        if main:
            allowed[main['workflow_run']['head_sha']] = main['id']
        with self._lock:
            self._listing = (self.clock(), data)
            self._allowed = allowed
        self._cleanup(set(allowed))
        return data

    def _artifact_for(self, sha: str) -> int:
        if not SHA.match(sha):
            raise PreviewNotFound(sha)
        self.listing()
        artifact_id = self._allowed.get(sha)
        if artifact_id is None:
            raise PreviewNotFound(sha)
        return artifact_id

    # ---------- one set ----------
    def manifest(self, sha: str) -> dict:
        artifact_id = self._artifact_for(sha)
        stored = self._stored_manifest(sha)
        if stored is not None:
            return stored
        with self._lock:
            gate = self._fetching.setdefault(sha, threading.Lock())
        with gate:
            stored = self._stored_manifest(sha)
            if stored is not None:
                return stored
            try:
                manifest, images = unpack(self.source.download(artifact_id))
            except ValueError as error:
                raise PreviewsUnavailable(f'The preview set could not be read ({error})') from None
            for name, data in images.items():
                self._put(f'{PREFIX}{sha}/{name}', data, 'image/png')
            # The manifest goes last: its presence means the whole set is stored.
            self._put(f'{PREFIX}{sha}/manifest.json', json.dumps(manifest).encode(), 'application/json')
            return manifest

    def image(self, sha: str, name: str) -> bytes:
        if not IMAGE.match(name):
            raise PreviewNotFound(name)
        self._artifact_for(sha)
        if self._stored_manifest(sha) is None:
            self.manifest(sha)
        try:
            data, _, _ = self.store.get(f'{PREFIX}{sha}/{name}')
        except LookupError:
            raise PreviewNotFound(name) from None
        return data

    # ---------- storage ----------
    def _stored_manifest(self, sha: str) -> dict | None:
        try:
            data, _, _ = self.store.get(f'{PREFIX}{sha}/manifest.json')
        except LookupError:
            return None
        return json.loads(data.decode('utf-8'))

    def _put(self, name: str, data: bytes, content_type: str):
        try:
            self.store.put(name, data, content_type, {'source': 'ui-previews'})
        except FileExistsError:
            pass   # an earlier, interrupted fetch stored it already; content is per commit

    def _cleanup(self, keep: set[str]):
        if self.clock() - self._cleaned_at < CLEANUP_SECONDS:
            return
        self._cleaned_at = self.clock()
        try:
            for obj in self.store.list(PREFIX):
                parts = obj.name.split('/')
                if len(parts) == 3 and parts[1] not in keep:
                    self.store.delete(obj.name)
        except Exception:
            pass   # best effort; the lifecycle rule removes anything left behind
