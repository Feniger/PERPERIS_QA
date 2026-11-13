"""UI previews (kattens-pt DEC-526): archive validation, listing, fetch-once storage, cleanup, API.

A fake GitHub source and the in-memory store only; CI never calls GitHub or a real bucket.
"""
from io import BytesIO
import json
import unittest
import zipfile

from PIL import Image

import previews
import screenshots

SHA_PR = 'a' * 40
SHA_MAIN = 'b' * 40
SHA_OLD = 'c' * 40


def png(size=(8, 12)):
    out = BytesIO()
    Image.new('RGB', size, 'white').save(out, format='PNG')
    return out.getvalue()


def archive(files: dict[str, bytes], manifest: dict | None = None) -> bytes:
    out = BytesIO()
    with zipfile.ZipFile(out, 'w') as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
        if manifest is not None:
            bundle.writestr('manifest.json', json.dumps(manifest))
    return out.getvalue()


def good_archive(sha=SHA_PR):
    return archive({'today-light.png': png(), 'today-dark.png': png()}, {
        'sha': sha, 'pr': 7, 'screens': [
            {'key': 'today', 'label': 'Today', 'theme': 'light', 'file': 'today-light.png'},
            {'key': 'today', 'label': 'Today', 'theme': 'dark', 'file': 'today-dark.png'},
            {'key': 'ghost', 'label': 'Ghost', 'theme': 'light', 'file': 'ghost-light.png'},
        ], 'notes': ['coach (dark): timeout']})


class FakeSource:
    def __init__(self):
        self.downloads = 0
        self.pulls = [{'number': 7, 'title': 'Pink buttons', 'draft': False, 'html_url': 'https://example.test/7',
                       'head': {'sha': SHA_PR, 'ref': 'qa-feature/42-pink-buttons'}},
                      {'number': 8, 'title': 'Still building', 'draft': False, 'html_url': 'https://example.test/8',
                       'head': {'sha': 'd' * 40, 'ref': 'docs/notes'}}]
        self.arts = [
            {'id': 1, 'created_at': '2026-11-13T10:00:00Z', 'expired': False,
             'workflow_run': {'head_sha': SHA_MAIN, 'head_branch': 'main'}},
            {'id': 2, 'created_at': '2026-11-13T11:00:00Z', 'expired': False,
             'workflow_run': {'head_sha': SHA_PR, 'head_branch': 'qa-feature/42-pink-buttons'}},
            {'id': 3, 'created_at': '2026-11-12T09:00:00Z', 'expired': True,
             'workflow_run': {'head_sha': SHA_OLD, 'head_branch': 'main'}},
        ]

    def open_pulls(self):
        return self.pulls

    def artifacts(self):
        return self.arts

    def download(self, artifact_id):
        self.downloads += 1
        return good_archive(SHA_PR if artifact_id == 2 else SHA_MAIN)


class UnpackTests(unittest.TestCase):
    def test_keeps_only_listed_pngs_and_drops_missing_screens(self):
        manifest, images = previews.unpack(good_archive())
        self.assertEqual(sorted(images), ['today-dark.png', 'today-light.png'])
        self.assertEqual([s['file'] for s in manifest['screens']], ['today-light.png', 'today-dark.png'])
        self.assertEqual(manifest['notes'], ['coach (dark): timeout'])

    def test_ignores_unexpected_names(self):
        data = archive({'today-light.png': png(), '../evil.png': png(), 'x.html': b'<script>'},
                       {'screens': [{'key': 'today', 'label': 'Today', 'theme': 'light', 'file': 'today-light.png'}]})
        _, images = previews.unpack(data)
        self.assertEqual(list(images), ['today-light.png'])

    def test_rejects_non_png_and_missing_manifest(self):
        with self.assertRaises(ValueError):
            previews.unpack(archive({'today-light.png': b'not a png'}, {'screens': []}))
        with self.assertRaises(ValueError):
            previews.unpack(archive({'today-light.png': png()}))
        with self.assertRaises(ValueError):
            previews.unpack(b'not a zip')


class PreviewsTests(unittest.TestCase):
    def setUp(self):
        self.source = FakeSource()
        self.store = screenshots.MemoryStore()
        self.now = 1000.0
        self.service = previews.Previews(self.source, self.store, clock=lambda: self.now)

    def test_listing_marks_ready_pulls_links_tickets_and_picks_latest_live_main(self):
        data = self.service.listing()
        self.assertEqual(data['main']['sha'], SHA_MAIN)
        by_number = {p['number']: p for p in data['pulls']}
        self.assertTrue(by_number[7]['ready'])
        self.assertEqual(by_number[7]['issue'], 42)
        self.assertFalse(by_number[8]['ready'])
        self.assertIsNone(by_number[8]['issue'])

    def test_fetches_a_set_once_then_serves_from_storage(self):
        manifest = self.service.manifest(SHA_PR)
        self.assertEqual(len(manifest['screens']), 2)
        self.service.manifest(SHA_PR)
        self.assertEqual(self.service.image(SHA_PR, 'today-dark.png')[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(self.source.downloads, 1)

    def test_only_open_pull_heads_and_latest_main_can_be_requested(self):
        for sha in (SHA_OLD, 'd' * 40, 'not-a-sha'):
            with self.assertRaises(previews.PreviewNotFound):
                self.service.manifest(sha)
        with self.assertRaises(previews.PreviewNotFound):
            self.service.image(SHA_PR, '../today-light.png')

    def test_cleanup_deletes_sets_no_longer_referenced(self):
        self.store.put(f'previews/{SHA_OLD}/today-light.png', png(), 'image/png', {})
        self.service.manifest(SHA_PR)
        self.now += previews.CLEANUP_SECONDS + previews.LIST_SECONDS + 1
        self.service.listing()
        names = [o.name for o in self.store.list('previews/')]
        self.assertFalse(any(SHA_OLD in n for n in names))
        self.assertTrue(any(SHA_PR in n for n in names))


class ApiTests(unittest.TestCase):
    def test_previews_script_is_served_and_loaded(self):
        import app
        self.assertIn('previews.js', app.STATIC_FILES)
        index = (app.ROOT / 'static' / 'index.html').read_text()
        self.assertIn('<script src="/static/previews.js"></script>', index)
        self.assertIn('id="view-previews"', index)


if __name__ == '__main__':
    unittest.main()
