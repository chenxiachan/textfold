"""The local server: python -m textfold serve  ->  http://localhost:3017  (ThoughtDAG keeps 3001)

  GET  /                      landing: paste a link, pick a language, the list of papers
  GET  /p/<id>?lang=xx        a paper in the reader
  GET  /api/papers            the papers on disk
  GET  /api/jobs              generation jobs (queued, running, recently finished)
  POST /api/jobs/stop         {id}: stop a job: a queued one is dropped; a running one ends once the model calls under way
                              return (what they wrote is kept in the cache, so starting it again goes on from there)
  POST /api/generate          {paper, lang, force}: queue a generation; one runs at a time. paper: an arXiv id or
                              link, a PMC or PPR id, a DOI (Europe PMC, epmc.py), or a stored document's id
  POST /api/import            {name, text}: store a Markdown file as a paper (local.py); its id, to generate
  GET  /api/models            every model this computer can reach: local agents and API providers
  POST /api/models/select     {model, effort}: the model generations use
  POST /api/agents/scan       look for agents again (also done at startup)
  POST /api/providers/probe   {base, api_key | key, preset}: a provider's /models
  POST /api/providers/save    {key?, preset, name, base, api_key?, models}  ·  POST /api/providers/remove {key}
  POST /api/settings          {use_thoughtdag_env}  ·  POST /api/settings/test {model?}: one tiny call
  GET  /api/notes/<id>        the reader's notes on a paper (docs/notes-format.md)
  POST /api/files             a file dropped on the landing, its bytes (X-Name, X-Lang): a PDF (X-Refs: the papers it
                              names), or a Word, PowerPoint, Excel, EPUB, HTML file or picture: through the PDF engine
  GET  /api/pdf/<id>          the paper's original PDF (arXiv, PubMed Central, bioRxiv), fetched once and kept (pdf.py)
  POST /api/pdf/<id>          the PDF itself (the reader's copy, when the server would not give it): kept the same way
  POST /api/notes/<id>        {op: "put", note} or {op: "delete", id}
  POST /api/export            {paper, lang, langs: "current" | "all", kinds: ["hl", "note", "ask"], pdf}: the paper as one
                              HTML file: its levels, the reader's notes of the kinds chosen, and (pdf) its original PDF
                              with PDF.js, for the side-by-side view
  POST /api/ask               {paper, id, question}: the model answers a note's question; the answer is stored in it
  POST /api/papers/delete     {paper}: move a paper to papers/.trash (not while it is being generated)
  GET  /api/bridge             the ChatGPT bridge (127.0.0.1:10531): running and its models, or what starting it needs
  POST /api/bridge/start       start it detached (bridges.py)
  POST /api/openrouter/start   {back}: the OpenRouter consent URL (OAuth PKCE); GET /oauth/openrouter is its return
  GET  /api/zotero             Zotero Desktop (its local API): running, its libraries, the one chosen (zotero.py)
  GET  /api/zotero/collections ?lib=users/0 | groups/<id>: the library's collections, each with its parent
  GET  /api/zotero/items       ?lib=…&collection=<key>&q=…: its items, each with the document it became, if any
  POST /api/zotero/library     {library}: the one to browse, kept
  POST /api/zotero/read        {item: "<lib>/<key>", lang}: an item read. {ref} for its paper online, generated as a
                               link is; else its file, taken as a file dropped on the landing is (/api/files' reply)

Loopback only, and POSTs from other origins are refused. Settings and keys live in
~/.textfold/settings.json (mode 600), never in the project folder, which may be synced or shared.
"""
import base64
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import agents, bridges, build, engine, fetch, langs, llm, local, models, notes, office, pdf, pdfdoc, pdftext, pipeline, providers, store, zotero

STATIC = {"app.js": "text/javascript", "app.css": "text/css", "reader.js": "text/javascript",
          "reader.css": "text/css", "i18n.js": "text/javascript", "tutorial.js": "text/javascript"}
# fonts and KaTeX, kept here so that no page asks another server for them (web/vendor)
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
VENDOR_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".woff2": "font/woff2",
                ".txt": "text/plain; charset=utf-8"}


def vendor_file(rel):
    """A file of web/vendor by its path under /static/vendor/, or None: nothing outside it, nothing but those kinds."""
    root = (build.WEB / "vendor").resolve()
    f = (root / rel).resolve()
    return f if rel and root in f.parents and f.is_file() and f.suffix in VENDOR_TYPES else None


