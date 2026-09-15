from pathlib import Path

from ase.contracts import Issue
from ase.repo_intelligence import HybridRetriever, RepositoryIndexer


def test_indexes_python_symbols_and_calls(tmp_path: Path) -> None:
    source = tmp_path / "billing.py"
    source.write_text(
        "def refund(invoice):\n    return calculate_refund(invoice)\n", encoding="utf-8"
    )
    index = RepositoryIndexer().index(tmp_path)
    assert "billing.py:refund" in index.symbols
    assert any(
        edge.kind == "calls" and edge.target == "calculate_refund" for edge in index.relationships
    )


def test_retrieval_explains_file_selection(tmp_path: Path) -> None:
    (tmp_path / "billing.py").write_text("def calculate_refund():\n    pass\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("def greeting():\n    pass\n", encoding="utf-8")
    index = RepositoryIndexer().index(tmp_path)
    results = HybridRetriever().retrieve(
        Issue(repository="demo", number=1, title="Refund calculation fails"), index
    )
    assert results[0].path == "billing.py"
    assert results[0].reason


def test_invalid_python_is_retained_without_symbols(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(:", encoding="utf-8")
    index = RepositoryIndexer().index(tmp_path)
    assert "broken.py" in index.files
    assert not index.symbols


def test_manifest_and_multiple_language_detection(tmp_path: Path) -> None:
    (tmp_path / "web.ts").write_text("export const value = 1", encoding="utf-8")
    output = tmp_path / "artifacts" / "index.json"
    index = RepositoryIndexer().index(tmp_path)
    index.write_manifest(output)
    assert index.files["web.ts"].language == "typescript"
    assert '"web.ts"' in output.read_text(encoding="utf-8")
