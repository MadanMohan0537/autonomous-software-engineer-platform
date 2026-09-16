import subprocess
from pathlib import Path

from ase.workspace import WorkspaceManager


def test_named_branch_worktree_lifecycle(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    (repository / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "base"], cwd=repository, check=True, capture_output=True
    )

    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repository, branch="ase/run-test")
    assert workspace.path.exists() and (workspace.path / "file.txt").exists()
    assert workspace.branch == "ase/run-test" and workspace.base_revision == workspace.base_sha
    branches = subprocess.run(
        ["git", "branch", "--list", "ase/run-test"], cwd=repository, capture_output=True, text=True
    ).stdout
    assert "ase/run-test" in branches
    manager.remove(workspace)
    assert not workspace.path.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", "ase/run-test"], cwd=repository, capture_output=True, text=True
    ).stdout
    assert "ase/run-test" not in branches


def test_default_root_is_a_temporary_directory() -> None:
    manager = WorkspaceManager()
    assert manager.base_dir.name == "ase-workspaces"
