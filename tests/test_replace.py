"""A Markdown document's text replaced in place (local.replace_text, POST /api/import with an id): the same id, folder
and notes; paragraphs named by their words (parse.py); notes found again in the new text (notes.reanchor); the levels
made again asking the model only for what changed (pipeline.py). In a data folder of its own; no model is called (a
fake answers in its place) and nothing is fetched.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import re
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from textfold import llm, local, markdown, notes, pipeline, semantic, server, store
from textfold.notes import _at, _spans
from textfold.parse import parse

TEXT = """# Heat pump diary

What the house did in the cold week.

## Monday

The heat pump ran all night. Its COP stayed above three, even at minus five.

The buffer tank held its heat until the morning. Nobody felt the change.

Same line again.

Same line again.

## Tuesday

A cold front arrived early. The buffer tank emptied by noon and the backup heater started.

The neighbours asked how loud it was.
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.object(store, "DATA", Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def units(self, pid):
        doc = store.parsed(pid)
        return doc, {c["id"]: c["units"] for c in doc["chunks"]}

    def note(self, pid, nid, uid, words, lang="en", kind="highlight", thread=()):
        """A note on words of a paragraph, anchored as the reader anchors a selection."""
        doc = store.parsed(pid)
        text, _ = _spans(doc["units"][uid]["toks"], doc["atoms"])
        i = text.index(words)
        anchor = {**_at(doc, uid, i, i + len(words)), "lang": lang, "level": 4}
        notes.put(pid, {"id": nid, "kind": kind, "anchor": anchor, "text": "", "thread": list(thread)})
        return anchor

    def notes(self, pid):
        return {n["id"]: n for n in notes.read(pid)["notes"]}

    def words_at(self, pid, anchor):
        """The words an anchor's tokens mark in the stored text, as the reader quotes them."""
        doc = store.parsed(pid)
        return notes._quoted(doc["units"][anchor["unit"]]["toks"], doc["atoms"], *anchor["tokens"])


class Ids(Base):
    def test_a_paragraph_is_named_by_its_words(self):
        pid = local.import_text(TEXT, "diary.md")
        _, u = self.units(pid)
        self.assertTrue(all(x.startswith("p-") for c in u.values() for x in c))
        self.assertEqual(u["sec-monday"][2] + "-2", u["sec-monday"][3])   # the same words twice: the second counted
        edited = TEXT.replace("## Monday\n", "## Monday\n\nA paragraph put first.\n").replace("Nobody felt", "No one felt")
        local.replace_text(pid, edited)
        _, v = self.units(pid)
        self.assertEqual(v["sec-monday"][1], u["sec-monday"][0])          # moved down one place: the same id
        self.assertEqual(v["sec-monday"][2], u["sec-monday"][1])          # edited: the same id too (ids.py)
        self.assertNotIn(v["sec-monday"][0], [x for c in u.values() for x in c])   # new: an id of its own
        self.assertEqual(v["sec-monday"][3:], u["sec-monday"][2:])
        self.assertEqual(v["sec-tuesday"], u["sec-tuesday"])

    def test_arxiv_and_pdf_ids_are_as_they_were(self):
        page = ('<article class="ltx_document"><section class="ltx_section" id="S1"><h2 class="ltx_title">1 Intro</h2>'
                '<div class="ltx_para" id="S1.p1"><p class="ltx_p" id="S1.p1.1">Words with an id.</p></div>'
                '<div class="ltx_para"><p class="ltx_p">Words without one.</p></div></section></article>')
        doc = parse(page, {"id": "2512.23916", "title": "A paper"})
        self.assertEqual(list(doc["units"]), ["S1.p1.1", "u1"])
        html = semantic.normalize(markdown.to_html("## One\n\nFirst words.\n\nSecond words.\n")[0])
        doc = parse(html, {"id": "pdf-0123456789abcdef", "title": "A PDF", "source": {"kind": "pdf"}})
        self.assertEqual(list(doc["units"]), ["u0", "u1"])

    def test_a_document_stored_before_keeps_its_ids_when_it_is_replaced(self):
        pid = local.import_text(TEXT, "diary.md")
        meta = json.loads((store.pdir(pid) / "meta.json").read_text(encoding="utf-8"))
        del meta["unit_ids"]   # as an earlier TextFold stored it: paragraphs named by their places, no ids in the page
        (store.pdir(pid) / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        src = store.pdir(pid) / "source.html"
        src.write_text(re.sub(r' data-[us]="[^"]*"', "", src.read_text(encoding="utf-8")), encoding="utf-8")
        _, u = self.units(pid)
        self.assertEqual(u["sec-monday"][0], "u1")
        self.note(pid, "n-cop", "u1", "Its COP stayed above three")
        ask = {"q": "Why?", "a": "Because it is mild [[u1]] and [[?]].", "cites": ["u1"]}
        self.note(pid, "n-ask", "u2", "held its heat", kind="ask", thread=[ask])
        local.replace_text(pid, TEXT.replace("## Tuesday", "## Tuesday\n\nA new line."))
        _, v = self.units(pid)
        got = self.notes(pid)
        self.assertEqual(v["sec-monday"][:2], ["u1", "u2"])   # the ids it had, so links made before still land (ids.py)
        self.assertEqual(got["n-cop"]["anchor"]["unit"], "u1")
        self.assertEqual(got["n-ask"]["anchor"]["unit"], "u2")
        self.assertEqual(got["n-ask"]["thread"][0]["cites"], ["u1"])
        self.assertEqual(got["n-ask"]["thread"][0]["a"], "Because it is mild [[u1]] and [[?]].")
        self.assertNotIn(v["sec-tuesday"][0], [x for c in u.values() for x in c])   # the new line: an id of its own


class Replace(Base):
    def test_the_id_notes_and_folder_stay(self):
        pid = local.import_text(TEXT, "diary.md")
        d = store.pdir(pid)
        _, u = self.units(pid)
        self.note(pid, "n-cop", u["sec-monday"][0], "Its COP stayed above three")
        store.write(pid, "en", {"version": "2026-10-01T10:00:00", "model": "fake", "units": {}, "chunks": {}, "edges": []})
        (d / "llm-cache").mkdir()
        (d / "llm-cache" / "abc.json").write_text("{}", encoding="utf-8")
        edited = TEXT.replace("# Heat pump diary", "# Heat pump diary, week 2").replace("emptied by noon", "emptied at noon")
        title = local.replace_text(pid, edited)
        self.assertEqual(title, "Heat pump diary, week 2")
        self.assertEqual((d / "source.md").read_text(encoding="utf-8"), edited)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["id"], meta["title"], meta["unit_ids"]), (pid, title, "text"))
        self.assertTrue(meta["replaced"])
        self.assertEqual(list(self.notes(pid)), ["n-cop"])
        self.assertEqual(notes.read(pid)["paper"]["title"], title)
        self.assertTrue((d / "llm-cache" / "abc.json").exists())   # the model's replies stay
        self.assertFalse((d / "layers").exists())                  # the levels went with the old text
        self.assertTrue((d / "previous" / "layers" / "en.json").exists())
        self.assertEqual((d / "previous" / "source.md").read_text(encoding="utf-8"), TEXT)
        self.assertEqual(json.loads((d / "previous" / "notes.json").read_text(encoding="utf-8"))["notes"][0]["id"], "n-cop")
        self.assertEqual(store.papers()[0]["id"], pid)

    def test_only_a_markdown_document_is_replaced(self):
        with self.assertRaises(LookupError):
            local.replace_text("md-not-there-12345678", TEXT)
        d = store.pdir("2512.23916")
        d.mkdir(parents=True)
        (d / "meta.json").write_text("{}", encoding="utf-8")
        (d / "source.html").write_text("<article></article>", encoding="utf-8")
        with self.assertRaises(LookupError):
            local.replace_text("2512.23916", TEXT)
        pid = local.import_text(TEXT, "diary.md")
        with self.assertRaises(ValueError):
            local.replace_text(pid, TEXT, "diary.docx")
        self.assertEqual((store.pdir(pid) / "source.md").read_text(encoding="utf-8"), TEXT)


