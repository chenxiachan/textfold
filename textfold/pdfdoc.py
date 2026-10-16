"""A PDF read by the PDF engine (engine.py), as a document: MinerU's structured output (structured_content.json: pages
of blocks, each with its type, its place on the page and its content) turned into the Markdown and HTML the other
sources go through. Running heads, page numbers and margin stamps are left out; the title and authors come from the
file's own metadata when it has them; headings get their depth from their numbering (MinerU gives every heading the
same level); a figure's panels and its caption become one figure, cut whole from its page when the engine cut it up;
a formula is LaTeX (the accurate tier) or the picture of it (the fast tier); the references become a list of works.
The engine's other files (a Word, PowerPoint or Excel file, an e-book, a saved web page, a picture) come out in the same
shape, without places on pages (but a picture's): their headings keep the depth the file gives them.

Its id: pdf-<the first 16 hex digits of the file's SHA-256>, so the same file is the same document; another file's,
doc-<the same>."""
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from . import local, markdown, store

DROP = {"header", "footer", "page_number", "aside_text"}
CAPTION = re.compile(r"^\s*(?:fig(?:ure)?\.?|table|tab\.|scheme|chart|abb(?:ildung)?\.?|tabelle|图|表)\s*[A-Z]?\d+", re.I)
FIGURE = re.compile(r"^\s*(?:fig(?:ure)?\.?|scheme|chart|abb(?:ildung)?\.?|图)\s*[A-Z]?\d+", re.I)   # not a table's
PAD = 0.008   # around a figure cut from its page, of the page's width and height: the labels at its edges
NEAR = 0.02   # of the page: pieces of a figure this close together are one figure
PANEL = re.compile(r"^\s*\(?[a-z]\)?\s*$|^\s*\(?[a-z]\)\s", re.I)   # "(a)", "f)": a panel's label, not a heading
DATE = re.compile(r"^(?:\w+ \d{1,2}, \d{4}|\d{1,2} \w+ \d{4}|\d{4}-\d{2}-\d{2})$")
REFS = re.compile(r"^\s*(?:\d+\.?\s*)?(?:references|bibliography|literature|works cited|literatur|参考文献)\s*$", re.I)
ABSTRACT = re.compile(r"^\s*(?:abstract|summary|zusammenfassung|摘要)\s*$", re.I)
# a numbered heading the fast tier took for a line of text: "1 Introduction", "2.3 Data", "IV. RESULTS"
NUMBERED = re.compile(r"^(?:[1-9]\d*(?:\.\d+){0,3}|[IVX]+)\.?\s+[A-Z][^.!?]{1,80}$")
# a publisher's labels on the page, not the paper's words
BOILER = re.compile(r"^\W*\d?\s*(?:check for updates|open access|research article|article|original article|review)\W*$", re.I)
# above the first heading: the paper's front matter (its authors, their places, its dates), unless it is a paragraph
FRONT = re.compile(r"^(?:<sup>|\d+\s*[A-Z][a-z]+ (?:of|for)\b|(?:received|accepted|published|revised|correspondence|e-?mail|keywords?)\b)|@", re.I)


def pid_of(data, ext="pdf"):
    return ("pdf-" if ext == "pdf" else "doc-") + hashlib.sha256(data).hexdigest()[:16]


def source_of(pid):
    """The file a converted document was read from, kept beside it (a PDF as source.pdf, another as original.docx,
    original.epub, ...: source.html is the document's own), or None."""
    d = store.pdir(pid)
    return d / "source.pdf" if (d / "source.pdf").exists() else next(iter(sorted(d.glob("original.*"))), None)


def kept_name(pid, file):
    """The name a converted document's file is kept under, beside it."""
    return "source.pdf" if pid.startswith("pdf-") else "original" + Path(file).suffix.lower()


