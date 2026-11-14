"""A translation put right where it is read (the app's Edit text, in a translation): a sentence of it rewritten, or a
term (a name the translation got wrong) written otherwise wherever it occurs. Before, Edit text opened the text's own
words at the top, whatever was selected in the translation. Now the translation itself is corrected, its levels made
again of its sentences, the notes made in it on their words; the corrections are kept and laid over the translation
when it is made again. A model that stands in for one, a data folder of its own; no network.

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

Readers skim long papers, as $x^2$ grows. They miss the argument of folding.

A ladder of levels helps them. Each level keeps the one below it.
"""
CFG = {"kind": "api", "id": "fake/translator", "effort": "none"}


class Model:
    """Answers the translation prompts in Chinese (or German: lang), the term 折叠 (Faltung) in every line, each
    marker copied where it was."""
    def __init__(self, lang="zh"):
        self.lang, self.prompts = lang, []

    def __call__(self, prompt, schema, model, cache_dir, force=False, timeout=900, system=None):
        if "titles" in schema["properties"]:
            return {"out": {"titles": ["题" for _ in prompt.split("\n\n")[-1].split("\n")]}, "cost": 0}, False
        self.prompts.append(prompt)
        de = self.lang == "de"
        units = re.findall(r"^\[U(\d+)\][^\n]*\n((?:  \d+: [^\n]*\n)+)", prompt, re.M)
        out = {"section": {"title": "Faltung Einleitung" if de else "折叠引言", "takeaway": "Über die Faltung" if de else "关于折叠的要点",
                           "topic": "Faltung" if de else "折叠"},
               "units": [], "paper_title": "Notizen zur Faltung" if de else "关于折叠的笔记"}
        for n, body in units:
            sents = re.findall(r"  \d+: ([^\n]*)", body)
            marks = [" ".join(re.findall(r"⟦[^⟧]*⟧", s)) for s in sents]
            out["units"].append({
                "id": f"U{n}",
                "sentences": [(f"Die Faltung {m} hilft dem Faltungsnetz {k}." if de else f"折叠句{k}：{m}读者。") for k, m in enumerate(marks)],
                "takeaway": "Faltung hilft Lesern" if de else "折叠帮助读者", "reason": "", "topic": "Faltung" if de else "折叠"})
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

    def units(self, doc, lang="zh"):
        return [(uid, u["tr"][lang]) for uid, u in doc["units"].items() if lang in u.get("tr", {})]

    def test_a_translation_reads_back_as_the_same_tokens(self):
        for lang in ("zh", "de"):
            doc = self.translated(lang)
            for uid, z in self.units(doc, lang):
                self.assertEqual(translate.assemble(doc["units"][uid], translate.said(z), doc["atoms"], lang, self.fail), z)

    def test_a_term_is_written_otherwise_wherever_it_occurs(self):
        self.translated()
        self.assertEqual(corrections.rename(self.pid, "zh", "折叠", "折页"), {"places": 12})
        doc = self.stored()
        for uid, z in self.units(doc):
            words = translate.line(z["toks"]) + translate.line(z["lad"]["brief"]) + translate.line(z["lad"]["topic"])
            self.assertNotIn("折叠", words)
            self.assertIn("折页", words)
        c = doc["chunks"][-1]["tr"]["zh"]
        self.assertEqual(c["title"], "折页引言")
        self.assertIn("折页", translate.line(c["lad"]["take"]))
        self.assertEqual(doc["meta"]["titles"]["zh"], "关于折页的笔记")
        first = self.units(doc)[0][1]
        self.assertIn({"a": "a0"}, first["toks"])   # (the formula's marker is still the formula)
        self.assertEqual(corrections.kept(self.pid, "zh")["terms"], [["折叠", "折页"]])

    def test_a_longer_term_is_not_written_twice(self):
        self.translated()
        corrections.rename(self.pid, "zh", "读者", "读者群")
        with self.assertRaises(ValueError):   # (nothing left to change)
            corrections.rename(self.pid, "zh", "读者", "读者群")
        doc = self.stored()
        corrections.apply(self.pid, doc, "zh")   # (laid over again: as it was)
        self.assertNotIn("读者群群", "".join(translate.line(z["toks"]) for _, z in self.units(doc)))

    def test_in_a_spaced_language_a_term_is_a_word_of_its_own(self):
        self.translated("de")
        corrections.rename(self.pid, "de", "Faltung", "Knickung")
        text = " ".join(translate.line(z["toks"]) for _, z in self.units(self.stored("de"), "de"))
        self.assertIn("Die Knickung", text)
        self.assertIn("Faltungsnetz", text)   # (a longer word with the term in it is another word)

    def test_a_sentence_is_rewritten_its_markers_kept(self):
        doc = self.translated()
        uid = next(uid for uid, z in self.units(doc) if {"a": "a0"} in z["toks"])
        got = corrections.lines_of(self.pid, "zh", uid, [0])
        self.assertEqual(got["source"], ["Readers skim long papers, as $x^2$ grows."])
        self.assertEqual(got["lines"], ["折叠句0：⟦1⟧读者。"])
        self.assertEqual(got["marks"], [{"n": "1", "what": "$x^2$"}])
        with self.assertRaisesRegex(ValueError, "markers"):
            corrections.rewrite(self.pid, "zh", uid, {"0": "读者略读长论文。"})
        corrections.rewrite(self.pid, "zh", uid, {"0": "随着 ⟦1⟧ 增长，读者略读长论文。"})
        z = self.stored()["units"][uid]["tr"]["zh"]
        said = translate.said(z)["sentences"]
        self.assertEqual(said[0], "随着 ⟦a0⟧ 增长，读者略读长论文。")
        self.assertEqual(said[1], "折叠句1：读者。")   # (the other sentence as it was)
        self.assertEqual(len(z["sents"]), 2)   # (one to one with the text's, so Key still picks its sentences)

    def test_corrections_hold_when_the_translation_is_made_again(self):
        doc = self.translated()
        uid = self.units(doc)[1][0]
        corrections.rewrite(self.pid, "zh", uid, {"1": "每一层保留它下面那一层。"})
        corrections.rename(self.pid, "zh", "折叠", "折页")
        model = Model()
        with mock.patch.object(llm, "call", model):
            pipeline.generate(self.pid, "zh", CFG, force=True)
        self.assertIn('"折页", not "折叠"', model.prompts[0])   # (every call made anew: the model is told)
        doc = self.stored()
        said = translate.said(doc["units"][uid]["tr"]["zh"])["sentences"]
        self.assertEqual(said[1], "每一层保留它下面那一层。")
        self.assertNotIn("折叠", "".join(translate.line(z["toks"]) for _, z in self.units(doc)))

    def test_a_rewritten_sentence_goes_when_the_text_it_translates_changed(self):
        doc = self.translated()
        uid = self.units(doc)[1][0]
        corrections.rewrite(self.pid, "zh", uid, {"1": "每一层保留它下面那一层。"})
        doc = self.stored()
        doc["units"][uid]["toks"] = doc["units"][uid]["toks"][:-3] + [{"t": "other"}, {"t": "."}]   # (its text edited)
        doc["units"][uid]["tr"]["zh"] = translate.assemble(doc["units"][uid], {"sentences": ["甲。", "乙。"], "takeaway": "要点", "reason": "", "topic": "要点"},
                                                           doc["atoms"], "zh", self.fail)
        corrections.apply(self.pid, doc, "zh")
        self.assertEqual(translate.said(doc["units"][uid]["tr"]["zh"])["sentences"], ["甲。", "乙。"])

    def test_a_note_in_the_translation_stays_on_its_words(self):
        doc = self.translated()
        uid = self.units(doc)[1][0]
        z = doc["units"][uid]["tr"]["zh"]
        t0, t1 = corrections._found(z["toks"], {}, "读者", notes._spans(z["toks"], {})[1][z["sents"][1][0]][0])   # (in its second sentence)
        notes.put(self.pid, {"id": "n-1", "kind": "highlight", "text": "", "ask": None,
                             "anchor": {"unit": uid, "section": None, "lang": "zh", "level": 4, "tokens": [t0, t1], "sents": [1],
                                        "quote": {"exact": "读者", "prefix": "", "suffix": ""}}})
        corrections.rewrite(self.pid, "zh", uid, {"0": "一个更长得多的第一句，在前面加了很多字。"})
        a = notes.read(self.pid)["notes"][0]["anchor"]
        z = self.stored()["units"][uid]["tr"]["zh"]
        self.assertEqual(notes._quoted(z["toks"], {}, *a["tokens"]), "读者")
        self.assertGreater(a["tokens"][0], t0)
        self.assertEqual(a["sents"], [1])
        corrections.rename(self.pid, "zh", "读者", "读者们")
        a = notes.read(self.pid)["notes"][0]["anchor"]
        z = self.stored()["units"][uid]["tr"]["zh"]
        self.assertEqual(a["quote"]["exact"], "读者们")
        self.assertEqual(notes._quoted(z["toks"], {}, *a["tokens"]), "读者们")

    def test_the_texts_own_language_has_no_translation_to_correct(self):
        self.translated()
        with self.assertRaisesRegex(ValueError, "no-translation"):
            corrections.rename(self.pid, "en", "folding", "creasing")


if __name__ == "__main__":
    unittest.main()
