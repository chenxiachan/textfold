"""A PDF converted again (accurately, POST /api/convert): the generations of its old text stop, and write nothing (a
translation of the old paragraphs, laid on the new ones by their places, showed each at another paragraph, outdated);
after the conversion the document is generated again in every language it had or was being generated in, not only
the one the page showed when the button was pressed. The reader's notes on it follow their passages into the new text,
whose paragraphs are named by their new places. In a data folder of its own; no model is called, the engine is not
run (the PDFs are read by TextFold's own reader, written by hand), and nothing is fetched.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pdftext import typeset   # noqa: E402
from test_replace import TEXT, fake_model   # noqa: E402

from textfold import llm, local, notes, pdfdoc, pdftext, pipeline, server, store   # noqa: E402
from textfold.notes import _at, _spans   # noqa: E402

SPEC = {"id": "fake/model", "kind": "api", "protocol": "openai", "base": "http://127.0.0.1:9", "api_key": "", "model": "fake"}
PID = "pdf-0123456789abcdef"
PAGE = '<article class="ltx_document"><section class="ltx_section" id="s1"><p class="ltx_p">{}</p></section></article>'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(store, "DATA", Path(self.tmp.name)),
                  mock.patch.object(server.models, "load_settings", return_value={})):   # (not the reader's own)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def stored(self, langs=("zh",)):
        """A converted PDF with its English and translations, as a fast conversion left it."""
        d = store.pdir(PID)
        d.mkdir(parents=True)
        (d / "meta.json").write_text('{"id": "%s", "title": "A paper", "source": {"kind": "pdf", "name": "a.pdf", "tier": "fast"}}' % PID)
        (d / "source.html").write_text(PAGE.format("The fast conversion's text."), encoding="utf-8")
        (d / "source.pdf").write_bytes(b"%PDF-1.4")
        store.write(PID, "en", {"version": "v1", "units": {}, "chunks": {}, "edges": []})
        for lang in langs:
            store.write(PID, f"tr-{lang}", {"version": "v2", "base": "v1", "units": {}, "chunks": {}})
        return d


class Pipeline(Base):
    def test_a_translation_stopped_before_it_is_written_is_not(self):
        pid = local.import_text(TEXT, "diary.md")
        stop = threading.Event()

        def build(*a, **kw):   # its last answer came as the document was converted again
            stop.set()
        with mock.patch.object(llm, "_once", fake_model([])), mock.patch.dict(os.environ):
            os.environ.pop("TEXTFOLD_NO_MODELS", None)   # (the build runs the tests with it: the model here is a stand-in)
            pipeline.generate(pid, "en", SPEC)
            with mock.patch.object(pipeline.translate, "build", build), self.assertRaises(llm.Stopped):
                pipeline.generate(pid, "zh", SPEC, stop=stop)
        self.assertIsNone(store.read(pid, "tr-zh"))
        self.assertIsNotNone(store.read(pid, "en"))


class Superseded(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server.threading, "Thread")   # (no workers: the jobs stay as they are put)
        p.start()
        self.addCleanup(p.stop)
        self.jobs = server.Jobs()

    def test_its_generations_stop_and_its_languages_are_given(self):
        d = self.stored(langs=("de",))
        meta = json.loads((d / "meta.json").read_text())
        (d / "meta.json").write_text(json.dumps({**meta, "pending": ["en", "fr"]}))   # its text edited when it was in French too
        running = self.jobs.submit(PID, "zh", False)
        running["status"] = "running"
        queued = self.jobs.submit(PID, "ja", False)
        other = self.jobs.submit("2401.00001", "zh", False)
        self.assertEqual(self.jobs.superseded(PID), ["en", "de", "fr", "zh", "ja"])
        self.assertTrue(running["stopping"] and self.jobs.stops[running["id"]].is_set())
        self.assertEqual(queued["status"], "stopped")
        self.assertEqual(other["status"], "queued")   # another paper's goes on
        again = self.jobs.submit(PID, "zh", False)
        self.assertNotEqual(again["id"], running["id"])   # a new one, of the new text: not the one being stopped


class Convert(Base):
    def test_its_old_translation_stops_and_every_language_is_made_again(self):
        d = self.stored()
        calls, started = [], threading.Event()

        def generate(pid, lang, spec, force=False, progress=None, log=None, stop=None, shown=None):
            calls.append((lang, (d / "source.html").read_text(encoding="utf-8")))
            if len(calls) == 1:   # the old text's translation, long under way when the conversion ends
                started.set()
                if not stop.wait(10):
                    raise AssertionError("the old text's generation went on")
                raise llm.Stopped()

        def store_pdf(pid, folder, sc, name, tier, replace=False):   # the accurate text, in place of the fast one
            self.assertTrue(replace)
            shutil.rmtree(d / "layers")
            (d / "source.html").write_text(PAGE.format("The accurate conversion's text."), encoding="utf-8")
        for p in (mock.patch.object(server.pipeline, "generate", generate), mock.patch.object(server.models, "resolve", return_value=SPEC),
                  mock.patch.object(server.engine, "convert"), mock.patch.object(server.pdfdoc, "read_output", return_value={}),
                  mock.patch.object(server.pdfdoc, "store_pdf", store_pdf)):
            p.start()
            self.addCleanup(p.stop)
        jobs = server.Jobs()
        old = jobs.submit(PID, "zh", False)
        self.assertTrue(started.wait(10))
        conv = jobs.convert(PID, d / "source.pdf", "a.pdf", "en", "accurate", replace=True)   # pressed while it showed English
        end = time.time() + 20
        while any(j["status"] in ("queued", "running") for j in jobs.items.values()) and time.time() < end:
            time.sleep(0.05)
        self.assertEqual((old["status"], conv["status"]), ("stopped", "done"))
        self.assertEqual([lang for lang, _ in calls], ["zh", "en", "zh"])
        self.assertTrue(all("accurate" in text for _, text in calls[1:]))
        self.assertEqual(jobs.items[conv["next"]]["lang"], "en")



class Converted(Base):
    HEAT = "Heat pumps move heat from a cold place to a warm one, and the compressor does the work."
    TANK = "The buffer tank stores heat for the night, so that the pump can rest while the price is high."
    HOUSE = "A paragraph the accurate conversion found before the others, about the house and its walls."

    def setUp(self):
        super().setUp()
        try:
            import pdf_inspector   # noqa: F401
        except ImportError:
            self.skipTest("pdf-inspector is not installed")

    def store(self, paragraphs, replace):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.pdf"
            f.write_bytes(typeset([(20, "Heat at Home"), (14, "1 Heating")] + [(11, t) for t in paragraphs]))
            out = pdftext.convert(f, Path(tmp) / "out")
            pdfdoc.store_pdf(PID, out, pdfdoc.read_output(out), "a.pdf", "fast", replace=replace)
        return store.parsed(PID)

    def unit_with(self, doc, words):
        return next(u for u, x in doc["units"].items() if words in _spans(x["toks"], doc["atoms"])[0])


class Notes(Converted):
    def test_a_note_follows_its_passage(self):
        doc = self.store([self.HEAT, self.TANK], replace=False)
        uid = self.unit_with(doc, "buffer tank")
        text, _ = _spans(doc["units"][uid]["toks"], doc["atoms"])
        i = text.index("buffer tank")
        notes.put(PID, {"id": "n-tank0001", "kind": "highlight", "text": "", "thread": [],
                        "anchor": {**_at(doc, uid, i, i + len("buffer tank")), "lang": "en", "level": 4}})
        new = self.store([self.HOUSE, self.HEAT, self.TANK], replace=True)   # converted again: one more paragraph first
        anchor = notes.read(PID)["notes"][0]["anchor"]
        self.assertEqual(anchor["unit"], self.unit_with(new, "buffer tank"))
        self.assertEqual(anchor["unit"], uid)   # its id stays, though its place is another now (ids.py)
        self.assertFalse(anchor.get("lost"))
        self.assertEqual(notes._quoted(new["units"][anchor["unit"]]["toks"], new["atoms"], *anchor["tokens"]), "buffer tank")


class Edited(Converted):
    """A converted PDF's text put right by the reader (a word the OCR read wrong; local.replace_text): the file's own
    title and source, the file and its places on the pages stay, and every paragraph keeps its id and its notes."""
    WRONG = "Heat pumps move heat from a cold place to a warm one, and the cornpressor does the work."

    def test_a_word_read_wrong_is_put_right_in_place(self):
        doc = self.store([self.WRONG, self.TANK], replace=False)
        heat, tank = self.unit_with(doc, "cornpressor"), self.unit_with(doc, "buffer tank")
        text, _ = _spans(doc["units"][tank]["toks"], doc["atoms"])
        i = text.index("buffer tank")
        notes.put(PID, {"id": "n-tank0001", "kind": "highlight", "text": "", "thread": [],
                        "anchor": {**_at(doc, tank, i, i + len("buffer tank")), "lang": "en", "level": 4}})
        store.write(PID, "en", {"version": "v1", "units": {}, "chunks": {}, "edges": []})
        d = store.pdir(PID)
        (d / "source.pdf").write_bytes(b"%PDF-1.4")   # (kept beside it by whoever converted it: pdftext.store, the server)
        (d / "layout.json").write_text(json.dumps([{"p": 1, "b": [0.1, 0.2, 0.9, 0.25], "t": self.WRONG},   # (the engine's
                                                   {"p": 1, "b": [0.1, 0.3, 0.9, 0.35], "t": self.TANK}]))    # places)
        was = (d / "meta.json").read_text(encoding="utf-8")
        md = local.text_of(PID)
        self.assertIn("cornpressor", md)
        self.assertEqual(local.replace_text(PID, md.replace("cornpressor", "compressor")), "Heat at Home")
        new = store.parsed(PID)
        self.assertEqual(self.unit_with(new, "the compressor does"), heat)   # its id stays
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual({**meta, "replaced": None, "pending": None}, {**json.loads(was), "replaced": None, "pending": None})   # the
        self.assertTrue(meta["replaced"])                                                                                     # file's title, source...
        self.assertEqual(meta["pending"], ["en"])   # its levels, to be made again
        self.assertTrue(all((d / f).exists() for f in ("source.pdf", "layout.json", "structured_content.json")))
        self.assertEqual(pdfdoc.place(PID)[heat]["boxes"], [[0.1, 0.2, 0.9, 0.25]])   # still found where it is on its page
        anchor = notes.read(PID)["notes"][0]["anchor"]
        self.assertEqual((anchor["unit"], anchor.get("lost")), (tank, None))
        self.assertIsNone(store.read(PID, "en"))   # its levels were the old text's
        self.assertEqual(local.original(PID), md)   # the text read wrong, kept in its history

    def test_converted_again_its_edit_history_goes_with_its_levels(self):
        self.store([self.WRONG, self.TANK], replace=False)
        local.replace_text(PID, local.text_of(PID).replace("cornpressor", "compressor"))
        self.assertIsNotNone(local.original(PID))
        self.store([self.HOUSE, self.WRONG, self.TANK], replace=True)   # converted again (accurately): a new text
        self.assertIn("cornpressor", local.text_of(PID))
        self.assertIsNone(local.original(PID))


if __name__ == "__main__":
    unittest.main()
