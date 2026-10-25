"""A reader's notes on a paper: highlights, notes, and questions answered by the model.

One file per paper, papers/<id>/notes.json, in the format of docs/notes-format.md (the contract for every export).
The notes are the reader's own: they are kept apart from the generated layers, and nothing here rewrites a layer.
"""
import difflib
import copy
import json
import math
import re
import threading
import time
import uuid
from collections import Counter

from . import kinds, langs, llm, store
from .tokens import plain
from . import sync_records
from .documents import ConflictError

FORMAT, VERSION = "textfold/notes", 1   # (files written under earlier names say "paperfold/notes" or "dynamic-reader/notes")
_LOCK = threading.RLock()   # merge an answer and save its note under the same lock
_STOPPED = set()           # the questions the reader stopped (by their request's id), until their answers come back
_STOP_LOCK = threading.Lock()
_INFLIGHT = set()          # request IDs currently calling a model; never replay in parallel


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


def _local_read(pid):
    f = _file(pid)
    if f.exists():
        data = {**json.loads(store.read_text(f)), "format": FORMAT}   # (an older file's name for the same format, renewed)
        base = store.text_lang(pid)
        if base != "en" and "en" not in store.languages(pid):
            for n in data.get("notes", []):
                _relabel(n, base)
        return data
    meta = json.loads(store.read_text(store.pdir(pid) / "meta.json"))
    return {"format": FORMAT, "version": VERSION,
            "paper": {"id": pid, "version": meta.get("version", ""), "title": meta.get("title", ""), "url": meta.get("abs_url", "")},
            "notes": []}


def _local__write(pid, data):
    f = _file(pid)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def _relabel(note, base):
    """A note made on the ladder of a text in another language than English said "en": the ladder had that name when
    it was always English. Read as the text's own language (base) while the text has no English translation, the
    note stays where it was made; True when it said "en"."""
    said = False
    for x in [note.get("anchor") or {}, *turns(note)]:
        if x.get("lang") == "en":
            x["lang"], said = base, True
    return said


def settle_base(pid, base):
    """Keep old note anchors on the text's own language before its first English translation."""
    if base == "en":
        return
    with _LOCK:
        if store.is_local():
            f = _file(pid)
            if not f.exists():
                return
            data = json.loads(store.read_text(f))
        else:
            data = read(pid)
            if data.get("base_language_settled") or store.read(pid, "tr-en"):
                return
            for note in data.get("notes", []):
                sync_records.settle_language(note, base)
            data["base_language_settled"] = base
            _write(pid, data)
            return
        if any([_relabel(n, base) for n in data.get("notes", [])]):
            _write(pid, data)


def _local_put(pid, note):
    """Add a note, or replace the one with its id. The server stamps the times."""
    if not isinstance(note, dict) or not str(note.get("id", "")).startswith("n-") or not isinstance(note.get("anchor"), dict):
        raise ValueError("bad note")
    with _LOCK:
        data = read(pid)
        old = next((n for n in data["notes"] if n["id"] == note["id"]), None)
        for field in ("messages", "branches"):
            if old and field not in note and field in old:
                note[field] = copy.deepcopy(old[field])
        note["created"] = (old or {}).get("created") or note.get("created") or now()
        note["updated"] = now()
        data["notes"] = [n for n in data["notes"] if n["id"] != note["id"]] + [note]
        _write(pid, data)
    return data


def _local_delete(pid, nid):
    with _LOCK:
        data = read(pid)
        data["notes"] = [n for n in data["notes"] if n["id"] != nid]
        _write(pid, data)
    return data


class _Notes(dict):
    """A read snapshot carries its merge base in memory, never in JSON exports."""


def read(pid):
    if store.is_local():
        return _local_read(pid)
    document = store.document(pid)
    if document.notes is not None:
        data = _Notes({**document.notes, "format": FORMAT})
    else:
        meta = document.meta
        data = _Notes({"format": FORMAT, "version": VERSION,
                "paper": {"id": pid, "version": meta.get("version", ""), "title": meta.get("title", ""), "url": meta.get("abs_url", "")},
                "notes": []})
    data.base_notes, data.revision = copy.deepcopy(document.notes), document.revision
    base = (document.layers.get("en") or {}).get("lang")
    if base not in langs.LANGS:
        base = store._detect(document.source.html or "")
    if base != "en" and "tr-en" not in document.layers and not data.get("base_language_settled"):
        for note in data.get("notes", []):
            sync_records.settle_language(note, base)
    return data


