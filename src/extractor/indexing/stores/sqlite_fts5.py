"""Lexical index on SQLite FTS5 (BM25 ranking). No extra dependency, one file per collection."""

import re
import sqlite3
from pathlib import Path
from typing import Sequence

from .base import Hit, LexicalIndex

_WORD = re.compile(r"\w+", re.UNICODE)


class SqliteFts5Index(LexicalIndex):
    name = "sqlite_fts5"

    def __init__(self, root: Path, tokenizer: str = "porter unicode61", **params):
        super().__init__(root, **params)
        self.tokenizer = tokenizer

    def _path(self, collection: str) -> Path:
        return self.root / "lexical" / f"{collection}.sqlite"

    def has(self, collection: str) -> bool:
        return self._path(collection).is_file()

    def build(self, collection: str, items: Sequence[tuple[str, str]]) -> None:
        path = self._path(collection)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        db = sqlite3.connect(path)
        try:
            db.execute(f"CREATE VIRTUAL TABLE passages USING fts5(id UNINDEXED, text, tokenize='{self.tokenizer}')")
            db.executemany("INSERT INTO passages(id, text) VALUES (?, ?)", list(items))
            db.commit()
        finally:
            db.close()

    def search(self, collection: str, query: str, k: int) -> list[Hit]:
        words = _WORD.findall(query)
        if not words or not self.has(collection):
            return []
        match = " OR ".join(f'"{w}"' for w in words)  # any word may match; BM25 ranks the rest
        db = sqlite3.connect(self._path(collection))
        try:
            rows = db.execute(
                "SELECT id, bm25(passages) FROM passages WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT ?",
                (match, k),
            ).fetchall()
        finally:
            db.close()
        return [Hit(id=i, score=-score) for i, score in rows]  # bm25() is lower = better

    def delete(self, collection: str) -> None:
        self._path(collection).unlink(missing_ok=True)
