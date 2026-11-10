from io import BytesIO
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from PIL import Image, PngImagePlugin
from fastapi import HTTPException

import app
import attachments
import identity
import test_identity as fixture


def image_bytes(fmt='PNG', metadata=False):
    output=BytesIO()
    with Image.new('RGB',(12,8),'green') as img:
        if metadata:
            info=PngImagePlugin.PngInfo()
            info.add_text('private note','synthetic-secret-metadata')
            img.save(output,format=fmt,pnginfo=info)
        else:
            img.save(output,format=fmt)
    return output.getvalue()


class ImageValidationTests(unittest.TestCase):
    def test_metadata_is_removed_and_supported_formats_are_normalized(self):
        for fmt in ('PNG','JPEG','WEBP'):
            content=attachments.normalize_image(image_bytes(fmt,metadata=fmt=='PNG'))
            with Image.open(BytesIO(content)) as decoded:
                self.assertEqual(decoded.format,'PNG')
                self.assertEqual(decoded.size,(12,8))
                self.assertFalse(decoded.info)
            self.assertNotIn(b'synthetic-secret-metadata',content)

    def test_oversized_malformed_svg_and_multiframe_are_rejected(self):
        for content in (b'<svg><script>bad</script></svg>',b'not an image',image_bytes('GIF')):
            with self.assertRaises(HTTPException) as exc:
                attachments.normalize_image(content)
            self.assertEqual(exc.exception.status_code,422)
        with patch.object(attachments,'MAX_PIXELS',2):
            with self.assertRaises(HTTPException):
                attachments.normalize_image(image_bytes())
        with self.assertRaises(HTTPException) as exc:
            attachments.normalize_image(b'x'*(attachments.MAX_BYTES+1))
        self.assertEqual(exc.exception.status_code,413)

    def test_local_store_cannot_serve_repository_paths_or_traversal(self):
        with self.assertRaises(ValueError):
            attachments.LocalStore(Path(__file__).resolve().parents[1]/'static')
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store=attachments.LocalStore(tmp)
            with self.assertRaises(ValueError):
                store.get('../../app.py','local-v1')
            key='a'*64+'.png'
            store.put(key,b'synthetic')
            with self.assertRaises(FileExistsError):
                store.put(key,b'overwrite')
            self.assertEqual(store.get(key,'local-v1'),b'synthetic')
            store.delete(key)
            self.assertFalse(list(Path(tmp).iterdir()))