class Notes(Base):
    def test_an_inserted_paragraph_leaves_every_note_on_its_paragraph(self):
        pid = local.import_text(TEXT, "diary.md")
        _, u = self.units(pid)
        put = {"n-open": (u["abstract"][0], "cold week"), "n-cop": (u["sec-monday"][0], "Its COP stayed above three"),
               "n-tank": (u["sec-monday"][1], "held its heat"), "n-dup": (u["sec-monday"][3], "Same line"),
               "n-front": (u["sec-tuesday"][0], "The buffer tank emptied by noon"), "n-loud": (u["sec-tuesday"][1], "how loud")}
        before = {nid: self.note(pid, nid, uid, words) for nid, (uid, words) in put.items()}
        edited = (TEXT.replace("## Monday\n", "## Monday\n\nA paragraph put first, with Its COP in it.\n")
                  .replace("Same line again.\n\nSame line again.", "Same line again.\n\nA line between.\n\nSame line again.")
                  .replace("## Tuesday\n", "## Tuesday\n\nAnother put first.\n"))
        local.replace_text(pid, edited)
        for nid, n in self.notes(pid).items():
            a, was = n["anchor"], before[nid]
            self.assertEqual(a["unit"], was["unit"], nid)   # the same words, so the same id
            self.assertEqual((a["tokens"], a["sents"], a["quote"]), (was["tokens"], was["sents"], was["quote"]), nid)
            self.assertNotIn("lost", a)
        _, v = self.units(pid)
        self.assertEqual(self.notes(pid)["n-dup"]["anchor"]["unit"], v["sec-monday"][5])   # the second "Same line", still

    def test_a_note_on_an_edited_paragraph_is_found_by_its_quote(self):
        pid = local.import_text(TEXT, "diary.md")
        _, u = self.units(pid)
        self.note(pid, "n-cop", u["sec-monday"][0], "Its COP stayed above three")
        self.note(pid, "n-noon", u["sec-tuesday"][0], "emptied by noon")
        self.note(pid, "n-typo", u["sec-monday"][1], "Nobody felt the change")
        edited = (TEXT.replace("The heat pump ran all night.", "The heat pump, a new one, ran all night long.")
                  .replace("A cold front arrived early. ", "")
                  .replace("Nobody felt the change", "No one felt the change"))
        local.replace_text(pid, edited)
        _, v = self.units(pid)
        got = self.notes(pid)
        a = got["n-cop"]["anchor"]
        self.assertEqual((a["unit"], a["section"], a["sents"]), (v["sec-monday"][0], "sec-monday", [1]))
        self.assertEqual(a["unit"], u["sec-monday"][0])   # edited, its paragraph keeps its id (ids.py)
        self.assertEqual(self.words_at(pid, a), "Its COP stayed above three")
        a = got["n-noon"]["anchor"]
        self.assertEqual((a["unit"], a["sents"], self.words_at(pid, a)), (v["sec-tuesday"][0], [0], "emptied by noon"))
        self.assertEqual(a["quote"]["prefix"], "The buffer tank ")   # the words around it now
        a = got["n-typo"]["anchor"]   # its own words edited: what stands where they stood
        self.assertEqual((a["unit"], self.words_at(pid, a), a["quote"]["exact"]), (v["sec-monday"][1], "No one felt the change", "No one felt the change"))
        self.assertFalse(any("lost" in n["anchor"] for n in got.values()))

    def test_a_note_on_a_paragraph_moved_to_another_section_goes_with_it(self):
        pid = local.import_text(TEXT, "diary.md")
        _, u = self.units(pid)
        was = self.note(pid, "n-loud", u["sec-tuesday"][1], "how loud")
        moved = TEXT.replace("\nThe neighbours asked how loud it was.\n", "\n").replace("## Monday\n", "## Monday\n\nThe neighbours asked how loud it was.\n")
        local.replace_text(pid, moved)
        a = self.notes(pid)["n-loud"]["anchor"]
        self.assertEqual((a["unit"], a["section"], a["tokens"]), (was["unit"], "sec-monday", was["tokens"]))

    def test_a_note_whose_passage_was_deleted_is_kept_and_marked(self):
        pid = local.import_text(TEXT, "diary.md")
        _, u = self.units(pid)
        was = self.note(pid, "n-loud", u["sec-tuesday"][1], "how loud")
        self.note(pid, "n-front", u["sec-tuesday"][0], "The buffer tank emptied by noon")
        cut = (TEXT.replace("\nThe neighbours asked how loud it was.\n", "\n")
               .replace("The buffer tank emptied by noon and the backup heater started.", "It stayed grey."))
        local.replace_text(pid, cut)
        _, v = self.units(pid)
        got = self.notes(pid)
        a = got["n-loud"]["anchor"]   # its paragraph gone: on its section's title, where the reader lists it
        self.assertEqual((a["unit"], a["section"], a["on"], a["tokens"], a["sents"]), (None, "sec-tuesday", "title", None, None))
        self.assertEqual(a["lost"]["unit"], was["unit"])
        self.assertEqual((a["lost"]["tokens"], a["lost"]["sents"]), (was["tokens"], was["sents"]))
        self.assertEqual(a["quote"], was["quote"])
        a = got["n-front"]["anchor"]   # its words gone, its paragraph edited: it stays on that paragraph
        self.assertEqual((a["unit"], a["tokens"], a["lost"]["unit"]), (v["sec-tuesday"][0], None, u["sec-tuesday"][0]))
        self.assertNotIn("on", a)
        local.replace_text(pid, TEXT)   # the text put back: both found again
        _, w = self.units(pid)
        got = self.notes(pid)
        for nid, uid, words in (("n-loud", w["sec-tuesday"][1], "how loud"), ("n-front", w["sec-tuesday"][0], "The buffer tank emptied by noon")):
            a = got[nid]["anchor"]
            self.assertEqual((a["unit"], self.words_at(pid, a)), (uid, words), nid)
            self.assertNotIn("lost", a)
            self.assertNotIn("on", a)

    def test_a_note_made_in_a_translation_follows_its_sentences(self):
        pid = local.import_text(TEXT, "diary.md")
        _, u = self.units(pid)
        anchor = {"unit": u["sec-tuesday"][0], "section": "sec-tuesday", "lang": "zh", "level": 4, "tokens": [9, 14],
                  "sents": [1], "quote": {"exact": "缓冲水箱中午就空了", "prefix": "", "suffix": ""}}
        notes.put(pid, {"id": "n-zh", "kind": "highlight", "anchor": anchor, "text": "", "thread": []})
        local.replace_text(pid, TEXT.replace("A cold front arrived early.", "A cold front came in before dawn."))
        _, v = self.units(pid)
        a = self.notes(pid)["n-zh"]["anchor"]
        self.assertEqual((a["unit"], a["sents"], a["tokens"], a["quote"]["exact"]), (v["sec-tuesday"][0], [1], None, "缓冲水箱中午就空了"))

    def test_notes_on_sections_stay_or_follow_their_paragraphs(self):
        pid = local.import_text(TEXT, "diary.md")
        for nid, sid in (("n-title", "sec-monday"), ("n-summary", "sec-tuesday")):
            notes.put(pid, {"id": nid, "kind": "note", "text": "mine", "thread": [], "anchor": {
                "unit": None, "section": sid, "on": "title" if nid == "n-title" else "summary", "lang": "en", "level": 1,
                "tokens": None, "sents": None, "quote": {"exact": "x", "prefix": "", "suffix": ""}}})
        notes.put(pid, {"id": "n-chat", "kind": "ask", "text": "", "thread": [], "anchor": {
            "unit": None, "section": None, "on": "paper", "lang": "en", "level": 2, "tokens": None, "sents": None, "quote": {"exact": ""}}})
        local.replace_text(pid, TEXT.replace("## Monday", "## Monday, the first day"))
        got = self.notes(pid)
        self.assertEqual(got["n-title"]["anchor"]["section"], "sec-monday")   # renamed, it keeps its id (ids.py)
        self.assertEqual(got["n-summary"]["anchor"]["section"], "sec-tuesday")
        self.assertEqual((got["n-chat"]["anchor"]["section"], got["n-chat"]["anchor"]["on"]), (None, "paper"))


