"""A web page read with a recipe written for its site (docs/web-adapt.md, the skill in skills/web-adapt): which element
holds the article, which parts are furniture, which marks open notes, which controls hold the article's words, and
which elements are figures drawn by scripts. A recipe names elements by CSS selectors and never holds words: the
words are taken from the page by this code, and checked, every one, against the page as its reader sees it.

The same recipe is meant for a page as fetched (no scripts run), as a browser rendered it (the app's, or a browser
extension's, after its scripts), and, later, for the live page in a reader's browser: so its selectors are CSS that
both soupsieve (here) and querySelectorAll (there) read, and what a recipe can do stops at what the reader can see.
A snapshot is the page as its reader saw it, a subscriber's whole, and is read as it is: text it hides, or a
paywall's cut for a reader without access, is never sought elsewhere.

  python3 -m textfold adapt <url> [--snapshot page.html [--rendered]]           what an agent is given: the outline
  python3 -m textfold adapt <url> [--snapshot page.html] --try recipe.json [--keep]   the page read with a recipe
"""
import html as htmllib
import json
import re
import unicodedata
import urllib.parse

from . import webpage

FORMAT, VERSION = "textfold/web-recipe", 1
LISTS = ("drop", "notes", "content", "figures")
BLOCKISH = {"p", "div", "section", "article", "main", "aside", "nav", "header", "footer", "table", "ul", "ol", "li", "dl",
            "h1", "h2", "h3", "h4", "h5", "h6", "figure", "blockquote", "pre", "form", "details", "d-article", "d-figure"}
