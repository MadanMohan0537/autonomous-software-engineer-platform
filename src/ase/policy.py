"""Deterministic authority boundaries for agent-proposed actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Policy:
    allowed_commands: frozenset[str] = frozenset(
        {"python", "python3", "pytest", "ruff", "mypy", "git", "npm", "npx"}
    )
    denied_git_subcommands: frozenset[str] = frozenset(
        {"push", "reset", "clean", "checkout", "switch", "merge", "rebase"}
    )
    denied_paths: frozenset[str] = frozenset({".git", ".env", ".ssh", "node_modules", ".venv"})
    max_changed_files: int = 25
    max_command_seconds: int = 120
    network_enabled: bool = False
    require_plan_approval: bool = True
    require_pr_approval: bool = True


@dataclass
class PolicyDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class PolicyEngine:
    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or Policy()

    def command(self, argv: list[str]) -> PolicyDecision:
        if not argv:
            return PolicyDecision(False, ["empty command"])
        executable = Path(argv[0]).name
        reasons: list[str] = []
        if executable not in self.policy.allowed_commands:
            reasons.append(f"command is not allowlisted: {executable}")
        if executable == "git" and len(argv) > 1 and argv[1] in self.policy.denied_git_subcommands:
            reasons.append(f"git subcommand is denied: {argv[1]}")
        shell_tokens = {"sh", "bash", "zsh", "cmd", "powershell", "pwsh"}
        if executable in shell_tokens:
            reasons.append("shell interpreters are not allowed")
        return PolicyDecision(not reasons, reasons)

    def changed_files(self, paths: list[str]) -> PolicyDecision:
        reasons: list[str] = []
        if len(paths) > self.policy.max_changed_files:
            reasons.append(
                f"patch changes {len(paths)} files; maximum is {self.policy.max_changed_files}"
            )
        for value in paths:
            parts = set(Path(value).parts)
            denied = parts & self.policy.denied_paths
            if denied:
                reasons.append(f"path contains denied segment {sorted(denied)[0]}: {value}")
        return PolicyDecision(not reasons, reasons)
