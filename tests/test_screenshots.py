"""Screenshots (decision 4.1): validation, the private store contract and the API.

Only the in-memory store and mocks are used; CI never touches a real bucket.
"""
from io import BytesIO
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image, PngImagePlugin

import screenshots


def image_bytes(fmt='PNG', size=(12, 8), metadata=False, mode='RGB', frames=1):
    output = BytesIO()
    with Image.new(mode, size, 'green') as img:
        if frames > 1:
            extra = [Image.new(mode, size, 'red') for _ in range(frames - 1)]
            img.save(output, format=fmt, save_all=True, append_images=extra)
        elif metadata and fmt == 'PNG':
            info = PngImagePlugin.PngInfo()
            info.add_text('private note', 'synthetic-secret-metadata')
            img.save(output, format=fmt, pnginfo=info)
        elif metadata and fmt == 'JPEG':
            exif = Image.Exif()
            exif[0x010E] = 'synthetic-secret-metadata'   # ImageDescription
            img.save(output, format=fmt, exif=exif)
        else:
            img.save(output, format=fmt)
    return output.getvalue()


def decoded(content):
    with Image.open(BytesIO(content)) as image:
        image.load()
        return image.format, image.size, dict(image.info), image.getexif()


class Validation(unittest.TestCase):
    def test_png_stays_png_photos_become_webp_and_metadata_is_stripped(self):
        for fmt, expected in (('PNG', ('png', 'PNG')), ('JPEG', ('webp', 'WEBP')), ('WEBP', ('webp', 'WEBP'))):
            with self.subTest(fmt=fmt):
                data, extension = screenshots.normalize_image(image_bytes(fmt, metadata=True))
                kind, size, info, exif = decoded(data)
                self.assertEqual((extension, kind), expected)
                self.assertEqual(size, (12, 8))
                self.assertNotIn(b'synthetic-secret-metadata', data)
                self.assertFalse(len(exif))
                self.assertNotIn('icc_profile', info)

    def test_transparency_is_kept(self):
        data, _ = screenshots.normalize_image(image_bytes('PNG', mode='RGBA'))
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.mode, 'RGBA')

    def test_downscaled_to_2000_px_on_the_long_side(self):
        for fmt, size, expected in (('PNG', (3000, 1500), (2000, 1000)), ('PNG', (1200, 2400), (1000, 2000)),
                                    ('JPEG', (4000, 3000), (2000, 1500)), ('PNG', (1999, 10), (1999, 10))):
            with self.subTest(fmt=fmt, size=size):
                data, _ = screenshots.normalize_image(image_bytes(fmt, size=size))
                self.assertEqual(decoded(data)[1], expected)

    def test_rejects_svg_text_gif_animation_and_truncated_files(self):
        for content in (b'<svg><script>bad</script></svg>', b'not an image', image_bytes('GIF'),
                        image_bytes('PNG', frames=2), image_bytes('WEBP', frames=2), image_bytes('PNG')[:40]):
            with self.assertRaises(screenshots.InvalidImage) as caught:
                screenshots.normalize_image(content)
            self.assertNotIsInstance(caught.exception, screenshots.TooLarge)

    def test_size_dimension_and_pixel_limits(self):
        with self.assertRaises(screenshots.TooLarge):
            screenshots.normalize_image(b'x' * (screenshots.MAX_BYTES + 1))
        with self.assertRaises(screenshots.TooLarge):
            screenshots.normalize_image(b'')
        with patch.object(screenshots, 'MAX_SIDE', 10):
            with self.assertRaises(screenshots.InvalidImage):
                screenshots.normalize_image(image_bytes(size=(12, 8)))
        with patch.object(screenshots, 'MAX_PIXELS', 50):
            with self.assertRaises(screenshots.InvalidImage):
                screenshots.normalize_image(image_bytes(size=(12, 8)))

    def test_decompression_bombs_are_refused(self):
        with patch.object(Image, 'MAX_IMAGE_PIXELS', 40):
            with self.assertRaises(screenshots.InvalidImage):
                screenshots.normalize_image(image_bytes(size=(12, 8)))

    def test_thumbnail_is_small_webp(self):
        data, _ = screenshots.normalize_image(image_bytes(size=(1600, 900)))
        kind, size, _, _ = decoded(screenshots.thumbnail(data))
        self.assertEqual((kind, size), ('WEBP', (480, 270)))


