"""Vector store on a plain numpy matrix with brute-force cosine search.

Exact and fast up to a few hundred thousand passages; one .npz file per
collection. Swap it for FAISS / LanceDB / a server when documents get large.
"""

from pathlib import Path
from typing import Sequence

import numpy as np

from .base import Hit, VectorStore


class NumpyVectorStore(VectorStore):
    name = "numpy"

    def _path(self, collection: str) -> Path:
        return self.root / "vectors" / f"{collection}.npz"

    def has(self, collection: str) -> bool:
        return self._path(collection).is_file()

    def build(self, collection: str, ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
        matrix = np.asarray(vectors, dtype="float32").reshape(len(ids), -1)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.where(norms == 0, 1, norms)  # unit length => dot product = cosine
        path = self._path(collection)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, ids=np.asarray(list(ids), dtype=str), matrix=matrix)

    def query(
        self, collection: str, vector: Sequence[float], k: int, allowed_ids: Sequence[str] | None = None
    ) -> list[Hit]:
        if not self.has(collection):
            return []
        with np.load(self._path(collection)) as data:
            ids, matrix = data["ids"], data["matrix"]
        q = np.asarray(vector, dtype="float32")
        norm = np.linalg.norm(q)
        scores = matrix @ (q / norm if norm else q)
        if allowed_ids is not None:
            scores = np.where(np.isin(ids, list(allowed_ids)), scores, -np.inf)
        top = np.argsort(-scores)[:k]
        return [Hit(id=str(ids[i]), score=float(scores[i])) for i in top if np.isfinite(scores[i])]

    def delete(self, collection: str) -> None:
        self._path(collection).unlink(missing_ok=True)
