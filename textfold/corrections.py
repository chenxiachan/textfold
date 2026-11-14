"""The reader's corrections of a translation, made where it is read (the app's Edit translation), as the text's own
words are (Edit text): the whole translation opened as text, at the words selected, saved as written, and then either
kept as it is or its shorter levels written again by the model from the sentences as corrected.

The translation is written as Markdown of a shape that reads back one to one (text_of): the paper's title (#), each
section's title (##), each paragraph's sentences one a line, a blank line between paragraphs; formulas as $LaTeX$,
citations and references as markers ⟦a12 [12]⟧ (what is inside after the id is what the reader sees, for them; the id
is what counts). A translation is sentence by sentence, one to one with the text's (Key picks its sentences, a note is
found again by them), so the edited text must keep that shape: the same titles, paragraphs and lines, each line
rewritten as the reader likes (read_back says where it does not).

The corrections are the reader's own, and kept with the notes (notes.json: corrections), not in the translation, which
is made again when the text is edited or the language generated again: laid over what comes back then (apply), each
while the words of the text it translates are still the same. The notes made in the translation stay on their words
(_follow). Saved to be generated again, the paragraphs changed are marked in the translation (relevel), and the next
generation of the language writes their levels again (translate.relevel), the sentences kept as the reader wrote them.
"""
import re
import threading

from . import notes, store, translate
from .tokens import plain

_LOCK = threading.Lock()   # one correction at a time: each reads the translation, changes it and writes it whole
MARKER = re.compile(r"⟦(a\d+)[^⟧]*⟧")


def _doc(pid, lang):
    """The text with its ladder and its translation into lang (the layer as stored); ValueError("no-translation")."""
    tr = store.read(pid, f"tr-{lang}") if lang != store.text_lang(pid) else None
    en = store.read(pid, "en")
    if tr is None or en is None:
        raise ValueError("no-translation")
    doc = store.parsed(pid)
    store.apply_en(doc, en)
    store.apply_tr(doc, lang, tr)
    return doc, tr


def _source(doc, uid, k):
    """Sentence k of a paragraph in the text's own words, as the reader quotes it."""
    u = doc["units"][uid]
    a, e = u["sents"][k]
    return plain(u["toks"][a:e], doc["atoms"]).strip()


def _shown(toks, atoms):
    """A sentence's tokens as a line to edit: a formula as $LaTeX$ (read back as that formula), another atom as its
    marker with what the reader sees of it."""
    line = translate.line(toks)

    def one(m):
        at = atoms.get(m.group(1), {})
        if _formula(atoms, m.group(1)):
            return f"${at['alt']}$"
        seen = re.sub(r"\s+", " ", at.get("text") or "").strip()
        return f"⟦{m.group(1)}{' ' + seen if seen else ''}⟧"
    return MARKER.sub(one, line)


def _formula(atoms, aid):
    at = atoms.get(aid, {})
    return at.get("kind") == "math" and bool(at.get("alt"))


def _shape(doc, lang):
    """What the text holds, in order: ("title", None), ("head", chunk), ("para", uid) for each paragraph translated."""
    out = [("title", None)]
    for c in doc["chunks"]:
        us = [uid for uid in c["units"] if lang in doc["units"][uid].get("tr", {})]
        if c["title"] and (us or lang in c.get("tr", {})):
            out.append(("head", c))
        out += [("para", uid) for uid in us]
    return out


def _title(doc, lang):
    return (doc["meta"].get("titles") or {}).get(lang) or doc["meta"].get("title") or ""


def _head(c, lang):
    return ((c.get("tr") or {}).get(lang) or {}).get("title") or c["title"]


def text_of(pid, lang, unit=None, sents=()):
    """The translation as text to edit, and where the sentences selected are in it ([from, to], characters; None)."""
    doc, _ = _doc(pid, lang)
    parts, at = [], None
    for kind, x in _shape(doc, lang):
        if kind == "title":
            parts.append(f"# {_title(doc, lang)}")
        elif kind == "head":
            parts.append(f"## {_head(x, lang)}")
        else:
            z = doc["units"][x]["tr"][lang]
            lines = [_shown(z["toks"][a:e], doc["atoms"]) for a, e in z["sents"]]
            if x == unit:
                start = sum(len(p) + 2 for p in parts)
                ks = [k for k in sents if 0 <= k < len(lines)] or [0]
                a = start + sum(len(ln) + 1 for ln in lines[:min(ks)])
                at = [a, a + sum(len(ln) + 1 for ln in lines[min(ks):max(ks) + 1]) - 1]
            parts.append("\n".join(lines))
    return {"text": "\n\n".join(parts) + "\n", "at": at}