class Service(unittest.TestCase):
    def setUp(self):
        self.now = [1_800_000_000.0]
        self.store = screenshots.MemoryStore()
        self.service = screenshots.Screenshots(self.store, clock=lambda: self.now[0])

    def upload(self, issue=7, subject='uid-a', name='Ann'):
        return self.service.upload(issue, image_bytes(metadata=True), subject=subject, name=name, is_admin=False)

    def test_objects_are_keyed_by_issue_with_metadata_and_no_email(self):
        item = self.upload()
        [(name, (_, data, content_type, metadata))] = self.store.objects.items()
        self.assertRegex(name, r'^issues/7/[a-f0-9]{32}\.png$')
        self.assertEqual(content_type, 'image/png')
        self.assertEqual(set(metadata), {'uploader', 'uploader_name', 'sha256', 'uploaded_at'})
        self.assertEqual((metadata['uploader'], metadata['uploader_name']), ('uid-a', 'Ann'))
        self.assertNotIn('@', ''.join(metadata.values()))
        self.assertEqual(item['id'], name.rsplit('/', 1)[1])
        self.assertTrue(item['can_delete'])
        self.assertEqual(self.service.list(8, subject='uid-a', is_admin=False), [])

    def test_cap_of_ten_is_counted_from_the_prefix(self):
        for _ in range(screenshots.MAX_PER_ISSUE):
            self.upload()
        with self.assertRaises(screenshots.LimitReached):
            self.upload()
        self.upload(issue=8)  # other issues are unaffected

    def test_writes_never_overwrite(self):
        self.store.put('issues/1/' + 'a' * 32 + '.png', b'x', 'image/png', {})
        with self.assertRaises(FileExistsError):
            self.store.put('issues/1/' + 'a' * 32 + '.png', b'y', 'image/png', {})

    def test_read_checks_integrity_and_names(self):
        item = self.upload()
        data, content_type = self.service.read(7, item['id'])
        self.assertEqual(content_type, 'image/png')
        self.assertEqual(self.service.read(7, item['id'], thumb=True)[1], 'image/webp')
        for bad in ('../x.png', 'a' * 32 + '.svg', item['id'].upper(), ''):
            with self.assertRaises(screenshots.NotFound):
                self.service.read(7, bad)
        with self.assertRaises(screenshots.NotFound):
            self.service.read(8, item['id'])     # wrong issue
        name = 'issues/7/' + item['id']
        generation, _, content_type, metadata = self.store.objects[name]
        self.store.objects[name] = (generation, b'tampered', content_type, metadata)
        with self.assertRaises(screenshots.StorageUnavailable):
            self.service.read(7, item['id'])

    def test_only_uploader_or_admin_deletes(self):
        item = self.upload()
        with self.assertRaises(PermissionError):
            self.service.delete(7, item['id'], subject='uid-b', is_admin=False)
        with self.assertRaises(PermissionError):
            self.service.delete(7, item['id'], subject=None, is_admin=False)
        self.service.delete(7, item['id'], subject='uid-a', is_admin=False)
        self.assertEqual(self.store.objects, {})
        other = self.upload()
        self.service.delete(7, other['id'], subject='uid-admin', is_admin=True)
        with self.assertRaises(screenshots.NotFound):
            self.service.delete(7, other['id'], subject='uid-a', is_admin=False)

    def test_objects_older_than_365_days_are_deleted_when_listed(self):
        old = self.upload()
        self.now[0] += 200 * 86400
        new = self.upload()
        self.now[0] += 166 * 86400
        with self.assertRaises(screenshots.NotFound):
            self.service.read(7, old['id'])
        self.assertEqual([i['id'] for i in self.service.list(7, subject=None, is_admin=True)], [new['id']])
        self.assertEqual(len(self.store.objects), 1)

    def test_verify_marks_and_reopen_rewrites_without_custom_time(self):
        first, second = self.upload(), self.upload()
        self.assertEqual(self.service.mark_verified(7), 0)
        names = sorted(self.store.objects)
        self.assertEqual({self.store.custom_times[n] for n in names}, {self.now[0]})
        generations = {n: self.store.objects[n][0] for n in names}
        self.assertEqual(self.service.clear_verified(7), 0)
        self.assertEqual(self.store.custom_times, {})
        self.assertTrue(all(self.store.objects[n][0] > generations[n] for n in names))   # new generation
        # Upload time survives the rewrite, so the 365-day limit still counts from the upload.
        self.assertEqual({i['uploaded_at'] for i in self.service.list(7, subject=None, is_admin=True)},
                         {first['uploaded_at'], second['uploaded_at']})

    def test_custom_time_failures_are_counted_not_raised(self):
        self.upload()
        with patch.object(self.store, 'set_custom_time', side_effect=OSError('down')):
            self.assertEqual(self.service.mark_verified(7), 1)
        with patch.object(self.store, 'rewrite_without_custom_time', side_effect=OSError('down')):
            self.assertEqual(self.service.clear_verified(7), 1)

    def test_upload_throttle_per_member(self):
        screenshots._uploads.clear()
        self.addCleanup(screenshots._uploads.clear)
        for _ in range(screenshots.UPLOAD_LIMIT):
            screenshots.throttle('uid-a', now=100.0)
        with self.assertRaises(screenshots.LimitReached):
            screenshots.throttle('uid-a', now=101.0)
        screenshots.throttle('uid-b', now=101.0)
        screenshots.throttle('uid-a', now=100.0 + screenshots.UPLOAD_WINDOW + 1)


