"""Build the retrieval tools (stores + embedder + reranker) from the runtime config."""

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..indexing.stores import build_stores
from .types import RetrievalTools


def build_retrieval_tools(config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore) -> RetrievalTools:
    """Same stores the indexer wrote to (same config, same root), plus the models that query them."""
    lexical, vectors = build_stores(config.indexing, store.root / "indexes")
    return RetrievalTools(lexical=lexical, vectors=vectors, embedder=adapters.embedder, reranker=adapters.reranker)