def _write(pid, data):
    if store.is_local():
        return _local__write(pid, data)
    base = getattr(data, "base_notes", None)
    for attempt in range(4):
        document = store.document(pid)
        if document.revision != getattr(data, "revision", document.revision):
            merged, _ = sync_records.reconcile(_records(pid, base), _records(pid, dict(data)), _records(pid, document.notes))
            document.notes = sync_records.join(pid, merged)["notes"]
        else:
            document.notes = dict(data)
        try:
            store.repository().save(document)
            data.clear()
            data.update(copy.deepcopy(document.notes))
            if isinstance(data, _Notes):
                data.base_notes, data.revision = copy.deepcopy(document.notes), document.revision
            return
        except ConflictError:
            if attempt == 3:
                raise


def _records(pid, value):
    all_records = sync_records.note_records(pid, value)
    return {k: v for k, v in all_records.items() if k == "notes" or k.startswith(("annotation/", "message/", "conflict/", "alias/"))}


def conflicts():
    if store.is_local():
        return []
    return [{"document": info.id, "title": info.meta.get("title", ""), **c} for info in store.repository().list()
            for c in (store.document(info.id).notes or {}).get("conflicts", [])]


def resolve(pid, identity, choice, text=None):
    """The same retained-revision UI also works in the legacy local backend."""
    with _LOCK:
        data = read(pid)
        values = _records(pid, data)
        conflict = values.get("conflict/" + identity)
        if not conflict:
            raise ValueError("Choose one of the retained revisions")
        values = sync_records.resolve_conflict(values, identity, choice, text)
        data.clear(); data.update(sync_records.join(pid, values)["notes"])
        _write(pid, data)


def _body(note):
    return {k: copy.deepcopy(v) for k, v in note.items() if k not in ("_base", "_revision", "thread", "ask", "messages", "branches")}


def present(data):
    """Read tokens for browser edits; never persist them into notes exports."""
    result = copy.deepcopy(data)
    if store.is_local():
        for note in result["notes"]:
            note.pop("messages", None)
            note.pop("branches", None)
        return result
    for note in result["notes"]:
        note["_base"] = _body(note)
        note["_revision"] = sync_records.digest(note["_base"])
        if note.get("thread") or note.get("ask") or note.get("messages"):
            note["messages"] = sync_records.messages(note)
            note["branches"] = sync_records.branches(note)
            note["thread"] = sync_records.conversation(note)
    return result


def put(pid, note):
    if store.is_local():
        return _local_put(pid, note)
    """Add a note, or replace the one with its id. The server stamps the times."""
    if not isinstance(note, dict) or not str(note.get("id", "")).startswith("n-") or not isinstance(note.get("anchor"), dict):
        raise ValueError("bad note")
    with _LOCK:
        note = copy.deepcopy(note)
        data = read(pid)
        aliases, seen = data.get("aliases", {}), set()
        while note["id"] in aliases and note["id"] not in seen:
            seen.add(note["id"])
            note["id"] = aliases[note["id"]]
        old = next((n for n in data["notes"] if n["id"] == note["id"]), None)
        base = note.pop("_base", None)
        note.pop("_revision", None)
        if old is None and isinstance(base, dict):
            conflict = {"key": "annotation/" + note["id"], "base": base, "variants": [None, _body(note)],
                        "deleted": True, "fields": [], "messages": sync_records.messages(note)}
            conflict["id"] = "c-" + sync_records.digest(conflict)[:32]
            held = {c["id"]: c for c in data.get("conflicts", [])}
            held[conflict["id"]] = conflict
            data["conflicts"] = list(held.values())
            _write(pid, data)
            return data
        if old and isinstance(base, dict):
            key = "annotation/" + note["id"]
            base["id"] = note["id"]
            merged, conflicts = sync_records.reconcile({key: base}, {key: _body(note)}, {key: _body(old)})
            history = {k: v for k, v in note.items() if k in ("thread", "ask", "messages", "branches")}
            note = {**merged[key], **history}
            held = {c["id"]: c for c in data.get("conflicts", [])}
            held.update({identity: merged["conflict/" + identity] for identity in conflicts})
            if held:
                data["conflicts"] = list(held.values())
        if "messages" in note or (old and "messages" in old):
            nodes = {m["id"]: m for m in sync_records.messages(old or {"messages": []})}
            for node in sync_records.messages(note):
                previous = nodes.get(node["id"])
                if previous and previous != node and not previous.get("pending"):
                    # A stale editor cannot overwrite confirmed message history.
                    continue
                nodes[node["id"]] = node
            note["messages"] = list(nodes.values())
            note["branches"] = sync_records.branches(note)
            note["thread"] = sync_records.conversation(note)
            note.pop("ask", None)
        note["created"] = (old or {}).get("created") or note.get("created") or now()
        note["updated"] = now()
        data["notes"] = [n for n in data["notes"] if n["id"] != note["id"]] + [note]
        _write(pid, data)
    return data


