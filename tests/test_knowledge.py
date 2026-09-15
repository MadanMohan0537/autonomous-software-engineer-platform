import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from ase.contracts import Issue, Relationship, Symbol
from ase.repo_intelligence import (
    AstPythonParser,
    CodeGraph,
    HashingEmbedder,
    KnowledgeIndex,
    KnowledgeRetriever,
    RepositoryIndexer,
    TreeSitterPythonParser,
    VoyageEmbedder,
    chunk_file,
    select_parser,
    tree_sitter_available,
)
from ase.repo_intelligence.indexer import FileRecord
from ase.repo_intelligence.lexical import BM25, tokenize
from ase.repo_intelligence.retrieval_eval import (
    RetrievalCase,
    cases_from_git_log,
    evaluate_retrieval,
)
from ase.repo_intelligence.vectors import VectorStore
from tests.conftest import git

SAMPLE = '''"""Billing helpers."""
import os
from decimal import Decimal


class Billing:
    """Handles billing."""

    def refund(self, invoice):
        """Refund an invoice."""
        total = calculate_refund(invoice)
        return self.notify(total)

    def notify(self, total):
        return os.path.join("x", str(total))


def calculate_refund(invoice):
    return Decimal(invoice.amount)


CONSTANT = 3
'''


def _parsers() -> list[object]:
    parsers: list[object] = [AstPythonParser()]
    if tree_sitter_available():
        parsers.append(TreeSitterPythonParser())
    return parsers


@pytest.mark.parametrize("parser", _parsers(), ids=lambda p: getattr(p, "name", "?"))
def test_parsers_share_one_output_shape(parser: AstPythonParser | TreeSitterPythonParser) -> None:
    parsed = parser.parse("billing.py", SAMPLE)
    names = [item.name for item in parsed.definitions]
    assert names == ["Billing", "Billing.refund", "Billing.notify", "calculate_refund"]
    refund = parsed.definitions[1]
    assert refund.parent == "Billing" and refund.docstring == "Refund an invoice."
    assert refund.start_line == 9 and refund.end_line == 12
    assert ("billing.py:Billing.refund", "calculate_refund", "calls") in {
        (r.source, r.target, r.kind) for r in parsed.relationships
    }
    assert ("billing.py:billing.py", "os", "imports") in {
        (r.source, r.target, r.kind) for r in parsed.relationships
    }
    assert parsed.parse_errors == 0


@pytest.mark.skipif(not tree_sitter_available(), reason="tree-sitter not installed")
def test_tree_sitter_recovers_definitions_around_syntax_errors() -> None:
    parsed = TreeSitterPythonParser().parse(
        "x.py", "def ok():\n    pass\n\ndef broken(:\n    pass\n"
    )
    assert parsed.parse_errors >= 1
    assert "x.py:ok" in parsed.symbols
    assert select_parser().name == "tree-sitter"
    assert select_parser("ast").name == "ast"


def test_chunking_by_definition_and_windows() -> None:
    parsed = AstPythonParser().parse("billing.py", SAMPLE)
    record = FileRecord("billing.py", "python", "d", SAMPLE, definitions=parsed.definitions)
    chunks = chunk_file(record)
    kinds = [(chunk.kind, chunk.symbol) for chunk in chunks]
    assert kinds[0] == ("header", None)
    assert ("definition", "Billing") in kinds and ("definition", "calculate_refund") in kinds
    assert kinds[-1] == ("header", None) and "CONSTANT" in chunks[-1].text
    assert all(chunk.tokens > 0 for chunk in chunks)

    # A class above the token budget is split into a header plus one chunk per method.
    small = chunk_file(record, max_tokens=40)
    assert ("definition", "Billing.refund") in [(c.kind, c.symbol) for c in small]

    markdown = FileRecord("README.md", "markdown", "d", "\n".join(f"line {i}" for i in range(130)))
    windows = chunk_file(markdown)
    assert [c.kind for c in windows] == ["window", "window", "window"]
    assert windows[0].start_line == 1 and windows[-1].end_line == 130
    assert chunk_file(FileRecord("empty.py", "python", "d", "")) == []


