"""A language translated for the first time is shown as it is made, section by section (asked for: "a section shown
once it is translated", not the whole text at the end). Each section is put in as soon as all its parts are back,
and the translation so far is written, marked partial, for the page to show (pipeline: shown, the sections written).
A translation stopped half way is a partial one, made whole the next time the language is generated; one made again
stays as it was until the new one is whole. A model that stands in for one, a data folder of its own; no network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from textfold import ladder, llm, local, pipeline, store, translate

TEXT = "# Folding in sections\n\n" + "\n".join(
    f"## Section {k}\n\nReaders of section {k} skim. They miss the argument of section {k}.\n\nA second paragraph of section {k}.\n"
    for k in range(1, 5))
CFG = {"kind": "api", "id": "fake/translator", "effort": "none"}


def levels(prompt, schema, model, cache_dir, force=False, timeout=900, system=None):
    units = re.findall(r"^\[U(\d+)\]", prompt, re.M)
    return {"out": {"section": {"takeaway": "Readers skim", "topic": "Readers"},
                    "units": [{"id": f"U{n}", "takeaway": "", "reason": "", "topic": "", "ev": [0], "role": "claim"} for n in units]}, "cost": 0}, False


class Model:
    """Answers each section in Chinese; asked: the sections, in the order asked."""
    def __init__(self):
        self.asked, self.lock = [], threading.Lock()

    def __call__(self, prompt, schema, model, cache_dir, force=False, timeout=900, system=None):
        if "titles" in schema["properties"]:
            return {"out": {"titles": ["题" for _ in prompt.split("\n\n")[-1].split("\n")]}, "cost": 0}, False
        sec = re.search(r"^This section: (.*)$", prompt, re.M).group(1)
        with self.lock:
            self.asked.append(sec)
        units = re.findall(r"^\[U(\d+)\][^\n]*\n((?:  \d+: [^\n]*\n)+)", prompt, re.M)
        return {"out": {"section": {"title": f"节 {sec}", "takeaway": "要点", "topic": "要点"},
                        "units": [{"id": f"U{n}", "sentences": [f"{sec} 译文{k}。" for k, _ in enumerate(re.findall(r"  \d+: ", body))],
                                   "takeaway": "要点", "reason": "", "topic": "要点"} for n, body in units]}, "cost": 0}, False


class Shown(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (mock.patch.object(store, "DATA", self.tmp / "data"), mock.patch.object(pipeline, "SHOW_EVERY", 0)):
            p.start()
            self.addCleanup(p.stop)
        self.pid = local.import_text(TEXT)
        doc = store.assemble(self.pid)
        with mock.patch.object(llm, "call", levels):
            ladder.build(doc, CFG, self.tmp / "cache", jobs=1, log=lambda *a: None)
        store.save_en(doc, "fake/levels")
        self.saved = []
        real = store.save_tr

        def save_tr(doc, lang, base, model, partial=False):
            self.saved.append((partial, sorted(c["id"] for c in doc["chunks"] if lang in c.get("tr", {}))))
            return real(doc, lang, base, model, partial)
        p = mock.patch.object(store, "save_tr", save_tr)
        p.start()
        self.addCleanup(p.stop)

    def sections(self):
        return [c["id"] for c in store.parsed(self.pid)["chunks"] if c["units"]]

    def generate(self, model, **kw):
        with mock.patch.object(llm, "call", model):
            return pipeline.generate(self.pid, "zh", CFG, jobs=1, **kw)

    def test_each_section_is_written_as_soon_as_it_is_translated(self):
        shown = []
        self.generate(Model(), shown=shown.append)
        n = len(self.sections())
        partial = [ids for p, ids in self.saved if p]
        self.assertEqual([len(ids) for ids in partial], list(range(1, n + 1)))   # one more section each time
        self.assertEqual(shown, list(range(1, n + 1)))
        self.assertEqual(self.saved[-1][0], False)   # the whole one, last
        tr = store.read(self.pid, "tr-zh")
        self.assertNotIn("partial", tr)
        self.assertEqual(translate.gaps(store.assemble(self.pid), "zh"), [])

    def test_a_translation_stopped_half_way_is_made_whole_next_time(self):
        stop = threading.Event()
        with self.assertRaises(llm.Stopped):
            self.generate(Model(), stop=stop, shown=lambda n: stop.set())
        tr = store.read(self.pid, "tr-zh")
        self.assertTrue(tr["partial"])
        self.assertEqual(len(tr["chunks"]), 1)
        doc = store.assemble(self.pid)
        self.assertIn("zh", doc["meta"]["langs"])   # (read as far as it goes: the rest in the text's own words)
        self.assertGreater(doc["meta"]["gaps"]["zh"], 0)
        self.generate(Model())
        tr = store.read(self.pid, "tr-zh")
        self.assertNotIn("partial", tr)
        self.assertEqual(sorted(tr["chunks"]), sorted(self.sections()))

    def test_a_translation_made_again_stays_as_it_was_until_it_is_whole(self):
        self.generate(Model())
        self.saved.clear()
        shown = []
        self.generate(Model(), force=True, shown=shown.append)
        self.assertEqual([p for p, _ in self.saved], [False])
        self.assertEqual(shown, [])

    def test_a_sections_parts_are_put_in_together(self):
        doc = store.assemble(self.pid)
        seen = []

        def sink():
            seen.append([c["id"] for c in doc["chunks"] if "zh" in c.get("tr", {})])
        with mock.patch.object(translate, "PART_CHARS", 60), mock.patch.object(llm, "call", Model()):
            tasks = [(c["id"], k) for c in doc["chunks"] if c["units"] for k, _ in enumerate(translate.parts(doc, c))]
            translate.build(doc, "zh", CFG, self.tmp / "cache", jobs=3, log=lambda *a: None, sink=sink)
        self.assertGreater(len(tasks), len(self.sections()))   # (each section asked in parts)
        self.assertEqual(len(seen), len(self.sections()))   # (told once a section, when its last part is in)
        for ids in seen:
            for cid in ids:
                c = next(c for c in doc["chunks"] if c["id"] == cid)
                self.assertTrue(all("zh" in doc["units"][uid].get("tr", {}) for uid in c["units"]))


if __name__ == "__main__":
    unittest.main()