BLOCKS = ("p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "blockquote", "dd", "dt", "pre", "caption")
GENERATED = re.compile(r"^(?:css|sc|jsx|emotion|styled|chakra|mui|tw)-[\w-]{4,}$|^_(?=[\w-]*\d)[\w-]{5,}$|^[A-Za-z]+_[\w-]+__[\w-]{5}$"
                       r"|^[A-Z][A-Za-z]+-[A-Za-z0-9]{5,6}$"   # (styled-components: BodyWrapper-cuqCAc)
                       r"|^(?=[\w-]{0,5}[A-Z])(?=[\w-]{0,5}[a-z0-9])[A-Za-z0-9-]{6}_[a-z][\w-]*$")   # (CSS modules: VeVOXq_container)
PAYWALL_CLASS = re.compile(r"paywall|piano|tp-modal|tp-container|metered|subscriber[-_]only|premium[-_]content|regwall|"
                           r"meter[-_]|gate[-_]?keeper|member[-_]only", re.I)
PAYWALL_WORDS = re.compile(r"subscribe to (?:continue|read|keep reading|unlock)|already a (?:subscriber|member)|for paid subscribers|"
                           r"member-only story|to continue reading|sign in to (?:continue|read)|this article is for subscribers|"
                           r"unlock this (?:article|story)|become a (?:paid )?(?:member|subscriber) to", re.I)


# ── the recipe ───────────────────────────────────────────────────────
def recipe_of(data):
    """A recipe checked for its shape: its selectors strings, its notes pairs. Raises ValueError naming what is wrong."""
    r = json.loads(data) if isinstance(data, (str, bytes)) else dict(data)
    if r.get("format", FORMAT) != FORMAT:
        raise ValueError(f"not a {FORMAT}")
    if not isinstance(r.get("article", ""), str):
        raise ValueError("article: one selector")
    for k in LISTS:
        v = r.setdefault(k, [])
        if not isinstance(v, list):
            raise ValueError(f"{k}: a list")
    for k in ("drop", "content", "figures"):
        if not all(isinstance(x, str) and x.strip() for x in r[k]):
            raise ValueError(f"{k}: selectors")
    if not all(isinstance(x, dict) and isinstance(x.get("mark"), str) and isinstance(x.get("note"), str) for x in r["notes"]):
        raise ValueError('notes: [{"mark": selector, "note": selector}]')
    for sel in [r.get("article") or ""] + r["drop"] + r["content"] + r["figures"] + [x[k] for x in r["notes"] for k in ("mark", "note")]:
        if re.search(r"::|:(?:hover|focus|active|visited|checked)\b", sel):
            raise ValueError(f"{sel}: a selector of what the page is, not of how it looks or what the reader does")
    return r


def _tag(page, recipe):
    """The page with the recipe's choices written on its elements (data-tf-*), as an extension would write them on the
    live page: soupsieve reads the selectors. Returns the page and, per selector, how many elements it matched."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page, "lxml")
    seen = {}

    def pick(sel):
        try:
            got = soup.select(sel)
        except Exception as e:   # (soupsieve's SelectorSyntaxError: a selector this code cannot read)
            raise ValueError(f"{sel}: {e}") from None
        seen[sel] = len(got)
        return got
    if recipe.get("article"):
        for el in pick(recipe["article"])[:1]:
            el["data-tf-article"] = "1"
    for sel in recipe["drop"]:
        for el in pick(sel):
            el["data-tf-drop"] = "1"
    for sel in recipe["content"]:
        for el in pick(sel):
            el["data-tf-content"] = "1"
    for sel in recipe["figures"]:
        for el in pick(sel):
            el["data-tf-figure"] = "1"
    k = 0
    for pair in recipe["notes"]:
        marks, notes = pick(pair["mark"]), pick(pair["note"])
        order = {id(el): i for i, el in enumerate(soup.find_all(True))}
        free = sorted(marks, key=lambda el: order.get(id(el), 0))
        for note in sorted(notes, key=lambda el: order.get(id(el), 0)):
            at = order.get(id(note), 0)
            # its mark: the one that names it (aria-describedby, aria-controls, a link to it), else the nearest before it
            nid = note.get("id")
            mark = next((m for m in free if nid and (nid in (m.get("aria-describedby") or "").split()
                                                     or nid in (m.get("aria-controls") or "").split()
                                                     or (m.get("href") or "").endswith("#" + nid))), None)
            mark = mark or next((m for m in reversed(free) if order.get(id(m), 0) < at), None)
            if mark is None:
                continue
            free.remove(mark)
            k += 1
            mark["data-tf-mark"], note["data-tf-note"] = str(k), str(k)
    return str(soup), seen


def seen(page, url):
    """The page as its reader sees it (webpage.visible), its scripts and styles out too: the words on it."""
    from lxml import etree
    root = webpage.visible(page, url)
    etree.strip_elements(root, "script", "style", with_tail=False)
    return root


# ── reading with it ──────────────────────────────────────────────────
def _lazy(root):
    """A picture a script fills in (data-src, srcset, a blank placeholder in src): its own address."""
    for img in root.iter("img"):
        src = img.get("src") or ""
        if src and not src.startswith("data:"):
            continue
        real = img.get("data-src") or img.get("data-lazy-src") or img.get("data-original") or ""
        if not real and (img.get("srcset") or img.get("data-srcset")):
            real = (img.get("srcset") or img.get("data-srcset")).split(",")[0].strip().split(" ")[0]
        if real:
            img.set("src", real)


def _figure(el, url):
    """A figure drawn by the page's scripts: its caption, as the page writes it, a link to the figure on the page."""
    from lxml import html as lhtml
    cap = el.find(".//figcaption")
    if cap is None:   # (a caption in a div of its own: Distill's div.figcaption)
        cap = next((c for c in el.iter() if isinstance(c.tag, str) and "caption" in (c.get("class") or "").lower()), None)
    words = (cap.text_content() if cap is not None else "") or el.get("aria-label") or ""
    if not words.strip():
        head = next((h for h in el.iter("h1", "h2", "h3", "h4", "h5", "h6")), None)
        words = head.text_content() if head is not None else ""
    words = re.sub(r"\s+", " ", words).strip() or "↗"   # (a figure with no words of its own: the link alone)
    href = url.split("#")[0] + ("#" + el.get("id") if el.get("id") else "")
    p = lhtml.fragment_fromstring("<p></p>", create_parent=False)
    a = lhtml.Element("a", href=href)
    a.text = words
    p.append(a)
    return p


def _paragraphs(art):
    """Words with no paragraphs of their own, broken by two line breaks (the HTML of the 1990s, forums, many a
    newsletter): each run between the breaks a paragraph."""
    from lxml import html as lhtml
    BR = object()
    for el in reversed(list(art.iter())):   # (the innermost first: a font that holds the essay, before its cell)
        if not isinstance(el.tag, str) or el.tag in ("pre", "code", "table", "tr", "p", "ul", "ol", "dl"):
            continue
        kids = list(el)
        if not any(a.tag == "br" and b.tag == "br" and not (a.tail or "").strip() for a, b in zip(kids, kids[1:])):
            continue
        items = [el.text or ""]   # the words and elements in order, a line break as BR
        for c in kids:
            tail, c.tail = c.tail or "", None
            el.remove(c)
            items += [BR if c.tag == "br" else c, tail]
        el.text = None
        runs, cur, i = [], [], 0
        while i < len(items):
            if isinstance(items[i], lhtml.HtmlElement) and items[i].tag in BLOCKISH:   # (a quotation among the words: a block of its own)
                runs += [cur, items[i]]
                cur, i = [], i + 1
                continue
            if items[i] is BR:
                j, n = i, 0
                while j < len(items) and (items[j] is BR or (isinstance(items[j], str) and not items[j].strip())):
                    n += items[j] is BR
                    j += 1
                if n >= 2:
                    runs.append(cur)
                    cur, i = [], j
                    continue
                cur.append(lhtml.Element("br"))
            else:
                cur.append(items[i])
            i += 1
        runs.append(cur)
        for run in runs:
            if not isinstance(run, list):
                el.append(run)
                continue
            p = lhtml.Element("p")
            for part in run:
                if isinstance(part, str):
                    last = p[-1] if len(p) else None
                    if last is None:
                        p.text = (p.text or "") + part
                    else:
                        last.tail = (last.tail or "") + part
                else:
                    p.append(part)
            if p.text_content().strip() or p.find(".//img") is not None:
                el.append(p)
        if el.tag not in BLOCKISH and el is not art:
            el.tag = "div"   # (a font or a span that now holds paragraphs is a block: its own cell does not wrap it in one)


def apply(page, url, recipe):
    """The page read with a recipe: (its article's HTML, ready for semantic.normalize; the counts of what was done;
    how many elements each selector matched)."""
    from lxml import html as lhtml
    tagged, seen = _tag(page, recipe)
    root = webpage.visible(tagged, url)
    art = root.xpath('//*[@data-tf-article]')
    if recipe.get("article") and not art:
        raise ValueError(f"article: {recipe['article']} matched nothing the reader can see")
    art = art[0] if art else (root.find(".//body") if root.find(".//body") is not None else root)
    done = {"dropped": 0, "content": 0, "figures": 0, "figures without a caption": 0}
    for el in art.xpath('.//*[@data-tf-drop]'):
        if el.getparent() is not None:
            el.drop_tree()
            done["dropped"] += 1
    for el in art.xpath('.//*[@data-tf-content]'):
        if el.tag in ("button", "label", "summary", "a"):
            el.tag = "span"
        done["content"] += 1
    for el in art.xpath('.//*[@data-tf-figure]'):
        if el.getparent() is None:
            continue
        card = _figure(el, url)
        if card.text_content() == "↗":
            done["figures without a caption"] += 1
        card.tail = el.tail
        el.getparent().replace(el, card)
        done["figures"] += 1
    _lazy(art)
    here = url.split("#")[0]
    for a in art.iter("a"):   # (a link within the page, made whole by visible(): its anchor again, as footnotes need it)
        if (a.get("href") or "").startswith(here + "#"):
            a.set("href", a.get("href")[len(here):])
    for el in art.iter("figure"):   # a figure with nothing drawn in it, its paragraphs text (The Guardian's explainers): a box
        if el.find(".//p") is not None or any(True for _ in el.iter("h2", "h3", "h4")):
            if not any(True for _ in el.iter("img", "picture", "svg", "canvas", "video", "audio", "iframe", "table", "math")):
                el.tag = "div"
    _paragraphs(art)
    webpage._hover_marks(art)
    notes = webpage._notes(art)
    webpage._hidden_out(art)
    done["notes"] = len(notes)
    art.tag = "div"   # (a cell, a font, a custom element: the article's box, read as one)
    html = lhtml.tostring(art, encoding="unicode")
    return webpage._noted(html, notes), done, seen


# ── what an agent is given ───────────────────────────────────────────
def _norm(s):
    s = unicodedata.normalize("NFKC", s).replace("​", "").replace("­", "")
    s = s.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-"}))
    return re.sub(r"\s+", " ", s).strip()


def _sig(el):
    cls = (el.get("class") or "").split()
    s = el.tag + (f"#{el.get('id')}" if el.get("id") else "") + "".join(f".{c}" for c in cls[:4]) + ("…" if len(cls) > 4 else "")
    attrs = [f'{k}="{el.get(k)}"' for k in ("role", "aria-label", "aria-describedby", "itemprop", "data-testid") if el.get(k)]
    attrs += [k for k in el.attrib if k.startswith("data-") and not k.startswith("data-tf-") and k != "data-testid"][:3]
    return s + (f" [{' '.join(attrs)}]"[:140] if attrs else "")


def outline(root, budget=320):
    """The page as a tree for an agent: each element's tag, id, classes and roles, how many words it holds, the first
    words of the small ones; a run of like siblings once, counted; a wrapper of one child joined to it. The smallest
    parts are left out until it fits in about `budget` lines."""
    body = root.find(".//body")
    body = body if body is not None else root
    LOUD = {"figure", "canvas", "svg", "iframe", "video", "audio", "table", "details", "article", "main", "aside", "nav", "footer", "header"}

    def words(el):
        return len(el.text_content().split())

    def walk(el, depth, floor, out):
        kids = [c for c in el if isinstance(c.tag, str) and not c.get("data-tf-css-hidden")]
        chain = [_sig(el)]
        while len(kids) == 1 and not (el.text or "").strip() and kids[0].tag not in LOUD:
            el = kids[0]
            chain.append(_sig(el))
            kids = [c for c in el if isinstance(c.tag, str) and not c.get("data-tf-css-hidden")]
        n = words(el)
        own = _norm(el.text_content())
        flat = not any(isinstance(d.tag, str) and d.tag in BLOCKISH for d in el.iterdescendants())
        leaf = n < floor or not kids or depth > 14 or flat   # (words and inline marks only: one block of text)
        line = "  " * depth + " > ".join(chain) + f"  ({n}w)"
        if leaf and own:
            line += f'  "{own[:70]}{"…" if len(own) > 70 else ""}"'
        out.append(line)
        if leaf:
            return
        i = 0
        while i < len(kids):
            c, j = kids[i], i + 1
            while j < len(kids) and _sig(kids[j]) == _sig(c):
                j += 1
            run = kids[i:j]
            each = [words(x) for x in run]
            if sum(each) or any(x.tag in LOUD for x in run):
                if j - i > 2 and max(each) <= sum(each) / 2:   # (like siblings, none holding most of the words: once)
                    first = next((x for x, n in zip(run, each) if n), c)
                    out.append("  " * (depth + 1) + f"{_sig(c)}  ×{j - i}  ({sum(each)}w in all)"
                               + (f'  first: "{_norm(first.text_content())[:60]}"' if sum(each) else ""))
                else:
                    for x, n in zip(run, each):
                        if n or x.tag in LOUD:
                            walk(x, depth + 1, floor, out)
            i = j
    for floor in (12, 30, 80, 200, 600, 2000):
        out = []
        walk(body, 0, floor, out)
        if len(out) <= budget:
            break
    return "\n".join(out[:budget]) + (f"\n… ({len(out) - budget} lines more)" if len(out) > budget else "")


def signals(page, root):
    """What a recipe may have to say: notes on hover, words in controls, figures drawn by scripts, a paywall's marks,
    how much of the page needs scripts to show, class names a build generates."""
    out = {}
    tips = root.xpath('//*[@role="tooltip"]')
    out["hover notes (role=tooltip)"] = f"{len(tips)}, e.g. {_sig(tips[0])}" if tips else 0
    plain = [el for el in root.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " tooltip ") or contains(@class, "popover") '
                                     'or contains(@class, "footnote") or contains(@class, "sidenote")]') if el.get("role") != "tooltip"]
    out["note-like classes without a role"] = f"{len(plain)}, e.g. {_sig(plain[0])}" if plain else 0
    cells = root.xpath('//td//button[normalize-space()] | //th//button[normalize-space()]')
    out["buttons with words in table cells"] = f"{len(cells)}, e.g. {_sig(cells[0])} in {_sig(next(cells[0].iterancestors('table')))}" if cells else 0
    drawn = {t: len(root.xpath(f"//{t}")) for t in ("canvas", "svg", "iframe", "video", "audio")}
    out["drawn or embedded (canvas, svg, iframe, video, audio)"] = drawn
    figs = [f for f in root.iter("figure") if f.find(".//img") is None]
    out["figures without a picture"] = f"{len(figs)}: " + ", ".join(_sig(f) for f in figs[:6]) if figs else 0
    out["details / tab panels"] = len(root.xpath("//details | //*[@role='tabpanel']"))
    hid = root.xpath("//*[@data-tf-css-hidden]")
    if hid or root.xpath("//*[@data-tf-shadow]"):
        out["a browser's snapshot"] = (f"{len(hid)} elements it did not show (left out; one on a mark becomes a note), "
                                       f"{len(root.xpath('//*[@data-tf-shadow]'))} shadow roots spelled out, "
                                       f"{len(root.xpath('//*[@data-tf-frame]'))} frames put in place")
    # a paywall: what the page says of itself (schema.org), its classes, its words
    free = []
    for s in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page if isinstance(page, str) else "", re.S | re.I):
        try:
            j = json.loads(s)
        except ValueError:
            continue
        stack = j if isinstance(j, list) else [j]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if str(x.get("isAccessibleForFree", "")).lower() in ("false", "0"):
                    part = x.get("hasPart")
                    parts = part if isinstance(part, list) else [part] if part else []
                    free.append("isAccessibleForFree false" + "".join(f", hidden part {p.get('cssSelector')}" for p in parts if isinstance(p, dict)))
                stack += [v for v in x.values() if isinstance(v, (dict, list))]
            elif isinstance(x, list):
                stack += x
    walls = sorted({_sig(el) for el in root.iter() if isinstance(el.tag, str) and PAYWALL_CLASS.search((el.get("class") or "") + " " + (el.get("id") or ""))})
    said = PAYWALL_WORDS.findall(root.text_content())
    out["paywall"] = ("; ".join(free + walls[:4] + [f'the page says "{w}"' for w in said[:2]])) or "none seen"
    raw = page if isinstance(page, str) else page.decode("utf-8", "replace")
    scripts = sum(len(s) for s in re.findall(r"<script[^>]*>(.*?)</script>", raw, re.S | re.I))
    text = len(_norm(root.text_content()))
    marks = [m for m in ("__NEXT_DATA__", "__NUXT__", "__APOLLO_STATE__", "window.__INITIAL_STATE__", 'id="root"', 'id="app"') if m in raw]
    out["scripts"] = f"{scripts:,} characters of script, {text:,} of visible text" + (f"; {', '.join(marks)}" if marks else "")
    classes = [c for el in root.iter() if isinstance(el.tag, str) for c in (el.get("class") or "").split()]
    gen = [c for c in classes if GENERATED.match(c)]
    out["class names that look generated"] = f"{len(gen)} of {len(classes)}, e.g. {', '.join(sorted(set(gen))[:4])}" if gen else 0
    return out


