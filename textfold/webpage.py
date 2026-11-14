"""Any web page: the article on it (the text a reader came for, without the site's menus, adverts and footers), read
with trafilatura as Markdown and stored through the Markdown path, with its pictures. Kept as a snapshot of the day it
was read. Text the page hides from its readers (display: none, aria-hidden, screen-reader classes) is removed first:
it is where words meant for a model rather than a person are put. A note the page writes inside its sentences, shown
on hover or in the margin (ARIA's tooltip, DPUB-ARIA's doc-footnote, Distill's d-footnote, Tufte's sidenote), would
run into the sentence it hangs on: it is taken out first, and comes back as a footnote under the page's own mark.

A page that needs its scripts to show its text, or a sign-in, gives too little to read (web-notext); a site that
refuses the request, web-blocked; a link to a PDF, web-pdf (the file is dropped on the landing instead).

Its id: web-<host>-<the first 12 hex digits of the address's SHA-256>; the address waits in url.txt until the fetch."""
import copy
import hashlib
import html as htmllib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import local, markdown, store
from .fetch import UA, urlread

LIMIT = 8 * 2**20   # no article's page is larger
MIN_WORDS = 80      # fewer: a page that shows its text with scripts, or behind a sign-in
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem|%)?\s*(?:;|$|!)"
                          r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$|!)", re.I)
HIDDEN_CLASS = re.compile(r"(?:^|\s)(?:sr-only|visually-hidden|screen-reader-text|screenreader-only|hidden|d-none|is-hidden)(?:\s|$)")


def pid_of(url):
    host = re.sub(r"^www\.", "", (urllib.parse.urlparse(url).hostname or "page").lower())
    host = re.sub(r"[^a-z0-9]+", "-", host).strip("-")[:40] or "page"
    return f"web-{host}-{hashlib.sha256(url.encode()).hexdigest()[:12]}"


def register(url):
    """A page's id (store.resolve), its address kept for the fetch."""
    url = url.strip()
    pid = pid_of(url)
    d = store.pdir(pid)
    if not (d / "meta.json").exists():
        d.mkdir(parents=True, exist_ok=True)
        (d / "url.txt").write_text(url, encoding="utf-8")
    return pid


def _get(url):
    req = urllib.request.Request(url, headers={**UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})

    def check(r):
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "application/pdf" or r.url.lower().split("?")[0].endswith(".pdf"):
            raise ValueError("web-pdf")
        if ctype and "html" not in ctype and "xml" not in ctype:
            raise ValueError("web-notext")
    try:
        data, final, headers = urlread(req, 30, LIMIT + 1, check, busy={500, 502, 504})   # (429 and 503: a wall)
        return data[:LIMIT], final, headers.get_content_charset()
    except urllib.error.HTTPError as e:
        if e.code in (401, 402, 403, 407, 429, 451, 503):   # a sign-in, a paywall, a wall against programs
            raise ValueError("web-blocked") from None
        raise


def visible(page, url):
    """The page as its reader sees it: hidden elements removed, links and pictures as full addresses."""
    from lxml import html as lhtml
    root = lhtml.fromstring(page)
    root.make_links_absolute(url, resolve_base_href=True)
    # (one a browser's snapshot marked as not shown is left for _hover_marks: a note on its mark, or out after)
    gone = [el for el in root.iter() if isinstance(el.tag, str) and (el.get("aria-hidden") == "true" or el.tag in ("template", "noscript") or (
        not el.get("data-tf-css-hidden") and (el.get("hidden") is not None or HIDDEN_STYLE.search(el.get("style") or "")
                                              or HIDDEN_CLASS.search(el.get("class") or ""))))]
    for el in gone:
        if el.getparent() is not None:
            el.drop_tree()
    return root


