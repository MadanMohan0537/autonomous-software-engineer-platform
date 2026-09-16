"""Constrained execution adapters.

Two backends share one interface. `LocalSandbox` is for development fixtures and CI: it
constrains commands and paths but is not an OS security boundary. `DockerSandbox` runs
the same argument arrays inside a throwaway container with no network, a non-root user,
and CPU, memory, process and time limits. Both refuse shell interpreters: the agent
proposes argument arrays, never shell strings, and the policy engine decides.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ase.contracts import CommandResult
from ase.policy import PolicyEngine

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class CommandDenied(RuntimeError):
    pass


class Sandbox(Protocol):
    root: Path

    def run(
        self, argv: list[str], cwd: Path | None = None, timeout: int | None = None
    ) -> CommandResult: ...


def _clip(value: str, maximum: int = 20_000) -> str:
    return value if len(value) <= maximum else value[:maximum] + "\n...[truncated]"


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


class LocalSandbox:
    def __init__(
        self, root: Path, policy: PolicyEngine | None = None, runner: Runner | None = None
    ) -> None:
        self.root = root.resolve()
        self.policy = policy or PolicyEngine()
        self._runner = runner or subprocess.run

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
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        started = time.monotonic()
        try:
            completed = self._runner(
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
                stdout=_clip(completed.stdout),
                stderr=_clip(completed.stderr),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=argv,
                cwd=str(working),
                exit_code=124,
                stdout=_clip(_text(exc.stdout)),
                stderr=_clip(_text(exc.stderr)),
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )


class DockerSandbox:
    """Runs policy-checked argument arrays in a disposable container.

    The workspace is bind-mounted at /workspace. Nothing else from the host is visible,
    the network is disabled unless policy allows it, and the container is removed when the
    command exits. Credentials are never passed in: the host process keeps them.
    """

    def __init__(
        self,
        root: Path,
        image: str = "ase-sandbox:latest",
        policy: PolicyEngine | None = None,
        cpus: float = 2.0,
        memory: str = "2g",
        pids_limit: int = 256,
        docker_binary: str = "docker",
        runner: Runner | None = None,
    ) -> None:
        self.root = root.resolve()
        self.image = image
        self.policy = policy or PolicyEngine()
        self.cpus = cpus
        self.memory = memory
        self.pids_limit = pids_limit
        self.docker_binary = docker_binary
        self._runner = runner or subprocess.run

    @staticmethod
    def available(docker_binary: str = "docker") -> bool:
        return shutil.which(docker_binary) is not None

    def docker_argv(self, argv: list[str], workdir: str, timeout: int) -> list[str]:
        network = (
            ["--network", "bridge"]
            if self.policy.policy.network_enabled
            else [
                "--network",
                "none",
            ]
        )
        return [
            self.docker_binary,
            "run",
            "--rm",
            *network,
            "--cpus",
            str(self.cpus),
            "--memory",
            self.memory,
            "--pids-limit",
            str(self.pids_limit),
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--user",
            "10001:10001",
            "--stop-timeout",
            str(timeout),
            "-e",
            "CI=true",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{self.root}:/workspace",
            "-w",
            workdir,
            self.image,
            *argv,
        ]

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
        relative = working.relative_to(self.root).as_posix()
        workdir = "/workspace" if relative == "." else f"/workspace/{relative}"
        command = self.docker_argv(argv, workdir, limit)
        started = time.monotonic()
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=limit + 15,
                check=False,
            )
            return CommandResult(
                command=argv,
                cwd=workdir,
                exit_code=completed.returncode,
                stdout=_clip(completed.stdout),
                stderr=_clip(completed.stderr),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=argv,
                cwd=workdir,
                exit_code=124,
                stdout=_clip(_text(exc.stdout)),
                stderr=_clip(_text(exc.stderr)),
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )


def build_sandbox(
    root: Path,
    backend: str = "local",
    image: str = "ase-sandbox:latest",
    policy: PolicyEngine | None = None,
) -> Sandbox:
    if backend in {"docker", "container"}:
        return DockerSandbox(root, image=image, policy=policy)
    if backend == "local":
        return LocalSandbox(root, policy=policy)
    raise ValueError(f"unknown sandbox backend: {backend}")
