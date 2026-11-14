"""A LaTeXML page as the reader's document.

Text lives in units (a paragraph, a list item, a figure caption), each a
token stream split into sentences; units get a ladder later. Everything
else (display equations, figure bodies, tables) is kept as the HTML
LaTeXML wrote. Headings open chunks: the unit of one model call and one
card on the map.

A unit's id is the page's own (arXiv's S2.p3), else its place (u12). A
document stored with "unit_ids": "text" in its meta (a Markdown file,
local.py) names each unit by its words instead (p-<hash>), so that a
paragraph keeps its id, and the notes and links on it, when the text
around it is edited. An id written into the page when it was stored
(data-u on a paragraph, data-s on a section: ids.py) comes first.
"""
import copy
import hashlib
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .latexml_safe import make_safe
from .semantic import _equation, upgrade
from .tokens import plain, split_sentences, tokenize_text

SECTION_LEVEL = {
    "ltx_section": 2, "ltx_appendix": 2, "ltx_subsection": 3,
    "ltx_subsubsection": 4, "ltx_paragraph": 5, "ltx_subparagraph": 5,
}
BLOCKISH = {"div", "table", "figure", "img", "ul", "ol", "svg", "object", "pre"}


def blockish(el):
    """A block inside el, which keeps it from being read as text. An image marked as part of a line (pf-inline: a
    formula some JATS draws as a picture, semantic.py) is not one."""
    return el.find(lambda t: t.name in BLOCKISH and not (t.name == "img" and "pf-inline" in t.get("class", [])))


class Doc:
    def __init__(self, meta):
        self.meta, self.atoms, self.units, self.chunks, self.blocks = meta, {}, {}, [], []
        self.images, self.bib, self.chunk = [], "", None
        # Text outside any section is still read: after the abstract and before section 1 (a teaser's caption, a closing
        # line) it belongs to the abstract; above the abstract (a teaser on the title page), to the first section.
        self.last, self.loose = None, []
        # every id a cross-reference may point at -> {"k": unit|fig|eq|chunk|bib, "id": what the reader calls it}
        self.targets, self.edges = {}, []
        self.env = None  # the theorem, definition or proof the walk is inside of
        self.by_text, self.seen = meta.get("unit_ids") == "text", {}
        self.made = None   # (ids.freeze: the units and sections read from each marked element)
        self.frozen = set()   # every id written into the page (data-u): one worked out here does not take one

    def text_id(self, toks):
        """A unit's id made from its words: the same paragraph has it wherever it moves, whatever changes around it.
        A paragraph that repeats one before it word for word is told apart by its count, in reading order (p-…-2)."""
        h = "p-" + hashlib.sha1(plain(toks, self.atoms).encode("utf-8")).hexdigest()[:8]
        self.seen[h] = self.seen.get(h, 0) + 1
        return h if self.seen[h] == 1 else f"{h}-{self.seen[h]}"

    def target(self, el, kind, tid):
        for e in [el] + [d for d in el.find_all(id=True) if not d.find_parent("math") and d.name != "math"]:
            if e.get("id"):
                self.targets.setdefault(e["id"], {"k": kind, "id": tid})

    def open_chunk(self, cid, num, title, html, level, app=False):
        self.chunk = self.last = {"id": cid, "num": num, "title": title, "html": html, "level": level, "app": app, "units": []}
        self.chunks.append(self.chunk)
        self.blocks.append({"k": "h", "chunk": cid})
        if self.loose:   # read before any section: shown just under this one's heading, and read with it
            moved = [b for b in self.blocks if b.get("chunk") is None and (b.get("u") or b.get("cap")) in self.loose]
            self.blocks = [b for b in self.blocks if not any(b is m for m in moved)] + [{**b, "chunk": cid} for b in moved]
            for uid in self.loose:
                self.units[uid]["chunk"] = cid
            self.chunk["units"].extend(self.loose)
            self.loose = []

    def home(self):
        """The section text is read with: the open one, else the last one (after the abstract), else none yet."""
        return self.chunk or self.last

    def add(self, block):
        uid = block.get("u") or block.get("cap")
        block["chunk"] = (self.units[uid].get("chunk") if uid in self.units else None) or (self.chunk["id"] if self.chunk else None)
        self.blocks.append(block)

    def json(self):
        alias = {k: v["id"] for k, v in self.targets.items() if v["k"] == "unit" and k != v["id"]}
        return {"meta": self.meta, "atoms": self.atoms, "units": self.units, "chunks": self.chunks,
                "blocks": self.blocks, "bib": self.bib, "images": self.images, "edges": self.edges, "alias": alias}


