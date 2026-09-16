"""Patch validation and application without shell interpretation."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ase.policy import PolicyEngine


@dataclass(frozen=True)
class PatchResult:
    applied: bool
    changed_files: list[str]
    stderr: str = ""


class PatchService:
    def __init__(self, workspace: Path, policy: PolicyEngine | None = None) -> None:
        self.workspace = workspace.resolve()
        self.policy = policy or PolicyEngine()

    def apply(self, unified_diff: str) -> PatchResult:
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", "-"],
            cwd=self.workspace,
            input=unified_diff,
            text=True,
            capture_output=True,
            check=False,
        )
        if check.returncode:
            return PatchResult(False, [], check.stderr)
        changed = self._paths(unified_diff)
        decision = self.policy.changed_files(changed)
        if not decision.allowed:
            return PatchResult(False, changed, "; ".join(decision.reasons))
        applied = subprocess.run(
            ["git", "apply", "--whitespace=error-all", "-"],
            cwd=self.workspace,
            input=unified_diff,
            text=True,
            capture_output=True,
            check=False,
        )
        return PatchResult(applied.returncode == 0, changed, applied.stderr)

    @staticmethod
    def _paths(diff: str) -> list[str]:
        paths = []
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                paths.append(line.removeprefix("+++ b/"))
        return sorted(set(paths))
