"""Screenshots for QA tickets: private bucket objects, no database (decision 4.1).

- Objects are `issues/<issue number>/<random id>.<png|webp>` in QA's private
  bucket. Custom metadata: uploader (identity subject, never an email),
  uploader_name, sha256, uploaded_at (epoch seconds). Listing the prefix is the
  record; the per-issue cap is counted from it.
- Images are validated, re-encoded (metadata stripped) and downscaled to at most
  2000 px on the long side. PNG stays PNG (screenshots); JPEG and WebP become
  WebP (photos).
- Images are only ever streamed through QA; there are no public or signed URLs.
- Retention: Verify sets each object's customTime and a bucket lifecycle rule
  deletes 30 days later; a second rule deletes after 365 days. Because a
  rewrite (Reopen) restarts the object's age, QA also deletes objects whose
  uploaded_at is older than 365 days whenever it lists an issue.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
import re
import secrets
import threading
import time
import warnings

from PIL import Image, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 25_000_000      # decoded pixels after JPEG draft scaling; bounds memory per decode
LONG_SIDE = 2000             # stored images are downscaled to this
THUMB_SIDE = 480
MAX_PER_ISSUE = 10
RETENTION_SECONDS = 365 * 86400
FORMATS = ('PNG', 'JPEG', 'WEBP')
CONTENT_TYPES = {'png': 'image/png', 'webp': 'image/webp'}
NAME = re.compile(r'^[a-f0-9]{32}\.(png|webp)$')
UPLOAD_WINDOW = 600
UPLOAD_LIMIT = 30            # per member per instance per window

# Decoding is memory-heavy; at most two at a time per instance (Cloud Run: 512 MiB).
_decode_slots = threading.BoundedSemaphore(2)


class InvalidImage(ValueError):
    """Not an acceptable image. The message is safe to show."""


class TooLarge(InvalidImage):
    pass


class LimitReached(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class StorageUnavailable(RuntimeError):
    pass


def normalize_image(content: bytes) -> tuple[bytes, str]:
    """Validate, strip metadata and downscale. Returns (bytes, 'png' | 'webp')."""
    if not content or len(content) > MAX_BYTES:
        raise TooLarge('A screenshot must be at most 10 MB')
    with _decode_slots:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(content), formats=FORMATS) as probe:
                    if probe.format not in FORMATS or getattr(probe, 'n_frames', 1) != 1:
                        raise InvalidImage('Unsupported image')
                    width, height = probe.size
                    if width < 1 or height < 1 or width > MAX_SIDE or height > MAX_SIDE:
                        raise InvalidImage('Image dimensions too large')
                    probe.verify()
                with Image.open(BytesIO(content), formats=FORMATS) as original:
                    source_format = original.format
                    if source_format == 'JPEG':
                        # Decode a reduced JPEG directly (1/2, 1/4, 1/8) when it is much larger than needed.
                        original.draft('RGB', (LONG_SIDE, LONG_SIDE))
                    if original.size[0] * original.size[1] > MAX_PIXELS:
                        raise InvalidImage('Image dimensions too large')
                    alpha = 'A' in original.getbands() or 'transparency' in original.info
                    with closing(original.convert('RGBA' if alpha else 'RGB')) as converted:
                        converted.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
                        # A fresh image carries no EXIF, ICC profile, text chunks or comments.
                        with Image.new(converted.mode, converted.size) as clean:
                            clean.paste(converted)
                            output = BytesIO()
                            if source_format == 'PNG':
                                clean.save(output, format='PNG', optimize=True)
                                extension = 'png'
                            else:
                                clean.save(output, format='WEBP', quality=85, method=4)
                                extension = 'webp'
        except InvalidImage:
            raise
        except (ValueError, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError,
                Image.DecompressionBombWarning):
            raise InvalidImage('Use a single-frame PNG, JPEG or WebP image') from None
    data = output.getvalue()
    if len(data) > MAX_BYTES:
        raise TooLarge('The processed screenshot is larger than 10 MB')
    return data, extension


def thumbnail(content: bytes) -> bytes:
    """A small WebP preview of a stored (already validated) image."""
    with _decode_slots:
        with Image.open(BytesIO(content), formats=('PNG', 'WEBP')) as image:
            image.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format='WEBP', quality=75)
    return output.getvalue()


def prefix(issue: int) -> str:
    if not isinstance(issue, int) or isinstance(issue, bool) or issue < 1:
        raise NotFound('Ticket not found')
    return f'issues/{issue}/'


def object_name(issue: int, name: str) -> str:
    if not NAME.fullmatch(name or ''):
        raise NotFound('Screenshot not found')
    return prefix(issue) + name


@dataclass(frozen=True)
class StoredObject:
    name: str                 # full object name
    generation: int
    size: int
    metadata: dict


class MemoryStore:
    """Stand-in for local development and tests. Same contract as BucketStore."""

    def __init__(self):
        self.objects: dict[str, tuple[int, bytes, str, dict]] = {}
        self.custom_times: dict[str, float] = {}
        self._generation = 0
        self._lock = threading.Lock()

    def put(self, name, data, content_type, metadata):
        with self._lock:
            if name in self.objects:      # if_generation_match=0
                raise FileExistsError(name)
            self._generation += 1
            self.objects[name] = (self._generation, data, content_type, dict(metadata))

    def list(self, prefix_):
        with self._lock:
            return [StoredObject(name, generation, len(data), dict(metadata))
                    for name, (generation, data, _, metadata) in sorted(self.objects.items())
                    if name.startswith(prefix_)]

    def get(self, name):
        with self._lock:
            if name not in self.objects:
                raise NotFound('Screenshot not found')
            generation, data, content_type, metadata = self.objects[name]
            return data, content_type, dict(metadata)

    def delete(self, name):
        with self._lock:
            self.objects.pop(name, None)
            self.custom_times.pop(name, None)

    def set_custom_time(self, name, when: float):
        with self._lock:
            if name in self.objects:
                if self.custom_times.get(name, 0) > when:   # like Cloud Storage: it can never move back
                    raise ValueError('customTime cannot decrease')
                self.custom_times[name] = when

    def rewrite_without_custom_time(self, name):
        with self._lock:
            if name in self.objects:
                _, data, content_type, metadata = self.objects[name]
                self._generation += 1
                self.objects[name] = (self._generation, data, content_type, metadata)
                self.custom_times.pop(name, None)

    def check_private(self):
        return True


class BucketStore:
    """QA's private Cloud Storage bucket, through the runtime service account (ADC)."""

    def __init__(self, bucket_name, project=None):
        if 'STORAGE_EMULATOR_HOST' in os.environ:
            raise RuntimeError('Emulator storage is not permitted for QA screenshots')
        from google.cloud import storage
        from google.api_core.retry import Retry
        self.retry = Retry(deadline=30)
        self.bucket = storage.Client(project=project).bucket(bucket_name)

    def check_private(self):
        """Staging refuses to start unless the bucket is private (uniform access + public access prevention)."""
        self.bucket.reload(timeout=10, retry=self.retry)
        iam = self.bucket.iam_configuration
        if not iam.uniform_bucket_level_access_enabled or iam.public_access_prevention != 'enforced':
            raise RuntimeError('The QA screenshot bucket must enforce uniform access and public access prevention')
        return True

    def put(self, name, data, content_type, metadata):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(name)
        blob.cache_control = 'private, no-store'
        blob.metadata = metadata
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0, timeout=20,
                                    retry=self.retry)
        except PreconditionFailed:
            raise FileExistsError(name) from None

    def list(self, prefix_):
        return [StoredObject(blob.name, blob.generation, blob.size or 0, dict(blob.metadata or {}))
                for blob in self.bucket.list_blobs(prefix=prefix_, timeout=15, retry=self.retry)]

    def get(self, name):
        from google.api_core.exceptions import NotFound as Missing
        blob = self.bucket.blob(name)
        try:
            blob.reload(timeout=10, retry=self.retry)
            if (blob.size or 0) > MAX_BYTES:
                raise StorageUnavailable('Object exceeds the size limit')
            data = blob.download_as_bytes(if_generation_match=blob.generation, timeout=20, retry=self.retry)
        except Missing:
            raise NotFound('Screenshot not found') from None
        return data, blob.content_type or '', dict(blob.metadata or {})

    def delete(self, name):
        from google.api_core.exceptions import NotFound as Missing
        try:
            self.bucket.blob(name).delete(timeout=15, retry=self.retry)
        except Missing:
            pass

    def set_custom_time(self, name, when: float):
        """Starts the lifecycle rule's 30-day clock (daysSinceCustomTime)."""
        from datetime import datetime, timezone
        blob = self.bucket.blob(name)
        blob.custom_time = datetime.fromtimestamp(when, timezone.utc)
        blob.patch(timeout=15, retry=self.retry)

    def rewrite_without_custom_time(self, name):
        """customTime can't be removed or moved back; replacing the object with an identical copy
        (same bytes and metadata, new generation) is Cloud Storage's documented reset."""
        blob = self.bucket.blob(name)
        blob.reload(timeout=10, retry=self.retry)
        if blob.custom_time is None:
            return
        generation = blob.generation
        data = blob.download_as_bytes(if_generation_match=generation, timeout=20, retry=self.retry)
        fresh = self.bucket.blob(name)
        fresh.cache_control = 'private, no-store'
        fresh.metadata = dict(blob.metadata or {})
        fresh.upload_from_string(data, content_type=blob.content_type, if_generation_match=generation, timeout=20,
                                 retry=self.retry)