def fake_model(prompts):
    """In the model's place: an answer of the right shape for a section's levels or for the links, each prompt kept.
    Its takeaways change with the section's text, as a model's do (the links are asked from the takeaways)."""
    def once(prompt, schema, spec, timeout, system):
        prompts.append(prompt)
        if "Its units in reading order" not in prompt:
            return {"links": []}, 0
        n = prompt.count("\n[U")
        return {"section": {"takeaway": "The section in brief", "topic": "The section"},
                "units": [{"id": f"U{k}", "role": "context", "takeaway": f"Point {k} of {len(prompt)}", "topic": "Point",
                           "reason": "", "evidence": [0]} for k in range(n)]}, 0
    return once


class Refold(Base):
    SPEC = {"id": "fake/model", "kind": "api", "protocol": "openai", "base": "http://127.0.0.1:9", "api_key": "", "model": "fake"}

    def test_a_refold_asks_only_for_the_section_that_changed(self):
        pid = local.import_text(TEXT, "diary.md")
        asked = []
        with mock.patch.object(llm, "_once", fake_model(asked)), mock.patch.dict(os.environ):
            os.environ.pop("TEXTFOLD_NO_MODELS", None)   # (the build runs the tests with it: the model here is a stand-in)
            pipeline.generate(pid, "en", self.SPEC)
            self.assertEqual(len(asked), 4)   # three sections and the links
            local.replace_text(pid, TEXT.replace("how loud it was", "how loud it was at night"))
            asked.clear()
            pipeline.generate(pid, "en", self.SPEC, force=True)
            self.assertEqual(len(asked), 2)   # Tuesday, and the links over the whole text
            self.assertIn("at night", asked[0])
            asked.clear()
            pipeline.generate(pid, "en", self.SPEC, force=True)   # folded again: a force is a new sample of all of it
            self.assertEqual(len(asked), 4)
        doc = store.assemble(pid)
        self.assertEqual(doc["meta"]["langs"], ["en"])
        self.assertTrue(all(u["lad"].get("model") == "fake/model" for u in doc["units"].values()))


