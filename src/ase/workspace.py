"""Isolated git worktrees for agent runs.

One run, one worktree, pinned to a commit. The platform (not the agent) creates and
removes worktrees, so `git worktree` is never on the agent's command allowlist path.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ase.contracts import new_id

EXCLUDED = [".", ":(exclude).ase-run", ":(exclude)__pycache__"]

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    path: Path
    repository: Path
    base_sha: str
    name: str


class WorkspaceManager:
    def __init__(
        self, base_dir: Path, git_binary: str = "git", runner: Runner | None = None
    ) -> None:
        self.base_dir = base_dir.resolve()
        self.git_binary = git_binary
        self._runner = runner or subprocess.run

    def _git(self, cwd: Path, *args: str) -> str:
        completed = self._runner(
            [self.git_binary, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0:
            raise WorkspaceError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
        return str(completed.stdout)

    def head_sha(self, repository: Path) -> str:
        return self._git(repository, "rev-parse", "HEAD").strip()

    def create(self, repository: Path, base_sha: str | None = None) -> Workspace:
        repository = repository.resolve()
        sha = base_sha or self.head_sha(repository)
        name = new_id("ws")
        path = self.base_dir / name
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._git(repository, "worktree", "add", "--detach", str(path), sha)
        return Workspace(path=path, repository=repository, base_sha=sha, name=name)

    def diff(self, workspace: Workspace) -> str:
        """Unified diff of every change in the worktree, including new files."""
        self._git(workspace.path, "add", "--intent-to-add", "--all", "--", *EXCLUDED)
        return self._git(workspace.path, "diff", "--no-color", "--no-ext-diff", "--", *EXCLUDED)

    def changed_files(self, workspace: Workspace) -> list[str]:
        self._git(workspace.path, "add", "--intent-to-add", "--all", "--", *EXCLUDED)
        output = self._git(workspace.path, "diff", "--name-only", "--", *EXCLUDED)
        return sorted(line.strip() for line in output.splitlines() if line.strip())

    def commit(self, workspace: Workspace, message: str) -> str:
        """Commit the worktree's changes and return the new SHA (used by delivery only)."""
        self._git(workspace.path, "add", "--all", "--", *EXCLUDED)
        self._git(
            workspace.path,
            "-c",
            "user.name=ase-agent",
            "-c",
            "user.email=ase-agent@localhost",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            message,
        )
        return self._git(workspace.path, "rev-parse", "HEAD").strip()

    def remove(self, workspace: Workspace) -> None:
        try:
            self._git(workspace.repository, "worktree", "remove", "--force", str(workspace.path))
        except WorkspaceError:
            shutil.rmtree(workspace.path, ignore_errors=True)
            self._git(workspace.repository, "worktree", "prune")