def models_payload():
    s = models.load_settings()
    cat, provs = models.catalog(s)
    selected = models.default_model(s, cat)
    return {
        "models": cat, "selected": selected, "selectedName": next((m["name"] for m in cat if m["id"] == selected), selected),
        "efforts": s.get("efforts") or {}, "bridge": bridges.bridge_status(),
        "agents": [{k: a[k] for k in ("runtime", "label", "binary", "path", "version", "runnable", "error")} | {"models": len(a["models"])}
                   for a in agents.STATE["agents"]],
        "scanning": agents.STATE["scanning"], "scannedAt": agents.STATE["scanned_at"],
        "providers": [{"key": p["key"], "name": p["name"], "source": p["source"], "base": p["base"], "protocol": p["protocol"],
                       "preset": p.get("preset"), "models": [m["id"] for m in p["models"]],
                       "hasKey": bool(p["api_key"]) and p["api_key"] not in ("none", "ollama")} for p in provs],
        "presets": providers.PRESETS[:-1] + [models.ANTHROPIC_PRESET, providers.PRESETS[-1]],
        "useThoughtdagEnv": s.get("use_thoughtdag_env", True), "thoughtdagEnv": providers.THOUGHTDAG_ENV.exists(),
        "pdfTier": s.get("pdf_tier") or "fast", "engine": engine.status(),
    }


