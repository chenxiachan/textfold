"""Cloud codec/config tests; the shared backend contract also runs via Miniflare."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from textfold import cloud_settings, store, storage_settings
from textfold.cloud_storage import CloudStorageBackend, StorageError, _NoRedirect, _pack, _unpack
from textfold.documents import Document, Source, Processing
from textfold.storage import LocalDirectoryBackend
from test_storage import BackendContract


class Codec(unittest.TestCase):
    def test_all_semantic_fields_history_and_opaque_bytes_round_trip(self):
        doc = Document('md-fixture', meta={'id': 'md-fixture', 'title': '中文 📚', '$bytes': 'ordinary data'},
                       source=Source(html='<p>Text</p>', markdown='# Text', xml='<text/>', wiki_html='wiki',
                                     reference='ref', pdf=b'pdf', originals={'docx': b'original'}),
                       layers={'en': {'version': 'v'}, 'tr-zh': {'title': '文献'}}, notes={'notes': [{'text': 'Mine'}]},
                       images={'nested/figure.png': b'pdf'}, processing=Processing({'pages': [1]}, {'a': 2}, {'reply': b'cache'}),
                       previous=Document('md-fixture', source=Source(markdown='Old', pdf=b'pdf')),
                       extensions={'plugin/data.bin': b'opaque'}, _legacy={'meta.json': b'  {"title":"legacy"}'},
                       created=123, updated=456)
        manifest, blobs = _pack(doc)
        calls = []
        restored = _unpack(json.loads(json.dumps(manifest)), lambda digest, size: calls.append(digest) or blobs[digest])
        self.assertEqual(restored, doc)
        self.assertEqual(restored._legacy, doc._legacy)
        self.assertEqual(len(calls), len(set(calls)))
        restored.meta['title'] = 'Edited'
        self.assertEqual(doc.meta['title'], '中文 📚')

    def test_corrupt_or_missing_blob_never_becomes_an_empty_document(self):
        manifest, _ = _pack(Document('md-fixture', source=Source(pdf=b'good')))
        with self.assertRaises(StorageError):
            _unpack(manifest, lambda *_: b'evil')
        with self.assertRaises(StorageError):
            _unpack({}, lambda *_: b'')

    def test_rejects_unsafe_keys_and_non_json_values(self):
        with self.assertRaises(ValueError):
            _pack(Document('md-fixture', images={'../outside': b'data'}))
        with self.assertRaises(ValueError):
            _pack(Document('md-fixture', meta={'value': float('nan')}))

    def test_endpoint_and_credentials_are_validated_before_network_use(self):
        for url in (None, 1, 'https://example.com:invalid', 'http://example.com', 'https://user:pass@example.com', 'https://example.com/path', 'file:///x', 'https://example.com/?token=x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                CloudStorageBackend(url, 'reader', 'secret')
        CloudStorageBackend('http://127.0.0.1:9876', '读者', '密码')
        for user, password in (('bad:user', 'secret'), ('reader', ''), ('reader', 'bad\npass')):
            with self.assertRaises(ValueError):
                CloudStorageBackend('https://example.com', user, password)

    def test_redirects_cannot_forward_credentials_and_errors_do_not_echo_secrets(self):
        with self.assertRaises(StorageError):
            _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')
        client = CloudStorageBackend('https://example.com', 'reader', 'secret')
        error = urllib.error.HTTPError(client.url, 401, 'secret', {}, io.BytesIO(b'secret'))
        with mock.patch.object(client._opener, 'open', side_effect=error), self.assertRaisesRegex(StorageError, '^Storage authentication failed$'):
            client.list()

    def test_requests_identify_textfold_and_reject_non_object_json(self):
        client = CloudStorageBackend('https://example.com', 'reader', 'secret')
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'[]'
        with mock.patch.object(client._opener, 'open', return_value=response) as opened:
            with self.assertRaisesRegex(StorageError, 'invalid response'):
                client.check()
            request = opened.call_args.args[0]
            self.assertTrue(request.get_header('User-agent').startswith('TextFold/'))


class KeyringFixture:
    def __init__(self):
        self.values = {}
    def set_password(self, service, user, password):
        self.values[service, user] = password
    def get_password(self, service, user):
        return self.values.get((service, user))
    def delete_password(self, service, user):
        self.values.pop((service, user), None)


class CloudSettings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / 'settings'
        self.backend = LocalDirectoryBackend(Path(tmp.name) / 'library')
        self.ring = KeyringFixture()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(cloud_settings, '_keyring', return_value=self.ring).start()
        mock.patch.object(CloudStorageBackend, 'check').start()
        mock.patch.object(CloudStorageBackend, 'sync_health', return_value={'format': 'textfold/storage-v2', 'library_id': 'fixture', 'cursor': 0}).start()
        self.body = {'enabled': True, 'url': 'https://storage.example.com', 'username': 'reader', 'password': 'secret-fixture'}

    def test_password_only_in_keyring_and_switch_is_deferred(self):
        status = cloud_settings.save(self.home, self.body, self.backend)
        self.assertTrue(status['restartRequired'])
        self.assertEqual(status['active'], 'local')
        self.assertNotIn('secret-fixture', (self.home / 'cloud-storage.json').read_text())
        self.assertNotIn('secret-fixture', json.dumps(status))
        active = cloud_settings.configured(self.home)
        from textfold.offline_storage import OfflineBackend
        self.assertIsInstance(active, OfflineBackend)
        self.assertFalse(cloud_settings.status(self.home, active)['restartRequired'])
        config = cloud_settings.load(self.home)
        self.assertEqual(self.ring.get_password(cloud_settings.SERVICE, config['credential']), 'secret-fixture')
        cloud_settings.save(self.home, {'enabled': False}, active)
        self.assertIsNone(cloud_settings.configured(self.home))

    def test_blank_password_keeps_only_same_identity_and_rotation_is_atomic(self):
        cloud_settings.save(self.home, self.body, self.backend)
        before = cloud_settings.load(self.home)
        cloud_settings.test(self.home, {**self.body, 'password': ''})
        with self.assertRaises(ValueError):
            cloud_settings.test(self.home, {**self.body, 'url': 'https://other.example.com', 'password': ''})
        with mock.patch.object(cloud_settings.os, 'replace', side_effect=OSError('disk full')), self.assertRaises(OSError):
            cloud_settings.save(self.home, {**self.body, 'password': 'new-password'}, self.backend)
        self.assertEqual(cloud_settings.load(self.home), before)
        self.assertEqual(len(self.ring.values), 1)
        self.assertEqual(cloud_settings._password(before), 'secret-fixture')

    def test_keyring_failure_preserves_previous_configuration(self):
        cloud_settings.save(self.home, self.body, self.backend)
        before = cloud_settings.load(self.home)
        with mock.patch.object(self.ring, 'set_password', side_effect=RuntimeError('secret detail')), self.assertRaisesRegex(OSError, 'Could not save'):
            cloud_settings.save(self.home, self.body, self.backend)
        self.assertEqual(cloud_settings.load(self.home), before)
        with mock.patch.object(cloud_settings, '_keyring', side_effect=OSError('locked')):
            blocked = cloud_settings.configured(self.home)
            self.assertTrue(cloud_settings.status(self.home, blocked)['error'])
            self.assertEqual(blocked.list(), [])  # cached metadata is available without keyring/network
            self.assertFalse(blocked.sync()['online'])

    def test_cloud_startup_does_not_activate_pending_local_move(self):
        cloud_settings.save(self.home, self.body, self.backend)
        with mock.patch.multiple(store, HOME=self.home, DATA=self.backend.root, _DEFAULT_DATA=self.backend.root,
                                 _repository=None, _repository_default=None), mock.patch.object(storage_settings, 'activate') as activate:
            store.initialize_storage()
            from textfold.offline_storage import OfflineBackend
            self.assertIsInstance(store.repository().backend, OfflineBackend)
            activate.assert_not_called()

    def check_late_edit_blocks_switch(self, target, during_save=False, resave=False):
        cloud_settings.save(self.home, self.body, self.backend)
        active = cloud_settings.configured(self.home)
        original = cloud_settings.load(self.home)
        doc = Document('md-late', meta={'id': 'md-late'}, source=Source(pdf=b'Original PDF'),
                       notes={'notes': [{'id': 'n-late', 'anchor': {'unit': 'p1'}, 'text': 'Unsent research'}]})
        if during_save:
            with mock.patch.object(CloudStorageBackend, 'check', side_effect=lambda: active.save(doc)):
                cloud_settings.save(self.home, target, active)
        else:
            cloud_settings.save(self.home, target, active)
            if resave:
                cloud_settings.save(self.home, {**target, 'username': 'third-account'}, active)
            active.save(doc)
        reopened = cloud_settings.configured(self.home)
        self.assertIsNotNone(reopened)
        self.assertEqual(reopened.cache.root, active.cache.root)
        self.assertEqual(reopened.status()['pending'], 1)
        self.assertEqual(reopened.get(doc.id).notes['notes'][0]['text'], 'Unsent research')
        self.assertEqual(reopened.get(doc.id).source.pdf, b'Original PDF')
        self.assertEqual(cloud_settings.load(self.home)['credential'], original['credential'])
        self.assertEqual(cloud_settings._password(original), 'secret-fixture')
        self.assertIn('switch', cloud_settings.status(self.home, reopened)['error'].lower())
        with zipfile.ZipFile(io.BytesIO(reopened.export_pending())) as archive:
            self.assertIn(b'Unsent research', archive.read('pending.json'))
        self.assertEqual(cloud_settings.configured(self.home).cache.root, active.cache.root)

    def test_account_switch_retains_late_edits_and_original_credential(self):
        self.check_late_edit_blocks_switch({**self.body, 'username': 'second-account', 'password': 'other-fixture'})

    def test_cache_switch_retains_late_edits(self):
        self.check_late_edit_blocks_switch({**self.body, 'cache_path': str(self.home / 'other-cache')})

    def test_disabling_cloud_retains_late_edits(self):
        self.check_late_edit_blocks_switch({'enabled': False})

    def test_switch_during_connection_probe_retains_concurrent_edits(self):
        self.check_late_edit_blocks_switch({**self.body, 'username': 'second-account'}, during_save=True)

    def test_repeated_deferred_switch_still_guards_the_active_workspace(self):
        self.check_late_edit_blocks_switch({**self.body, 'username': 'second-account'}, resave=True)

    def test_clean_switch_releases_old_credential_only_after_activation(self):
        cloud_settings.save(self.home, self.body, self.backend)
        active = cloud_settings.configured(self.home)
        original = cloud_settings.load(self.home)
        cloud_settings.save(self.home, {**self.body, 'username': 'second-account', 'password': 'other-fixture'}, active)
        self.assertEqual(cloud_settings._password(original), 'secret-fixture')
        reopened = cloud_settings.configured(self.home)
        self.assertNotEqual(reopened.cache.root, active.cache.root)
        self.assertEqual(cloud_settings._password(cloud_settings.load(self.home)), 'other-fixture')
        self.assertIsNone(self.ring.get_password(cloud_settings.SERVICE, original['credential']))

    def test_activation_checks_inflight_requests_even_without_a_pending_change(self):
        cloud_settings.save(self.home, self.body, self.backend)
        active = cloud_settings.configured(self.home)
        cloud_settings.save(self.home, {**self.body, 'username': 'second-account'}, active)
        doc = Document('md-inflight', meta={'id': 'md-inflight'}, source=Source(html='Original'))
        active.save(doc)
        with active.cache.transaction() as db:
            db.execute('INSERT INTO pending_requests VALUES (?,?,?,?,?,?,?)', (doc.id, 'uncertain-op', '{}', '{}', doc.revision, '{}', 'active'))
            db.execute('DELETE FROM pending_changes')
        reopened = cloud_settings.configured(self.home)
        self.assertEqual(reopened.cache.root, active.cache.root)
        self.assertEqual(reopened.status()['pending'], 1)

    def test_activation_checks_clean_but_unresolved_conflicts(self):
        cloud_settings.save(self.home, self.body, self.backend)
        active = cloud_settings.configured(self.home)
        cloud_settings.save(self.home, {'enabled': False}, active)
        doc = Document('md-conflict', meta={'id': 'md-conflict'}, source=Source(html='Original'),
                       notes={'notes': [], 'conflicts': [{'id': 'c-fixture', 'key': 'annotation/n-late', 'variants': [None, {'text': 'Retained'}]}]})
        active.save(doc)
        with active.cache.transaction() as db:
            db.execute('DELETE FROM pending_changes')
        reopened = cloud_settings.configured(self.home)
        self.assertEqual(reopened.cache.root, active.cache.root)
        self.assertEqual(reopened.status()['pending'], 0)
        self.assertEqual(reopened.status()['conflicts'], 1)

    def test_failed_activation_write_retains_guard_and_both_credentials_for_retry(self):
        cloud_settings.save(self.home, self.body, self.backend)
        active = cloud_settings.configured(self.home)
        original = cloud_settings.load(self.home)
        cloud_settings.save(self.home, {**self.body, 'username': 'second-account'}, active)
        target = cloud_settings.load(self.home)
        with mock.patch.object(cloud_settings.os, 'replace', side_effect=OSError('disk full')), self.assertRaises(OSError):
            cloud_settings.configured(self.home)
        self.assertEqual(cloud_settings.load(self.home), target)
        self.assertEqual(cloud_settings._password(original), 'secret-fixture')
        self.assertEqual(cloud_settings._password(target), 'secret-fixture')
        self.assertNotEqual(cloud_settings.configured(self.home).cache.root, active.cache.root)

    def test_canceled_switch_keeps_actual_cache_when_runtime_directory_changes(self):
        cloud_settings.save(self.home, self.body, self.backend)
        active = cloud_settings.configured(self.home, self.home / 'old-runtime')
        cloud_settings.save(self.home, {'enabled': False}, active)
        active.save(Document('md-late', meta={'id': 'md-late'}, source=Source(html='Unsent research')))
        for runtime in (self.home / 'new-runtime', None):
            reopened = cloud_settings.configured(self.home, runtime)
            self.assertEqual(reopened.cache.root, active.cache.root)
            self.assertEqual(reopened.status()['pending'], 1)
            self.assertEqual(reopened.get('md-late').source.html, 'Unsent research')

    def test_explicit_data_override_and_injected_backend_stay_isolated(self):
        cloud_settings.save(self.home, self.body, self.backend)
        with mock.patch.multiple(store, HOME=self.home, DATA=self.backend.root, _DEFAULT_DATA=self.home / 'other',
                                 _repository=None, _repository_default=None):
            self.assertIsInstance(store.repository().backend, LocalDirectoryBackend)
            with store.use_backend(self.backend):
                store.initialize_storage()
                self.assertIs(store.repository().backend, self.backend)


@unittest.skipUnless(os.environ.get('TEXTFOLD_TEST_STORAGE_URL'), 'run cloudflare/storage npm test for HTTP integration')
class CloudDocuments(BackendContract, unittest.TestCase):
    def test_legacy_native_language_index_matches_python_without_mutating_layers(self):
        from textfold import langs
        from textfold.storage import document_info
        from test_text_language import ZH, EN, DE
        texts = [ZH, EN, DE, 'ผลลัพธ์แสดงว่าวิธีนี้ช่วยให้ผู้อ่านเข้าใจบทความยาวได้ดีขึ้น และใช้เวลาน้อยลง',
                 '本文提出了一種新的方法，我們在多個基準上進行了實驗，結果表明這個方法是有效的。',
                 'この論文では、新しい手法を提案する。複数のベンチマークで実験を行った。']
        texts += [' '.join(sorted(words))*3 for words in langs._SMALL.values()]
        cases = [(text, {'version':'legacy'}) for text in texts]
        cases += [(ZH, {'version':'legacy','lang':code}) for code in ('','xx','zh-CN')]
        for i, (text, layer) in enumerate(cases):
            with self.subTest(index=i):
                doc = Document('md-native-'+str(i), meta={'title':'Legacy native text'},
                               source=Source(html='<p>'+text+'</p>'), layers={'en':layer})
                self.backend.save(doc)
                self.assertEqual(self.backend.info(doc.id).languages, document_info(doc).languages)
                self.assertEqual(self.backend.get(doc.id).layers, {'en':layer})

    def setUp(self):
        self.backend = CloudStorageBackend(os.environ['TEXTFOLD_TEST_STORAGE_URL'],
                                          os.environ['TEXTFOLD_TEST_STORAGE_USER'], os.environ['TEXTFOLD_TEST_STORAGE_PASSWORD'])
        self.backend.check()
        for row in self.backend.list():
            self.backend.delete(row.id)
        self.backend.purge(1e20)
        state = self.backend.get_library()
        state.catalog, state.zotero = {}, {}
        self.backend.save_library(state)

    def test_missing_document_and_empty_blob_round_trip(self):
        with self.assertRaises(FileNotFoundError):
            self.backend.get('md-missing')
        doc = Document('md-empty', meta={'id': 'md-empty'}, source=Source(pdf=b''))
        self.backend.save(doc)
        self.assertEqual(self.backend.get(doc.id).source.pdf, b'')

    def test_listing_reads_no_snapshots_or_binary_content(self):
        doc = Document('md-index', meta={'id': 'md-index'}, source=Source(pdf=b'original', html='text'))
        self.backend.save(doc)
        with mock.patch.object(self.backend, '_download', side_effect=AssertionError('binary read')):
            self.assertEqual(self.backend.info(doc.id).id, doc.id)
            self.assertEqual(self.backend.list()[0].id, doc.id)


@unittest.skipUnless(os.environ.get('TEXTFOLD_TEST_STORAGE_URL'), 'run cloudflare/storage npm test for HTTP integration')
class CloudRoutes(unittest.TestCase):
    import test_storage_routes as _routes
    start, stop, call = _routes.StorageRoutes.start, _routes.StorageRoutes.stop, _routes.StorageRoutes.call

    def setUp(self):
        self._routes.StorageRoutes.setUp(self)
        self.stop()
        self.env.update(PYTHON_KEYRING_BACKEND='keyring_fixture.FixtureKeyring',
                        PYTHONPATH=str(Path(__file__).parent.resolve()),
                        TEXTFOLD_TEST_KEYRING_FILE=str(self.root / 'test-keyring.json'))
        self.start()
        self.body = {'enabled': True, 'url': os.environ['TEXTFOLD_TEST_STORAGE_URL'].rstrip('/'),
                     'username': os.environ['TEXTFOLD_TEST_STORAGE_USER'], 'password': os.environ['TEXTFOLD_TEST_STORAGE_PASSWORD']}

    def test_connect_save_restart_and_return_local_without_moving_documents(self):
        code, imported = self.call('/api/import', {'name': 'local.md', 'text': '# Local\n\nKeep my existing document.'})
        self.assertEqual(code, 200)
        source = self.data / 'papers' / imported['paper'] / 'source.md'
        original = source.read_bytes()
        self.assertEqual(self.call('/api/storage/cloud/test', self.body), (200, {'ok': True}))
        code, saved = self.call('/api/storage/cloud', self.body)
        self.assertEqual(code, 200)
        self.assertEqual((saved['active'], saved['restartRequired']), ('local', True))
        self.assertNotIn(self.body['password'], json.dumps(saved))
        self.assertEqual(len(self.call('/api/papers')[1]), 1)
        self.stop(); self.start()
        self.assertEqual(self.call('/api/storage/cloud')[1]['active'], 'cloud')
        self.assertEqual(source.read_bytes(), original)
        _, remote = self.call('/api/import', {'name': 'remote.md', 'text': '# Remote\n\nSaved in D1 and R2.'})
        self.assertFalse((self.data / 'papers' / remote['paper']).exists())
        self.assertEqual(self.call('/api/ids/' + remote['paper'] + '?u=historical')[0], 200)
        import time
        self.call('/api/storage/sync', {'action': 'sync'})
        for _ in range(100):
            sync = self.call('/api/storage/sync')[1]
            if not sync['pending']:
                break
            time.sleep(.1)
        self.assertEqual(sync['pending'], 0, sync)
        code, disabled = self.call('/api/storage/cloud', {'enabled': False})
        self.assertEqual((code, disabled['restartRequired']), (200, True))
        self.stop(); self.start()
        self.assertEqual(self.call('/api/storage')[1]['backend'], 'local')
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(self.call('/api/papers')[1]), 1)

    def test_wrong_auth_does_not_persist_and_missing_keyring_keeps_settings_reachable(self):
        self.assertEqual(self.call('/api/storage/cloud', {**self.body, 'password': 'wrong'})[0], 400)
        self.assertFalse(self.call('/api/storage/cloud')[1]['enabled'])
        self.assertEqual(self.call('/api/storage/cloud', self.body)[0], 200)
        self.stop()
        (self.root / 'test-keyring.json').unlink()
        self.start()
        status = self.call('/api/storage/cloud')[1]
        self.assertTrue(status['error'])
        self.assertEqual(self.call('/api/settings')[0], 200)
        with __import__('urllib.request', fromlist=['urlopen']).urlopen(f'http://127.0.0.1:{self.port}/') as response:
            self.assertIn(b'storageError', response.read())
        self.assertEqual(self.call('/api/storage/cloud', {'enabled': False})[0], 200)
