"""The local server: python -m textfold serve  ->  http://localhost:3017  (ThoughtDAG keeps 3001)

  GET  /                      landing: paste a link, pick a language, the list of papers
  GET  /p/<id>?lang=xx        a paper in the reader
  GET  /api/papers            the papers on disk, each with when it came in and was last opened (library.py), and its
                              citekey when it was read from a Zotero item that has one (zotero.citekeys)
  GET  /api/jobs              generation jobs (queued, running, recently finished)
  POST /api/jobs/stop         {id}: stop a job: a queued one is dropped; a running one ends once the model calls under way
                              return (what they wrote is kept in the cache, so starting it again goes on from there)
  POST /api/generate          {paper, lang, force}: queue a generation; one runs at a time. paper: an arXiv id or
                              link, a PMC or PPR id, a DOI (Europe PMC, epmc.py), or a stored document's id
  POST /api/import            {name, text, id?}: store a Markdown file as a paper (local.py); its id, to generate. With
                              id (a Markdown document stored before, md-…): its text replaced by this one under the same
                              id, its notes found again in it and its levels set aside ({paper, title, replaced: true};
                              not while it is being generated); then /api/generate with force folds it again, asking the
                              model only for the sections that changed
  GET  /api/models            saved configuration and the last model discovery results (no service probes); its "lang", the
                              reader's language as the page last told it (null until it has), for other programs
  GET  /api/langs             the languages a text can be read in: [{code, name}], each by its own name (langs.py)
  POST /api/models/refresh    refresh local model services in the background; GET /api/models reports progress/errors
  POST /api/models/select     {model, effort}: the model generations use; {task: "ask", model, effort}: the one questions
                              are answered with (none: the same) and its reasoning level (none: the default)
  POST /api/agents/scan       look for agents again (also done at startup)
  POST /api/providers/probe   {base, api_key | key, preset}: a provider's /models
  POST /api/providers/save    {key?, preset, name, base, api_key?, models}  ·  POST /api/providers/remove {key}
  POST /api/settings          {use_thoughtdag_env}, {pdf_tier}, {lang} (one of langs.py; else 400)  ·  POST /api/settings/test
                              {model?} or {task: "ask"}: one tiny call
  GET  /api/notes/<id>        the reader's notes on a paper (docs/notes-format.md)
  GET  /api/doc/<id>?lang=xx  the document in the TextFold format (docs/format.md), as `textfold get --json` writes it,
                              from its layers alone: nothing is generated, no model is called
  POST /api/files             a file dropped on the landing, its bytes (X-Name, X-Lang): a PDF (X-Refs: the papers it
                              names), or a Word, PowerPoint, Excel, EPUB, HTML file or picture: through the PDF engine
  GET  /api/pdf/<id>          the paper's original PDF (arXiv, PubMed Central, bioRxiv), fetched once and kept (pdf.py)
  POST /api/pdf/<id>          the PDF itself (the reader's copy, when the server would not give it): kept the same way
  POST /api/pdf-figures/<id>  an arXiv paper's drawings with words cut from its PDF (fetched once): {cut: how many};
                              {back: true}: as the HTML has them again
  POST /api/notes/<id>        {op: "put", note} or {op: "delete", id}
  POST /api/export            {paper, lang, langs: "current" | "all", kinds: ["hl", "note", "ask"], pdf}: the paper as one
                              HTML file: its levels, the reader's notes of the kinds chosen, and (pdf) its original PDF
                              with PDF.js, for the side-by-side view
  POST /api/ask               {paper, id, question, req}: the model answers a note's question; the answer is stored in it
  POST /api/ask/stop          {req}: that question stopped: its answer, when it comes, is not stored
  POST /api/papers/delete     {paper}: move a paper to papers/.trash, kept 30 days (not while it is being generated); the
                              list. Or {papers: [ids]}: each that can go; {papers: the list, deleted: [ids], busy: [ids]}
  POST /api/papers/restore    {papers: [ids]}: those deleted last back from papers/.trash, in their folders (the
                              reader's Undo); {papers: the list, folders, restored: [ids]}
  POST /api/papers/name       {paper, name}: what the reader calls it in the library ("": its own title again); the list
  GET  /api/folders           the reader's folders (inside one another) and which document is in which (library.py)
  POST /api/folders           {op: new | rename | delete | nest | move, ...}: one change; the folders after it
  GET  /api/bridge             the ChatGPT bridge (127.0.0.1:10531): running and its models, or what starting it needs
  POST /api/bridge/start       start it detached (bridges.py)
  POST /api/openrouter/start   {back}: the OpenRouter consent URL (OAuth PKCE); GET /oauth/openrouter is its return
  GET  /api/zotero             Zotero Desktop (its local API): running, its libraries, the one chosen (zotero.py)
  GET  /api/zotero/collections ?lib=users/0 | groups/<id>: the library's collections, each with its parent
  GET  /api/zotero/items       ?lib=…&collection=<key>&q=…: its items, each with the document it became, if any
  POST /api/zotero/library     {library}: the one to browse, kept
  POST /api/zotero/read        {item: "<lib>/<key>", lang}: an item read. {ref} for its paper online, generated as a
                               link is; else its file, taken as a file dropped on the landing is (/api/files' reply)

Loopback only, and POSTs from other origins are refused. A request addressed to any name but this computer's own
(localhost, 127.0.0.1, [::1]) is answered 421 and nothing else: a web page that points its own domain at 127.0.0.1 (DNS
rebinding) is the same origin as its requests to the browser, so only the name they carry tells them apart. Settings and
keys live in ~/.textfold/settings.json (mode 600), never in the project folder, which may be synced or shared.
"""
import base64
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .documents import ConflictError
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__, agents, bridges, build, engine, fetch, fold, ids, langs, library, llm, local, models, notes, office, pdf, pdfdoc, pdffigs, pdftext, pipeline, providers, store, zotero

