"""The reader's corrections of a translation, made where it is read (the app's Edit translation): a sentence of it
rewritten, under the sentence of the text it translates (rewrite). The translation is put right in place, its
paragraph's levels made again of its sentences as the reply's were (translate.reword); no model is asked.

They are the reader's own, and kept with the notes (notes.json: corrections), not in the translation, which is made
again when the text is edited or the language generated again: laid over what comes back then (apply), each while the
sentence of the text it translates is still the same. The notes made in the translation stay on their words (_follow).
"""
import re
import threading

from . import notes, store, translate
from .tokens import plain

_LOCK = threading.Lock()   # one correction at a time: each reads the translation, changes it and writes it whole
SHORT = re.compile(r"⟦(\d+)⟧")


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


def _atoms_of(u):
    """The atoms of a paragraph in their order: what ⟦1⟧, ⟦2⟧... stand for in the lines the reader is given."""
    return list(dict.fromkeys(tk["a"] for tk in u["toks"] if "a" in tk))


def lines_of(pid, lang, uid, sents=()):
    """What the reader edits: the sentences asked for (all, when none is) in the text's own words, and their
    translations as lines, each formula, citation or reference a short marker ⟦1⟧ (marks says what each is)."""
    doc, _ = _doc(pid, lang)
    u = doc["units"].get(uid)
    z = u and u.get("tr", {}).get(lang)
    if not z:
        raise ValueError("no-translation")
    ks = sorted({int(k) for k in sents if 0 <= int(k) < len(z["sents"])}) or list(range(len(z["sents"])))
    ids = _atoms_of(u)
    short = {a: str(n + 1) for n, a in enumerate(ids)}
    said = translate.said(z)["sentences"]
    out = [re.sub(r"⟦(a\d+)⟧", lambda m: f"⟦{short.get(m.group(1), m.group(1))}⟧", said[k]) for k in ks]
    marks = [{"n": short[a], "what": notes._piece({"a": a}, doc["atoms"])} for a in ids
             if any(f"⟦{short[a]}⟧" in x for x in out)]
    return {"unit": uid, "sents": ks, "source": [_source(doc, uid, k) for k in ks], "lines": out, "marks": marks}


def _saved(pid, doc, lang, tr, before):
    """The translation written as corrected, and the notes made in it moved with their words."""
    store.save_tr(doc, lang, tr["base"], tr.get("model", ""))
    notes.correct(pid, lambda data: _follow(data, doc, lang, before))


def rewrite(pid, lang, uid, lines):
    """Sentences of a paragraph's translation as the reader wrote them ({k: line}, markers as lines_of gave them).
    ValueError: "markers" when a line lost or gained a formula's or a citation's marker, "empty" when nothing is left."""
    with _LOCK:
        doc, tr = _doc(pid, lang)
        u = doc["units"].get(uid)
        z = u and u.get("tr", {}).get(lang)
        if not z:
            raise ValueError("no-translation")
        long = {str(n + 1): a for n, a in enumerate(_atoms_of(u))}
        said = translate.said(z)["sentences"]
        got = {}
        for k, text in (lines or {}).items():
            k = int(k)
            if not 0 <= k < len(said):
                raise ValueError("no-sentence")
            text = SHORT.sub(lambda m: f"⟦{long.get(m.group(1), 'x')}⟧", re.sub(r"\s+", " ", str(text)).strip())
            if sorted(re.findall(r"⟦[^⟧]*⟧", text)) != sorted(re.findall(r"⟦[^⟧]*⟧", said[k])):
                raise ValueError("markers")
            if text != said[k]:
                got[k] = text
        if not got:
            return {"changed": 0}
        made = translate.reword(doc, lang, uid, got)
        if not made:
            raise ValueError("empty")
        before = _snapshot(doc, lang)
        u["tr"][lang] = made
        _saved(pid, doc, lang, tr, before)
        notes.correct(pid, lambda data: _keep_lines(data, lang, [(uid, k, _source(doc, uid, k), x) for k, x in got.items()]))
        return {"changed": len(got)}


def kept(pid, lang):
    """The corrections kept for lang: {"lines": [{unit, k, source, text}, ...]}."""
    return ((notes.read(pid).get("corrections") or {}).get(lang)) or {}


def apply(pid, doc, lang):
    """A translation made again (pipeline.generate), put right as the reader put it before: each sentence rewritten
    whose sentence of the text is still the same. (Before it is saved: no note has moved yet.)"""
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


# ── kept with the notes ──────────────────────────────────────────────────────────────────────────────────────────
def _keep_lines(data, lang, got):
    mine = data.setdefault("corrections", {}).setdefault(lang, {})
    lines = [x for x in mine.get("lines") or [] if (x.get("unit"), x.get("k")) not in {(uid, k) for uid, k, _, _ in got}]
    mine["lines"] = lines + [{"unit": uid, "k": k, "source": src, "text": text} for uid, k, src, text in got]


# ── the notes made in the translation, on their words again ─────────────────────────────────────────────────────
def _snapshot(doc, lang):
    return {"units": {uid: u["tr"][lang] for uid, u in doc["units"].items() if lang in u.get("tr", {})}}


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
        if a.get("lang") != lang:
            continue
        exact = (a.get("quote") or {}).get("exact") or ""
        if a.get("unit") in before["units"]:
            old, z = before["units"][a["unit"]], doc["units"][a["unit"]]["tr"][lang]
            if old == z or not a.get("tokens"):
                continue
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
