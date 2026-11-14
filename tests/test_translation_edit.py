"""A translation put right where it is read (the app's Edit translation), as the text's own words are (Edit text): the
whole translation opened as text at the words selected, saved as written, then kept as it is or its paragraphs'
shorter levels written again by the model from the sentences as corrected. Before, Edit text opened the text's own
words at the top, whatever was selected in the translation. The text keeps the translation's shape, one sentence a
line, so that it reads back one to one (Key picks its sentences, a note is found again by them); where it does not,
it says which line. The corrections are kept and laid over the translation when it is made again. A model that stands
in for one, a data folder of its own; no network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import corrections, llm, local, notes, pipeline, store, translate

TEXT = """# Notes on folding

## Introduction

Readers skim long papers, as $x^2$ grows[^1]. They miss the argument of [the method](#method).

A ladder of levels helps them. Each level keeps the one below it.

## Method

We fold each paragraph into a takeaway.

[^1]: A note on skimming.
"""
CFG = {"kind": "api", "id": "fake/translator", "effort": "none"}


class Model:
    """Answers the translation prompts in Chinese (or German: lang), each marker copied where it was, and the prompts
    that write a corrected paragraph's levels again (relevel)."""
    def __init__(self, lang="zh"):
        self.lang, self.prompts = lang, []

    def __call__(self, prompt, schema, model, cache_dir, force=False, timeout=900, system=None):
        if "titles" in schema["properties"]:
            return {"out": {"titles": ["题" for _ in prompt.split("\n\n")[-1].split("\n")]}, "cost": 0}, False
        self.prompts.append(prompt)
        units = re.findall(r"^\[U(\d+)\][^\n]*\n((?:  [^\n]*\n)+)", prompt, re.M)
        if "corrected by hand" in prompt:
            return {"out": {"units": [{"id": f"U{n}", "takeaway": "改过之后的要点", "reason": "", "topic": "改过"} for n, _ in units]}, "cost": 0}, False
        de = self.lang == "de"
        out = {"section": {"title": "Einleitung" if de else "引言", "takeaway": "Lesen" if de else "关于折叠的要点", "topic": "Lesen" if de else "折叠"},
               "units": [], "paper_title": "Notizen" if de else "关于折叠的笔记"}
        for n, body in units:
            sents = re.findall(r"  \d+: ([^\n]*)", body)
            marks = [" ".join(re.findall(r"⟦[^⟧]*⟧", s)) for s in sents]
            out["units"].append({"id": f"U{n}", "sentences": [(f"Der Satz {k} {m} hilft den Lesern." if de else f"折叠句{k}：{m}读者。") for k, m in enumerate(marks)],
                                 "takeaway": "Lesen hilft" if de else "折叠帮助读者", "reason": "", "topic": "Lesen" if de else "折叠"})
        return {"out": out, "cost": 0}, False


class Corrections(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        p = mock.patch.object(store, "DATA", self.tmp / "data")
        p.start()
        self.addCleanup(p.stop)
        self.pid = local.import_text(TEXT)

    def translated(self, lang="zh"):
        doc = store.assemble(self.pid)
        with mock.patch.object(llm, "call", Model(lang)):
            translate.build(doc, lang, CFG, self.tmp / "cache", jobs=1, log=lambda *a: None)
        store.save_en(doc, "fake/levels")
        store.save_tr(doc, lang, store.read(self.pid, "en")["version"], "fake/translator")
        return doc

    def stored(self, lang="zh"):
        return corrections._doc(self.pid, lang)[0]

    def first(self, doc):
        return next(uid for c in doc["chunks"] for uid in c["units"] if "zh" in doc["units"][uid].get("tr", {}))

    def test_a_translation_reads_back_as_the_same_tokens(self):
        for lang in ("zh", "de"):
            doc = self.translated(lang)
            for uid, u in doc["units"].items():
                if lang in u.get("tr", {}):
                    self.assertEqual(translate.assemble(u, translate.said(u["tr"][lang]), doc["atoms"], lang, self.fail), u["tr"][lang])

    def test_the_whole_translation_one_sentence_a_line_at_the_words_selected(self):
        doc = self.translated()
        uid = self.first(doc)
        got = corrections.text_of(self.pid, "zh", uid, [1])
        blocks = got["text"].split("\n\n")
        self.assertEqual(blocks[0], "# 关于折叠的笔记")
        self.assertEqual(blocks[1], "## 引言")
        self.assertEqual(blocks[2].split("\n"), ["折叠句0：$x^2$ ⟦a1⟧读者。", "折叠句1：⟦a2 the method⟧读者。"])
        a, b = got["at"]
        self.assertEqual(got["text"][a:b], "折叠句1：⟦a2 the method⟧读者。")

    def test_unchanged_it_changes_nothing(self):
        self.translated()
        before = store.read(self.pid, "tr-zh")
        self.assertEqual(corrections.save(self.pid, "zh", corrections.text_of(self.pid, "zh")["text"])["changed"], 0)
        self.assertEqual(store.read(self.pid, "tr-zh"), before)

    def test_a_sentence_and_a_title_put_right(self):
        doc = self.translated()
        uid = self.first(doc)
        text = corrections.text_of(self.pid, "zh")["text"]
        text = text.replace("折叠句0：$x^2$ ⟦a1⟧读者。", "随着 $x^2$ 增长 ⟦a1⟧，读者略读长论文。", 1).replace("## 引言", "## 导言", 1)
        self.assertEqual(corrections.save(self.pid, "zh", text)["changed"], 2)
        doc = self.stored()
        said = translate.said(doc["units"][uid]["tr"]["zh"])["sentences"]
        self.assertEqual(said[0], "随着 ⟦a0⟧ 增长 ⟦a1⟧，读者略读长论文。")   # (the formula is the text's own formula again)
        self.assertEqual(said[1], "折叠句1：⟦a2⟧读者。")
        self.assertEqual(next(c for c in doc["chunks"] if c["title"] == "Introduction")["tr"]["zh"]["title"], "导言")
        self.assertEqual(store.read(self.pid, "tr-zh").get("relevel"), None)   # (saved only: its levels as they were)

    def test_it_says_where_the_text_no_longer_reads_back(self):
        self.translated()
        text = corrections.text_of(self.pid, "zh")["text"]
        cases = [
            (text.replace("折叠句0：$x^2$ ⟦a1⟧读者。\n", "折叠句0：$x^2$ ⟦a1⟧读者。", 1), "lines", 5, (2, 1)),   # (two sentences, one line)
            (text.replace("## 引言\n\n", "", 1), "head", 3, None),
            (text + "\n一个新的段落。\n", "more", None, None),
            (text.replace("⟦a2 the method⟧", "the method", 1), "markers", 6, None),
        ]
        for edited, what, line, counts in cases:
            with self.subTest(what):
                with self.assertRaises(corrections.Shape) as e:
                    corrections.save(self.pid, "zh", edited)
                self.assertEqual(e.exception.what, what)
                if line:
                    self.assertEqual(e.exception.line, line)
                if counts:
                    self.assertEqual((e.exception.want, e.exception.got), counts)

    def test_saved_to_be_regenerated_its_levels_are_written_again_from_the_sentences(self):
        doc = self.translated()
        uid = self.first(doc)
        text = corrections.text_of(self.pid, "zh")["text"].replace("折叠句1：⟦a2 the method⟧读者。", "他们错过了 ⟦a2⟧ 的论证。", 1)
        self.assertEqual(corrections.save(self.pid, "zh", text, relevel=True)["relevel"], 1)
        self.assertEqual(store.read(self.pid, "tr-zh")["relevel"], [uid])
        model = Model()
        with mock.patch.object(llm, "call", model):
            pipeline.generate(self.pid, "zh", CFG)
        self.assertEqual(len(model.prompts), 1)   # (that paragraph's levels alone)
        self.assertIn("他们错过了 ⟦a2", model.prompts[0])
        z = self.stored()["units"][uid]["tr"]["zh"]
        self.assertEqual(translate.said(z)["sentences"][1], "他们错过了 ⟦a2⟧ 的论证。")
        self.assertEqual(translate.said(z)["takeaway"], "改过之后的要点")
        self.assertNotIn("relevel", store.read(self.pid, "tr-zh"))

    def test_corrections_hold_when_the_translation_is_made_again(self):
        doc = self.translated()
        uid = self.first(doc)
        text = corrections.text_of(self.pid, "zh")["text"].replace("折叠句1：⟦a2 the method⟧读者。", "他们错过了 ⟦a2⟧ 的论证。", 1).replace("## 引言", "## 导言", 1)
        corrections.save(self.pid, "zh", text)
        with mock.patch.object(llm, "call", Model()):
            pipeline.generate(self.pid, "zh", CFG, force=True)
        doc = self.stored()
        said = translate.said(doc["units"][uid]["tr"]["zh"])["sentences"]
        self.assertEqual(said[1], "他们错过了 ⟦a2⟧ 的论证。")
        self.assertEqual(said[0], "折叠句0：⟦a0⟧ ⟦a1⟧读者。")   # (the rest as the model wrote it)
        self.assertEqual(next(c for c in doc["chunks"] if c["title"] == "Introduction")["tr"]["zh"]["title"], "导言")

    def test_a_correction_goes_when_the_text_it_translates_changed(self):
        doc = self.translated()
        uid = self.first(doc)
        corrections.save(self.pid, "zh", corrections.text_of(self.pid, "zh")["text"].replace("折叠句1：⟦a2 the method⟧读者。", "他们错过了 ⟦a2⟧ 的论证。", 1))
        doc = self.stored()
        u = doc["units"][uid]
        u["toks"] = u["toks"][:-3] + [{"t": "other"}, {"t": "."}]   # (its text edited)
        u["tr"]["zh"] = translate.assemble(u, {"sentences": ["甲 ⟦a0⟧ ⟦a1⟧。", "乙 ⟦a2⟧。"], "takeaway": "要点", "reason": "", "topic": "要点"}, doc["atoms"], "zh", self.fail)
        corrections.apply(self.pid, doc, "zh")
        self.assertEqual(translate.said(u["tr"]["zh"])["sentences"], ["甲 ⟦a0⟧ ⟦a1⟧。", "乙 ⟦a2⟧。"])

    def test_a_note_in_the_translation_stays_on_its_words(self):
        doc = self.translated()
        uid = self.first(doc)
        z = doc["units"][uid]["tr"]["zh"]
        t0, t1 = corrections._found(z["toks"], doc["atoms"], "读者", notes._spans(z["toks"], doc["atoms"])[1][z["sents"][1][0]][0])
        notes.put(self.pid, {"id": "n-1", "kind": "highlight", "text": "", "ask": None,
                             "anchor": {"unit": uid, "section": None, "lang": "zh", "level": 4, "tokens": [t0, t1], "sents": [1],
                                        "quote": {"exact": "读者", "prefix": "", "suffix": ""}}})
        text = corrections.text_of(self.pid, "zh")["text"]
        corrections.save(self.pid, "zh", text.replace("折叠句0：$x^2$ ⟦a1⟧读者。", "一个更长得多的第一句 $x^2$ ⟦a1⟧，在前面加了很多字。", 1))
        a = notes.read(self.pid)["notes"][0]["anchor"]
        z = self.stored()["units"][uid]["tr"]["zh"]
        self.assertEqual(notes._quoted(z["toks"], doc["atoms"], *a["tokens"]), "读者")
        self.assertGreater(a["tokens"][0], t0)
        self.assertEqual(a["sents"], [1])

    def test_the_texts_own_language_has_no_translation_to_correct(self):
        self.translated()
        with self.assertRaisesRegex(ValueError, "no-translation"):
            corrections.text_of(self.pid, "en")


if __name__ == "__main__":
    unittest.main()
