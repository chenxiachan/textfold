"""A static gallery of papers to read in TextFold, for a free host (GitHub Pages, Cloudflare Pages).

    python3 tools/gallery.py --make pi/deepseek/deepseek-v4-flash    generates what the list still lacks, then builds
    python3 tools/gallery.py                                         builds out/gallery/site: index.html, <id>/index.html

It needs pikepdf besides the reader's own requirements: pip install -r tools/requirements-gallery.txt

The list is tools/gallery.json: one paper per field, in order, each with its field and a line on why it is worth
reading, in every language of the gallery, and a "title" where the model's translation of the title reads badly.
The gallery keeps its papers apart from the reader's own (out/gallery/papers); a paper already in the reader's
library starts from there, without its notes.

Only papers whose license lets anyone share a derivative go in: Creative Commons BY, BY-SA, BY-NC, BY-NC-SA and CC0
(a free gallery is non-commercial). NoDerivatives licenses and arXiv's default license stay out: that license lets
arXiv distribute a paper, nobody else. "consent": true in the list adds a paper anyway, for a paper whose authors agree
(your own). Each page is the self-contained static reader, which names the paper's license under its title; the
gallery opens it at the Topic level, the map of its sections.
"""
import argparse
import html
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from textfold import build, ladder, langs, pdf, store, translate  # noqa: E402
from textfold.tokens import plain  # noqa: E402

LIST = Path(__file__).with_suffix(".json")
FIXES = ROOT / "out" / "gallery" / "fixes"   # <id>.json: proofreading's corrections; kept with the gallery, as they hold papers' text
OPEN = ("/licenses/by/", "/licenses/by-sa/", "/licenses/by-nc/", "/licenses/by-nc-sa/", "/publicdomain/zero/")
REPO = "https://github.com/chenxiachan/textfold"
SITE = "https://chenxiachan.github.io/textfold/"   # where the gallery is served, at gallery/ (site/build.py --gallery)
ISSUES = "https://github.com/chenxiachan/paperfold-gallery/issues"
AUTHOR = "https://chenxiachan.github.io/"


def shareable(lic):
    return bool(lic) and "creativecommons.org" in lic and any(k in lic for k in OPEN)


def license_name(lic):
    if "publicdomain/zero" in lic:
        return "CC0"
    part = lic.split("/licenses/")[-1].strip("/").split("/")
    return f"CC {part[0].upper()} {part[1]}" if len(part) > 1 else "CC " + part[0].upper()


def seed(pid, lib):
    """A paper the reader already has: its source, layers and model replies, never its notes."""
    src, dst = lib / "papers" / pid, store.pdir(pid)
    if dst.exists() or not (src / "meta.json").exists():
        return
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("notes.json", "notes.*.json"))
    print(f"  · {pid}: from the library")


def missing(pid, want):
    have = ["en"] if store.read(pid, "en") else []
    return [l for l in want if l not in have + store.languages(pid)]


def make(entries, want, model, at_once):
    """The command line's generation (python -m textfold), one process per paper, with the gallery's papers folder."""
    logs = store.DATA / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TEXTFOLD_DATA": str(store.DATA), "PYTHONUNBUFFERED": "1"}

    def one(pid):
        todo = missing(pid, want)
        if not todo:
            return pid, "ready"
        cmd = [sys.executable, "-m", "textfold", pid, "--model", model] + [x for l in todo if l != "en" for x in ("--lang", l)]
        with open(logs / f"{pid}.log", "a", encoding="utf-8") as log:
            r = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        return pid, ("made " + " ".join(todo)) if r.returncode == 0 else f"failed ({logs / f'{pid}.log'})"

    with ThreadPoolExecutor(at_once) as ex:
        for pid, how in ex.map(one, [e["id"] for e in entries]):
            print(f"  {pid}: {how}")


def text_of(toks, atoms):
    """Tokens back to the text a reply had: formulas and citations as their markers ⟦a12 …⟧."""
    s = ""
    for k, tk in enumerate(toks):
        piece = translate.marked([tk], atoms) if "a" in tk else f"${tk['x']}$" if "x" in tk else tk.get("t", "")
        s += piece + (" " if tk.get("s") and k + 1 < len(toks) else "")
    return s.strip()


