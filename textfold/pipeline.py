"""Generate a paper in a language: fetch (an arXiv paper), parse, the ladder and links in the text's own language
(once: store.text_lang), and a translation into any other language, English too. The text's own language is never
translated: asked for, it is the ladder.

Every step reports through `progress(stage, done, total)`; the server turns that into a progress bar. A set `stop`
(a threading.Event) ends it with llm.Stopped: an agent's calls under way end at once (agents.run), an API's once they
return. What the model answered before stays in the cache, so the same generation started again goes on from there. The argument links are an extra: when their call fails (one
call over every takeaway, or the parts of a text too long for one), the levels are stored without them (and not asked
for again unless the text is folded again).

`force` asks the model again for every section (a new sample), with one exception: a document whose text was replaced
in place (local.replace_text) has no layers until it is folded again, and those are made from the replies kept for it,
so only the sections whose prompts changed are asked. A section's prompt holds the title, the opening, the outline and
its own text: an edit in one section asks for that section alone (and the argument links, one call over every
paragraph's takeaway, in parts for a text too long for one, when a takeaway came back changed); an edit to the title,
the opening or a heading asks for every
section. A translation's prompt also numbers the formulas, links and other atoms of the whole text in order, so a
section after an atom added or taken out is translated again too.
"""
import time
import urllib.error

from . import biorxiv, corrections, epmc, github, ladder, links, llm, local, models, notes, store, translate, webpage, wiki
from .fetch import fetch


def task(cfg, name):
    """The model spec a task runs with: its reasoning level from the settings (models.for_task)."""
    return models.for_task(llm.spec_of(cfg), name)


SHOW_EVERY = 1.5   # seconds: a translation shown as it is made is written at most this often (and its first section at once)


def _shown(pid, doc, lang, base, model, shown):
    """What translate.build tells each time a section is in: the translation so far written (partial), for the page to
    show; at most every SHOW_EVERY seconds, the first at once. shown(n): the sections written."""
    state = {"at": 0.0, "n": 0}

    def sink():
        now = time.monotonic()
        state["n"] += 1
        if state["at"] and now - state["at"] < SHOW_EVERY:
            return
        state["at"] = now
        corrections.apply(pid, doc, lang)   # (the reader's own corrections, on what is shown too)
        store.save_tr(doc, lang, base, model, partial=True)
        if shown:
            shown(sum(1 for c in doc["chunks"] if lang in c.get("tr", {})))
    return sink


def generate(ref, lang, cfg, force=False, jobs=4, progress=lambda *a: None, log=lambda *a: None, stop=None, shown=None):
    def check():
        if stop is not None and stop.is_set():
            raise llm.Stopped()

    pid = store.resolve(ref)
    progress("fetch", 0, 1)
    if store.is_imported(pid):   # stored when it was opened (local.py), or fetched once from Europe PMC (epmc.py)
        if pid.startswith(("pmc", "ppr")):
            epmc.fetch(pid)
        elif pid.startswith(("biorxiv-", "medrxiv-")):
            biorxiv.fetch(pid, progress)
        elif pid.startswith("wiki-"):
            wiki.fetch(pid)
        elif pid.startswith("gh-"):
            github.fetch(pid)
        elif pid.startswith("web-"):
            webpage.fetch(pid)
        d = store.pdir(pid)
    else:
        try:
            d = fetch(pid, store.library_root())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError("no-html")  # the app shows its own sentence for this
            raise
    check()
    progress("parse", 0, 1)
    doc = store.parsed(pid)
    cache = d / "llm-cache"
    en = store.read(pid, "en")
    doc["meta"]["lang"] = base = store.text_lang(pid, en)   # (the ladder is written in it: ladder.prompt_for)
    lang = lang or base
    if cfg is None and (en is None or force or (lang != base and store.read(pid, f"tr-{lang}") is None)):
        # fetched and parsed, so it can be read as written; its levels need a model
        raise RuntimeError("No model is available: install Claude Code, Codex, Pi, WorkBuddy or Antigravity, or add an API provider in the settings")
    model = llm.label(cfg) if cfg else ""
    replaced = bool(doc["meta"].get("replaced"))   # its text replaced in place: a missing layer is made from the replies kept
    if en is None or (force and lang == base):
        check()
        again = force and not (replaced and en is None)
        ladder.build(doc, task(cfg, "ladder"), cache, jobs=jobs, log=log, progress=lambda a, b: progress("ladder", a, b), force=again, stop=stop)
        check()
        progress("links", 0, 1)
        try:
            links.build(doc, task(cfg, "links"), cache, log=log, force=again, jobs=jobs)
        except Exception as e:   # the links are one call over every takeaway, an extra: failed (a long text's reply cut
            log(f"  ! links: {e}")   # off, a timeout), the levels are kept without them, as a failed section's are kept
        check()   # (stopped meanwhile, its text converted again: what it made is of the old text, and is not written)
        store.save_en(doc, model)
        en = store.read(pid, "en")
    else:
        store.apply_en(doc, en)
    if lang != base:
        if lang == "en":   # notes made on the ladder of a text in another language said "en" (it was always English)
            notes.settle_base(pid, base)
        tr = store.read(pid, f"tr-{lang}")
        if force or tr is None or tr.get("base") != en["version"] or tr.get("partial"):
            check()
            again = force and not (replaced and tr is None)
            # a language translated for the first time is shown as it is made, section by section (shown: how many
            # are, for the reader's page); one made again stays as it was until the new one is whole
            sink = None
            if not force and (tr is None or (tr.get("partial") and tr.get("base") == en["version"])):
                if tr is not None:
                    store.apply_tr(doc, lang, tr)   # (what a translation stopped before had: in, while the rest comes)
                sink = _shown(pid, doc, lang, en["version"], model, shown)
            translate.build(doc, lang, task(cfg, "translate"), cache, jobs=jobs, log=log, progress=lambda a, b: progress("translate", a, b), force=again, stop=stop, sink=sink)
            check()
            corrections.apply(pid, doc, lang)   # (the reader's own: the sentences they rewrote)
            store.save_tr(doc, lang, en["version"], model)
        elif cfg is not None:
            store.apply_tr(doc, lang, tr)
            was = tr.get("model") or model
            made_by = was if was == model else f"{was} + {model}"
            left = [uid for uid in tr.get("relevel") or [] if uid in doc["units"]]
            if left:   # paragraphs the reader corrected, saved to be generated again: their levels, from their sentences
                check()
                left = translate.relevel(doc, lang, left, task(cfg, "translate"), cache, jobs=jobs, log=log, progress=lambda a, b: progress("translate", a, b), stop=stop)
                check()
                store.save_tr(doc, lang, en["version"], made_by, relevel=left)
            if translate.gaps(doc, lang):   # paragraphs it was stored without (a call that failed): asked for now
                check()
                translate.fill(doc, lang, task(cfg, "translate"), cache, jobs=jobs, log=log, progress=lambda a, b: progress("translate", a, b), stop=stop)
                check()
                corrections.apply(pid, doc, lang)
                store.save_tr(doc, lang, en["version"], made_by, relevel=left)
    local.settle(pid)   # (its text edited: once generated in every language it was in, its edit history goes)
    progress("done", 1, 1)
    return pid