def delete(pid, nid, base=None):
    if store.is_local():
        return _local_delete(pid, nid)
    with _LOCK:
        data = read(pid)
        old = next((n for n in data["notes"] if n["id"] == nid), None)
        if old and isinstance(base, dict) and _body(old) != base:
            conflict = {"key": "annotation/" + nid, "base": base, "variants": [None, _body(old)],
                        "deleted": True, "fields": [], "messages": sync_records.messages(old)}
            conflict["id"] = "c-" + sync_records.digest(conflict)[:32]
            data.setdefault("conflicts", []).append(conflict)
        data["notes"] = [n for n in data["notes"] if n["id"] != nid]
        _write(pid, data)
    return data


ASK_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"}, "takeaway": {"type": "string"},
    "next": {"type": "array", "items": {"type": "string"}}}, "required": ["answer", "takeaway", "next"]}


def turns(note, head=None):
    """A question note's conversation: thread (from v0.9), or the single ask of the first notes."""
    if head is not None:
        return sync_records.conversation(note, head)
    return note["thread"] if "thread" in note else ([note["ask"]] if note.get("ask") else sync_records.conversation(note))


def _text(u, atoms, lang):
    z = (u.get("tr") or {}).get(lang)   # (none in the text's own language)
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
    bags = {uid: set(_terms(plain(u["toks"], atoms) + (" " + _text(u, atoms, lang) if lang in (u.get("tr") or {}) else ""))) for uid, u in units.items()}
    df = {}
    for bag in bags.values():
        for w in bag:
            df[w] = df.get(w, 0) + 1
    q, n = set(_terms(query)), len(units)
    score = {uid: sum(math.log(n / df[w]) for w in q if w in bag) for uid, bag in bags.items() if uid not in skip}
    return [uid for uid, sc in sorted(score.items(), key=lambda x: -x[1]) if sc > 0][:k]


TOOLS = """
You can search the web and open pages with your tools: use them for what the paper does not give, such as a work it
cites or builds on, a term it takes as known, or a figure to check. A statement from outside the paper ends with [[?]],
and each page you drew on is given where you use it, as a Markdown link [its title](https://…). The paper's own text
comes first."""


def prompt(doc, note, question, lang, earlier=(), ui=None, tools=False):
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
    # what they selected: on a written line (Key, Takeaway), its words as the model wrote them; else the passage
    quote = (a.get("shown") or {}).get("exact") or (a.get("quote") or {}).get("exact", "")
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
        if a.get("eq"):   # an equation set apart (reader.js eqEls): the selection is the formula, beside its paragraph
            where = (f"They are reading at the {name} level of a zoomable reader, which shows {whose}, and selected an "
                     f"equation set apart from the text in section {sec_name}, next to the paragraph below. The "
                     "selected text is that equation, in LaTeX.")
        elif level <= 2:
            where += (" The selection is the model's wording, not the authors': answer from the authors' text below, "
                      "and say so if that text does not bear the summary out.")
        body = f"The paragraph, in the authors' words:\n{plain(unit['toks'], atoms)}"
        if (unit.get("tr") or {}).get(lang):
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
Markdown is fine, math as $...$.{TOOLS if tools else ""}

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


