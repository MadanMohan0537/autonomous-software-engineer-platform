"""Explainable hybrid retrieval over lexical, symbol, path, and graph signals."""

from __future__ import annotations

from collections import defaultdict

from ase.contracts import ContextItem, Issue
from ase.repo_intelligence.indexer import RepositoryIndex


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