class AttachmentTests(unittest.TestCase):
    make_database=fixture.IdentityTests.make_database
    claims=staticmethod(fixture.IdentityTests.claims)
    exchange=fixture.IdentityTests.exchange
    admin=fixture.IdentityTests.admin
    add_member=fixture.IdentityTests.add_member
    create=fixture.IdentityTests.create

    def setUp(self):
        fixture.IdentityTests.setUp(self)
        self.store=attachments.LocalStore(Path(self.tmp.name)/'screenshots')
        self.enterContext(patch.object(app.APP.state,'attachments',self.store))
        self.admin()
        self.ticket=self.create().json()['ticket']
        self.tid=self.ticket['id']
        self.service=attachments.Attachments(self.db,self.store)

    def upload(self):
        return self.client.post(f'/api/tickets/{self.tid}/attachments',headers=self.headers,content=image_bytes(metadata=True))

    def test_authorized_upload_private_read_and_uploader_admin_deletion(self):
        self.add_member('tester','Tester')
        result=self.upload()
        self.assertEqual(result.status_code,201,result.text)
        aid=result.json()['id']
        response=self.client.get('/api/attachments/'+aid)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['Content-Type'],'image/png')
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertNotIn(b'synthetic-secret-metadata',response.content)
        listed=self.client.get(f'/api/tickets/{self.tid}/attachments').json()['attachments'][0]
        self.assertNotIn('object_key',listed)
        self.assertNotIn('generation',listed)
        self.add_member('developer','Developer')
        self.assertEqual(self.client.delete('/api/attachments/'+aid,headers=self.headers).status_code,403)
        self.exchange('tester')
        self.assertEqual(self.client.delete('/api/attachments/'+aid,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/attachments/'+aid).status_code,404)
        self.assertFalse(list(self.store.root.iterdir()))

    def test_anonymous_csrf_quota_and_invalid_uploads(self):
        result=self.upload()
        aid=result.json()['id']
        self.assertEqual(self.client.get('/api/attachments/'+aid,headers={'Cookie':''}).status_code,401)
        self.assertEqual(self.client.post(f'/api/tickets/{self.tid}/attachments',headers={'Cookie':''},content=image_bytes()).status_code,401)
        self.assertEqual(self.client.post(f'/api/tickets/{self.tid}/attachments',content=image_bytes()).status_code,403)
        self.assertEqual(self.client.post(f'/api/tickets/{self.tid}/attachments',headers=self.headers,content=b'<svg/>').status_code,422)
        self.assertEqual(self.client.post('/api/tickets/9999/attachments',headers=self.headers,content=image_bytes()).status_code,404)
        with patch.object(attachments,'MAX_FILES',1):
            self.assertEqual(self.upload().status_code,409)
        with patch.object(attachments,'MAX_BYTES',32):
            self.assertEqual(self.upload().status_code,413)
        with self.db.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM qa_attachments').fetchone()['n'],1)

    def test_failed_upload_compensates_even_if_object_was_written(self):
        original=self.store.put
        def failed_put(key,data):
            original(key,data)
            raise OSError('synthetic storage failure')
        with patch.object(self.store,'put',side_effect=failed_put):
            self.assertEqual(self.upload().status_code,503)
        self.assertFalse(list(self.store.root.iterdir()))
        self.assertEqual(self.client.get(f'/api/tickets/{self.tid}/attachments').json()['attachments'],[])
        with self.db.connection() as db:
            self.assertEqual(db.execute('SELECT state FROM qa_attachments').fetchone()['state'],'failed')

    def test_failed_delete_revokes_reads_then_cleanup_retries(self):
        aid=self.upload().json()['id']
        with patch.object(self.store,'delete',side_effect=OSError('synthetic unavailable')):
            self.assertEqual(self.client.delete('/api/attachments/'+aid,headers=self.headers).status_code,503)
        self.assertEqual(self.client.get('/api/attachments/'+aid).status_code,404)
        with self.db.connection(write=True) as db:
            db.execute('UPDATE qa_attachments SET created_at=?',(int(time.time())-301,))
        self.assertEqual(self.service.cleanup(),1)
        self.assertFalse(list(self.store.root.iterdir()))

    def test_failed_compensation_keeps_pending_metadata_for_reconciliation(self):
        original=self.store.put
        def failed_put(key,data):
            original(key,data)
            raise OSError('synthetic response loss')
        with patch.object(self.store,'put',side_effect=failed_put),patch.object(self.store,'delete',side_effect=OSError('synthetic cleanup failure')):
            self.assertEqual(self.upload().status_code,503)
        with self.db.connection(write=True) as db:
            row=db.execute('SELECT * FROM qa_attachments').fetchone()
            self.assertEqual(row['state'],'pending')
            db.execute('UPDATE qa_attachments SET created_at=?',(int(time.time())-301,))
        self.assertEqual(self.service.cleanup(),1)
        self.assertFalse(list(self.store.root.iterdir()))

    def test_corrupt_object_and_disabled_feature_fail_closed(self):
        aid=self.upload().json()['id']
        with patch.object(self.store,'get',return_value=b'corrupt'):
            self.assertEqual(self.client.get('/api/attachments/'+aid).status_code,503)
        with patch.object(app.APP.state,'attachments',None):
            self.assertEqual(self.upload().status_code,404)
            self.assertFalse(self.client.get('/api/auth/config').json()['attachments_enabled'])


@unittest.skipUnless(os.getenv('PERPERIS_QA_TEST_POSTGRES_URL'), 'Set explicit disposable PostgreSQL test URL')
class PostgreSQLAttachmentTests(AttachmentTests):
    make_database=fixture.PostgreSQLIdentityTests.make_database


class CloudStoreContractTests(unittest.TestCase):
    def test_existing_bucket_privacy_and_generation_conditions(self):
        from unittest.mock import Mock
        with patch('google.cloud.storage.Client') as client:
            bucket=client.return_value.bucket.return_value
            bucket.iam_configuration.uniform_bucket_level_access_enabled=True
            bucket.iam_configuration.public_access_prevention='enforced'
            blob=bucket.blob.return_value
            blob.generation=123
            blob.download_as_bytes.return_value=b'synthetic'
            with patch.dict(os.environ,{'PERPERIS_QA_IDENTITY_PROJECT':'synthetic-project'}):
                store=attachments.CloudStore('synthetic-private-bucket')
            key='a'*64+'.png'
            self.assertEqual(store.put(key,b'synthetic'),'123')
            self.assertEqual(blob.upload_from_string.call_args.kwargs['if_generation_match'],0)
            self.assertEqual(blob.cache_control,'private, no-store')
            self.assertEqual(store.get(key,'123'),b'synthetic')
            self.assertEqual(blob.download_as_bytes.call_args.kwargs['if_generation_match'],123)
            store.delete(key,'123')
            self.assertEqual(blob.delete.call_args.kwargs['if_generation_match'],123)
            self.assertFalse(client.return_value.create_bucket.called)
            bucket.iam_configuration.public_access_prevention='inherited'
            with patch.dict(os.environ,{'PERPERIS_QA_IDENTITY_PROJECT':'synthetic-project'}):
                with self.assertRaises(ValueError):
                    attachments.CloudStore('synthetic-private-bucket')

    def test_cloud_storage_cannot_use_local_shared_authentication(self):
        with patch.dict(os.environ,{'PERPERIS_QA_ATTACHMENT_BACKEND':'gcs','PERPERIS_QA_AUTH_MODE':'local_shared'}):
            with self.assertRaises(ValueError):
                attachments.configured_store()