def from_env(env=os.environ):
    """BucketStore when PERPERIS_QA_SCREENSHOT_BUCKET is set; MemoryStore for local
    development with PERPERIS_QA_SCREENSHOTS=memory; otherwise None (screenshots off)."""
    bucket = env.get('PERPERIS_QA_SCREENSHOT_BUCKET', '').strip()
    if bucket:
        if env.get('PERPERIS_QA_AUTH_MODE', 'local_shared') != 'identity':
            raise RuntimeError('The screenshot bucket requires verified identity sign-in')
        return BucketStore(bucket, project=env.get('PERPERIS_QA_IDENTITY_PROJECT') or None)
    if env.get('PERPERIS_QA_SCREENSHOTS', '') == 'memory':
        if 'K_SERVICE' in env or env.get('PERPERIS_QA_ENV', 'local') != 'local':
            raise RuntimeError('In-memory screenshots are for local development only')
        return MemoryStore()
    return None


_upload_lock = threading.Lock()
_uploads: dict[str, tuple[float, int]] = {}


def throttle(subject_key: str, now: float | None = None):
    """In-memory fixed window per member per instance."""
    now = time.time() if now is None else now
    with _upload_lock:
        for key, (start, _) in list(_uploads.items()):
            if start <= now - UPLOAD_WINDOW:
                del _uploads[key]
        start, count = _uploads.get(subject_key, (now, 0))
        if count >= UPLOAD_LIMIT:
            raise LimitReached('Too many uploads; try again in a few minutes')
        _uploads[subject_key] = (start, count + 1)


