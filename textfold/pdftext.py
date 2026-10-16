"""PDFs that carry their own words (typeset, or exported from Word or LaTeX), read by TextFold itself when the PDF
engine is not installed. pdf-inspector (Firecrawl's, a Rust library of a few MB, with no models) tells whether the
pages have their text in them and writes each page as Markdown; its headings, paragraphs, lists and tables become
blocks in the shape the engine writes (structured_content.json), so that pdfdoc stores them as it stores the engine's.

A figure is found by its caption and cut whole from its page, as the engine's are (engine.crop, with pypdfium2 here);
the words drawn in it (an axis's numbers, a diagram's labels), which pdf-inspector writes as the page's text, go.

A scan, or a PDF whose text cannot be read (a broken font encoding), still needs the engine: its pages are pictures,
read by a model. What this reading leaves out, the engine gives: formulas as LaTeX, a figure no caption names, each
paragraph's place on its page for the side-by-side view. A PDF read this way can be converted accurately later, once
the engine is installed (its tier is "text", not "accurate")."""
import html
import json
import re
import shutil
import tempfile
from pathlib import Path

from .pdfdoc import ABSTRACT, FIGURE, NUMBERED, REFS, depth, plausible

TIER = "text"
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")
# words set in bold or italics at the start of a line: a heading run into its paragraph ("**3. Methodology** The ..."),
# in pieces when the line broke inside it ("*2.1. Evolution of ... toward high-precision* *KGs* Bridge ...")
LEAD = re.compile(r"^((?:(\*{1,2})[^*\n]+?\2[\s\u2013\u2014-]*)+)(.*)$")
# a numbered heading ("3", "3.2.", "A.1", "IV."; not "A" as a title's first word): not joined to the one before
NUMBER = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-Z](?:\.\d+)+\.?|[A-Z]\.|[IVX]+\.)\s+\S")
PAGE_NO = re.compile(r"^\W*\d{1,4}\W*$")
# arXiv's stamp down a preprint's first page ("arXiv:2512.24601v3 [cs.AI] 11 May 2026"), which reads as a heading
STAMP = re.compile(r"^arXiv:\d{4}\.\d{4,5}(?:v\d+)?\s*\[[^\]]+\]\s+\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}$")
# a figure's caption, at the start of its line: its number, then a colon, a full stop or a bar ("Figure 2:",
# "Fig. 3.", "Figure 4 |"); one in bold may go on without ("Fig. 1 Overview"). Not "Figure 3 shows ..." in the text.
CAPTION_AT = re.compile(r"^\s*(?:fig(?:ure)?\.?|scheme|chart|abb(?:ildung)?\.?|图)\s*[A-Z]?\d+[a-z]?\s*(?:[:.|]|$)", re.I)
TABLE_AT = re.compile(r"^\s*(?:table|tab\.|tabelle|表)\s*[A-Z]?\d+[a-z]?\s*(?:[:.|]|$)", re.I)   # (a table's: where a figure ends)


def _plain(s):
    return re.sub(r"\s+", " ", re.sub(r"\*+", "", s)).strip()


def _join(lines):
    """A paragraph's lines as one, the words a line's end cut in two whole again ("communica- tion"; "OCR- free" keeps
    its hyphen)."""
    text = " ".join(x.strip() for x in lines if x.strip())
    return re.sub(r"(?<=[A-Za-z0-9])- (?=[a-z])", "-", re.sub(r"(?<=[a-z])- (?=[a-z])", "", text))


def _heading_of(s):
    """A line's leading bold or italic words when they are a heading (numbered, the abstract's, the references') and
    the rest: "**Abstract.** Engineering drawings ..." is the heading Abstract and its paragraph."""
    m = LEAD.match(s.strip())
    if not m:
        return None, s
    head = _plain(m.group(1))
    if ABSTRACT.match(head.rstrip(".:")) or REFS.match(head.rstrip(".:")):
        return head.rstrip(".:"), m.group(3)
    if NUMBERED.match(head):
        return head, m.group(3)
    return None, s


