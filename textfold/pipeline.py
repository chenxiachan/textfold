"""Generate a paper in a language: fetch (an arXiv paper), parse, the ladder and links in the text's own language
(once: store.text_lang), and a translation into any other language, English too. The text's own language is never
translated: asked for, it is the ladder.

Every step reports through `progress(stage, done, total)`; the server turns that into a progress bar. A set `stop`
(a threading.Event) ends it with llm.Stopped at the next step: what the model has already written stays in the cache,
so the same generation started again goes on from there. The argument links are an extra: when their one call fails,
the levels are stored without them (and not asked for again unless the text is folded again).

`force` asks the model again for every section (a new sample), with one exception: a document whose text was replaced
in place (local.replace_text) has no layers until it is folded again, and those are made from the replies kept for it,
so only the sections whose prompts changed are asked. A section's prompt holds the title, the opening, the outline and
its own text: an edit in one section asks for that section alone (and the argument links, one call over every
paragraph's takeaway, when a takeaway came back changed); an edit to the title, the opening or a heading asks for every
section. A translation's prompt also numbers the formulas, links and other atoms of the whole text in order, so a
section after an atom added or taken out is translated again too.
"""
import urllib.error

from . import biorxiv, epmc, github, ladder, links, llm, models, notes, store, translate, webpage, wiki
from .fetch import fetch


def task(cfg, name):
    """The model spec a task runs with: its reasoning level from the settings (models.for_task)."""
    return models.for_task(llm.spec_of(cfg), name)


def generate(ref, lang, cfg, force=False, jobs=4, progress=lambda *a: None, log=lambda *a: None, stop=None):
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
        raise RuntimeError("No model is available: install Claude Code, Codex, Pi or WorkBuddy, or add an API provider in the settings")
    model = llm.label(cfg) if cfg else ""
    replaced = bool(doc["meta"].get("replaced"))   # its text replaced in place: a missing layer is made from the replies kept
    if en is None or (force and lang == base):
        check()
        again = force and not (replaced and en is None)
        ladder.build(doc, task(cfg, "ladder"), cache, jobs=jobs, log=log, progress=lambda a, b: progress("ladder", a, b), force=again, stop=stop)
        check()
        progress("links", 0, 1)
        try:
            links.build(doc, task(cfg, "links"), cache, log=log, force=again)
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
        if force or tr is None or tr.get("base") != en["version"]:
            check()
            again = force and not (replaced and tr is None)
            translate.build(doc, lang, task(cfg, "translate"), cache, jobs=jobs, log=log, progress=lambda a, b: progress("translate", a, b), force=again, stop=stop)
            check()
            store.save_tr(doc, lang, en["version"], model)
        elif cfg is not None:
            store.apply_tr(doc, lang, tr)
            if translate.gaps(doc, lang):   # paragraphs it was stored without (a call that failed): asked for now
                check()
                translate.fill(doc, lang, task(cfg, "translate"), cache, jobs=jobs, log=log, progress=lambda a, b: progress("translate", a, b), stop=stop)
                was = tr.get("model") or model
                check()
                store.save_tr(doc, lang, en["version"], was if was == model else f"{was} + {model}")
    progress("done", 1, 1)
    return pid