def _local_ask(pid, nid, question, spec, timeout=300, ui=None, req=None):
    """Answer a question in note nid's conversation with the model, add the turn to the note, and return the note.
    Stopped meanwhile (stop, by req): Stopped, and the note as it is."""
    doc = store.assemble(pid)
    with _LOCK:
        note = next((n for n in read(pid)["notes"] if n["id"] == nid), None)
    if not note:
        raise ValueError("no such note")
    done = [t for t in turns(note) if t.get("a")]   # answered turns (a pending or failed one is being asked again)
    lang = next((t.get("lang") for t in turns(note) if t.get("lang")), None) or note["anchor"].get("lang") or doc["meta"]["lang"]
    tools = isinstance(spec, dict) and bool(spec.get("tools"))   # (looked up on the web by the agent: the time that takes)
    timeout = max(timeout, 900) if tools else timeout
    text, nums = prompt(doc, note, question, lang, done[-6:], ui if ui in langs.LANGS else None, tools)
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
                              "model": spec["id"], "lang": lang, "at": now(), **({"tools": True} if tools else {})}]
    note.pop("ask", None)
    note["kind"] = "ask"
    with _STOP_LOCK:   # (a stop and the answer crossing: the one second knows of the other)
        if req and req in _STOPPED:
            _STOPPED.discard(req)
            raise Stopped(req)
        put(pid, note)
    return note


def ask(pid, nid, question, spec, timeout=300, ui=None, req=None, parent=None):
    if store.is_local():
        return _local_ask(pid, nid, question, spec, timeout, ui, req)
    token = (pid, nid, req or uuid.uuid4().hex)
    with _LOCK:
        if token in _INFLIGHT:
            raise ValueError("This question is already being answered")
        note = next((n for n in read(pid)["notes"] if n["id"] == nid), None)
        if note and req:
            nodes = {m["id"]: m for m in sync_records.messages(note)}
            previous = nodes.get(req + "-q")
            if previous and previous.get("text") != question:
                raise ValueError("Question request ID was reused")
            if nodes.get(req + "-a", {}).get("text"):
                return next(n for n in present(read(pid))["notes"] if n["id"] == nid)
        _INFLIGHT.add(token)
    try:
        return _ask(pid, nid, question, spec, timeout, ui, req, parent)
    finally:
        with _LOCK:
            _INFLIGHT.discard(token)


def _ask(pid, nid, question, spec, timeout=300, ui=None, req=None, parent=None):
    """Answer a question in note nid's conversation with the model, add the turn to the note, and return the note.
    Stopped meanwhile (stop, by req): Stopped, and the note as it is."""
    doc = store.assemble(pid)
    with _LOCK:
        note = next((n for n in read(pid)["notes"] if n["id"] == nid), None)
    if not note:
        raise ValueError("no such note")
    nodes = sync_records.messages(note)
    question_id = (req or "m-" + uuid.uuid4().hex) + "-q"
    answer_id = question_id[:-2] + "-a"
    existing_question = next((m for m in nodes if m["id"] == question_id), None)
    if existing_question:
        if existing_question.get("text") != question:
            raise ValueError("Question request ID was reused")
        parent = existing_question.get("parent")
    elif parent is None:
        parent = sync_records.last_answer(note)
    if parent is not None and not any(m["id"] == parent for m in nodes):
        raise ValueError("Unknown conversation parent")
    done = [t for t in sync_records.conversation({**note, "messages": nodes}, parent) if t.get("a")] if parent else []
    lang = next((t.get("lang") for t in done if t.get("lang")), None) or note["anchor"].get("lang") or doc["meta"]["lang"]
    tools = isinstance(spec, dict) and bool(spec.get("tools"))
    timeout = max(timeout, 900) if tools else timeout
    text, nums = prompt(doc, note, question, lang, done[-6:], ui if ui in langs.LANGS else None, tools)
    # Only file-based model caching borrows a workspace. It must close before
    # the answer edits the latest notes, which may change during the model call.
    with store.repository().workspace(pid) as folder:
        rec, _ = llm.call(text, ASK_SCHEMA, spec, folder / "llm-cache", timeout=timeout, system=kinds.adapt(SYSTEM_ASK, doc["meta"]))
    out = rec["out"]
    answer, cites = _cited(out["answer"], nums, doc["units"])
    held = []   # the citations kept aside while an id written out in full (not as a citation) becomes its section
    bare = CITE.sub(lambda m: held.append(m.group(0)) or f"\ue000{len(held) - 1}\ue001", answer)
    bare, more = _ids_out(bare, [], doc)
    answer = re.sub("\ue000(\\d+)\ue001", lambda m: held[int(m.group(1))], bare)
    cites += [c for c in more if c not in cites]
    nxt = [re.sub(r"\s+", " ", CITE.sub("", q)).strip() for q in (out.get("next") or []) if isinstance(q, str)]
    turn = {"q": question, "a": answer, "takeaway": CITE.sub("", out["takeaway"]).strip(),
            "cites": cites, "next": [q for q in nxt if q][:2],
            "model": spec["id"], "lang": lang, "at": now(), **({"tools": True} if tools else {})}
    with _STOP_LOCK:   # (a stop and the answer crossing: the one second knows of the other)
        if req and req in _STOPPED:
            _STOPPED.discard(req)
            raise Stopped(req)
        with _LOCK:
            note = next((n for n in read(pid)["notes"] if n["id"] == nid), None)
            if note is None:
                raise ValueError("no such note")   # do not resurrect a deleted note
            nodes = {m["id"]: m for m in sync_records.messages(note)}
            nodes[question_id] = {"id": question_id, "parent": parent, "role": "user", "text": question}
            nodes[answer_id] = {**turn, "id": answer_id, "parent": question_id, "role": "assistant", "text": answer}
            note["messages"] = list(nodes.values())
            note["thread"] = sync_records.conversation(note, answer_id)
            note.pop("ask", None)
            note["kind"] = "ask"
            put(pid, note)
    return next(n for n in present(read(pid))["notes"] if n["id"] == nid)


