"""An arXiv paper's drawings as TeX set them, when the reader asks. LaTeXML turns a TikZ picture into an <svg> whose
words (a box's text, a label) are HTML in <foreignObject>, set again by the browser in its own fonts: wider or narrower
than TeX's, they may run out of their boxes and over one another, on arXiv's own HTML page too. The paper's PDF has the
figure as TeX set it.

Such a figure is shown as the HTML has it, with a button under it (the page's "offer"); nothing is downloaded until the
reader presses it. Then the PDF is fetched once, as for the side-by-side view, and kept (source.pdf), and each of the
paper's figures with words in a drawing is cut from it (pdftext's figures, found by their captions) into img/ beside
the other figures (pdf-figure-<n>.png, as picture.py keeps it). img/pdf-figures.json says what was found, a figure the
PDF did not give included (null), so that the button goes; under the figures taken, another puts the paper's back
as the HTML has them (back: the cuts and the list go, the PDF stays). A figure is taken only when the PDF's caption of that
number reads as the HTML's (a later version may number them otherwise). Its caption stays the HTML's: read, translated
and zoomed as before. Drawings without words, which the browser draws as TeX did, are left as they are."""
import json
import re
import threading
from html import escape

from . import engine, pdf, pdftext, picture, store

KEPT = "pdf-figures.json"
ARXIV = re.compile(r"\d{4}\.\d{4,5}")
LABEL = re.compile(r"fig(?:ure)?\.?\s*([A-Z]?\d+[a-z]?)$", re.I)   # the HTML's "Figure 3"
CAPTION = re.compile(r"\s*fig(?:ure)?\.?\s*([A-Z]?\d+[a-z]?)", re.I)   # the PDF's "Figure 3: ..."
_lock = threading.Lock()   # (one paper cut once, however often the button is pressed)


def drawn(doc):
    """An arXiv paper's figures with words in a drawing, by their numbers: {"3": the figure's block}."""
    out = {}
    if not ARXIV.fullmatch(doc["meta"].get("id") or ""):
        return out
    for b in doc["blocks"]:
        if b["k"] == "fig" and b.get("kind") == "figure" and "ltx_picture" in b["html"] and "<foreignobject" in b["html"].lower():
            m = LABEL.search(b.get("label") or "")
            if m:
                out.setdefault(m.group(1), b)
    return out


def _kept(folder):
    try:
        return json.loads((folder / KEPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def place(doc, folder, src_of):
    """doc's figures with words in a drawing: as cut from the PDF where that was asked for and found (b["pdf"] =
    "cut": the button that puts them back), else offered (b["pdf"] = "offer", the reader's button); src_of(name): the
    address of a picture of folder in the page."""
    kept = _kept(folder)
    for n, b in drawn(doc).items():
        if n not in kept:
            b["pdf"] = "offer"
        elif kept[n] and (folder / kept[n]["file"]).is_file():
            b["html"] = (f'<img class="ltx_graphics pdf-cut" src="{escape(src_of(kept[n]["file"]))}" alt="{escape(b["label"])}"'
                         f' style="width: {round(100 * kept[n]["width"])}%">')
            b["pdf"] = "cut"


def _words(s):
    return re.findall(r"[a-z0-9]+", s.lower())


def _alike(html_caption, pdf_caption):
    """Whether two captions are one: most of the HTML's first words in the PDF's (its formulas may read otherwise)."""
    mine, theirs = _words(html_caption)[:12], set(_words(pdf_caption))
    return len(mine) < 3 or sum(w in theirs for w in mine) >= 0.6 * len(mine)


def cut(file, want, folder):
    """want {number: caption}: the figures of the PDF file that bear those numbers and captions, cut into folder,
    {number: {"file", "width": its width as a share of the text's}, or None where the PDF has none}."""
    _, found, frame = pdftext.figures_in(file)
    by_no = {}
    for no, f in found:
        m = CAPTION.match(f["cap"]["text"])
        if m:
            by_no.setdefault(m.group(1), (no, f))
    got, jobs = {n: None for n in want}, []
    folder.mkdir(parents=True, exist_ok=True)
    for n, caption in want.items():
        if n not in by_no or not _alike(caption, by_no[n][1]["caption"]):
            continue
        no, f = by_no[n]
        (W, H), (L, B, R, T) = f["size"], f["rect"]
        box = [max(0, L / W), max(0, (H - T) / H), min(1, R / W), min(1, (H - B) / H)]
        jobs.append((n, (no, box, folder / f"pdf-figure-{n}.png"), min(1, (R - L) / max(frame[1] - frame[0], 1))))
    done = engine.crop_here(file, [j for _, j, _ in jobs])
    for n, (_, _, out), width in jobs:
        if str(out) not in done:
            continue
        data, name = picture.compact(out.read_bytes(), out.name)
        if name != out.name:
            (folder / name).write_bytes(data)
            out.unlink()
        got[n] = {"file": name, "width": round(width, 3)}
    return got


@store.in_document
def from_pdf(pid):
    """The reader's button: the paper's figures with words in a drawing cut from its PDF (fetched now if it is not
    kept yet). How many it gave; LookupError when there is no PDF to be had."""
    doc = store.parsed(pid)
    folder = store.pdir(pid) / "img"
    figs = drawn(doc)
    with _lock:
        kept = _kept(folder)
        want = {n: _caption(doc, b) for n, b in figs.items() if n not in kept}
        if want:
            file = pdf.path(pid)
            if file is None:
                raise LookupError(pid)
            kept.update(cut(file, want, folder))
            (folder / KEPT).write_text(json.dumps(kept, indent=1), encoding="utf-8")
    return sum(1 for n in figs if kept.get(n))


@store.in_document
def back(pid):
    """The other button: the paper's figures as the HTML has them again, and offered again. The PDF stays (the
    side-by-side view reads it)."""
    folder = store.pdir(pid) / "img"
    with _lock:
        for c in _kept(folder).values():
            if c and re.fullmatch(r"pdf-figure-[\w.-]+", c.get("file") or ""):
                (folder / c["file"]).unlink(missing_ok=True)
        (folder / KEPT).unlink(missing_ok=True)


def _caption(doc, b):
    u = doc["units"].get(b.get("cap") or "")
    return " ".join(tk.get("t", "") for tk in u["toks"]) if u else ""
