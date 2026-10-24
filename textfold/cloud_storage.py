"""A document backend served by any TextFold Storage API v1 service.

Only document values cross the application interface. The private wire codec
lets servers deduplicate binary content while committing a document with CAS.
"""
import hashlib
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from .documents import ConflictError, Document, DocumentInfo, LibraryState, Processing, Source
from ._storage_layout import checked_id, checked_key
from . import __version__

MAX_MANIFEST = 8 * 1024 * 1024
MAX_BLOB = 100 * 1024 * 1024


class StorageError(OSError):
    """A transport, authentication or integrity failure; never an empty library."""


class StorageAuthenticationError(StorageError):
    """Credentials must be checked before retrying frequently."""


class StorageIdentityError(StorageError):
    """A workspace must never silently bind itself to a different library."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib can forward Authorization headers when following redirects.
        raise StorageError("Storage endpoint redirected; check its configured URL")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _pack(document):
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


def _unpack(value, download):
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


class CloudStorageBackend:
    name = "cloud"

    def __init__(self, url, username, password, timeout=120, workers=4):
        if not isinstance(url, str):
            raise ValueError("Enter a storage service URL")
        parsed = urllib.parse.urlsplit(url)
        parsed.port  # validate malformed/out-of-range ports before saving settings
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if not parsed.hostname or (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Use an HTTPS storage Worker URL (HTTP is allowed only on loopback)")
        if not isinstance(username, str) or not username or ':' in username or any(ord(c) < 32 for c in username):
            raise ValueError("Enter a storage username without colons or control characters")
        if not isinstance(password, str) or not password or any(ord(c) < 32 for c in password):
            raise ValueError("Enter a nonempty storage password without control characters")
        if not 1 <= workers <= 16 or timeout <= 0:
            raise ValueError("Invalid storage client limits")
        self.url, self.timeout, self.workers = url.rstrip("/"), timeout, workers
        self._authorization = "Basic " + base64.b64encode((username + ':' + password).encode('utf-8')).decode('ascii')
        self._opener = urllib.request.build_opener(_NoRedirect())

    def check(self):
        if self._request("GET", "health").get("format") != "textfold/storage-v1":
            raise StorageError("The endpoint does not implement TextFold Storage API v1")

    def _request(self, method, path, data=None, *, binary=False, limit=MAX_MANIFEST, version="v1"):
        headers = {"Authorization": self._authorization, "Accept": "application/json",
                   "User-Agent": f"TextFold/{__version__} (Storage API v1)"}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream" if binary else "application/json"
            data = data if binary else _json(data)
            if not binary and len(data) > MAX_MANIFEST:
                raise ValueError("Storage request exceeds the manifest limit")
        request = urllib.request.Request(self.url + "/" + version + "/" + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if method == "HEAD":
                    return None
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise StorageError("Storage response exceeds its expected size")
                if binary:
                    return raw
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise StorageError("Storage returned an invalid response")
                return value
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status == 404:
                raise FileNotFoundError(path) from None
            if status == 409:
                raise ConflictError("Remote document or library state changed; reload before saving") from None
            if status in (401, 403):
                raise StorageAuthenticationError("Storage authentication failed") from None
            raise StorageError(f"Storage request failed (HTTP {status}); reload before retrying a write") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise StorageError("Storage connection failed; a write may have committed, reload before retrying") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise StorageError("Storage returned an invalid response") from error

    def _path(self, document_id):
        return "documents/" + urllib.parse.quote(checked_id(document_id), safe="")

    def sync_health(self):
        value = self._request("GET", "health", version="v2")
        if value.get("format") != "textfold/storage-v2" or not isinstance(value.get("library_id"), str) or not value["library_id"]:
            raise StorageError("This service needs the record synchronization upgrade")
        if type(value.get("cursor")) is not int or not 0 <= value["cursor"] <= 9007199254740991:
            raise StorageError("Invalid synchronization cursor")
        return value

    def open_notifications(self, url, ping_interval=60):
        import logging
        from websockets.sync.client import connect
        from websockets.exceptions import InvalidStatus
        target, origin = urllib.parse.urlsplit(url), urllib.parse.urlsplit(self.url)
        expected = 'wss' if origin.scheme == 'https' else 'ws'
        if target.scheme != expected or target.netloc != origin.netloc or target.query or target.fragment or target.username or target.password:
            raise ValueError('Notifications must use the configured storage origin')
        # This pinned synchronous client rejects redirects. Disable its wire
        # logger so even application-wide DEBUG cannot print Authorization.
        logger = logging.Logger('textfold.storage.events')
        logger.disabled = True
        try:
            return connect(url, additional_headers={'Authorization': self._authorization},
                           user_agent_header=f'TextFold/{__version__}', proxy=None, compression=None,
                           open_timeout=10, close_timeout=1, ping_interval=ping_interval, ping_timeout=20,
                           max_size=4096, max_queue=8, logger=logger)
        except InvalidStatus as error:
            if error.response.status_code in (401, 403):
                raise StorageAuthenticationError('Storage notification authentication failed') from None
            raise StorageError('Storage notification upgrade failed') from None

    def sync_index(self):
        cursor, rows = "", []
        while True:
            page = self._request("GET", "index?after=" + urllib.parse.quote(cursor, safe=""), version="v2")
            rows.extend(page["documents"])
            following = page["next"]
            if following is None:
                return rows
            if not isinstance(following, str) or following <= cursor:
                raise StorageError("Invalid sync index pagination")
            cursor = following

    def sync_snapshot(self, document_id):
        path = "library" if document_id == "@library" else self._path(document_id)
        return self._request("GET", path, version="v2")

    def sync_commit(self, document_id, payload):
        path = "library" if document_id == "@library" else self._path(document_id)
        return self._request("PUT", path, payload, version="v2")

    def sync_changes(self, cursor):
        return self._request("GET", "changes?cursor=" + str(cursor), version="v2")

    def get(self, document_id):
        value = self._request("GET", self._path(document_id))
        result = _unpack(value["document"], self._download)
        if result.id != document_id:
            raise StorageError("Remote document identity mismatch")
        info = value["info"]
        result.revision, result.created, result.updated = info["revision"], info["created"], info["updated"]
        return result

    def _download(self, digest, size):
        try:
            return self._request("GET", "blobs/" + digest, binary=True, limit=size)
        except FileNotFoundError:
            raise StorageError("Document references an unavailable blob") from None

    def _upload(self, item):
        digest, raw = item
        try:
            self._request("HEAD", "blobs/" + digest)
        except FileNotFoundError:
            pass
        else:
            return
        # PUT also repairs the D1 registration if a previous upload reached R2
        # but failed before its registry entry was committed.
        self._request("PUT", "blobs/" + digest, raw, binary=True)

    def save(self, document):
        manifest, blobs = _pack(document)
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(self._upload, blobs.items()))
        info = self._request("PUT", self._path(document.id),
                             {"revision": document.revision, "commit": str(uuid.uuid4()), "document": manifest})
        document.revision, document.created, document.updated = info["revision"], info["created"], info["updated"]

    def info(self, document_id):
        return DocumentInfo(**self._request("GET", self._path(document_id) + "/info"))

    def list(self):
        after = ""
        rows = []
        while True:
            page = self._request("GET", "documents?after=" + urllib.parse.quote(after, safe=""))
            rows.extend(DocumentInfo(**row) for row in page["documents"])
            cursor = page["next"]
            if cursor is None:
                return rows
            if not isinstance(cursor, str) or cursor <= after:
                raise StorageError("Invalid storage index pagination")
            after = cursor

    def delete(self, document_id):
        self._request("DELETE", self._path(document_id))

    def trash(self, document_id, placement=None):
        return self._request("POST", self._path(document_id) + "/trash",
                             {"commit": str(uuid.uuid4()), "placement": placement})["token"]

    def restore(self, document_id):
        return self._request("POST", self._path(document_id) + "/restore", {})["placement"]

    def purge(self, before):
        return self._request("POST", "trash/purge", {"before": before})["removed"]

    def get_library(self):
        return LibraryState(**self._request("GET", "library"))

    def save_library(self, state):
        value = self._request("PUT", "library", {"catalog": state.catalog, "zotero": state.zotero,
                                                  "revision": state.revision, "commit": str(uuid.uuid4())})
        state.revision = value["revision"]


class UnavailableCloudStorage(CloudStorageBackend):
    """Keep Settings reachable when the keyring is locked; deny library access."""
    def __init__(self, config, message):
        self.url = config.get("url", "")
        self.configuration_revision = config.get("revision")
        self.configuration_error = message
        self.workers = 1

    def _request(self, *args, **kwargs):
        raise StorageError(self.configuration_error)
