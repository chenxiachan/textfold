"""The PDF engine: MinerU, pinned, in a Python of its own under the system's folder for app data (engine/ beside the
papers of an installed TextFold and the desktop app's), installed when the reader asks for it and removed the same way.
Nothing of it goes into TextFold's own Python: it is about 3.4 GB (torch, and 2 GB of models) and needs Python 3.10+.
Besides PDFs it reads Word, PowerPoint, Excel, OpenDocument, EPUB and HTML files and pictures (FILES).

TextFold runs its command line, mineru-kit, never the `mineru` document library (which keeps an index of the reader's
files and asks to send usage data). Two tiers: fast (MinerU's flash: seconds, no model; formulas come out as pictures,
some headings as text) and accurate (advanced: a vision model, several seconds a page; formulas as LaTeX, tables
right). Image analysis stays off: on, it turns charts into data tables and diagrams into mermaid code."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from . import store

VERSION = "4.0.10"
TIERS = {"fast": "flash", "accurate": "advanced"}
MARK = "installed.txt"   # written last: an engine folder without it is an install that did not finish
# the files it reads besides PDFs (MinerU's own list, mineru/filetypes.py), by their extension, with the name a reader
# knows them by. A picture is read like a scanned page, in either tier; the others have one way to be read (the fast
# tier: their text and structure are in the file, no model is needed)
PICTURES = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "jp2"}
FILES = {"docx": "Word", "doc": "Word", "rtf": "RTF", "pptx": "PowerPoint", "ppt": "PowerPoint", "xlsx": "Excel", "xls": "Excel",
         "odt": "OpenDocument", "ods": "OpenDocument", "odp": "OpenDocument", "epub": "EPUB", "html": "HTML", "htm": "HTML",
         "mhtml": "MHTML", "mht": "MHTML", "csv": "CSV", "tsv": "TSV", "ofd": "OFD"} | {x: "Image" for x in PICTURES}


def kind_of(name):
    """A file's extension as the engine knows it ("tif" is its "tiff"), or None for a file it does not read."""
    ext = Path(name).suffix.lower().lstrip(".")
    ext = {"tif": "tiff", "shtml": "html"}.get(ext, ext)
    return ext if ext == "pdf" or ext in FILES else None


def tier_for(file, tier):
    """The tier a file is read in: a PDF's or a picture's as asked, any other file's the fast one."""
    ext = Path(str(file)).suffix.lower().lstrip(".")
    return tier if ext == "pdf" or ext in PICTURES else "fast"


class Stopped(Exception):
    pass


def home():
    return store.app_data() / "engine"


def legacy():
    """Where PaperFold installed it (kept there: 3.4 GB is not moved)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PaperFold" / "engine"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PaperFold" / "engine"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "paperfold" / "engine"


def _bin(e, name):
    return e / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / (name + (".exe" if sys.platform == "win32" else ""))


def _ready(e):
    return _bin(e, "mineru-kit").exists() and (e / "mineru-home" / "models").exists() and ((e / MARK).exists() or e == legacy())


def where():
    """The installed engine's folder, or None."""
    return next((e for e in (home(), legacy()) if _ready(e)), None)


def _env(e):
    return {**os.environ, "MINERU_HOME": str(e / "mineru-home"), "UV_PYTHON_INSTALL_DIR": str(e / "python"),
            "UV_CACHE_DIR": str(e / ".uv-cache"), "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
            "PYTHONUNBUFFERED": "1", "TQDM_MININTERVAL": "0.5"}


_size = {}


