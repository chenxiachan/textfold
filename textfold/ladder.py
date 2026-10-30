"""Each unit's ladder: Full ⊃ Key ⊃ Brief ⊃ Takeaway ⊃ Topic.

Same split as ThoughtDAG's ladder (src/lib/ladder.ts), for the same reason:
a model told to only delete words paraphrases anyway, and pure deletion
leaves the short levels meaningless. So:
  Full      the unit as the author wrote it
  Key       1 to 3 of its sentences, VERBATIM, chosen by the model as evidence
  Brief     takeaway + one sentence of reason, WRITTEN
  Takeaway  a headline sentence, WRITTEN (Brief starts with it)
  Topic     a noun phrase, cut from inside the takeaway when the model's
            phrase is there verbatim, else the model's phrase as written
Written words that also occur in the key sentences carry `m`, the index of
that source token, so the morph can slide the word instead of swapping it.
One model call per chunk (a heading and the units under it).
"""
import bisect
import difflib
import re
from concurrent.futures import ThreadPoolExecutor

from . import kinds, langs, llm
from .tokens import is_cjk, lcs_pairs, norm, plain, tokenize_text, words

ROLES = ["context", "gap", "claim", "method", "setup", "result", "analysis", "limitation"]
BUDGET = {"takeaway": 14, "reason": 22, "topic": 3, "section": 16}
# A CJK text's budgets. The English ones are word counts, and tokens.py counts a CJK character as a word, while a
# Chinese sentence of the length an English one needs holds about three times the characters; a Chinese topic is
# a phrase of a few characters, not three words.
CJK_BUDGET = {"takeaway": 40, "reason": 60, "topic": 8, "section": 40}
# A CJK local takeaway is cut at a clause or sentence edge inside its budget, not at a word count: the stock one
# cut mid-clause and the reader showed it (a takeaway ending "…的含义2013"). The topic is the takeaway's first
# noun-ish run: a leading discourse filler (随着 / 因此 / 首先 …), a list marker or an ordinal is not what a unit
# is about. A section heading keeps its ordinal: "第二节" is not "第二……".
CJK_EDGE = "。！？；"          # strong edges: a takeaway may end after one, punctuation kept
CJK_SOFT = "，、："            # soft edges: end before one, it is dropped
CJK_FILLER = ("随着", "根据", "由于", "因此", "但是", "而且", "并且", "不过", "然而", "所以", "如果", "虽然", "尽管",
              "目前", "首先", "其次", "再次", "最后", "此外", "另外", "同时", "综上", "总之", "基于", "鉴于",
              "针对", "对于", "关于", "近年来", "当前", "实践中", "从我国", "从中国")
CJK_NUMERAL = "一二三四五六七八九十百千"
CJK_ORDINAL_RE = re.compile(r"第[" + CJK_NUMERAL + r"]|其[一二三四]|一[二三是]")
CJK_SECTION_TAIL = ("节", "章", "编", "条", "款", "项", "部分")   # "第二节" is a heading: its ordinal stays
CJK_ORDINAL_TAIL = ("第一", "第二", "第三", "第四", "一是", "二是", "三是")   # a topic never ends inside an ordinal
CJK_TOPIC_TAIL = "的地得了着与和及或"                                        # a topic never ends on a function word
CJK_DANGLER = "主是支第被把将能可各每"     # characters that almost only start a word: a Han character after one means the topic was cut mid-word, so step back
CJK_TOPIC_EDGE = CJK_EDGE + CJK_SOFT + ".,;:!?"                            # boundaries inside a topic span (a half-width stop in a quotation counts)
_FILLER_SORTED = sorted(CJK_FILLER, key=len, reverse=True)
CJK_MARKERS = "①②③④⑤⑥⑦⑧⑨⑩⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽⒈⒉⒊⒋⒌⒍⒎⒏⒐⒑⁰¹²³⁴⁵⁶⁷⁸⁹†‡§¶"   # footnote and citation markers: never part of a topic
CJK_LABEL_RE = re.compile(r"^(?:图|表|Figure|Fig\.?|Tab\.?|Table)\s*\d+(?:\s*[-–—]\s*\d+)?\s*")   # the "图 1—1 " label prefix
CJK_TAIL_PUNCT = CJK_EDGE + CJK_SOFT + ".,;:!?)）】]」』”’\"'"               # a topic is a label: it does not end on punctuation
# Apparatus: a contents line (dot leaders), a footnote (a leading ①-⑩ together with a citation shape), an English
# footnote. These are the plumbing of a paper, not its argument: they fold to nothing and stay as they are at Full.
TOC_LINE = re.compile(r"[.．…·]\s*[.．…·]\s*[.．…·]")                      # the dot leaders of a contents line ("第一章 …… 1")
CITE_MARK = re.compile(r"\[[A-Za-z/]+\]|出版社|学报|：\s*第?\s*\d{1,4}\s*[-–—]\s*\d{1,4}|pp?\.\s*\d"
                       r"|\bDOI\b|\bISSN\b|CN:\s*\d|[EMDJ]B/OL|https?://|www\.")
FOOT_START = re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*")
FOOT_NOTE = re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]?\s*《[^》]{2,30}》\s*第\s*\d+\s*条")   # a statutory note at the head of a line


