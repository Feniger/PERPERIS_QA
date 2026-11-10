"""Private image storage, bounded decoding, two-phase metadata and cleanup.

No bucket is created. Disabled by default; local storage is development-only.
"""
from contextlib import closing
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import time
import warnings

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

import identity

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_FILES = 10
KEY = re.compile(r'^[a-f0-9]{64}\.png$')


def normalize_image(content):
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(413, 'Screenshot must be between 1 byte and 10 MiB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=('PNG','JPEG','WEBP')) as original:
                if original.format not in {'PNG','JPEG','WEBP'} or getattr(original, 'n_frames', 1) != 1:
                    raise ValueError('Unsupported image')
                width, height = original.size
                if width > 8192 or height > 8192 or width * height > MAX_PIXELS:
                    raise ValueError('Image dimensions too large')
                original.verify()
            with Image.open(BytesIO(content), formats=('PNG','JPEG','WEBP')) as original:
                # Copy decoded pixels into a fresh image to strip EXIF, comments,
                # profile, filenames, text chunks and other embedded metadata.
                converted = original.convert('RGBA' if 'A' in original.getbands() or 'transparency' in original.info else 'RGB')
                with closing(converted):
                    with Image.new(converted.mode, converted.size) as clean:
                        clean.paste(converted)
                        output = BytesIO()
                        clean.save(output, format='PNG')
                        normalized = output.getvalue()
        if len(normalized) > MAX_BYTES:
            raise HTTPException(413, 'Decoded screenshot exceeds the storage size limit')
        return normalized
    except HTTPException:
        raise
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, 'Use a valid, single-frame PNG, JPEG or WebP within image limits') from None


