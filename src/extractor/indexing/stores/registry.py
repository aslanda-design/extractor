"""Store registry: maps a name from the runtime config to an implementation."""

from pathlib import Path
from typing import Callable

from ...config.runtime import IndexingConfig
from .base import LexicalIndex, VectorStore

_LEXICAL: dict[str, Callable[..., LexicalIndex]] = {}
_VECTOR: dict[str, Callable[..., VectorStore]] = {}


def register_lexical_store(name: str, factory: Callable[..., LexicalIndex]) -> None:
    _LEXICAL[name] = factory


def register_vector_store(name: str, factory: Callable[..., VectorStore]) -> None:
    _VECTOR[name] = factory


def registered_stores() -> dict[str, list[str]]:
    """Names usable in `indexing.lexical_store.name` / `indexing.vector_store.name`."""
    return {"lexical": sorted(_LEXICAL), "vector": sorted(_VECTOR)}


def _pick(registry: dict, kind: str, name: str):
    try:
        return registry[name]
    except KeyError:
        raise ValueError(f"no {kind} store named '{name}' (registered: {sorted(registry)})") from None


def build_stores(config: IndexingConfig, root: Path) -> tuple[LexicalIndex, VectorStore]:
    """Instantiate the configured stores; `root` is where file-based stores keep their data."""
    lexical = _pick(_LEXICAL, "lexical", config.lexical_store.name)(root, **config.lexical_store.params)
    vector = _pick(_VECTOR, "vector", config.vector_store.name)(root, **config.vector_store.params)
    return lexical, vector