def is_apparatus(u, atoms):
    """True for a unit that is the paper's plumbing, not its argument: a contents line, a footnote
    (citation, cross-reference, statutory note). Such a unit folds to nothing and stays at Full."""
    t = plain(u["toks"], atoms).strip()
    if not t:
        return True
    if len(t) < 120 and TOC_LINE.search(t):
        return True
    if FOOT_START.match(t):
        if CITE_MARK.search(t):
            return True
        if FOOT_NOTE.match(t) or "参见" in t or "前引" in t or "同前注" in t:
            return True
        ascii_ratio = sum(c.isascii() and c.isalpha() for c in t) / max(1, sum(c.isalpha() for c in t))
        if ascii_ratio > 0.6 and len(t) < 300:      # an English footnote ("① Ronald J. Mann, …")
            return True
    return False


def suppressed():
    """A ladder that renders nothing below Full: no brief, no takeaway, no topic, no evidence."""
    return {"role": "context", "ev": [], "brief": [], "tn": 0, "topic": [], "model": "local"}


# A CJK unit often opens with a lead-in ("以……教授为主要代表人物的……论") or a conditional ("而如果不……的话");
# the real claim comes after it. local() therefore takes the sentence that reads most like a claim, not the first.
CJK_CLAIM = ("认为", "主张", "应当", "应该", "需要", "必须", "旨在", "在于", "而是", "可见", "因此",
             "所以", "证明", "表明", "说明", "标志", "意味着", "构成", "属于", "界定", "确立", "即：")
CJK_COND_LEAD = ("而如果", "如果", "若", "倘若", "假如", "尽管", "虽然", "由于", "随着", "鉴于", "但是")


def _claim_sentence(u, atoms):
    """The sentence that states the point, not necessarily the first: an introduction or a conditional
    set-up opens many Chinese units, and the claim comes a sentence or two later."""
    best, best_score = 0, -99.0
    for i, (s, e) in enumerate(u["sents"][:4]):
        t = plain(u["toks"][s:e], atoms)
        if len(t) < 10:
            continue
        score = 0.4 - 0.6 * i
        if any(m in t for m in CJK_CLAIM):
            score += 3
        if "：" in t or ":" in t:
            score += 1.5
        if any(t.startswith(m) for m in CJK_COND_LEAD):
            score -= 2.5
        if score > best_score:
            best, best_score = i, score
    return u["sents"][best] if u["sents"] else None

SCHEMA = {
    "type": "object",
    "properties": {
        "section": {"type": "object", "properties": {"takeaway": {"type": "string"}, "topic": {"type": "string"}},
                    "required": ["takeaway", "topic"]},
        "units": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "role": {"type": "string", "enum": ROLES},
            "takeaway": {"type": "string"}, "topic": {"type": "string"}, "reason": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "integer"}}},
            "required": ["id", "role", "takeaway", "topic", "reason", "evidence"]}},
    },
    "required": ["section", "units"],
}

KIND = {"para": "paragraph", "item": "list item", "abstract": "abstract", "caption": "caption"}


def _cjk_share(text):
    """CJK share among word characters: dot leaders and punctuation must not make a Chinese heading look
    English ("三、立法位阶低且滞后.........." is 18% CJK over all characters but all of its word characters)."""
    w = [c for c in text if c.isalnum() or is_cjk(c)]
    return (sum(is_cjk(c) for c in w) / len(w)) if w else 0.0


def budgets_of(u):
    """The budgets for one unit's own text (no document at hand): a CJK unit's are in characters."""
    text = "".join(tk.get("t") or "" for tk in u["toks"])
    return CJK_BUDGET if _cjk_share(text) > 0.2 else BUDGET


def budgets_for(doc):
    """The budgets for a whole document, from its text: its units carry the CJK share."""
    text = "".join(tk.get("t") or "" for u in doc["units"].values() for tk in u["toks"])[:4000]
    return CJK_BUDGET if _cjk_share(text) > 0.2 else BUDGET


def cjk_doc(doc):
    """A document written in Chinese or Japanese. Only such a document takes the CJK rules (budgets in characters,
    apparatus, the section's background, the half sentence a page break left): a paper in English folds as it did
    before them, the same prompt and the same levels."""
    return budgets_for(doc) is CJK_BUDGET


def outline(doc, here):
    rows = []
    for c in doc["chunks"]:
        pad = "  " * max(0, c["level"] - 2)
        rows.append(f"{pad}{'▶ ' if c['id'] == here else ''}{(c['num'] + ' ') if c['num'] else ''}{c['title']}")
    return "\n".join(rows)


def tables_of(doc, chunk, limit=1500, most=3):
    """The section's tables as text, a row a line (they are not units: a section's summary should still know them).
    A section without one gets nothing, so its prompt, and its cached answer, stay as they were."""
    from bs4 import BeautifulSoup
    out = []
    for b in doc["blocks"]:
        html = b.get("html") or ""
        table = (b["k"] == "fig" and b.get("kind") == "table") or (b["k"] == "raw" and "<table" in html and "ltx_equation" not in html)
        if b.get("chunk") == chunk["id"] and table and "<table" in html:   # (not an equation's table)
            soup = BeautifulSoup(html, "lxml")
            for m in soup.find_all("math"):   # a formula once, as its TeX (not its MathML and its TeX again)
                m.replace_with(f"${m['alttext']}$" if m.get("alttext") else m.get_text(" ", strip=True))
            rows = [" | ".join(c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])) for tr in soup.find_all("tr")]
            text = "\n".join(r for r in rows if r.strip(" |")).replace("\u200b", "")
            if text:
                out.append(text[:limit] + (" …" if len(text) > limit else ""))
    return out[:most]


