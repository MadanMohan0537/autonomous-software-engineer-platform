import subprocess
from pathlib import Path

import pytest

from ase.ide import IDEWorkspace, WorkspacePathError


def repository(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def calculate_total():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "base",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def test_tree_read_search_and_diff(tmp_path: Path) -> None:
    root = repository(tmp_path)
    ide = IDEWorkspace(root)
    assert any(entry.name == "src" and entry.kind == "directory" for entry in ide.tree())
    assert "calculate_total" in ide.read("src/app.py")
    assert ide.search("calculate_total")[0].line == 1
    (root / "src" / "app.py").write_text("def calculate_total():\n    return 2\n", encoding="utf-8")
    assert "return 2" in ide.diff("src/app.py")


def test_workspace_rejects_escape_binary_and_unknown_recipe(tmp_path: Path) -> None:
    ide = IDEWorkspace(repository(tmp_path))
    with pytest.raises(WorkspacePathError):
        ide.read("../secret.txt")
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    with pytest.raises(WorkspacePathError):
        ide.read("binary.bin")
    with pytest.raises(WorkspacePathError):
        ide.run_recipe("arbitrary-shell")


def test_search_requires_meaningful_query(tmp_path: Path) -> None:
    assert IDEWorkspace(repository(tmp_path)).search("x") == []