def depth(title):
    """A heading's depth from its numbering: "3", "3." or "III." is 1, "3.2" or "A.1" 2, "3.2.1" 3; unnumbered, 1."""
    m = re.match(r"^\s*((?:\d+|[A-Z]|[IVX]+)(?:[.\-]\d+)*)[.)]?\s+\S", title)
    if not m:
        return 1
    return min(3, len(re.split(r"[.\-]", m.group(1).rstrip("."))))


def _text(s):
    return (s or "").strip()


def _md_escape_heading(s):
    return re.sub(r"\s+", " ", s).strip().lstrip("#").strip()


def _unwrap(s):
    """Words set in bold or italics as a whole ("**Abstract**", "*Figure 1. ...*"), without the marks."""
    m = re.fullmatch(r"(\*{1,3}|_{1,3})(.+?)\1", (s or "").strip(), re.S)
    return m.group(2).strip() if m and m.group(1) not in m.group(2) else (s or "").strip()


# a link within the file (a footnote's mark, a slide's): its words
LOCAL_LINK = re.compile(r'<a\s+href="#[^"]*"[^>]*>(.*?)</a>|(?<!!)\[([^\]]*)\]\((?!https?:|mailto:)[^)\s]*\)', re.S)


def _local_links(s):
    return LOCAL_LINK.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), s)


def _table_html(content):
    """A table as HTML: MinerU gives it as HTML or as a Markdown table."""
    c = _text(content)
    if c.lower().startswith("<table"):
        return c
    html, _ = markdown.to_html(c)
    m = re.search(r"<table.*?</table>", html, re.S)
    return m.group(0) if m else f"<p>{html}</p>"


def _caption_of(block):
    return " ".join(_captions(block))


def _captions(b):
    return [_unwrap(c.get("content")) for c in b.get("captions") or [] if _text(c.get("content"))]


def _figure_table(b):
    """A figure the engine read as a table (its words in cells, its pictures cut into pieces): a figure's caption, or
    none and pictures in its cells. Its picture (the engine's own, of the whole region) stands for it."""
    if b.get("type") != "table" or not b.get("image_source"):
        return False
    cap = next((c for c in _captions(b) if CAPTION.match(c)), "")
    return bool(FIGURE.match(cap)) if cap else "<img" in (b.get("content") or "")


def _panel_boxes(b):
    """Where a picture's panel labels are ("(a)", "t=1, substep 1"): its captions before the figure's own."""
    caps = [c for c in b.get("captions") or [] if _text(c.get("content"))]
    i = next((n for n, c in enumerate(caps) if CAPTION.match(_text(c.get("content")))), len(caps))
    return [c["bbox"] for c in caps[:i] if c.get("bbox")]


def _gap(a, b):
    """How far apart two boxes are (0 when they touch or overlap)."""
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0)


def _union(boxes):
    return [min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes)]


