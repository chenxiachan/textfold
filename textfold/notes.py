"""A reader's notes on a paper: highlights, notes, and questions answered by the model.

One file per paper, papers/<id>/notes.json, in the format of docs/notes-format.md (the contract for every export).
The notes are the reader's own: they are kept apart from the generated layers, and nothing here rewrites a layer.
"""
import json
import math
import re
import threading
import time

from . import kinds, langs, llm, store
from .tokens import plain

FORMAT, VERSION = "textfold/notes", 1   # (files written under earlier names say "paperfold/notes" or "dynamic-reader/notes")
_LOCK = threading.Lock()   # the server answers on several threads; one writer at a time
_STOPPED = set()           # the questions the reader stopped (by their request's id), until their answers come back
_STOP_LOCK = threading.Lock()


class Stopped(Exception):
    """A question the reader stopped while the model was answering it: its answer, come back, is not kept."""


def stop(req):
    """The reader stopped a question (its words wrong, say). The model call under way is not called back; its answer,
    when it comes, goes nowhere."""
    with _STOP_LOCK:
        _STOPPED.add(str(req))


def _file(pid):
    return store.pdir(pid) / "notes.json"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def kind(n):
    """A note's kind, as the reader counts them: a question ("ask"), a note with words ("note"), a highlight ("hl")."""
    return "ask" if n.get("kind") == "ask" else "note" if (n.get("text") or "").strip() else "hl"


def read(pid):
    f = _file(pid)
    if f.exists():
        return {**json.loads(f.read_text(encoding="utf-8")), "format": FORMAT}   # (an older file's name for the same format, renewed)
    meta = json.loads((store.pdir(pid) / "meta.json").read_text(encoding="utf-8"))
    return {"format": FORMAT, "version": VERSION,
            "paper": {"id": pid, "version": meta.get("version", ""), "title": meta.get("title", ""), "url": meta.get("abs_url", "")},
            "notes": []}


def _write(pid, data):
    f = _file(pid)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def put(pid, note):
    """Add a note, or replace the one with its id. The server stamps the times."""
    if not isinstance(note, dict) or not str(note.get("id", "")).startswith("n-") or not isinstance(note.get("anchor"), dict):
        raise ValueError("bad note")
    with _LOCK:
        data = read(pid)
        old = next((n for n in data["notes"] if n["id"] == note["id"]), None)
        note["created"] = (old or {}).get("created") or note.get("created") or now()
        note["updated"] = now()
        data["notes"] = [n for n in data["notes"] if n["id"] != note["id"]] + [note]
        _write(pid, data)
    return data


def delete(pid, nid):
    with _LOCK:
        data = read(pid)
        data["notes"] = [n for n in data["notes"] if n["id"] != nid]
        _write(pid, data)
    return data


ASK_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"}, "takeaway": {"type": "string"},
    "next": {"type": "array", "items": {"type": "string"}}}, "required": ["answer", "takeaway", "next"]}


def turns(note):
    """A question note's conversation: thread (from v0.9), or the single ask of the first notes."""
    return note.get("thread") or ([note["ask"]] if note.get("ask") else [])


def _text(u, atoms, lang):
    z = (u.get("tr") or {}).get(lang) if lang != "en" else None
    return plain(z["toks"] if z else u["toks"], atoms)


SYSTEM_ASK = ("You help a reader understand a research paper. You answer from the paper's own text and say where it says "
              "so. When the paper does not say something, you say that plainly and keep any general knowledge apart. "
              "You answer with JSON only.")
# the levels as the reader names them, and whose words each one holds
LEVELS = {4: ("Full", "the authors' own words"), 3: ("Brief", "the authors' own key sentences"),
          2: ("Key", "a two-sentence summary written by a model"), 1: ("Takeaway", "a one-line summary written by a model"),
          0: ("Topic", "a few words written by a model")}
STOP = set("the a an of and or to in on for with by at from as is are was were be been this that these those it its we our "
           "their they which what who how why when where does do did can could would should may might not no than then "
           "there here into about over under also such only more most less very mean means meaning".split())
