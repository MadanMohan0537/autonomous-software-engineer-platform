"""The knowledge index: chunks, lexical + vector retrieval fused with RRF, and a code graph.

Everything is keyed by the commit SHA it was built from. An index for a different SHA
is a different index; the agent never retrieves stale code.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from ase.contracts import Symbol, looks_like_test
from ase.repo_intelligence.chunks import Chunk, chunk_file
from ase.repo_intelligence.embeddings import Embedder, HashingEmbedder
from ase.repo_intelligence.graph import CodeGraph, Subgraph
from ase.repo_intelligence.indexer import RepositoryIndexer
from ase.repo_intelligence.lexical import BM25, tokenize
from ase.repo_intelligence.vectors import VectorStore

RRF_K = 60
TEST_FILE_WEIGHT = 0.75  # tests are useful context, but source definitions come first


class SearchHit(BaseModel):
    chunk: Chunk
    score: float
    reasons: list[str] = Field(default_factory=list)


class IndexMeta(BaseModel):
    sha: str
    root: str
    embedder: str
    parser: str
    files: int
    chunks: int
    symbols: int


class KnowledgeIndex:
    def __init__(
        self,
        meta: IndexMeta,
        chunks: list[Chunk],
        symbols: dict[str, Symbol],
        graph: CodeGraph,
        vectors: VectorStore,
        embedder: Embedder,
    ) -> None:
        self.meta = meta
        self.chunks = chunks
        self.symbols = symbols
        self.graph = graph
        self.vectors = vectors
        self.embedder = embedder
        self._by_id = {chunk.id: chunk for chunk in chunks}
        self._bm25 = BM25([tokenize(chunk.text) for chunk in chunks])

    # -- build / persist --------------------------------------------------------------
    @classmethod
    def build(
        cls,
        root: Path,
        sha: str,
        embedder: Embedder | None = None,
        indexer: RepositoryIndexer | None = None,
        cache_dir: Path | None = None,
    ) -> KnowledgeIndex:
        embedder = embedder or HashingEmbedder()
        indexer = indexer or RepositoryIndexer()
        repository = indexer.index(root)
        chunks: list[Chunk] = []
        for record in repository.files.values():
            chunks.extend(chunk_file(record))
        matrix = _embed_with_cache(embedder, chunks, cache_dir)
        vectors = VectorStore([chunk.id for chunk in chunks], matrix)
        graph = CodeGraph.build(
            list(repository.files), repository.symbols, repository.relationships
        )
        meta = IndexMeta(
            sha=sha,
            root=str(repository.root),
            embedder=embedder.name,
            parser=repository.parser_name,
            files=len(repository.files),
            chunks=len(chunks),
            symbols=len(repository.symbols),
        )
        return cls(meta, chunks, dict(repository.symbols), graph, vectors, embedder)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "meta.json").write_text(self.meta.model_dump_json(indent=2), encoding="utf-8")
        (directory / "chunks.json").write_text(
            json.dumps([chunk.model_dump() for chunk in self.chunks]), encoding="utf-8"
        )
        (directory / "symbols.json").write_text(
            json.dumps({key: value.model_dump() for key, value in self.symbols.items()}),
            encoding="utf-8",
        )
        (directory / "graph.json").write_text(json.dumps(self.graph.to_dict()), encoding="utf-8")
        self.vectors.save(directory)

    @classmethod
    def load(cls, directory: Path, embedder: Embedder | None = None) -> KnowledgeIndex:
        meta = IndexMeta.model_validate_json((directory / "meta.json").read_text(encoding="utf-8"))
        chunks = [
            Chunk.model_validate(item)
            for item in json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        ]
        symbols = {
            key: Symbol.model_validate(value)
            for key, value in json.loads(
                (directory / "symbols.json").read_text(encoding="utf-8")
            ).items()
        }
        graph = CodeGraph.from_dict(
            json.loads((directory / "graph.json").read_text(encoding="utf-8"))
        )
        vectors = VectorStore.load(directory)
        return cls(meta, chunks, symbols, graph, vectors, embedder or HashingEmbedder())

    @staticmethod
    def location(index_dir: Path, sha: str) -> Path:
        return index_dir / sha

    # -- queries ----------------------------------------------------------------------
    def search(self, query: str, k: int = 10) -> list[SearchHit]:
        if not self.chunks:
            return []
        query_tokens = tokenize(query)
        fused: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}

        for rank, (position, _) in enumerate(self._bm25.top(query_tokens, k=k * 3), start=1):
            chunk_id = self.chunks[position].id
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            reasons.setdefault(chunk_id, []).append(f"lexical rank {rank}")

        if len(self.vectors):
            query_vector = self.embedder.embed([query])[0]
            for rank, (chunk_id, _) in enumerate(self.vectors.search(query_vector, k * 3), start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
                reasons.setdefault(chunk_id, []).append(f"semantic rank {rank}")

        focus = set(query_tokens)
        for chunk_id in list(fused):
            chunk = self._by_id[chunk_id]
            if chunk.symbol and focus & set(tokenize(chunk.symbol)):
                fused[chunk_id] += 1.0 / RRF_K
                reasons[chunk_id].append("symbol name matches issue terms")
            if looks_like_test(chunk.path):
                fused[chunk_id] *= TEST_FILE_WEIGHT
                reasons[chunk_id].append("test file, down-weighted")

        ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:k]
        return [
            SearchHit(chunk=self._by_id[chunk_id], score=round(score, 6), reasons=reasons[chunk_id])
            for chunk_id, score in ordered
        ]

    def neighbors(self, symbol_id: str, depth: int = 1) -> Subgraph:
        return self.graph.neighbors(symbol_id, depth)

    def repo_map(self, focus: str | Sequence[str], budget_tokens: int = 2_000) -> str:
        terms = tokenize(focus) if isinstance(focus, str) else list(focus)
        return self.graph.repo_map(terms, budget_tokens)

    def chunk_for_symbol(self, symbol_id: str) -> Chunk | None:
        path, _, name = symbol_id.partition(":")
        for chunk in self.chunks:
            if chunk.path == path and chunk.symbol == name:
                return chunk
        return None

    def files(self) -> list[str]:
        return sorted({chunk.path for chunk in self.chunks})


def _embed_with_cache(
    embedder: Embedder, chunks: list[Chunk], cache_dir: Path | None
) -> np.ndarray[tuple[int, int], np.dtype[np.float32]]:
    """Embed chunks, reusing cached vectors keyed by content digest and embedder name."""
    cache_path = (
        cache_dir / f"embeddings-{embedder.name.replace(':', '_')}.json" if cache_dir else None
    )
    cache: dict[str, list[float]] = {}
    if cache_path and cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    vectors: list[np.ndarray[tuple[int], np.dtype[np.float32]] | None] = [None] * len(chunks)
    missing: list[int] = []
    for position, chunk in enumerate(chunks):
        cached = cache.get(chunk.digest)
        if cached is not None and len(cached) == embedder.dimension:
            vectors[position] = np.asarray(cached, dtype=np.float32)
        else:
            missing.append(position)
    if missing:
        fresh = embedder.embed([chunks[position].text for position in missing])
        for row, position in enumerate(missing):
            vectors[position] = fresh[row]
            cache[chunks[position].digest] = [float(value) for value in fresh[row]]
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    if not chunks:
        return np.zeros((0, embedder.dimension), dtype=np.float32)
    return np.vstack([vector for vector in vectors if vector is not None]).astype(np.float32)
