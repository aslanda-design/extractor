"""DocumentManifest: what documents are in scope for a request (stage S01)."""

from datetime import date
from enum import StrEnum

from .common import StrictModel


class RelationshipKind(StrEnum):
    ATTACHMENT = "attachment"
    AMENDMENT = "amendment"
    REPLACEMENT = "replacement"


class DocumentRelationship(StrictModel):
    """`this document` <kind> `target` (e.g. this is an amendment of target)."""

    kind: RelationshipKind
    target_document_id: str


class DocumentEntry(StrictModel):
    document_id: str  # derived from the content hash, so it is stable
    filename: str
    sha256: str
    format: str  # lowercase extension without dot, e.g. "pdf"
    size_bytes: int
    relationships: list[DocumentRelationship] = []


class Scope(StrictModel):
    document_ids: list[str]
    section_restrictions: dict[str, list[str]] = {}  # document_id -> section selectors
    as_of: date | None = None  # relevant time scope
    precedence_rules: list[str] = []  # supplied rules, verbatim
    precedence_resolved: bool = True  # False => affected tasks must stay uncertain


class DocumentManifest(StrictModel):
    manifest_id: str  # fingerprint of the content below
    documents: list[DocumentEntry]
    scope: Scope

    def entry(self, document_id: str) -> DocumentEntry:
        for doc in self.documents:
            if doc.document_id == document_id:
                return doc
        raise KeyError(document_id)
