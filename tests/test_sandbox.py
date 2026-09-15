from pathlib import Path

import pytest

from ase.sandbox import CommandDenied, LocalSandbox


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