STATIC = {"app.js": "text/javascript", "app.css": "text/css", "reader.js": "text/javascript",
          "reader.css": "text/css", "i18n.js": "text/javascript", "tutorial.js": "text/javascript", "webmcp.js": "text/javascript"}
# fonts and KaTeX, kept here so that no page asks another server for them (web/vendor)
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
VENDOR_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".woff2": "font/woff2",
                ".txt": "text/plain; charset=utf-8"}


def vendor_file(rel):
    """A file of web/vendor by its path under /static/vendor/, or None: nothing outside it, nothing but those kinds."""
    root = (build.WEB / "vendor").resolve()
    f = (root / rel).resolve()
    return f if rel and root in f.parents and f.is_file() and f.suffix in VENDOR_TYPES else None


# Only an explicit refresh contacts local services. Page rendering reads the last results,
# and a failed probe leaves that service's previous result available.
DISCOVERY = {"live": {"ollama": [], "bridge": None}, "bridge": {"running": False, "node": bool(shutil.which("npx"))},
             "refreshing": False, "ready": False, "error": ""}
DISCOVERY_LOCK = threading.Lock()


def refresh_models():
    with DISCOVERY_LOCK:
        if DISCOVERY["refreshing"]:
            return
        DISCOVERY["refreshing"] = True

    def refresh():
        live = dict(DISCOVERY["live"])
        status = DISCOVERY["bridge"]
        errors = []
        try:
            if not os.environ.get("TEXTFOLD_NO_MODELS"):
                try:
                    live["ollama"] = providers.ollama_models(strict=True)
                except Exception:
                    errors.append("Ollama")
                try:
                    status = bridges.bridge_status(fresh=True, strict=True)
                    live["bridge"] = bridges.bridge_provider(providers.NOT_TEXT, status)
                except Exception:
                    errors.append("ChatGPT bridge")
        finally:
            with DISCOVERY_LOCK:
                DISCOVERY.update(live=live, bridge=status, ready=DISCOVERY["ready"] or not errors,
                                 error=", ".join(errors), refreshing=False)

    threading.Thread(target=refresh, daemon=True).start()


def cloud_storage_status():
    from . import cloud_settings
    return cloud_settings.status(store.HOME, store.repository().backend)


