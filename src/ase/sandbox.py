"""Constrained local execution adapter.

The local adapter is for development and CI. Production deployments should replace it
with a container or microVM backend while retaining the same interface.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ase.contracts import CommandResult
from ase.policy import PolicyEngine


class CommandDenied(RuntimeError):
    pass


class LocalSandbox:
    def __init__(self, root: Path, policy: PolicyEngine | None = None) -> None:
        self.root = root.resolve()
        self.policy = policy or PolicyEngine()

    def run(
        self, argv: list[str], cwd: Path | None = None, timeout: int | None = None
    ) -> CommandResult:
        decision = self.policy.command(argv)
        if not decision.allowed:
            raise CommandDenied("; ".join(decision.reasons))
        working = (cwd or self.root).resolve()
        if working != self.root and self.root not in working.parents:
            raise CommandDenied("working directory escapes sandbox root")
        maximum = self.policy.policy.max_command_seconds
        limit = min(timeout or maximum, maximum)
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "LANG": "C.UTF-8",
            "CI": "true",
        }
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=working,
                env=environment,
                capture_output=True,
                text=True,
                timeout=limit,
                check=False,
            )
            return CommandResult(
                command=argv,
                cwd=str(working),
                exit_code=completed.returncode,
                stdout=self._clip(completed.stdout),
                stderr=self._clip(completed.stderr),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=argv,
                cwd=str(working),
                exit_code=124,
                stdout=self._clip(self._text(exc.stdout)),
                stderr=self._clip(self._text(exc.stderr)),
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )

    @staticmethod
    def _clip(value: str, maximum: int = 20_000) -> str:
        return value if len(value) <= maximum else value[:maximum] + "\n...[truncated]"

    @staticmethod
    def _text(value: bytes | str | None) -> str:
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value or ""