# ── the check ────────────────────────────────────────────────────────
def _where(el, stop):
    for a in el.iterancestors():
        if a is stop:
            break
        cls = [c for c in (a.get("class") or "").split() if not GENERATED.match(c)]
        if a.get("id") or cls or a.tag in ("table", "figure", "aside", "nav", "footer", "header", "section"):
            return a.tag + (f"#{a.get('id')}" if a.get("id") else "") + "".join(f".{c}" for c in cls[:2])
    return el.tag


def _own_text(el):
    """An element's words outside the blocks inside it (an item's words before its nested list)."""
    out = [el.text or ""]
    for ch in el:
        if isinstance(ch.tag, str) and ch.tag not in BLOCKS and not any(isinstance(d.tag, str) and d.tag in BLOCKS for d in ch.iterdescendants()):
            out.append(ch.text_content())
        elif isinstance(ch.tag, str) and ch.tag not in BLOCKS:
            out.append(_own_text(ch))
        out.append(ch.tail or "")
    return "".join(out)


def _blocks(root):
    """The page's blocks of text, each with its own words (a block's words outside the blocks inside it):
    [(element, its words normalized, as the page writes them)]."""
    out = []
    for el in root.iter(*BLOCKS):
        inner = any(isinstance(d.tag, str) and d.tag in BLOCKS for d in el.iterdescendants())
        raw = re.sub(r"\s+", " ", _placed(webpage.NOTE_AT.sub("", _own_text(el) if inner else el.text_content()))).strip()
        t = _norm(raw)
        if t:
            out.append((el, t, raw))
    return out


