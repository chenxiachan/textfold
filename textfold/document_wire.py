"""Private document wire codec, shared by cloud transport and SQLite caches."""
import hashlib
import json
import re
from .documents import Document, Processing, Source
from ._storage_layout import checked_id, checked_key
MAX_MANIFEST = 8 * 1024 * 1024
MAX_BLOB = 100 * 1024 * 1024
class StorageError(OSError):
    """A transport or integrity failure, never an empty library."""
def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")

def pack(document):
    blobs = {}

    def blob(data):
        if not isinstance(data, bytes) or len(data) > MAX_BLOB:
            raise ValueError("Document blobs must be bytes within the upload limit")
        digest = hashlib.sha256(data).hexdigest()
        blobs[digest] = data
        return {"sha256": digest, "size": len(data)}

    def mapping(values):
        return {checked_key(key): blob(value) for key, value in values.items()}

    def doc(value, depth=0):
        if depth > 8:
            raise ValueError("Document history is too deeply nested")
        checked_id(value.id)
        source, processing = value.source, value.processing
        return {"format": "textfold/document-v1", "id": value.id, "meta": value.meta,
                "source": {"html": source.html, "markdown": source.markdown, "xml": source.xml,
                           "wiki_html": source.wiki_html, "reference": source.reference,
                           "pdf": blob(source.pdf) if source.pdf is not None else None,
                           "originals": mapping(source.originals)},
                "layers": value.layers, "notes": value.notes, "images": mapping(value.images),
                "processing": {"layout": processing.layout, "structured": processing.structured,
                               "replies": mapping(processing.replies)},
                "previous": doc(value.previous, depth + 1) if value.previous is not None else None,
                "extensions": mapping(value.extensions), "legacy": mapping(value._legacy),
                "created": value.created, "updated": value.updated}

    manifest = doc(document)
    if len(_json(manifest)) > MAX_MANIFEST - 1024:
        raise ValueError("Document manifest exceeds the storage limit")
    return manifest, blobs


def unpack(value, download):
    cache = {}

    def blob(ref):
        digest, size = ref["sha256"], ref["size"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(size, int) or not 0 <= size <= MAX_BLOB:
            raise StorageError("Invalid remote blob reference")
        if digest not in cache:
            raw = download(digest, size)
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                raise StorageError("Document blob failed its checksum verification")
            cache[digest] = raw
        elif len(cache[digest]) != size:
            raise StorageError("Conflicting remote blob sizes")
        return cache[digest]

    def mapping(values):
        return {checked_key(key): blob(ref) for key, ref in values.items()}

    def doc(value, depth=0):
        if depth > 8 or value["format"] != "textfold/document-v1":
            raise StorageError("Unsupported document snapshot")
        source, processing = value["source"], value["processing"]
        return Document(checked_id(value["id"]), meta=value["meta"],
                        source=Source(html=source["html"], markdown=source["markdown"], xml=source["xml"],
                                      wiki_html=source["wiki_html"], reference=source["reference"],
                                      pdf=blob(source["pdf"]) if source["pdf"] is not None else None,
                                      originals=mapping(source["originals"])),
                        layers=value["layers"], notes=value["notes"], images=mapping(value["images"]),
                        processing=Processing(layout=processing["layout"], structured=processing["structured"], replies=mapping(processing["replies"])),
                        previous=doc(value["previous"], depth + 1) if value["previous"] is not None else None,
                        extensions=mapping(value["extensions"]), _legacy=mapping(value["legacy"]),
                        created=value["created"], updated=value["updated"])

    try:
        return doc(value)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise StorageError("Invalid remote document snapshot") from error