def models_payload():
    s = models.load_settings()
    with DISCOVERY_LOCK:
        discovery = dict(DISCOVERY)
    cat, provs = models.catalog(s, discovery["live"])
    # While the agents are still being looked for, or the local services not yet asked, a saved choice missing from the
    # list is not known to be gone: it shows as saved, and the reader's choice is not switched under them. Once both are
    # known, one that is gone gives way as a generation's does (models.default_model), so the page names the model the
    # next generation uses.
    if agents.STATE["scanning"] or not agents.STATE["scanned_at"] or not discovery["ready"]:
        for mid in (s.get("model"), s.get("ask_model")):
            if mid and not any(m["id"] == mid for m in cat):
                cat.append({"id": mid, "name": mid, "kind": "api", "group": "saved", "efforts": []})
    selected = models.default_model(s, cat)
    ask, ask_effort = models.ask_choice(s, cat)
    name = lambda mid: next((m["name"] for m in cat if m["id"] == mid), mid)
    return {
        "models": cat, "selected": selected, "selectedName": name(selected),
        "askModel": ask, "askEffort": ask_effort, "askName": name(ask) if ask and ask != selected else "",
        "efforts": s.get("efforts") or {}, "bridge": discovery["bridge"],
        "discovery": {k: discovery[k] for k in ("refreshing", "ready", "error")},
        "agents": [{k: a[k] for k in ("runtime", "label", "binary", "path", "version", "runnable", "error")} | {"models": len(a["models"])}
                   for a in agents.STATE["agents"]],
        "scanning": agents.STATE["scanning"], "scannedAt": agents.STATE["scanned_at"],
        "providers": [{"key": p["key"], "name": p["name"], "source": p["source"], "base": p["base"], "protocol": p["protocol"],
                       "preset": p.get("preset"), "models": [m["id"] for m in p["models"]],
                       "hasKey": bool(p["api_key"]) and p["api_key"] not in ("none", "ollama")} for p in provs],
        "presets": providers.PRESETS[:-1] + [models.ANTHROPIC_PRESET, providers.PRESETS[-1]],
        "useThoughtdagEnv": s.get("use_thoughtdag_env", True), "thoughtdagEnv": providers.THOUGHTDAG_ENV.exists(),
        "pdfTier": s.get("pdf_tier") or "fast", "engine": engine.status(), "storage": store.storage_status(),
        "cloudStorage": cloud_storage_status(),
        "lang": s.get("lang") if s.get("lang") in langs.LANGS else None,
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
                w = json.loads(store.read_text(side))
                f = side.parent / Path(w.get("file") or side.stem + ".pdf").name
                if f.exists() and not store.has(f.stem):
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

    def _same(self, **kw):   # the same request already waiting or running (and not being stopped) is that job
        return next((j for j in self.items.values() if j["status"] in ("queued", "running") and not j.get("stopping")
                     and all(j.get(k) == v for k, v in kw.items())), None)

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
        Path(file).with_suffix(".json").write_text(json.dumps({"name": name, "lang": lang, "file": Path(file).name}, ensure_ascii=False), encoding="utf-8")

    def read_pdf(self, pid, file, name, lang):
        """A PDF that has its words in it, read by TextFold itself while the engine is not installed (pdftext.py), the
        file gone from uploads/ once it is stored: its levels' job, or None when it needs the engine (a scan)."""
        if not pdftext.save(pid, file, name):
            return None
        Path(file).unlink(missing_ok=True)
        return self.submit(pid, lang, False)

    def install_engine(self):
        return self._same(kind="install") or self._new(self.eq, kind="install", title="PDF engine")

    def superseded(self, pid):
        """A document about to be converted again: the languages it has and is being generated in, the generations of
        its old text stopped (what they would write belongs to that text: a translation of another text's paragraphs,
        laid on the new ones by their places)."""
        langs = (["en"] if store.read(pid, "en") else []) + store.languages(pid)
        for j in list(self.items.values()):
            if j["kind"] == "generate" and j["pid"] == pid and j["status"] in ("queued", "running"):
                langs.append(j["lang"])
                self.stop(j["id"])
        return list(dict.fromkeys(langs))

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
            if job["kind"] == "generate" and stage not in ("queued", "fetch") and store.has(job["pid"]):
                if not job.get("title"):   # a new paper's title, once it is parsed
                    try:
                        job["title"] = store.document(job["pid"]).meta["title"]
                    except (OSError, ValueError, KeyError):
                        pass
                if job.get("attach") and not job.get("attached"):   # the reader's own copy of an online paper's PDF
                    try:
                        if not pdf.present(job["pid"]):
                            pdf.keep(job["pid"], Path(job["attach"]).read_bytes())
                        # Never delete the only uploaded original while its
                        # copy still exists only in the generation workspace.
                        if not store.repository().checkpoint(job["pid"]):
                            return
                        job["attached"] = True
                        Path(job["attach"]).unlink()
                    except OSError:
                        pass
        return progress

    def _log(self, job):
        def log(line):
            job["log"] = (job["log"] + [line])[-40:]
        return log

    def _end(self, job, e, stop):
        if job["pid"] and store.readable(job["pid"]):
            job["readable"] = True   # what failed was its levels: it reads as written
        if isinstance(e, (llm.Stopped, engine.Stopped)) or stop.is_set():
            job.update(status="stopped", stopping=False)
        else:
            job.update(status="error", error="no-model" if str(e).startswith("No model is available") else str(e))
            self._log(job)(traceback.format_exc()[-800:])
            print(f"job {job['id']} {job['pid']} {job['lang']} failed:\n" + "\n".join(job["log"][-12:]), flush=True)
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
                if str(e) == "no-html" and not stop.is_set():
                    try:   # an arXiv paper without its HTML version: its PDF (the reader's, else arXiv's), read here
                        if job.get("attach"):
                            self._fallback(job)
                        else:
                            self._pdf_link(job, f"https://arxiv.org/pdf/{job['pid']}")
                        continue
                    except Exception:   # arXiv's PDF out of reach too: the app's own sentence for no HTML
                        pass
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

    def _pdf_link(self, job, url=None):
        url = url or store.document(job["pid"]).source.reference.strip()
        with urllib.request.urlopen(urllib.request.Request(url, headers=fetch.UA), timeout=90) as r:
            data = r.read(pdf.LIMIT + 1)
        if data[:5] != b"%PDF-" or len(data) > pdf.LIMIT:
            raise ValueError("not-pdf")
        store.discard_document(job["pid"])   # (only its address was there)
        pid = pdfdoc.pid_of(data)
        name = Path(urllib.parse.urlparse(url).path).name or "document.pdf"
        f = store.DATA / "uploads" / f"{pid}.pdf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        job["finished"] = time.time()
        if store.has(pid):   # read before
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
                    langs = self.superseded(job["pid"]) if job["replace"] else []
                    pdfdoc.store_pdf(job["pid"], out, pdfdoc.read_output(out), job["name"], job["tier"], replace=job["replace"])
                    store.keep_original(job["pid"], Path(job["file"]).suffix.lower().lstrip("."),
                                        Path(job["file"]).read_bytes(), only_if_missing=True)
                    shutil.rmtree(out, ignore_errors=True)
                    if Path(job["file"]).parent == store.DATA / "uploads":
                        Path(job["file"]).unlink(missing_ok=True)
                        Path(job["file"]).with_suffix(".json").unlink(missing_ok=True)
                    PAGES.pop(job["pid"], None)
                    job["readable"] = True
                    job["next"] = self.submit(job["pid"], job["lang"] or "en", False)["id"]   # its levels, in the other queue
                    for lang in langs:   # converted again: its other languages made again of the new text (English first)
                        if lang not in ("en", job["lang"] or "en"):
                            self.submit(job["pid"], lang, False)
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
    if store.has(pid):   # this file, converted before
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
    if store.has(pid):   # this file, converted before
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


def assembled(pid, only=None):
    """The paper as the reader needs it (store.assemble), under the name the reader gave it in the library, if any."""
    doc = store.assemble(pid, only)
    name = library.name_of(pid)
    if name:
        doc["meta"]["name"] = name
    gone = ids.load(pid)   # (a link to a paragraph or section no longer in the text opens where it went)
    if gone["units"] or gone["sections"]:
        doc["gone"] = gone
    return doc


def reader_page(pid):
    repo = store.repository()
    sig = (repo.cache_token,) + repo.revision(pid)
    sig += ((build.WEB / "template.html").stat().st_mtime, library.name_of(pid))  # a changed page template, or name, re-renders too
    sig += tuple((name, (build.WEB / name).stat().st_mtime_ns) for name in STATIC)
    hit = PAGES.get(pid)
    if not hit or hit[0] != sig:
        hit = (sig, build.page(assembled(pid), app=True))
        PAGES[pid] = hit
    return hit[1]


def listed():
    """The papers for the list: those on disk, with when each came in and was last opened, and the citekey of each one
    read from a Zotero item that has one."""
    index = store.repository().list()
    rows = library.stamp(store.papers(index), index=index)
    keys = zotero.citekeys()
    for p in rows:
        if keys.get(p["id"]):
            p["citekey"] = keys[p["id"]]
    return rows


def landing_page(jobs):
    # The agents on this computer are looked for as the server starts, in a second or two: the first page waits that
    # long for them, so it names the model in use rather than asking for one to be connected
    agents.wait_scanned(2)
    data = models_payload()
    from .cloud_storage import StorageError
    storage_error = ""
    try:
        papers, folders = listed(), library.folders()
    except StorageError as error:
        # The settings page must remain usable when a cloud service/keyring is
        # unavailable. This is an explicit error state, never a local fallback.
        papers, folders, storage_error = [], {"folders": []}, str(error)
    boot = {"papers": papers, "folders": folders, "storageError": storage_error, "jobs": jobs.list(), "model": data["selectedName"] or "",
            "askName": data["askName"], "modelData": data,
            "bridge": data["bridge"], "langs": langs.natives()}
    t = build.app_assets((build.WEB / "app.html").read_text(encoding="utf-8"))
    return t.replace("{{HEAD}}", build.fonts_head(build.APP_ASSETS)).replace("{{BOOT}}", json.dumps(boot, ensure_ascii=False).replace("</", "<\\/"))


LOCAL_NAMES = ("localhost", "127.0.0.1", "[::1]")


def addressed_here(host):
    """Whether a request's Host header names this computer (any port): what a page on another site cannot make its
    browser send, even once its domain points at 127.0.0.1."""
    m = re.fullmatch(r"(\[[0-9a-f:.]+\]|[^:\[\]]+)(?::\d{1,5})?", (host or "").strip().lower())
    return bool(m) and m.group(1) in LOCAL_NAMES


def make_handler(jobs):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            if 200 <= code < 300 and self.command == "POST" and (self.path.startswith("/api/notes/") or self.path == "/api/ask"):
                from .offline_storage import OfflineBackend
                backend = store.repository().backend
                if isinstance(backend, OfflineBackend):
                    status = backend.status()
                    self.send_header("X-TextFold-Save-State", "local" if status["pending"] else "synced")
                    self.send_header("X-TextFold-Online", "1" if status["online"] else "0")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass   # (the reader stopped waiting: a question stopped, a page left)

        def json(self, obj, code=200):
            self.send(code, json.dumps(obj, ensure_ascii=False))

        def do_GET(self):
            if not addressed_here(self.headers.get("Host")):   # (DNS rebinding: the module's docstring)
                return self.json({"error": "misdirected"}, 421)
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
                    name = m.group(2)
                    kind = IMAGE_TYPES.get(Path(name).suffix.lower())
                    data = store.document(m.group(1)).images.get(name) if store.has(m.group(1)) else None
                    if kind and data is not None:
                        return self.send(200, data, kind, cache="max-age=86400")
                    return self.json({"error": "no such picture"}, 404)
                m = re.fullmatch(r"/p/([\w.-]+)", u.path)
                if m and store.valid_id(m.group(1)):
                    if not store.readable(m.group(1)):
                        self.send_response(302)
                        self.send_header("Location", "/")
                        self.end_headers()
                        return
                    library.opened(m.group(1))
                    return self.send(200, reader_page(m.group(1)), "text/html; charset=utf-8")
                if u.path.startswith("/static/") and u.path[8:] in STATIC:
                    data = (build.WEB / u.path[8:]).read_bytes()
                    version = hashlib.sha256(data).hexdigest()
                    cache = "private, max-age=31536000, immutable" if parse_qs(u.query).get("v") == [version] else "no-store"
                    return self.send(200, data, STATIC[u.path[8:]] + "; charset=utf-8", cache=cache)
                if u.path.startswith("/static/vendor/"):
                    f = vendor_file(unquote(u.path[len("/static/vendor/"):]))
                    if f:
                        return self.send(200, f.read_bytes(), VENDOR_TYPES[f.suffix], cache="max-age=86400")
                if u.path == "/api/papers":
                    return self.json(listed())
                if u.path == "/api/folders":
                    return self.json(library.folders())
                if u.path == "/api/storage":
                    return self.json(store.storage_status())
                if u.path == "/api/storage/cloud":
                    return self.json(cloud_storage_status())
                if u.path in ("/api/storage/sync", "/api/storage/conflicts", "/api/storage/export"):
                    from .offline_storage import OfflineBackend
                    backend = store.repository().backend
                    if not isinstance(backend, OfflineBackend):
                        return self.json(notes.conflicts() if u.path.endswith("/conflicts") else None)
                    if u.path.endswith("/conflicts"):
                        return self.json(backend.conflicts())
                    if u.path.endswith("/export"):
                        return self.send(200, backend.export_pending(), "application/zip")
                    return self.json(backend.status())
                if u.path == "/api/langs":
                    return self.json([{"code": code, "name": name} for code, name in langs.natives().items()])
                if u.path == "/api/jobs":
                    return self.json(jobs.list())
                if u.path in ("/api/models", "/api/settings"):
                    return self.json(models_payload())
                m = re.fullmatch(r"/api/pdfmap/([\w.-]+)", u.path)   # a converted PDF's paragraphs on its pages
                if m and m.group(1).startswith("pdf-") and store.valid_id(m.group(1)) and store.has(m.group(1)):
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
                if m and store.valid_id(m.group(1)) and store.has(m.group(1)):
                    return self.json(notes.present(notes.read(m.group(1))))
                m = re.fullmatch(r"/api/doc/([\w.-]+)", u.path)   # (a document not folded yet: its local levels, said so)
                if m:
                    pid, lang = m.group(1), (parse_qs(u.query).get("lang") or ["en"])[0]
                    if not (store.valid_id(pid) and store.readable(pid) and store.has(pid)):
                        return self.json({"error": "no such paper"}, 404)
                    if lang not in langs.LANGS:
                        return self.json({"error": f"unknown language {lang}"}, 400)
                    return self.json(fold.document(pid, lang))
                m = re.fullmatch(r"/api/ids/([\w.-]+)", u.path)   # what paragraphs' and sections' ids name now (ids.py)
                if m:
                    pid, q = m.group(1), parse_qs(u.query)
                    if not (store.valid_id(pid) and store.readable(pid)):
                        return self.json({"error": "no such paper"}, 404)
                    asked = lambda k: [x for v in q.get(k, []) for x in v.split(",") if x][:1000]
                    return self.json(ids.lookup(pid, store.parsed(pid), asked("u"), asked("s")))
                m = re.fullmatch(r"/api/pdf/([\w.-]+)", u.path)
                if m and store.valid_id(m.group(1)) and store.has(m.group(1)):
                    f = pdf.read(m.group(1)) if pdf.has_source(m.group(1)) else None
                    if not f:   # with where the reader may open it, to drop it on the pane
                        return self.json({"error": "no-pdf", "open": pdf.where(m.group(1)) if pdf.has_source(m.group(1)) else None}, 404)
                    return self.send(200, f, "application/pdf", cache="max-age=86400")
                if u.path == "/favicon.ico":
                    return self.send(204, b"", "image/x-icon")
                self.json({"error": "not found"}, 404)
            except Exception as e:
                self.json({"error": str(e)}, 500)

        def do_POST(self):
            if not addressed_here(self.headers.get("Host")):
                return self.json({"error": "misdirected"}, 421)
            u = urlparse(self.path)
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1"):
                return self.json({"error": "forbidden"}, 403)  # another site may not drive this server
            try:
                size = int(self.headers.get("Content-Length") or 0)
                m = re.fullmatch(r"/api/pdf/([\w.-]+)", u.path)
                if m:   # a paper's PDF, dropped on the reader's pane: the file's bytes, not JSON
                    if not (store.valid_id(m.group(1)) and store.has(m.group(1))):
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
                if u.path == "/api/storage/sync":
                    from .offline_storage import OfflineBackend
                    backend = store.repository().backend
                    if not isinstance(backend, OfflineBackend):
                        if body.get("action") == "resolve" and store.valid_id(body.get("document", "")):
                            try:
                                notes.resolve(body["document"], str(body.get("conflict", "")), body.get("choice"), body.get("text"))
                                return self.json(None)
                            except (ValueError, OSError, ConflictError) as error:
                                return self.json({"error": str(error)}, 400)
                        return self.json({"error": "Select cloud storage and restart first"}, 400)
                    try:
                        action = body.get("action", "sync")
                        if action == "sync":
                            reason = body.get('reason', 'manual')
                            backend.request_sync(reason if reason in ('manual', 'online', 'focus') else 'manual')
                        elif action in ("import", "download"):
                            from .storage_settings import active_path
                            backend.background(action, store.LocalDirectoryBackend(active_path(store.DATA, store.HOME)) if action == "import" else None)
                        elif action == "clear":
                            return self.json(backend.clear_cache())
                        elif action == "resolve":
                            backend.resolve(str(body.get("document", "")), str(body.get("conflict", "")), body.get("choice"), body.get("text"))
                        else:
                            raise ValueError("Unknown storage action")
                        return self.json(backend.status())
                    except (ValueError, OSError, ConflictError) as error:
                        return self.json({"error": str(error)}, 400)
                if u.path in ("/api/storage/cloud", "/api/storage/cloud/test"):
                    from . import cloud_settings
                    try:
                        if not isinstance(body, dict):
                            raise ValueError("Expected cloud storage settings")
                        result = cloud_settings.test(store.HOME, body) if u.path.endswith("/test") else cloud_settings.save(store.HOME, body, store.repository().backend)
                        return self.json(result)
                    except (ValueError, OSError) as error:
                        return self.json({"error": str(error)}, 400)
                if u.path == "/api/storage":
                    try:
                        return self.json(store.schedule_storage(body.get("path"), bool(body.get("cancel"))))
                    except (ValueError, OSError) as e:
                        return self.json({"error": str(e)}, 400)
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
                            zotero.link(lib, key, pid, got["title"], got["record"])
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
                    original = pdfdoc.original(pid) if pid.startswith(("pdf-", "doc-")) and store.valid_id(pid) and store.has(pid) else None
                    source = "document." + original[0] if original else None
                    if not source or tier not in engine.TIERS or engine.tier_for(source, tier) != tier:
                        return self.json({"error": "no such PDF"}, 404)
                    if not engine.where():
                        return self.json({"error": "needs-engine"}, 409)
                    # A background converter needs a durable local input after this request ends.
                    src = store.DATA / "uploads" / f"{pid}-again{Path(source).suffix}"
                    src.parent.mkdir(parents=True, exist_ok=True)
                    src.write_bytes(original[1])
                    name = store.document(pid).meta.get("source", {}).get("name") or "document.pdf"
                    return self.json(jobs.convert(pid, src, name, body.get("lang") or "en", tier, replace=True))
                if u.path == "/api/jobs/stop":
                    job = jobs.stop(body.get("id", ""))
                    return self.json(job) if job else self.json({"error": "no such job"}, 404)
                if u.path == "/api/import":
                    text, name = str(body.get("text") or ""), str(body.get("name") or "document.md")[:200]
                    if not text.strip():
                        return self.json({"error": "empty"}, 400)
                    if body.get("id"):
                        return self.json({"error": "Original source is immutable; import changed content as a new document"}, 409)
                    try:
                        pid = local.import_text(text, name)
                    except ValueError:
                        return self.json({"error": "not-markdown"}, 400)
                    return self.json({"paper": pid, "title": store.document(pid).meta["title"]})
                if u.path == "/api/export":   # the paper as one file, saved by the reader for themself
                    pid, lang = str(body.get("paper") or ""), str(body.get("lang") or "en")
                    if not store.valid_id(pid) or not store.has(pid):
                        return self.json({"error": "no such paper"}, 404)
                    kinds = {k for k in body.get("kinds") or [] if k in ("hl", "note", "ask")}
                    doc = assembled(pid, only=[lang] if body.get("langs") == "current" else None)
                    chosen = [n for n in notes.read(pid)["notes"] if notes.kind(n) in kinds]
                    saved = {"notes": chosen, "saved": uuid.uuid4().hex[:12], "lang": lang}
                    if body.get("pdf") and pdf.has_source(pid):   # the original beside it: the file, and PDF.js to show it
                        f = pdf.read(pid)
                        if f:
                            saved["pdf"] = base64.b64encode(f).decode()
                            saved["pdfjs"] = {"lib": (build.VENDOR / "pdfjs" / "pdf.min.mjs").read_text(encoding="utf-8"),
                                              "worker": (build.VENDOR / "pdfjs" / "pdf.worker.min.mjs").read_text(encoding="utf-8")}
                            if pid.startswith("pdf-"):   # a converted PDF: its paragraphs' places on the pages
                                with RELAYOUT:
                                    try:
                                        pdfdoc.relayout(pid)
                                    except Exception:
                                        pass
                                saved["pdfmap"] = pdfdoc.place(pid)
                    html = build.page(doc, app=False, saved=saved)
                    return self.send(200, html, "text/html; charset=utf-8")
                m = re.fullmatch(r"/api/pdf-figures/([\w.-]+)", u.path)   # the button under a drawing set out of place
                if m and store.valid_id(m.group(1)) and store.has(m.group(1)):
                    if body.get("back"):
                        pdffigs.back(m.group(1))
                        PAGES.pop(m.group(1), None)
                        return self.json({"cut": 0})
                    try:
                        n = pdffigs.from_pdf(m.group(1))
                    except LookupError:
                        return self.json({"error": "no-pdf"}, 502)
                    PAGES.pop(m.group(1), None)
                    return self.json({"cut": n})
                m = re.fullmatch(r"/api/notes/([\w.-]+)", u.path)
                if m and store.valid_id(m.group(1)) and store.has(m.group(1)):
                    try:
                        if body.get("op") == "delete":
                            return self.json(notes.present(notes.delete(m.group(1), str(body.get("id", "")), body.get("base"))))
                        return self.json(notes.present(notes.put(m.group(1), body.get("note"))))
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
                    many = isinstance(body.get("papers"), list)
                    pids = [str(p) for p in body["papers"]] if many else [str(body.get("paper", ""))]
                    there = [p for p in dict.fromkeys(pids) if store.valid_id(p) and store.has(p)]
                    live = {j["pid"] for j in jobs.list() if j["status"] in ("queued", "running")}
                    if not many and not there:
                        return self.json({"error": "no such paper"}, 404)
                    if not many and there[0] in live:
                        return self.json({"error": "busy"}, 409)
                    deleted = [p for p in there if p not in live]
                    for pid in deleted:   # (its place in the library goes with it, for an Undo)
                        store.trash(pid, library.forget(pid))
                        PAGES.pop(pid, None)
                    if not many:
                        return self.json(listed())
                    return self.json({"papers": listed(), "deleted": deleted, "busy": [p for p in there if p in live]})
                if u.path == "/api/papers/restore":
                    pids = body.get("papers") if isinstance(body.get("papers"), list) else []
                    restored = []
                    for pid in dict.fromkeys(str(p) for p in pids):
                        kept = store.untrash(pid)
                        if kept is not None:
                            library.remember(pid, kept)
                            restored.append(pid)
                    return self.json({"papers": listed(), "folders": library.folders(), "restored": restored})
                if u.path == "/api/papers/name":
                    pid = str(body.get("paper", ""))
                    if not store.valid_id(pid) or not store.has(pid):
                        return self.json({"error": "no such paper"}, 404)
                    library.name(pid, body.get("name"))
                    return self.json(listed())
                if u.path == "/api/folders":
                    try:
                        return self.json(library.change(body))
                    except ValueError as e:
                        return self.json({"error": str(e)}, 400)
                if u.path == "/api/ask/stop":
                    notes.stop(str(body.get("req") or "")[:40])
                    if store.valid_id(body.get("paper", "")) and body.get("id"):
                        notes.cancel_question(body["paper"], body["id"], str(body.get("req") or "")[:40])
                        return self.json(notes.present(notes.read(body["paper"])))
                    return self.json({"ok": True})
                if u.path == "/api/ask":
                    pid = str(body.get("paper", ""))
                    if not store.valid_id(pid) or not store.has(pid):
                        return self.json({"error": "no such paper"}, 404)
                    try:
                        return self.json(notes.ask(pid, str(body.get("id", "")), str(body.get("question", "")).strip()[:2000],
                                                   models.ask_spec(), ui=str(body.get("ui") or ""),
                                                   req=str(body.get("req") or "")[:40] or None, parent=body.get("parent")))
                    except notes.Stopped:
                        return self.json({"error": "stopped"}, 409)
                    except ValueError as e:
                        return self.json({"error": str(e)}, 400)
                    except Exception as e:  # the model failed: the note keeps its question, the reader can ask again
                        return self.json({"error": str(e)[:600]}, 502)
                if u.path == "/api/models/select" and body.get("task") == "ask":
                    s["ask_model"] = body.get("model") or None
                    s["ask_effort"] = body.get("effort") or None
                    models.save_settings(s)
                    return self.json(models_payload())
                if u.path == "/api/models/select":
                    s["model"] = body.get("model") or None
                    if "effort" in body and s["model"]:
                        if body["effort"]:
                            s["efforts"][s["model"]] = body["effort"]
                        else:
                            s["efforts"].pop(s["model"], None)
                    models.save_settings(s)
                    return self.json(models_payload())
                if u.path == "/api/models/refresh":
                    refresh_models()
                    return self.json(models_payload())
                if u.path == "/api/agents/scan":
                    try:
                        agents.scan()
                    finally:
                        agents.STATE["scanning"] = False
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
                    if "lang" in body:   # the reader's language, which the page keeps itself: for other programs to read
                        if body["lang"] not in langs.LANGS:
                            return self.json({"error": f"unknown language {body['lang']}"}, 400)
                        s["lang"] = body["lang"]
                    if "use_thoughtdag_env" in body:
                        s["use_thoughtdag_env"] = bool(body["use_thoughtdag_env"])
                    if body.get("pdf_tier") in engine.TIERS:
                        s["pdf_tier"] = body["pdf_tier"]
                    models.save_settings(s)
                    return self.json(models_payload())
                if u.path == "/api/settings/test":
                    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
                    try:
                        spec = models.ask_spec() if body.get("task") == "ask" else models.resolve(body.get("model"))   # (questions: their model, reasoning as set)
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


def announce(port):
    """server.json in the data folder: where this server is, for the programs that work with it (an editor's plugin, a
    script), which could not tell the desktop app's free port from a server run by hand on 3017. Replaced whole, so a
    program reading it never sees half of it. A server ended outright (on Windows, the app's server.kill(), which sends
    no signal) leaves it behind: its url then answers nothing."""
    f = store.DATA / "server.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"server.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"format": "textfold/server", "version": 1, "url": f"http://127.0.0.1:{port}", "port": port,
                               "pid": os.getpid(), "textfold": __version__,
                               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=2), encoding="utf-8")
    os.replace(tmp, f)
    return f