# ── notes inside the sentences ───────────────────────────────────────
NOTE = "TFNOTE{}Q"   # a note's place in the text trafilatura reads, until the note comes back there
NOTE_AT = re.compile(r"TFNOTE(\d+)Q")
MARK = re.compile(r"\[?(?:\d{1,3}[a-z]?|[a-z]|[*†‡§¶⊕]{1,3})\]?|\(\d{1,3}\)", re.I)   # 1, [10], 3a, *, †, (2)
IN_TEXT = {"p", "li", "dd", "dt", "td", "th", "figcaption", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
RUN_ON = {"p", "div", "section", "aside", "blockquote", "ul", "ol", "li", "dl", "dt", "dd", "table", "tbody", "thead",
          "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption", "header", "footer"}
NOT_WORDS = {"script", "style", "button", "svg", "canvas", "input", "template", "noscript", "iframe", "video", "audio"}


def _swap(el, text):
    """el out of the tree, text in its place, what came after it kept."""
    parent = el.getparent()
    if parent is None:
        return
    text += el.tail or ""
    prev = el.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + text
    else:
        parent.text = (parent.text or "") + text
    parent.remove(el)


def _within(el, root):
    while el is not None and el is not root:
        el = el.getparent()
    return el is root


def _words(el):
    """A note's words: as inline HTML, its links, emphasis, code and formulas kept, its paragraphs and line breaks run
    together (a footnote is one line), its buttons and scripts gone; and as plain text."""
    from lxml import etree, html as lhtml
    box = copy.deepcopy(el)
    box.tail = None
    for e in list(box.iter()):
        if e is box or not isinstance(e.tag, str):
            continue
        if e.tag in NOT_WORDS:
            _swap(e, " ")
        elif e.tag in RUN_ON or e.tag == "br":
            e.text, e.tail = " " + (e.text or ""), " " + (e.tail or "")
    etree.strip_elements(box, etree.Comment, with_tail=False)
    etree.strip_tags(box, "br", *RUN_ON)
    inner = htmllib.escape(box.text or "", quote=False) + "".join(lhtml.tostring(c, encoding="unicode") for c in box)
    return re.sub(r"\s+", " ", inner).strip(), re.sub(r"\s+", " ", box.text_content()).strip()


def _notes(root):
    """Notes the page writes inside its sentences and shows only on hover or in the margin: ARIA's tooltip, under the
    mark that names it (aria-describedby); DPUB-ARIA's doc-footnote within a paragraph, under its doc-noteref;
    Distill's <d-footnote> and Tufte's sidenote, numbered as their pages number them; Tufte's marginnote. Each is taken
    out of its sentence, its mark's place kept (NOTE), and given back: [(the mark as the page shows it, its words as
    HTML, as text)]. A tooltip without a mark (a figure's, a button's, a word's) is not the article's text, and goes.
    A site's recipe (adapt.py) names the others: a note data-tf-note="k", its mark data-tf-mark="k"."""
    named = {}
    for el in root.iter():
        if isinstance(el.tag, str):
            for i in (el.get("aria-describedby") or "").split():
                named.setdefault(i, el)
            if el.get("data-tf-mark"):
                named.setdefault("tf:" + el.get("data-tf-mark"), el)
            if el.get("role") == "doc-noteref" and urllib.parse.urldefrag(el.get("href") or "")[1]:
                named.setdefault(urllib.parse.urldefrag(el.get("href"))[1], el)
    inside = lambda el: any(a.tag in IN_TEXT for a in el.iterancestors())  # noqa: E731
    found = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        role, cls = el.get("role") or "", (el.get("class") or "").split()
        kind = ("given" if el.get("data-tf-note") else "tip" if role == "tooltip" else "dpub" if role in ("doc-footnote", "doc-endnote") and inside(el)
                else "distill" if el.tag == "d-footnote" else "side" if el.tag == "span" and "sidenote" in cls and inside(el)
                else "margin" if el.tag == "span" and "marginnote" in cls and inside(el) else None)
        if kind:
            found.append((kind, el))
    notes, count = [], {}
    for kind, el in found:
        if not _within(el, root):   # (in a note taken out already: its words went with it)
            continue
        count[kind] = count.get(kind, 0) + 1
        mark_el = (named.get("tf:" + el.get("data-tf-note")) if kind == "given"
                   else named.get(el.get("id")) if el.get("id") else None)
        if kind in ("side", "margin"):   # Tufte's toggle beside it: a label (⊕ for a margin note) and its checkbox
            for sib in list(el.itersiblings(preceding=True))[:2]:
                if "margin-toggle" in (sib.get("class") or "").split():
                    mark_el = sib if sib.tag == "label" else mark_el
                    if sib.tag == "input":
                        _swap(sib, "")
        if mark_el is not None and not _within(mark_el, root):
            mark_el = None
        said = ""
        if mark_el is not None:
            said = re.sub(r"\s+", "", mark_el.text_content().replace(el.text_content(), "", 1) if el in mark_el.iterdescendants()
                          else mark_el.text_content())
        if kind == "tip" and not MARK.fullmatch(said):
            _swap(el, "")
            continue
        mark = said if MARK.fullmatch(said) else str(count[kind])
        words = _words(el)
        if mark_el is not None:
            _swap(mark_el, NOTE.format(len(notes)))
            if _within(el, root):
                _swap(el, "")
        else:
            _swap(el, NOTE.format(len(notes)))
        notes.append((mark, *words))
    return notes


def _hover_marks(root):
    """In a browser's snapshot (the app's, an extension's), what the browser did not show is marked data-tf-css-hidden.
    One that hangs on a mark (the element just before it, or the one holding it, reads 1, [10] or *) is a note shown
    on hover, whatever the page calls it: marked for _notes as a recipe's note is. The rest goes (_hidden_out)."""
    k = 0
    for el in root.xpath('//*[@data-tf-css-hidden]'):
        if el.get("role") == "tooltip" or el.get("data-tf-note") or el.tag == "d-footnote" or not _within(el, root) \
                or any(a.get("data-tf-css-hidden") for a in el.iterancestors()):
            continue
        prev = el.getprevious()
        while prev is not None and not isinstance(prev.tag, str):
            prev = prev.getprevious()
        mark = None
        if prev is not None and not prev.get("data-tf-css-hidden") and MARK.fullmatch(re.sub(r"\s+", "", prev.text_content())) \
                and not (el.getprevious() is prev and (prev.tail or "").strip()):
            mark = prev
        else:
            parent = el.getparent()
            rest = re.sub(r"\s+", "", parent.text_content().replace(el.text_content(), "", 1)) if parent is not None else ""
            if parent is not None and parent is not root and MARK.fullmatch(rest):
                mark = parent
        if mark is not None and el.text_content().strip():
            k += 1
            mark.set("data-tf-mark", f"h{k}")
            el.set("data-tf-note", f"h{k}")


def _hidden_out(root):
    for el in root.xpath('//*[@data-tf-css-hidden]'):
        if _within(el, root):
            _swap(el, "")


def _noted(html, notes):
    """Each note back in its place in the article's HTML, as LaTeXML writes a footnote (the reader shows it on hover),
    under the page's own mark."""
    def one(m):
        k = int(m.group(1))
        if k >= len(notes) or not notes[k][1]:
            return ""
        sup = f'<sup class="ltx_note_mark">{htmllib.escape(notes[k][0])}</sup>'
        return (f'<span class="ltx_note ltx_role_footnote">{sup}<span class="ltx_note_outer"><span class="ltx_note_content">'
                f'{sup}{notes[k][1]}</span></span></span>')
    html = re.sub(r"<[^>]*>", lambda m: NOTE_AT.sub("", m.group(0)), html)   # (one in an attribute, an image's alt: gone)
    return NOTE_AT.sub(one, html)


def _md_noted(md, notes):
    """The Markdown kept of the page: each note a footnote of its own, its words at the end."""
    there = []

    def ref(m):
        k = int(m.group(1))
        if k >= len(notes) or not notes[k][2]:
            return ""
        there.append(k)
        return f"[^{k + 1}]"
    md = NOTE_AT.sub(ref, md)
    esc = lambda s: re.sub(r"([\\`*_\[\]<>$~|])", r"\\\1", s)  # noqa: E731
    ends = [f"[^{k + 1}]: {esc(notes[k][2])}" for k in dict.fromkeys(there)]
    return md.rstrip() + ("\n\n" + "\n".join(ends) + "\n" if ends else "\n")


def _clean(md, title):
    md = re.sub(r"!\[[^\]]*\]\(\s*\)", "", md)                      # pictures without an address (a layout's spacers)
    lines = [ln for ln in md.splitlines() if not re.fullmatch(r"\s*\|[\s|]*", ln)]   # a layout table's empty cells
    # the title again, as the article's first heading
    for i, ln in enumerate(lines):
        if ln.strip():
            m = re.match(r"^#+\s+(.*)$", ln)
            if m and re.sub(r"\W+", " ", m.group(1)).strip().lower() == re.sub(r"\W+", " ", title).strip().lower():
                del lines[i]
            break
    return "\n".join(lines).strip() + "\n"


# ── formulas written for a script to draw, and figures that are pages of their own ──────────────────────────
# A fetched page runs no script: a formula it writes in its text for MathJax or KaTeX to draw is still TeX there, and
# trafilatura, reading it as words, escapes its _ and * for Markdown (\sigma\_i). An interactive figure is often a page
# of its own in an <iframe> (al-folio's and Distill's blogs, a video), which a reading of words leaves out. Both are
# held in their place as placeholders while trafilatura reads the page, then put back: the TeX as written, a display
# formula a paragraph of its own; the figure as a link the reader draws as the page drew it (reader.js, embeds). A
# browser's snapshot has its formulas drawn already, as MathML (_math_views).
MATH = "TFMATH{}Q"
INLINE = "TFTEX{}Q"
EMBED = "TFEMBED{}Q"
PANELS = "TFPANELS{}Q"
HELD = re.compile(r"(TFMATH\d+Q|TFTEX\d+Q|TFEMBED\d+Q|TFPANELS\d+Q)")
TEX = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
# $...$ in a page's text is a formula only where the page says so, in the settings it gives MathJax or KaTeX (a "$" is
# money as often): then by Pandoc's rule, no space inside either $, no digit after the closing one
DOLLARS_SET = re.compile(r"""inlineMath\s*:\s*\[\s*\[\s*['"]\$['"]|left\s*:\s*['"]\$['"]\s*,\s*right\s*:\s*['"]\$['"]""")
DOLLARS = re.compile(r"(?<![\\$])\$(?=[^\s$])([^$\n]+?)(?<=\S)\$(?![\d$])")
NO_TEX = {"script", "style", "pre", "code", "textarea", "kbd", "samp", "math"}
LOCAL_HOST = re.compile(r"^(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1)$", re.I)


def _tex(root):
    r"""Each display formula ($$...$$, \[...\]) in the page's text a placeholder, and each $...$ where the page's own
    settings make $ a formula's: their TeX, by placeholder ((display, TeX))."""
    found = []
    dollars = any(DOLLARS_SET.search(x.text or "") for x in root.iter("script"))

    def held(text):
        def one(m):
            found.append((True, re.sub(r"\s+", " ", m.group(1) or m.group(2)).strip()))
            return f" {MATH.format(len(found) - 1)} "

        def inline(m):
            found.append((False, m.group(1)))
            return INLINE.format(len(found) - 1)
        if not text or "$" not in text and "\\[" not in text:
            return text
        text = TEX.sub(one, text)
        return DOLLARS.sub(inline, text) if dollars else text
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        inside = el.tag in NO_TEX or any(a.tag in NO_TEX for a in el.iterancestors())
        if not inside:
            el.text = held(el.text)
        parent = el.getparent()
        if parent is not None and not (parent.tag in NO_TEX or any(a.tag in NO_TEX for a in parent.iterancestors())):
            el.tail = held(el.tail)
    return found


def _height(el):
    """The height the page gave an element, in pixels (its height attribute, or its style's), or None."""
    m = re.search(r"(?:^|;)\s*height\s*:\s*(\d+(?:\.\d+)?)px", el.get("style") or "")
    h = m.group(1) if m else el.get("height") or ""
    return int(float(h)) if re.fullmatch(r"\d+(?:\.\d+)?", h.strip()) else None


def _embeds(root):
    """Each <iframe> of another page (its address is the web's, not this computer's) a placeholder, in its place: what
    it is, by placeholder: its address, its name (title), the height the page gave it. One a few pixels high is a
    tracker's, not a figure."""
    found = []
    for fr in list(root.iter("iframe")):
        src = (fr.get("src") or fr.get("data-src") or "").strip()
        try:
            u = urllib.parse.urlsplit(src)
        except ValueError:
            continue
        h = _height(fr)
        if u.scheme not in ("http", "https") or not u.hostname or LOCAL_HOST.match(u.hostname) or (h is not None and h < 60):
            continue
        found.append({"src": src, "title": re.sub(r"\s+", " ", fr.get("title") or fr.get("aria-label") or "").strip(),
                      "h": max(160, min(1400, h or 480))})
        held = fr.makeelement("p", {})   # (a paragraph of its own: trafilatura drops a few words left between blocks)
        held.text, held.tail = EMBED.format(len(found) - 1), fr.tail
        fr.getparent().replace(fr, held)
    return found


def _real(img):
    """A picture of the page, not a lazy loader's stand-in (a 1-pixel GIF until a script swaps the picture in)."""
    src = img.get("src") or ""
    return bool(src) and not src.startswith("data:image/gif") and not (img.get("width") == "1" or img.get("height") == "1")


PANEL_WORDS = 4   # words a panel may have beside it, outside the caption: its letter, "(a)", a short label
NOT_PANELS = {"p", "table", "ul", "ol", "dl", "pre", "blockquote", "figure", "h1", "h2", "h3", "h4", "h5", "h6"}


def _panels(root):
    """Each <figure> holding several pictures and nothing else but its caption (a figure's panels, (a) to (d), that its
    page lays side by side) a placeholder, in its place: trafilatura would make each picture a figure of its own, one
    under the other. A figure with words of its own (paragraphs, a table, a figure in it, or a page's figure left open
    that runs on over the article) is read as before. What it is, by placeholder: its pictures (address, alt) and its
    caption (HTML)."""
    from lxml import html as lhtml
    found = []
    for fig in list(root.iter("figure")):
        if fig.getparent() is None or any(a.tag == "figure" for a in fig.iterancestors()):
            continue
        pics = [(im.get("src"), im.get("alt") or "") for im in fig.iter("img") if _real(im)]
        if len(pics) < 2:
            continue
        cap = next(fig.iter("figcaption"), None)
        inside = [e for e in fig.iter() if e is not fig and isinstance(e.tag, str) and (cap is None or not _within(e, cap))]
        words = len(fig.text_content().split()) - (len(cap.text_content().split()) if cap is not None else 0)
        if any(e.tag in NOT_PANELS for e in inside) or words > PANEL_WORDS * len(pics):
            continue
        inner = (htmllib.escape(cap.text or "", quote=False) + "".join(lhtml.tostring(c, encoding="unicode") for c in cap)) if cap is not None else ""
        found.append({"pics": pics, "cap": re.sub(r"\s+", " ", inner).strip()})
        held = fig.makeelement("p", {})   # (a paragraph of its own, as an embed's)
        held.text, held.tail = PANELS.format(len(found) - 1), fig.tail
        fig.getparent().replace(fig, held)
    return found


def _panels_html(f):
    """A figure's panels as LaTeXML writes a figure's subfigures (the reader lays them side by side): rows of up to
    four, three a row for six or nine."""
    n = len(f["pics"])
    per = n if n <= 4 else 3 if n % 3 == 0 else 4
    cells = []
    for k, (src, alt) in enumerate(f["pics"]):
        if k and k % per == 0:
            cells.append('<div class="ltx_flex_break"></div>')
        cells.append(f'<div class="ltx_flex_cell"><img src="{htmllib.escape(src)}" alt="{htmllib.escape(alt)}"></div>')
    cap = f"<figcaption>{f['cap']}</figcaption>" if f["cap"] else ""
    return f'<figure><div class="ltx_flex_figure">{"".join(cells)}</div>{cap}</figure>'


def _embed_html(e):
    name = e["title"] or urllib.parse.urlsplit(e["src"]).hostname
    return (f'<figure><a class="pf-embed pf-h-{e["h"]}" href="{htmllib.escape(e["src"])}" title="{htmllib.escape(e["title"])}">'
            f"{htmllib.escape(name, quote=False)}</a></figure>")


def _put_back(md, tex, embeds, panels=()):
    """The placeholders in the Markdown as what they hold: a formula a paragraph of its own ($$ on lines of their own),
    a figure an HTML block; in a list item, a table's row or a quote, where a paragraph would end it, in its line."""
    def inline(m):
        w = m.group(0)
        k = int(re.search(r"\d+", w).group(0))
        return (f"$${tex[k][1]}$$" if w.startswith("TFMATH") else f"${tex[k][1]}$" if w.startswith("TFTEX")
                else _panels_html(panels[k]) if w.startswith("TFPANELS") else _embed_html(embeds[k]))
    out = []
    for ln in md.splitlines():
        ln = re.sub(r"TFTEX\d+Q", inline, ln)   # (a formula in the line: there, as written)
        if not HELD.search(ln):
            out.append(ln)
        elif re.match(r"\s*(?:\||[-*+]\s|\d+[.)]\s|>)", ln):
            out.append(HELD.sub(inline, ln))
        else:
            for piece in HELD.split(ln):
                k = re.search(r"\d+", piece).group(0) if HELD.fullmatch(piece) else None
                if k is None:
                    if piece.strip():
                        out += ["", piece.strip(), ""]
                elif piece.startswith("TFMATH"):
                    out += ["", "$$", tex[int(k)][1], "$$", ""]
                elif piece.startswith("TFPANELS"):
                    out += ["", _panels_html(panels[int(k)]), ""]
                else:
                    out += ["", _embed_html(embeds[int(k)]), ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def read(page, final):
    """A page's article as TextFold reads it with no recipe (adapt.py has the ones written for a site): its Markdown,
    its HTML, and what the page says of itself (title, site, authors, date, license)."""
    import warnings
    # (macOS's own Python is built with LibreSSL; urllib3, which trafilatura brings, says so on import, harmlessly)
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    import trafilatura
    tree = visible(page, final)
    _hover_marks(tree)
    notes = _notes(tree)
    _hidden_out(tree)
    tex, embeds, panels = _tex(tree), _embeds(tree), _panels(tree)
    md = trafilatura.extract(tree, url=final, output_format="markdown", include_images=True, include_links=True,
                             include_tables=True, include_formatting=True, favor_recall=True) or ""
    if len(HELD.sub("", md).split()) < MIN_WORDS:
        raise ValueError("web-notext")
    info = about(tree, final)
    md = _put_back(_clean(md, info["title"]), tex, embeds, panels)
    html, _ = markdown.to_html(md)
    return _md_noted(md, notes), _noted(html, notes), info


# ── an article a reading view gave (the browser's button: extension/reader.js) ──────────────
VIEWS = ("defuddle", "readability")
LINKS_ONLY = 12   # words: a block at the head all of whose words are links (View PDF, Next article) is the site's


def view_of(read):
    """Which of the readings the browser's button sent to take: (its name, it), or (None, None) when neither has the
    words to read (the snapshot is read then). Defuddle's, which keeps a page's formulas, abstract and code, unless
    Readability's has half as many words again: Defuddle took away what it should not have."""
    got = {k: v for k, v in (read or {}).items() if k in VIEWS and isinstance(v, dict) and isinstance(v.get("html"), str)
           and isinstance(v.get("words"), (int, float)) and v["words"] >= MIN_WORDS}
    d, r = got.get("defuddle"), got.get("readability")
    if d and not (r and r["words"] >= 1.5 * d["words"]):
        return "defuddle", d
    return ("readability", r) if r else (None, None)


def _key(s):
    return re.sub(r"\W+", " ", s or "").strip().lower()


def _from_title(root, title):
    """The article from its title on: the heading that is the page's title and what stands before it (a publisher's
    name, a breadcrumb) gone, when that is a small part of the reading."""
    want, total = _key(title), len(root.text_content().split())
    if not want:
        return
    for h in root.iter("h1", "h2", "h3"):
        if _key(h.text_content()) != want:
            continue
        before, el = [], h
        while el is not root:
            before += list(el.itersiblings(preceding=True))
            el = el.getparent()
        if sum(len(b.text_content().split()) for b in before) > total * 0.15:
            return
        el = h
        while el is not root:
            parent = el.getparent()
            parent.text = None
            for b in el.itersiblings(preceding=True):
                parent.remove(b)
            el = parent
        _swap(h, "")
        return


HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
BULLET = re.compile(r"^\s*[•◦▪▫‣⁃∙·●○■□►▸–—-]\s*$")


def _site_bits(el):
    """A short block that is none of the article's words: all its words links (View PDF, Next article), a picture that
    is a link (a journal's logo), or nothing but punctuation (what a removed button left: ",")."""
    words = el.text_content().split()
    if len(words) > LINKS_ONLY:
        return False
    if not re.search(r"\w", el.text_content()):
        return all(img.getparent() is not None and any(a.tag == "a" for a in img.iterancestors()) for img in el.iter("img"))
    linked = sum(len(a.text_content().split()) for a in el.iter("a"))
    return linked == len(words) and all(any(a.tag == "a" for a in img.iterancestors()) for img in el.iter("img"))


def _head(root):
    """The head of the article as a reading view leaves it on a publisher's page: the site's bits before the article's
    own words (_site_bits), and then the headings left with nothing under them (Published by), gone."""
    blocks = ("p", "div", "ul", "ol", "section", "figure")
    heads = []
    for el in list(root.iter(*blocks, *HEADINGS)):
        if not _within(el, root):
            continue
        if el.tag in HEADINGS:
            heads.append(el)
            continue
        if any(d.tag in blocks or d.tag in HEADINGS for d in el.iterdescendants()):
            continue   # (its blocks are looked at one by one)
        words = el.text_content().split()
        if len(words) >= 30 and sum(len(a.text_content().split()) for a in el.iter("a")) < len(words) / 2:
            break   # (the article's own words have begun)
        if not _site_bits(el):
            continue
        while True:
            parent = el.getparent()
            _swap(el, "")
            if parent is root or parent.tag not in blocks or (parent.text_content().strip() and not _site_bits(parent)):
                break
            el = parent
    for h in heads:   # (a heading whose next words, in the order they are read, are another heading's)
        if not _within(h, root) or (h.tail or "").strip():
            continue
        order = list(root.iter())
        for e in order[order.index(h) + 1:]:
            if not isinstance(e.tag, str) or any(a is h for a in e.iterancestors()):
                continue
            if e.tag in HEADINGS:
                _swap(h, "")
                break
            if (e.text or "").strip() or (e.tail or "").strip() or e.tag == "img":
                break


def _bullets(root):
    """A list's item that writes its own bullet (•) before its words: the list has one already."""
    for li in root.iter("li"):
        if li.text and BULLET.match(li.text):
            li.text = None
        first = li[0] if len(li) else None
        if first is not None and not (li.text or "").strip() and len(first) == 0 and BULLET.match(first.text_content() or ""):
            _swap(first, "")


def _math_views(root):
    """Formulas Readability leaves as the page drew them, as their MathML (KaTeX keeps it beside its drawing, MathJax 3
    in mjx-assistive-mml): the drawing goes."""
    for box in root.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " katex ")] | //mjx-container'):
        m = next((x for x in box.iter() if isinstance(x.tag, str) and x.tag.rsplit("}", 1)[-1] == "math"), None)
        if m is not None and box.getparent() is not None:
            if box.tag == "mjx-container" and box.get("display") == "true":
                m.set("display", "block")
            m.tail = box.tail
            box.getparent().replace(box, m)