def test_tokenizer_and_bm25_prefer_identifier_matches() -> None:
    assert "refund" in tokenize("calculate_refund") and "calculate" in tokenize("calculateRefund")
    docs = [
        tokenize("def calculate_refund(invoice): return invoice.amount"),
        tokenize("def greeting(): pass"),
    ]
    index = BM25(docs)
    top = index.top(tokenize("refund calculation fails"))
    assert top and top[0][0] == 0
    assert BM25([]).top(["x"]) == []


def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder(dimension=64)
    matrix = embedder.embed(["refund invoice", "refund invoice", "unrelated words"])
    assert matrix.shape == (3, 64)
    assert np.allclose(matrix[0], matrix[1])
    assert abs(float(np.linalg.norm(matrix[0])) - 1.0) < 1e-5
    assert float(matrix[0] @ matrix[2]) < float(matrix[0] @ matrix[1])


def test_voyage_embedder_uses_http_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert (
            payload["model"] == "voyage-code-3" and request.headers["Authorization"] == "Bearer k"
        )
        data = [{"index": i, "embedding": [1.0, 2.0, 2.0]} for i, _ in enumerate(payload["input"])]
        return httpx.Response(200, json={"data": list(reversed(data))})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://voyage.test")
    embedder = VoyageEmbedder("k", dimension=3, client=client, batch_size=2)
    matrix = embedder.embed(["a", "b", "c"])
    assert matrix.shape == (3, 3) and abs(float(np.linalg.norm(matrix[0])) - 1.0) < 1e-6
    assert embedder.embed([]).shape == (0, 3)