def _center_in(b, box):
    x, y = (b["bbox"][0] + b["bbox"][2]) / 2, (b["bbox"][1] + b["bbox"][3]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _picture(b):
    return bool(b.get("bbox")) and ((b.get("type") == "image" and bool(b.get("image_source"))) or _figure_table(b))


def _zones(blocks, page_of):
    """The figures the engine read in pieces, found by where the pieces are on the page: pictures this close together,
    with the short texts among and around them (a figure's labels, which the fast tier reads as lines of text). By the
    id of each piece, its figure: {"pictures": the ids of its pictures, "labels": where its labels are}. One with text
    of the page's own inside it, two figures' captions, or other blocks between its pieces in reading order, is left
    out: its pieces stay as the engine read them."""
    out, pages = {}, {}
    for n, b in enumerate(blocks):
        pages.setdefault(page_of[id(b)], []).append((n, b))
    for items in pages.values():
        groups = [{"pics": [(n, b)], "labels": [], "box": _union([b["bbox"]] + _panel_boxes(b))} for n, b in items if _picture(b)]
        if not groups:
            continue
        texts = [(n, b, _text(b.get("content"))) for n, b in items if b.get("type") in ("text", "paragraph_title") and b.get("bbox")]
        taken, changed = set(), True
        while changed:
            changed = False
            pair = next(((g, h) for g in groups for h in groups if g is not h and _gap(g["box"], h["box"]) <= NEAR), None)
            if pair:
                g, h = pair
                g["pics"] += h["pics"]
                g["labels"] += h["labels"]
                g["box"] = _union([g["box"], h["box"]])
                groups.remove(h)
                changed = True
                continue
            for n, b, content in texts:
                words = len(content.split())
                if n in taken or not content or words > 25 or CAPTION.match(content):
                    continue
                short = words <= 6 and not (NUMBERED.match(content) or REFS.match(content) or ABSTRACT.match(content))
                g = next((g for g in groups if _center_in(b, g["box"]) or (short and _gap(b["bbox"], g["box"]) <= NEAR)), None)
                if g:
                    g["labels"].append((n, b))
                    g["box"] = _union([g["box"], b["bbox"]])
                    taken.add(n)
                    changed = True
        for g in groups:
            if len(g["pics"]) == 1 and not g["labels"]:
                continue
            pieces = {n for n, _ in g["pics"] + g["labels"]}
            if sum(1 for _, b in g["pics"] if any(CAPTION.match(c) for c in _captions(b))) > 1:
                continue   # two figures
            if any(n not in pieces for n in range(min(pieces), max(pieces) + 1)):
                continue   # something else read between its pieces
            if any(n not in pieces and b.get("bbox") and _center_in(b, g["box"]) for n, b in items):
                continue   # the page's own text inside it
            zone = {"pictures": frozenset(id(b) for _, b in g["pics"]), "labels": [b["bbox"] for _, b in g["labels"]]}
            for _, b in g["pics"] + g["labels"]:
                out[id(b)] = zone
    return out


def plausible(title):
    """Words, not a file's code name: most of its tokens are made of letters ("11-nelson65 mc-nwf1 nm" is not)."""
    toks = title.split()
    wordy = [t for t in toks if sum(c.isalpha() for c in t) >= 0.8 * len(t)]
    return 8 <= len(title) <= 300 and len(toks) >= 2 and len(wordy) >= 0.6 * len(toks)


def to_markdown(sc, name="", crops=None, want=None):
    """(title, authors, Markdown with HTML for figures and tables) from MinerU's structured content. A figure the
    engine cut up (the accurate tier cuts one into its panels, and their labels into captions) is one picture cut from
    its page: want, a list, gets each such figure's {"page", "box", "blocks"}; crops is the picture made of each,
    {(page, box): src}. Without its picture, the figure is its pieces as the engine cut them."""
    doc = sc.get("metadata", {}).get("document", {}) or {}
    # a PDF or a picture (a page); else a file whose structure is in it (Word, EPUB, ...): its headings' depths are
    # its own, its text is all the author's (no running heads, stamps or front matter to tell from it)
    paged = (sc.get("metadata", {}).get("file_suffix") or "pdf") == "pdf"
    blocks = [b for p in sc.get("pages", []) for b in p.get("blocks", []) if b.get("type") not in DROP]
    page_of = {id(b): p.get("page_idx", n) + 1 for n, p in enumerate(sc.get("pages", [])) for b in p.get("blocks", [])}
    zones = _zones(blocks, page_of)
    # the title the page shows, when the engine found a whole one; else the file's own when it is words (it is often a
    # code: "11-nelson65 mc-nwf1"); else the file's name
    first = next((b for b in blocks if b.get("type") == "doc_title"), None)
    shown = re.sub(r"\s+", " ", _unwrap(first.get("content"))) if first is not None else ""
    meta_title = re.sub(r"\s+", " ", _text(doc.get("title")))
    title = (shown if plausible(shown) and len(shown.split()) >= 3 else meta_title if plausible(meta_title)
             else shown if plausible(shown) else re.sub(r"[_-]+", " ", Path(name).stem).strip())
    # a file without a title of its own whose first line is a heading like no other (its title, set as a heading)
    heads = [b for b in blocks if b.get("type") == "paragraph_title"]
    lead = blocks[0] if not paged and first is None and not plausible(meta_title) and blocks and blocks[0] in heads else None
    if lead is not None and plausible(_unwrap(lead.get("content"))) and [h.get("level") for h in heads].count(lead.get("level")) == 1:
        title = re.sub(r"\s+", " ", _unwrap(lead.get("content")))
    else:
        lead = None
    top = min((int(h.get("level") or 2) for h in heads if h is not lead), default=2)   # a file's shallowest heading
    authors = [a.strip() for x in doc.get("authors") or [] for a in re.split(r"\s*[;\n]\s*", x) if a.strip()]
    byline = {re.sub(r"\s+", " ", a) for a in authors}   # a file's own: its authors' names, under its title
    # what comes between the title and the first heading (the authors, their places, a date) is the paper's front matter
    has_heading = any(b.get("type") == "paragraph_title" for b in blocks)
    out, notes, figure, deferred, in_refs, front = [], [], [], [], False, True
    figure_notes = []   # a figure read as a table: its footnotes, after it
    seen_title, refs_at = False, None

    def place(x):
        """Figures and tables placed after the references (a journal's last pages) go before them."""
        (deferred if in_refs else out).append(x)

    def region(members):
        """Where a figure in pieces is on its page (its pieces and their labels), or None: one picture, or pages apart."""
        pages, ids = {page_of[id(m)] for m in members}, {id(m) for m in members}
        labels = [box for m in members for box in _panel_boxes(m)]
        for zone in {id(zones[i]): zones[i] for i in ids if i in zones}.values():
            if zone["pictures"] <= ids:
                labels += zone["labels"]
        if len(pages) != 1 or (len(members) == 1 and not labels) or not all(m.get("bbox") for m in members):
            return None
        boxes = [m["bbox"] for m in members] + labels
        return pages.pop(), [round(max(0.0, min(x[0] for x in boxes) - PAD), 4), round(max(0.0, min(x[1] for x in boxes) - PAD), 4),
                             round(min(1.0, max(x[2] for x in boxes) + PAD), 4), round(min(1.0, max(x[3] for x in boxes) + PAD), 4)]

    def flush_figure(caption=""):
        if not figure:
            return
        members, src = list(figure), None
        figure.clear()
        at = region(members)
        if at:
            if want is not None:
                want.append({"page": at[0], "box": at[1], "blocks": [id(m) for m in members]})
            src = (crops or {}).get((at[0], tuple(at[1])))
        imgs = "".join(f'<img src="{s}" alt="">' for s in ([src] if src else [m["image_source"] for m in members]))
        cap = f"<figcaption>{caption}</figcaption>" if caption else ""
        place(f"<figure>{imgs}{cap}</figure>")
        if figure_notes:
            place(" ".join(figure_notes))
            figure_notes.clear()

    def figure_caption(b):
        """A figure's own caption ("Figure 2: ..."), not a panel's ("(a)", "t=1, substep 1")."""
        caps = _captions(b)
        i = next((n for n, c in enumerate(caps) if CAPTION.match(c)), None)
        return " ".join(caps[i:]) if i is not None else ""

    for b in blocks:
        kind, content = b.get("type"), _text(b.get("content"))
        if kind in ("doc_title", "paragraph_title"):
            content = _unwrap(content)
        if b is lead:
            continue
        if not paged:
            content = re.sub(r"\s*\u21a9\ufe0e?", "", _local_links(content))   # (a footnote's way back to its mark: ↩)
            if kind == "text" and (ABSTRACT.match(_unwrap(content)) or REFS.match(_unwrap(content))):
                kind, content = "paragraph_title", _unwrap(content)   # "Abstract", "**References**": a heading
            if kind == "paragraph_title" and not seen_title and re.sub(r"\s+", " ", content) == title:
                seen_title = True   # the title again, as the first heading (an e-book's title page)
                continue
            if front and kind == "text" and (re.sub(r"\s+", " ", content) in byline or DATE.match(content)):
                continue   # the file's authors and date, under its title: its metadata has them
            if kind in ("text", "list"):
                front = False
        if paged and kind in ("text", "paragraph_title") and BOILER.match(content):
            continue
        if kind in ("text", "paragraph_title") and id(b) in zones:
            continue   # a figure's label read as a line of text ("Reasoning Task", "100"): the figure shows it
        if _figure_table(b):
            kind = "image"
            figure_notes.extend(_text(c.get("content")) for c in b.get("footnotes") or [] if _text(c.get("content")))
        if kind != "image" and figure:   # panels with no caption of their own: a caption after them, or none
            cap = content if kind == "text" and CAPTION.match(content) else ""
            flush_figure(cap)
            if cap:
                continue
        if paged and kind == "text" and not in_refs and NUMBERED.match(content) and len(content.split()) <= 10:
            kind = "paragraph_title"
        if kind == "doc_title":
            if not seen_title and re.sub(r"\s+", " ", content) == title:
                seen_title = True
                continue
            kind = "paragraph_title"
        if kind == "paragraph_title":
            if not content or PANEL.match(content) or len(content) > 160:
                if content and len(content) > 160:
                    out.append(content)
                continue
            if DATE.match(content) or not re.search(r"[^\W\d_]", content):   # a date, a dotted rule
                continue
            front = False
            in_refs = bool(REFS.match(content))
            if in_refs and refs_at is None:
                refs_at = len(out)
            deep = depth(content) if paged else min(3, max(1, int(b.get("level") or top) - top + 1))
            out.append("#" * (1 + deep) + " " + _md_escape_heading(content))
            continue
        if paged and front and has_heading and kind in ("text", "list") and (len(content.split()) < 25 or FRONT.search(content)):
            continue   # authors, affiliations, dates above the first heading (an abstract, an introduction stay)
        if kind == "image":
            if b.get("image_source"):
                figure.append(b)
            cap = figure_caption(b) if paged else _caption_of(b)   # (a file's captions are its pictures' own)
            if cap:
                flush_figure(cap)
            continue
        if kind == "table":
            cap = figure_caption(b) or _caption_of(b)
            foot = " ".join(_text(c.get("content")) for c in b.get("footnotes") or [])
            place("<figure>" + (f"<figcaption>{cap}</figcaption>" if cap else "") + _table_html(content) + "</figure>")
            if foot:
                place(foot)
            continue
        if kind == "equation":
            if content:
                latex = content.strip().strip("$").strip()
                out.append(f"$$\n{latex}\n$$")
            elif b.get("image_source"):   # the fast tier: the formula as a picture of it
                box = b.get("bbox") or [0, 0, 0.3, 0]
                width = max(40, round((box[2] - box[0]) * 760))   # as wide as on the page (a column is about 760 px)
                out.append(f'<p><img src="{b["image_source"]}" alt="formula" width="{width}"></p>')
            continue
        if kind == "ref_text" or (in_refs and kind in ("text", "list")):
            for line in content.splitlines():
                if line.strip():
                    out.append("- " + line.strip().lstrip("-• ").strip())
            continue
        if kind == "page_footnote":
            notes.append(content)
            continue
        if kind == "code":
            out.append("```\n" + content + "\n```")
            continue
        if content:
            out.append(content)
    flush_figure()
    if figure_notes:
        place(" ".join(figure_notes))
    if deferred:
        at = refs_at if refs_at is not None else len(out)
        out[at:at] = deferred
    if notes:
        out.append("## Notes")
        out.extend(notes)
    # a list's items stay together; everything else is a block of its own
    text, prev = [], None
    for x in out:
        if prev is not None and not (x.startswith("- ") and prev.startswith("- ")):
            text.append("")
        text.append(x)
        prev = x
    return title or "Untitled PDF", authors, "\n".join(text) + "\n"


def cut_figures(sc, folder, pdf, name=""):
    """Each figure the engine cut up, cut whole from its page into the engine's output folder (images/):
    {(page, box): src}, for to_markdown. Nothing when it cannot be done: the figure is then its pieces."""
    from . import engine
    want = []
    to_markdown(sc, name, want=want)
    if not want or not Path(pdf).is_file():
        return {}
    (Path(folder) / "images").mkdir(parents=True, exist_ok=True)
    jobs = [(w["page"], w["box"], Path(folder) / "images" / f"figure-{w['page']}-{n + 1}.jpg") for n, w in enumerate(want)]
    try:
        done = engine.crop(pdf, jobs)
    except Exception:
        return {}
    return {(p, tuple(box)): f"images/{out.name}" for p, box, out in jobs if str(out) in done}


def store_pdf(pid, folder, sc, name, tier, replace=False):
    """A converted PDF stored as a document (papers/<pid>/), its pictures read from the engine's output folder. The
    file itself is kept as source.pdf, so the reader can show it beside the text (another file, as original.<its
    extension>); the engine's structured content as
    structured_content.json, so that its figures and places can be made again without the engine. Converted again
    (replace), the reader's notes and the file stay; the levels go, since they belong to the text they were made from."""
    from . import engine
    d = store.pdir(pid)
    ext = "pdf" if pid.startswith("pdf-") else engine.kind_of(name) or "file"
    pdf = Path(folder) / engine.PDF
    title, authors, text = to_markdown(sc, name, cut_figures(sc, folder, pdf if pdf.is_file() else d / "source.pdf", name))
    html, _ = markdown.to_html(text)
    doc = sc.get("metadata", {}).get("document", {}) or {}
    doi = next((re.sub(r"^doi:\s*", "", i, flags=re.I) for i in doc.get("identifiers") or [] if re.match(r"^(doi:)?10\.\d{4,9}/", i, re.I)), "")
    url = next((i for i in doc.get("identifiers") or [] if re.match(r"https?://", i)), "")
    producer = (sc.get("metadata", {}).get("producer") or {}).get("name") or "mineru"   # (or TextFold's own reader)
    meta = {"id": pid, "version": "", "title": title, "authors": authors, "date": (doc.get("created_at") or "")[:10],
            "snapshot": time.strftime("%Y-%m-%d"), "abs_url": url or (f"https://doi.org/{doi}" if doi else ""), "html_url": "",
            "doi": doi, "license": "", "source": {"kind": "pdf", "name": name, "tier": tier, "engine": producer},
            "label": f"PDF · {name}"}
    if ext != "pdf":   # read in one way only, but a picture (fast or accurate, as a page is)
        meta["source"] = {"kind": "file", "format": ext, "name": name, "tier": engine.tier_for(f"x.{ext}", tier) if ext in engine.PICTURES else "",
                          "engine": producer}
        meta["label"] = f"{engine.FILES.get(ext, ext.upper())} · {name}"
    keep = d.with_name(d.name + ".keep")   # the reader's notes and the file, on disk while the document is rewritten
    if replace and d.exists():
        shutil.rmtree(keep, ignore_errors=True)
        keep.mkdir(parents=True)
        for f in [source_of(pid), d / "notes.json"]:
            if f and f.exists():
                shutil.copy2(f, keep / f.name)
        if (d / "source.html").exists():
            (d / "source.html").unlink()   # so that store_doc writes the document again
    try:
        local.store_doc(pid, html, meta, "source.md", text, lambda f: local.Images(f, Path(folder)).resolve, sizes=True)
        from . import zotero
        zotero.apply(pid)   # read from a Zotero item's file: the item's own title, authors and citekey
        (d / "layout.json").write_text(json.dumps(layout_of(sc), ensure_ascii=False), encoding="utf-8")
        (d / "structured_content.json").write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    finally:
        if keep.exists():
            for f in keep.iterdir():
                shutil.copy2(f, d / f.name)
            shutil.rmtree(keep)
    return pid


def layout_of(sc):
    """Each block's page, its place on it (0 to 1 of the page's width and height) and its words: what the reader's
    side-by-side view finds a paragraph by. A scan has no text of its own to find it in; the engine read it from the
    picture. A caption carries its figure's place too (the whole figure's, when the engine cut it into pieces)."""
    want = []
    to_markdown(sc, want=want)
    whole = {i: w["box"] for w in want for i in w["blocks"]}
    out = []
    for n, page in enumerate(sc.get("pages", [])):
        no = page.get("page_idx", n) + 1
        for b in page.get("blocks", []):
            kind = b.get("type")
            if kind in DROP or not b.get("bbox"):
                continue
            box = [round(float(x), 4) for x in b["bbox"]]
            if kind in ("image", "table"):
                fig = whole.get(id(b), box)
                for c in b.get("captions") or []:
                    if c.get("bbox") and _text(c.get("content")):
                        out.append({"p": no, "b": [round(float(x), 4) for x in c["bbox"]], "t": _text(c.get("content")), "fig": fig})
                if kind == "image":
                    continue
            text = re.sub(r"<[^>]+>", " ", _text(b.get("content")))
            if text:
                out.append({"p": no, "b": box, "t": text[:4000]})
    return out


def place(pid):
    """Each paragraph's place on the file's pages, {unit id: {"page": n, "boxes": [[x0, y0, x1, y1], ...]}}: matched
    in reading order against the engine's blocks, by the words they share."""
    from .tokens import plain
    f = store.pdir(pid) / "layout.json"
    if not f.exists():
        return {}
    blocks = json.loads(f.read_text(encoding="utf-8"))
    words = lambda s: set(re.findall(r"\w+", s.lower()))  # noqa: E731
    bw = [words(b["t"]) for b in blocks]
    doc = store.parsed(pid)
    out, at = {}, 0
    for uid, u in doc["units"].items():   # (in reading order)
        uw = words(plain(u["toks"], doc["atoms"]))
        if not uw:
            continue
        best, score, size = None, 0.0, 0
        for k in range(max(0, at - 3), min(len(blocks), at + 60)):
            s = len(uw & bw[k]) / len(uw) if bw[k] else 0
            if s > score or (s == score and best is not None and len(bw[k]) < size):   # a tie: the nearer in size
                best, score, size = k, s, len(bw[k])
        if best is None or score < 0.5:
            continue
        b = blocks[best]
        out[uid] = {"page": b["p"], "boxes": [b["b"]] + ([b["fig"]] if b.get("fig") else [])}
        at = best
    return out


def relayout(pid):
    """A PDF converted before its layout was kept: the engine runs again (the fast tier: seconds) for the places alone;
    the document stays as it is (the same file and tier give the same text). False when that cannot be done now."""
    import tempfile
    from . import engine
    d = store.pdir(pid)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    if (d / "layout.json").exists():
        return True
    if (meta.get("source") or {}).get("tier") != "fast":   # the accurate tier takes minutes: not on a page's request
        return False
    if not engine.where() or not (d / "source.pdf").exists():
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out = engine.convert(d / "source.pdf", Path(tmp) / "out", "fast")
        (d / "layout.json").write_text(json.dumps(layout_of(read_output(out)), ensure_ascii=False), encoding="utf-8")
    return True


def read_output(folder):
    """MinerU's structured content from an output folder (its zip unpacked)."""
    return json.loads((Path(folder) / "structured_content.json").read_text(encoding="utf-8"))