class LocalStore:
    def __init__(self, root):
        root = Path(root).resolve()
        repository = Path(__file__).resolve().parent
        if root == repository or repository in root.parents:
            raise ValueError('Local screenshots must be outside the repository and static directory')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.stat().st_mode & 0o077:
            raise ValueError('Local screenshot directory must be private (0700)')
        self.root = root

    def path(self, key):
        if not KEY.fullmatch(key):
            raise ValueError('Invalid object key')
        return self.root / key

    def put(self, key, content):
        fd = os.open(self.path(key), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(content)
        return 'local-v1'

    def get(self, key, generation):
        fd = os.open(self.path(key), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            content = source.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('Object exceeds size limit')
        return content

    def delete(self, key, generation=None):
        self.path(key).unlink(missing_ok=True)


class CloudStore:
    def __init__(self, bucket_name):
        if 'STORAGE_EMULATOR_HOST' in os.environ:
            raise ValueError('Emulator storage is not permitted for private cloud screenshots')
        from google.cloud import storage
        from google.api_core.retry import Retry
        self.retry = Retry(deadline=45)
        # Existing dedicated QA bucket only, using ADC. No bucket/ACL creation.
        self.bucket = storage.Client(project=os.environ['PERPERIS_QA_IDENTITY_PROJECT']).bucket(bucket_name)
        self.bucket.reload(timeout=10, retry=self.retry)
        iam = self.bucket.iam_configuration
        if not iam.uniform_bucket_level_access_enabled or iam.public_access_prevention != 'enforced':
            raise ValueError('QA bucket must enforce uniform access and public access prevention')

    @staticmethod
    def validate(key):
        if not KEY.fullmatch(key):
            raise ValueError('Invalid object key')

    def put(self, key, content):
        self.validate(key)
        blob = self.bucket.blob(key)
        blob.cache_control = 'private, no-store'
        blob.upload_from_string(content, content_type='image/png', if_generation_match=0, timeout=15, retry=self.retry)
        if blob.generation is None:
            raise ValueError('Missing object generation')
        return str(blob.generation)

    def get(self, key, generation):
        self.validate(key)
        if generation is None:
            raise ValueError('Missing object generation')
        blob = self.bucket.blob(key, generation=int(generation))
        content = blob.download_as_bytes(start=0, end=MAX_BYTES, if_generation_match=int(generation), timeout=15, retry=self.retry)
        if len(content) > MAX_BYTES:
            raise ValueError('Object exceeds size limit')
        return content

    def delete(self, key, generation=None):
        from google.api_core.exceptions import NotFound
        self.validate(key)
        blob = self.bucket.blob(key, generation=int(generation) if generation else None)
        try:
            blob.delete(if_generation_match=int(generation) if generation else None, timeout=15, retry=self.retry)
        except NotFound:
            pass


def configured_store():
    backend = os.getenv('PERPERIS_QA_ATTACHMENT_BACKEND', 'disabled')
    if backend == 'disabled':
        return None
    if backend == 'local':
        if 'K_SERVICE' in os.environ or os.getenv('PERPERIS_QA_ENV', 'local') != 'local':
            raise ValueError('Local screenshots are local-development only')
        root = os.getenv('PERPERIS_QA_ATTACHMENT_DIR')
        if not root:
            raise ValueError('Explicit private local screenshot directory required')
        return LocalStore(root)
    if backend == 'gcs':
        if identity.mode() != 'identity':
            raise ValueError('Cloud screenshots require verified identity mode')
        bucket = os.getenv('PERPERIS_QA_ATTACHMENT_BUCKET')
        if not bucket:
            raise ValueError('Explicit dedicated QA bucket required')
        return CloudStore(bucket)
    raise ValueError('Unknown attachment backend')


class Attachments:
    def __init__(self, database, store):
        self.database = database
        self.store = store

    def upload(self, ticket_id, content, user):
        content = normalize_image(content)
        attachment_id = secrets.token_hex(16)
        key = secrets.token_hex(32)+'.png'
        checksum = hashlib.sha256(content).hexdigest()
        with self.database.connection(write=True) as db:
            identity.authorize_member(db, user)
            if db.execute('SELECT id FROM tickets WHERE id=?' + db.lock_clause, (ticket_id,)).fetchone() is None:
                raise HTTPException(404, 'Ticket not found')
            count = db.execute("SELECT COUNT(*) AS n FROM qa_attachments WHERE ticket_id=? AND state IN ('pending','ready')", (ticket_id,)).fetchone()['n']
            if count >= MAX_FILES:
                raise HTTPException(409, 'A ticket can contain at most 10 screenshots')
            db.execute('''INSERT INTO qa_attachments(id,ticket_id,uploader_subject,uploader_name,object_key,state,mime_type,size_bytes,checksum,created_at)
                VALUES(?,?,?,?,?,'pending','image/png',?,?,?)''',
                (attachment_id, ticket_id, user.subject, user.name, key, len(content), checksum, int(time.time())))
        generation = None
        try:
            generation = self.store.put(key, content)
            with self.database.connection(write=True) as db:
                identity.authorize_member(db, user)
                # Cleanup may have claimed an old pending reservation after a crash.
                result = db.execute("UPDATE qa_attachments SET state='ready',generation=? WHERE id=? AND state='pending'", (generation, attachment_id))
                if result.rowcount != 1:
                    raise ValueError('Upload reservation is no longer pending')
                identity.security_event(db, user.subject, str(ticket_id), 'attachment_uploaded')
        except Exception:
            # An upload may have created an object before the client observed a
            # failure. Random unique keys allow compensating deletion even then.
            try:
                self.store.delete(key, generation)
                with self.database.connection(write=True) as db:
                    db.execute("UPDATE qa_attachments SET state='failed' WHERE id=? AND state='pending'", (attachment_id,))
            except Exception:
                pass  # Keep pending metadata for explicit cleanup; never expose it.
            raise HTTPException(503, 'Screenshot upload failed; retry later') from None
        return {'id': attachment_id, 'ticket_id': ticket_id, 'mime_type': 'image/png', 'size_bytes': len(content)}

    def list(self, ticket_id):
        with self.database.connection() as db:
            if db.execute('SELECT id FROM tickets WHERE id=?', (ticket_id,)).fetchone() is None:
                raise HTTPException(404, 'Ticket not found')
            return [dict(r) for r in db.execute("SELECT id,ticket_id,uploader_name,mime_type,size_bytes,created_at FROM qa_attachments WHERE ticket_id=? AND state='ready' ORDER BY created_at,id", (ticket_id,)).fetchall()]

    def read(self, attachment_id):
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM qa_attachments WHERE id=? AND state='ready'", (attachment_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Screenshot not found')
        try:
            content = self.store.get(row['object_key'], row['generation'])
            if hashlib.sha256(content).hexdigest() != row['checksum']:
                raise ValueError('Object integrity check failed')
            return content
        except Exception:
            raise HTTPException(503, 'Screenshot is temporarily unavailable') from None

    def delete(self, attachment_id, user):
        with self.database.connection(write=True) as db:
            identity.authorize_member(db, user)
            row = db.execute('SELECT * FROM qa_attachments WHERE id=?' + db.lock_clause, (attachment_id,)).fetchone()
            if row is None or row['state'] not in {'ready','deleting'}:
                raise HTTPException(404, 'Screenshot not found')
            if user.role != 'Admin' and (user.subject is None or row['uploader_subject'] != user.subject):
                raise HTTPException(403, 'Only the uploader or Admin can delete a screenshot')
            db.execute("UPDATE qa_attachments SET state='deleting' WHERE id=?", (attachment_id,))
            identity.security_event(db, user.subject, str(row['ticket_id']), 'attachment_delete_requested')
        # Reads are revoked before object removal. Failed deletes remain queued.
        try:
            self.store.delete(row['object_key'], row['generation'])
            with self.database.connection(write=True) as db:
                db.execute('DELETE FROM qa_attachments WHERE id=?', (attachment_id,))
        except Exception:
            raise HTTPException(503, 'Screenshot access revoked; storage cleanup is pending') from None

    def cleanup(self):
        """Operator-invoked reconciliation; only old incomplete/deleting metadata."""
        current = int(time.time())
        with self.database.connection(write=True) as db:
            rows = db.execute("SELECT * FROM qa_attachments WHERE state IN ('pending','failed','deleting') AND created_at<?" + db.lock_clause, (current-300,)).fetchall()
            for row in rows:
                db.execute("UPDATE qa_attachments SET state='deleting' WHERE id=?", (row['id'],))
        cleaned = 0
        for row in rows:
            try:
                self.store.delete(row['object_key'], row['generation'])
                with self.database.connection(write=True) as db:
                    db.execute("DELETE FROM qa_attachments WHERE id=? AND state='deleting'", (row['id'],))
                cleaned += 1
            except Exception:
                pass  # Retain metadata for next retry; do not print keys/content.
        return cleaned


def main():
    import argparse
    from database import from_env
    parser = argparse.ArgumentParser(description='Reconcile incomplete private screenshot uploads/deletes.')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        parser.error('--apply is required to delete incomplete objects')
    db = from_env()
    try:
        store = configured_store()
        if store is None:
            raise ValueError('Attachments are disabled')
        db.start()
        db.check_schema()
        print('Incomplete screenshots cleaned:', Attachments(db, store).cleanup())
    except Exception:
        parser.exit(1, 'Screenshot cleanup failed; verify private configuration.\n')
    finally:
        db.close()


if __name__ == '__main__':
    main()