def test_vector_store_round_trip(tmp_path: Path) -> None:
    store = VectorStore(["a", "b"], np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    assert store.search(np.asarray([1.0, 0.1], dtype=np.float32), k=1)[0][0] == "a"
    assert store.search(np.zeros(2, dtype=np.float32)) == []
    store.save(tmp_path)
    loaded = VectorStore.load(tmp_path)
    assert loaded.ids == ["a", "b"] and len(loaded) == 2
    assert len(VectorStore.load(tmp_path / "missing")) == 0


def test_code_graph_neighbors_and_repo_map() -> None:
    symbols = {
        "billing.py:Billing.refund": Symbol(
            id="billing.py:Billing.refund",
            path="billing.py",
            name="Billing.refund",
            kind="function",
            start_line=9,
            end_line=12,
            language="python",
        ),
        "billing.py:calculate_refund": Symbol(
            id="billing.py:calculate_refund",
            path="billing.py",
            name="calculate_refund",
            kind="function",
            start_line=18,
            end_line=19,
            language="python",
        ),
        "util.py:helper": Symbol(
            id="util.py:helper",
            path="util.py",
            name="helper",
            kind="function",
            start_line=1,
            end_line=2,
            language="python",
        ),
    }
    relationships = [
        Relationship(source="billing.py:Billing.refund", target="calculate_refund", kind="calls"),
        Relationship(source="billing.py:billing.py", target="util", kind="imports"),
        Relationship(source="billing.py:Billing.refund", target="missing_fn", kind="calls"),
    ]
    graph = CodeGraph.build(["billing.py", "util.py"], symbols, relationships)
    assert graph.graph.has_edge("sym:billing.py:Billing.refund", "sym:billing.py:calculate_refund")
    assert graph.graph.has_edge("file:billing.py", "file:util.py")
    near = graph.neighbors("billing.py:Billing.refund")
    assert {node["id"] for node in near.nodes} >= {"billing.py:calculate_refund", "billing.py"}
    assert any(edge["kind"] == "calls" for edge in near.edges)
    assert graph.neighbors("nope").nodes == []

    outline = graph.repo_map(["refund"], budget_tokens=500)
    assert outline.splitlines()[0] == "billing.py"
    assert "def calculate_refund" in outline
    assert graph.repo_map(["zzz"], budget_tokens=1)  # unknown focus still yields the top file
    restored = CodeGraph.from_dict(json.loads(json.dumps(graph.to_dict())))
    assert restored.graph.number_of_edges() == graph.graph.number_of_edges()
    assert CodeGraph().repo_map(["x"]) == ""


def test_knowledge_index_build_search_persist(fixture_repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    index = KnowledgeIndex.build(fixture_repo, "abc123", cache_dir=cache)
    assert index.meta.files >= 3 and index.meta.symbols >= 2 and index.meta.chunks >= 3
    hits = index.search("refund amount rejects zero adjustment", k=5)
    assert hits and hits[0].chunk.path == "pricing/calc.py"
    assert hits[0].chunk.symbol == "refund_amount"
    assert any("symbol name" in reason for reason in hits[0].reasons)
    assert "pricing/calc.py" in index.repo_map("refund adjustment")
    assert index.chunk_for_symbol("pricing/calc.py:refund_amount") is not None
    assert index.chunk_for_symbol("pricing/calc.py:nope") is None
    assert "pricing/calc.py" in index.files()
    assert index.neighbors("pricing/calc.py:refund_amount").nodes

    location = KnowledgeIndex.location(tmp_path / "index", "abc123")
    index.save(location)
    loaded = KnowledgeIndex.load(location)
    assert (
        loaded.meta.sha == "abc123"
        and loaded.search("discount percent")[0].chunk.symbol == "apply_discount"
    )

    # Second build reuses the embedding cache (same digests) and yields identical vectors.
    again = KnowledgeIndex.build(fixture_repo, "abc123", cache_dir=cache)
    assert np.allclose(again.vectors.matrix, index.vectors.matrix)
    assert (cache / "embeddings-hashing.json").exists()

    empty = KnowledgeIndex.build(tmp_path / "nothing-here", "sha")
    assert empty.search("anything") == [] and empty.meta.chunks == 0


def test_knowledge_retriever_and_recall(fixture_repo: Path) -> None:
    index = KnowledgeIndex.build(fixture_repo, "sha")
    repository = RepositoryIndexer().index(fixture_repo)
    issue = Issue(repository="demo", number=1, title="refund_amount rejects a zero adjustment")
    context = KnowledgeRetriever(index).retrieve(issue, repository, limit=3)
    assert context[0].path == "pricing/calc.py" and "refund_amount" in context[0].symbols
    assert context[0].reason
    assert (
        KnowledgeRetriever(KnowledgeIndex.build(fixture_repo / "nothing", "s")).retrieve(
            issue, repository
        )
        == []
    )

    report = evaluate_retrieval(
        index,
        [
            RetrievalCase(
                name="zero", query="zero adjustment refund", expected_files=["pricing/calc.py"]
            ),
            RetrievalCase(
                name="miss", query="kubernetes ingress", expected_files=["deploy/ingress.yaml"]
            ),
        ],
        k=3,
    )
    assert report.recall_at_k == 0.5 and report.cases[0].hit and not report.cases[1].hit


def test_cases_from_git_log(fixture_repo: Path) -> None:
    source = fixture_repo / "pricing" / "calc.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    (fixture_repo / "tests" / "test_calc.py").write_text("# only tests\n", encoding="utf-8")
    git(
        fixture_repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@e",
        "commit",
        "-qam",
        "fix: refund rounding",
    )
    cases = cases_from_git_log(fixture_repo, limit=5)
    assert cases[0].query.startswith("fix: refund rounding")
    assert cases[0].expected_files == ["pricing/calc.py"]
    assert all("tests/test_calc.py" not in case.expected_files for case in cases)