def _public(obj: StoredObject, viewer_subject: str | None, viewer_is_admin: bool) -> dict:
    meta = obj.metadata
    uploaded = int(meta.get('uploaded_at', '0') or 0)
    return {'id': obj.name.rsplit('/', 1)[1], 'uploader_name': meta.get('uploader_name', ''),
            'uploaded_at': uploaded, 'size': obj.size,
            'can_delete': viewer_is_admin or (viewer_subject is not None and meta.get('uploader') == viewer_subject)}


class Screenshots:
    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock

    def _current(self, issue: int) -> list[StoredObject]:
        """An issue's objects; expired ones (uploaded over 365 days ago) are deleted on sight."""
        now = self.clock()
        kept = []
        for obj in self.store.list(prefix(issue)):
            if not NAME.fullmatch(obj.name.rsplit('/', 1)[-1]):
                continue
            uploaded = int(obj.metadata.get('uploaded_at', '0') or 0)
            if uploaded and now - uploaded > RETENTION_SECONDS:
                self.store.delete(obj.name)
                continue
            kept.append(obj)
        kept.sort(key=lambda o: (int(o.metadata.get('uploaded_at', '0') or 0), o.name))
        return kept

    def list(self, issue: int, *, subject: str | None, is_admin: bool) -> list[dict]:
        return [_public(obj, subject, is_admin) for obj in self._current(issue)]

    def count(self, issue: int) -> int:
        return len(self._current(issue))

    def upload(self, issue: int, content: bytes, *, subject: str | None, name: str, is_admin: bool) -> dict:
        data, extension = normalize_image(content)
        if self.count(issue) >= MAX_PER_ISSUE:
            raise LimitReached(f'A ticket can have at most {MAX_PER_ISSUE} screenshots')
        object_ = prefix(issue) + secrets.token_hex(16) + '.' + extension
        metadata = {'uploader': subject or 'local', 'uploader_name': name[:60],
                    'sha256': hashlib.sha256(data).hexdigest(), 'uploaded_at': str(int(self.clock()))}
        self.store.put(object_, data, CONTENT_TYPES[extension], metadata)
        return _public(StoredObject(object_, 0, len(data), metadata), subject, is_admin)

    def read(self, issue: int, name: str, *, thumb: bool = False) -> tuple[bytes, str]:
        data, content_type, metadata = self.store.get(object_name(issue, name))
        extension = name.rsplit('.', 1)[1]
        if content_type != CONTENT_TYPES[extension] or hashlib.sha256(data).hexdigest() != metadata.get('sha256'):
            raise StorageUnavailable('Screenshot failed its integrity check')
        uploaded = int(metadata.get('uploaded_at', '0') or 0)
        if uploaded and self.clock() - uploaded > RETENTION_SECONDS:
            raise NotFound('Screenshot not found')
        if thumb:
            return thumbnail(data), 'image/webp'
        return data, content_type

    def mark_verified(self, issue: int) -> int:
        """Verify: set customTime on the issue's images so the lifecycle rule deletes them 30 days later.
        Returns how many could not be marked (the 365-day limit still applies to those)."""
        now, failed = self.clock(), 0
        for obj in self._current(issue):
            try:
                self.store.set_custom_time(obj.name, now)
            except Exception:
                failed += 1
        return failed

    def clear_verified(self, issue: int) -> int:
        """Reopen: rewrite the issue's images without customTime. Upload time is kept in metadata."""
        failed = 0
        for obj in self._current(issue):
            try:
                self.store.rewrite_without_custom_time(obj.name)
            except Exception:
                failed += 1
        return failed

    def delete(self, issue: int, name: str, *, subject: str | None, is_admin: bool):
        full = object_name(issue, name)
        for obj in self.store.list(prefix(issue)):
            if obj.name == full:
                if not is_admin and (subject is None or obj.metadata.get('uploader') != subject):
                    raise PermissionError('Only the uploader or an Admin can delete a screenshot')
                self.store.delete(full)
                return
        raise NotFound('Screenshot not found')