class Jobs:
    """Two queues, each worked one job at a time: generation (model calls: waits on the network) and the PDF engine
    (installing it, converting a file: the computer's own GPU), so that one PDF converts while another is generated."""

    def __init__(self):
        self.items, self.q, self.eq = {}, queue.Queue(), queue.Queue()
        self.stops = {}   # job id -> threading.Event, set when the reader stops it (kept out of the job: it is sent as JSON)
        self.pending = {}  # files that wait for the engine to be installed: pid -> (file, name, lang)
        for side in (store.DATA / "uploads").glob("*.json"):   # (kept beside each, so a restart forgets none)
            try:
                w = json.loads(side.read_text())
                f = side.parent / Path(w.get("file") or side.stem + ".pdf").name
                if f.exists() and not (store.pdir(f.stem) / "meta.json").exists():
                    self.pending[f.stem] = (str(f), w["name"], w["lang"])
            except (OSError, ValueError, KeyError, AttributeError):
                pass
        threading.Thread(target=self.work, daemon=True).start()
        threading.Thread(target=self.work_engine, daemon=True).start()

    def _new(self, q, **kw):
        job = {"id": uuid.uuid4().hex[:10], "kind": "generate", "pid": "", "lang": "", "force": False, "status": "queued",
               "stage": "queued", "done": 0, "total": 0, "error": "", "log": [], "created": time.time(), "finished": None, **kw}
        self.items[job["id"]] = job
        self.stops[job["id"]] = threading.Event()
        q.put(job["id"])
        return job

    def _same(self, **kw):   # the same request already waiting or running is that job
        return next((j for j in self.items.values() if j["status"] in ("queued", "running") and all(j.get(k) == v for k, v in kw.items())), None)

    def submit(self, ref, lang, force, attach=None):
        pid = store.resolve(ref)
        return self._same(kind="generate", pid=pid, lang=lang) or self._new(self.q, pid=pid, lang=lang, force=bool(force), attach=attach)

    def convert(self, pid, file, name, lang, tier=None, replace=False):
        """A file through the engine (then generated): file is on disk; replace converts a stored one again."""
        tier = engine.tier_for(file, tier or models.load_settings().get("pdf_tier") or "fast")
        return self._same(kind="convert", pid=pid) or self._new(self.eq, kind="convert", pid=pid, lang=lang, file=str(file), name=name,
                                                                title=name, tier=tier, replace=bool(replace))

    def wait_for_engine(self, pid, file, name, lang):
        """A file kept until the engine is installed (then converted), with a note of its name and language beside it."""
        self.pending[pid] = (str(file), name, lang)
        Path(file).with_suffix(".json").write_text(json.dumps({"name": name, "lang": lang, "file": Path(file).name}, ensure_ascii=False))

    def read_pdf(self, pid, file, name, lang):
        """A PDF that has its words in it, read by TextFold itself while the engine is not installed (pdftext.py), the
        file gone from uploads/ once it is stored: its levels' job, or None when it needs the engine (a scan)."""
        if not pdftext.save(pid, file, name):
            return None
        Path(file).unlink(missing_ok=True)
        return self.submit(pid, lang, False)

    def install_engine(self):
        return self._same(kind="install") or self._new(self.eq, kind="install", title="PDF engine")

    def stop(self, job_id):
        job = self.items.get(job_id)
        if not job or job["status"] not in ("queued", "running"):
            return job
        if job["status"] == "queued":   # never started: off the queue (work() skips it)
            job.update(status="stopped", finished=time.time())
        else:
            job["stopping"] = True
            self.stops[job_id].set()
        return job

    def _progress(self, job, stop):
        def progress(stage, done, total):
            if stop.is_set() and stage != "done":   # a fetch that reports (bioRxiv's paced figures) ends here too
                raise llm.Stopped()
            job.update(stage=stage, done=done, total=total)
            if stage in ("ladder", "links", "translate", "done"):   # parsed: the reader can open it (in its own words)
                job["readable"] = True
            if job["kind"] == "generate" and stage not in ("queued", "fetch") and (store.pdir(job["pid"]) / "meta.json").exists():
                if not job.get("title"):   # a new paper's title, once it is parsed
                    try:
                        job["title"] = json.loads((store.pdir(job["pid"]) / "meta.json").read_text())["title"]
                    except (OSError, ValueError, KeyError):
                        pass
                if job.get("attach") and not job.get("attached"):   # the reader's own copy of an online paper's PDF
                    job["attached"] = True
                    try:
                        if not (store.pdir(job["pid"]) / "source.pdf").exists():
                            pdf.keep(job["pid"], Path(job["attach"]).read_bytes())
                        Path(job["attach"]).unlink()
                    except OSError:
                        pass
        return progress

    def _log(self, job):
        def log(line):
            job["log"] = (job["log"] + [line])[-40:]
        return log

    def _end(self, job, e, stop):
        if job["pid"] and (store.pdir(job["pid"]) / "source.html").exists():
            job["readable"] = True   # what failed was its levels: it reads as written
        if isinstance(e, (llm.Stopped, engine.Stopped)) or stop.is_set():
            job.update(status="stopped", stopping=False)
        else:
            job.update(status="error", error="no-model" if str(e).startswith("No model is available") else str(e))
            self._log(job)(traceback.format_exc()[-800:])
        job["finished"] = time.time()
        PAGES.pop(job["pid"], None)

    def work(self):
        while True:
            job = self.items[self.q.get()]
            if job["status"] == "stopped":
                continue
            job["status"] = "running"
            stop = self.stops[job["id"]]
            try:
                try:
                    spec = models.resolve(None)  # the model chosen in the settings, resolved once per job
                    job["model"] = spec["id"]
                except RuntimeError:   # none: the paper is still fetched and parsed, to be read as written
                    spec = None
                pipeline.generate(job["pid"], job["lang"], spec, force=job["force"], progress=self._progress(job, stop), log=self._log(job), stop=stop)
                job["status"] = "done"
            except Exception as e:  # shown in the app; the traceback stays in the job log
                if job.get("attach") and str(e) == "no-html" and not stop.is_set():
                    self._fallback(job)   # an arXiv paper without its HTML version: its PDF, converted here
                    continue
                if str(e) == "web-pdf" and not stop.is_set():
                    try:
                        self._pdf_link(job)   # a link to a PDF: the file, as if it had been dropped on the landing
                        continue
                    except Exception as e2:
                        e = e2
                self._end(job, e, stop)
                continue
            job["finished"] = time.time()
            PAGES.pop(job["pid"], None)

    def _pdf_link(self, job):
        url = (store.pdir(job["pid"]) / "url.txt").read_text().strip()
        with urllib.request.urlopen(urllib.request.Request(url, headers=fetch.UA), timeout=90) as r:
            data = r.read(pdf.LIMIT + 1)
        if data[:5] != b"%PDF-" or len(data) > pdf.LIMIT:
            raise ValueError("not-pdf")
        shutil.rmtree(store.pdir(job["pid"]), ignore_errors=True)   # (only its address was there)
        pid = pdfdoc.pid_of(data)
        name = Path(urllib.parse.urlparse(url).path).name or "document.pdf"
        f = store.DATA / "uploads" / f"{pid}.pdf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        job["finished"] = time.time()
        if (store.pdir(pid) / "meta.json").exists():   # read before
            f.unlink()
            job.update(status="moved", next=self.submit(pid, job["lang"], False)["id"])
        elif engine.where():
            job.update(status="moved", next=self.convert(pid, f, name, job["lang"])["id"])
        elif nxt := self.read_pdf(pid, f, name, job["lang"]):
            job.update(status="moved", next=nxt["id"])
        else:
            self.wait_for_engine(pid, f, name, job["lang"])
            job.update(status="error", error="needs-engine", upload=pid)

    def _fallback(self, job):
        file = Path(job["attach"])
        pid = file.stem
        job.update(finished=time.time())
        if engine.where():
            nxt = self.convert(pid, file, job.get("name") or file.name, job["lang"])
            job.update(status="moved", next=nxt["id"])
        elif nxt := self.read_pdf(pid, file, job.get("name") or file.name, job["lang"]):
            job.update(status="moved", next=nxt["id"])
        else:
            self.wait_for_engine(pid, file, job.get("name") or file.name, job["lang"])
            job.update(status="error", error="needs-engine", upload=pid)

    def work_engine(self):
        while True:
            job = self.items[self.eq.get()]
            if job["status"] == "stopped":
                continue
            job["status"] = "running"
            stop = self.stops[job["id"]]
            progress, log = self._progress(job, stop), self._log(job)
            try:
                if job["kind"] == "install":
                    engine.install(progress, log, stop)
                    waiting, self.pending = self.pending, {}
                    for pid, (file, name, lang) in waiting.items():
                        self.convert(pid, file, name, lang)
                else:
                    out = store.DATA / "uploads" / f"{job['pid']}-{job['tier']}"
                    try:
                        engine.convert(job["file"], out, job["tier"], progress, log, stop)
                    except Exception:   # a file the engine could not read: TextFold's own reader, if it has one
                        if stop.is_set() or not office.reads(engine.kind_of(job["file"])):
                            raise
                        log("the engine could not read this file: TextFold reads it itself")
                        office.convert(job["file"], out)
                    pdfdoc.store_pdf(job["pid"], out, pdfdoc.read_output(out), job["name"], job["tier"], replace=job["replace"])
                    src = store.pdir(job["pid"]) / pdfdoc.kept_name(job["pid"], job["file"])   # the file itself, kept
                    if not src.exists():
                        src.write_bytes(Path(job["file"]).read_bytes())
                    shutil.rmtree(out, ignore_errors=True)
                    if Path(job["file"]).parent == store.DATA / "uploads":
                        Path(job["file"]).unlink(missing_ok=True)
                        Path(job["file"]).with_suffix(".json").unlink(missing_ok=True)
                    PAGES.pop(job["pid"], None)
                    job["readable"] = True
                    job["next"] = self.submit(job["pid"], job["lang"] or "en", False)["id"]   # its levels, in the other queue
                job.update(status="done", finished=time.time())
            except Exception as e:
                if job["kind"] == "convert":   # what the engine wrote before it failed
                    shutil.rmtree(store.DATA / "uploads" / f"{job['pid']}-{job['tier']}", ignore_errors=True)
                self._end(job, e, stop)

    def list(self):
        now = time.time()
        return [j for j in self.items.values() if j["status"] in ("queued", "running") or now - (j["finished"] or now) < 600]