FORMULA = "∮"   # a formula's place, on the page and in the reading alike: its TeX and its drawing read otherwise
HELD_TEX = re.compile(r"TFMATH\d+Q|TFTEX\d+Q")
# TeX in the text, as the reading draws it (markdown.py: $...$ by Pandoc's rule, no space inside, no digit after)
TEX_TEXT = re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)|" + webpage.DOLLARS.pattern, re.S)


def _placed(t):
    """Text with each formula its place (FORMULA): one held by webpage._tex, and TeX the page writes in its text for a
    script to draw, $$...$$, \\[...\\], \\(...\\) and $...$, which the reading draws (markdown.py)."""
    return TEX_TEXT.sub(f" {FORMULA} ", HELD_TEX.sub(FORMULA, t))
SENTENCE = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])")   # (a sentence of Chinese or Japanese ends with no space after it)
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def _long(t, words):
    """Long enough to be told apart where it stands: so many words, or twice as many CJK characters (a sentence of
    Chinese or Japanese has no spaces to count words by)."""
    return len(t.split()) >= words or len(CJK.findall(t)) >= 2 * words


def _formulas_out(root):
    """Each formula of the page its place alone (FORMULA): one drawn (webpage.formula: MathML, MathJax's or KaTeX's
    drawing, one whose source the browser's button read), and TeX written in its text for a script to draw
    (webpage._tex). MathJax's preview of one goes. (how many)"""
    for el in root.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " MathJax_Preview ")]'):
        webpage._swap(el, "")
    n = 0
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or el.getparent() is None or not webpage._within(el, root):
            continue
        if el.tag.rsplit("}", 1)[-1] in ("math", "mjx-container") or el.get("data-tf-math") is not None \
                or webpage.FORMULA_CLASS.search(el.get("class") or ""):
            webpage._swap(el, f" {FORMULA} ")
            n += 1
    return n + len(webpage._tex(root))


