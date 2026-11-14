"""A paper in another language, built on its ladder, in the text's own language (English for most papers).

Every sentence is translated one to one, so Key stays an exact subset of the translated
Full text (the ladder's evidence indices still apply), and the roles and links stay the
same. Brief, Takeaway and Topic are rewritten in the target language from the ladder's. Formulas, citations and cross-references travel through the translation as markers
⟦a12⟧ and come back as the very same atoms, so they stay clickable and slide when the
reader switches language. One call per section; a long section is translated in parts (PART_CHARS of English each,
its sentences and their levels), because a reply that long comes back with its last paragraphs missing (seen without
reasoning), and the parts run in parallel. The section's own lines come with the first part. What comes back without
a translation (a call that failed, a paragraph the model left out) is asked for once more, the paragraphs alone; a
translation stored with gaps still (gaps) has them asked for the next time its language is generated (fill), the rest
kept.
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


PART_CHARS = 6500


def size(doc, uid):
    """The English a unit's entry in the reply translates: its sentences and the levels written for it. Many short
    paragraphs have more of the levels than of the text (#99)."""
    u, A = doc["units"][uid], doc["atoms"]
    lad = u.get("lad") or {}
    return len(plain(u["toks"], A)) + len(plain(lad.get("brief") or [], A)) + len(plain(lad.get("topic") or [], A))


def parts(doc, chunk):
    """A section's units in as few runs as keep each near PART_CHARS of English to translate (size), the runs of about
    equal length."""
    sizes = [size(doc, uid) for uid in chunk["units"]]
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


def _ask(doc, lang, tasks, cfg, cache_dir, jobs, log, tick, force, stop, again=False, sink=None):
    """The parts asked, in parallel; each reply's translations put in its units, and the section's own lines (and the
    paper's title) taken from its first part. Asked again (again: the gaps), a section takes its lines from the first
    part that brings them while it has none, and a reply that still leaves a paragraph out is forgotten, so the next
    time asks anew rather than reading it back. A section is put in as soon as all its parts are back (sink: told
    then, to show it). (cost, errors, warnings)"""
    cost, warnings, errors = 0.0, [], []
    meta = doc["meta"]

    def run(task):
        c, k, us = task
        if stop is not None and stop.is_set():   # stopped: the parts not yet sent are skipped
            return task, None, llm.Stopped()
        try:
            with llm.stoppable(stop):   # (stopped while it runs: the agent's call ends there)
                return task, llm.call(prompt_for(doc, {**c, "units": us}, lang), SCHEMA, cfg, cache_dir, force, system=llm.system_for(doc["meta"])), None
        except Exception as e:
            return task, None, e

    titles = {}   # the paper's title, by the part that brought it: the last in the paper's order is kept

    def take(i):
        nonlocal cost
        (c, k, us), res, err = got[i]
        if err:
            errors.append(err)
            log(f"  ! {lang} {c['id']}{f' part {k + 1}' if k else ''}: {err}")
            return
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
                titles[i] = clean(out["paper_title"])
                meta["titles"][lang] = titles[max(titles)]
        log(f"  {'·' if cached else '✓'} {lang} {c['id']:<14} {len(us):>2} units{f' (part {k + 1})' if k else ''}{' again' if again else ''}")

    # each part counted as its reply comes; a section read once all its parts are back, its parts in their order, so
    # which part brings its lines stays the same whatever came back first (as #70 has it)
    parts_of = {}
    for i, (c, _, _) in enumerate(tasks):
        parts_of.setdefault(id(c), []).append(i)
    waiting = {key: len(v) for key, v in parts_of.items()}
    got = {}
    with ThreadPoolExecutor(jobs) as ex:
        for i, r in llm.as_answered(ex, lambda it: (it[0], run(it[1])), list(enumerate(tasks)), log, llm.label(cfg)):
            got[i] = r
            tick()
            key = id(tasks[i][0])
            waiting[key] -= 1
            if not waiting[key]:
                for j in parts_of[key]:
                    take(j)
                if sink is not None:
                    sink()
            if stop is not None and stop.is_set():
                raise llm.Stopped()
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


def build(doc, lang, cfg, cache_dir, jobs=4, log=print, progress=None, force=False, stop=None, sink=None):
    """The paper in lang, section by section; then what came back without a translation is asked for once more (the
    paragraphs alone), so one failed call or a paragraph left out does not leave English in the translation. sink: told
    each time a section is in (pipeline.generate: the translation shown as it is made)."""
    meta = doc["meta"]
    meta.setdefault("titles", {})
    tasks = [(c, k, us) for c in doc["chunks"] if c["units"] for k, us in enumerate(parts(doc, c))]   # (a short section is one part: its prompt is as before)
    tick, more = _counter(progress, len(tasks))
    cost, errors, warnings = _ask(doc, lang, tasks, cfg, cache_dir, jobs, log, tick, force, stop, sink=sink)
    if tasks and len(errors) == len(tasks):
        raise RuntimeError(str(errors[0]))
    retry = _again(doc, lang)
    if retry:
        log(f"  ~ {lang}: {len(gaps(doc, lang))} paragraphs without a translation, asked again")
        more(len(retry))
        c2, _, w2 = _ask(doc, lang, retry, cfg, cache_dir, jobs, log, tick, force, stop, again=True, sink=sink)
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


# ── the reader's corrections (the app's Edit translation) ─────────────────────────────────────────────────────────
# A sentence of a translation put right where it is read: its paragraph is assembled again from what the reply said
# (said), that sentence as the reader wrote it, as the reply itself was: its sentences one to one with the text's, its
# levels made of them. No model is asked.

def line(toks):
    """Tokens as a line that written() reads back into the same tokens: an atom as its marker ⟦a12⟧, a formula of the
    line's own as $...$."""
    s = ""
    for k, tk in enumerate(toks):
        s += f"⟦{tk['a']}⟧" if "a" in tk else f"${tk['x']}$" if "x" in tk else tk.get("t", "")
        if tk.get("s") and k + 1 < len(toks):
            s += " "
    return s


def said(z):
    """What the reply said for a paragraph, as assemble() read it, from its translation z: its sentences, takeaway,
    reason and topic."""
    lad = z["lad"]
    b, tn = lad["brief"], lad["tn"]
    return {"sentences": [line(z["toks"][a:e]) for a, e in z["sents"]], "takeaway": line(b[:tn]),
            "reason": line(b[tn + 1:-1]) if len(b) > tn + 1 else "", "topic": line(lad["topic"])}


def reword(doc, lang, uid, sentences):
    """A paragraph's translation made again with sentences ({k: line}) in place of its own; None when it would have no
    text."""
    u = doc["units"][uid]
    o = said(u["tr"][lang])
    for k, z in sentences.items():
        o["sentences"][int(k)] = re.sub(r"\s+", " ", z or "").strip()
    return assemble(u, o, doc["atoms"], lang, lambda w: None)


# ── the levels of paragraphs the reader corrected, written again (Edit translation: Save and regenerate) ──────────
RELEVEL_SCHEMA = {
    "type": "object",
    "properties": {"units": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "takeaway": {"type": "string"}, "reason": {"type": "string"}, "topic": {"type": "string"}},
        "required": ["id", "takeaway", "reason", "topic"]}}},
    "required": ["units"],
}


