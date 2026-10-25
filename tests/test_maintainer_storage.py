"""Compatibility regressions from the maintainer review; disposable data only."""
import copy, json, tempfile, unittest
from pathlib import Path
from textfold import sync_records
from textfold.documents import Document, Source
from textfold.document_wire import pack
from textfold.sqlite_storage import SQLiteBackend

class ReviewRegressions(unittest.TestCase):
    def test_cloud_native_language_migration_preserves_confirmed_conversation(self):
        from unittest import mock
        from textfold import local, store, notes, pipeline, llm
        from test_text_language import Model, ZH, CFG
        with tempfile.TemporaryDirectory() as root, store.use_backend(SQLiteBackend(root)), \
             mock.patch.object(llm, 'call', Model()) as model, \
             mock.patch.object(pipeline, 'task', lambda cfg, name: cfg), \
             mock.patch.object(pipeline.links, 'build', lambda *a, **k: 0):
            pid = local.import_text(ZH, 'native.md')
            pipeline.generate(pid, None, CFG, jobs=1)
            self.assertEqual(store.assemble(pid)['meta']['langs'], ['zh'])
            self.assertEqual(model.translations, [])
            doc = store.document(pid)
            doc.notes = {'notes':[{'id':'n-legacy', 'kind':'ask', 'anchor':{'on':'paper','lang':'en'},
                'thread':[{'q':'Why?', 'a':'Paid answer', 'lang':'en'}]}]}
            store.repository().save(doc)
            before = copy.deepcopy(store.document(pid).notes['notes'][0]['messages'])
            seen = notes.read(pid)['notes'][0]
            self.assertEqual((seen['anchor']['lang'],seen['thread'][0]['lang']), ('zh','zh'))
            self.assertEqual(sync_records.messages(seen), before)
            pipeline.generate(pid, 'en', CFG, jobs=1)
            self.assertEqual(store.assemble(pid)['meta']['langs'], ['zh','en'])
            seen = notes.read(pid)['notes'][0]
            self.assertEqual(seen['messages'], before)
            self.assertEqual(len(sync_records.branches(seen)), 1)
            seen['thread'].append({'q':'In English?', 'a':'A new answer', 'lang':'en'})
            notes.put(pid, seen)
            notes.settle_base(pid, 'zh')
            seen = notes.read(pid)['notes'][0]
            self.assertEqual([t['lang'] for t in seen['thread']], ['zh','en'])
            self.assertEqual([m for m in seen['messages'] if m['id'] in {old['id'] for old in before}], before)
            self.assertEqual(len(sync_records.branches(seen)), 1)
            self.assertEqual(len(seen['messages']), 4)

    def test_cloud_question_keeps_upstream_web_tools_behavior(self):
        from unittest import mock
        from textfold import local, store, notes, llm
        with tempfile.TemporaryDirectory() as root, store.use_backend(SQLiteBackend(root)):
            pid = local.import_text('# Tools\n\nOne paragraph about folding.', 'tools.md')
            uid = next(iter(store.assemble(pid)['units']))
            notes.put(pid, {'id':'n-tools','kind':'ask','anchor':{'unit':uid,'level':4,'tokens':[0,1]},'thread':[]})
            seen = []
            def reply(text, schema, spec, cache, timeout=300, system=None):
                seen.append((text,timeout))
                return {'out':{'answer':'Answer','takeaway':'Point','next':[]}}, False
            with mock.patch.object(llm, 'call', reply):
                notes.ask(pid,'n-tools','Why?',{'kind':'agent','id':'x/codex/m'})
                result = notes.ask(pid,'n-tools','And then?',{'kind':'agent','id':'x/codex/m','tools':True})
            self.assertEqual([t.get('tools') for t in result['thread']], [None,True])
            self.assertEqual([time for _,time in seen], [300,900])
            self.assertNotIn('search the web', seen[0][0])
            self.assertIn('search the web', seen[1][0])

    def test_documented_thread_additions_survive_stale_message_sidecars(self):
        note = {'id':'n-thread', 'anchor':{}, 'thread':[{'q':'First', 'a':'Old reply'}]}
        note['messages'] = sync_records.messages(note)
        note['thread'].append({'q':'Added by old pip', 'a':'Paid reply'})
        doc = Document('md-legacy', notes={'notes':[note]})
        joined = sync_records.join(doc.id, sync_records.split(pack(doc)[0]))
        actual = joined['notes']['notes'][0]
        self.assertEqual([t['q'] for t in actual['thread']], ['First', 'Added by old pip'])
        self.assertEqual(len(actual['messages']), 4)
        self.assertEqual(sync_records.last_answer(note), actual['thread'][-1]['message_id'])
        actual['text'] = 'Edited annotation'
        again = sync_records.join(doc.id, sync_records.note_records(doc.id, {'notes':[actual]}))
        self.assertEqual(again['notes']['notes'][0]['thread'], actual['thread'])

    def test_empty_canonical_thread_does_not_resurrect_archived_messages(self):
        note = {'id':'n-thread', 'anchor':{}, 'thread':[{'q':'First', 'a':'Old reply'}]}
        note['messages'] = sync_records.messages(note); note['thread'] = []
        value = sync_records.join('md-thread', sync_records.note_records('md-thread', {'notes':[note]}))
        self.assertEqual(value['notes']['notes'][0]['thread'], [])
        self.assertEqual(len(value['notes']['notes'][0]['messages']), 2)

    def test_id_history_merges_each_identity_and_conflict_candidates_keep_other_ids(self):
        with tempfile.TemporaryDirectory() as root:
            backend = SQLiteBackend(root)
            def records(units):
                raw = json.dumps({'format':'textfold/ids', 'version':1, 'units':units, 'sections':{}}).encode()
                doc = Document('md-history', extensions={'ids.json':raw}, _legacy={'ids.json':raw})
                manifest, blobs = pack(doc)
                backend._blobs(blobs)
                return sync_records.split(manifest)
            def history(values):
                ref = values['extensions/ids.json']
                return json.loads(backend._download(ref['sha256'], ref['size']))['units']
            with backend.transaction():
                base = records({'old':{'section':'s1'}})
                left = records({'old':{'section':'s1'}, 'phone':{'section':'s2'}})
                right = records({'old':{'section':'s1'}, 'laptop':{'section':'s3'}})
                merged, conflicts = backend.reconcile(base,left,right)
                self.assertEqual(conflicts, [])
                self.assertEqual(set(history(merged)), {'old','phone','laptop'})
                left = records({'old':{'section':'phone'}, 'phone':{'section':'s2'}})
                right = records({'old':{'section':'laptop'}, 'laptop':{'section':'s3'}})
                merged, conflicts = backend.reconcile(base,left,right)
                initial, initial_conflicts = backend.reconcile(records({}), records({'phone':{'section':'s2'}}), records({'laptop':{'section':'s3'}}))
                self.assertEqual(initial_conflicts, [])
                self.assertEqual(set(history(initial)), {'phone','laptop'})
                first, first_conflicts = backend.reconcile({}, records({'phone':{'section':'s2'}}), records({'laptop':{'section':'s3'}}))
                self.assertEqual(first_conflicts, [])
                self.assertEqual(set(history(first)), {'phone','laptop'})
                deleted, deleted_conflicts = backend.reconcile(base, {}, records({'old':{'section':'s1'},'new':{'section':'s4'}}))
                self.assertEqual(deleted_conflicts, [])
                self.assertEqual(set(history(deleted)), {'new'})
                self.assertEqual(len(conflicts), 1)
                for ref in merged['conflict/'+conflicts[0]]['variants']:
                    value = json.loads(backend._download(ref['sha256'], ref['size']))
                    self.assertEqual(set(value['units']), {'old','phone','laptop'})

    def test_default_local_mode_runs_without_cloud_optional_packages(self):
        import os, subprocess, sys
        with tempfile.TemporaryDirectory() as root:
            script = r"""
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('keyring', 'websockets'): raise ImportError('Optional cloud package absent')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from textfold import store, local, notes, cloud_settings
store.initialize_storage()
pid = local.import_text('# Default local\n\nNo cloud packages required.')
notes.put(pid, {'id':'n-local','anchor':{'on':'paper'},'text':'Kept'})
assert notes.read(pid)['notes'][0]['text'] == 'Kept'
assert cloud_settings.status(store.HOME, store.repository().backend)['enabled'] is False
"""
            env = {**os.environ, 'TEXTFOLD_DATA':root, 'USERPROFILE':root, 'HOME':root, 'PYTHONUTF8':'1'}
            result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8','replace'))

    def test_canonical_head_honors_one_sided_append_and_prefers_descendants(self):
        base = {'annotation/n-one':{'id':'n-one','text':'Old','thread_head':'z-old'}}
        local = copy.deepcopy(base); local['annotation/n-one']['thread_head'] = 'a-new'
        remote = copy.deepcopy(base); remote['annotation/n-one']['text'] = 'Remote annotation'
        merged, conflicts = sync_records.reconcile(base,local,remote)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged['annotation/n-one']['thread_head'], 'a-new')
        self.assertEqual(merged['annotation/n-one']['text'], 'Remote annotation')
        local['annotation/n-one']['thread_head'] = 'z-parent'
        remote['annotation/n-one']['thread_head'] = 'a-child'
        local['message/n-one/z-parent'] = {'id':'z-parent','parent':'z-old','role':'assistant','text':'Parent'}
        remote.update({'message/n-one/z-parent':local['message/n-one/z-parent'],
            'message/n-one/a-child':{'id':'a-child','parent':'z-parent','role':'assistant','text':'Child'}})
        merged, conflicts = sync_records.reconcile(base,local,remote)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged['annotation/n-one']['thread_head'], 'a-child')

    def test_local_layers_catalog_progress_cached_fetch_and_images_are_lazy(self):
        import threading, urllib.request, urllib.error
        from http.server import ThreadingHTTPServer
        from types import SimpleNamespace
        from unittest import mock
        from textfold import store, storage, library, server, fetch
        with tempfile.TemporaryDirectory() as root:
            backend = storage.LocalDirectoryBackend(root)
            backend.save(Document('2306.03310',meta={'id':'2306.03310','title':'Lazy fixture'},
                source=Source(html='<p>Source</p>'), images={'one.png':b'image-fixture'}))
            with store.use_backend(backend) as repo, mock.patch.object(backend,'get',side_effect=AssertionError('No whole-document read')), \
                 mock.patch.object(repo,'library',side_effect=AssertionError('No catalog snapshot')), \
                 mock.patch.object(repo,'save_library',side_effect=AssertionError('No catalog backend write')):
                store.write('2306.03310','en',{'version':'test'})
                library.change({'op':'new','name':'Reading'})
                job={'kind':'generate','pid':'2306.03310'}
                server.Jobs._progress(SimpleNamespace(),job,threading.Event())('parse',0,1)
                self.assertEqual(job['title'],'Lazy fixture')
                self.assertEqual(fetch.fetch('2306.03310',Path(root)).resolve(), (Path(root)/'papers/2306.03310').resolve())
                httpd=ThreadingHTTPServer(('127.0.0.1',0),server.make_handler(SimpleNamespace()))
                worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
                try:
                    url='http://127.0.0.1:'+str(httpd.server_port)+'/p/2306.03310/img/'
                    with urllib.request.urlopen(url+'one.png') as response: self.assertEqual(response.read(),b'image-fixture')
                    with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(url+'missing.png')
                    self.assertEqual(error.exception.code,404)
                finally: httpd.shutdown();httpd.server_close();worker.join(5)

    def test_local_parser_paths_honor_readonly_snapshot_and_discard_edits(self):
        from textfold import store, storage
        with tempfile.TemporaryDirectory() as root, store.use_backend(storage.LocalDirectoryBackend(root)) as repo:
            doc = Document('md-readonly',meta={'title':'Original'},source=Source(html='Original source'))
            repo.save(doc)
            original = store.pdir(doc.id)
            with repo.workspace(doc.id,write=False) as folder:
                self.assertNotEqual(folder.resolve(), original.resolve())
                self.assertEqual(store.pdir(doc.id).resolve(), folder.resolve())
                (store.pdir(doc.id)/'source.html').write_text('Discard this edit',encoding='utf-8')
                self.assertFalse(repo.checkpoint(doc.id))
            self.assertEqual((original/'source.html').read_text(encoding='utf-8'),'Original source')

    def test_selected_library_copy_keeps_desktop_startup_progress(self):
        from unittest import mock
        from textfold import store, storage, storage_settings, cloud_settings
        with tempfile.TemporaryDirectory() as root:
            current, target = Path(root)/'current', Path(root)/'copied'
            backend = storage.LocalDirectoryBackend(current)
            backend.save(Document('md-copy',meta={'title':'Keep source'},source=Source(html='Original bytes')))
            source = current/'papers/md-copy/source.html'
            before = source.read_bytes()
            messages = []
            with mock.patch.object(store,'DATA',current), mock.patch.object(store,'_DEFAULT_DATA',current), \
                 mock.patch.object(store,'_repository',None), mock.patch.object(store,'_repository_default',None), \
                 mock.patch.object(cloud_settings,'configured',return_value=None):
                storage_settings.schedule(current,current,str(target))
                store.initialize_storage(log=messages.append)
                self.assertEqual(store.library_root().resolve(),target.resolve())
                self.assertTrue(any('copying the library' in line for line in messages))
                self.assertEqual((target/'papers/md-copy/source.html').read_bytes(),before)
                self.assertEqual(source.read_bytes(),before)

    def test_rendered_cloud_local_and_exported_pages_select_the_right_conversation_mode(self):
        import re
        from textfold import build, local, server, storage, store
        def data(html):
            return json.loads(re.search(r'<script[^>]*id="paper-data"[^>]*>(.*?)</script>',html,re.S).group(1))
        with tempfile.TemporaryDirectory() as root:
            disk = storage.LocalDirectoryBackend(Path(root)/'local')
            with store.use_backend(disk):
                pid = local.import_text('# Mode fixture\n\nA document for reading.','mode.md')
                document = store.document(pid)
                local_page = server.reader_page(pid)
            cloud = SQLiteBackend(Path(root)/'cloud')
            document.revision = None
            with store.use_backend(cloud):
                store.repository().save(document)
                cloud_page = server.reader_page(pid)
                exported = build.page(server.assembled(pid),app=False)
            self.assertIs(data(cloud_page).get('cloudSync'),True)
            self.assertIs(data(local_page).get('cloudSync'),False)
            self.assertIs(data(exported).get('cloudSync'),False)
