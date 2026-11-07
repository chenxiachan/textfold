"""Any semantic HTML as the document parse.py reads.

Sources other than arXiv's LaTeXML page (Markdown now; JATS XML, Word and EPUB later) are first written as plain
semantic HTML: headings, paragraphs, lists, tables, figures, math, footnotes. normalize() rewrites that HTML in the
shape LaTeXML gives arXiv's pages (sections with titles, ltx_p paragraphs, figures with captions, equation tables,
footnotes inside the text), so every source is cut into units by one parser and drawn by one reader.

It is also where a document is made safe. The reader runs on the local server's origin, so a script in a file could
drive that server: only the elements and attributes a document needs are kept, and links and images only where they
point somewhere harmless.
"""
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DROP = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "textarea", "select", "option",
        "template", "link", "meta", "noscript", "svg", "canvas", "video", "audio", "base", "frame", "frameset", "head",
        "title"}
KEEP = {"a", "b", "blockquote", "br", "caption", "cite", "code", "dd", "del", "div", "dl", "dt", "em", "figcaption",
        "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre",
        "s", "section", "span", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul"}
MATHML = {"math", "semantics", "annotation", "annotation-xml", "mrow", "mi", "mn", "mo", "ms", "mtext", "mspace",
          "mfrac", "msqrt", "mroot", "msub", "msup", "msubsup", "munder", "mover", "munderover", "mmultiscripts",
          "mprescripts", "none", "mtable", "mtr", "mlabeledtr", "mtd", "mstyle", "mpadded", "mphantom", "menclose",
          "merror", "mfenced"}
ATTRS = {"id", "class", "title", "lang", "dir", "href", "src", "alt", "colspan", "rowspan", "start", "value"}
INLINE_SPAN = {"mark", "u", "small", "abbr", "q", "ins"}   # read as text; their look is not worth an atom
BLOCK = {"p", "ul", "ol", "dl", "table", "figure", "pre", "blockquote", "hr", "div", "section",
         "h1", "h2", "h3", "h4", "h5", "h6"}

SECTION_CLASS = ["ltx_section", "ltx_subsection", "ltx_subsubsection", "ltx_paragraph"]
HEAD_NUM = re.compile(r"^\s*((?:\d+\.)*\d+\.?|[A-Z]\.(?:\d+\.?)*|[IVX]+\.)\s+(?=\S)")
CAPTION = re.compile(r"^\s*((?:(?:Extended Data|Supplementary|Supplemental|Appendix)\s+)?"
                     r"(?:Figure|Fig\.?|Table|Tab\.?|Abbildung|Abb\.|Tabelle|Figura|Tabla|Tableau|图|圖|表)"
                     r"\s*S?[A-Z]?\d+(?:\.\d+)*[a-z]?)\s*[:.：]\s*", re.I)
TABLE_NOTE = re.compile(r"^\s*(?:Table|Tabelle|表)\s*[:：]\s*", re.I)   # Pandoc's caption line: "Table: ..."
ABSTRACT = re.compile(r"^(abstract|summary|tl;?dr|摘要|概要|zusammenfassung|résumé|resumen|riassunto|аннотация)$", re.I)
REFERENCES = re.compile(r"^(references|bibliography|works cited|literature( cited)?|sources|参考文献|參考文獻|"
                        r"literatur(verzeichnis)?|références|referencias|bibliografia|riferimenti( bibliografici)?|"
                        r"список литературы|литература)$", re.I)


def normalize(html, resolve_img=None, sizes=False):
    """Semantic HTML -> the HTML parse.parse reads. resolve_img(src) gives where the page finds an image (img/...) or
    None when it cannot be had; without it, images are left out. sizes: keep an image's width and height in pixels, as
    a Markdown file's author wrote them (a README's small logo); other sources' images are fetched at another size."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.body or soup
    for c in [s for s in root.descendants if isinstance(s, Comment)]:
        c.extract()
    _sanitize(root, sizes)
    _diagrams(root)
    _math(soup, root)
    _footnotes(soup, root)
    if sizes:   # the Markdown path (a README, a document, a web page, a converted PDF): its tables that are not data
        _unfold_tables(soup, root)
    for cap in root.find_all("figcaption"):   # a caption written in paragraphs (<figcaption><p>...): their words
        ps = cap.find_all("p", recursive=False)
        for k, p in enumerate(ps):
            if k:
                p.insert_before(" ")
            p.unwrap()
        if ps and isinstance(cap.contents[-1], NavigableString):
            cap.contents[-1].replace_with(cap.contents[-1].rstrip())
    _loose_images(soup, root)
    _images(soup, root, resolve_img)
    _tables(soup, root)
    # (a walk with a set of names: find_all with a list builds a matcher for each name on every call)
    flat = {"blockquote", "div", "section", "article", "main", "header", "footer", "aside", "hr", *INLINE_SPAN}
    for el in [t for t in root.descendants if t.name in flat]:
        if el.name == "hr":
            el.decompose()
        elif el.name in INLINE_SPAN:
            el.name = "span"
        elif not _ours(el):
            el.unwrap()   # a quotation's paragraphs are read like any other; containers carry nothing of their own
    _lists(soup, root)
    blocks = _flow(soup, root)
    art = soup.new_tag("article", attrs={"class": "ltx_document"})
    _sections(soup, art, blocks)
    for el in art.find_all(True):
        _keep_classes(el)
    return str(art)


def _ours(el):
    return any(c.startswith("ltx_") for c in el.get("class", []))


def _sanitize(root, sizes=False):
    dropped = set()   # ids of the elements inside a dropped one (el.decomposed would search each element's subtree)
    for el in list(root.find_all(True)):
        if id(el) in dropped:
            continue
        name = (el.name or "").lower()
        if name in DROP:
            dropped.update(map(id, el.descendants))
            el.decompose()
            continue
        if name in MATHML:
            for k in list(el.attrs):
                if k.lower().startswith("on") or k.lower() in ("href", "src", "style") or ":" in k and k != "xmlns":
                    del el.attrs[k]
            continue
        if name not in KEEP:
            el.unwrap()
            continue
        align = re.search(r"text-align:\s*(left|right|center)", el.get("style", "")) if name in ("td", "th") else None
        size = {k: el.get(k) for k in ("width", "height") if sizes and name == "img" and re.fullmatch(r"\d{1,4}", str(el.get(k, "")))}
        for k in list(el.attrs):
            if k.lower() not in ATTRS:
                del el.attrs[k]
        el.attrs.update(size)   # a picture's size in pixels, as its page gave it (a logo stays small)
        if align:   # a Markdown table's column alignment, as LaTeXML's class
            el["class"] = [f"ltx_align_{align.group(1)}"]
        # (/p/<id>: a document of the library, as a summary's notes link to theirs: summary.py)
        if name == "a" and not re.match(r"^(#|https?://|mailto:|/p/[\w.-]+(?:\?lang=[\w-]+)?(?:#[\w=&.-]*)?$)", el.get("href", ""), re.I):
            el.attrs.pop("href", None)
        if name == "img" and re.match(r"^(javascript|vbscript|data:(?!image/(png|jpe?g|gif|webp)))", el.get("src", ""), re.I):
            el.attrs.pop("src", None)


MERMAID = {"mermaid", "language-mermaid", "language-{mermaid}"}   # (```{mermaid}: Quarto's)


def _diagrams(root):
    """A diagram written in Mermaid (```mermaid in Markdown; <pre class="mermaid"> or <pre lang="mermaid"> on a web page)
    as <pre class="pf-mermaid"> holding its source alone: the reader draws it (web/vendor/mermaid), and where it cannot,
    the source shows as the code it is."""
    for el in [t for t in root.descendants if t.name in ("pre", "div")]:
        code = el.find("code", recursive=False) if el.name == "pre" else None
        names = {*el.get("class", []), *(code.get("class", []) if code else [])}
        if not (names & MERMAID or el.get("lang") == "mermaid") or (el.name == "div" and el.find(True)):
            continue   # (a div: only one holding the source alone, not a page's diagram already drawn)
        src = el.get_text().strip("\n")
        if src.strip():
            el.name, el.attrs = "pre", {"class": ["pf-mermaid"]}
            el.string = src


def _keep_classes(el):
    cls = [c for c in el.get("class", []) if c.startswith(("ltx_", "pf-"))]
    if cls:
        el["class"] = cls
    else:
        el.attrs.pop("class", None)


# ── math ──────────────────────────────────────────────────────────────
def tex_math(latex, display=False):
    """LaTeX as MathML that carries its source (alttext), as LaTeXML writes formulas: the browser draws it with no
    script, and the model reads the LaTeX. Marked pf-tex: written in LaTeX, so the reader draws it with KaTeX, as Typora
    draws it with MathJax (MathML from latex2mathml has no aligned, no \\operatorname, no space after \\log); the MathML
    is what a page without KaTeX shows. A formula the converter cannot read is its source, for KaTeX to draw."""
    latex = latex.strip()
    texts, plain = _texts(latex)
    try:
        from latex2mathml.converter import convert
        m = BeautifulSoup(convert(plain, display="block" if display else "inline"), "lxml").find("math")
    except Exception:
        m = None
    if m is None:
        m = BeautifulSoup(f'<math display="{"block" if display else "inline"}"><merror><mtext></mtext></merror></math>', "lxml").find("math")
        m.mtext.string = latex
    for t in m.find_all("mtext"):
        if t.get_text() in texts:
            t.string = texts[t.get_text()]
    m["alttext"] = latex
    m["class"] = ["pf-tex"]
    return m


TEXT_CMD = re.compile(r"\\(?:text|textrm|textnormal|mbox)\s*\{")
TEXT_ESC = {"textbackslash": "\\", "textasciicircum": "^", "textasciitilde": "~"}


def _texts(latex):
    """The words of each \\text{...} of a formula as they read, and the formula with a placeholder for each. latex2mathml
    ends \\text at its first } and keeps its escapes as they are written (50\\% off, x\\textasciicircum{), so the words
    are put back in its <mtext> afterwards. A \\text holding math or a command of its own is left to latex2mathml."""
    texts, out, pos = {}, [], 0
    for m in TEXT_CMD.finditer(latex):
        if m.start() < pos:
            continue
        i, depth = m.end(), 1
        while i < len(latex) and depth:   # to the brace closing it (an escaped one is a character of the text)
            if latex[i] == "\\":
                i += 2
                continue
            depth += {"{": 1, "}": -1}.get(latex[i], 0)
            i += 1
        if depth:
            break
        body = latex[m.end():i - 1]
        words = re.sub(r"\\(textbackslash|textasciicircum|textasciitilde)\b(?:\{\})?|\\([{}%&#$_ ])|~",
                       lambda x: TEXT_ESC[x.group(1)] if x.group(1) else x.group(2) or "\u00a0", body)
        if "$" in body.replace("\\$", "") or re.search(r"\\[A-Za-z]", re.sub(r"\\(textbackslash|textasciicircum|textasciitilde)\b", "", body)):
            continue
        key = f"TFTEXT{len(texts)}X"
        texts[key] = words.replace(" ", "\u00a0")   # (as latex2mathml keeps a text's spaces: a MathML token trims its own)
        out.append(latex[pos:m.start()] + r"\text{" + key + "}")
        pos = i
    return texts, "".join(out) + latex[pos:]


def _strip_delims(s):
    s = s.strip()
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b) - 1:
            return s[len(a):len(s) - len(b)]
    return s


def _equation(soup, math, number=None, eid=None):
    """LaTeXML's equation table; its number (number: "(2)") in the cell on the right, as arXiv's are."""
    t = soup.new_tag("table", attrs={"class": "ltx_equation ltx_eqn_table", **({"id": eid} if eid else {})})
    tr = soup.new_tag("tr", attrs={"class": "ltx_equation ltx_eqn_row"})
    for cls in ("ltx_eqn_cell ltx_eqn_center_padleft", "ltx_eqn_cell ltx_align_center", "ltx_eqn_cell ltx_eqn_center_padright"):
        tr.append(soup.new_tag("td", attrs={"class": cls}))
    tr.find_all("td")[1].append(math)
    if number:
        tr.append(_eqno(soup, number))
    tbody = soup.new_tag("tbody")
    tbody.append(tr)
    t.append(tbody)
    return t


# A Markdown file's display formulas are numbered as Typora numbers them (Auto Numbering Math Equations): each in turn,
# (1), (2), on the right; \tag{A} gives its own, (A), and \tag*{A} it without the brackets, neither counted; \notag,
# \nonumber and a starred environment (equation*, align*) none. \label{x} names the formula, and \eqref{x} (\ref{x})
# elsewhere is its number, a link to it. KaTeX numbers equation, align and gather itself, by CSS counters that would
# count only the formulas on screen: their numbers are given here, and they are drawn as aligned and gathered.
ENVS = {"equation": None, "align": "aligned", "flalign": "aligned", "alignat": "alignedat", "gather": "gathered",
        "multline": "gathered"}
ENV = re.compile(r"^\\begin\{(" + "|".join(ENVS) + r")(\*?)\}(.*)\\end\{\1\2\}$", re.S)
LABEL = re.compile(r"\\label\s*\{([^{}]*)\}")
TAG = re.compile(r"\\tag(\*?)\s*\{([^{}]*)\}")
NOTAG = re.compile(r"\\(?:notag|nonumber)(?![A-Za-z])")
REF = re.compile(r"\\(eq)?ref\s*\{([^{}]*)\}")


def _numbered(latex, auto):
    """(the formula as drawn, its number or None, its label or None). auto: numbered unless it says otherwise."""
    label = LABEL.search(latex)
    tag = TAG.search(latex)
    rows = len(re.findall(r"\\\\", latex)) + 1
    off = len(NOTAG.findall(latex)) >= rows   # (\notag on every line of an align: no number)
    latex = NOTAG.sub("", TAG.sub("", LABEL.sub("", latex))).strip()
    env = ENV.match(latex)
    if env:
        name, star, body = env.groups()
        off = off or bool(star)
        inner = ENVS[name]
        latex = body.strip() if inner is None else f"\\begin{{{inner}}}{body}\\end{{{inner}}}"
    number = (tag.group(2).strip() if tag.group(1) else f"({tag.group(2).strip()})") if tag else None
    return latex, number or ("" if off or not auto else None), label.group(1).strip() if label else None


def _eqno(soup, number):
    no = soup.new_tag("td", attrs={"class": "ltx_eqn_cell ltx_eqn_eqno"})
    tag = soup.new_tag("span", attrs={"class": "ltx_tag ltx_tag_equation"})
    tag.string = number
    no.append(tag)
    return no


def _label_id(label):
    """A labelled formula's id: eq-heat for \\label{eq:heat}, and for \\label{heat}."""
    name = re.sub(r"[^\w-]+", "-", label).strip("-")
    return name if name.startswith("eq-") else "eq-" + name


def _ref(m, labels):
    """What \\eqref{x} (m: a REF match) reads: the number of the formula x names, (2); \\ref{x} without the brackets."""
    number = labels.get(m.group(2).strip(), ("(?)",))[0]
    return number if m.group(1) else number.strip("()")


def _ref_link(soup, m, labels):
    """$\\eqref{eq:heat}$ alone: "(1)", a link to the formula, as arXiv's "Eq. (1)"."""
    a = soup.new_tag("a", attrs={"class": "ltx_ref"})
    if m.group(2).strip() in labels:
        a["href"] = "#" + labels[m.group(2).strip()][1]
    a.string = _ref(m, labels)
    return a


def _math(soup, root):
    """markdown-it's and Pandoc's math (span.math.inline, div.math.block, span.math.display) becomes MathML; MathML
    that came as such keeps its LaTeX annotation as its alttext, and MathML that is nothing but its LaTeX is drawn from
    it. markdown-it's display formulas (a Markdown file's $$) are numbered, and any formula's \\tag; a reference to a
    label is its number."""
    found, labels, n = [], {}, 0
    for el in [t for t in root.descendants if t.name in ("span", "div") and "math" in t.get("class", ())]:
        cls = el.get("class", [])
        display = el.name == "div" or "display" in cls or "block" in cls
        latex, number, label = _strip_delims(el.get_text("")), None, None
        if display:
            latex, number, label = _numbered(latex, auto=el.name == "div")
            if number is None:
                n += 1
                number = f"({n})"
            if label:
                labels[label] = (number, _label_id(label))
        found.append((el, display, latex, number, label))
    for el, display, latex, number, label in found:
        alone = None if display else REF.fullmatch(latex.strip())
        if alone:
            el.replace_with(_ref_link(soup, alone, labels))
            continue
        m = tex_math(REF.sub(lambda r: r"\text{" + _ref(r, labels) + "}", latex), display)
        el.replace_with(_equation(soup, m, number, label and labels[label][1]) if display else m)
    for m in root.find_all("math"):
        if not m.get("alttext"):
            ann = m.find("annotation", attrs={"encoding": "application/x-tex"})
            if ann:
                m["alttext"] = ann.get_text("").strip()
            elif m.get("data-latex"):   # (Defuddle's)
                m["alttext"] = m["data-latex"]
        if m.get("alttext") and not m.find(lambda t: t.name not in ("semantics", "annotation", "annotation-xml")):
            drawn = tex_math(m["alttext"], m.get("display") == "block")   # (its LaTeX alone: a page's MathJax drew it)
            m.replace_with(drawn)
            m = drawn
        if m.get("display") == "block" and not m.find_parent("table", class_="ltx_equation"):
            ph = soup.new_tag("span")
            m.replace_with(ph)
            ph.replace_with(_equation(soup, m))


def upgrade(soup, root):
    """A Markdown document stored before 0.10.34, read as one stored now: each display formula numbered (its \\label,
    \\tag and environment read as _math reads them), $\\eqref{}$ its number and a link, every formula marked for KaTeX
    (pf-tex), every table ruled (pf-grid). Only how they look: its paragraphs keep their ids (ids.freeze marked them)
    and their words, so their levels and notes stay theirs, and the file stays as it was stored (parse.py)."""
    labels, n, eqs = {}, 0, []
    for t in root.find_all("table", class_="ltx_equation"):
        m = t.find("math")
        if m is None or not m.get("alttext") or t.find(class_="ltx_eqn_eqno"):
            continue
        latex, number, label = _numbered(m["alttext"], auto=True)
        if number is None:
            n += 1
            number = f"({n})"
        if label:
            labels[label] = (number, t.get("id") or _label_id(label))
            t["id"] = labels[label][1]
        eqs.append((t, m, latex, number))
    for t, m, latex, number in eqs:
        m["alttext"] = latex
        if number:
            t.find("tr").append(_eqno(soup, number))
    for m in root.find_all("math"):
        if not m.get("alttext"):
            continue
        alone = None if m.get("display") == "block" else REF.fullmatch(m["alttext"].strip())
        if alone:
            m.replace_with(_ref_link(soup, alone, labels))
            continue
        m["alttext"] = REF.sub(lambda r: r"\text{" + _ref(r, labels) + "}", m["alttext"])
        m["class"] = ["pf-tex"]
    for t in root.find_all("table", class_="ltx_tabular"):
        t["class"] = [c for c in t.get("class", []) if c != "pf-grid"] + ["pf-grid"]


# ── footnotes ─────────────────────────────────────────────────────────
def _footnotes(soup, root):
    """A footnote list at the end (markdown-it, Pandoc) moves into the text, where its mark is: LaTeXML's shape, which
    the reader shows on hover."""
    notes = {}
    boxes = [t for t in root.descendants if t.name in ("section", "div", "ol") and any(c.startswith("footnotes") for c in t.get("class", ()))]
    for box in boxes:
        for li in box.find_all("li", id=True):
            for back in li.find_all("a", class_=re.compile("footnote-back|footnote-backref")):
                back.decompose()
            for back in li.find_all("a", href=re.compile(r"^#fnref")):
                back.decompose()
            notes[li["id"]] = li
    if not notes:
        return
    n = 0
    for a in root.find_all("a", href=True):
        fid = a["href"][1:] if a["href"].startswith("#") else None
        if fid not in notes:
            continue
        n += 1
        mark = re.sub(r"[\[\]\s]", "", a.get_text("")) or str(n)
        note = soup.new_tag("span", attrs={"class": "ltx_note ltx_role_footnote"})
        sup = soup.new_tag("sup", attrs={"class": "ltx_note_mark"})
        sup.string = mark
        outer = soup.new_tag("span", attrs={"class": "ltx_note_outer"})
        content = soup.new_tag("span", attrs={"class": "ltx_note_content"})
        sup2 = soup.new_tag("sup", attrs={"class": "ltx_note_mark"})
        sup2.string = mark
        content.append(sup2)
        for p in list(notes[fid].children):
            if isinstance(p, Tag) and p.name == "p":
                for x in list(p.contents):
                    content.append(x.extract())
                content.append(" ")
            elif isinstance(p, (Tag, NavigableString)) and str(p).strip():
                content.append(p.extract() if isinstance(p, Tag) else str(p))
        outer.append(content)
        note.append(sup)
        note.append(outer)
        host = a.parent if a.parent and a.parent.name == "sup" and len(a.parent.find_all(True)) == 1 else a
        host.replace_with(note)
    for box in boxes:
        box.decompose()
    for hr in root.find_all("hr", class_="footnotes-sep"):
        hr.decompose()


# ── figures and tables ────────────────────────────────────────────────
def _caption(soup, text_el, label_re=CAPTION):
    """A figcaption from a paragraph (or caption): its "Figure 3:" label, when it has one, as LaTeXML's tag."""
    cap = soup.new_tag("figcaption", attrs={"class": "ltx_caption"})
    for x in list(text_el.contents):
        cap.append(x.extract())
    first = next((x for x in cap.contents if isinstance(x, NavigableString) and x.strip()), None)
    if first is not None:
        m = label_re.match(str(first))
        if m:
            tag = soup.new_tag("span", attrs={"class": "ltx_tag ltx_tag_figure"})
            tag.string = m.group(1) + ": "
            first.replace_with(str(first)[m.end():])
            cap.insert(0, tag)
    return cap


def _sibling(el, step):
    s = el.next_sibling if step > 0 else el.previous_sibling
    while s is not None and isinstance(s, NavigableString) and not s.strip():
        s = s.next_sibling if step > 0 else s.previous_sibling
    return s if isinstance(s, Tag) else None


def _lone_img(p):
    kids = [c for c in p.contents if not (isinstance(c, NavigableString) and not c.strip()) and getattr(c, "name", None) != "br"]
    return kids[0] if len(kids) == 1 and getattr(kids[0], "name", None) == "img" else None


LOOSE = {"img", "a", "br", "span", "b", "strong", "em", "i", "code", "sub", "sup"}


def _loose_images(soup, root):
    """Pictures written straight into a container (a README's centred <div>: its logo, its badges, its GIF) into a
    paragraph of their own, with what runs beside them, so a lone one can become a figure like any other."""
    for img in list(root.find_all("img")):
        parent = img.parent
        while parent is not None and parent.name == "a":
            img, parent = parent, parent.parent
        if parent is None or parent.name not in ("div", "section", "article", "main", "body", "blockquote", "center"):
            continue
        run = [img]
        for step in (-1, 1):
            sib = img.previous_sibling if step < 0 else img.next_sibling
            while sib is not None and (isinstance(sib, NavigableString) or sib.name in LOOSE):
                (run.insert(0, sib) if step < 0 else run.append(sib))
                sib = sib.previous_sibling if step < 0 else sib.next_sibling
        p = soup.new_tag("p")
        run[0].insert_before(p)
        for n in run:
            p.append(n.extract())


def _images(soup, root, resolve_img):
    for p in root.find_all("p"):
        img = _lone_img(p)
        if img is None:
            continue
        if str(img.get("width", "")).isdigit() and int(img["width"]) < 160:
            continue   # a logo or an icon: drawn where it is, not a figure with a caption
        fig = soup.new_tag("figure", attrs={"class": "ltx_figure"})
        fig.append(img.extract())
        nxt = _sibling(p, 1)
        if nxt is not None and nxt.name == "p" and CAPTION.match(nxt.get_text("")) and not CAPTION.match(nxt.get_text("")).group(1).lower().startswith(("tab", "表")):
            fig.append(_caption(soup, nxt))
            nxt.decompose()
        elif (img.get("alt") or "").strip() and not re.search(r"\.(png|jpe?g|gif|webp|svg)$", img["alt"], re.I):
            cap = soup.new_tag("p")
            cap.string = img["alt"].strip()
            fig.append(_caption(soup, cap))
        p.replace_with(fig)
    for img in root.find_all("img"):
        where = resolve_img(img.get("src", "")) if resolve_img and img.get("src") else None
        if where:
            img["src"] = where
        else:   # an image the document names but that could not be had: its name stays, as a placeholder
            ph = soup.new_tag("span", attrs={"class": "pf-missing"})
            ph.string = "🖼 " + ((img.get("alt") or "").strip() or img.get("src", "").split("/")[-1].split("?")[0] or "image")
            img.replace_with(ph)
    for fig in root.find_all("figure"):
        if fig.find("table") and not fig.find("img"):
            continue   # a table's figure: _tables
        fig["class"] = ["ltx_figure"]
        cap = fig.find("figcaption")
        if cap is not None and "ltx_caption" not in cap.get("class", []):
            p = soup.new_tag("p")
            for x in list(cap.contents):
                p.append(x.extract())
            cap.replace_with(_caption(soup, p))


BLOCK = ["p", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table", "div", "figure"]


def _unfold_tables(soup, root):
    """Tables that hold text, not data, read as text: a layout's table (a screenshot beside its paragraph, in one to
    three rows) gives its cells' contents in reading order; a table of short names each with a sentence (a README's
    features, a comparison in words) becomes a list, "name: sentence", so its sentences fold like any other. A display
    formula's table (_equation) is none of these: a long one has thirty words in its MathML, and unfolded it lost its
    box, which scrolls it within the column, and in a list item the formula itself."""
    def words(el):
        return len(el.get_text(" ", strip=True).split())

    for t in root.find_all("table"):
        if t.find_parent("table") is not None or t.parent is None or _ours(t):
            continue   # a table inside another: the outer one decides; a formula's stays
        rows = [r for r in t.find_all("tr") if r.find_parent("table") is t]
        cells = [r.find_all(["td", "th"], recursive=False) for r in rows]
        if not rows or not any(cells):
            continue
        ncol = max(len(c) for c in cells)
        if len(rows) <= 3 and ncol <= 3 and (t.find("img") is not None or any(words(c) >= 30 for r in cells for c in r)):
            out = []
            for c in (c for r in cells for c in r):
                if not c.get_text(strip=True) and c.find("img") is None:
                    continue
                if c.find(BLOCK, recursive=False) is not None:
                    out.extend(list(c.contents))
                else:
                    p = soup.new_tag("p")
                    for x in list(c.contents):
                        p.append(x.extract())
                    out.append(p)
            for x in out:
                t.insert_before(x.extract() if isinstance(x, Tag) else NavigableString(str(x)))
            t.decompose()
            continue
        head = rows[0].find("th") is not None or t.find("thead") is not None
        body = [r for r in (cells[1:] if head else cells) if len(r) == 2]
        if ncol == 2 and len(body) >= 2 and all(words(a) <= 6 for a, _ in body) and sum(words(b) for _, b in body) / len(body) >= 8:
            ul = soup.new_tag("ul")
            for a, b in body:
                li, name = soup.new_tag("li"), soup.new_tag("strong")
                for x in list(a.contents):
                    name.append(x.extract())
                li.append(name)
                li.append(NavigableString(": "))
                for x in list(b.contents):
                    li.append(x.extract())
                ul.append(li)
            t.replace_with(ul)


def _tables(soup, root):
    for t in root.find_all("table"):
        if _ours(t):
            continue   # an equation's table
        # pf-grid: a table from Markdown, a web page or a document, drawn as Typora draws one (reader.css): every cell
        # ruled, every other row shaded. A paper's (LaTeXML's) keeps its booktabs rules
        t["class"] = ["ltx_tabular", "ltx_centering", "pf-grid"]
        rows = t.find_all("tr")
        for n, tr in enumerate(rows):
            cells = tr.find_all(["td", "th"])
            head = tr.find_parent("thead") is not None or all(c.name == "th" for c in cells)
            for c in cells:
                cls = ["ltx_td"] + (["ltx_th", "ltx_border_b"] if head else []) + (["ltx_border_tt"] if n == 0 else []) + \
                      (["ltx_border_bb"] if n == len(rows) - 1 else [])
                c["class"] = cls + [x for x in c.get("class", []) if x.startswith("ltx_align_")]
        fig = t.find_parent("figure")
        if fig is None:
            fig = soup.new_tag("figure")
            t.replace_with(fig)
            fig.append(t)
        fig["class"] = ["ltx_table"]
        cap = fig.find("figcaption") or t.find("caption")
        src = None
        if cap is None:
            before, after = _sibling(fig, -1), _sibling(fig, 1)
            if before is not None and before.name == "p" and CAPTION.match(before.get_text("")):
                src = before
            elif after is not None and after.name == "p" and (CAPTION.match(after.get_text("")) or TABLE_NOTE.match(after.get_text(""))):
                src = after
        else:
            src = cap
        if src is not None:
            p = soup.new_tag("p")
            for x in list(src.contents):
                p.append(x.extract())
            first = next((x for x in p.contents if isinstance(x, NavigableString) and x.strip()), None)
            if first is not None and TABLE_NOTE.match(str(first)):
                first.replace_with(TABLE_NOTE.sub("", str(first), count=1))
            src.decompose()
            fig.insert(0, _caption(soup, p))


# ── lists ─────────────────────────────────────────────────────────────
def _wrap_inline(soup, el):
    """Text written straight into a list item (a tight list) becomes a paragraph, as LaTeXML writes every item."""
    run = []

    def flush():
        if any(not (isinstance(x, NavigableString) and not x.strip()) for x in run):
            p = soup.new_tag("p")
            run[0].insert_before(p)
            for x in run:
                p.append(x.extract())
        run.clear()

    for ch in list(el.contents):
        if isinstance(ch, Tag) and ch.name in BLOCK:
            flush()
        else:
            run.append(ch)
    flush()


def _lists(soup, root):
    for lst in [t for t in root.descendants if t.name in ("ul", "ol", "dd")]:
        if lst.name == "dd":
            _wrap_inline(soup, lst)
            continue
        n = int(lst.get("start", 1)) if str(lst.get("start", "1")).isdigit() else 1
        for li in lst.find_all("li", recursive=False):
            _wrap_inline(soup, li)
            if lst.name == "ol":
                n = int(li["value"]) if str(li.get("value", "")).isdigit() else n
                tag = soup.new_tag("span", attrs={"class": "ltx_tag ltx_tag_item"})
                tag.string = f"{n}."
                li.insert(0, tag)
                n += 1


# ── the flow and its sections ─────────────────────────────────────────
def _flow(soup, root):
    """The document as a flat run of blocks: loose inline text becomes paragraphs, a paragraph holding a block (a
    display formula Pandoc wrote inside it) is split around it."""
    out, run = [], []
    blank = lambda x: (isinstance(x, NavigableString) and not x.strip()) or getattr(x, "name", None) == "br"

    def flush():
        if not all(blank(x) for x in run):   # line breaks alone (a README's <br> spacers) make no paragraph
            p = soup.new_tag("p")
            for x in run:
                p.append(x.extract() if isinstance(x, Tag) else NavigableString(str(x)))
            out.append(p)
        run.clear()

    for ch in list(root.contents):
        if isinstance(ch, Tag) and (ch.name in BLOCK or _ours(ch)):
            flush()
            if ch.name == "p" and all(blank(x) for x in ch.contents):
                ch.decompose()
                continue
            out.extend(_split_p(soup, ch) if ch.name == "p" else [ch.extract()])
        elif isinstance(ch, (Tag, NavigableString)):
            run.append(ch)
    flush()
    return out


def _split_p(soup, p):
    p = p.extract()
    if not any(t.name in ("table", "figure", "ul", "ol", "pre", "div") for t in p.descendants):
        return [p]
    parts, cur = [], soup.new_tag("p")
    for ch in list(p.contents):
        if isinstance(ch, Tag) and ch.name in ("table", "figure", "ul", "ol", "pre", "div"):
            if cur.get_text("").strip() or cur.find(True):
                parts.append(cur)
            parts.append(ch.extract())
            cur = soup.new_tag("p")
        else:
            cur.append(ch.extract() if isinstance(ch, Tag) else NavigableString(str(ch)))
    if cur.get_text("").strip() or cur.find(True):
        parts.append(cur)
    return parts


def slug(text):
    s = re.sub(r"[^\w\- ]", "", text.lower(), flags=re.U).strip()
    return re.sub(r"\s+", "-", s)


def _sections(soup, art, blocks):
    heads = [b for b in blocks if re.fullmatch(r"h[1-6]", b.name or "")]
    is_head = {id(h) for h in heads}   # by identity: bs4 compares tags by content, and two headings may read alike
    top = min((int(h.name[1]) for h in heads), default=1)
    used, anchors = set(), {}
    lead = bool(blocks) and id(blocks[0]) not in is_head
    explicit_abstract = next((h for h in heads if ABSTRACT.match(h.get_text(" ", strip=True))), None) if not lead else None
    stack = [(1, art)]
    if lead:   # text before the first heading: an untitled first section, the document's "abstract" (it is read
        sec = soup.new_tag("section", attrs={"class": "ltx_section", "id": "abstract"})   # as context for the rest)
        art.append(sec)
        stack.append((2, sec))
        used.add("abstract")
    bib = []
    for b in blocks:
        if id(b) in is_head:
            depth = min(int(b.name[1]) - top, 3)
            rank = 2 + depth
            while stack[-1][0] >= rank:
                stack.pop()
            text = b.get_text(" ", strip=True)
            if REFERENCES.match(text):
                sec = soup.new_tag("section", attrs={"class": "ltx_bibliography"})
                bib.append(sec)
            else:
                sid = "abstract" if b is explicit_abstract else "sec-" + (slug(text) or str(len(used)))
                base, k = sid, 2
                while sid in used:
                    sid, k = f"{base}-{k}", k + 1
                used.add(sid)
                anchors[slug(text)] = sid
                if b.get("id"):   # a source's own id for the section (JATS: <sec id="sec2">), which its links use
                    anchors[b["id"]] = sid
                cls = SECTION_CLASS[depth]
                # an appendix where the source says so (JATS's <app> reaches the heading as ltx_title_appendix), not
                # where a heading's words seem to. Its subsections sit inside it, as parse.in_appendix finds them;
                # the class on them would take their level (SECTION_LEVEL: ltx_appendix is a section's)
                if "ltx_title_appendix" in (b.get("class") or []):
                    cls = [cls, "ltx_appendix"]
                sec = soup.new_tag("section", attrs={"class": cls, "id": sid})
            h = soup.new_tag(f"h{rank}", attrs={"class": "ltx_title ltx_title_section"})
            for x in list(b.contents):
                h.append(x.extract() if isinstance(x, Tag) else NavigableString(str(x)))
            first = next((x for x in h.contents if isinstance(x, NavigableString) and x.strip()), None)
            m = HEAD_NUM.match(str(first)) if first is not None and h.contents and first is h.contents[0] else None
            if m:
                tag = soup.new_tag("span", attrs={"class": "ltx_tag ltx_tag_section"})
                tag.string = m.group(1).rstrip(".")
                first.replace_with(str(first)[m.end():])
                h.insert(0, tag)
            sec.append(h)
            stack[-1][1].append(sec)
            stack.append((rank, sec))
        else:
            if b.name == "p":
                b["class"] = ["ltx_p"]
            stack[-1][1].append(b)
    for sec in bib:
        _bibliography(soup, sec)
    for el in [t for t in art.descendants if t.name in ("p", "a")]:
        if el.name == "p":
            el["class"] = ["ltx_p"]
        elif el.get("href", "").startswith("#") and el["href"][1:] in anchors:   # [Methods](#methods): the section's own id
            el["href"] = "#" + anchors[el["href"][1:]]


def _bibliography(soup, sec):
    """A references section is a list of works, not text to fold: LaTeXML's bibliography, shown at Full."""
    items = []
    for ch in [c for c in sec.contents if isinstance(c, Tag) and not re.fullmatch(r"h[1-6]", c.name)]:
        if ch.name in ("ul", "ol"):
            for li in ch.find_all("li", recursive=False):
                tag = li.find(class_="ltx_tag")
                if tag:
                    tag.decompose()
                items.append(li)
        elif ch.get_text("").strip():
            items.append(ch)
        ch.extract()
    ul = soup.new_tag("ul", attrs={"class": "ltx_biblist"})
    for n, it in enumerate(items, 1):   # a work keeps the id its citations point at (JATS: <ref id="B3">)
        li = soup.new_tag("li", attrs={"class": "ltx_bibitem", "id": it.get("id") or f"bib{n}"})
        for x in list(it.contents):
            li.append(x.extract() if isinstance(x, Tag) else NavigableString(str(x)))
        ul.append(li)
    sec.append(ul)
