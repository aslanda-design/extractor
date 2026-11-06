"""Search index implementations. Import and register a new backend here."""

from .base import Hit, LexicalIndex, VectorStore
from .numpy_store import NumpyVectorStore
from .registry import build_stores, register_lexical_store, register_vector_store, registered_stores
from .sqlite_fts5 import SqliteFts5Index

register_lexical_store("sqlite_fts5", SqliteFts5Index)
register_vector_store("numpy", NumpyVectorStore)

__all__ = [
    "Hit", "LexicalIndex", "VectorStore", "build_stores",
    "register_lexical_store", "register_vector_store", "registered_stores",
]