# the pairs of characters a question is asked with, in Chinese, Japanese and Korean (what, why, how, meaning, this...):
# they say nothing about where the answer is, and would find any paragraph that happens to contain them
CJK_STOP = set("什么 是什 么意 意思 么是 怎么 怎样 为什 如何 哪些 哪个 这个 那个 这里 这些 那些 是否 能否 可以 一下 解释 请问 "
               "指的 是指 含义 為什 甚麼 這個 這裡 とは 何で 何を どう どう いう 意味 です すか って 무엇 의미 뭐야 이것".split())


def _terms(text):
    """The words a question can be matched on: Latin words and symbols (D=4 stays D=4), and pairs of CJK characters."""
    low = text.lower()
    latin = [w for w in re.findall(r"[a-z][a-z0-9]*(?:[=<>≤≥]\d+)?|\d+(?:\.\d+)?", low) if w not in STOP and len(w) > 1]
    cjk = re.findall(r"[぀-ヿ㐀-鿿가-힯]{2,}", text)
    return latin + [b for w in cjk for i in range(len(w) - 1) if (b := w[i:i + 2]) not in CJK_STOP]


def retrieve(doc, query, lang, skip=(), k=5):
    """The paragraphs anywhere in the paper that share the most telling words with the question and the selection
    (in the authors' text, and in the reader's language when it is another), best first."""
    units, atoms = doc["units"], doc["atoms"]
    bags = {uid: set(_terms(plain(u["toks"], atoms) + (" " + _text(u, atoms, lang) if lang != "en" else ""))) for uid, u in units.items()}
    df = {}
    for bag in bags.values():
        for w in bag:
            df[w] = df.get(w, 0) + 1
    q, n = set(_terms(query)), len(units)
    score = {uid: sum(math.log(n / df[w]) for w in q if w in bag) for uid, bag in bags.items() if uid not in skip}
    return [uid for uid, sc in sorted(score.items(), key=lambda x: -x[1]) if sc > 0][:k]


