"""TextFold.

  python -m textfold serve [--port 3017]                 the local app (landing, sidebar, reader)
  python -m textfold <arxiv id or url> [--lang zh] ...   generate from the command line, then export
  python -m textfold notes.md [--lang zh] ...           the same for a Markdown file
  python -m textfold export <arxiv id>                   one self-contained HTML file in out/<id>/
  python3 -m textfold get <link or file> [--level topic] [--section 3] [--json]   a document at one level (for a person or an agent)
  python3 -m textfold engine status | install | remove              the PDF engine (MinerU, about 3.4 GB)
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

from . import build, local, pipeline


def _say(*a):
    print(*a, file=sys.stderr, flush=True)


def _utf8_streams():
    """The document's text (titles, quotes) reaches stdout/stderr as UTF-8, whatever the locale's codepage is: on
    Windows a redirected or piped stream is otherwise cp936 (or another legacy codepage) and a character outside it
    raises instead of printing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def get(a):
    """A document at one level, on stdout: written first when it has no levels yet (a model is called), unless
    --no-generate. A PDF (or a Word, PowerPoint, Excel, EPUB or HTML file, a picture) goes through the PDF engine; a
    Markdown or text file is read as it is."""
    from . import agents, engine, fold, models, office, pdfdoc, pdftext, store
    f = Path(a.ref).expanduser()
    ext = engine.kind_of(f.name) if f.is_file() else None
    if ext:
        data = f.read_bytes()
        pid = pdfdoc.pid_of(data, ext)
        if not (store.pdir(pid) / "meta.json").exists() and not engine.where() and ext != "pdf" and office.reads(ext):
            office.save(pid, f, f.name)   # (the engine reads it more completely, when it is installed)
        elif not (store.pdir(pid) / "meta.json").exists() and not engine.where() and ext == "pdf" and pdftext.save(pid, f, f.name):
            pass   # a PDF with its words in it, read by TextFold itself (a scan still needs the engine)
        elif not (store.pdir(pid) / "meta.json").exists():
            if not engine.where():
                what = "PDF" if ext == "pdf" else "picture" if ext in engine.PICTURES else f"{engine.FILES[ext]} file"
                _say(f"This {what} needs the PDF engine: python3 -m textfold engine install")
                return 2
            tier = engine.tier_for(f"x.{ext}", models.load_settings().get("pdf_tier") or "fast")
            out = store.DATA / "uploads" / f"{pid}-{tier}"
            src = f
            if f.suffix.lower() != f".{ext}":   # (an extension the engine knows by another: .tif)
                src = store.DATA / "uploads" / f"{pid}.{ext}"
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_bytes(data)
            engine.convert(src, out, tier, progress=lambda s, d, t: _say(f"  converting {d}/{t}") if t else None, log=lambda line: None)
            pdfdoc.store_pdf(pid, out, pdfdoc.read_output(out), f.name, tier)
            (store.pdir(pid) / pdfdoc.kept_name(pid, src)).write_bytes(data)
            shutil.rmtree(out, ignore_errors=True)   # the engine's output (and its copy of the file), stored
            if src != f:
                src.unlink(missing_ok=True)
    elif f.is_file():
        pid = local.import_file(f)
    else:
        pid = store.resolve(a.ref)
    if not a.no_generate:
        chosen = models.load_settings().get("model") or ""
        if not chosen or agents.runtime_of(chosen):   # an agent's model (or none chosen): the agents on this computer
            agents.scan()                             # found first, as the server finds them when it starts
    try:
        spec = None if a.no_generate else models.resolve(None)
    except RuntimeError:
        spec = None
    try:
        pipeline.generate(pid, a.lang, spec, progress=lambda s, d, t: _say(f"  {s} {d}/{t}") if t and s != "done" else None, log=lambda line: None)
    except RuntimeError as e:   # fetched and parsed; its levels need a model: the local ones, said so
        if "No model" not in str(e):
            raise
        _say("No model: the written levels are the author's first sentences (connect one with python3 -m textfold serve, Settings).")
    d = fold.document(pid, a.lang)
    if a.json:
        import json
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(fold.markdown(d, a.level, a.section), end="")
    return 0


def engine_cmd(args):
    from . import engine
    what = args[0] if args else "status"
    if what == "install":
        engine.install(progress=lambda s, d, t: _say(f"  {s} ({d}/{t})"), log=_say)
    elif what == "remove":
        engine.uninstall()
    elif what != "status":
        _say("python3 -m textfold engine status | install | remove")
        return 2
    st = engine.status()
    print(f"PDF engine: {'installed' if st['installed'] else 'not installed'} (MinerU {st['version']})"
          + (f", {st['bytes'] / 1e9:.1f} GB in {st['path']}" if st["installed"] else ""))
    return 0


def main():
    _utf8_streams()
    if sys.argv[1:2] == ["--version"]:
        from . import __version__
        return print(f"TextFold {__version__}")
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        ap = argparse.ArgumentParser(prog="python3 -m textfold serve")
        ap.add_argument("--port", type=int, default=3017)
        a = ap.parse_args(sys.argv[2:])
        from .server import serve
        return serve(a.port)
    if len(sys.argv) > 1 and sys.argv[1] == "get":
        from .fold import LEVELS
        ap = argparse.ArgumentParser(prog="python3 -m textfold get", description="A document at one level: the map first, then the parts that matter, deeper.")
        ap.add_argument("ref", help="a link (any web page, arXiv, PubMed Central, bioRxiv, Wikipedia, GitHub), an arXiv id, a DOI, or a file (.pdf, .docx, .pptx, .xlsx, .epub, .html, a picture, .md, .txt)")
        ap.add_argument("--level", default="topic", choices=LEVELS, help="topic (a map of the sections) ... full (every word); default topic")
        ap.add_argument("--section", help="one section: its number (3.2), its id (S3), or its place in the list (1 is the first)")
        ap.add_argument("--lang", default="en", help="the language to read it in (en, zh, de, ...); default en")
        ap.add_argument("--json", action="store_true", help="the whole document in the TextFold format (docs/format.md)")
        ap.add_argument("--no-generate", action="store_true", help="call no model: the levels already written, or the local ones")
        return sys.exit(get(ap.parse_args(sys.argv[2:])))
    if len(sys.argv) > 1 and sys.argv[1] == "engine":
        return sys.exit(engine_cmd(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        print(build.export(sys.argv[2]))
        return
    ap = argparse.ArgumentParser(prog="textfold", description="Generate a zoomable reader for an arXiv paper or a Markdown file.")
    ap.add_argument("paper", help="arXiv id or URL, e.g. 2512.23916, or a Markdown file")
    ap.add_argument("--lang", action="append", default=[], help="also generate this language (zh, ja, es, ...); repeatable")
    ap.add_argument("--model", default="sonnet", help="model alias for `claude -p` (default: sonnet)")
    ap.add_argument("--jobs", type=int, default=4, help="parallel model calls")
    ap.add_argument("--force", action="store_true", help="regenerate (ignore what is stored and cached)")
    a = ap.parse_args()
    t0 = time.time()
    ref = local.import_file(a.paper) if Path(a.paper).suffix.lower() in local.KINDS and Path(a.paper).is_file() else a.paper
    pid = None
    for lang in ["en"] + a.lang:
        pid = pipeline.generate(ref, lang, a.model, force=a.force, jobs=a.jobs, log=print,
                                progress=lambda s, d, t: print(f"  {s} {d}/{t}", end="\r"))
    print(f"\n  → {build.export(pid)}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