def math_alt(el):
    """A formula's LaTeX. LaTeXML gives some formulas as MathML alone ("0.25°" in GraphCast): their visible text, so the
    model still reads the number."""
    alt = el.get("alttext") or ""
    if not alt.strip():
        ann = el.find("annotation", attrs={"encoding": "application/x-tex"})
        alt = ann.get_text("") if ann else re.sub(r"[\u200b-\u200d\u2061-\u2064]", "", el.get_text(""))
    return re.sub(r"\s+", " ", alt).strip()


def text_of(el):
    """Visible text with math as $latex$ (MathML's own text repeats the annotation)."""
    if isinstance(el, NavigableString):
        return "" if isinstance(el, Comment) else str(el)
    if el.name == "math":
        return f"${math_alt(el)}$"
    if el.name == "annotation":
        return ""
    return "".join(text_of(c) for c in el.children)


ANNOTATIONS = ("annotation", "annotation-xml")


def shown(el, strip=False):
    """An element's text as the page shows it. A formula's annotations (its LaTeX, its content MathML) are not drawn,
    and get_text reads them too: a list marker of MathML read "∙\\bullet" (#128), a figure's label, a section's number or
    a link with a formula in it would repeat their LaTeX the same way."""
    if el.find(ANNOTATIONS):
        el = copy.copy(el)
        for a in el.find_all(ANNOTATIONS):
            a.decompose()
    return el.get_text("", strip=strip)


def font_of(el, inherited):
    fonts = [c for c in el.get("class", []) if c.startswith("ltx_font_")]
    if el.name in ("em", "i") and not fonts:
        fonts = ["ltx_font_italic"]
    if el.name in ("strong", "b") and not fonts:
        fonts = ["ltx_font_bold"]
    merged = sorted(set((inherited or "").split()) | set(fonts))
    return " ".join(merged) or None


def flatten(node, doc, toks, font=None):
    for ch in node.children:
        if isinstance(ch, Comment):
            continue
        if isinstance(ch, NavigableString):
            tokenize_text(str(ch), font, toks)
            continue
        if not isinstance(ch, Tag):
            continue
        cls = ch.get("class", [])
        if "ltx_nodisplay" in cls:   # hidden on arXiv's page too (an ACM figure's description, "TeaserFigure")
            continue
        if ch.name == "math":
            atom(doc, toks, ch, "math", alt=math_alt(ch))
        elif ch.name == "cite":
            atom(doc, toks, ch, "cite", text=shown(ch))
        elif ch.name == "a":
            atom(doc, toks, ch, "ref", text=shown(ch))
        elif ch.name == "br":
            if toks:
                toks[-1]["s"] = 1
        elif "ltx_note" in cls:
            atom(doc, toks, ch, "note", text="")
        elif ch.name in ("span", "em", "strong", "b", "i") and not blockish(ch):
            flatten(ch, doc, toks, font_of(ch, font))
        else:
            atom(doc, toks, ch, "html", text=text_of(ch))


def atom(doc, toks, el, kind, alt="", text=""):
    aid = f"a{len(doc.atoms)}"
    doc.atoms[aid] = {"kind": kind, "html": str(el), **({"alt": alt} if alt else {}), "text": text}
    toks.append({"a": aid})


LONG = 12  # a paragraph with more sentences than this is cut into parts of about PART sentences
PART = 7
CJK_TEXT = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def cjk_heavy(toks):
    """A CJK paragraph keeps all its sentences but is never cut into parts: cutting it would change the sentence
    budget the Chinese ladder is written against, and — because a cut part takes the next free id — shift every
    later unit's id, so the layer data (keyed on ids) would land on the wrong paragraphs."""
    total = sum(len(tk.get("t") or "") for tk in toks)
    cjk = sum(len(CJK_TEXT.findall(tk.get("t") or "")) for tk in toks)
    return total > 0 and cjk / total > 0.2


