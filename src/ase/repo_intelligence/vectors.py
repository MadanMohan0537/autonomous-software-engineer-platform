"""A small on-disk vector store: numpy matrix + id list, cosine similarity search.

Enough for one repository. Swap it for LanceDB or pgvector behind the same three
methods when the index outgrows memory.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

Matrix = NDArray[np.float32]


class VectorStore:
    def __init__(self, ids: list[str] | None = None, matrix: Matrix | None = None) -> None:
        self.ids: list[str] = ids or []
        self.matrix: Matrix = matrix if matrix is not None else np.zeros((0, 0), dtype=np.float32)

    def __len__(self) -> int:
        return len(self.ids)

    def search(self, query: Matrix, k: int = 10) -> list[tuple[str, float]]:
        if not self.ids or self.matrix.size == 0:
            return []
        vector = np.asarray(query, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            return []
        scores = self.matrix @ (vector / norm)
        order = np.argsort(-scores)[:k]
        return [(self.ids[int(i)], float(scores[int(i)])) for i in order if scores[int(i)] > 0]

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.matrix)
        (directory / "vector_ids.json").write_text(json.dumps(self.ids), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> VectorStore:
        ids_path = directory / "vector_ids.json"
        matrix_path = directory / "vectors.npy"
        if not ids_path.exists() or not matrix_path.exists():
            return cls()
        ids = json.loads(ids_path.read_text(encoding="utf-8"))
        matrix = np.load(matrix_path).astype(np.float32)
        return cls([str(item) for item in ids], matrix)