def prompt(doc, note, question, lang, earlier=(), ui=None):
    """What the model is given: the paper (title, abstract, a line per section), where the reader is and whose words
    they selected, the selection and its paragraph (or its section; or, for a question about the whole paper, every
    paragraph's point), passages from anywhere in the paper that share the question's words, the conversation so
    far, and the paragraphs it may cite. Paragraphs go by short numbers ([12]), which the answer cites inline and
    ask() turns back into paragraph ids: a number is copied right far more often than an id like S3.SS2.p4.1.
    Returns the prompt and the numbers: {"12": "S3.SS2.p4.1", ...}."""
    nums = {}
    tag = lambda uid: nums.setdefault(uid, str(len(nums) + 1))  # noqa: E731
    a = note["anchor"]
    units, atoms, chunks = doc["units"], doc["atoms"], doc["chunks"]
    order = list(units)
    unit = units.get(a.get("unit"))
    whole = a.get("on") == "paper"
    sec_id = (unit or {}).get("chunk") or a.get("section")
    chunk = None if whole else next((c for c in chunks if c["id"] == sec_id), None)
    sec_name = f"{(chunk or {}).get('num') or ''} {(chunk or {}).get('title') or ''}".strip()
    line = lambda uid: plain(units[uid]["lad"]["brief"][:units[uid]["lad"]["tn"]], atoms)
    abstract = " ".join(plain(units[uid]["toks"], atoms) for c in chunks if c["id"] == "abstract" for uid in c["units"])
    outline = "\n".join(f"- {c.get('num') or ''} {c['title']}: {plain(c['lad']['take'], atoms)}".strip() + ("   <- the reader is here" if c is chunk else "")
                        for c in chunks if c.get("lad"))
    level = a.get("level", 4)
    name, whose = LEVELS.get(level, LEVELS[4])
    quote = (a.get("quote") or {}).get("exact", "")
    if whole:
        near = []
        where = (f"They are reading at the {name} level of a zoomable reader and asked about the paper as a whole. Answer "
                 "from the authors' text: every paragraph's point is listed below, section by section, and passages follow.")
        body = "The paper, section by section (each paragraph's point, with its number):\n" + _map(chunks, units, line, atoms, tag)
        own = []
    elif unit:
        at = order.index(a["unit"])
        near = order[max(0, at - 8): at + 9]
        where = (f"They are reading at the {name} level of a zoomable reader, which shows {whose}, and selected text in a "
                 f"paragraph of section {sec_name}.")
        if level <= 2:
            where += (" The selection is the model's wording, not the authors': answer from the authors' text below, "
                      "and say so if that text does not bear the summary out.")
        body = f"The paragraph, in the authors' words:\n{plain(unit['toks'], atoms)}"
        if lang != "en" and (unit.get("tr") or {}).get(lang):
            body += f"\n\n(The same paragraph in {langs.name(lang)}, as the reader sees it:)\n{_text(unit, atoms, lang)}"
        own = [a["unit"]]
    else:
        sec_units = (chunk or {}).get("units", [])
        near = list(sec_units)
        what = "the title of" if a.get("on") == "title" else "the one-line summary (written by a model) of"
        where = (f"They are reading at the {name} level of a zoomable reader and selected text in {what} section {sec_name}. "
                 "Answer from the authors' text: the section's paragraphs are summed up below, and passages from it follow.")
        take = plain(chunk["lad"]["take"], atoms) if chunk and chunk.get("lad") else ""
        body = (f"The section's summary: {take}\n\nThe section, paragraph by paragraph (each paragraph's point):\n"
                + "\n".join(f"[{tag(uid)}] {line(uid)}" for uid in sec_units[:40]))
        own = []
    found = retrieve(doc, f"{question} {quote}", lang, skip=set(own), k=8 if whole else 5)
    passages = "\n\n".join(f"[{tag(uid)}] {plain(units[uid]['toks'], atoms)[:900]}" for uid in found)
    talk = ("Earlier in this conversation:\n" + "".join(f"Q: {t['q']}\nA: {t['a']}\n\n" for t in earlier)) if earlier else ""
    cite = list(dict.fromkeys([*own, *found, *near]))
    citable = ("Any paragraph number in the paper above or in the passages." if whole
               else "\n".join(f"[{tag(uid)}] {line(uid)}" for uid in cite))
    selected = f'The selected text:\n"{quote}"\n\n' if quote else ""
    text = f"""A reader of this paper {"asked about it" if whole else "selected some text and asked about it"}.

Paper: {doc['meta']['title']}
Abstract: {abstract}

The paper, one line per section:
{outline}

{where}

{selected}{body}

Passages from elsewhere in the paper that share words with the question:
{passages or "(none)"}

{talk}The question:
{question}

Answer in the language the question is written in (when that is unclear, in {langs.name(ui or lang)}). Conclusion first, then
the grounds. Be precise about what the paper says, and say so when it does not say it. At most {200 if whole else 120} words;
Markdown is fine, math as $...$.

Cite as you write: right after each statement drawn from the paper, put the number of the paragraph that bears it out most
directly in double brackets, like [[12]], or two, [[12]][[30]], when one is not enough. Every statement from the paper carries
a number, and only numbers listed here are used. A sentence that is your own inference or general knowledge rather than the paper's ends with [[?]].

Also give: the answer in one sentence of at most 20 words, with no numbers (takeaway); and two short questions a reader of
this paper might ask next (next): answerable from the paper, different from what has been asked, at most 15 words each, in the
language of the question.

Paragraphs you may cite:
{citable}
"""
    return kinds.adapt(text, doc["meta"]), {v: k for k, v in nums.items()}


MAP_CHARS = 60000   # the whole paper's map, at most (a long paper keeps every section, fewer paragraphs each)


def _map(chunks, units, line, atoms, tag):
    secs = [(c, [u for u in c["units"] if u in units]) for c in chunks if c.get("lad")]
    for per in (10 ** 6, 12, 6, 3, 0):
        out = "\n".join(f"## {c.get('num') or ''} {c['title']}: {plain(c['lad']['take'], atoms)}".replace("##  ", "## ") +
                        "".join(f"\n[{tag(uid)}] {line(uid)}" for uid in us[:per]) for c, us in secs)
        if len(out) <= MAP_CHARS:
            return out
    return out[:MAP_CHARS]


