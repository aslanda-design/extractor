"""S01 - Establish the document scope.

Turns a DocumentRequest into an immutable DocumentManifest:
  - content hash and stable id per document,
  - supplied relationships (attachment / amendment / replacement),
  - section restrictions, time scope and precedence rules, kept verbatim.

Upload order never decides precedence: documents are stored in a canonical
(id-sorted) order, and if several documents come with no relationship and no
precedence rule, `scope.precedence_resolved` is False so affected tasks can
stay uncertain instead of guessing.
"""

from pathlib import Path

from ..artifacts.fingerprint import fingerprint, sha256_file
from ..artifacts.store import ArtifactStore
from ..contracts.manifest import (
    DocumentEntry,
    DocumentManifest,
    DocumentRelationship,
    Scope,
)
from .request import DocumentRequest

SUPPORTED_FORMATS = {
    "pdf", "docx", "doc", "txt", "md", "html", "htm",
    "png", "jpg", "jpeg", "tif", "tiff", "xlsx", "csv",
}


class ManifestError(ValueError):
    """The request cannot be turned into a valid manifest."""


def build_manifest(request: DocumentRequest, store: ArtifactStore | None = None) -> DocumentManifest:
    """Build the manifest; if `store` is given, also retain the original files."""
    if not request.documents:
        raise ManifestError("the request contains no documents")

    # 1. Hash every file and give it a stable id.
    hashed: list[tuple[Path, str, str]] = []  # (path, sha256, document_id)
    names: dict[str, str] = {}  # filename -> document_id
    seen_hashes: dict[str, str] = {}
    for item in request.documents:
        path = item.path
        if not path.is_file():
            raise ManifestError(f"document not found: {path}")
        fmt = path.suffix.lower().lstrip(".")
        if fmt not in SUPPORTED_FORMATS:
            raise ManifestError(f"unsupported format '.{fmt}': {path.name}")
        if path.name in names:
            raise ManifestError(f"two documents share the filename '{path.name}'")
        digest = sha256_file(path)
        if digest in seen_hashes:
            raise ManifestError(f"'{path.name}' has identical content to '{seen_hashes[digest]}'")
        seen_hashes[digest] = path.name
        document_id = f"doc_{digest[:12]}"
        names[path.name] = document_id
        hashed.append((path, digest, document_id))

    # 2. Resolve relationships by filename.
    def resolve(ref: str) -> str:
        key = Path(ref).name
        if key not in names:
            raise ManifestError(f"relationship target '{ref}' is not part of the request")
        return names[key]

    entries: list[DocumentEntry] = []
    restrictions: dict[str, list[str]] = {}
    for item, (path, digest, document_id) in zip(request.documents, hashed):
        relationships = [
            DocumentRelationship(kind=r.kind, target_document_id=resolve(r.target))
            for r in item.relationships
        ]
        if any(r.target_document_id == document_id for r in relationships):
            raise ManifestError(f"'{path.name}' cannot be related to itself")
        entries.append(
            DocumentEntry(
                document_id=document_id,
                filename=path.name,
                sha256=digest,
                format=path.suffix.lower().lstrip("."),
                size_bytes=path.stat().st_size,
                relationships=relationships,
            )
        )
        if item.sections:
            restrictions[document_id] = list(item.sections)

    # 3. Canonical order: independent of upload order.
    entries.sort(key=lambda e: e.document_id)

    has_precedence_info = bool(request.precedence_rules) or any(e.relationships for e in entries)
    scope = Scope(
        document_ids=[e.document_id for e in entries],
        section_restrictions=restrictions,
        as_of=request.as_of,
        precedence_rules=list(request.precedence_rules),
        precedence_resolved=len(entries) == 1 or has_precedence_info,
    )
    manifest = DocumentManifest(
        manifest_id=fingerprint(entries, scope), documents=entries, scope=scope
    )

    if store is not None:
        for (path, digest, _), _item in zip(hashed, request.documents):
            store.retain_original(path, digest)
        store.put("manifests", manifest.manifest_id, manifest)
    return manifest