def worded(doc, tk, links=True):
    """A token that shows words: a word, or an atom with words of its own (inline code's, a citation's; a link's, when
    links). A link to a place on this same page (#…) is not: a list of such links is the page's contents or a list of
    its own sentences to go to, its words the page's own, read where they are."""
    if "t" in tk:
        return True
    at = doc.atoms.get(tk.get("a"), {})
    if at.get("kind") == "ref":
        if not links:
            return False
        href = (re.search(r'\shref="([^"]*)"', at.get("html") or "") or [None, ""])[1]
        page = str(doc.meta.get("abs_url") or "").split("#")[0].rstrip("/")
        if href.startswith("#") or (page and "#" in href and href.split("#")[0].rstrip("/") == page):
            return False
    return at.get("kind") in ("ref", "cite", "html") and bool(re.search(r"\w", at.get("text") or ""))


def add_units(doc, el, role, uid=None, split=True, linked=None, **extra):
    """The text of el as one unit, or as several when the author wrote a page-long paragraph: a 28-sentence
    introduction zoomed as a single unit keeps 3 sentences of 28 at Key and says nothing at Topic. linked, for a list's
    item: a unit even when its words are all inline code (False), or in links too (True: an item of a list whose other
    items have words of their own)."""
    toks = []
    flatten(el, doc, toks)
    if not any("t" in tk if linked is None else worded(doc, tk, linked) for tk in toks):
        return []
    sents = split_sentences(toks)
    parts = [(0, len(toks), sents)]
    if split and len(sents) > LONG and not cjk_heavy(toks):
        k = -(-len(sents) // PART)
        size = -(-len(sents) // k)
        parts = []
        for i in range(0, len(sents), size):
            grp = sents[i:i + size]
            a, b = grp[0][0], grp[-1][1]
            parts.append((a, b, [[x - a, y - a] for x, y in grp]))
    frozen = (el.get("data-u") or "").split()   # the ids it was stored with (ids.py), a part's each
    base = (frozen[:1] or [None])[0] or uid or el.get("id") or f"u{len(doc.units)}"
    ids = []
    for n, (a, b, ss) in enumerate(parts):
        u = frozen[n] if n < len(frozen) else doc.text_id(toks[a:b]) if doc.by_text else base if n == 0 else f"{base}~{n + 1}"
        while u in doc.units or (n >= len(frozen) and u in doc.frozen):
            u += "'"
        home = doc.home()
        doc.units[u] = {"id": u, "role": role, "toks": toks[a:b], "sents": ss,
                        "chunk": home["id"] if home else None, **extra, **({"env": doc.env} if doc.env else {})}
        if home:
            home["units"].append(u)
        else:
            doc.loose.append(u)
        doc.targets.setdefault(u, {"k": "unit", "id": u})
        ids.append(u)
    para = el.find_parent(class_="ltx_para")
    if para is not None and para.get("id"):  # "see paragraph S2.p3" points at the wrapper; its first text stands for it
        doc.targets.setdefault(para["id"], {"k": "unit", "id": ids[0]})
    if el.get("id"):   # (named otherwise when it was stored: a reference to the element still finds it)
        doc.targets.setdefault(el["id"], {"k": "unit", "id": ids[0]})
    if doc.made is not None and el.get("data-tf-k"):
        doc.made["units"][el["data-tf-k"]] = ids
    return ids


def add_unit(doc, el, role, uid=None, **extra):
    ids = add_units(doc, el, role, uid, split=False, **extra)
    return ids[0] if ids else None


def heading(el):
    h = el.find(re.compile(r"^h[1-6]$"), class_="ltx_title", recursive=False)
    if not h:
        return "", "", ""
    tag = h.find(class_="ltx_tag")
    num = shown(tag, strip=True) if tag else ""
    if tag:
        tag.extract()
    html = h.decode_contents().strip()
    return num, re.sub(r"\s+", " ", text_of(h)).strip(), html


def in_appendix(el):
    """An appendix is one LaTeXML named (ltx_appendix), or a section sitting inside one: the element's own
    class, or an ancestor's — never the section read just before it (a plain sibling after the appendices, a
    NeurIPS checklist, is body all the same)."""
    node = el
    while isinstance(node, Tag):
        if "ltx_appendix" in (node.get("class") or []):
            return True
        node = node.parent
    return False


def section(el, doc):
    cls = el.get("class", [])
    level = next((v for k, v in SECTION_LEVEL.items() if k in cls), 5)
    num, title, html = heading(el)
    cid = el.get("data-s") or el.get("id") or f"c{len(doc.chunks)}"
    doc.targets.setdefault(cid, {"k": "chunk", "id": cid})
    if el.get("id"):
        doc.targets.setdefault(el["id"], {"k": "chunk", "id": cid})
    if doc.made is not None and el.get("data-tf-k"):
        doc.made["chunks"][el["data-tf-k"]] = cid
    doc.open_chunk(cid, num, title, html, level, app=in_appendix(el))
    walk(el, doc)


def items(el):
    """A list's items as (marker, the element holding the item's text, an element to leave out). A description list
    (LaTeX's description) has a term and its text; a term with words leads the text's first paragraph, so it is read,
    translated and zoomed with it. A list put straight in a list, not in one of its items (<ul><ul>, as Zhihu writes
    a nested list), is drawn by a browser as a nested list: given as itself, marker None, to be read a level deeper."""
    if el.name != "dl":
        for li in el.find_all(["li", "ul", "ol"], recursive=False):
            if li.name != "li":
                yield None, li, None
                continue
            tag = li.find(class_="ltx_tag", recursive=False)
            yield (shown(tag, strip=True) if tag else "•"), li, tag
        return
    for dt in el.find_all("dt", recursive=False):
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        p = dd.find("p", class_="ltx_p")
        term = dt.find(class_="ltx_tag") or dt
        if p is not None and term.get_text("", strip=True):
            p.insert(0, " ")
            for node in reversed(list(term.contents)):
                p.insert(0, node)
        yield "", dd, None


def _beside_links(li):
    """An item's words outside its links (a reference's authors beside its linked title)."""
    return any(re.search(r"\w", s) for s in li.find_all(string=True) if s.find_parent("a") is None)


def lst(el, doc, depth=0):
    # an item whose words are all in links is read when its list has items with words of their own (a reference list,
    # some entries a bare title): a list of links alone is a menu, a site's row of buttons, its previous and next
    # articles; and a paragraph of links in an item with words of its own is a row of buttons (a reference's links to
    # the databases that hold it)
    linked = any(marker is not None and _beside_links(li) for marker, li, _ in items(el))
    for marker, li, tag in items(el):
        if marker is None:
            lst(li, doc, depth + 1)
            continue
        first, titled = True, linked and not _beside_links(li)
        # an item's paragraphs may hold equations or a nested list between its text
        parts = [c for ch in li.find_all(recursive=False) if ch is not tag
                 for c in (ch.find_all(recursive=False) if "ltx_para" in ch.get("class", []) else [ch])]
        for ch in parts:
            if ch.name == "p" and "ltx_p" in ch.get("class", []) and not blockish(ch):
                ids = add_units(doc, ch, "item", linked=titled, list=el.get("id") or "list", depth=depth, marker=marker if first else "")
                for uid in ids:
                    doc.add({"k": "u", "u": uid})
                    first = False
                if not ids and ch.find(["img", "svg", "math", "video", "iframe"]):
                    doc.add({"k": "raw", "html": str(ch)})   # (a formula or a picture alone: shown as it is, as block shows one)
            elif ch.name in ("ul", "ol", "dl"):
                lst(ch, doc, depth + 1)
            else:
                block(ch, doc)


def figure(el, doc):
    kind = "table" if "ltx_table" in el.get("class", []) else "figure"
    fid = el.get("id") or f"fig{len(doc.blocks)}"
    doc.target(el, "fig", fid)
    cap = el.find("figcaption", recursive=False)
    cap_uid = None
    label = ""
    if cap:
        tag = cap.find(class_="ltx_tag")
        label = shown(tag, strip=True).rstrip(":").strip() if tag else ""
        if tag:  # the label is shown on its own at every level, so a caption cut to its key sentences keeps it
            tag.extract()
        cap_uid = add_unit(doc, cap, "caption", uid=fid + ".cap", of=fid)
        cap.extract()
    doc.add({"k": "fig", "id": fid, "kind": kind, "label": label, "html": el.decode_contents(), "cap": cap_uid})


def block(ch, doc):
    cls = ch.get("class", [])
    if ch.name == "section" and "ltx_bibliography" in cls:
        h = ch.find(re.compile(r"^h[1-6]$"), recursive=False)
        if h:
            h.extract()
        for li in ch.find_all("li", class_="ltx_bibitem"):
            if li.get("id"):
                doc.targets[li["id"]] = {"k": "bib", "id": li["id"]}
        doc.bib = ch.decode_contents()
    elif ch.name == "section" and any(c in SECTION_LEVEL for c in cls):
        section(ch, doc)
    elif re.match(r"^h[1-6]$", ch.name or "") and "ltx_title" in cls:
        return
    elif "ltx_para" in cls:
        walk(ch, doc)
    elif ch.name == "figure":
        figure(ch, doc)
    elif ch.name == "table" and any(c.startswith("ltx_equation") for c in cls):
        eid = ch.get("id") or f"eq{len(doc.blocks)}"
        doc.target(ch, "eq", eid)
        doc.add({"k": "eq", "id": eid, "html": str(ch)})
    elif ch.name == "p" and "ltx_p" in cls:
        ids = [] if blockish(ch) else add_units(doc, ch, "para")
        for uid in ids:
            doc.add({"k": "u", "u": uid})
        if not ids:
            doc.add({"k": "raw", "html": str(ch)})
    elif any(c.startswith("ltx_theorem") for c in cls) or "ltx_proof" in cls:
        box(ch, doc)
    elif ch.name in ("ul", "ol", "dl"):
        lst(ch, doc)
    else:
        doc.add({"k": "raw", "html": str(ch)})


def box(el, doc):
    """A theorem, lemma, definition, remark or proof: a framed group whose paragraphs are ordinary units."""
    cls = el.get("class", [])
    kind = "proof" if "ltx_proof" in cls else next((c[len("ltx_theorem_"):] for c in cls if c.startswith("ltx_theorem_")), "theorem")
    title = el.find(re.compile(r"^h[1-6]$"), class_="ltx_title", recursive=False)
    title_html = re.sub(r"(</\w+>)\s+([.:])", r"\1\2", title.decode_contents().strip()) if title else ""
    title_text = re.sub(r"\s+", " ", text_of(title)).strip().rstrip(".") if title else kind.capitalize()
    if title:
        title.extract()
    bid = el.get("id") or f"box{len(doc.blocks)}"
    doc.add({"k": "box", "id": bid, "kind": kind, "title": title_html})
    n0, outer = len(doc.units), doc.env
    doc.env = {"kind": kind, "title": title_text}
    walk(el, doc)
    doc.env = outer
    doc.add({"k": "box_end", "id": bid})
    new = list(doc.units)[n0:]
    if new:  # "by Theorem 3.2" lands on the theorem's first paragraph
        doc.target(el, "unit", new[0])


def walk(container, doc):
    for ch in container.find_all(recursive=False):
        block(ch, doc)


def parse(html, meta, made=None):
    """made: filled with the ids read from each element marked by ids.freeze ({"units": {mark: [ids]}, "chunks":
    {mark: id}})."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:   # bs4's FeatureNotFound: the parser the page is read with is not installed
        raise RuntimeError("The lxml package is missing: run  python3 -m pip install -r requirements.txt") from e
    art = soup.select_one("article.ltx_document")
    doc = Doc(meta)
    doc.frozen = {x for el in art.find_all(attrs={"data-u": True}) for x in el["data-u"].split()}
    if made is not None:
        made.update(units={}, chunks={})
        doc.made = made
    # LaTeXML embeds SVG figures as <object>; an <img> shows them as well and can carry a data URI
    for obj in art.find_all("object"):
        if re.search(r"\.(svg|png|jpe?g|gif)$", obj.get("data", ""), re.I):
            obj.name = "img"
            obj["src"] = obj.attrs.pop("data")
            obj.attrs.pop("type", None)
    # a drawing LaTeXML gives no viewBox, only its size (in px): given one of that size it draws as before, and can be
    # scaled down to a narrower column (reader.css) instead of being cut at the column's edge
    num = re.compile(r"\d+(?:\.\d+)?")
    for svg in art.find_all("svg"):
        w, h = svg.get("width", ""), svg.get("height", "")
        if not svg.get("viewbox") and num.fullmatch(w) and num.fullmatch(h) and not svg.find_parent("svg"):
            svg["viewBox"] = f"0 0 {w} {h}"
    # a display formula alone in a paragraph, as a Markdown document imported from 0.10.0 to 0.10.25 keeps a long one
    # (semantic.py read its equation's table as a layout's): an equation again, in its own box, and in a list item shown
    for m in art.find_all("math", attrs={"display": "block"}):
        p = m.parent
        if (p.name == "p" and "ltx_p" in p.get("class", []) and not p.find_parent(["table", "figure"])
                and [x for x in p.contents if not (isinstance(x, NavigableString) and not x.strip())] == [m]):
            p.replace_with(_equation(soup, m.extract()))
    # a Markdown document stored before 0.10.34: its formulas numbered and drawn, its tables ruled, as a new one is
    # (semantic.upgrade), with no new import
    if str(meta.get("id", "")).startswith("md-") and not art.find(class_=["pf-tex", "pf-grid"]):
        upgrade(soup, art)
    make_safe(art)   # arXiv's page reaches the reader as it is, so what could run script is taken out here (latexml_safe.py)
    doc.images = sorted({img["src"] for img in art.find_all("img") if img.get("src") and not img["src"].startswith("data:")})
    for ch in art.find_all(recursive=False):
        cls = ch.get("class", [])
        if "ltx_title_document" in cls or "ltx_authors" in cls:
            continue
        if "ltx_abstract" in cls:
            doc.open_chunk("abstract", "", "Abstract", "Abstract", 2)
            for p in ch.find_all("p", class_="ltx_p"):
                for uid in add_units(doc, p, "abstract"):
                    doc.add({"k": "u", "u": uid})
            doc.chunk = None
            continue
        block(ch, doc)
    _check(doc)
    ref_edges(doc)
    return doc.json()


def ref_edges(doc):
    """Cross-references the author wrote ("Figure 2", "Eq. 3", "Appendix A") as edges from the unit that cites them."""
    cap_of = {b["id"]: b["cap"] for b in doc.blocks if b["k"] == "fig" and b.get("cap")}
    seen = set()
    for uid, u in doc.units.items():
        for tk in u["toks"]:
            at = doc.atoms.get(tk.get("a"), {})
            if at.get("kind") != "ref":
                continue
            m = re.search(r'href="#([^"]+)"', at["html"])
            tgt = doc.targets.get(m.group(1)) if m else None
            if not tgt or tgt["k"] == "bib":
                continue
            kind, tid = tgt["k"], tgt["id"]
            if kind == "fig" and tid in cap_of:
                kind, tid = "unit", cap_of[tid]
            if tid == uid or (u.get("of") and tid == u["of"]) or (uid, tid) in seen:
                continue
            seen.add((uid, tid))
            doc.edges.append({"f": uid, "t": tid, "tk": kind, "rel": "ref", "via": tk["a"]})


def _check(doc):
    placed = {b.get("u") for b in doc.blocks if b["k"] == "u"} | {b.get("cap") for b in doc.blocks if b["k"] == "fig"}
    missing = [u for u in doc.units if u not in placed]
    if missing:
        raise RuntimeError(f"units without a block: {missing[:5]}")