def shown(page, url, recipe=None):
    """The page as its reader reads it, with the recipe's choices on it: its formulas each a place (FORMULA), its notes
    and their marks out of the text (they show on hover), its pictures' alt texts in (a screen reader says them).
    (its tree, its words, normalized)"""
    root = seen(_tag(page, recipe)[0] if recipe else page, url)
    webpage._frames(root)   # (a frame of another page: a figure, its words a still of what it draws)
    _formulas_out(root)
    webpage._hover_marks(root)
    webpage._notes(root)
    webpage._hidden_out(root)
    for m in root.xpath('//*[@role="doc-noteref"] | //*[contains(concat(" ", @class, " "), " footnote-ref ")]'):
        webpage._swap(m, "")   # (an endnote's mark: in the reading, the note on hover)
    for img in root.iter("img"):
        if (img.get("alt") or "").strip():
            img.tail = f" {img.get('alt')} " + (img.tail or "")
    return root, _flat_text(root)


def _flat_text(el):
    return _norm(_placed(webpage.NOTE_AT.sub("", el.text_content())))


MARKS = re.compile(r"\[\d{1,3}\]|↩︎|↩|↩︎?")   # (a footnote's mark and its way back, on the page: on hover in the reading)


def _runs(toks):
    """A paragraph's words between its atoms (formulas, notes, references): [text]."""
    out, cur = [], ""
    for tk in toks:
        if "t" not in tk:
            out.append(cur)
            cur = ""
            continue
        cur += tk["t"] + (" " if tk.get("s") else "")
    return [r for r in out + [cur] if r.strip()]


