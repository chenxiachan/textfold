"""notes.ask and notes.stop: a question the reader stopped while the model answered it leaves the note as it was; one
not stopped is answered into it. The model and the paper are stand-ins here: nothing calls a model.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import kinds, llm, notes, store

PID = "2512.24601"
NOTE = {"id": "n-test0001", "kind": "ask", "text": "",
        "anchor": {"unit": None, "section": None, "on": "paper", "lang": "en", "quote": {"exact": ""}},
        "thread": [{"q": "What is asked first?", "a": "The first answer.", "lang": "en"}]}
REPLY = {"out": {"answer": "The second answer.", "takeaway": "", "next": []}}


class AskStop(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(store, "DATA", Path(tmp.name)),
                  mock.patch.object(store, "assemble", return_value={"meta": {}, "units": {}, "chunks": []}),
                  mock.patch.object(notes, "prompt", return_value=("the prompt", {})),
                  mock.patch.object(kinds, "adapt", side_effect=lambda system, meta: system)):
            p.start()
            self.addCleanup(p.stop)
        store.pdir(PID).mkdir(parents=True)
        (store.pdir(PID) / "meta.json").write_text(json.dumps({"title": "A paper"}), encoding="utf-8")
        notes.put(PID, json.loads(json.dumps(NOTE)))

    def thread(self):
        return next(n for n in notes.read(PID)["notes"] if n["id"] == NOTE["id"])["thread"]

    def ask(self, req):
        return notes.ask(PID, NOTE["id"], "What is asked next?", {"id": "claude/claude-code/sonnet"}, req=req)

    def test_an_answer_not_stopped_is_kept(self):
        with mock.patch.object(llm, "call", return_value=(REPLY, None)):
            self.ask("r-kept")
        self.assertEqual([t["a"] for t in self.thread()], ["The first answer.", "The second answer."])

    def test_a_question_stopped_while_answered_leaves_the_note(self):
        def answering(*a, **k):   # the reader stops it while the model is at work
            notes.stop("r-stopped")
            return REPLY, None
        with mock.patch.object(llm, "call", side_effect=answering), self.assertRaises(notes.Stopped):
            self.ask("r-stopped")
        self.assertEqual([t["a"] for t in self.thread()], ["The first answer."])
        with mock.patch.object(llm, "call", return_value=(REPLY, None)):   # (asked again: answered, the stop spent)
            self.ask("r-stopped")
        self.assertEqual(len(self.thread()), 2)

    def test_another_question_stopped_is_not_this_one(self):
        notes.stop("r-other")
        with mock.patch.object(llm, "call", return_value=(REPLY, None)):
            self.ask("r-mine")
        self.assertEqual(len(self.thread()), 2)






if __name__ == "__main__":
    unittest.main()

class CloudAsk(AskStop):
    def setUp(self):
        super().setUp()
        from textfold.sqlite_storage import SQLiteBackend
        from textfold.documents import Document
        backend = SQLiteBackend(store.DATA / "cloud-workspace")
        document = store.document(PID); document.revision = None
        backend.save(document)
        context = store.use_backend(backend); context.__enter__(); self.addCleanup(context.__exit__, None, None, None)

    def test_cancel_pending_preserves_legacy_language_without_resurrecting_turn(self):
        from textfold import sync_records
        note = notes.read(PID)['notes'][0]
        note['thread'][0]['lang'] = 'en'
        note['messages'] = sync_records.messages(note)
        sync_records.settle_language(note, 'zh')
        parent = note['thread'][-1]['message_id']
        note['messages'] += [{'id':'r-overlay-q','parent':parent,'role':'user','text':'Pending','pending':True},
            {'id':'r-overlay-a','parent':'r-overlay-q','role':'assistant','text':'','pending':True}]
        note['thread'] = sync_records.conversation(note, 'r-overlay-a')
        notes.put(PID,note)
        before = {m['id']:m for m in notes.read(PID)['notes'][0]['messages'] if not m.get('pending')}
        notes.cancel_question(PID,note['id'],'r-overlay')
        saved = notes.read(PID)['notes'][0]
        self.assertEqual([t['q'] for t in saved['thread']], ['What is asked first?'])
        self.assertEqual(saved['thread'][0]['lang'], 'zh')
        self.assertEqual({m['id']:m for m in saved['messages']}, before)

    def test_completed_request_is_not_sent_to_model_again(self):
        with mock.patch.object(llm, 'call', return_value=(REPLY, None)) as called:
            self.ask('r-replay'); self.ask('r-replay')
        self.assertEqual(called.call_count, 1)

    def test_parallel_questions_keep_the_parent_context_and_both_answers(self):
        import concurrent.futures, threading
        from textfold import sync_records
        parent = sync_records.messages(NOTE)[-1]['id']
        barrier = threading.Barrier(2)
        def answer(*a, **k):
            barrier.wait(timeout=5)
            return REPLY, None
        with mock.patch.object(llm, 'call', side_effect=answer), concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(notes.ask, PID, NOTE['id'], question, {'id': 'fake'}, req=req, parent=parent)
                       for question, req in [('Phone question', 'r-phone'), ('Laptop question', 'r-laptop')]]
            for f in futures: f.result(timeout=10)
        note = notes.read(PID)['notes'][0]
        self.assertEqual(set(sync_records.branches(note)), {'r-phone-a', 'r-laptop-a'})
        self.assertEqual([t['q'] for t in notes.turns(note, 'r-phone-a')], ['What is asked first?', 'Phone question'])
        self.assertEqual([t['q'] for t in notes.turns(note, 'r-laptop-a')], ['What is asked first?', 'Laptop question'])

    def test_stale_browser_text_and_independent_color_preserve_conflict_revisions(self):
        stale = notes.present(notes.read(PID))['notes'][0]
        changed = {**stale, 'text': 'Remote text', 'color': 'blue'}
        notes.put(PID, changed)
        notes.put(PID, {**stale, 'text': 'Offline text'})
        data = notes.read(PID)
        self.assertEqual(data['notes'][0]['color'], 'blue')
        self.assertEqual({v['text'] for v in data['conflicts'][0]['variants']}, {'Remote text', 'Offline text'})
        self.assertNotIn('_base', data['notes'][0])
        conflict = data['conflicts'][0]
        selected = next(i for i, v in enumerate(conflict['variants']) if v['text'] == 'Offline text')
        notes.resolve(PID, conflict['id'], selected)
        self.assertEqual(notes.read(PID)['notes'][0]['text'], 'Offline text')
        self.assertEqual(notes.read(PID)['notes'][0]['color'], 'blue')
        self.assertEqual(notes.read(PID).get('conflicts'), [])

    def test_deleted_note_is_not_resurrected_by_a_stale_browser(self):
        stale = notes.present(notes.read(PID))['notes'][0]
        notes.delete(PID, stale['id'])
        notes.put(PID, {**stale, 'text': 'Edit made after deletion'})
        data = notes.read(PID)
        self.assertEqual(data['notes'], [])
        self.assertEqual(data['conflicts'][0]['variants'][1]['text'], 'Edit made after deletion')

    def test_cancel_pending_cloud_question_does_not_recreate_it_from_thread(self):
        note = notes.read(PID)['notes'][0]
        from textfold import sync_records
        nodes = sync_records.messages(note); parent = note['thread'][-1]['message_id']
        note['messages'] = nodes + [{'id':'r-cancel-q','parent':parent,'role':'user','text':'Pending','pending':True},
            {'id':'r-cancel-a','parent':'r-cancel-q','role':'assistant','text':'','pending':True}]
        note['thread'] = note['thread'] + [{'q':'Pending','a':'','pending':True}]
        notes.put(PID,note); notes.cancel_question(PID,note['id'],'r-cancel')
        saved = notes.read(PID)['notes'][0]
        self.assertEqual([t['q'] for t in saved['thread']], ['What is asked first?'])
        self.assertFalse(any(m['id'].startswith('r-cancel') for m in saved['messages']))
