"""BM25 over code-aware tokens.

Written by hand on purpose: it is forty lines, it teaches why lexical retrieval still
matters when the query is an identifier, and it removes a dependency. Identifiers are
split on snake_case and camelCase so `calculate_refund` matches "refund".
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

import numpy as np
from numpy.typing import NDArray

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOPWORDS = frozenset(
    {"the", "and", "for", "with", "that", "this", "from", "self", "def", "return", "import"}
)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _IDENTIFIER.findall(text):
        lowered = word.lower()
        if len(lowered) > 1 and lowered not in STOPWORDS:
            tokens.append(lowered)
        parts = [part.lower() for part in _CAMEL.findall(word.replace("_", " "))]
        if len(parts) > 1:
            tokens.extend(part for part in parts if len(part) > 1 and part not in STOPWORDS)
    return tokens


class BM25:
    def __init__(self, documents: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_count = len(documents)
        self.doc_lengths = np.array([len(doc) for doc in documents], dtype=float)
        self.avg_length = float(self.doc_lengths.mean()) if self.doc_count else 0.0
        self.term_frequencies: list[Counter[str]] = [Counter(doc) for doc in documents]
        document_frequency: Counter[str] = Counter()
        for counts in self.term_frequencies:
            document_frequency.update(counts.keys())
        self.idf: dict[str, float] = {
            term: math.log(1 + (self.doc_count - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }

    def scores(self, query: Iterable[str]) -> NDArray[np.float64]:
        result = np.zeros(self.doc_count, dtype=float)
        if not self.doc_count:
            return result
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index, counts in enumerate(self.term_frequencies):
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                length_norm = 1 - self.b + self.b * self.doc_lengths[index] / self.avg_length
                result[index] += (
                    idf * frequency * (self.k1 + 1) / (frequency + self.k1 * length_norm)
                )
        return result

    def top(self, query: Iterable[str], k: int = 10) -> list[tuple[int, float]]:
        scores = self.scores(list(query))
        order = np.argsort(-scores)[:k]
        return [(int(index), float(scores[index])) for index in order if scores[index] > 0]