def relevel_prompt(doc, chunk, lang, uids):
    """The short levels in lang of paragraphs of one section whose translation the reader corrected, from their
    sentences as corrected (the text's own, and its levels, beside them)."""
    U, A = doc["units"], doc["atoms"]
    L, b = langs.name(lang), langs.budget(lang)
    E = langs.name(doc["meta"].get("lang") if doc["meta"].get("lang") in langs.LANGS else "en")
    unit, acro = b["unit"], " (an English acronym counts as one)" if b["unit"] == "characters" else ""
    blocks = []
    for n, uid in enumerate(uids):
        u, lad, z = U[uid], U[uid]["lad"], U[uid]["tr"][lang]
        kind = KIND.get(u["role"], "paragraph") + (f" inside {u['env']['title']}" if u.get("env") else "")
        reason = plain(lad["brief"][lad["tn"] + 1:-1], A) if len(lad["brief"]) > lad["tn"] + 1 else ""
        blocks.append("\n".join(
            [f"[U{n}] {kind}"] + [f"  {E} {i}: {marked(u['toks'][s:e], A)}" for i, (s, e) in enumerate(u["sents"])]
            + [f"  {L} {i}: {marked(z['toks'][s:e], A)}" for i, (s, e) in enumerate(z["sents"])]
            + [f"  {E} takeaway: {plain(lad['brief'][:lad['tn']], A)}", f"  {E} reason: {reason}", f"  {E} topic: {plain(lad['topic'], A)}"]))
    head = f"{chunk['num']} {chunk['title']}".strip()
    return kinds.adapt(f"""A reader corrected by hand the {L} translation of some paragraphs of a research paper. Write their short zoom levels in {L} again, from the {L} sentences as the reader corrected them.

Paper: {doc['meta']['title']}
This section: {head}

Each unit lists its {E} sentences, the {L} sentences as corrected, then the {E} short levels already written for it.

{chr(10).join(blocks)}

Rules:
- Use the words, names and terms of the corrected {L} sentences, never others for them.
- takeaway: the {E} takeaway as natural {L} of at most {b['takeaway']} {unit}, conclusion first.
- reason: the {E} reason as natural {L} of at most {b['reason']} {unit}; "" when the {E} reason is empty.
- topic: at most {b['topic']} {unit}{acro}, copied VERBATIM from inside the {L} takeaway.
- {langs.punctuation_rule(lang)} Math only by copying a marker ⟦…⟧.

Return JSON {{"units": [{{"id": "U0", "takeaway": "...", "reason": "...", "topic": "..."}}, ...]}} with exactly one entry per unit, in order.""", doc["meta"])