class FakeJobs:
    def __init__(self):
        self.running = []

    def list(self):
        return [{"pid": pid, "status": "running"} for pid in self.running]


class Server(Base):
    def setUp(self):
        super().setUp()
        settings = mock.patch.object(server.models, "load_settings", return_value={})   # (not the reader's own)
        settings.start()
        self.addCleanup(settings.stop)
        self.jobs = FakeJobs()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.jobs))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_import_with_an_id_cannot_replace_original_source(self):
        code, got = self.post("/api/import", {"name": "diary.md", "text": TEXT})
        self.assertEqual((code, set(got)), (200, {"paper", "title"}))
        pid = got["paper"]
        code, got = self.post("/api/import", {"name": "diary.md", "text": TEXT.replace("# Heat pump diary", "# Diary"), "id": pid})
        self.assertEqual(code, 409)
        self.assertIn('immutable', got['error'])
        self.assertEqual((store.pdir(pid) / "source.md").read_text(encoding="utf-8"), TEXT)
        self.assertEqual(self.post("/api/import", {"text": TEXT, "id": "md-gone-12345678"})[0], 409)
        self.assertEqual(self.post("/api/import", {"text": TEXT, "id": "../papers"})[0], 409)

    def test_no_replace_while_it_is_generated(self):
        pid = self.post("/api/import", {"name": "diary.md", "text": TEXT})[1]["paper"]
        self.jobs.running.append(pid)
        self.assertEqual(self.post("/api/import", {"text": "# Other\n\nWords.", "id": pid})[0], 409)
        self.assertEqual((store.pdir(pid) / "source.md").read_text(encoding="utf-8"), TEXT)


if __name__ == "__main__":
    unittest.main()