def lines(lad, atoms):
    """A unit's short levels as text: takeaway, reason, topic."""
    b, tn = lad["brief"], lad["tn"]
    return {"takeaway": text_of(b[:tn], atoms), "reason": text_of(b[tn + 1:-1], atoms) if len(b) > tn + 1 else "",
            "topic": plain(lad["topic"], atoms)}   # as the prompts wrote it: a formula as $latex$, which the topic finds


def section_lines(lad, atoms):
    if not lad:
        return {"takeaway": "", "topic": ""}
    return {"takeaway": text_of(lad["take"], atoms), "topic": plain([lad["take"][i] for i in lad["topic"]], atoms)}


def review(doc, want):
    """The paper for a proofreader: each section and unit, the English sentences, and every language's text."""
    A, out = doc["atoms"], []
    for c in doc["chunks"]:
        if not c["units"]:
            continue
        sec = {"id": c["id"], "title": f"{c['num']} {c['title']}".strip(), "en": section_lines(c.get("lad"), A), "units": []}
        for l in want[1:]:
            t = c.get("tr", {}).get(l) or {}
            sec[l] = {"title": t.get("title", ""), **section_lines(t.get("lad"), A)}
        for uid in c["units"]:
            u = doc["units"][uid]
            item = {"id": uid, "source": [text_of(u["toks"][a:b], A) for a, b in u["sents"]],
                    "en": {"role": u["lad"].get("role"), **lines(u["lad"], A)}}
            for l in want[1:]:
                z = u.get("tr", {}).get(l)
                if z:
                    item[l] = {"sentences": [text_of(z["toks"][a:b], A) for a, b in z["sents"]], **lines(z["lad"], A)}
            sec["units"].append(item)
        out.append(sec)
    return {"id": doc["meta"]["id"], "title": doc["meta"]["title"], "sections": out}


def corrected(doc, fixes, want):
    """Proofreading's corrections, put through the same assembly as the model's replies (ladder and translate)."""
    A, said = doc["atoms"], []
    chunks = {c["id"]: c for c in doc["chunks"]}
    for uid, f in fixes.get("units", {}).items():
        u = doc["units"][uid]
        if "en" in f:
            o = {**lines(u["lad"], A), "role": u["lad"].get("role"), "evidence": u["lad"]["ev"], **f["en"]}
            u["lad"] = ladder.assemble(u, o, A, u["lad"].get("model"))
        for l in want[1:]:
            if l not in f:
                continue
            z = u["tr"][l]
            o = {"sentences": [text_of(z["toks"][a:b], A) for a, b in z["sents"]], **lines(z["lad"], A)}
            o.update({k: v for k, v in f[l].items() if k != "sentences"})
            for i, t in (f[l].get("sentences") or {}).items():
                assert 0 <= int(i) < len(o["sentences"]), f"{uid} {l}: no sentence {i}"
                o["sentences"][int(i)] = t
            u["tr"][l] = translate.assemble(u, o, A, l, lambda w: said.append(f"{l} {w}"))
    for cid, f in fixes.get("sections", {}).items():
        c = chunks[cid]
        if "en" in f:
            c["lad"] = ladder.section_ladder({**section_lines(c.get("lad"), A), **f["en"]}, 3, doc, c)
        for l in want[1:]:
            if l in f:
                t = c["tr"][l]
                sec = {**section_lines(t.get("lad"), A), **{k: v for k, v in f[l].items() if k != "title"}}
                c["tr"][l] = {"title": f[l].get("title", t["title"]), "lad": ladder.section_ladder(sec, langs.budget(l)["topic"], doc, c)}
    return said


SLIM_ABOVE, SIDE = 4_000_000, 1600