class Shape(ValueError):
    """The edited text does not read back one to one: what is wrong ("lines", "head", "more", "less", "markers"), at
    which line of it, and the sentences the paragraph has in the text (want) and now (got)."""
    def __init__(self, what, line, want=None, got=None):
        super().__init__(what)
        self.what, self.line, self.want, self.got = what, line, want, got


def _blocks(text):
    """The text's blocks: (first line's number, 1-based; its lines)."""
    out, cur, start = [], [], 0
    for n, ln in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if ln.strip():
            if ln.lstrip().startswith("#") and cur:   # (a title is a block of its own, a blank line before it or not)
                out.append((start, cur))
                cur = []
            if not cur:
                start = n
            cur.append(ln.strip())
            if ln.lstrip().startswith("#"):
                out.append((start, cur))
                cur = []
        elif cur:
            out.append((start, cur))
            cur = []
    if cur:
        out.append((start, cur))
    return out


def read_back(doc, lang, text):
    """What the edited text changes: {"title": text?, "heads": {chunk id: title}, "lines": {uid: {k: line}}}. Shape
    when it does not read back one to one."""
    shape, blocks = _shape(doc, lang), _blocks(text)
    got = {"title": None, "heads": {}, "lines": {}}
    for i, (kind, x) in enumerate(shape):
        if i >= len(blocks):
            raise Shape("less", blocks[-1][0] + len(blocks[-1][1]) if blocks else 1)
        n, lines = blocks[i]
        heading = re.match(r"^(#{1,6})\s+(.*)$", lines[0])
        if kind in ("title", "head"):
            if not heading:
                raise Shape("head", n)
            new = heading.group(2).strip()
            if kind == "title" and new != _title(doc, lang):
                got["title"] = new
            elif kind == "head" and new != _head(x, lang):
                got["heads"][x["id"]] = new
            continue
        z = doc["units"][x]["tr"][lang]
        if heading or len(lines) != len(z["sents"]):
            raise Shape("lines", n, len(z["sents"]), len(lines))
        for k, (ln, (a, e)) in enumerate(zip(lines, z["sents"])):
            was = _shown(z["toks"][a:e], doc["atoms"])
            if ln != was:
                mine = MARKER.sub(lambda m: f"⟦{m.group(1)}⟧", ln)
                want = [m for m in MARKER.findall(translate.line(z["toks"][a:e])) if not _formula(doc["atoms"], m)]
                if sorted(MARKER.findall(mine)) != sorted(want):   # (a formula is $LaTeX$ in the line, not a marker)
                    raise Shape("markers", n + k)
                got["lines"].setdefault(x, {})[k] = mine
    if len(blocks) > len(shape):
        raise Shape("more", blocks[len(shape)][0])
    return got


def save(pid, lang, text, relevel=False):
    """The translation as the reader edited it (text_of's text), put in place and kept; relevel: its paragraphs changed
    marked to have their levels written again at the next generation. Shape when it does not read back."""
    with _LOCK:
        doc, tr = _doc(pid, lang)
        got = read_back(doc, lang, text)
        if not (got["title"] or got["heads"] or got["lines"]):
            return {"changed": 0, "relevel": len(tr.get("relevel") or [])}
        before = {uid: u["tr"][lang] for uid, u in doc["units"].items() if lang in u.get("tr", {})}
        for uid, lines in got["lines"].items():
            made = translate.reword(doc, lang, uid, lines)
            if not made:
                raise Shape("lines", 0)
            doc["units"][uid]["tr"][lang] = made
        _titles(doc, lang, got)
        marked = sorted(set(tr.get("relevel") or []) | (set(got["lines"]) if relevel else set()))
        store.save_tr(doc, lang, tr["base"], tr.get("model", ""), relevel=marked)
        kept_lines = [(uid, k, _source(doc, uid, k), x) for uid, lines in got["lines"].items() for k, x in lines.items()]
        heads = {cid: (next(c["title"] for c in doc["chunks"] if c["id"] == cid), t) for cid, t in got["heads"].items()}
        title = (doc["meta"].get("title") or "", got["title"]) if got["title"] else None
        notes.correct(pid, lambda data: (_keep(data, lang, kept_lines, heads, title), _follow(data, doc, lang, before)))
        return {"changed": len(kept_lines) + len(heads) + bool(title), "relevel": len(marked)}


def _titles(doc, lang, got):
    for c in doc["chunks"]:
        if c["id"] in got["heads"]:
            c.setdefault("tr", {}).setdefault(lang, {"title": c["title"], "lad": None})["title"] = got["heads"][c["id"]]
    if got["title"]:
        doc["meta"].setdefault("titles", {})[lang] = got["title"]


