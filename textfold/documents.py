"""Storage values: a document is the identity, read and save unit.

These objects describe content, not its physical layout. A backend may keep a
document in a directory, a database row, an archive, or several remote objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol


class ConflictError(RuntimeError):
    """The document changed after the caller read it; no changes were saved."""


@dataclass
class Source:
    html: str | None = None
    markdown: str | None = None
    xml: str | None = None
    wiki_html: str | None = None
    pdf: bytes | None = None
    reference: str | None = None
    originals: dict[str, bytes] = field(default_factory=dict)  # format -> original bytes


@dataclass
class Processing:
    layout: object = None
    structured: object = None
    replies: dict[str, bytes] = field(default_factory=dict)  # model cache ID -> opaque reply bytes


@dataclass
class Document:
    id: str
    meta: dict | None = None
    source: Source = field(default_factory=Source)
    layers: dict[str, dict] = field(default_factory=dict)
    notes: dict | None = None
    images: dict[str, bytes] = field(default_factory=dict)  # resource ID -> bytes
    processing: Processing = field(default_factory=Processing)
    previous: "Document | None" = None
    extensions: dict[str, bytes] = field(default_factory=dict)
    created: float = 0
    updated: float = 0
    revision: object = None  # opaque backend token; None means create only
    # Private lossless round-trip information for old local libraries. Other
    # backends need only preserve it as opaque data, never interpret it.
    _legacy: dict[str, bytes] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class DocumentInfo:
    """Lightweight document index row; listing must not load original PDFs."""
    id: str
    meta: dict
    titles: dict
    languages: list[str]
    note_count: int
    created: float
    updated: float
    revision: object
    readable: bool = False


@dataclass
class LibraryState:
    """Library-wide organization and external bindings, outside any document."""
    catalog: dict = field(default_factory=dict)
    zotero: dict = field(default_factory=dict)
    revision: object = None


class StorageBackend(Protocol):
    """Plugins exchange whole documents. No file/path/workspace methods.

    get raises FileNotFoundError. save compares document.revision with the
    current token (None creates only), then refreshes it. Competing writes
    raise ConflictError without replacing the newer document. Returned values
    are detached snapshots, so editing one cannot silently change storage.
    """
    def get(self, document_id: str) -> Document: ...
    def info(self, document_id: str) -> DocumentInfo: ...
    def save(self, document: Document) -> None: ...
    def list(self) -> Iterable[DocumentInfo]: ...
    def delete(self, document_id: str) -> None: ...
    def trash(self, document_id: str, placement: dict | None = None) -> str: ...
    def restore(self, document_id: str) -> dict | None: ...
    def purge(self, before: float) -> list[str]: ...
    def get_library(self) -> LibraryState: ...
    def save_library(self, state: LibraryState) -> None: ...