def _named(headers):
    """A dropped file's name and the language to read it in, from the request's headers."""
    lang = headers.get("X-Lang") or "en"
    return Path(unquote(headers.get("X-Name") or "document.pdf")).name[:200] or "document.pdf", lang if lang in langs.LANGS else "en"


def take_file(jobs, data, headers):
    """A file from the landing, as (reply, status): a PDF (take_pdf), or another the engine reads (a Word, PowerPoint
    or Excel file, an e-book, a saved web page, a picture: engine.FILES). With the engine installed, it reads them all
    (most completely); without it, TextFold reads the ones it can itself, at once (office.py), and keeps the others
    (a picture, an old PowerPoint) until the engine is installed."""
    name, lang = _named(headers)
    ext = engine.kind_of(name)
    if data[:5] == b"%PDF-" or ext == "pdf":
        return take_pdf(jobs, data, headers)
    if not ext:
        return {"error": "not-supported"}, 400
    pid = pdfdoc.pid_of(data, ext)
    f = store.DATA / "uploads" / f"{pid}.{ext}"
    f.parent.mkdir(parents=True, exist_ok=True)
    if (store.pdir(pid) / "meta.json").exists():   # this file, converted before
        return {"job": jobs.submit(pid, lang, False), "via": "file"}, 200
    if not f.exists():
        f.write_bytes(data)
    if engine.where():
        return {"job": jobs.convert(pid, f, name, lang), "via": "file"}, 200
    if office.reads(ext):
        try:
            office.save(pid, f, name)
        except ValueError:
            return {"error": "bad-file"}, 400
        finally:
            f.unlink(missing_ok=True)
        # read: it opens now, as a converted PDF does, while its levels are written (no model is needed to read it)
        return {"job": jobs.submit(pid, lang, False), "paper": pid, "via": "file"}, 200
    jobs.wait_for_engine(pid, f, name, lang)
    return {"needs": "engine", "upload": pid, "name": name, "engine": engine.status()}, 200


def take_pdf(jobs, data, headers):
    """A PDF from the landing, as (reply, status). The paper online when the page found one it names (an arXiv id, a
    DOI) and it can be read there: faster and truer than a conversion, and the file is kept as its original. Else the
    file through the PDF engine; without one, it waits for the reader to install it."""
    if data[:5] != b"%PDF-":
        return {"error": "not-pdf"}, 400
    name, lang = _named(headers)
    try:
        refs = [r for r in json.loads(unquote(headers.get("X-Refs") or "[]")) if isinstance(r, str)][:6]
    except ValueError:
        refs = []
    pid = pdfdoc.pid_of(data)
    f = store.DATA / "uploads" / f"{pid}.pdf"
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_bytes(data)
    if (store.pdir(pid) / "meta.json").exists():   # this file, converted before
        f.unlink()
        return {"job": jobs.submit(pid, lang, False), "via": "pdf"}, 200
    for ref in refs:
        try:
            job = jobs.submit(ref, lang, False, attach=str(f))
        except Exception:   # not a paper online (ValueError), or its server out of reach
            continue
        job.setdefault("name", name)
        return {"job": job, "via": "online", "ref": ref}, 200
    if engine.where():
        return {"job": jobs.convert(pid, f, name, lang), "via": "pdf"}, 200
    job = jobs.read_pdf(pid, f, name, lang)
    if job:   # read: it opens now, as a converted PDF does, while its levels are written
        return {"job": job, "paper": pid, "via": "pdf"}, 200
    jobs.wait_for_engine(pid, f, name, lang)
    return {"needs": "engine", "upload": pid, "name": name, "engine": engine.status()}, 200


PAGES = {}  # pid -> (signature of its layers and template, rendered page)
RELAYOUT = threading.Lock()


