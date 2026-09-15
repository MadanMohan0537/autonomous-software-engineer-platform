import subprocess
from pathlib import Path

from ase.workspace import WorkspaceManager


def test_worktree_lifecycle(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    (repository / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repository, check=True, capture_output=True)
    manager = WorkspaceManager(repository, tmp_path / "workspaces")
    workspace = manager.create("run-test")
    assert workspace.path.exists()
    assert (workspace.path / "file.txt").exists()
    manager.remove(workspace)
    assert not workspace.path.exists()