def slim(src, dst):
    """The PDF a visitor downloads to see the original pages. A large one gets its large pictures as JPEGs of at most
    1600 pixels a side (GraphCast's 40 MB come to 10), through pikepdf; its fonts and text stay exactly as they were,
    since PDF.js draws them as in the app and they are what finds each paragraph on its page. (Ghostscript makes it
    smaller, but rewrites the fonts, and PDF.js then leaves out their ligatures: "signi cant".) Without pikepdf a large
    PDF stops the build: put online whole, it would cost every visitor its full size, and the gallery's history for good."""
    if src.stat().st_size <= SLIM_ABOVE:
        shutil.copyfile(src, dst)
        return
    try:
        import pikepdf
    except ImportError:
        sys.exit(f"{src.name} is {src.stat().st_size / 1e6:.0f} MB, and pikepdf is not installed to make it smaller: "
                 "pip install -r tools/requirements-gallery.txt")
    import io
    from pikepdf import Name, PdfImage
    doc = pikepdf.open(src)
    images = [o for o in doc.objects if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == "/Image"]
    masks = {o.SMask.objgen for o in images if o.get("/SMask") is not None}
    for raw in images:   # every picture, also those inside a figure's own drawing, not only the pages' own
        if raw.objgen in masks or raw.get("/ImageMask") or raw.get("/Mask") is not None:
            continue   # a mask stays as it is, and so does a picture cut out by a colour key
        size = len(raw.read_raw_bytes())
        if size < 100_000:
            continue
        try:
            im = PdfImage(raw).as_pil_image()
        except Exception:
            continue
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((SIDE, SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80, optimize=True)
        if buf.tell() >= size * 0.8:
            continue
        raw.write(buf.getvalue(), filter=Name.DCTDecode)
        raw.Width, raw.Height, raw.BitsPerComponent = im.width, im.height, 8
        raw.ColorSpace = Name.DeviceRGB if im.mode == "RGB" else Name.DeviceGray
        for k in ("/DecodeParms", "/Decode", "/Intent"):
            if k in raw:
                del raw[k]
    tmp = dst.with_suffix(".part")
    doc.save(tmp, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    if tmp.stat().st_size < src.stat().st_size:
        tmp.replace(dst)
    else:
        tmp.unlink()
        shutil.copyfile(src, dst)


def main():
    ap = argparse.ArgumentParser(prog="gallery")
    ap.add_argument("--make", metavar="MODEL", help="generate the papers and languages the list still lacks with this model")
    ap.add_argument("--at-once", type=int, default=3, help="papers generated at the same time (with --make)")
    ap.add_argument("--data", default=str(ROOT / "out" / "gallery"), help="the gallery's own papers folder and site")
    ap.add_argument("--review", action="store_true", help="also write each paper's text for proofreading, in <data>/review")
    ap.add_argument("--unproofread", action="store_true", help="build papers that have no corrections yet (a first look, never to publish)")
    a = ap.parse_args()
    spec = json.loads(LIST.read_text(encoding="utf-8"))
    want = spec["langs"]
    lib = store.DATA
    store.DATA = Path(a.data)   # every read and write of the store goes to the gallery's folder
    global FIXES
    FIXES = store.DATA / "fixes"
    (store.DATA / "papers").mkdir(parents=True, exist_ok=True)
    for e in spec["papers"]:
        seed(e["id"], lib)
    if a.make:
        make(spec["papers"], want, a.make, a.at_once)
    # Without its corrections a paper would go out with the model's errors back in it, and nothing would say so.
    lost = [e["id"] for e in spec["papers"] if not (FIXES / f"{e['id']}.json").exists()]
    if lost and not a.unproofread:
        sys.exit(f"No proofreading corrections for {', '.join(lost)} in {FIXES}.\n"
                 "They are kept in the gallery's repository (github.com/chenxiachan/paperfold-gallery, fixes/): copy them here,\n"
                 "or proofread these papers first (--review). --unproofread builds without them, for a look only.")

    site = store.DATA / "site"
    site.mkdir(parents=True, exist_ok=True)
    known = {p["id"]: p for p in store.papers()}
    cards = []
    for e in spec["papers"]:
        pid, p = e["id"], known.get(e["id"])
        if not p or missing(pid, want):
            print(f"  - {pid}: not ready ({', '.join(missing(pid, want)) if p else 'not generated'}); run with --make")
            continue
        meta = json.loads((store.pdir(pid) / "meta.json").read_text(encoding="utf-8"))
        lic = meta.get("license", "")
        if not (shareable(lic) or e.get("consent")):
            print(f"  - {pid}: left out ({lic or 'no license'})")
            continue
        out = site / pid
        out.mkdir(exist_ok=True)
        doc = store.assemble(pid)
        doc["meta"].setdefault("titles", {}).update(e.get("title", {}))   # a title the list words better than the model
        fx = FIXES / f"{pid}.json"
        if fx.exists():
            for w in corrected(doc, json.loads(fx.read_text(encoding="utf-8")), want):
                print(f"  ~ {pid} {w}")
        if a.review:
            (store.DATA / "review").mkdir(exist_ok=True)
            (store.DATA / "review" / f"{pid}.json").write_text(json.dumps(review(doc, want), ensure_ascii=False, indent=1), encoding="utf-8")
        beside = None
        if pdf.has_source(pid):   # its original pages next to the text: the PDF, fetched once into the gallery's folder
            f = pdf.path(pid)
            if f:
                slim(f, out / "source.pdf")
                beside = {"file": "source.pdf", "pdfjs": "../assets/pdfjs/"}
            else:
                print(f"  ~ {pid}: no PDF to be had, so no original pages beside it")
        (out / "index.html").write_text(build.page(doc, app=False, assets="../assets/", pdf=beside), encoding="utf-8")
        cards.append((e, p, meta, lic))
        print(f"  + {pid}: {p['title'][:60]}")
    # the pages' fonts, KaTeX and PDF.js, beside them: a visitor's browser asks no other server for anything
    shutil.rmtree(site / "assets", ignore_errors=True)
    for part in ("fonts", "katex", "pdfjs"):
        shutil.copytree(build.VENDOR / part, site / "assets" / part)
    for d in site.iterdir():   # a paper taken off the list leaves the site too
        if d.is_dir() and d.name != "assets" and d.name not in {c[0]["id"] for c in cards}:
            shutil.rmtree(d)
            print(f"  - {d.name}: removed from the site")
    (site / "index.html").write_text(index(cards, want), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")   # GitHub Pages serves the files as they are
    print(f"{len(cards)} papers in {site}")


UI = {
    "en": {"name": "EN", "lede": "Landmark papers from {n} fields{extra}, ready to fold and unfold. Each one opens as a one-screen map of its sections. Pinch, or press 1 to 5, to go from the map down to every word.",
           "extra": ", a Wikipedia article and a GitHub README",
           "own": "Unfold any paper, article or README yourself with TextFold", "consent": "shared with the author's consent",
           "open": "Open the map", "orig": "Original title: ",
           "foot": "Papers belong to their authors and keep their licenses, named under each title. TextFold is not affiliated with the authors; the levels, links and translations were made by AI.",
           "takedown": "An author who would like a paper taken down: {issue}.", "issue": "open an issue",
           "made": "Made by {me} with {pf}."},
    "zh": {"name": "中文", "lede": "{n} 个领域的标志性论文{extra}，可以随手折叠、展开。每篇先以一屏的章节地图打开；双指缩放，或按 1 到 5，就能从地图一路展开到每一个词。",
           "extra": "，外加一篇维基百科条目和一份 GitHub README",
           "own": "用 TextFold 展开任何一篇论文、文章或 README", "consent": "经作者同意分享",
           "open": "打开地图", "orig": "原标题：",
           "foot": "论文属于原作者，沿用作者的许可协议，标在每篇标题下方。TextFold 与作者没有隶属关系；各层级、链接和译文由 AI 生成。",
           "takedown": "作者如希望撤下论文，请{issue}。", "issue": "提交 issue",
           "made": "由 {me} 用 {pf} 制作。"},
    "de": {"name": "DE", "lede": "Wegweisende Arbeiten aus {n} Fachgebieten{extra} zum Auf- und Zufalten. Jede öffnet als Karte ihrer Abschnitte auf einem Bildschirm. Mit zwei Fingern zoomen oder 1 bis 5 drücken, und es geht von der Karte bis zu jedem Wort.",
           "extra": ", dazu ein Wikipedia-Artikel und ein GitHub-README,",
           "own": "Jede Arbeit, jeden Artikel und jedes README selbst mit TextFold auffalten", "consent": "mit Zustimmung des Autors geteilt",
           "open": "Karte öffnen", "orig": "Originaltitel: ",
           "foot": "Die Arbeiten gehören ihren Autorinnen und Autoren und behalten deren Lizenz, genannt unter jedem Titel. TextFold ist mit ihnen nicht verbunden; Ebenen, Verweise und Übersetzungen hat KI erstellt.",
           "takedown": "Autorinnen und Autoren, die eine Arbeit entfernen lassen möchten: {issue}.", "issue": "Issue eröffnen",
           "made": "Erstellt von {me} mit {pf}."},
}


def source(pid, meta):
    """Where a card's text comes from: arXiv's id, else the source's own label (en.wikipedia.org, GitHub · owner/repo)."""
    return f"arXiv:{pid}" if store.ARXIV_ID.fullmatch(pid) else meta.get("label") or pid


def person(a):
    """arXiv's "Last, First" as the name is written."""
    last, _, first = a.partition(", ")
    return f"{first} {last}" if first else a


def index(cards, want):
    e = html.escape

    def each(get):   # one span per language; the page shows the chosen one
        return "".join(f'<span data-l="{l}">{e(get(l))}</span>' for l in want)

    def linked(key, **links):   # a line per language with links put in its {placeholders}
        out = []
        for l in want:
            text = e(UI[l][key])
            for name, make in links.items():
                text = text.replace("{" + name + "}", make(l))
            out.append(f'<span data-l="{l}">{text}</span>')
        return "".join(out)

    rows = []
    for c, p, meta, lic in cards:
        pid, authors = p["id"], [person(x) for x in p.get("authors", [])]
        by = ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else "")
        titles = {**p["titles"], **c.get("title", {})}
        title = each(lambda l: titles.get(l) or p["title"])
        orig = "".join(f'<span data-l="{l}">{e(UI[l]["orig"])}{e(p["title"])}</span>' for l in want if titles.get(l) and l != "en")
        held = f'<a href="{e(lic)}">{e(license_name(lic))}</a>' if shareable(lic) else each(lambda l: UI[l]["consent"])
        rows.append(f'''<article class="paper">
  <div class="field">{each(lambda l: c["field"][l])}</div>
  <a class="title" data-pid="{e(pid)}" href="{e(pid)}/index.html#level=Topic">{title}</a>
  {f'<div class="orig">{orig}</div>' if orig else ''}
  <p class="why">{each(lambda l: c["why"][l])}</p>
  <div class="meta"><span>{e(by)}</span><span>{e(meta.get("date", "")[:4])}</span><span>{e(source(pid, meta))}</span><span class="lic">{held}</span></div>
  <div class="go">{each(lambda l: UI[l]["open"])} <span aria-hidden="true">→</span></div>
</article>''')
    papers = [c for c in cards if not c[1]["id"].startswith(("wiki-", "gh-", "md-"))]   # the fields count papers only
    n = len(papers)
    extra = lambda l: UI[l]["extra"] if len(papers) < len(cards) else ""
    tabs = "".join(f'<button type="button" data-set="{l}">{e(UI[l]["name"])}</button>' for l in want)
    show = " ".join(f'html[data-lang="{l}"] [data-l]:not([data-l="{l}"]) {{ display: none; }}' for l in want)
    return f'''<!doctype html>
<html lang="en" data-lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TextFold Gallery</title>
<link rel="canonical" href="{SITE}gallery/">
<meta name="description" content="Landmark papers from {n} fields in TextFold: fold any paper from every word to a one-screen map, in English, Chinese and German.">
<style>
:root {{ --bg: #FAF9F7; --card: #ffffff; --ink: #1f1d1a; --muted: #6b6558; --line: #e6e1d8; --accent: #6B5CE7; --soft: #efedfd; --warm: #E08A3C; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #17161a; --card: #1f1e23; --ink: #ece9f1; --muted: #a29fae; --line: #34323a; --accent: #a99ffb; --soft: #2a2740; --warm: #e8a35f; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 48px 16px 72px; }}
header {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
header svg {{ width: 34px; height: 34px; }}
h1 {{ margin: 0; font-size: 26px; letter-spacing: -.01em; }}
.langs {{ margin-left: auto; display: flex; gap: 4px; background: var(--card); border: 1px solid var(--line); border-radius: 999px; padding: 3px; }}
.langs button {{ font: inherit; font-size: 13px; color: var(--muted); background: none; border: 0; border-radius: 999px; padding: 3px 12px; cursor: pointer; }}
.langs button[aria-pressed="true"] {{ background: var(--accent); color: #fff; }}
.lede {{ color: var(--muted); max-width: 680px; margin: 14px 0 6px; }}
.own {{ display: inline-block; margin-bottom: 28px; color: var(--accent); font-weight: 600; text-decoration: none; }}
.own:hover {{ text-decoration: underline; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 14px; }}
.paper {{ position: relative; display: flex; flex-direction: column; gap: 8px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; transition: border-color .15s, transform .15s; }}
.paper:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
.paper a.title::after {{ content: ""; position: absolute; inset: 0; z-index: 1; border-radius: 14px; }}   /* the whole card opens the paper */
.paper .lic a {{ position: relative; z-index: 2; }}
.field {{ align-self: flex-start; font-size: 12px; font-weight: 650; letter-spacing: .02em; color: var(--accent); background: var(--soft); border-radius: 999px; padding: 2px 10px; }}
.title {{ font-size: 16.5px; font-weight: 650; line-height: 1.35; color: var(--ink); text-decoration: none; }}
.orig {{ font-size: 12.5px; color: var(--muted); line-height: 1.4; }}
.why {{ margin: 0; font-size: 14px; color: var(--ink); opacity: .9; flex: 1; }}
.meta {{ display: flex; flex-wrap: wrap; gap: 2px 10px; font-size: 12px; color: var(--muted); }}
.meta a {{ color: var(--muted); }}
.go {{ font-size: 13px; font-weight: 600; color: var(--warm); }}
footer {{ margin-top: 40px; color: var(--muted); font-size: 13px; line-height: 1.6; }}
footer p {{ margin: 0 0 6px; }}
footer a {{ color: var(--accent); }}
{show}
</style>
<main>
  <header><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="2.4" rx="1.2" fill="#6B5CE7"/><rect x="2" y="8.8" width="11" height="2.4" rx="1.2" fill="#6B5CE7" fill-opacity=".4"/><rect x="2" y="14.6" width="5" height="2.4" rx="1.2" fill="#E08A3C"/></svg><h1>TextFold</h1><nav class="langs">{tabs}</nav></header>
  <p class="lede">{each(lambda l: UI[l]["lede"].format(n=n, extra=extra(l)))}</p>
  <a class="own" href="{SITE}">{each(lambda l: UI[l]["own"])} →</a>
  <div class="grid">
{"".join(rows)}
  </div>
  <footer>
    <p>{each(lambda l: UI[l]["foot"])}</p>
    <p>{linked("takedown", issue=lambda l: f'<a href="{ISSUES}">{e(UI[l]["issue"])}</a>')}</p>
    <p>{linked("made", me=lambda l: f'<a href="{AUTHOR}">Xia Chen</a>', pf=lambda l: f'<a href="{REPO}">TextFold</a>')}</p>
  </footer>
</main>
<script>
(function () {{
  var LANGS = {json.dumps(want)}, root = document.documentElement;
  function pick() {{
    try {{ var s = localStorage.getItem('pf-gallery-lang'); if (LANGS.indexOf(s) >= 0) return s; }} catch (e) {{}}
    var nav = navigator.languages || [navigator.language || 'en'];
    for (var i = 0; i < nav.length; i++) {{ var b = String(nav[i]).toLowerCase().split('-')[0]; if (LANGS.indexOf(b) >= 0) return b; }}
    return 'en';
  }}
  function set(l) {{
    root.dataset.lang = l; root.lang = l === 'zh' ? 'zh-CN' : l;
    document.querySelectorAll('.langs button').forEach(function (b) {{ b.setAttribute('aria-pressed', String(b.dataset.set === l)); }});
    document.querySelectorAll('a.title').forEach(function (a) {{ a.href = a.dataset.pid + '/index.html#lang=' + l + '&level=Topic'; }});
  }}
  document.querySelectorAll('.langs button').forEach(function (b) {{
    b.addEventListener('click', function () {{ set(b.dataset.set); try {{ localStorage.setItem('pf-gallery-lang', b.dataset.set); }} catch (e) {{}} }});
  }});
  set(pick());
}})();
</script>
</html>
'''


if __name__ == "__main__":
    main()
