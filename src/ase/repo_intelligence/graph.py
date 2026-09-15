"""Code graph: files, symbols, calls and imports, plus a personalised-PageRank repo map.

The repo map is the trick that makes a small context window feel large: rank every
symbol by how connected it is to the terms in the issue, then print the top slice as an
outline the model can navigate from. It is an outline, not the code, so it costs a few
hundred tokens instead of the whole repository.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from ase.contracts import Relationship, Symbol
from ase.repo_intelligence.lexical import tokenize

COMMON_NAME_PENALTY = 0.15


@dataclass
class Subgraph:
    center: str
    nodes: list[dict[str, str]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)


def _short(name: str) -> str:
    return name.split(".")[-1]


def _module_candidates(module: str) -> list[str]:
    if not module:
        return []
    base = module.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py"]


class CodeGraph:
    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()

    @classmethod
    def build(
        cls, files: list[str], symbols: dict[str, Symbol], relationships: list[Relationship]
    ) -> CodeGraph:
        code = cls()
        graph = code.graph
        by_short: dict[str, list[str]] = defaultdict(list)
        file_set = set(files)
        for path in files:
            graph.add_node(f"file:{path}", kind="file", name=path, path=path)
        for symbol in symbols.values():
            node = f"sym:{symbol.id}"
            graph.add_node(
                node,
                kind=symbol.kind,
                name=symbol.name,
                path=symbol.path,
                start=symbol.start_line,
                end=symbol.end_line,
            )
            graph.add_edge(f"file:{symbol.path}", node, kind="defines", weight=1.0)
            by_short[_short(symbol.name)].append(node)
        for edge in relationships:
            source_path, _, source_name = edge.source.partition(":")
            source_node = (
                f"sym:{edge.source}" if f"sym:{edge.source}" in graph else f"file:{source_path}"
            )
            if source_node not in graph:
                continue
            if edge.kind == "calls":
                targets = by_short.get(_short(edge.target), [])
                if not targets:
                    continue
                weight = 1.0 / len(targets) if len(targets) <= 3 else COMMON_NAME_PENALTY
                for target in targets:
                    if target != source_node:
                        graph.add_edge(source_node, target, kind="calls", weight=weight)
            elif edge.kind == "imports":
                for candidate in _module_candidates(edge.target):
                    if candidate in file_set:
                        graph.add_edge(
                            f"file:{source_path}", f"file:{candidate}", kind="imports", weight=1.0
                        )
                        break
        return code

    # -- queries ----------------------------------------------------------------------
    def neighbors(self, symbol_id: str, depth: int = 1) -> Subgraph:
        center = f"sym:{symbol_id}"
        result = Subgraph(center=symbol_id)
        if center not in self.graph:
            return result
        undirected = self.graph.to_undirected(as_view=True)
        reached = nx.single_source_shortest_path_length(undirected, center, cutoff=depth)
        for node in sorted(reached):
            data = self.graph.nodes[node]
            result.nodes.append(
                {
                    "id": node.split(":", 1)[1],
                    "kind": str(data.get("kind", "")),
                    "path": str(data.get("path", "")),
                }
            )
        for source, target, data in self.graph.edges(data=True):
            if source in reached and target in reached:
                result.edges.append(
                    {
                        "source": source.split(":", 1)[1],
                        "target": target.split(":", 1)[1],
                        "kind": str(data.get("kind", "")),
                    }
                )
        return result

    def rank(self, focus_terms: list[str]) -> dict[str, float]:
        """Personalised PageRank toward nodes whose names share tokens with the focus."""
        if self.graph.number_of_nodes() == 0:
            return {}
        focus = {term.lower() for term in focus_terms}
        personalization: dict[str, float] = {}
        for node, data in self.graph.nodes(data=True):
            name_tokens = set(tokenize(str(data.get("name", "")))) | set(
                tokenize(str(data.get("path", "")))
            )
            overlap = len(name_tokens & focus)
            if overlap:
                personalization[node] = float(overlap)
        if not personalization:
            personalization = dict.fromkeys(self.graph.nodes, 1.0)
        scores = nx.pagerank(
            self.graph, alpha=0.85, personalization=personalization, weight="weight"
        )
        return {str(node): float(score) for node, score in scores.items()}

    def repo_map(self, focus_terms: list[str], budget_tokens: int = 2_000) -> str:
        scores = self.rank(focus_terms)
        if not scores:
            return ""
        file_scores: dict[str, float] = defaultdict(float)
        symbols_by_file: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for node, score in scores.items():
            data = self.graph.nodes[node]
            path = str(data.get("path", ""))
            file_scores[path] += score
            if data.get("kind") != "file":
                label = f"{'class' if data.get('kind') == 'class' else 'def'} {data.get('name')}"
                symbols_by_file[path].append(
                    (score, f"{label}  (L{data.get('start')}-{data.get('end')})")
                )
        lines: list[str] = []
        used = 0
        for path, _ in sorted(file_scores.items(), key=lambda item: -item[1]):
            block = [path]
            for _, label in sorted(symbols_by_file[path], key=lambda item: -item[0])[:12]:
                block.append(f"    {label}")
            cost = sum(len(line) for line in block) // 4 + len(block)
            if used + cost > budget_tokens and lines:
                break
            lines.extend(block)
            used += cost
        return "\n".join(lines)

    # -- persistence ------------------------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [{"id": node, **data} for node, data in self.graph.nodes(data=True)],
            "edges": [
                {"source": source, "target": target, **data}
                for source, target, data in self.graph.edges(data=True)
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> CodeGraph:
        code = cls()
        nodes = payload.get("nodes", [])
        edges = payload.get("edges", [])
        if isinstance(nodes, list):
            for item in nodes:
                if isinstance(item, dict):
                    data = dict(item)
                    identifier = str(data.pop("id"))
                    code.graph.add_node(identifier, **data)
        if isinstance(edges, list):
            for item in edges:
                if isinstance(item, dict):
                    data = dict(item)
                    source = str(data.pop("source"))
                    target = str(data.pop("target"))
                    code.graph.add_edge(source, target, **data)
        return code
