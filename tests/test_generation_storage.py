"""Generation storage costs, using disposable documents and a stand-in model."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from textfold import local, store, pipeline, llm
from textfold.offline_storage import OfflineBackend
from textfold.cloud_storage import StorageError
from textfold.sqlite_storage import SQLiteBackend
from test_text_language import Model, EN, CFG


class GenerationStorage(unittest.TestCase):
    def test_stopped_generation_keeps_completed_replies_and_releases_sync(self):
        remote = SimpleNamespace(url='https://storage.example.test', sync_health=mock.Mock(side_effect=StorageError('offline')))
        with tempfile.TemporaryDirectory() as root:
            backend = OfflineBackend(root, remote)
            with store.use_backend(backend), mock.patch.object(backend, 'request_sync') as notify:
                pid = local.import_text(EN, 'fixture.md')
                before = backend.cache.get(pid).source
                notify.reset_mock()
                def stop(doc, cfg, cache, **kwargs):
                    cache.mkdir(parents=True, exist_ok=True)
                    (cache/'completed.json').write_bytes(b'{"out":"paid reply"}')
                    raise llm.Stopped()
                with mock.patch.object(pipeline.ladder, 'build', stop), \
                     mock.patch.object(pipeline, 'task', lambda cfg, name: cfg), self.assertRaises(llm.Stopped):
                    pipeline.generate(pid, None, CFG)
                self.assertEqual(notify.call_count, 1)
            restarted = OfflineBackend(root, remote)
            self.assertEqual(restarted.cache.get(pid).processing.replies['completed.json'], b'{"out":"paid reply"}')
            self.assertEqual(restarted.cache.get(pid).source, before)
            self.assertGreater(restarted.status()['pending'], 0)
            self.assertEqual(backend._deferred, {})

    def test_document_deferral_is_nested_and_other_documents_remain_syncable(self):
        remote = SimpleNamespace(url='https://storage.example.test')
        with tempfile.TemporaryDirectory() as root:
            backend = OfflineBackend(root, remote)
            from textfold.documents import Document
            backend.cache.save(Document('md-held', meta={'title':'Keep'}))
            with backend.cache.transaction() as db:
                db.execute('DELETE FROM pending_changes')
            with mock.patch.object(backend, 'request_sync') as notify:
                with backend.defer_sync('md-held'):
                    self.assertGreater(backend.status()['pending'], 0)
                    backend.clear_cache()
                    self.assertEqual(backend.cache.get('md-held').meta['title'], 'Keep')
                    with backend.defer_sync('md-held'):
                        changed = backend.cache.get('md-held')
                        changed.meta['title'] = 'Updated'
                        backend.save(changed)
                    self.assertEqual(notify.call_count, 0)
                    backend.save(Document('md-other', meta={'title':'Other'}))
                    self.assertEqual(notify.call_count, 1)
                self.assertEqual(notify.call_count, 2)
            self.assertEqual(backend._deferred, {})

    def test_workspace_small_reads_do_not_reread_original_payloads(self):
        with tempfile.TemporaryDirectory() as root, store.use_backend(SQLiteBackend(root)) as repo:
            pid = local.import_text(EN, 'fixture.md')
            with store.edit(pid) as doc:
                doc.source.pdf = b'%PDF-' + b'x' * (1024 * 1024)
                doc.images['figure.bin'] = b'figure bytes'
            with repo.workspace(pid):
                read = Path.read_bytes
                payloads = []
                def counted(path):
                    if path.name in ('source.pdf', 'figure.bin'):
                        payloads.append(path.name)
                    return read(path)
                with mock.patch.object(Path, 'read_bytes', counted):
                    for _ in range(10):
                        self.assertTrue(store.asset(pid, 'meta.json'))
                        self.assertFalse(store.has(pid, 'missing.json'))
                        if _ == 0:
                            self.assertIsNone(store.read(pid, 'en'))
                        store.write(pid, 'en', {'version':'test','lang':'en'})
                        self.assertEqual(store.read(pid, 'en')['version'], 'test')
                        store.parsed(pid)
                        store.languages(pid)
                self.assertEqual(payloads, [], 'small workspace reads must not scan PDF/images')

    def test_fold_defers_sync_until_local_generation_finishes(self):
        remote = SimpleNamespace(url='https://storage.example.test', sync_health=mock.Mock(side_effect=StorageError('offline')))
        with tempfile.TemporaryDirectory() as root:
            backend = OfflineBackend(root, remote)
            with store.use_backend(backend), mock.patch.object(backend, 'request_sync') as notify:
                pid = local.import_text(EN, 'fixture.md')
                notify.reset_mock()
                def progress(stage, *args):
                    self.assertEqual(notify.call_count, 0, 'partial fold must stay local until generation exits')
                with mock.patch.object(llm, 'call', Model()), \
                     mock.patch.object(pipeline, 'task', lambda cfg, name: cfg), \
                     mock.patch.object(pipeline.links, 'build', lambda *a, **kw: 0):
                    pipeline.generate(pid, 'zh', CFG, jobs=1, progress=progress)
                self.assertEqual(notify.call_count, 1)
                self.assertIn('en', backend.cache.get(pid).layers)
                self.assertIn('tr-zh', backend.cache.get(pid).layers)