def kept(pid, lang):
    """The corrections kept for lang: {"lines": [{unit, k, source, text}], "heads": {chunk: {source, text}},
    "title": {source, text}}."""
    return ((notes.read(pid).get("corrections") or {}).get(lang)) or {}


def apply(pid, doc, lang):
    """A translation made again (pipeline.generate), put right as the reader put it before: each sentence rewritten
    whose sentence of the text is still the same, each title whose title in the text is. (Before it is saved: no note
    has moved yet.)"""
    c = kept(pid, lang)
    by_unit = {}
    for x in c.get("lines") or []:
        u = doc["units"].get(x.get("unit"))
        k = x.get("k")
        if u and lang in u.get("tr", {}) and isinstance(k, int) and k < len(u["sents"]) and _source(doc, x["unit"], k) == x.get("source"):
            by_unit.setdefault(x["unit"], {})[k] = x["text"]
    for uid, got in by_unit.items():
        made = translate.reword(doc, lang, uid, got)
        if made:
            doc["units"][uid]["tr"][lang] = made
    heads = {cid: h["text"] for cid, h in (c.get("heads") or {}).items()
             if any(ch["id"] == cid and ch["title"] == h.get("source") for ch in doc["chunks"])}
    t = c.get("title") or {}
    _titles(doc, lang, {"heads": heads, "title": t.get("text") if t.get("source") == doc["meta"].get("title") else None})


# ── kept with the notes ──────────────────────────────────────────────────────────────────────────────────────────
def _keep(data, lang, lines, heads, title):
    mine = data.setdefault("corrections", {}).setdefault(lang, {})
    done = {(uid, k) for uid, k, _, _ in lines}
    mine["lines"] = [x for x in mine.get("lines") or [] if (x.get("unit"), x.get("k")) not in done] + [
        {"unit": uid, "k": k, "source": src, "text": text} for uid, k, src, text in lines]
    for cid, (src, text) in heads.items():
        mine.setdefault("heads", {})[cid] = {"source": src, "text": text}
    if title:
        mine["title"] = {"source": title[0], "text": title[1]}


# ── the notes made in the translation, on their words again ─────────────────────────────────────────────────────
def _found(toks, atoms, exact, near):
    """Tokens [first, last] whose words are exact, the place nearest the character `near`; None when not there."""
    text, spans = notes._spans(toks, atoms)
    at, best = -1, None
    while exact:
        at = text.find(exact, at + 1)
        if at < 0:
            break
        if best is None or abs(at - near) < abs(best - near):
            best = at
    if best is None:
        return None
    inside = [i for i, (a, b) in enumerate(spans) if b > best and a < best + len(exact)]
    return [inside[0], inside[-1]] if inside else None


def _start(toks, atoms, tokens):
    spans = notes._spans(toks, atoms)[1]
    return spans[tokens[0]][0] if tokens and tokens[0] < len(spans) else 0


def _follow(data, doc, lang, before):
    """Each note made in lang on a paragraph whose translation changed, on its words again: where they are now, else on
    the sentences it was on."""
    A = doc["atoms"]
    for n in data.get("notes", []):
        a = n.get("anchor") or {}
        if a.get("lang") != lang or a.get("unit") not in before or not a.get("tokens"):
            continue
        old, z = before[a["unit"]], doc["units"][a["unit"]]["tr"][lang]
        if old == z:
            continue
        exact = (a.get("quote") or {}).get("exact") or ""
        got = _found(z["toks"], A, exact, _start(old["toks"], A, a["tokens"]))
        if got is None:   # its words rewritten: the sentences it was on, which stay one to one
            ks = [k for k in a.get("sents") or [] if k < len(z["sents"])] or [0]
            got = [z["sents"][min(ks)][0], z["sents"][max(ks)][1] - 1]
        t0, t1 = got
        a["tokens"] = [t0, t1]
        a["sents"] = [k for k, (s, e) in enumerate(z["sents"]) if e > t0 and s <= t1]
        a["quote"] = {"exact": notes._quoted(z["toks"], A, t0, t1),
                      "prefix": notes._quoted(z["toks"], A, max(0, t0 - 12), t0 - 1, after=True)[-40:],
                      "suffix": notes._quoted(z["toks"], A, t1 + 1, min(len(z["toks"]) - 1, t1 + 12), before=True)[:40]}
        if a.get("shown"):   # a line of its levels it was made on (Key, Takeaway): its words there, else the passage
            w = _found(z["lad"]["brief"], A, a["shown"].get("exact") or "", 0)
            if w:
                a["shown"] = {"words": w, "exact": notes._quoted(z["lad"]["brief"], A, w[0], w[1])}
            else:
                a.pop("shown")
    return data
