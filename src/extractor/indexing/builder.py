"""S04 - Build everything searchable for one document, and cache it.

    parse (S03, cached)  ->  chunk_document  ->  lexical index
                                             ->  embeddings -> vector index
                                             ->  coverage ledger

Caching: the IndexedDocument is stored under a key made of the document hash,
the parser, the parsing + chunking settings and the embedding model identity.
Change any of them and the next run rebuilds exactly what depends on it.
Embeddings are cached separately, so changing only the vector DATABASE
re-fills it without re-running the embedding model.
"""

from pathlib import Path

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore, fingerprint
from ..config import RuntimeConfig
from ..contracts.coverage import CoverageEntry, CoverageLedger, CoverageState
from ..contracts.document import DocumentRepresentation
from ..contracts.manifest import DocumentEntry
from ..contracts.passage import IndexedDocument
from .chunking_pipeline import chunk_document
from .provenance import check_invariants
from .stores import LexicalIndex, VectorStore, build_stores

EMBED_BATCH = 64


class IndexingError(RuntimeError):
    """A structural promise of S04 was broken (a bug, not a property of the document)."""


def initial_coverage(representation: DocumentRepresentation) -> CoverageLedger:
    """Which parts were parsed, and which parsing problems remain open (coverage limitations)."""
    ledger = CoverageLedger()
    for block in representation.blocks:
        ledger.record(CoverageEntry(document_id=block.document_id, block_id=block.block_id,
                                    page=block.page, state=CoverageState.PARSED))
    for issue in representation.issues:
        if not issue.resolved:
            ledger.record(CoverageEntry(document_id=issue.document_id, page=issue.page,
                                        state=CoverageState.UNRESOLVED,
                                        note=f"{issue.kind.value}: {issue.message}"))
    return ledger


class IndexBuilder:
    def __init__(self, config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore):
        self.config, self.adapters, self.store = config, adapters, store
        self.lexical: LexicalIndex
        self.vectors: VectorStore
        self.lexical, self.vectors = build_stores(config.indexing, store.root / "indexes")

    def index_key(self, entry: DocumentEntry) -> str:
        return fingerprint(entry.sha256, self.adapters.parser.identity, self.config.parsing,
                           self.config.indexing.chunking, self.adapters.embedder.identity)

    def index(self, entry: DocumentEntry, source_path: Path) -> IndexedDocument:
        key = self.index_key(entry)
        cached = self.store.get_model("indexed", key, IndexedDocument) if self.config.parsing.use_cache else None
        doc = cached or self._chunk(entry, source_path, key)
        self._fill_stores(doc, key)
        return doc

    # --- steps ----------------------------------------------------------------------

    def _chunk(self, entry: DocumentEntry, source_path: Path, key: str) -> IndexedDocument:
        representation = self.adapters.parser.parse(entry, source_path)  # S03 (has its own cache)
        embedder = self.adapters.embedder
        doc = chunk_document(representation, self.config.indexing.chunking, embedder.count_tokens)
        doc = doc.model_copy(update={
            "coverage": initial_coverage(doc.representation),
            "index_key": key,
            "embedding_model": embedder.identity.label()})
        problems = check_invariants(doc)
        if problems:
            raise IndexingError(f"{entry.filename}: " + "; ".join(problems[:5]))
        self.store.put("indexed", key, doc)
        return doc

    def _fill_stores(self, doc: IndexedDocument, key: str) -> None:
        if not doc.passages:
            return
        include = self.config.indexing.chunking.include_heading_in_index
        if not self.lexical.has(key):
            self.lexical.build(key, [(p.passage_id, p.indexed_text(include)) for p in doc.passages])
        if not self.vectors.has(key):
            self.vectors.build(key, [p.passage_id for p in doc.passages], self._embeddings(doc, key, include))

    def _embeddings(self, doc: IndexedDocument, key: str, include: bool) -> list[list[float]]:
        cached = self.store.get("embeddings", key)  # survives a change of vector database
        if cached and cached["ids"] == [p.passage_id for p in doc.passages]:
            return cached["vectors"]
        texts = [p.indexed_text(include) for p in doc.passages]
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            vectors += self.adapters.embedder.embed(texts[i:i + EMBED_BATCH])
        self.store.put("embeddings", key, {"ids": [p.passage_id for p in doc.passages], "vectors": vectors})
        return vectors
