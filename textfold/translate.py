"""A paper in another language, built on its ladder, in the text's own language (English for most papers).

Every sentence is translated one to one, so Key stays an exact subset of the translated
Full text (the ladder's evidence indices still apply), and the roles and links stay the
same. Brief, Takeaway and Topic are rewritten in the target language from the ladder's. Formulas, citations and cross-references travel through the translation as markers
⟦a12⟧ and come back as the very same atoms, so they stay clickable and slide when the
reader switches language. One call per section; a long section is translated in parts (PART_CHARS of English each),
because a reply that long comes back with its last paragraphs missing (seen without reasoning), and the parts run in
parallel. The section's own lines come with the first part. What comes back without a translation (a call that
failed, a paragraph the model left out) is asked for once more, the paragraphs alone; a translation stored with gaps
still (gaps) has them asked for the next time its language is generated (fill), the rest kept.
"""
import re
from concurrent.futures import ThreadPoolExecutor

from . import kinds, langs, llm
from .ladder import KIND, align, clean, outline, section_ladder, topic_tokens, written
from .tokens import plain

SCHEMA = {
    "type": "object",
    "properties": {
        "paper_title": {"type": "string"},
        "section": {"type": "object", "properties": {"title": {"type": "string"}, "takeaway": {"type": "string"},
                                                      "topic": {"type": "string"}}, "required": ["title", "takeaway", "topic"]},
        "units": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "sentences": {"type": "array", "items": {"type": "string"}},
            "takeaway": {"type": "string"}, "reason": {"type": "string"}, "topic": {"type": "string"}},
            "required": ["id", "sentences", "takeaway", "reason", "topic"]}},
    },
    "required": ["section", "units"],
}


def marked(toks, atoms):
    """A token run for translation: every atom as a marker ⟦a12 …⟧ that shows what it is."""
    s = ""
    for k, tk in enumerate(toks):
        if "a" in tk:
            at = atoms[tk["a"]]
            inner = f"${at.get('alt', '')}$" if at["kind"] == "math" else (at.get("text") or "").strip()
            piece = f"⟦{tk['a']}{(' ' + inner) if inner else ''}⟧"
        else:
            piece = tk["t"]
        s += piece + (" " if tk.get("s") and k + 1 < len(toks) else "")
    return s.strip()


def prompt_for(doc, chunk, lang):
    U, A = doc["units"], doc["atoms"]
    L, b = langs.name(lang), langs.budget(lang)
    E = langs.name(doc["meta"].get("lang") if doc["meta"].get("lang") in langs.LANGS else "en")   # the text's own (pipeline)
    unit, acro = b["unit"], " (an English acronym counts as one)" if b["unit"] == "characters" else ""
    blocks = []
    for n, uid in enumerate(chunk["units"]):
        u, lad = U[uid], U[uid]["lad"]
        kind = KIND.get(u["role"], "paragraph") + (f" inside {u['env']['title']}" if u.get("env") else "")
        lines = [f"[U{n}] {kind}"] + [f"  {i}: {marked(u['toks'][s:e], A)}" for i, (s, e) in enumerate(u["sents"])]
        reason = plain(lad["brief"][lad["tn"] + 1:-1], A) if len(lad["brief"]) > lad["tn"] + 1 else ""
        lines += [f"  {E} takeaway: {plain(lad['brief'][:lad['tn']], A)}", f"  {E} reason: {reason}",
                  f"  {E} topic: {plain(lad['topic'], A)}"]
        blocks.append("\n".join(lines))
    sec = chunk.get("lad")
    head = f"{chunk['num']} {chunk['title']}".strip()
    title_rule = f"\n- paper_title: the paper's title in {L}." if chunk["id"] == "abstract" else ""
    return kinds.adapt(f"""Translate one section of a research paper into {L} for a zoomable reader, and write its short zoom levels in {L}.

Paper: {doc['meta']['title']}

Outline (▶ marks this section):
{outline(doc, chunk['id'])}

This section: {head}
{E} section takeaway: {plain(sec['take'], A) if sec else ''}

Each unit (a paragraph, list item or caption) lists its numbered {E} sentences, then the {E} short levels already written for it.

{chr(10).join(blocks)}

Rules:
- sentences: translate EVERY numbered sentence into fluent, precise academic {L}: exactly one {L} sentence per {E} sentence, same count and order, because the reader shows them sentence by sentence. Never merge, split or drop a sentence; a sentence fragment stays a fragment.
- Markers ⟦…⟧ stand for formulas, citations and cross-references. Copy each marker exactly once and unchanged (including what is inside it) where it belongs in the {L} sentence. Do not write math of your own in the sentences.
- Terminology: keep established acronyms and proper names as the field writes them in {L} (usually unchanged, e.g. SNN, MLP, CartPole); translate other technical terms with the standard {L} terms, the same way throughout.
- takeaway: the {E} takeaway as natural {L} of at most {b['takeaway']} {unit}, conclusion first, not a word-by-word translation.
- reason: the {E} reason as natural {L} of at most {b['reason']} {unit}; "" when the {E} reason is empty.
- topic: at most {b['topic']} {unit}{acro}, copied VERBATIM from inside the {L} takeaway.
- section: title (the section title in {L}, without its number), takeaway (at most {b['section']} {unit}), topic (at most {b['topic']} {unit}, verbatim from that takeaway).{title_rule}
- {langs.punctuation_rule(lang)} In takeaway and reason, math only by copying a marker.

Return JSON {{"section": {{"title": "...", "takeaway": "...", "topic": "..."}}, "units": [{{"id": "U0", "sentences": ["...", ...], "takeaway": "...", "reason": "...", "topic": "..."}}, ...]}} with exactly one entry per unit, in order.""", doc["meta"])