def blocks_of(md):
    """A page's Markdown as the engine's blocks: headings, paragraphs, lists, tables, code."""
    out, para = [], []

    def text(lines):
        if lines and _join(lines):
            out.append({"type": "text", "content": _join(lines)})

    def flush():
        lines = [x for x in para if x.strip()]
        para.clear()
        if not lines:
            return
        if all(x.lstrip().startswith("|") for x in lines):
            out.append({"type": "table", "content": "\n".join(x.strip() for x in lines)})
            return
        if LIST.match(lines[0]) and all(LIST.match(x) or x.startswith((" ", "\t")) for x in lines):
            out.append({"type": "list", "content": "\n".join(x.rstrip() for x in lines)})
            return
        run = []
        for line in lines:
            head, rest = _heading_of(line)
            if head is None:
                run.append(line)
                continue
            text(run)
            run = [rest] if rest.strip() else []
            out.append({"type": "paragraph_title", "content": head, "level": depth(head) + 1})
        text(run)

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            j = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("```")), len(lines))
            code = "\n".join(lines[i + 1:j]).strip("\n")
            if code.strip():
                out.append({"type": "code", "content": code})
            i = j + 1
            continue
        m = HEADING.match(line)
        if m:
            flush()
            out.append({"type": "paragraph_title", "content": _plain(m.group(2)), "level": len(m.group(1))})
        elif not line.strip():
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return _joined_headings(out)


def _joined_headings(blocks):
    """A heading the page broke over two lines, set as two ("... from construction to refinement with", "LLMs and
    GNNs"): one. A numbered heading, the abstract's or the references' stay their own."""
    out = []
    for b in blocks:
        prev = out[-1] if out else None
        if (prev is not None and b["type"] == prev["type"] == "paragraph_title" and not NUMBER.match(b["content"])
                and (b.get("level") == prev.get("level") or prev["content"].endswith("-"))   # ("LARGE LAN-", "GUAGE MODELS")
                and not any(p.match(x["content"]) for p in (ABSTRACT, REFS) for x in (b, prev))):
            prev["content"] = re.sub(r"(?<=\w)-$", "", prev["content"]) + ("" if prev["content"].endswith("-") else " ") + b["content"]
            continue
        out.append(b)
    return out


def _lead_key(b):
    """What a page's first block begins with, as a running head would (its page number aside), or None."""
    if b.get("type") != "text":
        return None
    m = LEAD.match(b["content"])
    s = m.group(1) if m else b["content"]
    return re.sub(r"\d+", "#", _plain(s)) if len(s.split()) <= 20 else None


