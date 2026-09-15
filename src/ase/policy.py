"""Deterministic authority boundaries for agent-proposed actions.

Policy lives outside the model boundary. It can be loaded from a versioned
`.ase/policy.yaml`, and unknown or missing values fail closed to the typed defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from ase.contracts import looks_like_test

DEFAULT_ALLOWED_COMMANDS = frozenset(
    {"python", "python3", "pytest", "ruff", "mypy", "git", "npm", "npx", "coverage", "mutmut"}
)


@dataclass(frozen=True)
class Policy:
    allowed_commands: frozenset[str] = DEFAULT_ALLOWED_COMMANDS
    denied_git_subcommands: frozenset[str] = frozenset(
        {"push", "reset", "clean", "checkout", "switch", "merge", "rebase"}
    )
    denied_paths: frozenset[str] = frozenset({".git", ".env", ".ssh", "node_modules", ".venv"})
    max_changed_files: int = 25
    max_command_seconds: int = 120
    network_enabled: bool = False
    require_plan_approval: bool = True
    require_pr_approval: bool = True
    allow_merge: bool = False
    allow_deploy: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Policy:
        """Build a policy from the `.ase/policy.yaml` schema. Missing keys keep defaults."""
        policy = cls()
        authority = data.get("authority") or {}
        execution = data.get("execution") or {}
        if not isinstance(authority, dict) or not isinstance(execution, dict):
            raise ValueError("policy sections must be mappings")
        updates: dict[str, Any] = {}
        if "open_draft_pull_request" in authority:
            updates["require_pr_approval"] = authority["open_draft_pull_request"] != "automatic"
        if "merge_pull_request" in authority:
            updates["allow_merge"] = authority["merge_pull_request"] == "allowed"
        if "deploy_production" in authority:
            updates["allow_deploy"] = authority["deploy_production"] == "allowed"
        if "plan" in authority:
            updates["require_plan_approval"] = authority["plan"] != "automatic"
        if "network" in execution:
            updates["network_enabled"] = execution["network"] == "allowed"
        if "max_seconds" in execution:
            updates["max_command_seconds"] = int(execution["max_seconds"])
        if "max_changed_files" in execution:
            updates["max_changed_files"] = int(execution["max_changed_files"])
        if "denied_paths" in execution:
            updates["denied_paths"] = frozenset(str(item) for item in execution["denied_paths"])
        if "allowed_commands" in execution:
            updates["allowed_commands"] = frozenset(
                str(item) for item in execution["allowed_commands"]
            )
        return replace(policy, **updates)

    @classmethod
    def from_file(cls, path: Path) -> Policy:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("policy file must contain a mapping")
        return cls.from_mapping(loaded)

    @classmethod
    def for_repository(cls, root: Path) -> Policy:
        """Load `<root>/.ase/policy.yaml` when present, otherwise the typed defaults."""
        candidate = root / ".ase" / "policy.yaml"
        return cls.from_file(candidate) if candidate.is_file() else cls()


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

    def write_path(self, path: str, task_is_tests: bool = False) -> PolicyDecision:
        """Agent writes to test files are denied unless the task itself is about tests."""
        decision = self.changed_files([path])
        if not decision.allowed:
            return decision
        if looks_like_test(path) and not task_is_tests:
            return PolicyDecision(False, [f"writes to test files are denied for this task: {path}"])
        return PolicyDecision(True, [])

    def authority(self, action: str) -> PolicyDecision:
        """Consequential actions are denied unless the policy explicitly grants them."""
        grants = {
            "merge_pull_request": self.policy.allow_merge,
            "deploy_production": self.policy.allow_deploy,
            "open_draft_pull_request": True,
        }
        if action not in grants:
            return PolicyDecision(False, [f"unknown action: {action}"])
        return PolicyDecision(grants[action], [] if grants[action] else [f"{action} is denied"])