def prompt_for(doc, chunk):
    U, A = doc["units"], doc["atoms"]
    b = budgets_for(doc)
    cjk = b is CJK_BUDGET
    # written in the text's own language (pipeline: store.text_lang); a CJK text's lines name none, as before
    own = langs.name(doc["meta"].get("lang") if doc["meta"].get("lang") in langs.LANGS else "en")
    one = "ONE sentence" if cjk else f"ONE {own} sentence"
    more = "ONE more sentence" if cjk else f"ONE more {own} sentence"
    count = "characters" if cjk else "words"
    rules = """

Each line is read on its own, out of order, by someone deciding whether to zoom back in:
- Never supply a fact the unit does not contain: no name, standard, system, number or date of your own. When it writes "the cross-border payment system", write that, not the name of one particular system.
- The line must stand alone: do not open it with 该, 此, 其, 上述, this, these or it. Name the thing again.
- A term a non-specialist would not know is glossed in eight characters or fewer where it first appears.
- Keep every number the point depends on, each with its anchor: what it counts, in which year, for whom.
- A process is who acts, through which channel, to whom. A bare sequence of steps is not a takeaway.
- The reason sentence gives the mechanism or the consequence, not where the claim comes from: not "supported by X", not "as Y shows", unless the source itself is the finding.""" if cjk else ""
    job = ("stating what this section establishes in the paper and what it does there (fixes the terms, surveys the rules, "
           "measures the gap, compares a foreign regime, proposes a remedy); a line that only says what the section covers "
           "is not a summary.") if cjk else "stating what this section establishes in the paper;"
    abstract = " ".join(plain(U[u]["toks"], A) for u in doc["chunks"][0]["units"]) if doc["chunks"] and doc["chunks"][0]["id"] == "abstract" else ""
    blocks = []
    for n, uid in enumerate(chunk["units"]):
        u = U[uid]
        kind = KIND.get(u["role"], "paragraph")
        if u["role"] == "caption":
            fig = next((b for b in doc["blocks"] if b.get("cap") == uid), {})
            kind = f"caption of {fig.get('label') or fig.get('kind', 'figure')}"
        if u.get("env"):
            kind = f"{kind} inside {u['env']['title']}"
        lines = [f"[U{n}] {kind}"] + [f"  {i}: {plain(u['toks'][s:e], A)}" for i, (s, e) in enumerate(u["sents"])]
        blocks.append("\n".join(lines))
    head = f"{chunk['num']} {chunk['title']}".strip()
    tables = tables_of(doc, chunk)
    context = ("\n\nTables in this section, for context (they are not units; a takeaway may use what they show):\n\n" + "\n\n".join(tables)) if tables else ""
    prompt = kinds.adapt(f"""Paper: {doc['meta']['title']}

Paper abstract, for context:
{abstract}

Outline (▶ marks this section):
{outline(doc, chunk['id'])}

This section: {head}

Its units in reading order. A unit is a paragraph, a list item, or a figure or table caption; each unit's sentences are numbered from 0.

{chr(10).join(blocks)}{context}

A reader zooms out of this paper level by level: full text, then key sentences, then brief, then takeaway, then topic. Write the short levels for every unit, and one takeaway for the whole section.

For each unit:
- role: the unit's job in the paper's argument, one of: context (background, prior work), gap (what prior work lacks or gets wrong), claim (a thesis or hypothesis the paper puts forward), method (what the authors built or how it works), setup (data, parameters, protocol), result (a measured finding), analysis (an interpretation, mechanism or theory), limitation (a caveat or future work).
- takeaway: {one} of at most {b['takeaway']} {count} stating what this unit establishes: the claim, design choice, number or finding itself, conclusion first, packed like a headline. Name the thing itself, not the activity ("this paragraph describes", "the authors discuss", "details of"). Keep the paper's own terms. Someone reading only the takeaways of the whole paper in order should be able to follow its argument.
- topic: the unit's subject as a bare noun phrase of at most {b['topic']} {count}, copied VERBATIM from inside the takeaway.
- reason: {more} of at most {b['reason']} {count} that reads on from the takeaway and gives its main reason, mechanism, condition or consequence; do not restate the takeaway. Use "" when the unit is a single short sentence.
- evidence: the numbers of the 1 or 2 sentences (3 only when the unit has 7 or more) whose verbatim text best backs the takeaway, ascending; prefer sentences that state a result, a definition or a decision over sentences that only set up or list.
- A unit that is not a passage of the text (a label, a heading read as a line of text, an author's note or address, a date), or that is already as short as its takeaway would be, stays as written: give its takeaway, topic and reason as "".{rules}

For the section: takeaway, ONE sentence of at most {b['section']} {count} {job} topic, at most {b['topic']} {count} copied verbatim from that takeaway.

Style: no em or en dashes as punctuation, use commas or colons (hyphenated terms such as "zero-shot" are fine). Units show math as $...$; when a symbol is essential to a takeaway or reason, copy its $...$ exactly as it appears in the unit, otherwise avoid math. No citations; no figure or table numbers except in a caption's own lines.

Return JSON: {{"section": {{"takeaway": "...", "topic": "..."}}, "units": [{{"id": "U0", "role": "...", "takeaway": "...", "topic": "...", "reason": "...", "evidence": [0]}}, ...]}} with exactly one entry per unit, in order.""", doc["meta"])
    if cjk:   # the style rules above are written for English punctuation
        prompt = prompt.replace("no em or en dashes as punctuation, use commas or colons",
                                "use the punctuation of the text's own language")
    return prompt


