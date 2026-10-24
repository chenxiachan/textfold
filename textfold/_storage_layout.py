"""Private legacy directory IO. This is not the storage plugin interface."""
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


def rename_directory(source, destination):
    """A transient Windows sharing violation must not lose a restore operation."""
    for attempt in range(5):
        try:
            return Path(source).rename(destination)
        except PermissionError as error:
            if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


def replace_file(source, destination):
    """Atomic replacement can briefly be held by Windows file indexing too."""
    for attempt in range(5):
        try:
            return os.replace(source, destination)
        except PermissionError as error:
            if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


def checked_key(key):
    key = str(key)
    parts = PurePosixPath(key)
    if not key or parts.is_absolute() or "\\" in key or ":" in key or any(p in (".", "..", "") for p in key.split("/")):
        raise ValueError("Storage keys must be relative paths without traversal")
    return parts.as_posix()


@dataclass(frozen=True)
class Entry:
    key: str
    modified: float
    size: int
    version: object = None


class DirectoryFiles:
    """Internal IO for the built-in layout and its verified folder migration."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def path(self, key):
        path = self.root.joinpath(*checked_key(key).split("/"))
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Storage path escapes the library")
        return path

    def read(self, key):
        return self.path(key).read_bytes()

    def write(self, key, data):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            replace_file(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)

    def exists(self, key):
        return self.path(key).exists()

    def entries(self, prefix):
        folder = self.path(prefix)
        if folder.is_file():
            files = [folder]
        else:
            files = folder.rglob("*") if folder.exists() else []
        for path in files:
            if path.is_file() and path.resolve().is_relative_to(self.root):
                st = path.stat()
                yield Entry(path.relative_to(self.root).as_posix(), st.st_mtime, st.st_size, (st.st_mtime_ns, st.st_ctime_ns))

    def entries_for_root(self):
        for path in self.root.rglob("*") if self.root.exists() else []:
            if path.is_file() and path.resolve().is_relative_to(self.root):
                st = path.stat()
                yield Entry(path.relative_to(self.root).as_posix(), st.st_mtime, st.st_size, (st.st_mtime_ns, st.st_ctime_ns))

def checked_id(identity):
    if not isinstance(identity, str) or not identity or identity.startswith(".") or "/" in identity:
        raise ValueError("Invalid document id")
    return checked_key(identity)


def decode_text(raw):
    # Import lazily: store also imports the storage layer.
    from .store import codepage
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            value = raw.decode(codepage())
        except (LookupError, UnicodeDecodeError):
            value = raw.decode("utf-8", "replace")
    return value.replace("\r\n", "\n").replace("\r", "\n")


class LegacyLayout:
    """The old layout lives only here and in file-based parser integrations.

    Plugins receive semantic Document values. This codec is also the private
    bridge between those values and tools requiring a temporary directory.
    Unchanged text/JSON retains its exact original bytes and formatting.
    """
    TEXT = {"html": "source.html", "markdown": "source.md", "xml": "source.xml",
            "wiki_html": "source.wiki.html", "reference": "url.txt"}
    JSON = {"meta": "meta.json", "notes": "notes.json"}

    @classmethod
    def decode(cls, identity, raw, *, revision=None, entries=()):
        from .documents import Document
        doc = Document(checked_id(identity), revision=revision, _legacy=dict(raw))
        rest = dict(raw)
        for field, name in cls.TEXT.items():
            if name in rest:
                setattr(doc.source, field, decode_text(rest.pop(name)))
        if "source.pdf" in rest:
            doc.source.pdf = rest.pop("source.pdf")
        for field, name in cls.JSON.items():
            if name in rest:
                setattr(doc, field, json.loads(decode_text(rest.pop(name))))
        for name, field in (("layout.json", "layout"), ("structured_content.json", "structured")):
            if name in rest:
                setattr(doc.processing, field, json.loads(decode_text(rest.pop(name))))
        previous = {}
        for name, value in list(rest.items()):
            if name.startswith("original.") and "/" not in name:
                doc.source.originals[name[9:]] = value
            elif name.startswith("layers/") and name.endswith(".json") and name.count("/") == 1:
                doc.layers[name[7:-5]] = json.loads(decode_text(value))
            elif name.startswith("img/"):
                doc.images[name[4:]] = value
            elif name.startswith("llm-cache/"):
                doc.processing.replies[name[10:]] = value
            elif name.startswith("previous/"):
                previous[name[9:]] = value
            else:
                continue
            rest.pop(name)
        if previous:
            doc.previous = cls.decode(identity, previous)
        doc.extensions = rest
        entries = list(entries)
        if entries:
            doc.created = min(e.modified for e in entries)
            changed = [e.modified for e in entries if e.key.startswith("layers/") and e.key.endswith(".json")]
            doc.updated = max(changed) if changed else next((e.modified for e in entries if e.key == "meta.json"), doc.created)
        return doc

    @classmethod
    def encode(cls, doc):
        checked_id(doc.id)
        raw = dict(doc.extensions)

        def text(name, value):
            old = doc._legacy.get(name)
            raw[name] = old if old is not None and decode_text(old) == value else value.encode("utf-8")

        def obj(name, value):
            old = doc._legacy.get(name)
            try:
                same = old is not None and json.loads(decode_text(old)) == value
            except ValueError:
                same = False
            raw[name] = old if same else json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8")

        for field, name in cls.TEXT.items():
            value = getattr(doc.source, field)
            if value is not None:
                text(name, value)
        if doc.source.pdf is not None:
            raw["source.pdf"] = doc.source.pdf
        for field, name in cls.JSON.items():
            value = getattr(doc, field)
            if value is not None:
                obj(name, value)
        for name, value in doc.layers.items():
            obj(f"layers/{name}.json", value)
        for field, name in (("layout", "layout.json"), ("structured", "structured_content.json")):
            value = getattr(doc.processing, field)
            if value is not None:
                obj(name, value)
        raw.update({"original." + k: v for k, v in doc.source.originals.items()})
        raw.update({"img/" + k: v for k, v in doc.images.items()})
        raw.update({"llm-cache/" + k: v for k, v in doc.processing.replies.items()})
        if doc.previous is not None:
            raw.update({"previous/" + k: v for k, v in cls.encode(doc.previous).items()})
        for name in raw:
            checked_key(name)
        return raw

    @staticmethod
    def read_folder(folder):
        files = DirectoryFiles(folder)
        return {e.key: files.read(e.key) for e in files.entries_for_root()}

    @staticmethod
    def write_folder(folder, raw):
        files = DirectoryFiles(folder)
        existing = {e.key for e in files.entries_for_root()}
        for key in existing - raw.keys():
            files.path(key).unlink()
        for key, value in raw.items():
            if key not in existing or files.read(key) != value:
                files.write(key, value)
        # A removed semantic field (for example all layers after replacement)
        # also removes its empty legacy directory.
        if Path(folder).exists():
            for path in sorted(Path(folder).rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if path.is_dir() and not path.is_symlink():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
