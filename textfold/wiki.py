"""Wikipedia articles, in any language edition, as semantic HTML for semantic.normalize.

  https://en.wikipedia.org/wiki/Bayes%27_theorem · https://zh.wikipedia.org/wiki/贝叶斯定理 · en.m.wikipedia.org/wiki/…

The title is resolved by the Action API (redirects and spelling followed to the article itself), the article's HTML
(Parsoid's) read from the MediaWiki REST API; both answer any client, with a User-Agent that says who asks.

What a page shows around the article is left out: infoboxes, navigation boxes and sidebars, hatnotes ("For other
uses ..."), maintenance notices, small inline icons, and the sections that only list links ("See also", "External
links", "Further reading"). Links to other articles are read as their words, so the model can rephrase and translate
them. Formulas keep their MathML, with their TeX as alttext; reference marks [1] point into one bibliography.

Stored as papers/wiki-<language>-<page id>/, with the revision read and the day it was read (the snapshot: the article
may have changed since); under CC BY-SA 4.0, as Wikipedia's text is.
"""
import json
import re
import time
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

from . import local, store
from .fetch import UA

URL = re.compile(r"https?://([a-z][a-z-]{1,15})\.(?:m\.)?wikipedia\.org/(?:wiki|zh-[a-z]+|zh)/([^?#\s]+)", re.I)
URL_Q = re.compile(r"https?://([a-z][a-z-]{1,15})\.(?:m\.)?wikipedia\.org/w/index\.php\?(?:[^#\s]*&)?title=([^&#\s]+)", re.I)
DROP = (".mw-editsection, .hatnote, .navbox, .vertical-navbox, .sidebar, .infobox, .metadata, .ambox, .ombox, .tmbox, "
        ".noprint, .mw-empty-elt, .shortdescription, .sistersitebox, .portalbox, .navigation-not-searchable, .gallery, "
        ".mw-kartographer-maplink, .mwe-math-fallback-image-inline, .mwe-math-fallback-image-display, .mw-cite-backlink, "
        "style, link, meta, [role=navigation], [role=note]")
# sections that only list links elsewhere: their heading in the larger editions
LINK_SECTIONS = re.compile(
    r"^(see also|external links|further reading|other websites|related pages|"
    r"参见|参看|相关条目|外部链接|延伸阅读|扩展阅读|參見|參看|相關條目|外部連結|延伸閱讀|"
    r"関連項目|外部リンク|関連文献|관련 항목|외부 링크|"
    r"siehe auch|weblinks|voir aussi|liens externes|véase también|enlaces externos|ver também|ligações externas|"
    r"voci correlate|collegamenti esterni|см\. также|ссылки)$", re.I)
REF_SECTIONS = re.compile(
    r"^(references|notes|citations|footnotes|sources|notes and references|bibliography|works cited|cited sources|"
    r"参考文献|参考资料|注释|脚注|参考來源|參考文獻|參考資料|註釋|腳註|注釈|出典|각주|참고 문헌|"
    r"einzelnachweise|anmerkungen|literatur|notes et références|références|referencias|notas|"
    r"referências|note|bibliografia|примечания|литература)$", re.I)


def ref_of(ref):
    """(language edition, title) for a Wikipedia article's link; (None, None) for anything else."""
    m = URL.search(ref or "") or URL_Q.search(ref or "")
    if not m:
        return None, None
    return m.group(1).lower(), urllib.parse.unquote(m.group(2)).replace("_", " ")


def _get(url, timeout=60, variant=None):
    """variant: the script a Chinese article is converted to (zh-cn). Its source mixes simplified and traditional and
    marks words that must not be converted, which Parsoid's HTML leaves empty until the article is converted."""
    headers = {**UA, **({"Accept-Language": variant} if variant else {})}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def lookup(lang, title=None, pageid=None):
    """(page id, title, revision) of an article, its redirects followed; ValueError("no-article") when there is none."""
    q = {"action": "query", "redirects": 1, "prop": "info", "format": "json", "formatversion": 2}
    q.update({"pageids": pageid} if pageid else {"titles": title})
    pages = json.loads(_get(f"https://{lang}.wikipedia.org/w/api.php?{urllib.parse.urlencode(q)}", 30))["query"].get("pages", [])
    p = pages[0] if pages else {}
    if not p or p.get("missing") or p.get("invalid") or p.get("ns") != 0:
        raise ValueError("no-article")
    return p["pageid"], p["title"], p.get("lastrevid"), p.get("touched", "")


def pid_of(lang, pageid):
    return f"wiki-{lang}-{pageid}"


def _tex(math):
    """A formula's TeX without the {\\displaystyle ...} that Wikipedia wraps it in."""
    alt = (math.get("alttext") or "").strip()
    m = re.fullmatch(r"\{\\(?:displaystyle|textstyle)\s*(.*)\}", alt, re.S)
    return (m.group(1) if m else alt).strip()


def _best(img):
    """The largest image the srcset offers, as an absolute URL."""
    src = img.get("src", "")
    cands = [(float(m.group(2) or 1), m.group(1)) for m in re.finditer(r"(\S+)\s+([\d.]+)x", img.get("srcset", ""))]
    if cands:
        src = max(cands)[1]
    return "https:" + src if src.startswith("//") else src


