"""Interfaces for the two search indexes. Implement one to plug in another database.

A *collection* is one named index (the builder uses one per document and
indexing configuration). Implementations decide what a collection physically
is: a file, a table, a namespace on a server.

To add a backend (FAISS, LanceDB, pgvector, Qdrant, Elasticsearch...):
  1. subclass `VectorStore` or `LexicalIndex` in a new file under stores/
  2. register it in stores/__init__.py with `register_vector_store(...)` or
     `register_lexical_store(...)`
  3. select it in the runtime config:
         indexing: {vector_store: {name: my_store, params: {url: ...}}}
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

from ...contracts.common import StrictModel


class Hit(StrictModel):
    id: str  # passage id
    score: float  # higher = more relevant; scales differ between indexes (fuse by rank, not score)


class LexicalIndex(ABC):
    """Keyword (BM25-style) search over passage text."""

    name: str

    def __init__(self, root: Path, **params):
        self.root = Path(root)

    @abstractmethod
    def has(self, collection: str) -> bool: ...

    @abstractmethod
    def build(self, collection: str, items: Sequence[tuple[str, str]]) -> None:
        """Create or REPLACE the collection from (passage_id, text) pairs."""

    @abstractmethod
    def search(self, collection: str, query: str, k: int) -> list[Hit]: ...

    @abstractmethod
    def delete(self, collection: str) -> None: ...


class VectorStore(ABC):
    """Nearest-neighbour search over passage embeddings (cosine similarity)."""

    name: str

    def __init__(self, root: Path, **params):
        self.root = Path(root)

    @abstractmethod
    def has(self, collection: str) -> bool: ...

    @abstractmethod
    def build(self, collection: str, ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
        """Create or REPLACE the collection."""

    @abstractmethod
    def query(
        self, collection: str, vector: Sequence[float], k: int, allowed_ids: Sequence[str] | None = None
    ) -> list[Hit]:
        """Top-k most similar ids; `allowed_ids` restricts the candidates when given."""

    @abstractmethod
    def delete(self, collection: str) -> None: ...