def _footnote_list(box, root):
    """Defuddle's list of footnotes (div#footnotes) as normalize knows one; an item no link in the text points to (a
    reference list Defuddle took for footnotes, its citations named otherwise) stays where the list is, as a list."""
    from lxml import html as lhtml
    cited = {a.get("href")[1:] for a in root.iter("a") if (a.get("href") or "").startswith("#") and not _within(a, box)}
    loose = lhtml.Element("ol")
    for li in [li for li in box.iter("li") if li.get("id") not in cited]:
        li.getparent().remove(li)
        for back in li.xpath('.//a[contains(@class, "footnote-backref")]'):
            _swap(back, "")
        li.attrib.pop("id", None)
        loose.append(li)
    if len(loose):
        box.addprevious(loose)
    if box.find(".//li") is None:
        _swap(box, "")
    else:
        box.set("class", "footnotes " + (box.get("class") or ""))


def read_view(view, page, final):
    """A reading view's article as TextFold reads it: its HTML for semantic.normalize (links and pictures as full
    addresses; the page's title, what stands before it and the site's bits at its head gone; bullets written into list
    items gone; formulas as MathML, not as drawn; Defuddle's footnote list as normalize knows one), and what the
    page says of itself, from the snapshot (about): its title the reading view's when the page names itself nowhere
    TextFold reads."""
    from lxml import html as lhtml
    info = about(visible(page, final), final)
    host = re.sub(r"^www\.", "", urllib.parse.urlparse(final).hostname or "")
    if info["title"] == host and (view.get("title") or "").strip():
        info["title"] = view["title"].strip()[:300]
    root = lhtml.fragment_fromstring(view["html"], create_parent="div")
    root.make_links_absolute(final, resolve_base_href=False)
    for a in root.iter("a"):   # (a place on the page, a footnote's mark: there again)
        if (a.get("href") or "").startswith(final + "#"):
            a.set("href", a.get("href")[len(final):])
    _from_title(root, info["title"])
    _head(root)
    _bullets(root)
    _math_views(root)
    for box in root.xpath('//div[@id="footnotes"]'):
        _footnote_list(box, root)
    html = lhtml.tostring(root, encoding="unicode")
    if len(re.sub(r"<[^>]+>", " ", html).split()) < MIN_WORDS and not re.search(r"[぀-ヿ㐀-鿿]", html):
        raise ValueError("web-notext")
    return html, info