def reader_page(pid):
    layers = store.pdir(pid) / "layers"
    sig = tuple(sorted((f.name, f.stat().st_mtime) for f in layers.glob("*.json"))) if layers.exists() else ()
    sig += ((build.WEB / "template.html").stat().st_mtime,)  # a changed page template re-renders too
    hit = PAGES.get(pid)
    if not hit or hit[0] != sig:
        hit = (sig, build.page(store.assemble(pid), app=True))
        PAGES[pid] = hit
    return hit[1]


def landing_page(jobs):
    s = models.load_settings()
    chosen = models.default_model(s)
    boot = {"papers": store.papers(), "jobs": jobs.list(), "model": models.label(chosen, s) if chosen else "",
            "bridge": bridges.bridge_status(), "langs": [[code, v[1]] for code, v in langs.LANGS.items()]}
    t = (build.WEB / "app.html").read_text()
    return t.replace("{{HEAD}}", build.fonts_head(build.APP_ASSETS)).replace("{{BOOT}}", json.dumps(boot, ensure_ascii=False).replace("</", "<\\/"))


def make_handler(jobs):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(data)

        def json(self, obj, code=200):
            self.send(code, json.dumps(obj, ensure_ascii=False))

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/":
                    return self.send(200, landing_page(jobs), "text/html; charset=utf-8")
                if u.path == "/api/bridge":
                    return self.json(bridges.bridge_status(fresh=True))
                if u.path == "/api/openrouter/minted":   # the app, waiting while the system browser authorises
                    return self.json({"minted": bool(bridges.minted())})
                if u.path == "/oauth/openrouter":   # OpenRouter sends the reader back here with a code
                    code = (parse_qs(u.query).get("code") or [""])[0]
                    desktop = bridges.for_desktop()
                    key, err, back = bridges.openrouter_finish(code)
                    back = back if back.startswith("/") else "/"
                    to = f"{back}#or-minted" if key else f"{back}#or-failed={quote(err or '')}"
                    if desktop:   # this tab is the system browser's: the app, waiting for the key, takes it from here
                        page = (f'<!doctype html><meta charset="utf-8"><title>TextFold</title><body style="font-family:system-ui;'
                                f'display:grid;place-items:center;height:100vh;margin:0;background:#FAF9F7;color:#1f1d1a">'
                                f'<div style="text-align:center;line-height:1.7"><h2>{"✓" if key else "✕"} OpenRouter</h2>'
                                f'<p style="color:#6b6558">{"回到 TextFold 即可，本页可以关闭。<br>Return to TextFold; this tab can be closed." if key else (err or "")}</p></div>')
                        return self.send(200, page, "text/html; charset=utf-8")
                    page = (f'<!doctype html><meta charset="utf-8"><title>TextFold</title><body style="font-family:system-ui;'
                            f'display:grid;place-items:center;height:100vh;margin:0;background:#FAF9F7;color:#1f1d1a">'
                            f'<div style="text-align:center"><h2>{"✓ OpenRouter" if key else "OpenRouter ✕"}</h2></div>'
                            f'<script>location.replace({json.dumps(to)})</script>')
                    return self.send(200, page, "text/html; charset=utf-8")
                m = re.fullmatch(r"/p/([\w.-]+)/img/([\w.-]+)", u.path)   # a paper's picture (a drawing stays inside its page)
                if m and store.valid_id(m.group(1)):
                    f = store.pdir(m.group(1)) / "img" / m.group(2)
                    kind = IMAGE_TYPES.get(f.suffix.lower())
                    if kind and f.is_file():
                        return self.send(200, f.read_bytes(), kind, cache="max-age=86400")
                    return self.json({"error": "no such picture"}, 404)
                m = re.fullmatch(r"/p/([\w.-]+)", u.path)
                if m and store.valid_id(m.group(1)):
                    if not (store.pdir(m.group(1)) / "source.html").exists():
                        self.send_response(302)
                        self.send_header("Location", "/")
                        self.end_headers()
                        return
                    return self.send(200, reader_page(m.group(1)), "text/html; charset=utf-8")
                if u.path.startswith("/static/") and u.path[8:] in STATIC:
                    return self.send(200, (build.WEB / u.path[8:]).read_bytes(), STATIC[u.path[8:]] + "; charset=utf-8")
                if u.path.startswith("/static/vendor/"):
                    f = vendor_file(unquote(u.path[len("/static/vendor/"):]))
                    if f:
                        return self.send(200, f.read_bytes(), VENDOR_TYPES[f.suffix], cache="max-age=86400")
                if u.path == "/api/papers":
                    return self.json(store.papers())
                if u.path == "/api/jobs":
                    return self.json(jobs.list())
                if u.path in ("/api/models", "/api/settings"):
                    return self.json(models_payload())
                m = re.fullmatch(r"/api/pdfmap/([\w.-]+)", u.path)   # a converted PDF's paragraphs on its pages
                if m and m.group(1).startswith("pdf-") and store.valid_id(m.group(1)) and (store.pdir(m.group(1)) / "meta.json").exists():
                    with RELAYOUT:   # (converted before the layout was kept: found again once, in seconds)
                        try:
                            pdfdoc.relayout(m.group(1))
                        except Exception:
                            pass
                    return self.json(pdfdoc.place(m.group(1)))
                if u.path == "/api/engine":
                    return self.json({**engine.status(), "pending": sorted(jobs.pending)})
                if u.path.startswith("/api/zotero"):
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    try:
                        if u.path == "/api/zotero":
                            return self.json(zotero.status())
                        if u.path == "/api/zotero/collections":
                            return self.json(zotero.collections(q.get("lib", "")))
                        if u.path == "/api/zotero/items":
                            return self.json(zotero.items(q.get("lib", ""), q.get("collection", ""), q.get("q", "")[:200]))
                    except zotero.Off as e:   # Zotero closed, or its local API off: the app says which
                        return self.json({"error": str(e)}, 503)
                    except ValueError as e:
                        return self.json({"error": str(e)}, 400)
                m = re.fullmatch(r"/api/notes/([\w.-]+)", u.path)
                if m and store.valid_id(m.group(1)) and (store.pdir(m.group(1)) / "meta.json").exists():
                    return self.json(notes.read(m.group(1)))
                m = re.fullmatch(r"/api/pdf/([\w.-]+)", u.path)
                if m and store.valid_id(m.group(1)) and (store.pdir(m.group(1)) / "meta.json").exists():
                    f = pdf.path(m.group(1)) if pdf.has_source(m.group(1)) else None
                    if not f:   # with where the reader may open it, to drop it on the pane
                        return self.json({"error": "no-pdf", "open": pdf.where(m.group(1)) if pdf.has_source(m.group(1)) else None}, 404)
                    return self.send(200, f.read_bytes(), "application/pdf", cache="max-age=86400")
                if u.path == "/favicon.ico":
                    return self.send(204, b"", "image/x-icon")
                self.json({"error": "not found"}, 404)
            except Exception as e:
                self.json({"error": str(e)}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1"):
                return self.json({"error": "forbidden"}, 403)  # another site may not drive this server
            try:
                size = int(self.headers.get("Content-Length") or 0)
                m = re.fullmatch(r"/api/pdf/([\w.-]+)", u.path)
                if m:   # a paper's PDF, dropped on the reader's pane: the file's bytes, not JSON
                    if not (store.valid_id(m.group(1)) and (store.pdir(m.group(1)) / "meta.json").exists()):
                        return self.json({"error": "no such paper"}, 404)
                    if size > pdf.LIMIT:
                        return self.json({"error": "too-large"}, 413)
                    return self.json({"ok": True}) if pdf.keep(m.group(1), self.rfile.read(size)) else self.json({"error": "not-pdf"}, 400)
                if u.path in ("/api/files", "/api/pdfs"):   # a file dropped on the landing: its bytes (a PDF's X-Refs: what the page found in it)
                    if size > pdf.LIMIT:
                        return self.json({"error": "too-large"}, 413)
                    return self.json(*take_file(jobs, self.rfile.read(size), self.headers))
                if size > 12_000_000:   # a Markdown file is far smaller; this is a request gone wrong
                    return self.json({"error": "too-large"}, 413)
                body = json.loads(self.rfile.read(size) or b"{}")
                s = models.load_settings()
                if u.path == "/api/generate":
                    lang = body.get("lang") or "en"
                    if lang not in langs.LANGS:
                        return self.json({"error": f"unknown language {lang}"}, 400)
                    try:
                        job = jobs.submit(body.get("paper", ""), lang, body.get("force"))
                    except ValueError as e:   # not a paper's reference, or (a DOI) none Europe PMC can read in full
                        return self.json({"error": str(e) if str(e) in ("no-fulltext", "no-article") else "bad-ref"}, 400)
                    return self.json(job)
                if u.path == "/api/zotero/library":
                    try:
                        zotero.choose(str(body.get("library") or ""))
                    except ValueError as e:
                        return self.json({"error": str(e)}, 400)
                    return self.json(zotero.status())
                if u.path == "/api/zotero/read":   # an item of the Zotero library: its paper online, or its file as a dropped one
                    lib, _, key = str(body.get("item") or "").rpartition("/")
                    lang = body.get("lang") if body.get("lang") in langs.LANGS else "en"
                    try:
                        got = zotero.read(lib, key)
                        if "ref" in got:   # generated as a pasted link is (from the page, which asks for a model first)
                            pid = store.resolve(got["ref"])
                            zotero.link(lib, key, pid, got["title"])
                            return self.json({"ref": pid, "title": got["title"]})
                    except zotero.Off as e:
                        return self.json({"error": str(e)}, 503)
                    except ValueError as e:   # no file and no paper online; not a library or item
                        return self.json({"error": str(e) if str(e) in ("no-file", "no-fulltext", "bad-library", "bad-item") else "bad-ref"}, 400)
                    named = {"X-Name": quote(got["name"]), "X-Lang": lang, "X-Refs": quote(json.dumps(got["refs"]))}
                    reply, code = take_file(jobs, got["data"], named)
                    pid = reply.get("paper") or reply.get("upload") or (reply.get("job") or {}).get("pid")
                    if code == 200 and pid:
                        zotero.link(lib, key, pid, got["title"], got["record"])
                        zotero.apply(pid)   # (read at once, or read before: its record now; else once converted)
                    return self.json({**reply, "name": got["name"]}, code)
                if u.path == "/api/engine/install":
                    return self.json(jobs.install_engine())
                if u.path == "/api/engine/remove":
                    if any(j["kind"] in ("install", "convert") and j["status"] in ("queued", "running") for j in jobs.items.values()):
                        return self.json({"error": "busy"}, 409)
                    return self.json(engine.uninstall())
                if u.path == "/api/convert":   # a stored PDF (or picture) converted again, at another tier (its levels follow)
                    pid, tier = str(body.get("paper") or ""), str(body.get("tier") or "accurate")
                    src = pdfdoc.source_of(pid) if pid.startswith(("pdf-", "doc-")) and store.valid_id(pid) else None
                    if not src or tier not in engine.TIERS or engine.tier_for(src, tier) != tier:
                        return self.json({"error": "no such PDF"}, 404)
                    if not engine.where():
                        return self.json({"error": "needs-engine"}, 409)
                    name = json.loads((store.pdir(pid) / "meta.json").read_text()).get("source", {}).get("name") or "document.pdf"
                    return self.json(jobs.convert(pid, src, name, body.get("lang") or "en", tier, replace=True))
                if u.path == "/api/jobs/stop":
                    job = jobs.stop(body.get("id", ""))
                    return self.json(job) if job else self.json({"error": "no such job"}, 404)
                if u.path == "/api/import":
                    text, name = str(body.get("text") or ""), str(body.get("name") or "document.md")[:200]
                    if not text.strip():
                        return self.json({"error": "empty"}, 400)
                    try:
                        pid = local.import_text(text, name)
                    except ValueError:
                        return self.json({"error": "not-markdown"}, 400)
                    return self.json({"paper": pid, "title": json.loads((store.pdir(pid) / "meta.json").read_text())["title"]})
                if u.path == "/api/export":   # the paper as one file, saved by the reader for themself
                    pid, lang = str(body.get("paper") or ""), str(body.get("lang") or "en")
                    if not store.valid_id(pid) or not (store.pdir(pid) / "meta.json").exists():
                        return self.json({"error": "no such paper"}, 404)
                    kinds = {k for k in body.get("kinds") or [] if k in ("hl", "note", "ask")}
                    doc = store.assemble(pid, only=[lang] if body.get("langs") == "current" else None)
                    chosen = [n for n in notes.read(pid)["notes"] if notes.kind(n) in kinds]
                    saved = {"notes": chosen, "saved": uuid.uuid4().hex[:12], "lang": lang}
                    if body.get("pdf") and pdf.has_source(pid):   # the original beside it: the file, and PDF.js to show it
                        f = pdf.path(pid)
                        if f:
                            saved["pdf"] = base64.b64encode(f.read_bytes()).decode()
                            saved["pdfjs"] = {"lib": (build.VENDOR / "pdfjs" / "pdf.min.mjs").read_text(),
                                              "worker": (build.VENDOR / "pdfjs" / "pdf.worker.min.mjs").read_text()}
                            if pid.startswith("pdf-"):   # a converted PDF: its paragraphs' places on the pages
                                with RELAYOUT:
                                    try:
                                        pdfdoc.relayout(pid)
                                    except Exception:
                                        pass
                                saved["pdfmap"] = pdfdoc.place(pid)
                    html = build.page(doc, app=False, saved=saved)
                    return self.send(200, html, "text/html; charset=utf-8")
                m = re.fullmatch(r"/api/notes/([\w.-]+)", u.path)
                if m and store.valid_id(m.group(1)) and (store.pdir(m.group(1)) / "meta.json").exists():
                    try:
                        if body.get("op") == "delete":
                            return self.json(notes.delete(m.group(1), str(body.get("id", ""))))
                        return self.json(notes.put(m.group(1), body.get("note")))
                    except ValueError as e:
                        return self.json({"error": str(e)}, 400)
                if u.path == "/api/bridge/start":
                    try:
                        return self.json(bridges.bridge_start())
                    except RuntimeError as e:
                        return self.json({"error": str(e)}, 400)
                if u.path == "/api/openrouter/start":
                    callback = f"http://{self.headers.get('Host') or 'localhost:3017'}/oauth/openrouter"
                    return self.json({"url": bridges.openrouter_start(callback, str(body.get("back") or "/"), bool(body.get("desktop")))})
                if u.path == "/api/papers/delete":
                    pid = str(body.get("paper", ""))
                    if not store.valid_id(pid) or not (store.pdir(pid) / "meta.json").exists():
                        return self.json({"error": "no such paper"}, 404)
                    if any(j["pid"] == pid and j["status"] in ("queued", "running") for j in jobs.list()):
                        return self.json({"error": "busy"}, 409)
                    store.trash(pid)
                    PAGES.pop(pid, None)
                    return self.json(store.papers())
                if u.path == "/api/ask":
                    pid = str(body.get("paper", ""))
                    if not store.valid_id(pid) or not (store.pdir(pid) / "meta.json").exists():
                        return self.json({"error": "no such paper"}, 404)
                    try:
                        return self.json(notes.ask(pid, str(body.get("id", "")), str(body.get("question", "")).strip()[:2000],
                                                   models.for_task(models.resolve(None), "ask"), ui=str(body.get("ui") or "")))
                    except ValueError as e:
                        return self.json({"error": str(e)}, 400)
                    except Exception as e:  # the model failed: the note keeps its question, the reader can ask again
                        return self.json({"error": str(e)[:600]}, 502)
                if u.path == "/api/models/select":
                    s["model"] = body.get("model") or None
                    if "effort" in body and s["model"]:
                        if body["effort"]:
                            s["efforts"][s["model"]] = body["effort"]
                        else:
                            s["efforts"].pop(s["model"], None)
                    models.save_settings(s)
                    return self.json(models_payload())
                if u.path == "/api/agents/scan":
                    agents.scan()
                    return self.json(models_payload())
                if u.path == "/api/providers/probe":
                    key = (bridges.minted() if body.get("minted") else None) or body.get("api_key") or \
                        next((p.get("api_key") for p in s["providers"] if p["key"] == body.get("key")), "")
                    preset = next((p for p in providers.PRESETS + [models.ANTHROPIC_PRESET] if p["id"] == body.get("preset")), None)
                    if preset and preset.get("fixed"):  # MiniMax and Anthropic have no /models a key can list
                        found = [{"id": i} for i in preset["fixed"]]
                    else:
                        try:
                            found = providers.probe(body.get("base", ""), key)
                        except Exception as e:
                            return self.json({"error": str(e)}, 400)
                    for rid in (preset or {}).get("recommend", []):  # recommended ids the probe left out
                        if rid not in {m["id"] for m in found}:
                            found.append({"id": rid, "recommended": True})
                    current = next((p.get("models") for p in s["providers"] if p["key"] == body.get("key")), None)
                    picked = providers.preselect(found, preset, [m["id"] for m in current] if current else None,
                                                 free_first=(preset or {}).get("id") == "openrouter")
                    return self.json({"models": found, "picked": picked, "recommend": (preset or {}).get("recommend", [])})
                if u.path == "/api/providers/save":
                    preset = body.get("preset") or "custom"
                    known = providers.PRESETS + [models.ANTHROPIC_PRESET]
                    name = (body.get("name") or "").strip() or next((p["name"] for p in known if p["id"] == preset), "") or "Custom"
                    key = body.get("key") or re.sub(r"[^a-z0-9]+", "-", f"{preset}-{name}".lower()).strip("-")
                    old = next((p for p in s["providers"] if p["key"] == key), {})
                    row = {"key": key, "preset": preset, "name": name, "base": (body.get("base") or "").strip().rstrip("/"),
                           "api_key": (bridges.take_minted() if body.get("minted") else None) or body.get("api_key") or old.get("api_key", ""),
                           "models": [{"id": i} for i in (body.get("models") or [])][:providers.MAX_MODELS]}
                    if preset == "anthropic":
                        row["protocol"] = "anthropic"
                    s["providers"] = ([p for p in s["providers"] if p["key"] != key] + [row])[-providers.MAX_PROVIDERS:]
                    models.save_settings(s)
                    return self.json(models_payload())
                if u.path == "/api/providers/remove":
                    s["providers"] = [p for p in s["providers"] if p["key"] != body.get("key")]
                    models.save_settings(s)
                    return self.json(models_payload())
                if u.path == "/api/settings":
                    if "use_thoughtdag_env" in body:
                        s["use_thoughtdag_env"] = bool(body["use_thoughtdag_env"])
                    if body.get("pdf_tier") in engine.TIERS:
                        s["pdf_tier"] = body["pdf_tier"]
                    models.save_settings(s)
                    return self.json(models_payload())
                if u.path == "/api/settings/test":
                    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
                    try:
                        spec = models.resolve(body.get("model"))
                        t0 = time.time()
                        with tempfile.TemporaryDirectory() as tmp:
                            rec, _ = llm.call('Return JSON {"ok": true}.', schema, spec, Path(tmp), force=True, timeout=180)
                        return self.json({"ok": bool(rec["out"].get("ok")), "model": spec["id"], "seconds": round(time.time() - t0, 1)})
                    except Exception as e:
                        return self.json({"ok": False, "error": str(e)[:600]})
                self.json({"error": "not found"}, 404)
            except Exception as e:
                self.json({"error": str(e)}, 500)

        def log_message(self, *args):
            pass

    return Handler


def _quiet_children():
    """Windows: the app starts the server without a console, and every console program it starts in turn (an agent,
    MinerU, npx) would open a window of its own for as long as it runs. They start without one."""
    init = subprocess.Popen.__init__

    def quiet(self, *a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        init(self, *a, **kw)
    subprocess.Popen.__init__ = quiet


def serve(port=3017):
    if os.name == "nt":
        _quiet_children()
    jobs = Jobs()
    # look for the agents on this computer while the server comes up
    threading.Thread(target=agents.scan, kwargs={"log": print}, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(jobs))
    print(f"TextFold on http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