def _html_words(html):
    """What a block of the reading's HTML shows: its words, a formula its place, a picture its alt text."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for m in soup.find_all("math"):
        m.replace_with(f" {FORMULA} ")
    for img in soup.find_all("img"):
        img.replace_with(f" {img.get('alt') or ''} ")
    return _norm(_placed(soup.get_text(" ")))


def _note_words(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for m in soup.select("sup.ltx_note_mark"):
        m.decompose()
    box = soup.select_one("span.ltx_note_content") or soup
    for m in box.find_all("math"):
        m.replace_with(f" {FORMULA} ")
    return _norm(box.get_text(" "))


def reading(doc):
    """What the reader shows of a reading (parse.parse's), block by block: ([(kind, its words, normalized)], the words
    of its notes, shown on hover). A formula is its place (FORMULA)."""
    atoms, units = doc["atoms"], doc["units"]
    told = []

    def said(toks):
        out = []
        for tk in toks:
            if "t" in tk:
                out.append(tk["t"] + (" " if tk.get("s") else ""))
                continue
            if "x" in tk:
                out.append(f" {FORMULA} ")
                continue
            at = atoms.get(tk.get("a"), {})
            if at.get("kind") == "math":
                out.append(f" {FORMULA} ")
            elif at.get("kind") == "note":
                told.append(_note_words(at.get("html") or ""))
            elif "<img" in (at.get("html") or "") or "<math" in (at.get("html") or ""):
                out.append(" " + _html_words(at["html"]) + " ")
            else:
                out.append(at.get("text") or "")
        return _norm(_placed("".join(out)))
    chunks = {c["id"]: c for c in doc["chunks"]}
    meta = doc.get("meta") or {}
    authors = [a for a in meta.get("authors") or [] if isinstance(a, str)]
    out = [("header", _norm(x)) for x in [meta.get("title") or "", ", ".join(authors), *authors, str(meta.get("date") or "")]]
    for b in doc["blocks"]:
        k = b["k"]
        if k == "h" and b.get("chunk") in chunks:
            c = chunks[b["chunk"]]   # (its number apart, as the reader shows it: "1. Introduction" on the page)
            out += [("heading", _norm(f'{c.get("num") or ""} {c.get("title") or ""}')), ("heading", _norm(c.get("title") or ""))]
        elif k == "u":
            out.append(("paragraph", said(units[b["u"]]["toks"])))
        elif k == "fig":
            out.append(("figure", _html_words(b.get("html") or "")))
            if b.get("cap") in units:   # (its label shown apart, before it: "Fig. 1")
                out.append(("caption", _norm(f'{b.get("label") or ""} {said(units[b["cap"]]["toks"])}')))
        elif k in ("raw", "eq"):   # (a raw block of code or a table is "code": it is not meant to fold)
            html = b.get("html") or ""
            code = k == "raw" and bool(re.match(r"\s*<(pre|table|div[^>]*\bpf-(?:tex|grid|mermaid))", html))
            out.append(("code" if code else k, _html_words(html)))
        elif k == "box" and b.get("title"):
            out.append(("box", _html_words(b["title"])))
    if doc.get("bib"):
        out.append(("references", _html_words(doc["bib"])))
    return [(k, t) for k, t in out if t], [t for t in told if t]


def _to_here(el, url):
    """A block whose words are links to places on this same page, and little else (a number, a dot): the page's
    contents, or a list of its own sentences to go to. Not the article's words: those are read where they are
    (parse.worded)."""
    links = list(el.iter("a"))
    here = url.split("#")[0].rstrip("/")
    if not links or not all((a.get("href") or "").startswith("#") or ("#" in (a.get("href") or "")
                                                                       and a.get("href").split("#")[0].rstrip("/") == here) for a in links):
        return False
    words = lambda t: re.sub(r"[\W\d_]+", "", t)  # noqa: E731
    return words(el.text_content()) == words("".join(a.text_content() for a in links))


def _area(blocks, found, root):
    """The element the reading was taken from, when no recipe names it: the deepest holding nine tenths of the page's
    words the reading has (a page's comments, its recommendations, its menus are outside it)."""
    weight, total = {}, 0
    for (el, t, _), hit in zip(blocks, found):
        if not hit:
            continue
        w = len(t)
        total += w
        for a in el.iterancestors():   # (an element that holds blocks, not a block: one long paragraph is not the article)
            weight[a] = weight.get(a, 0) + w
    best, depth = root, -1
    for a, w in weight.items():
        if w >= 0.9 * total:
            d = sum(1 for _ in a.iterancestors())
            if d > depth:
                best, depth = a, d
    return best


def _states(blocks, url, shown_blocks, told):
    """Each block of the page in a reading (what it shows, reading()): "there", "formulas" (its words there, not its
    formulas), "contents" (links to places on the same page: neither the reading's nor missing from it), "missing".
    And the reading's words, flat."""
    got = " ".join(t for _, t in shown_blocks)
    flat_got = got.replace(" ", "").replace("🖼", "") + "|" + "|".join(t.replace(" ", "") for t in told)
    # its words alone, a formula's place kept: a section's number, a footnote's left as a digit, a dash or quote set
    # otherwise, do not make a block missing; nor its formulas, when they are not there (bare)
    words = lambda x: re.sub(r"[^\w" + FORMULA + r"]+|[\d_]+", "", x)  # noqa: E731
    words_got = words(flat_got.replace("|", " | "))
    bare_got = words_got.replace(FORMULA, "")
    state = []
    for el, t, _ in blocks:
        f = MARKS.sub("", t).replace(" ", "")
        w = words(f)
        lettered = bool(w.replace(FORMULA, ""))   # (a number alone, a table's cell, is compared as it is written)
        if not f.replace(FORMULA, "") or f in flat_got or (lettered and w in words_got):
            state.append("there")
            continue
        if _to_here(el, url):
            state.append("contents")
            continue
        said = [words(MARKS.sub("", x)) for x in SENTENCE.split(t) if _long(x, 4) or len(x) >= 12]
        if len(said) > 1 and sum(x in words_got for x in said) >= 0.8 * len(said):
            state.append("there")   # (a long block, its sentences there: told apart by an atom or a space the reading moved)
        elif lettered and FORMULA in f and w.replace(FORMULA, "") in bare_got:
            state.append("formulas")
        else:
            state.append("missing")
    return state, flat_got


def _page(page, url, recipe=None):
    """The page as the check reads it: (its tree, its words flat, its blocks, the recipe's article or None, how many
    blocks are in the figures the recipe shows as a link)."""
    root, text = shown(page, url, recipe)
    hit = root.xpath('//*[@data-tf-article]') if recipe and recipe.get("article") else []
    carded = 0
    for el in root.xpath('.//*[@data-tf-drop] | .//*[@data-tf-figure]'):
        if el.getparent() is not None and (not hit or webpage._within(el, hit[0])):
            carded += len(_blocks(el)) if el.get("data-tf-figure") else 0
            el.drop_tree()
    return root, text.replace(" ", ""), _blocks(root), (hit[0] if hit else None), carded


def _said(page_parts, url, doc, read, area):
    """One reading (doc; read: what reading() gave of it, and its states) against the page, within area."""
    root, flat, blocks, hit, carded = page_parts
    shown_blocks, told, state, flat_got = read
    got = " ".join(t for _, t in shown_blocks)
    not_seen, twice = [], []
    flat_bare = flat.replace(FORMULA, "")   # (a formula the reading lost runs the words around it together: said apart)
    for u in doc["units"].values():
        for run in (_placed(r.replace("🖼", " ")) for r in _runs(u["toks"])):   # (the words between its formulas and other atoms)
            for bit in run.split(FORMULA):   # (TeX in its words: a formula, in its place on the page)
                for piece in SENTENCE.split(_norm(bit)):
                    if _long(piece, 5) and piece.replace(" ", "") not in flat_bare:
                        not_seen.append(piece)
    for kind, t in shown_blocks:
        if kind != "paragraph":
            continue
        head = " ".join(t.replace("🖼", " ").split()[:10])
        if len(t.split()) >= 6 and flat.count(head.replace(" ", "")) != 1:
            twice.append(head)
    missing, elsewhere, moved, short, in_order = {}, {}, [], [], []
    page_blocks, article, lacked = 0, 0, 0   # (the article's text, and how much of it the reading lacks, in characters)
    for (el, t, raw), s in zip(blocks, state):
        inside = el is area or webpage._within(el, area)
        if inside and (_long(t, 8) or len(t) >= 24):
            page_blocks += 1
        if inside and s != "contents":
            article += len(t)
            lacked += len(t) if s == "missing" else 0
        if s == "formulas" and inside:
            moved.append(raw)
        elif s == "missing" and inside and not (_long(t, 4) or len(t) >= 20):
            short.append(t)   # (a label: a chart's, a logo's name, a button's; or a short item of a list)
        elif s == "missing":
            (missing if inside else elsewhere).setdefault(_where(el, area if inside else root), []).append(t)
            if inside:
                in_order.append(raw)
    in_area = _flat_text(area).replace(" ", "")
    outside = [t for kind, t in shown_blocks if kind in ("paragraph", "raw") and t.replace(" ", "") not in in_area
               and t.replace(" ", "") in flat and len(t) >= 2]
    pictures = len({img.get("src") for img in area.iter("img") if webpage._real(img)})
    kept = {htmllib.unescape(h) for b in doc["blocks"] if b.get("k") == "fig"
            for h in re.findall(r'class="pf-embed[^"]*" href="([^"]+)"', b.get("html") or "")}
    framed = {e["src"]: e["title"] for e in map(webpage._framed, area.iter("iframe")) if e}
    lost_frames = [title or src for src, title in framed.items() if src not in kept]
    text = sum(len(t) for k, t in shown_blocks if k in ("paragraph", "raw", "caption"))
    unfolded = sum(len(t) for k, t in shown_blocks if k == "raw") / text if text else 0.0
    group = lambda d, n: {k: (len(v), v[:3]) for k, v in sorted(d.items(), key=lambda kv: -len(kv[1]))[:n]}  # noqa: E731
    return {"words": len(got.split()), "paragraphs": len(doc["units"]),
            "notes": sum(1 for a in doc["atoms"].values() if a.get("kind") == "note"),
            "article": "the recipe's" if hit is not None else _sig(area),
            "not on the page": not_seen[:8], "not on the page (count)": len(not_seen),
            "missing": group(missing, 12), "missing (blocks)": sum(len(v) for v in missing.values()),
            "missing (in order)": in_order[:12], "missing (share)": round(lacked / article, 3) if article else 0.0,
            "missing, short": len(short), "missing, short (e.g.)": short[:5],
            "formulas not there": len(moved), "formulas not there (e.g.)": moved[:12],
            "from outside the article": len(outside), "from outside the article (e.g.)": outside[:5],
            "not in the reading, outside the article": group(elsewhere, 5),
            "formulas": {"page": in_area.count(FORMULA), "reading": flat_got.split("|")[0].count(FORMULA)},
            "pictures": {"page": pictures, "reading": len(doc.get("images") or [])},
            "interactive figures": {"page": len(framed), "reading": len(framed) - len(lost_frames)},
            "interactive figures not there": lost_frames,
            "in figures shown as a link": carded, "not folding": round(unfolded, 2),
            "not found again by its words": len(twice), "not found again (e.g.)": twice[:3], "page blocks": page_blocks}


def _checked(page, url, docs, recipe=None):
    """Readings of one page (docs, parsed) checked against it, each within the same article: the recipe's, or the
    element the readings' words are in, all of them together (a reading that took a part of the article is measured
    against the whole of it, not against that part). [what check says], in the order of docs."""
    parts = _page(page, url, recipe)
    root, _, blocks, hit, _ = parts
    reads = []
    for doc in docs:
        shown_blocks, told = reading(doc)
        reads.append((shown_blocks, told, *_states(blocks, url, shown_blocks, told)))
    area = hit if hit is not None else _area(blocks, [any(r[2][i] in ("there", "formulas") for r in reads) for i in range(len(blocks))], root)
    return [_said(parts, url, doc, r, area) for doc, r in zip(docs, reads)]


def check(page, url, html, recipe=None, doc=None):
    """The reading (html, normalized; or doc, as parse.parse gives it) against the page as its reader sees it, as the
    reader shows it: words taken that the page does not show (there should be none); the page's blocks the reading
    lacks, within its article (the recipe's, or the element the reading was taken from), grouped by where they are,
    and the blocks whose words are there but not their formulas, all of them or in their places; the reading's
    paragraphs taken from outside the article (a site's furniture, most often: a byline, a count of likes); the
    formulas and pictures of each; and paragraphs a reader's browser could not find again by their first words. A
    formula is compared by its place, not its TeX (a page draws it, the reading keeps its source)."""
    if doc is None:
        from . import semantic
        from .parse import parse
        doc = parse(semantic.normalize(html, sizes=True), {"id": "web-check"})
    return _checked(page, url, [doc], recipe)[0]


def cost(c):
    """How far a reading is from its page, by what check says of it: blocks of the article it lacks, those whose
    formulas are not there, the article's formulas, pictures and interactive figures it lacks (a display formula is a
    block of none but it),
    places where it has words the page does not show, the article's blocks its text that does not fold stands for; a
    short label it lacks counts a quarter, a block it took from outside the article half (a byline is less than a
    paragraph lost)."""
    return (c["missing (blocks)"] + c["formulas not there"] + max(0, c["formulas"]["page"] - c["formulas"]["reading"])
            + max(0, c["pictures"]["page"] - c["pictures"]["reading"]) + len(c["interactive figures not there"])
            + c["not on the page (count)"] + c["not folding"] * c["page blocks"]
            + 0.25 * c["missing, short"] + 0.5 * c["from outside the article"])


def choose(page, url, docs):
    """Of the readings of a page ({name: doc}, in the order to take when they are as far from it), the one that differs
    least from it, all checked against the same article: (its name, {name: what check says})."""
    names = list(docs)
    said = dict(zip(names, _checked(page, url, [docs[n] for n in names])))
    return min(names, key=lambda n: (cost(said[n]), names.index(n))), said


def report(c):
    run = (f"   <- the page has {c['page blocks']} blocks of text here: paragraphs run together?"
           if c.get("page blocks", 0) >= 8 and c["paragraphs"] < c["page blocks"] / 2 else "")
    lines = [f"words: {c['words']:,}  paragraphs: {c['paragraphs']}  notes: {c['notes']}" + run,
             f"the article: {c['article']}",
             f"words the page does not show: {c['not on the page (count)']}" + "".join(f'\n    "{p[:110]}"' for p in c["not on the page"]),
             f"blocks of the article not in the reading: {c['missing (blocks)']}"
             + (f" (and {c['in figures shown as a link']} in the figures the recipe shows as a link)" if c.get("in figures shown as a link") else "")]
    for where, (n, eg) in c["missing"].items():
        lines.append(f"    {where}: {n}" + "".join(f'\n        "{e[:90]}"' for e in eg))
    lines.append(f"short ones (a label, a short item): {c['missing, short']}" + "".join(f'  "{e[:30]}"' for e in c["missing, short (e.g.)"]))
    lines.append(f"blocks whose words are there, not their formulas: {c['formulas not there']}"
                 + "".join(f'\n    "{e[:90]}"' for e in c["formulas not there (e.g.)"]))
    lines.append(f"of the reading's text, in blocks that do not fold: {c['not folding']:.0%}")
    lines.append(f"formulas: {c['formulas']['page']} on the page, {c['formulas']['reading']} in the reading;  "
                 f"pictures: {c['pictures']['page']} on the page, {c['pictures']['reading']} in the reading;  "
                 f"interactive figures: {c['interactive figures']['page']} on the page, {c['interactive figures']['reading']} in the reading"
                 + "".join(f'\n    not there: "{e[:90]}"' for e in c["interactive figures not there"]))
    lines.append(f"paragraphs of the reading from outside the article: {c['from outside the article']}"
                 + "".join(f'\n    "{e[:90]}"' for e in c["from outside the article (e.g.)"]))
    if c["not in the reading, outside the article"]:
        lines.append("not in the reading, outside the article (a site's furniture, or what the reading missed):")
        for where, (n, eg) in c["not in the reading, outside the article"].items():
            lines.append(f"    {where}: {n}" + "".join(f'\n        "{e[:90]}"' for e in eg[:2]))
    lines.append(f"paragraphs a browser could not find again by their first words alone: {c['not found again by its words']}"
                 + "".join(f'\n    "{e[:90]}"' for e in c["not found again (e.g.)"]))
    return "\n".join(lines)


_CHECKED = {}   # pid: (the times of its files, what stored said): said again when the page, its reading or its meta change


def stored(pid):
    """A page kept with its snapshot (snapshot.html: one sent from the browser) checked against its reading as the
    reader shows it, for the reader to say (/api/check): what share of the article's text it lacks (missing_share,
    0 to 1); how much of its text is in blocks that do not fold
    (not_folding, 0 to 1); how many blocks of the article the reading lacks (missing;
    short: a label, a short item), whose formulas are not there (formulas), what the reading has that the page does
    not show (not_on_page), what it took from outside the article (outside), each with examples as the page writes
    them (…_eg); the formulas on the page and in the reading; the interactive figures of the article not in the
    reading (frames, their names: frames_eg) and on the page (fr_page); edited: its text was edited in TextFold, and the edits
    differ from the page too. None: no such page (any other document)."""
    from . import store
    if not store.valid_id(pid):
        return None
    d = store.pdir(pid)
    files = [d / f for f in ("snapshot.html", "source.html", "meta.json")]
    if not all(f.exists() for f in files):
        return None
    key = tuple(f.stat().st_mtime_ns for f in files)
    if pid in _CHECKED and _CHECKED[pid][0] == key:
        return _CHECKED[pid][1]
    meta = json.loads(store.read_text(d / "meta.json"))
    url = meta.get("abs_url") or (meta.get("source") or {}).get("url") or ""
    c = check(store.read_text(d / "snapshot.html"), url, None, doc=store.parsed(pid))
    got = {"article": c["article"], "missing": c["missing (blocks)"], "missing_eg": c["missing (in order)"],
           "short": c["missing, short"], "short_eg": c["missing, short (e.g.)"],
           "formulas": c["formulas not there"], "formulas_eg": c["formulas not there (e.g.)"],
           "not_on_page": c["not on the page (count)"], "not_on_page_eg": c["not on the page"],
           "outside": c["from outside the article"], "outside_eg": c["from outside the article (e.g.)"],
           "f_page": c["formulas"]["page"], "f_reading": c["formulas"]["reading"], "edited": bool(meta.get("replaced")),
           "not_folding": c["not folding"], "missing_share": c["missing (share)"],
           "frames": len(c["interactive figures not there"]), "frames_eg": c["interactive figures not there"][:5],
           "fr_page": c["interactive figures"]["page"]}
    _CHECKED[pid] = (key, got)
    return got


def snapshot(url, path=None, rendered=False):
    """The page: fetched as TextFold fetches it (no scripts run), or a file saved before, as fetched or as a browser
    rendered it. (text, its address, how it was had)."""
    if path:
        from pathlib import Path
        return Path(path).read_text(encoding="utf-8", errors="replace"), url, "rendered" if rendered else "saved"
    data, final, charset = webpage._get(url)
    return data.decode(charset or "utf-8", "replace"), final, "fetched"


def brief(url, page, final, how):
    """What an agent is given, as text: the snapshot, the page's outline, its signals, and the check of TextFold's
    reading of it with no recipe."""
    root = seen(page, final)
    out = [f"# {final}", f"snapshot: {how}; {len(page):,} characters", "", "## Signals"]
    out += [f"- {k}: {v}" for k, v in signals(page, root).items()]
    webpage._hidden_out(root)   # (a browser's snapshot: the outline is of what it showed)
    out += ["", "## Outline (what the reader can see)", outline(root), "", "## TextFold's reading with no recipe"]
    try:
        _, html, _ = webpage.read(page, final)
        out.append(report(check(page, final, html)))
    except ValueError as e:
        out.append(f"none: {e} (too few words: the page may show its text with scripts, or behind a sign-in)")
    return "\n".join(out)


def tried(page, final, recipe):
    """The page read with a recipe, and the check of it, as text."""
    html, done, matched = apply(page, final, recipe)
    wall = signals(page, seen(page, final))["paywall"]
    out = ([f"NOTE: the page marks a paywall ({wall[:160]}). A subscriber's snapshot holds the whole article; one taken "
            "without access may hold part of it: then say so, and never ask for what it does not show.", ""] if wall != "none seen" else [])
    out += ["## Selectors", *[f"- {s}: {n}" + ("   <- matched nothing" if not n else "") for s, n in matched.items()],
            "", "## Done", *[f"- {k}: {v}" for k, v in done.items()], "", "## The reading, checked",
            report(check(page, final, html, recipe))]
    return "\n".join(out), html


def host_of(url):
    return re.sub(r"^www\.", "", (urllib.parse.urlparse(url).hostname or "").lower())
