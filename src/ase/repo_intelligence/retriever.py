"""Explainable retrieval: every context item says why it was chosen.

`HybridRetriever` works on the lightweight `RepositoryIndex` (tokens, symbols, edges) and
needs nothing built or persisted. `KnowledgeRetriever` works on a built `KnowledgeIndex`
(chunks, BM25, embeddings, graph) and aggregates chunk hits into file-level context.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from ase.contracts import ContextItem, Issue
from ase.repo_intelligence.index import KnowledgeIndex
from ase.repo_intelligence.indexer import RepositoryIndex


class ContextRetriever(Protocol):
    def retrieve(
        self, issue: Issue, index: RepositoryIndex, limit: int = 8
    ) -> list[ContextItem]: ...


class HybridRetriever:
    def retrieve(self, issue: Issue, index: RepositoryIndex, limit: int = 8) -> list[ContextItem]:
        query = self._tokens(f"{issue.title} {issue.body} {' '.join(issue.labels)}")
        symbol_files: dict[str, set[str]] = defaultdict(set)
        graph_bonus: dict[str, float] = defaultdict(float)

        for symbol in index.symbols.values():
            if query & self._tokens(symbol.name):
                symbol_files[symbol.path].add(symbol.name)
                graph_bonus[symbol.path] += 0.2
        for edge in index.relationships:
            if query & self._tokens(edge.target):
                source_path = edge.source.split(":", 1)[0]
                graph_bonus[source_path] += 0.1

        results: list[ContextItem] = []
        for path, record in index.files.items():
            overlap = len(query & record.tokens) / max(len(query), 1)
            path_score = len(query & self._tokens(path)) / max(len(query), 1)
            score = min(1.0, 0.65 * overlap + 0.15 * path_score + graph_bonus[path])
            if score <= 0:
                continue
            reasons: list[str] = []
            if overlap:
                reasons.append("issue terms appear in file")
            if path_score:
                reasons.append("path matches issue vocabulary")
            if symbol_files[path]:
                reasons.append("matching symbols found")
            if graph_bonus[path] and not symbol_files[path]:
                reasons.append("related dependency found")
            results.append(
                ContextItem(
                    path=path,
                    reason="; ".join(reasons),
                    score=round(score, 4),
                    symbols=sorted(symbol_files[path]),
                )
            )
        return sorted(results, key=lambda item: (-item.score, item.path))[:limit]

    @staticmethod
    def _tokens(value: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in value)
        return {token for token in normalized.split() if len(token) > 2}


class KnowledgeRetriever:
    """File-level context from chunk-level hybrid search plus the code graph."""

    def __init__(self, knowledge: KnowledgeIndex) -> None:
        self.knowledge = knowledge

    def retrieve(self, issue: Issue, index: RepositoryIndex, limit: int = 8) -> list[ContextItem]:
        query = f"{issue.title}\n{issue.body}\n{' '.join(issue.labels)}"
        hits = self.knowledge.search(query, k=limit * 4)
        if not hits:
            return []
        # One strongly matching definition beats many weak matches: best hit plus a small
        # bonus for the rest, so files with many tiny chunks do not win by volume.
        chunk_scores: dict[str, list[float]] = defaultdict(list)
        symbols: dict[str, set[str]] = defaultdict(set)
        reasons: dict[str, set[str]] = defaultdict(set)
        for hit in hits:
            chunk_scores[hit.chunk.path].append(hit.score)
            if hit.chunk.symbol:
                symbols[hit.chunk.path].add(hit.chunk.symbol)
            reasons[hit.chunk.path].update(hit.reasons)
        by_file = {
            path: max(scores) + 0.1 * (sum(scores) - max(scores))
            for path, scores in chunk_scores.items()
        }
        best = max(by_file.values())
        results = [
            ContextItem(
                path=path,
                reason="; ".join(sorted(reasons[path])),
                score=round(min(1.0, score / max(best, 1e-9)), 4),
                symbols=sorted(symbols[path]),
            )
            for path, score in by_file.items()
        ]
        return sorted(results, key=lambda item: (-item.score, item.path))[:limit]