def clean(s):
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"\s*——\s*", "，", s)
    s = re.sub(r"\s*[—–]\s*|\s+-\s+", ", ", s)
    return re.sub(r"[\s.;,!?:。；，！？：、]+$", "", s)


MARK = re.compile(r"⟦(a\d+)[^⟧]*⟧|\$([^$]+)\$")


def written(text, u, atoms, ev, toks=None, sents=None):
    """A written line as tokens. A marker ⟦a12⟧ is that atom; $...$ that matches one of the unit's own formulas
    becomes that atom too (the formula then slides between levels), anything else is rendered with KaTeX."""
    toks, sents = (u["toks"], u["sents"]) if toks is None else (toks, sents)
    out = []
    key_atoms = [tk["a"] for s in ev if s < len(sents) for tk in toks[sents[s][0]:sents[s][1]] if "a" in tk]
    all_atoms = key_atoms + [tk["a"] for tk in toks if "a" in tk]
    squash = lambda x: re.sub(r"\s+", "", x)  # noqa: E731
    pos = 0
    for m in MARK.finditer(text):
        tokenize_text(text[pos:m.start()], None, out)
        if m.group(1):
            if m.group(1) in atoms:
                out.append({"a": m.group(1)})
        else:
            hit = next((a for a in all_atoms if atoms[a].get("kind") == "math" and squash(atoms[a].get("alt", "")) == squash(m.group(2))), None)
            out.append({"a": hit} if hit else {"x": m.group(2)})
        pos = m.end()
    tokenize_text(text[pos:], None, out)
    return out


def locate(topic, take):
    """The topic as a run of the takeaway's tokens: indices, or [] when it is not in there verbatim."""
    squash = lambda tk: re.sub(r"\W", "", (tk.get("t") or "").lower())  # noqa: E731
    want = re.sub(r"\W", "", topic.lower())
    if not want:
        return []
    for i in range(len(take)):
        if not squash(take[i]) or not want.startswith(squash(take[i])):
            continue
        acc = ""
        for j in range(i, len(take)):
            acc += squash(take[j])
            if acc == want:
                return list(range(i, j + 1))
            if not want.startswith(acc):
                break
    return []


def is_word(tk):
    return "a" in tk or "x" in tk or bool(re.search(r"\w", tk.get("t", "")))


def head_words(toks, n):
    """Indices of the first n words (punctuation between them included, none at the end)."""
    idx, w = [], 0
    for i, tk in enumerate(toks):
        if is_word(tk) and w == n:
            break
        idx.append(i)
        w += is_word(tk)
    while idx and not is_word(toks[idx[-1]]):
        idx.pop()
    return idx


def assemble(u, o, atoms, model, cjk=None):
    """cjk: the unit is of a CJK document (cjk_doc); None: judged from the unit's own text."""
    cjk = budgets_of(u) is CJK_BUDGET if cjk is None else cjk
    if cjk and is_apparatus(u, atoms):   # an apparatus unit does not fold, even when the model wrote one for it
        return {"role": "context", "ev": [], "brief": [], "tn": 0, "topic": [], "model": model}
    ns = len(u["sents"])
    ev = sorted({int(x) for x in (o.get("evidence") or []) if isinstance(x, int) and 0 <= x < ns})[:3] or [0]
    take_text, reason_text = clean(o.get("takeaway")), clean(o.get("reason"))
    if not take_text:
        return local(u, atoms, cjk)
    take = written(take_text, u, atoms, ev)
    dot = "。" if cjk and _cjk_share(take_text + reason_text) > 0.2 else "."
    brief = [dict(tk) for tk in take]
    if reason_text:
        brief.append({"t": dot} if dot == "。" else {"t": dot, "s": 1})
        brief += written(reason_text, u, atoms, ev)
    brief.append({"t": dot})
    if take:
        take[-1].pop("s", None)
    tn = len(take)
    align(u, brief, ev, atoms)
    topic = topic_tokens(clean(o.get("topic")), brief, tn, u, atoms, ev, (CJK_BUDGET if cjk else BUDGET)["topic"])
    return {"role": o.get("role") if o.get("role") in ROLES else "context", "ev": ev, "brief": brief, "tn": tn,
            "topic": topic, "model": model}


def align(u, brief, ev, atoms, toks=None, sents=None):
    toks, sents = (u["toks"], u["sents"]) if toks is None else (toks, sents)
    key = [i for s in ev if s < len(sents) for i in range(*sents[s])]
    for bi, kj in lcs_pairs([norm(tk, atoms) for tk in brief], [norm(toks[i], atoms) for i in key]):
        brief[bi]["m"] = key[kj]


def _skip_marker(text, i):
    """Skip a list marker like "1.", "2、" or "三、" at i; a date ("2019年") or a version ("3.5") is kept."""
    j = i
    while j < len(text) and (text[j].isdigit() or text[j] in CJK_NUMERAL):
        j += 1
    if j > i and j < len(text) and text[j] in ".,、)）;；:：":
        return j + 1
    return i