def _ids_out(answer, cites, doc):
    """A paragraph id the model wrote into its answer anyway ([S2.p1.3]) is not for a reader: it joins the cites, and
    the text says the section instead (§2)."""
    units, cites = doc["units"], list(cites)
    num = {c["id"]: c.get("num") for c in doc["chunks"]}

    def swap(m):
        ids = [i.strip() for i in re.split(r"[,;]\s*", next(g for g in m.groups() if g))]
        if not all(i in units for i in ids):
            return m.group(0)
        cites.extend(i for i in ids if i not in cites)
        secs = sorted({f"§{num[units[i].get('chunk')]}" for i in ids if num.get(units[i].get("chunk"))})
        return f" ({', '.join(secs)})" if secs else ""
    ids = r"([A-Za-z]+[\w.~-]*\.p[\w.~-]*(?:[,;]\s*[A-Za-z]+[\w.~-]*\.p[\w.~-]*)*)"
    answer = re.sub(rf"\s*(?:\(\[{ids}\]\)|\[{ids}\]|\({ids}\))", swap, answer)
    return answer.strip(), cites


CITE = re.compile(r"\[\[\s*([^\[\]]+?)\s*\]\]")


def _cited(answer, nums, units):
    """The answer's inline citations as paragraph ids: [[12]] -> [[S3.p2.1]]. [[?]] (the model's own inference) stays;
    a number that names no paragraph becomes [[!12]], which the reader shows as a citation that went wrong rather than
    dropping it. Returns the answer and the paragraphs it cites, in order."""
    cites = []

    def one(x):
        x = x.strip().lstrip("#")
        if x == "?":
            return "[[?]]"
        uid = nums.get(x) or (x if x in units else None)
        if not uid:
            return f"[[!{x}]]"
        if uid not in cites:
            cites.append(uid)
        return f"[[{uid}]]"
    answer = CITE.sub(lambda m: "".join(one(x) for x in re.split(r"[,;，；]\s*", m.group(1)) if x.strip()), answer)
    return answer, cites


def ask(pid, nid, question, spec, timeout=300, ui=None, req=None):
    """Answer a question in note nid's conversation with the model, add the turn to the note, and return the note.
    Stopped meanwhile (stop, by req): Stopped, and the note as it is."""
    doc = store.assemble(pid)
    with _LOCK:
        note = next((n for n in read(pid)["notes"] if n["id"] == nid), None)
    if not note:
        raise ValueError("no such note")
    done = [t for t in turns(note) if t.get("a")]   # answered turns (a pending or failed one is being asked again)
    lang = next((t.get("lang") for t in turns(note) if t.get("lang")), None) or note["anchor"].get("lang") or "en"
    text, nums = prompt(doc, note, question, lang, done[-6:], ui if ui in langs.LANGS else None)
    rec, _ = llm.call(text, ASK_SCHEMA, spec, store.pdir(pid) / "llm-cache", timeout=timeout, system=kinds.adapt(SYSTEM_ASK, doc["meta"]))
    out = rec["out"]
    answer, cites = _cited(out["answer"], nums, doc["units"])
    held = []   # the citations kept aside while an id written out in full (not as a citation) becomes its section
    bare = CITE.sub(lambda m: held.append(m.group(0)) or f"\ue000{len(held) - 1}\ue001", answer)
    bare, more = _ids_out(bare, [], doc)
    answer = re.sub("\ue000(\\d+)\ue001", lambda m: held[int(m.group(1))], bare)
    cites += [c for c in more if c not in cites]
    nxt = [re.sub(r"\s+", " ", CITE.sub("", q)).strip() for q in (out.get("next") or []) if isinstance(q, str)]
    note["thread"] = done + [{"q": question, "a": answer, "takeaway": CITE.sub("", out["takeaway"]).strip(),
                              "cites": cites, "next": [q for q in nxt if q][:2],
                              "model": spec["id"], "lang": lang, "at": now()}]
    note.pop("ask", None)
    note["kind"] = "ask"
    with _STOP_LOCK:   # (a stop and the answer crossing: the one second knows of the other)
        if req and req in _STOPPED:
            _STOPPED.discard(req)
            raise Stopped(req)
        put(pid, note)
    return note