def _du(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def status():
    e = where()
    out = {"installed": bool(e), "path": str(e or home()), "version": VERSION, "tiers": list(TIERS),
           "supported": sys.platform in ("darwin", "linux", "win32"), "legacy": bool(e and e == legacy())}
    if e:
        key = (str(e), (e / "venv").stat().st_mtime)
        if key not in _size:   # the folder is large; counted once
            _size.clear()
            _size[key] = _du(e)
        out["bytes"] = _size[key]
    return out


def _run(cmd, env, log, stop, on_line=None):
    """A step of the install or a conversion: its output to the job's log (tqdm's bars, split on carriage returns,
    to on_line); stopped at once when the reader stops the job."""
    p = subprocess.Popen([str(c) for c in cmd], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if stop is not None:   # a model that hangs on a page prints nothing for minutes: stopped from outside the read
        def watch():
            while p.poll() is None:
                if stop.wait(0.5):
                    p.terminate()
                    return
        threading.Thread(target=watch, daemon=True).start()
    buf, last = b"", ""
    try:
        while True:
            chunk = p.stdout.read1(4096)
            if not chunk:
                break
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for raw in lines:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if on_line:
                    on_line(line)
                if line != last and not re.search(r"\d+%\|", line):   # bars go to on_line only, not the log
                    log(line[-300:])
                last = line
        code = p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    if stop is not None and stop.is_set():
        raise Stopped()
    if code:
        raise RuntimeError(f"{Path(str(cmd[0])).name} exited with {code}")


def _uv(e, log, stop):
    """uv, the installer of the engine's Python and packages: the one on this computer, or its own copy (pip)."""
    found = shutil.which("uv")
    if found:
        return found
    target = e / "uv"
    _run([sys.executable, "-m", "pip", "install", "--quiet", "--target", target, "uv"], _env(e), log, stop)
    for cand in (target / "bin" / "uv", target / "bin" / "uv.exe", target / "Scripts" / "uv.exe"):
        if cand.exists():
            return str(cand)
    raise RuntimeError("uv could not be installed")


def install(progress=lambda *a: None, log=lambda *a: None, stop=None):
    """MinerU and its models into home(): about 20 minutes and 3.4 GB. An interrupted install starts again from the
    step it stopped at (each step is idempotent)."""
    e = home()
    e.mkdir(parents=True, exist_ok=True)
    env = _env(e)
    steps = 4
    progress("engine-uv", 0, steps)
    uv = _uv(e, log, stop)
    progress("engine-python", 1, steps)
    if not _bin(e, "python").exists():
        _run([uv, "venv", e / "venv", "--python", "3.12", "--python-preference", "only-managed"], env, log, stop)
    progress("engine-packages", 2, steps)
    _run([uv, "pip", "install", "--python", _bin(e, "python"), f"mineru=={VERSION}"], env, log, stop)
    progress("engine-models", 3, steps)
    _run([_bin(e, "mineru-kit"), "models", "download", "--tier", "standard"], env, log, stop)
    shutil.rmtree(e / ".uv-cache", ignore_errors=True)
    (e / MARK).write_text(f"mineru=={VERSION}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    progress("engine-models", steps, steps)
    return status()


def uninstall():
    e = where() or (home() if home().exists() else None)
    if e:
        shutil.rmtree(e)
    _size.clear()
    return status()


# the accurate tier's page by page pass, its long one (the others count boxes, formulas or weights)
BAR = re.compile(r"^Two Step Extraction:\s+\d+%\|.*?\|\s*(?P<done>\d+)/(?P<total>\d+)")


def _whole(z):
    """Whether the engine's zip was written to its end, with the document in it."""
    try:
        with zipfile.ZipFile(z) as f:
            return "structured_content.json" in f.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def convert(pdf, out, tier="fast", progress=lambda *a: None, log=lambda *a: None, stop=None):
    """A PDF (or another file the engine reads: FILES) converted into out/ (MinerU's zip, unpacked:
    structured_content.json, images/; and a PDF itself, as input.pdf). Progress by page, from
    the model's own bars (the accurate tier's last pass, "Two Step Extraction", is the long one)."""
    e = where()
    if e is None:
        raise RuntimeError("needs-engine")
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    z = out / "result.zip"
    progress("convert", 0, 0)

    def on_line(line):
        m = BAR.match(line)
        if m and int(m.group("total")):
            progress("convert", int(m.group("done")), int(m.group("total")))

    tier = tier_for(pdf, tier)
    cmd = [_bin(e, "mineru-kit"), "parse", pdf, "-o", z, "--format", "zip", "--tier", TIERS.get(tier, tier)]
    if TIERS.get(tier, tier) == "advanced":
        cmd.append("--disable-image-analysis")
    try:
        _run(cmd, _env(e), log, stop, on_line)
    except RuntimeError:   # it may fail on its way out, its work written (a picture: a lock of its OCR library's)
        if not _whole(z):
            raise
        log("the engine ended abnormally after writing its output, which is kept")
    with zipfile.ZipFile(z) as f:
        for name in f.namelist():   # nothing outside out/
            if name.startswith("/") or ".." in Path(name).parts:
                raise RuntimeError("unsafe path in the engine's output")
        f.extractall(out)
    z.unlink()
    if not (out / "structured_content.json").exists():
        raise RuntimeError("the engine wrote no document")
    if Path(str(pdf)).suffix.lower() == ".pdf":
        shutil.copyfile(pdf, out / PDF)   # the file beside its output: a figure is cut from its page (crop)
    return out


PDF = "input.pdf"

# each region of a page as a picture (the engine's own pypdfium2, a library of MinerU's): pages counted from 1, the
# region in fractions of the page's width and height (as MinerU gives them), the longest edge at most 2000 px
CROP = r"""
import json, sys
import pypdfium2 as pdfium
job = json.load(sys.stdin)
doc = pdfium.PdfDocument(job["pdf"])
for page_no, (x0, y0, x1, y1), out in job["crops"]:
    try:
        page = doc[page_no - 1]
        if page.get_rotation():
            continue
        w, h = page.get_size()
        scale = min(3.0, 2000 / max((x1 - x0) * w, (y1 - y0) * h, 1))
        img = page.render(scale=scale, crop=(x0 * w, (1 - y1) * h, (1 - x1) * w, y0 * h)).to_pil()
        img.convert("RGB").save(out, quality=92)
        print(json.dumps(out), flush=True)
    except Exception as e:
        print("skipped", out, e, file=sys.stderr)
"""


def crop(pdf, crops):
    """Regions of a PDF's pages as pictures: crops is [(page, [x0, y0, x1, y1], out file)]; the out files written
    (a region that cannot be cut is left out). With no engine, cut here (crop_here)."""
    e = where()
    if not crops:
        return set()
    if e is None:
        return crop_here(pdf, crops)
    job = json.dumps({"pdf": str(pdf), "crops": [[p, list(b), str(o)] for p, b, o in crops]})
    try:
        r = subprocess.run([str(_bin(e, "python")), "-c", CROP], input=job, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_env(e), timeout=120 + 5 * len(crops))
    except (OSError, subprocess.TimeoutExpired):
        return set()
    done = set()
    for line in r.stdout.splitlines():
        try:
            done.add(json.loads(line))
        except ValueError:
            pass
    return {o for o in done if Path(o).is_file()}


PDFIUM = threading.Lock()   # pdfium serves one thread at a time, and the server reads PDFs on more than one


def crop_here(pdf, crops):
    """crop in this process, with TextFold's own pypdfium2 (a PDF read without the engine, pdftext.py): CROP's lines.
    Nothing when pypdfium2 is not installed."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return set()
    done = set()
    with PDFIUM:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            for page_no, (x0, y0, x1, y1), out in crops:
                try:
                    page = doc[page_no - 1]
                    if page.get_rotation():
                        continue
                    w, h = page.get_size()
                    scale = min(3.0, 2000 / max((x1 - x0) * w, (y1 - y0) * h, 1))
                    img = page.render(scale=scale, crop=(x0 * w, (1 - y1) * h, (1 - x1) * w, y0 * h)).to_pil()
                    img.convert("RGB").save(out, quality=92)
                    done.add(str(out))
                except Exception:   # (a region that cannot be cut is left out, as there)
                    continue
        finally:
            doc.close()
    return {o for o in done if Path(o).is_file()}
