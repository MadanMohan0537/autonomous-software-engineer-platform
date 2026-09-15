"""Disposable Git worktree lifecycle with validated repository paths."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Workspace:
    path: Path
    branch: str
    base_revision: str


class WorkspaceManager:
    def __init__(self, repository: Path, workspace_root: Path | None = None) -> None:
        self.repository = repository.resolve()
        default_root = Path(tempfile.gettempdir()) / "ase-workspaces"
        self.workspace_root = (workspace_root or default_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def create(self, run_id: str, base_revision: str = "HEAD") -> Workspace:
        branch = f"ase/{run_id}"
        path = Path(tempfile.mkdtemp(prefix=f"{run_id}-", dir=self.workspace_root))
        path.rmdir()
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(path), base_revision],
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "failed to create worktree")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=path, text=True
        ).strip()
        return Workspace(path=path, branch=branch, base_revision=revision)

    def remove(self, workspace: Workspace) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(workspace.path)],
            cwd=self.repository,
            capture_output=True,
            check=False,
        )
        if workspace.path.exists():
            shutil.rmtree(workspace.path)