def _skip_ordinal(text, i):
    """Skip an ordinal like "第一，" or "一是" at i; a section heading ("第二节"), a "第十二条", a plain
    "第三方" or a "第一次" (an ordinal run followed straight by a content character) keeps it."""
    m = CJK_ORDINAL_RE.match(text, i)
    if m is None or text.startswith(CJK_SECTION_TAIL, m.end()):
        return i
    if text.startswith("第", i) and m.end() < len(text) and text[m.end()] not in CJK_SOFT + CJK_EDGE + " ":
        return i
    return m.end()


def _skip_filler(text, i):
    """Skip punctuation, one discourse filler and the punctuation after it ("第一，为了…" → "为了…")."""
    while i < len(text) and text[i] in CJK_SOFT + CJK_EDGE + " ":
        i += 1
    hit = next((f for f in _FILLER_SORTED if text.startswith(f, i)), None)
    if hit is None:
        return i
    i += len(hit)
    while i < len(text) and text[i] in CJK_SOFT + CJK_EDGE + " ":
        i += 1
    return i


def cjk_topic_span(text, n):
    """A CJK topic's char span in `text` (in the text's own coordinates): footnote markers (①②…) are dropped
    wherever they sit ("第③方支付" → "第三方支付"), a figure/table label, list markers, ordinals and leading
    fillers are skipped, the span is cut at a clause edge (a natural phrase may run a few characters past the
    budget) or at the budget, and it never ends on punctuation: a topic is a label, not a sentence."""
    clean, back = "", []                      # the text without markers, and where each clean char sat in the original
    for k, ch in enumerate(text):
        if ch not in CJK_MARKERS:
            clean += ch
            back.append(k)
    if not clean:
        return None
    i = 0
    while i < len(clean) and not is_cjk(clean[i]) and not (clean[i].isascii() and clean[i].isalnum()):
        i += 1                               # "①", punctuation, spaces
    i = _skip_marker(clean, i)                # "1.", "2、", "三、" (a date or a version number stays)
    i = _skip_ordinal(clean, i)               # "第一，"/"一是" ("第二节" stays)
    m = CJK_LABEL_RE.match(clean, i)          # the label prefix of "图 1—1 …", "表 2 …"
    if m:
        i = m.end()
    i = _skip_filler(clean, i)                # a filler, and the punctuation after it
    for p in range(i, min(i + 5, len(clean) - 1)):    # a second filler inside the first few characters ("我国对于…")
        nxt = _skip_filler(clean, p)
        if nxt != p:
            i = nxt
            break
    if i >= len(clean):
        return None
    j, edge = i, None
    while j < min(len(clean), i + n + 5):
        if clean[j] in CJK_TOPIC_EDGE:
            edge = j
            break
        j += 1
    if edge is not None:
        end = edge + 1 if clean[edge] in CJK_EDGE else edge
    else:
        end = min(len(clean), i + n)
        while end < len(clean) and (clean[end].isdigit() or clean[end - 1].isdigit()):
            end += 1                           # never cut a run of digits in half ("…4月29日国家…")
    while end > i + 1 and clean[end - 1] in CJK_TOPIC_TAIL:
        end -= 1
    while end - i >= 4 and clean[end - 2:end] in CJK_ORDINAL_TAIL:
        end -= 2                               # never end inside an ordinal ("…认为第三")
    if (i + 2 < end < len(clean) and clean[end - 1] in CJK_DANGLER
            and is_cjk(clean[end]) and clean[end] not in CJK_TOPIC_EDGE):
        end -= 1                               # never end on a word-initial character ("…的创新之处主" → "…的创新之处")
    while end > i + 1 and clean[end - 1] in CJK_TAIL_PUNCT:
        end -= 1                               # drop the closing punctuation ("…备考属性。" → "…备考属性")
    if end - i < 2:
        return None
    return (back[i], back[end - 1] + 1)        # back to the coordinates of the original text