PART_CHARS = 5000


def parts(doc, chunk):
    """A section's units in as few runs as keep each near PART_CHARS of English, the runs of about equal length."""
    U, A = doc["units"], doc["atoms"]
    sizes = [len(plain(U[uid]["toks"], A)) for uid in chunk["units"]]
    k = max(1, -(-sum(sizes) // PART_CHARS))
    if k == 1:
        return [list(chunk["units"])]
    target, out, run, acc = sum(sizes) / k, [], [], 0
    for uid, n in zip(chunk["units"], sizes):
        if run and len(out) < k - 1 and acc + n / 2 > target * (len(out) + 1):
            out.append(run)
            run = []
        run.append(uid)
        acc += n
    return out + [run]


def unmark(text, atoms):
    """A title is shown as text, not tokens: a marker the model carried into it becomes $latex$ (or its text) again."""
    def put(m):
        at = atoms.get(m.group(1), {})
        return f"${at['alt']}$" if at.get("kind") == "math" and at.get("alt") else (at.get("text") or "")
    return re.sub(r"⟦(a\d+)[^⟧]*⟧", put, text or "")


def assemble(u, o, atoms, lang, warn):
    zs = [z.strip() for z in (o.get("sentences") or [])]
    n = len(u["sents"])
    if len(zs) != n:
        warn(f"{u['id']}: {len(zs)} translated sentences for {n}")
        zs = (zs + [""] * n)[:n] if len(zs) < n else zs[:n - 1] + [" ".join(zs[n - 1:])]
    toks, sents = [], []
    for z in zs:
        start = len(toks)
        toks += written(z, u, atoms, [])
        if toks and not langs.cjk(lang):  # sentences of a spaced language are separated by a space
            toks[-1]["s"] = 1
        sents.append([start, len(toks)])
    if toks:
        toks[-1].pop("s", None)
    missing = {tk["a"] for tk in u["toks"] if "a" in tk} - {tk["a"] for tk in toks if "a" in tk}
    if missing:
        warn(f"{u['id']}: {len(missing)} formula/citation markers lost in translation")
    ev = [s for s in u["lad"]["ev"] if s < len(sents)] or [0]
    # (a line the model translated without a takeaway, a list of keywords: its first sentence stands in, as a paragraph
    # without a model's levels has its own)
    take_text, reason_text = clean(o.get("takeaway")) or next((z for z in zs if z), ""), clean(o.get("reason"))
    if not take_text or not toks:
        return None
    stop = langs.stop(lang)
    take = written(take_text, u, atoms, ev, toks, sents)
    brief = [dict(tk) for tk in take]
    if reason_text:
        brief.append({"t": stop} if langs.cjk(lang) else {"t": stop, "s": 1})
        brief += written(reason_text, u, atoms, ev, toks, sents)
    brief.append({"t": stop})
    tn = len(take)
    align(u, brief, ev, atoms, toks, sents)
    topic = topic_tokens(clean(o.get("topic")), brief, tn, u, atoms, ev, langs.budget(lang)["topic"], toks, sents)
    return {"toks": toks, "sents": sents, "lad": {"brief": brief, "tn": tn, "topic": topic}}


def worded(u):
    """A unit with words of its own to translate: not a formula or a citation alone, nor a letter left over from a
    figure ("D")."""
    return len(re.findall(r"\w", "".join(tk.get("t", "") for tk in u["toks"] if "a" not in tk))) > 1


def gaps(doc, lang):
    """The paragraphs of the sections that have no translation in lang: a part whose call failed (a timeout, a rate
    limit, a reply that was not JSON), a paragraph the model left out of its reply."""
    U = doc["units"]
    return [uid for c in doc["chunks"] for uid in c["units"] if lang not in U[uid].get("tr", {}) and worded(U[uid])]


def _ask(doc, lang, tasks, cfg, cache_dir, jobs, log, tick, force, stop, again=False):
    """The parts asked, in parallel; each reply's translations put in its units, and the section's own lines (and the
    paper's title) taken from its first part. Asked again (again: the gaps), a section takes its lines from the first
    part that brings them while it has none, and a reply that still leaves a paragraph out is forgotten, so the next
    time asks anew rather than reading it back. (cost, errors, warnings)"""
    cost, warnings, errors = 0.0, [], []
    meta = doc["meta"]

    def run(task):
        c, k, us = task
        if stop is not None and stop.is_set():   # stopped: the parts not yet sent are skipped
            return task, None, llm.Stopped()
        try:
            return task, llm.call(prompt_for(doc, {**c, "units": us}, lang), SCHEMA, cfg, cache_dir, force, system=llm.system_for(doc["meta"]),
                                  **({"stop": stop} if stop is not None else {})), None
        except Exception as e:
            return task, None, e

    # each part counted as its reply comes, the replies then read in the paper's order: which part brings a section's
    # lines and the paper's title stays the same whatever came back first (as #70 has it)
    got = {}
    with ThreadPoolExecutor(jobs) as ex:
        for i, r in llm.as_answered(ex, lambda it: (it[0], run(it[1])), list(enumerate(tasks)), log, llm.label(cfg)):
            got[i] = r
            tick()
            if stop is not None and stop.is_set():
                raise llm.Stopped()
    for i in range(len(tasks)):
        (c, k, us), res, err = got[i]
        if err:
            errors.append(err)
            log(f"  ! {lang} {c['id']}{f' part {k + 1}' if k else ''}: {err}")
            continue
        rec, cached = res
        cost += 0 if cached else (rec.get("cost") or 0)
        out = rec["out"]
        by_id = {x.get("id"): x for x in llm.units_of(out)}
        left = 0
        for n, uid in enumerate(us):
            o = by_id.get(f"U{n}")
            z = assemble(doc["units"][uid], o, doc["atoms"], lang, warnings.append) if o else None
            if z:
                doc["units"][uid].setdefault("tr", {})[lang] = z
            else:
                left += 1
                warnings.append(f"{uid}: no translation")
        if again and left:
            llm.forget(rec)
        if k == 0 and (not again or not (c.get("tr", {}).get(lang) or {}).get("lad")):
            sec = llm.as_obj(out.get("section"))   # the section's title and lines, and the paper's title
            # an untitled section (a document's opening text) stays untitled: the model would name it
            title = (unmark(clean(sec.get("title")), doc["atoms"]) or c["title"]) if c["title"] else ""
            c.setdefault("tr", {})[lang] = {"title": title,
                                             "lad": section_ladder(sec, langs.budget(lang)["topic"], doc, c)}
            if out.get("paper_title") and not (again and meta["titles"].get(lang)):
                meta["titles"][lang] = clean(out["paper_title"])
        log(f"  {'·' if cached else '✓'} {lang} {c['id']:<14} {len(us):>2} units{f' (part {k + 1})' if k else ''}{' again' if again else ''}")
    return cost, errors, warnings


def _again(doc, lang):
    """The gaps as parts to ask: by section, a section's missing paragraphs in as few runs as fit PART_CHARS."""
    missing = set(gaps(doc, lang))
    tasks = []
    for c in doc["chunks"]:
        us = [uid for uid in c["units"] if uid in missing]
        if us:
            tasks += [(c, k, run) for k, run in enumerate(parts(doc, {**c, "units": us}))]
    return tasks


def _counter(progress, total):
    state = {"done": 0, "total": total}

    def tick():
        state["done"] += 1
        if progress:
            progress(state["done"], state["total"])

    def more(n):
        state["total"] += n
        if progress:
            progress(state["done"], state["total"])
    if progress:
        progress(0, total)
    return tick, more


def build(doc, lang, cfg, cache_dir, jobs=4, log=print, progress=None, force=False, stop=None):
    """The paper in lang, section by section; then what came back without a translation is asked for once more (the
    paragraphs alone), so one failed call or a paragraph left out does not leave English in the translation."""
    meta = doc["meta"]
    meta.setdefault("titles", {})
    tasks = [(c, k, us) for c in doc["chunks"] if c["units"] for k, us in enumerate(parts(doc, c))]   # (a short section is one part: its prompt is as before)
    tick, more = _counter(progress, len(tasks))
    cost, errors, warnings = _ask(doc, lang, tasks, cfg, cache_dir, jobs, log, tick, force, stop)
    if tasks and len(errors) == len(tasks):
        raise RuntimeError(str(errors[0]))
    retry = _again(doc, lang)
    if retry:
        log(f"  ~ {lang}: {len(gaps(doc, lang))} paragraphs without a translation, asked again")
        more(len(retry))
        c2, _, w2 = _ask(doc, lang, retry, cfg, cache_dir, jobs, log, tick, force, stop, again=True)
        cost += c2
        warnings += w2
    cost += _titles(doc, lang, cfg, cache_dir, force, log)
    for w in warnings:
        log(f"  ~ {lang} {w}")
    return cost


def fill(doc, lang, cfg, cache_dir, jobs=4, log=print, progress=None, stop=None):
    """A stored translation's gaps (gaps()), asked for now: the paragraphs alone, the rest kept as it is. Made by
    TextFold 0.10.14 and before, a translation kept what one failed call left out. RuntimeError when every call
    failed again."""
    doc["meta"].setdefault("titles", {})
    retry = _again(doc, lang)
    tick, _ = _counter(progress, len(retry))
    cost, errors, warnings = _ask(doc, lang, retry, cfg, cache_dir, jobs, log, tick, False, stop, again=True)
    if retry and len(errors) == len(retry):
        raise RuntimeError(str(errors[0]))
    cost += _titles(doc, lang, cfg, cache_dir, False, log)
    for w in warnings:
        log(f"  ~ {lang} {w}")
    return cost


def _titles(doc, lang, cfg, cache_dir, force, log):
    """Headings with no text of their own ("2 Experiments"), and the paper title when it came back untranslated, in
    one call. Its cost."""
    meta, L = doc["meta"], langs.name(lang)
    bare = [c for c in doc["chunks"] if lang not in c.get("tr", {}) and c["title"]]
    if meta["titles"].get(lang) == meta["title"]:
        meta["titles"].pop(lang)  # the section call sometimes hands the English title back
    titles = [c["title"] for c in bare] + ([] if meta["titles"].get(lang) else [meta["title"]])
    if not titles:
        return 0.0
    schema = {"type": "object", "properties": {"titles": {"type": "array", "items": {"type": "string"}}}, "required": ["titles"]}
    prompt = (f"Paper: {meta['title']}\n\nTranslate these titles of the paper (section titles, possibly the paper title itself) "
              f"into {L}, keeping established acronyms unchanged and any $...$ math unchanged. "
              "Return JSON {\"titles\": [...]} in the same order.\n\n" + "\n".join(titles))
    prompt = kinds.adapt(prompt, meta)
    try:
        rec, cached = llm.call(prompt, schema, cfg, cache_dir, force, system=llm.system_for(meta))
    except Exception as e:
        log(f"  ! {lang} titles: {e}")
        return 0.0
    got = rec["out"].get("titles", [])
    for c, t in zip(bare, got):
        c.setdefault("tr", {})[lang] = {"title": clean(t) or c["title"], "lad": None}
    if not meta["titles"].get(lang) and len(got) == len(titles) and clean(got[-1]) != meta["title"]:
        meta["titles"][lang] = clean(got[-1])
    return 0 if cached else (rec.get("cost") or 0)