def cancel_question(pid, nid, req):
    with _LOCK:
        data = read(pid)
        note = next((n for n in data["notes"] if n["id"] == nid), None)
        if not note:
            return
        ids = {req + "-q", req + "-a"}
        note["messages"] = [m for m in sync_records.messages(note) if m["id"] not in ids or not m.get("pending")]
        note["thread"] = sync_records.conversation({"messages": note["messages"],
                                                   "legacy_language": note.get("legacy_language", {})})
        _write(pid, data)


# ── after the text changed (a Markdown document replaced by its edited text, local.replace_text) ───────

def reanchor(pid, old, new):
    """Every note found again in the new text (new, parsed) of a paper whose old text was old. A paragraph still there,
    word for word, keeps its notes as they were (only its id may be another: one read before ids were made from words).
    A note on an edited or moved paragraph is found by its quote: its words where the words around them fit best, else
    what now stands between the words that were around them. A note whose passage is gone is kept, marked "lost" with
    where it was: on the edited paragraph it was on if that is still there, else on its section's title, so that the
    reader lists and shows it; the next replace looks for it again. A conversation's citations follow the paragraphs."""
    with _LOCK:
        if (store.is_local() and not _file(pid).exists()) or (not store.is_local() and store.document(pid).notes is None):
            return None
        data = read(pid)
        moves = Moves(old, new, store.text_lang(pid))
        data["notes"] = [moves.note(n) for n in data["notes"]]
        data["paper"] = {**data.get("paper", {}), "title": new["meta"].get("title", "")}
        _write(pid, data)
    return data


def _order(doc):
    first = [uid for c in doc["chunks"] for uid in c["units"] if uid in doc["units"]]
    return first + [uid for uid in doc["units"] if uid not in set(first)]


def _piece(tk, atoms):
    """A token as the reader quotes it (reader.js, tokPlain): a formula as $LaTeX$, another atom as its text."""
    if "a" in tk:
        at = atoms.get(tk["a"], {})
        return f"${at.get('alt', '')}$" if at.get("kind") == "math" else (at.get("text") or "")
    return f"${tk['x']}$" if "x" in tk else tk.get("t", "")


def _quoted(toks, atoms, a, b, after=False, before=False):
    """Tokens a..b as the reader quotes them (reader.js, toksText), with their spaces."""
    s = ""
    for i in range(a, b + 1):
        s += _piece(toks[i], atoms) + (" " if toks[i].get("s") and (i < b or after) else "")
    return (" " if before and 0 < a <= len(toks) and toks[a - 1].get("s") else "") + s


def _spans(toks, atoms):
    """A unit's text as the reader quotes it, and where each token lies in it."""
    s, spans = "", []
    for i, tk in enumerate(toks):
        piece = _piece(tk, atoms)
        spans.append((len(s), len(s) + len(piece)))
        s += piece + (" " if tk.get("s") and i < len(toks) - 1 else "")
    return s, spans