def to_html(parsoid):
    """Parsoid's HTML of an article as semantic HTML."""
    soup = BeautifulSoup(parsoid, "lxml")
    body = soup.body or soup
    for el in body.select(DROP):
        el.decompose()
    for t in body.find_all("table"):
        if "wikitable" not in t.get("class", []) and not t.find_parent("table"):
            t.decompose()   # layout and navigation tables; a data table is a wikitable
    # sections that only list links, and the reference lists (gathered into one bibliography at the end)
    refs = []
    for ol in body.select("ol.mw-references, ol.references"):
        refs.extend(ol.find_all("li", recursive=False))
        ol.extract()
    for h in body.find_all(re.compile(r"^h[2-6]$")):
        name = h.get_text(" ", strip=True)
        if LINK_SECTIONS.match(name) or REF_SECTIONS.match(name):
            sec = h.find_parent("section")
            if REF_SECTIONS.match(name) and sec is not None:   # a list of works the notes cite (Bibliography, Sources)
                refs.extend(li.extract() for li in sec.select("ul > li, ol > li"))
            if sec is not None and sec.find(re.compile(r"^h[2-6]$")) is h:
                sec.decompose()
            else:
                h.decompose()
    # formulas: the MathML (TeX as alttext) in place of the whole math element
    for el in body.select("span.mwe-math-element"):
        m = el.find("math")
        if m is None:
            el.decompose()
            continue
        m["alttext"] = _tex(m)
        if "mwe-math-element-block" in el.get("class", []):
            m["display"] = "block"
        m.attrs.pop("class", None)
        el.replace_with(m)
    # a formula alone in its line (indented with ":" in wikitext) is a display formula
    for m in body.find_all("math"):
        rest = "".join(c.get_text("") if hasattr(c, "get_text") else str(c) for c in m.parent.contents if c is not m)
        if m.parent.name in ("p", "dd", "div", "section", "body") and not re.sub(r"[\s,.;:]", "", rest):
            m["display"] = "block"
            if m.parent.name == "p":
                m.parent.unwrap()
    # indentation (": text" in wikitext) is a definition list without terms: its content, unindented
    for dl in body.find_all("dl"):
        if not dl.find("dt"):
            for dd in dl.find_all("dd", recursive=False):
                dd.unwrap()
            dl.unwrap()
    for p in body.find_all("p"):   # an anchor left alone (<p><span id="Flash_cards"></span></p>)
        if not p.get_text(strip=True) and not p.find(["img", "math"]):
            p.decompose()
    # reference marks
    for sup in body.select("sup.mw-ref, sup.reference"):
        a = sup.find("a", href=True)
        if a is None:
            sup.decompose()
            continue
        target = a["href"].split("#", 1)[-1]
        cite = soup.new_tag("cite", attrs={"class": "ltx_cite"})
        link = soup.new_tag("a", href=f"#{target}")
        link.string = a.get_text("").replace(" ", "")
        cite.append(link)
        sup.replace_with(cite)
    # links: to another article, its words; elsewhere on the web, kept
    for a in body.find_all("a"):
        rel = " ".join(a.get("rel", []))
        href = a.get("href", "")
        if "mw:ExtLink" in rel and re.match(r"(https?:)?//", href):
            a["href"] = "https:" + href if href.startswith("//") else href
        elif not href.startswith("#") or a.find_parent("cite") is None:
            a.unwrap()
    # pictures: a figure's image at its largest; small inline pictures (flags, icons) left out
    for img in body.find_all("img"):
        if img.find_parent("figure") is None:
            img.decompose()
        else:
            img["src"] = _best(img)
            img.attrs.pop("srcset", None)
    out = body.decode_contents()
    if refs:
        items = []
        for li in refs:
            text = li.select_one(".mw-reference-text, .reference-text") or li
            items.append(f'<li id="{li.get("id", "")}">{text.decode_contents()}</li>')
        out += "<h2>References</h2><ul>" + "".join(items) + "</ul>"
    return out


def fetch(pid, progress=None):
    """Store a Wikipedia article (once). Its id: wiki-<language>-<page id>."""
    if store.readable(pid):
        return pid
    lang, pageid = re.fullmatch(r"wiki-([a-z][a-z-]*)-(\d+)", pid).groups()
    pageid, title, rev, _ = lookup(lang, pageid=int(pageid))
    key = urllib.parse.quote(title.replace(" ", "_"), safe="")
    parsoid = _get(f"https://{lang}.wikipedia.org/w/rest.php/v1/page/{key}/html",
                   variant="zh-cn" if lang == "zh" else None).decode("utf-8", "replace")
    stamp = re.search(r'<meta property="dc:modified" content="([^"]+)"', parsoid)
    meta = {"id": pid, "version": f"rev {rev}" if rev else "", "title": title, "authors": ["Wikipedia contributors"],
            "date": stamp.group(1)[:10] if stamp else "", "lang": lang, "snapshot": time.strftime("%Y-%m-%d"),
            "abs_url": f"https://{lang}.wikipedia.org/w/index.php?title={key}&oldid={rev}" if rev else f"https://{lang}.wikipedia.org/wiki/{key}",
            "html_url": "", "license": "https://creativecommons.org/licenses/by-sa/4.0/",
            "source": {"kind": "wikipedia", "name": title, "revision": rev}, "label": f"{lang}.wikipedia.org"}
    html = to_html(parsoid)
    return local.store_doc(pid, html, meta, "wiki_html", parsoid, lambda folder: local.Images(folder, None).resolve)