def withdraw(f):
    """server.json removed when this server stops, if it is still this one's: a server started since on the same
    data folder has written its own, and that one stays."""
    try:
        if json.loads(f.read_text(encoding="utf-8")).get("pid") == os.getpid():
            f.unlink()
    except (OSError, ValueError):
        pass


def serve(port=3017):
    if os.name == "nt":
        _quiet_children()
    store.initialize_storage()
    from .offline_storage import OfflineBackend
    backend = store.repository().backend
    if isinstance(backend, OfflineBackend):
        backend.start()
    jobs = Jobs()
    # look for the agents on this computer while the server comes up, and erase what was deleted 30 days ago
    threading.Thread(target=agents.scan, kwargs={"log": print}, daemon=True).start()
    threading.Thread(target=store.empty_trash, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(jobs))
    port = httpd.server_address[1]   # (the one given, or the free one the system chose for 0)

    def killed(signum, frame):
        # the desktop app's server.kill(): server.json goes, then the server, at once as before (stopping as ^C does
        # would wait for the model calls under way, since Python's exit waits for a thread pool's work)
        withdraw(f)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    f = announce(port)
    try:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, killed)
        print(f"TextFold on http://localhost:{port}")
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if isinstance(backend, OfflineBackend):
            backend.close()
        withdraw(f)