class Configuration(unittest.TestCase):
    def test_from_env(self):
        self.assertIsNone(screenshots.from_env({}))
        self.assertIsInstance(screenshots.from_env({'PERPERIS_QA_SCREENSHOTS': 'memory'}), screenshots.MemoryStore)
        for env in ({'PERPERIS_QA_SCREENSHOTS': 'memory', 'K_SERVICE': 'x'},
                    {'PERPERIS_QA_SCREENSHOTS': 'memory', 'PERPERIS_QA_ENV': 'staging'},
                    {'PERPERIS_QA_SCREENSHOT_BUCKET': 'b'}):           # bucket needs identity mode
            with self.subTest(env=env), self.assertRaises(RuntimeError):
                screenshots.from_env(env)

    def test_bucket_must_be_private(self):
        def store(ubla, pap):
            instance = screenshots.BucketStore.__new__(screenshots.BucketStore)
            instance.retry = None
            instance.bucket = MagicMock()
            instance.bucket.iam_configuration = SimpleNamespace(
                uniform_bucket_level_access_enabled=ubla, public_access_prevention=pap)
            return instance
        self.assertTrue(store(True, 'enforced').check_private())
        for ubla, pap in ((False, 'enforced'), (True, 'inherited'), (True, 'unspecified')):
            with self.subTest(ubla=ubla, pap=pap), self.assertRaises(RuntimeError):
                store(ubla, pap).check_private()


def fixture_origin():
    import test_identity
    return test_identity.ORIGIN


