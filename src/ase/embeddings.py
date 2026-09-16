"""Async embedding facade over `ase.repo_intelligence.embeddings`.

The retrieval stack works on numpy matrices synchronously; this module keeps the small
async, list-of-floats interface that the IDE and worker code paths use, delegating the
actual vectors to the one hashing implementation so both routes embed identically.
"""

from __future__ import annotations

import math
from typing import Protocol

from ase.repo_intelligence.embeddings import HashingEmbedder as _HashingEmbedder


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    def __init__(self, dimensions: int = 128) -> None:
        self.dimensions = dimensions
        self._inner = _HashingEmbedder(dimension=dimensions)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        matrix = self._inner.embed(texts)
        return [[float(value) for value in row] for row in matrix.tolist()]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have equal dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)
