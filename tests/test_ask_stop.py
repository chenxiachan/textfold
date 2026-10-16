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