def _running_heads(pages):
    """The running head over most pages ("X. Li et al. Advanced Engineering Informatics 76 (2026) 105034"), out:
    pdf-inspector leaves some, run into the page's first paragraph. A page number alone is marked as one."""
    keys = [_lead_key(p["blocks"][0]) if p["blocks"] else None for p in pages]
    counts = {k: keys.count(k) for k in keys if k}
    for p, k in zip(pages, keys):
        if k and counts[k] >= max(3, len(pages) // 2):
            b = p["blocks"][0]
            m = LEAD.match(b["content"])
            rest = m.group(3).strip() if m and re.sub(r"\d+", "#", _plain(m.group(1))) == k else ""
            if rest:
                b["content"] = rest
            else:
                p["blocks"].pop(0)
        for b in p["blocks"]:
            if b["type"] == "text" and PAGE_NO.match(b["content"]):
                b["type"] = "page_number"


def _words(s):
    return set(re.findall(r"\w+", s.lower()))


def _title(pages, known=""):
    """The paper's title: among the headings on its first page before its first section, the one the file's own
    title (known) names, else none (pdfdoc takes the file's); without one, the largest, the longest of those. The
    others there (the journal's name, the authors') are front matter, read as text."""
    if not pages:
        return
    heads = []
    for b in pages[0]["blocks"]:
        if b["type"] == "paragraph_title":
            if NUMBER.match(b["content"]) or ABSTRACT.match(b["content"]) or REFS.match(b["content"]):
                break
            heads.append(b)
    if heads:
        kw = _words(known) if plausible(known) else set()
        named = [b for b in heads if kw and len(_words(b["content"]) & kw) >= 0.6 * len(_words(b["content"]) | kw)]
        title = named[0] if named else None if kw else min(heads, key=lambda b: (b.get("level") or 1, -len(b["content"].split())))
        for b in heads:
            b["type"] = "doc_title" if b is title else "text"


def structure(pages, title=""):
    """The engine's structured content from the pages' Markdown, [(page index, markdown)], and the title the file
    gives itself (often none, or its code name)."""
    out = [{"page_idx": n, "blocks": blocks_of(md)} for n, md in pages]
    for p in out:
        p["blocks"] = [b for b in p["blocks"] if not STAMP.match(_plain(b["content"]))]
    _running_heads(out)
    _title(out, title)
    return {"pages": out, "metadata": {"file_suffix": "pdf", "producer": {"name": "pdf-inspector"},
                                       "document": {"title": title} if title else {}}}


# ── figures ──────────────────────────────────────────────────────────
# pdf-inspector writes no pictures. A figure is found by its caption: the pictures and drawings on its side of the
# caption (above, or below when nothing is above) in its column, up to the nearest line of prose, a heading or another
# caption, with the labels set beside them (an axis's title). PDF points throughout, from the page's lower left corner,
# as pdf-inspector gives them; the engine's places are fractions of the page from its upper left corner.

def _line(page, items):
    size = max(i.font_size for i in items)
    base = min(i.y for i in items)
    text = ""
    for k, i in enumerate(items):   # a space where the words stand apart (a word set in pieces stays whole)
        if k and i.x - (items[k - 1].x + items[k - 1].width) > 0.15 * i.font_size and not text.endswith(" "):
            text += " "
        text += i.text
    return {"page": page, "x0": min(i.x for i in items), "x1": max(i.x + i.width for i in items), "base": base,
            "top": max(i.y for i in items) + 0.7 * size, "bottom": base - 0.25 * size, "size": size,
            "bold": items[0].is_bold, "text": re.sub(r"\s+", " ", text).strip()}


def _lines(items):
    """The pages' words as lines, cut where they stand apart (two columns side by side)."""
    rows = {}
    for i in items:
        if i.item_type == "text" and i.text.strip() and not i.rotation:
            rows.setdefault((i.page, round(i.y / 2)), []).append(i)
    out = []
    for (page, _), items in sorted(rows.items()):
        items.sort(key=lambda i: i.x)
        run = []
        for i in items:
            if run and i.x - (run[-1].x + run[-1].width) > 1.5 * max(i.font_size, run[-1].font_size):
                out.append(_line(page, run))
                run = []
            run.append(i)
        out.append(_line(page, run))
    return out


def _body_size(lines):
    """The size most of the words are set in."""
    sizes = sorted((round(l["size"], 1), len(l["text"])) for l in lines)
    half, seen = sum(n for _, n in sizes) / 2, 0
    for size, n in sizes:
        seen += n
        if seen >= half:
            return size
    return 10


def _is_caption(line):
    return bool(CAPTION_AT.match(line["text"]) or (line["bold"] and FIGURE.match(line["text"])))


def _words_of(s):
    return re.findall(r"\w+", s.lower())


def _key(s):
    """A caption's first words, to know its paragraph by: letters and digits alone."""
    return re.sub(r"[\W_]+", "", _plain(s)).lower()[:24]


def _overlap(a, b):
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return max(w, 0) * max(h, 0) / small


def _region(cap, lines, shapes, span, body, col, up):
    """The figure a caption names, (left, bottom, right, top), or None: the shapes above it (below, up False) within
    span, up to the nearest line of prose, heading or caption, with the short lines beside them."""
    def within(a0, a1):   # most of it inside the column
        return min(a1, span[1]) - max(a0, span[0]) >= 0.5 * max(a1 - a0, 1)

    # the caption's own lines: those under its first, set as it is, close together
    own, last = {id(cap)}, cap
    for l in sorted((l for l in lines if l["base"] < cap["base"] and within(l["x0"], l["x1"])), key=lambda l: -l["base"]):
        if last["base"] - l["base"] > 1.7 * cap["size"] or abs(l["size"] - cap["size"]) > 0.6 or _is_caption(l):
            break
        own.add(id(l))
        last = l
    side = [l for l in lines if id(l) not in own and within(l["x0"], l["x1"])
            and (l["base"] > cap["base"] if up else l["base"] < last["base"])]

    def drawn_on(l):   # words set on a drawing (a diagram's boxes, a plot's title): its labels, not the page's prose
        x, y = (l["x0"] + l["x1"]) / 2, (l["base"] + l["top"]) / 2
        return any(s[0] <= x <= s[2] and s[1] <= y <= s[3] and (s[2] - s[0]) * (s[3] - s[1]) > 1600 for s in shapes)
    stops = [l for l in side if _is_caption(l) or TABLE_AT.match(l["text"]) or (not drawn_on(l) and (l["size"] > body + 1
             or (abs(l["size"] - body) < 0.6 and l["x1"] - l["x0"] > 0.6 * col)))]
    stop_ids, grew = {id(l) for l in stops}, True
    while grew:   # and the lines of their paragraphs: a caption's next lines, a paragraph's short last one
        grew = False
        for l in side:
            if id(l) in stop_ids or drawn_on(l):
                continue
            if any(abs(s["base"] - l["base"]) <= 1.7 * s["size"] and abs(s["size"] - l["size"]) < 0.6
                   and min(s["x1"], l["x1"]) > max(s["x0"], l["x0"]) for s in stops):
                stops.append(l)
                stop_ids.add(id(l))
                grew = True
    if up:
        lo, hi = cap["top"], min((l["bottom"] for l in stops), default=10_000)
    else:
        lo, hi = max((l["top"] for l in stops), default=0), last["bottom"]
    found = [s for s in shapes if within(s[0], s[2]) and s[1] >= lo - 3 and s[3] <= hi + 3]
    if not found:
        return None
    L, B, R, T = min(s[0] for s in found), min(s[1] for s in found), max(s[2] for s in found), max(s[3] for s in found)
    grew = True
    while grew:   # the labels: short lines near the drawing, grown in steps
        grew = False
        for l in side:
            if id(l) in stop_ids or l["bottom"] < lo - 1 or l["top"] > hi + 1 or l["size"] > body + 0.6:
                continue
            if L <= l["x0"] and l["x1"] <= R and B <= l["bottom"] and l["top"] <= T:
                continue
            if l["x0"] >= L - 14 and l["x1"] <= R + 14 and l["bottom"] >= B - 14 and l["top"] <= T + 14:
                L, B, R, T = min(L, l["x0"]), min(B, l["bottom"]), max(R, l["x1"]), max(T, l["top"])
                grew = True
    L, B, R, T = L - 3, max(B - 3, lo + 0.5), R + 3, min(T + 3, hi - 0.5)
    if R - L < 36 or T - B < 24:
        return None
    words = {w for l in side if L <= l["x0"] and l["x1"] <= R and B <= l["bottom"] and l["top"] <= T for w in _words_of(l["text"])}
    mine = sorted((l for l in lines if id(l) in own), key=lambda l: -l["base"])
    capbox = (min(l["x0"] for l in mine), last["bottom"], max(l["x1"] for l in mine), cap["top"])
    return {"rect": (L, B, R, T), "words": words, "capbox": capbox, "caption": " ".join(l["text"] for l in mine)}


def _drawn_in(b, words):
    """A block of the page's text that is a figure's own words (its labels, its axes' numbers), which pdf-inspector
    sets among the page's paragraphs. Not the paper's title, nor a numbered heading."""
    if b["type"] not in ("text", "list", "table", "code", "paragraph_title") or b["type"] == "paragraph_title" and (
            NUMBER.match(b["content"]) or ABSTRACT.match(_plain(b["content"])) or REFS.match(_plain(b["content"]))):
        return False
    mine = _words_of(_plain(b.get("content") or ""))
    return bool(mine) and sum(w in words for w in mine) >= 0.85 * len(mine)


def _frame(lines, body):
    """Where the prose stands, the same on every page: (left, right, a column's width). From the whole document, since
    a page may have no prose in one of its columns (a figure fills it)."""
    prose = [l for l in lines if abs(l["size"] - body) < 0.6]
    if not prose:
        return None
    lefts, rights = sorted(l["x0"] for l in prose), sorted(l["x1"] for l in prose)
    widths = sorted(l["x1"] - l["x0"] for l in prose)
    n = len(prose)
    return lefts[n // 20], rights[n - 1 - n // 20], widths[int(n * 0.9)]   # (a full line of prose: the column's width)


def _figures_on(page, lines, body, frame, raw):
    """The figures on a page: [{"rect", "words", "capbox", "cap"}] in PDF points."""
    if page.get_rotation():
        return []
    W, H = page.get_size()
    cl, cb = page.get_cropbox()[:2]
    x0, x1, col = frame
    two, mid = col < 0.7 * (x1 - x0), (x0 + x1) / 2
    shapes = []
    for o in page.get_objects(max_depth=1):
        if o.type in (raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_FORM, raw.FPDF_PAGEOBJ_SHADING):
            l, b, r, t = o.get_bounds()
            l, b, r, t = l - cl, b - cb, r - cl, t - cb
            if (r - l) * (t - b) < 4 or r - l > 0.95 * W or t - b > 0.95 * H:
                continue   # a speck; the page's frame or its ground
            if t - b < 1.5 and r - l > 0.8 * col:
                continue   # a rule (under a running head, above the footnotes)
            shapes.append((l, b, r, t))
    out = []
    reading = lambda l: (two and l["x0"] >= mid - 6, -l["base"])   # (the left column first, each from the top)
    for cap in sorted((l for l in lines if _is_caption(l)), key=reading):
        span = (x0, mid) if two and cap["x1"] <= mid + 6 else (mid, x1) if two and cap["x0"] >= mid - 6 else (x0, x1)
        fig = _region(cap, lines, shapes, span, body, col, True) or _region(cap, lines, shapes, span, body, col, False)
        if fig and not any(_overlap(fig["rect"], f["rect"]) > 0.3 for f in out):
            out.append({**fig, "cap": cap, "size": (W, H)})
    return out


def add_figures(file, sc, out):
    """The figures of a PDF read this way, cut whole from their pages into out/images/ (engine.crop, here without the
    engine) and set in sc as the engine's pictures are, each with its caption; the words drawn in them out of the
    pages' text. Nothing when pypdfium2 is not installed (the captions stay, as text)."""
    from . import engine
    try:
        import pdf_inspector
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        return
    try:
        items = pdf_inspector.extract_text_with_positions(str(file))
    except Exception:
        return
    lines = _lines(items)
    pages = sorted({l["page"] for l in lines if _is_caption(l)})
    if not pages:
        return
    body, found = _body_size(lines), []
    frame = _frame(lines, body)
    if frame is None:
        return
    with engine.PDFIUM:
        doc = pdfium.PdfDocument(str(file))
        try:
            for no in pages:
                found += [(no, f) for f in _figures_on(doc[no - 1], [l for l in lines if l["page"] == no], body, frame, raw)]
        except Exception:
            found = []
        finally:
            doc.close()
    if not found:
        return
    for no, f in found:   # the words drawn in it, turned ones too (an axis's title, slanted ticks)
        L, B, R, T = f["rect"]
        f["words"] |= {w for i in items if i.page == no and i.item_type == "text" and L <= i.x + i.width / 2 <= R
                       and B <= i.y + i.height / 2 <= T for w in _words_of(i.text)}
    (Path(out) / "images").mkdir(parents=True, exist_ok=True)
    jobs = []
    for n, (no, f) in enumerate(found):
        (W, H), (L, B, R, T) = f["size"], f["rect"]
        f["box"] = [max(0, L / W), max(0, (H - T) / H), min(1, R / W), min(1, (H - B) / H)]
        jobs.append((no, f["box"], Path(out) / "images" / f"figure-{no}-{n + 1}.jpg"))
    done = engine.crop(file, jobs)
    by_page = {p["page_idx"]: p for p in sc["pages"]}
    for (no, f), (_, box, img) in zip(found, jobs):
        page = by_page.get(no - 1)
        if page is None or str(img) not in done:
            continue
        W, H = f["size"]
        c = f["capbox"]
        key = _key(f["cap"]["text"])
        at = next((k for k, b in enumerate(page["blocks"]) if b["type"] in ("text", "paragraph_title")
                   and key and _key(b["content"]).startswith(key)), None)
        caption = _plain(page["blocks"][at]["content"]) if at is not None else f["caption"]
        block = {"type": "image", "image_source": f"images/{img.name}", "bbox": box,
                 "captions": [{"content": caption, "bbox": [c[0] / W, (H - c[3]) / H, c[2] / W, (H - c[1]) / H]}]}
        if at is None:
            page["blocks"].append(block)
        else:
            page["blocks"][at] = block
        page["blocks"] = [b for b in page["blocks"] if not _drawn_in(b, f["words"])]


def read(file):
    """A PDF's pages as ([(page index, markdown)], its own title) when they have their words in them; None when the
    engine is needed (a scan, pictures of pages, a broken font encoding). ValueError("bad-file") when it is no PDF
    pdf-inspector can open."""
    try:
        import pdf_inspector
    except ImportError:   # not installed (no build of it for this system): the engine reads the PDF, as before
        return None
    try:
        kind = pdf_inspector.detect_pdf(str(file))
        if kind.pdf_type != "text_based" or kind.pages_needing_ocr or kind.has_encoding_issues:
            return None
        result = pdf_inspector.extract_pages_markdown(str(file))
    except Exception as e:   # a damaged file, an encrypted one
        raise ValueError("bad-file") from e
    if result.pages_needing_ocr or not any(p.markdown.strip() for p in result.pages):
        return None
    return [(p.page, p.markdown) for p in result.pages], html.unescape(kind.title or "").strip()


def convert(file, out):
    """A PDF read into out/ (structured_content.json), as the engine would write it; None when it needs the engine."""
    got = read(file)
    if got is None:
        return None
    sc = structure(*got)
    if not any(p["blocks"] for p in sc["pages"]):
        return None
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    add_figures(file, sc, out)
    (out / "structured_content.json").write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    return out


def save(pid, file, name):
    """A PDF read by TextFold itself and stored as a document (pdfdoc.store_pdf, as the engine's are), the file kept
    beside it as source.pdf; False when it needs the engine (or pdf-inspector cannot open it: the engine may)."""
    from . import pdfdoc, store
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = convert(file, Path(tmp) / "out")
        except ValueError:
            return False
        if out is None:
            return False
        pdfdoc.store_pdf(pid, out, pdfdoc.read_output(out), name, TIER)
    kept = store.pdir(pid) / "source.pdf"
    if not kept.exists():
        kept.write_bytes(Path(file).read_bytes())
    return True