def relevel(doc, lang, uids, cfg, cache_dir, jobs=4, log=print, progress=None, stop=None):
    """The levels in lang of the paragraphs uids (corrected by the reader) written again, section by section, their
    sentences kept as they are. The paragraphs left as they were (a call that failed), to be asked for again."""
    by_chunk = [(c, [uid for uid in c["units"] if uid in set(uids) and lang in doc["units"][uid].get("tr", {})]) for c in doc["chunks"]]
    tasks = [(c, us) for c, us in by_chunk if us]
    tick, _ = _counter(progress, len(tasks))
    left = []

    def run(task):
        c, us = task
        if stop is not None and stop.is_set():
            return task, None, llm.Stopped()
        try:
            with llm.stoppable(stop):
                return task, llm.call(relevel_prompt(doc, c, lang, us), RELEVEL_SCHEMA, cfg, cache_dir, False, system=llm.system_for(doc["meta"])), None
        except Exception as e:
            return task, None, e
    with ThreadPoolExecutor(jobs) as ex:
        for _, ((c, us), res, err) in llm.as_answered(ex, lambda it: (it[0], run(it[1])), list(enumerate(tasks)), log, llm.label(cfg)):
            tick()
            if stop is not None and stop.is_set():
                raise llm.Stopped()
            if err:
                log(f"  ! {lang} {c['id']} levels: {err}")
                left += us
                continue
            by_id = {x.get("id"): x for x in llm.units_of(res[0]["out"])}
            for n, uid in enumerate(us):
                o, u = by_id.get(f"U{n}"), doc["units"][uid]
                made = assemble(u, {**(o or {}), "sentences": said(u["tr"][lang])["sentences"]}, doc["atoms"], lang, lambda w: None) if o else None
                if made:
                    u["tr"][lang] = made
                else:
                    left.append(uid)
            log(f"  ✓ {lang} {c['id']:<14} {len(us):>2} units' levels again")
    return left
