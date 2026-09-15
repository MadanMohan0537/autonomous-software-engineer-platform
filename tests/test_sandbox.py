import subprocess
from pathlib import Path
from typing import Any

import pytest

from ase.policy import Policy, PolicyEngine
from ase.sandbox import CommandDenied, DockerSandbox, LocalSandbox, build_sandbox


def test_runs_allowlisted_command(tmp_path: Path) -> None:
    result = LocalSandbox(tmp_path).run(["python", "-c", "print('ok')"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"


def test_blocks_escape_and_unlisted_command(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    with pytest.raises(CommandDenied):
        sandbox.run(["curl", "https://example.com"])
    with pytest.raises(CommandDenied):
        sandbox.run(["python", "-V"], cwd=tmp_path.parent)


def test_rejects_empty_command(tmp_path: Path) -> None:
    with pytest.raises(CommandDenied):
        LocalSandbox(tmp_path).run([])


def test_local_timeout_is_reported(tmp_path: Path) -> None:
    def runner(*args: Any, **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1, output=b"partial", stderr=None)

    result = LocalSandbox(tmp_path, runner=runner).run(["python", "-c", "pass"], timeout=1)
    assert result.timed_out and result.exit_code == 124 and result.stdout == "partial"


def test_docker_argv_isolates_the_container(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    sandbox = DockerSandbox(tmp_path, image="img", runner=runner)
    (tmp_path / "sub").mkdir()
    result = sandbox.run(["pytest", "-q"], cwd=tmp_path / "sub")
    assert result.exit_code == 0 and result.cwd == "/workspace/sub"
    argv = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "none" in argv[argv.index("--network") + 1]
    assert "--cap-drop" in argv and "ALL" in argv
    assert argv[-2:] == ["pytest", "-q"]
    assert f"{tmp_path.resolve()}:/workspace" in argv

    open_network = DockerSandbox(
        tmp_path, policy=PolicyEngine(Policy(network_enabled=True)), runner=runner
    )
    open_network.run(["pytest"])
    assert calls[1][calls[1].index("--network") + 1] == "bridge"


def test_docker_denies_like_local_and_reports_timeouts(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    sandbox = DockerSandbox(tmp_path, runner=runner)
    with pytest.raises(CommandDenied):
        sandbox.run(["bash", "-c", "true"])
    with pytest.raises(CommandDenied):
        sandbox.run(["pytest"], cwd=tmp_path.parent)
    assert sandbox.run(["pytest"], timeout=1).timed_out


def test_build_sandbox_selects_backend(tmp_path: Path) -> None:
    assert isinstance(build_sandbox(tmp_path, "local"), LocalSandbox)
    assert isinstance(build_sandbox(tmp_path, "docker"), DockerSandbox)
    with pytest.raises(ValueError):
        build_sandbox(tmp_path, "cloud")
    assert isinstance(DockerSandbox.available("definitely-not-a-binary"), bool)