def cjk_takeaway(brief, n):
    """Indices of a CJK takeaway: the first n words, but ending at the last clause edge inside them. An edge in
    the first two tokens is a fragment marker (a line-break shard like "构，"), not a clause, so edges start at
    token 2; the head's end counts one token past its last word (head_words drops trailing punctuation, and a
    sentence-final "。" right there is still an edge); with no edge inside the budget, the first edge within 50%
    past it is used before falling back."""
    head = head_words(brief, n)
    if not head:
        return head
    limit = head[-1] + 1
    far = min(len(brief), limit + 1 + n // 2)
    edges = [k for k in range(2, far)
             if any(c in CJK_EDGE + CJK_SOFT + "," for c in (brief[k].get("t") or ""))]
    inside = [k for k in edges if k <= limit]
    edge = inside[-1] if inside else (edges[0] if edges else None)
    if edge is None:
        return head
    strong = any(c in CJK_EDGE for c in (brief[edge].get("t") or ""))
    return list(range(edge + 1)) if strong else list(range(edge))


def cjk_topic(brief, tn, n):
    """Indices of a CJK topic inside brief[:tn] (cjk_topic_span over its text, mapped back to tokens)."""
    text, starts = "", []
    for i in range(tn):
        starts.append(len(text))
        text += brief[i].get("t") or ""
    span = cjk_topic_span(text, n)
    if span is None:
        return head_words(brief[:tn], n)
    lo = bisect.bisect_left(starts, span[0])
    hi = bisect.bisect_left(starts, span[1])
    rng = list(range(lo, max(hi, lo + 1)))
    kept = [k for k in rng if (brief[k].get("t") or "").strip(CJK_MARKERS + " ")]
    return kept or rng                      # a token that is only a footnote marker is not part of a topic


def local(u, atoms, cjk=None):
    """No model reply: the first sentence stands in for brief, its head for the shorter levels. In a CJK document
    (cjk; None: judged from the unit's own text) the unit's claim sentence stands in, and apparatus (a contents
    line, a footnote) folds to nothing: it stays at Full and hides below it."""
    if not (budgets_of(u) is CJK_BUDGET if cjk is None else cjk):
        s, e = u["sents"][0]
        brief = [dict(tk, m=s + k) for k, tk in enumerate(u["toks"][s:e])]
        head = head_words(brief, BUDGET["takeaway"])
        tn = (head[-1] + 1) if head else len(brief)
        topic = [{**{x: brief[i][x] for x in ("t", "a", "x", "s", "f") if x in brief[i]}, "k": i} for i in head_words(brief[:tn], 3)]
        return {"role": "context", "ev": [0], "brief": brief, "tn": tn, "topic": topic, "model": "local"}
    if is_apparatus(u, atoms):
        return suppressed()
    span = _claim_sentence(u, atoms)
    if span is None:
        return suppressed()
    s, e = span
    brief = [dict(tk, m=s + k) for k, tk in enumerate(u["toks"][s:e])
             if not ((tk.get("t") or "").strip(CJK_MARKERS + " ") == "" and "a" not in tk and "x" not in tk)]
    if not brief:
        return suppressed()
    b = budgets_of(u)
    cjk = _cjk_share("".join(tk.get("t") or "" for tk in brief)) > 0.2
    head = cjk_takeaway(brief, b["takeaway"]) if cjk else head_words(brief, b["takeaway"])
    if cjk and head and any(("".join(brief[i].get("t") or "" for i in head)).startswith(m) for m in CJK_COND_LEAD):
        strong = [k for k in range(head[-1] + 1, min(len(brief), head[-1] + 1 + b["takeaway"]))
                  if any(c in CJK_EDGE for c in (brief[k].get("t") or ""))]
        head = list(range(strong[0] + 1)) if strong else list(range(len(brief)))   # a half conditional says nothing: run on to the next stop
    tn = (head[-1] + 1) if head else len(brief)
    tix = cjk_topic(brief, tn, b["topic"]) if cjk else head_words(brief[:tn], b["topic"])
    topic = []
    for i in tix:
        tk = {**{x: brief[i][x] for x in ("t", "a", "x", "s", "f") if x in brief[i]}, "k": i}
        t = re.sub("[" + re.escape(CJK_MARKERS) + "]", "", tk.get("t") or "")
        if not t and "a" not in tk and "x" not in tk:
            continue                      # a token that is only a footnote marker is not part of a topic
        tk["t"] = t
        topic.append(tk)
    if not topic:                          # everything was stripped: fall back to the head words
        topic = [{**{x: brief[i][x] for x in ("t", "a", "x", "s", "f") if x in brief[i]}, "k": i}
                 for i in head_words(brief[:tn], b["topic"])]
    return {"role": "context", "ev": [0], "brief": brief, "tn": tn, "topic": topic, "model": "local"}


BACKGROUND = {"context", "setup"}     # a unit that states no point of its own: it sets the scene or the data
CAP = 2                               # how many background lines in a row one section may show at Takeaway
CJK_ISSUE = re.compile(r"^\s*(《[^》]{2,40}》|(19|20)\d{2}\s*年)")   # a line whose subject is the document it came from
SAME = 0.8                            # how close a unit's takeaway has to be to its section's line to be a repeat


def _flat(text):
    return re.sub(r"[\s，。、：；！？,.:;!?“”‘’\"'（）()《》]", "", text or "")


def _take(lad):
    return "".join(tk.get("t") or "" for tk in lad["brief"][:lad["tn"]])


def repeats_section(lad, section, same=None, prefix=False):
    """True when the unit's takeaway is the section's own one-line summary again, word for word or close to it.
    Both come from one reply, so this is common, and at Takeaway a section then says the same sentence twice:
    the summary under the heading, and a bullet. The summary keeps it, the bullet steps down."""
    if not section:
        return False
    a = _flat(_take(lad) or "".join(tk.get("t") or "" for tk in lad["brief"]))   # a line that stepped down has no takeaway left
    b = _flat("".join(tk.get("t") or "" for tk in section.get("take") or []))
    if len(a) < 8 or len(b) < 8:
        return False
    # 一方是另一方的前缀：条目就是摘要把那句话又说了一遍，只是后面多带了理由。相似度算不出来
    # （0.73，差一点点），但这种重复在读的时候一眼就看得出。
    if prefix and (a.startswith(b) or b.startswith(a)) and min(len(a), len(b)) >= 12:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= (same or SAME)


def citation_first(lad):
    """A background line that opens with 《…》 or with a year answers "where was this written", not "what does the
    paper establish". It steps down to Brief, where the reader who wants the document still finds it. A line with
    a colon states a specification or a list, so it stays; a unit that is not mostly Chinese is never one."""
    take = _take(lad).strip()
    if _cjk_share(take) <= 0.2 or "：" in take or ":" in take:
        return False
    if any(m in take for m in CJK_CLAIM):
        return False
    return bool(CJK_ISSUE.match(take))


def fold_background(chunk, doc):
    """What a section shows at Takeaway: its points, not its reading list.

    A Chinese thesis section often runs three or four background units in a row (this document in this year,
    the next one in the next year). At Takeaway each of them becomes a line, and a page of such lines reads as
    a table of contents: the reader who zoomed out that far is looking for the argument, not for where each
    fact came from. So a section keeps at most CAP background lines in a row, a line that opens with its own
    source yields its place to the section's one-line summary, and a line that repeats that summary yields to it
    as well. Nothing is lost: Brief and the levels below still carry every line in full.
    """
    U, section = doc["units"], chunk.get("lad")
    lads = [(uid, U[uid].get("lad") or {}) for uid in chunk["units"]]
    text = "".join(tk.get("t") or "" for _, l in lads for tk in l["brief"][:l.get("tn") or 0])
    if not cjk_doc(doc) or _cjk_share(text) <= 0.2:
        return      # an English paper folds exactly as it did before: these thresholds were set on Chinese theses
    first = next((i for i, (_, l) in enumerate(lads) if l.get("brief") and l.get("tn")), None)
    if first is None:     # nothing in this section folds: leave it alone
        return
    run = 0
    for i, (uid, l) in enumerate(lads):
        if not l.get("brief") or not l.get("tn"):
            run = 0
            continue
        if repeats_section(l, section):
            l["tn"] = 0
            continue
        if i == first or l.get("role") not in BACKGROUND:
            run = 0       # the section's opening line always stands: a heading with nothing under it says less
            continue
        run += 1
        if run > CAP or citation_first(l):
            l["tn"] = 0   # the line steps down to Brief: same words, one level out


NOTE = re.compile(r"^\s*[①-⑳]")        # a footnote the PDF left as a block of its own, marker and all
WHOLE = set("。！？.!?：:；;”’」』）)")    # a sentence that ends here needs nothing from the one before
GLUE = 40                               # characters of a cut-off tail the next unit carries in at Brief


def _is_note(u, atoms):
    """A block that is not prose: a footnote marker the PDF left on its own, or a figure's caption."""
    return u.get("role") == "caption" or bool(NOTE.match(plain(u["toks"], atoms)))


def _tail(u, atoms):
    """The sentence a page break cut in half, as far back as it has to go to stand on its own.

    Empty when the unit ends whole. Otherwise the last clause, joined with the one before it while the run is
    short: "而该条例的法律位阶为行政" needs "过去…都是部门规章" in front of it, and "至少需要五名…" needs
    "…提出最低要求". A whole sentence is not wanted either — the reader gets the contrast, not the paragraph.
    """
    t = plain(u["toks"], atoms).strip()
    t = re.sub(r"[①-⑳]+$", "", t).rstrip()   # a footnote marker follows the stop, it does not end the sentence
    if not t or t[-1] in WHOLE:
        return ""
    cut = 0
    for m in re.finditer(r"[。！？!?]", t):
        cut = m.end()            # 前面那句是完整的，上一单元已经显示过：再带一遍就是重复
    sent = t[cut:] if len(t[cut:]) >= 4 else t
    tail = sent
    for m in reversed(list(re.finditer(r"[，,；;：:]", sent))):
        tail = sent[m.end():]
        if len(tail) >= 20:     # enough of a run to carry a subject and a contrast
            break
    if len(tail) < 20 and tail != sent:
        tail = sent             # the last clause is too short to stand on its own: carry the whole sentence
    out = tail[-GLUE:]
    if len(tail) > GLUE:        # cut at a clause mark near the cut, never through a word
        m = re.search(r"[，,；;：:。]", out[:10])
        if m:
            out = out[m.end():]
    return out.lstrip("。！？.!?，,；;：:”’\"）)")


def _widen(u, lad, atoms, most=6):
    """glue restores the front of the sentence, which leaves this unit's first sentence as a bare tail
    ("要求。"). Key shows that one sentence and the reader sees a line that stops as soon as it has said
    anything. The sentence after it comes in too, and the line means something."""
    sents = u.get("sents") or []
    if len(sents) < 2:
        return
    a, b = sents[0]
    if len(plain(u["toks"][a:b], atoms).strip()) > most:
        return
    lad["ev"] = sorted(set(lad.get("ev") or []) | {0, 1})[:3]


def dress(chunk, doc):
    """The sentence a page break cut in half. A paragraph split across a page break arrives as two units: the first
    ends mid-word ("…的法律位阶为行政", "…熟悉外汇业务的工作人"), the second opens with the rest ("法规，加强对…",
    "员，并且在…"). Brief shows one sentence per unit, so the reader meets the second half alone and the sentence has
    no subject. The tail of the first is carried into the second as `glue`, and the sentence arrives whole. A
    footnote between the two halves is stepped over, not quoted.

    Every unit stays at Brief, the background whose takeaway stepped down too: a unit read at one level is read at
    every level in from it (the zoom only adds), and a line that steps down is still read one level in.

    A CJK document's only: an English paper's units end where its sentences do, and Brief shows them as they were.
    """
    if not cjk_doc(doc):
        return
    U, A = doc["units"], doc["atoms"]
    lads = [(uid, U[uid].get("lad") or {}) for uid in chunk["units"]]
    for i, (uid, l) in enumerate(lads):
        if not l.get("brief") or _is_note(U[uid], A):
            continue
        j = i - 1
        while j >= 0 and _is_note(U[lads[j][0]], A):
            j -= 1                        # the footnote sits between the halves; the paragraph is still cut
        if j < 0:
            continue
        tail = _tail(U[lads[j][0]], A)
        if tail:
            l["glue"] = tail
            _widen(U[uid], l, A)


def topic_tokens(text, brief, tn, u, atoms, ev, budget=None, toks=None, sents=None):
    """Topic tokens; `k` is the takeaway token a topic word slides from. A phrase the model wrote that is not in the
    takeaway verbatim is kept when short (ThoughtDAG does the same): it read better than a cut of the takeaway's head."""
    budget = budget or BUDGET["topic"]
    loc = locate(text, brief[:tn])
    if loc:
        return [{**{x: brief[i][x] for x in ("t", "a", "x", "s", "f") if x in brief[i]}, "k": i} for i in loc]
    if text and sum(map(is_word, tokenize_text(text))) <= budget + 2:
        own = written(text, u, atoms, ev, toks, sents)
        for ti, bi in lcs_pairs([norm(tk, atoms) for tk in own], [norm(tk, atoms) for tk in brief[:tn]]):
            own[ti]["k"] = bi
        if own:
            own[-1].pop("s", None)
        return own
    return [{**{x: brief[i][x] for x in ("t", "a", "x", "s", "f") if x in brief[i]}, "k": i} for i in head_words(brief[:tn], budget)]


def section_ladder(o, topic_budget=3, doc=None, chunk=None):
    if isinstance(o, str):   # a model that wrote the section's takeaway alone, not as {takeaway, topic}
        o = llm.as_obj(o) or {"takeaway": o}
    o = o if isinstance(o, dict) else {}
    take_text = clean(o.get("takeaway"))
    if not take_text:
        return None
    if doc and chunk:  # $...$ in a section line becomes one of the section's own formulas
        pool = [{"a": tk["a"]} for uid in chunk["units"] for tk in doc["units"][uid]["toks"] if "a" in tk]
        take = written(take_text, {"toks": pool, "sents": []}, doc["atoms"], [])
    else:
        take = tokenize_text(take_text)
    tb = budgets_for(doc)["topic"] if doc else topic_budget
    return {"take": take, "topic": locate(clean(o.get("topic")), take) or head_words(take, tb)}


def build(doc, cfg, cache_dir, jobs=4, log=print, progress=None, force=False, stop=None):
    chunks = [c for c in doc["chunks"] if c["units"]]
    cost, warnings, errors = 0.0, [], []
    model = llm.label(cfg)
    b = budgets_for(doc)
    cjk = b is CJK_BUDGET

    def run(c):
        if stop is not None and stop.is_set():   # stopped: the chunks not yet sent are skipped
            return c, None, llm.Stopped()
        try:
            return c, llm.call(prompt_for(doc, c), SCHEMA, cfg, cache_dir, force, system=llm.system_for(doc["meta"]),
                               **({"stop": stop} if stop is not None else {})), None
        except Exception as e:  # one failed chunk falls back to local ladders, the rest still build
            return c, None, e

    if progress:
        progress(0, len(chunks))
    with ThreadPoolExecutor(jobs) as ex:   # (each section as its reply comes: what is done with one reads only its own)
        for n_done, (c, res, err) in enumerate(llm.as_answered(ex, run, chunks, log, model), 1):
            if progress:
                progress(n_done, len(chunks))
            if stop is not None and stop.is_set():
                raise llm.Stopped()
            if err:
                errors.append(err)
                log(f"  ! {c['id']}: {err}")
                continue
            rec, cached = res
            cost += 0 if cached else (rec.get("cost") or 0)
            out = rec["out"] if isinstance(rec["out"], dict) else {}
            try:
                by_id = {x.get("id"): x for x in llm.units_of(out)}
                for n, uid in enumerate(c["units"]):
                    o = by_id.get(f"U{n}")
                    u = doc["units"][uid]
                    u["lad"] = assemble(u, o, doc["atoms"], model, cjk) if o else local(u, doc["atoms"], cjk)
                    if not o:
                        warnings.append(f"{uid}: missing from reply")
                    elif words(u["lad"]["brief"][:u["lad"]["tn"]]) > b["takeaway"] + (4 if not cjk else 15):
                        warnings.append(f"{uid}: long takeaway ({words(u['lad']['brief'][:u['lad']['tn']])} {'characters' if cjk else 'words'})")
                c["lad"] = section_ladder(out.get("section"), 3, doc, c)
                fold_background(c, doc)   # what the section shows at Takeaway, once its units are folded
                dress(c, doc)             # the sentence a page break cut in half; the background Brief leaves out
            except Exception as e:   # a reply of a shape not foreseen: this chunk's units fall back, as for a failed call
                errors.append(RuntimeError(f"{model}: a reply TextFold could not read ({type(e).__name__}: {e})"))
                log(f"  ! {c['id']}: {errors[-1]}")
                llm.forget(rec)
                for uid in c["units"]:
                    doc["units"][uid].pop("lad", None)
                continue
            log(f"  {'·' if cached else '✓'} {c['id']:<14} {len(c['units']):>2} units")
    if chunks and len(errors) == len(chunks):  # nothing came back: a backend problem, not a paper problem
        raise RuntimeError(str(errors[0]))
    for u in doc["units"].values():
        u.setdefault("lad", local(u, doc["atoms"], cjk))
    for w in warnings:
        log(f"  ~ {w}")
    return cost
