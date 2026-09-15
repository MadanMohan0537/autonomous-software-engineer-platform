"""Embedding backends behind one small protocol.

`HashingEmbedder` is deterministic, offline and free: hashed unigram and bigram counts,
L2-normalised. It is the default so the whole pipeline works with zero credentials.
`VoyageEmbedder` calls Voyage AI's code embeddings over HTTP when a key is configured.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

import httpx
import numpy as np
from numpy.typing import NDArray

from ase.repo_intelligence.lexical import tokenize

Matrix = NDArray[np.float32]


class Embedder(Protocol):
    name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> Matrix: ...


def _bucket(token: str, dimension: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimension


class HashingEmbedder:
    name = "hashing"

    def __init__(self, dimension: int = 512) -> None:
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> Matrix:
        matrix = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = tokenize(text)
            for token in tokens:
                matrix[row, _bucket(token, self.dimension)] += 1.0
            for left, right in zip(tokens, tokens[1:], strict=False):
                matrix[row, _bucket(f"{left} {right}", self.dimension)] += 0.5
            norm = float(np.linalg.norm(matrix[row]))
            if norm:
                matrix[row] /= norm
        return matrix


class VoyageEmbedder:
    name = "voyage"

    def __init__(
        self,
        api_key: str,
        model: str = "voyage-code-3",
        dimension: int = 1024,
        client: httpx.Client | None = None,
        base_url: str = "https://api.voyageai.com/v1",
        batch_size: int = 64,
    ) -> None:
        self.model = model
        self.dimension = dimension
        self.batch_size = batch_size
        self.name = f"voyage:{model}"
        self.client = client or httpx.Client(base_url=base_url, timeout=60)
        self.client.headers["Authorization"] = f"Bearer {api_key}"

    def embed(self, texts: Sequence[str]) -> Matrix:
        rows: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            response = self.client.post(
                "/embeddings",
                json={"input": batch, "model": self.model, "input_type": "document"},
            )
            response.raise_for_status()
            payload = response.json()
            ordered = sorted(payload["data"], key=lambda item: int(item["index"]))
            rows.extend([float(value) for value in item["embedding"]] for item in ordered)
        if not rows:
            return np.zeros((0, self.dimension), dtype=np.float32)
        matrix = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return np.asarray(matrix / norms, dtype=np.float32)


def select_embedder(voyage_api_key: str | None = None, model: str = "voyage-code-3") -> Embedder:
    if voyage_api_key:
        return VoyageEmbedder(voyage_api_key, model=model)
    return HashingEmbedder()