def _at(doc, uid, start, end):
    """The anchor of characters [start, end) of a unit's text, as the reader makes one: tokens, sentences, quote."""
    u, A = doc["units"][uid], doc["atoms"]
    toks = u["toks"]
    inside = [i for i, (a, b) in enumerate(_spans(toks, A)[1]) if b > start and a < end]
    if not inside:
        return None
    t0, t1 = inside[0], inside[-1]
    return {"unit": uid, "section": u.get("chunk"), "tokens": [t0, t1],
            "sents": [k for k, (a, b) in enumerate(u["sents"]) if b > t0 and a <= t1],
            "quote": {"exact": _quoted(toks, A, t0, t1), "prefix": _quoted(toks, A, max(0, t0 - 12), t0 - 1, after=True)[-40:],
                      "suffix": _quoted(toks, A, t1 + 1, min(len(toks) - 1, t1 + 12), before=True)[:40]}}


def _sentences(doc, uid, sents):
    """The quote of a unit's sentences (first to last) in the text's own words: what a note made in a translation is
    found again by, since its tokens and its quote are the translation's, which is made again."""
    u = doc["units"].get(uid)
    if not u or not sents or max(sents) >= len(u["sents"]):
        return None
    spans = _spans(u["toks"], doc["atoms"])[1]
    got = _at(doc, uid, spans[u["sents"][min(sents)][0]][0], spans[u["sents"][max(sents)][1] - 1][1])
    return got and got["quote"]