class Api(unittest.TestCase):
    """Through the app, signed in as listed members (identity mode, provider mocked)."""

    def setUp(self):
        import app
        import identity
        import test_identity as fixture
        from fastapi.testclient import TestClient
        self.app = app
        self.enterContext(patch.dict(os.environ, {
            'PERPERIS_QA_AUTH_MODE': 'identity', 'PERPERIS_QA_ENV': 'local', 'PERPERIS_QA_TICKET_SOURCE': 'memory',
            'PERPERIS_QA_ORIGIN': fixture.ORIGIN, 'PERPERIS_QA_IDENTITY_PROJECT': 'synthetic-qa-project',
            'PERPERIS_QA_SECRET': 'synthetic-session-secret-' * 3, 'PERPERIS_QA_MEMBERS': fixture.MEMBERS}))
        identity._attempts.clear()
        screenshots._uploads.clear()
        self.enterContext(patch.object(identity, 'verify_provider_token', side_effect=fixture.IdentityTests.claims))
        self.enterContext(patch.object(identity, 'verify_provider_session'))
        self.client = self.enterContext(TestClient(app.APP, base_url=fixture.ORIGIN))
        self.store = screenshots.MemoryStore()
        app.APP.state.screenshots = screenshots.Screenshots(self.store)
        self.headers = {'X-QA-Request': '1', 'Origin': fixture.ORIGIN}
        self.sign_in('tester@example.test')
        self.ticket = self.client.post('/api/tickets', headers=self.headers, json={
            'title': 'Synthetic screenshot ticket', 'reporter': 'x', 'testing_round': 'R01',
            'description': 'Expected a button, saw none'}).json()['ticket']['id']

    def sign_in(self, email):
        self.client.cookies.clear()
        response = self.client.post('/api/session', headers=self.headers, json={'id_token': email})
        self.assertEqual(response.status_code, 200)

    def path(self, suffix=''):
        return f'/api/tickets/{self.ticket}/screenshots' + suffix

    def upload(self, content=None, ticket=None):
        url = f'/api/tickets/{ticket}/screenshots' if ticket else self.path()
        return self.client.post(url, headers=self.headers, content=content or image_bytes(metadata=True))

    def test_config_reports_screenshots_on(self):
        config = self.client.get('/api/auth/config').json()
        self.assertEqual((config['screenshots_enabled'], config['screenshot_limit']), (True, 10))

    def test_upload_list_view_delete(self):
        created = self.upload()
        self.assertEqual(created.status_code, 201, created.text)
        item = created.json()['screenshot']
        listed = self.client.get(self.path()).json()
        self.assertEqual([i['id'] for i in listed['screenshots']], [item['id']])
        self.assertEqual(listed['screenshots'][0]['uploader_name'], 'Tester')
        self.assertNotIn('uploader', listed['screenshots'][0])
        view = self.client.get(self.path('/' + item['id']))
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.headers['content-type'], 'image/png')
        self.assertEqual(view.headers['cache-control'], 'private, no-store')
        self.assertEqual(view.headers['x-content-type-options'], 'nosniff')
        self.assertIn('sandbox', view.headers['content-security-policy'])
        self.assertNotIn(b'synthetic-secret-metadata', view.content)
        thumb = self.client.get(self.path('/' + item['id']) + '?thumb=true')
        self.assertEqual(thumb.headers['content-type'], 'image/webp')
        # Another non-admin member cannot delete; the uploader and an Admin can.
        self.sign_in('dev@example.test')
        self.assertFalse(self.client.get(self.path()).json()['screenshots'][0]['can_delete'])
        self.assertEqual(self.client.delete(self.path('/' + item['id']), headers=self.headers).status_code, 403)
        self.sign_in('tester@example.test')
        self.assertEqual(self.client.delete(self.path('/' + item['id']), headers=self.headers).status_code, 204)
        self.assertEqual(self.client.get(self.path('/' + item['id'])).status_code, 404)
        second = self.upload().json()['screenshot']
        self.sign_in('admin@example.test')
        self.assertEqual(self.client.delete(self.path('/' + second['id']), headers=self.headers).status_code, 204)
        self.assertEqual(self.store.objects, {})

    def test_auth_origin_and_ticket_are_required(self):
        item = self.upload().json()['screenshot']
        self.client.cookies.clear()
        self.assertEqual(self.client.get(self.path()).status_code, 401)
        self.assertEqual(self.client.get(self.path('/' + item['id'])).status_code, 401)
        self.assertEqual(self.upload().status_code, 401)
        self.sign_in('tester@example.test')
        self.assertEqual(self.client.post(self.path(), headers={'X-QA-Request': '1'},
                                          content=image_bytes()).status_code, 403)
        self.assertEqual(self.client.delete(self.path('/' + item['id']), headers={'X-QA-Request': '1'}).status_code, 403)
        self.assertEqual(self.upload(ticket=9999).status_code, 404)
        self.assertEqual(self.client.get('/api/tickets/9999/screenshots').status_code, 404)

    def test_invalid_oversized_capped_and_throttled_uploads(self):
        self.assertEqual(self.upload(b'<svg/>').status_code, 422)
        self.assertEqual(self.upload(b'x' * (screenshots.MAX_BYTES + 1)).status_code, 413)
        with patch.object(screenshots, 'MAX_PER_ISSUE', 1):
            self.assertEqual(self.upload().status_code, 201)
            self.assertEqual(self.upload().status_code, 409)
        with patch.object(screenshots, 'UPLOAD_LIMIT', 0):
            response = self.upload()
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['retry-after'], str(screenshots.UPLOAD_WINDOW))

    def test_storage_failures_are_generic(self):
        with patch.object(self.store, 'put', side_effect=OSError('gs://secret-bucket/issues/1 exploded')):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-bucket', response.text)

    def test_github_comment_is_posted_once_and_edited(self):
        tickets = self.app.APP.state.tickets
        first = self.upload().json()['screenshot']
        self.upload()
        comment = tickets.screenshot_comments[self.ticket]
        self.assertIn('📎 2 screenshots in PERPERIS QA: ' + fixture_origin() + f'/#ticket-{self.ticket}', comment)
        self.assertNotIn('@', comment)
        self.client.delete(self.path('/' + first['id']), headers=self.headers)
        self.assertIn('📎 1 screenshot in PERPERIS QA', tickets.screenshot_comments[self.ticket])

    def test_comment_failure_does_not_fail_the_upload(self):
        with patch.object(self.app.APP.state.tickets, 'upsert_screenshot_comment', side_effect=RuntimeError('GitHub down')):
            self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(len(self.store.objects), 1)

    def test_verify_and_reopen_set_and_clear_the_deletion_clock(self):
        self.upload()
        [name] = self.store.objects
        self.sign_in('tester@example.test')
        for status, expected in (('verified', True), ('reopened', False), ('verified', True)):
            changed = self.client.patch(f'/api/tickets/{self.ticket}', headers=self.headers, json={'status': status})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(name in self.store.custom_times, expected, status)
        with patch.object(self.store, 'rewrite_without_custom_time', side_effect=OSError('down')):
            changed = self.client.patch(f'/api/tickets/{self.ticket}', headers=self.headers, json={'status': 'reopened'})
        self.assertEqual(changed.status_code, 200)   # the status change stands; the 365-day limit still applies

    def test_disabled_when_no_store(self):
        self.app.APP.state.screenshots = None
        self.assertEqual(self.client.get(self.path()).status_code, 404)
        self.assertFalse(self.client.get('/api/auth/config').json()['screenshots_enabled'])


if __name__ == '__main__':
    unittest.main()