def about(tree, final):
    """What a page says of itself: its title (the site's name off its end), site, authors, date and license."""
    import trafilatura
    m = trafilatura.extract_metadata(tree, default_url=final)
    host = re.sub(r"^www\.", "", urllib.parse.urlparse(final).hostname or "")
    site = (m.sitename if m and m.sitename else "") or host
    title = NOTE_AT.sub("", (m.title if m and m.title else "") or host)
    for name in {site, host}:   # "How to Do Great Work - Paul Graham": the site's name off the end
        title = re.sub(rf"\s*[-|–—·:]\s*{re.escape(name)}\s*$", "", title, flags=re.I) if name else title
    authors = [a.strip() for a in re.split(r";|\band\b", (m.author if m and m.author else "")) if a.strip()]
    return {"title": title.strip() or host, "site": site, "authors": authors[:12], "date": (m.date if m and m.date else "") or "",
            "license": m.license if m and m.license and re.match(r"https?://", m.license) else ""}


def meta_of(pid, final, info):
    return {"id": pid, "version": "", "title": info["title"], "authors": info["authors"], "date": info["date"],
            "snapshot": time.strftime("%Y-%m-%d"), "abs_url": final, "html_url": "", "license": info["license"],
            "source": {"kind": "web", "site": info["site"], "url": final}, "label": info["site"]}


def fetch(pid, progress=None):
    """Store a web page's article (once)."""
    d = store.pdir(pid)
    if (d / "meta.json").exists() and (d / "source.html").exists():
        return pid
    url = store.read_text(d / "url.txt").strip()
    data, final, charset = _get(url)
    page = data.decode(charset or "utf-8", "replace") if charset else data
    md, html, info = read(page, final)
    pre = local.prefetch(re.findall(r'<img[^>]*\ssrc="([^"]+)"', html))
    return local.store_doc(pid, html, meta_of(pid, final, info), "source.md", md,
                           lambda folder: local.Images(folder, None, pre=pre).resolve, sizes=True)