def _common(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


EDGE = 20   # what a passage at the start (or end) of its paragraph scores there, when it was at the start before too
FIT = 8     # of several places with the same words, the words around one must fit this far to be chosen (a word or two)


class Moves:
    """Where the old text's paragraphs are in the new one, found by aligning the two in reading order: the paragraphs
    still there word for word (same: a paragraph that repeats another is told apart by its place among the rest), the
    ones edited (near: the most alike, at least half, of the new ones in their place), and where each one is or was among
    the new paragraphs (at)."""

    def __init__(self, old, new, base="en"):
        self.old, self.new, self.base = old, new, base   # (base: the language of the text's own words, a note made on them)
        a, self.order = _order(old), _order(new)
        ta = [plain(old["units"][u]["toks"], old["atoms"]) for u in a]
        tb = [plain(new["units"][u]["toks"], new["atoms"]) for u in self.order]
        self.texts = {uid: _spans(new["units"][uid]["toks"], new["atoms"])[0] for uid in self.order}
        self.same, self.near, self.at = {}, {}, {}
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
            for i in range(i1, i2):
                self.at[a[i]] = j1 + (i - i1 if op == "equal" else 0)
                if op == "equal":
                    self.same[a[i]] = self.order[j1 + i - i1]
            if op != "replace":
                continue
            free = set(range(j1, j2))
            for i in range(i1, i2):
                best, score = None, 0.5
                for j in sorted(free, key=lambda j: abs(j - j1 - (i - i1)))[:12]:   # (the nearest: a long rewrite stays quick)
                    m = difflib.SequenceMatcher(None, ta[i], tb[j], autojunk=False)
                    if m.real_quick_ratio() > score and m.quick_ratio() > score and m.ratio() > score:
                        best, score = j, m.ratio()
                if best is not None:
                    self.near[a[i]] = self.order[best]
                    free.discard(best)

    def chunk(self, uid):
        return self.new["units"][uid].get("chunk")

    def note(self, n):
        a = dict(n.get("anchor") or {})
        n = {**n, "anchor": a}
        self.recite(n)
        if a.get("on") == "paper":
            return n
        lost = a.get("lost")
        was = lost or a   # where its passage was: the note's anchor, or (lost before) where it was when it was lost
        if not was.get("unit"):   # a section's summary or title
            olds = next((c["units"] for c in self.old["chunks"] if c["id"] == a.get("section")), [])
            a["section"] = self.section(a.get("section"), olds)
            return n
        uid = was["unit"]
        if not lost and uid in self.same:
            a.update(unit=self.same[uid], section=self.chunk(self.same[uid]))
            return n
        got = self.passage(a, lost)
        if got:
            a.pop("lost", None)
            a.pop("on", None)
            a.update(got)
            return n
        if not lost:
            lost = {"unit": uid, "section": a.get("section"), "tokens": a.get("tokens"), "sents": a.get("sents"), "since": now()}
            if a.get("lang", self.base) != self.base:   # (its quote is the translation's: the text's own words, to look for next time)
                lost["quote"] = _sentences(self.old, uid, a.get("sents"))
        here = a.get("unit") and (self.same.get(a["unit"]) or self.near.get(a["unit"]))   # its paragraph, edited, still there
        if here:
            a.pop("on", None)
            a.update(unit=here, section=self.chunk(here), tokens=None, sents=None, lost=lost)
        else:
            a.update(unit=None, section=self.section(a.get("section"), [uid]), on="title", tokens=None, sents=None, lost=lost)
        return n

    def passage(self, a, lost=None):
        """A note's passage in the new text, as anchor fields, or None (lost: where it was when it was lost before).
        Words the model wrote (no tokens) are not in the text: such a note goes with its paragraph, edited."""
        was = lost or a
        near, at = self.near.get(was["unit"]), self.at.get(was["unit"])
        if was.get("tokens") is None:
            return {"unit": near, "section": self.chunk(near), "tokens": None, "sents": None} if near else None
        en = a.get("lang", self.base) == self.base   # (on the text's own words)
        q = (a.get("quote") or {}) if en else ((lost or {}).get("quote") or _sentences(self.old, was["unit"], was.get("sents")))
        hit = q and self.found(q, near, at)
        got = hit and _at(self.new, *hit)
        if not got or en:
            return got
        return {"unit": got["unit"], "section": got["section"], "tokens": None, "sents": got["sents"]}

    def found(self, quote, near=None, at=None):
        """Where a quoted passage is in the new text: (unit, start, end) of its words, or None. Each place its exact words
        are is scored by how much of the words before and after it fits (the paragraph it was on, edited, wins a tie,
        then the place nearest its old one); one place alone is taken, but of several, one whose surroundings do not
        fit is not guessed at. With the words themselves edited: what stands between the old words before and after
        them, where those fit in one place only (or in the paragraph it was on)."""
        exact, pre, suf = quote.get("exact") or "", quote.get("prefix") or "", quote.get("suffix") or ""
        if not exact.strip():
            return None
        hits = []
        for k, uid in enumerate(self.order):
            text = self.texts[uid]
            i = text.find(exact)
            while i >= 0:
                j = i + len(exact)
                fit = ((_common(text[:i][::-1], pre[::-1]) if pre else EDGE * (i == 0))
                       + (_common(text[j:], suf) if suf else EDGE * (j == len(text))))
                hits.append((fit, uid == near, -abs(k - at) if at is not None else 0, uid, i, j))
                i = text.find(exact, i + 1)
        if hits:
            top = max(hits, key=lambda h: h[:3])
            return top[3:] if len(hits) == 1 or top[0] >= FIT or top[1] else None
        if len(pre) + len(suf) < 12:   # too little around it to tell where it was
            return None
        gaps = []
        for uid in self.order:
            text = self.texts[uid]
            for s in ([m.end() for m in re.finditer(re.escape(pre), text)] if pre else [0]):
                e = text.find(suf, s) if suf else len(text)
                gap = text[s:e] if e >= 0 else ""
                if gap.strip() and len(gap) <= 2 * len(exact) + 40:
                    gaps.append((uid, s + len(gap) - len(gap.lstrip()), e - len(gap) + len(gap.rstrip())))
        mine = [g for g in gaps if g[0] == near]
        return mine[0] if mine else gaps[0] if len(gaps) == 1 else None

    def section(self, sid, units):
        """Where a note goes in the new text when it has no paragraph: its section, if that is still there; else the one
        most of the given paragraphs are in now; else the one before the place they were; else the first."""
        new = self.new
        if any(c["id"] == sid for c in new["chunks"]):
            return sid
        now_in = [self.chunk(m) for u in units if (m := self.same.get(u) or self.near.get(u)) and self.chunk(m)]
        if now_in:
            return Counter(now_in).most_common(1)[0][0]
        places = [self.at[u] for u in units if u in self.at]
        if self.order and places:
            return self.chunk(self.order[max(0, min(min(places), len(self.order)) - 1)])
        return next((c["id"] for c in new["chunks"] if c["units"]), new["chunks"][0]["id"] if new["chunks"] else None)

    def recite(self, n):
        """A conversation's citations, following the paragraphs they name."""
        moved = {**self.near, **self.same}
        for t in turns(n):
            if t.get("cites"):
                t["cites"] = list(dict.fromkeys(moved.get(c, c) for c in t["cites"]))
            if t.get("a"):
                t["a"] = CITE.sub(lambda m: f"[[{moved.get(m.group(1), m.group(1))}]]", t["a"])
