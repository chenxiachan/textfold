"""A local document backend selected for the next application start.

Only papers/, library.json and zotero.json move. The runtime directory and
credentials do not. A copy is verified before selection changes; source files
are never moved or removed. No work happens merely by importing this module.
"""
import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

from ._storage_layout import DirectoryFiles

_lock = threading.RLock()
NAMES = ("papers", "library.json", "zotero.json")
MARKER = ".textfold-migration.json"


def _process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) terminates processes on Windows; query the handle instead.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5   # access denied: treat as running
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _check_other_server(runtime):
    try:
        server = json.loads((Path(runtime) / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(server, dict):
        return
    if server.get("format") == "textfold/server" and server.get("pid") != os.getpid() and _process_alive(server.get("pid")):
        raise OSError("Close other TextFold instances, then restart to copy the library")


def load(home):
    path = Path(home) / "storage.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("backend", "local") != "local":
        raise ValueError("Unsupported document storage configuration")
    return value


def active_path(default, home):
    return Path(load(home).get("path") or default).expanduser().resolve()


def _save(home, value):
    DirectoryFiles(home).write("storage.json", json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def status(root, home):
    config = load(home)
    return {"backend": "local", "path": str(Path(root).resolve()),
            "pending": config.get("pending") or "", "error": config.get("error") or ""}


def _target(source, target):
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Enter an absolute folder path")
    path = Path(target.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("The library folder must be an absolute path")
    path, source = path.resolve(), Path(source).resolve()
    if path == source:
        return path
    if path.is_relative_to(source) or source.is_relative_to(path):
        raise ValueError("The new library must not contain, or be inside, the current library")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Choose an empty folder; existing files will not be overwritten")
    return path


def schedule(root, home, target=None, cancel=False):
    """Validate now, copy on next start, so current writes stay in one library."""
    with _lock:
        config = load(home)
        root = Path(root).resolve()
        if cancel:
            config.pop("pending", None)
            config.pop("error", None)
        else:
            path = _target(root, target)
            if path == root:
                config.pop("pending", None)
            else:
                config["pending"] = str(path)
            config.pop("error", None)
        config.update(backend="local", path=str(root))
        _save(home, config)
        return status(root, home)


def _files(root):
    files = []
    for name in NAMES:
        path = root / name
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Migration does not follow symbolic links")
        for item in path.rglob("*") if path.is_dir() else [path]:
            if item.is_symlink() or getattr(item, "is_junction", lambda: False)() or not item.resolve().is_relative_to(root):
                raise ValueError("Migration does not follow symbolic links")
            if item.is_file():
                files.append(item)
    return files


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_library(source, destination):
    """Resumable verified copy. Conflicting files always stop the migration."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Overlapping library folders")
    marker = destination / MARKER
    if marker.exists():
        journal = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(journal, dict) or journal.get("source") != str(source) or journal.get("version") not in (1, 2):
            raise ValueError("This folder belongs to another migration")
    else:
        _target(source, str(destination))
        destination.mkdir(parents=True, exist_ok=True)
        journal = {"source": str(source), "version": 2, "files": {}}
    if any(p.name not in (*NAMES, MARKER) for p in destination.iterdir()):
        raise ValueError("Migration stopped: unexpected files in destination")
    before = {p.relative_to(source).as_posix(): _digest(p) for p in _files(source)}
    existing = {p.relative_to(destination).as_posix(): _digest(p) for p in _files(destination)}
    owned = journal.get("files", {})
    if not isinstance(owned, dict) or any(not isinstance(values, list) or not all(isinstance(v, str) for v in values)
                                          for values in owned.values()):
        raise ValueError("Invalid migration file manifest")
    target = DirectoryFiles(destination)
    for key, digest in existing.items():
        if digest != before.get(key) and digest not in owned.get(key, []):
            raise ValueError(f"Migration stopped: conflicting file {key}")
    # Journal both old and proposed copies before changing anything. A retry
    # can replace/remove only unchanged files that this migration itself owns.
    planned = {key: list(values) for key, values in owned.items()}
    for key, digest in before.items():
        planned[key] = sorted(set(planned.get(key, []) + [digest]))
    target.write(MARKER, json.dumps({"source": str(source), "version": 2, "files": planned}).encode("utf-8"))
    for key in existing.keys() - before.keys():
        out = target.path(key)
        if _digest(out) != existing[key]:
            raise ValueError(f"Migration stopped: conflicting file {key}")
        out.unlink()
    for key, digest in before.items():
        if existing.get(key) == digest:
            continue
        out = target.path(key)
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=out.name + ".", suffix=".textfold-copy", dir=out.parent)
        os.close(fd)
        temp = Path(name)
        try:
            shutil.copy2(source / key, temp)
            if _digest(temp) != digest:
                raise OSError(f"Copy verification failed: {key}")
            if out.exists() and (key not in existing or _digest(out) != existing[key]):
                raise ValueError(f"Migration stopped: conflicting file {key}")
            os.replace(temp, out)
        finally:
            temp.unlink(missing_ok=True)
    after = {p.relative_to(source).as_posix(): _digest(p) for p in _files(source)}
    if before != after:
        raise OSError("The original library changed during copying; close other TextFold instances and retry")
    copied = {p.relative_to(destination).as_posix(): _digest(p) for p in _files(destination)}
    if copied != before:
        raise OSError("The copied library changed during verification")
    # The marker is kept until storage.json has selected the verified copy.


def activate(default, home):
    """Startup only. A failed copy leaves the old library active and readable."""
    with _lock:
        config = load(home)
        if not config.get("pending"):
            return active_path(default, home)
        source = Path(config.get("path") or default).resolve()
        target = Path(config["pending"]).resolve()
        try:
            _check_other_server(default)
            copy_library(source, target)
            selected = {**config, "path": str(target), "backend": "local"}
            selected.pop("pending", None)
            selected.pop("error", None)
            _save(home, selected)
        except (OSError, ValueError) as error:
            config["error"] = str(error)
            try:
                _save(home, config)
            except OSError:
                pass   # disk full must not prevent reading the original library
            return source
        try:
            (target / MARKER).unlink(missing_ok=True)
        except OSError:
            pass   # the verified library is already selected; cleanup may wait
        return target
