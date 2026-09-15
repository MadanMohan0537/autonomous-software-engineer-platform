from pathlib import Path

import pytest

from ase.policy import Policy, PolicyEngine


def test_allows_safe_test_command() -> None:
    assert PolicyEngine().command(["pytest", "tests/test_policy.py"]).allowed


def test_denies_shell_and_git_push() -> None:
    engine = PolicyEngine()
    assert not engine.command(["bash", "-c", "anything"]).allowed
    assert not engine.command(["git", "push"]).allowed


def test_patch_scope_and_sensitive_paths() -> None:
    engine = PolicyEngine(Policy(max_changed_files=1))
    assert not engine.changed_files(["a.py", "b.py"]).allowed
    assert not engine.changed_files([".env"]).allowed


def test_test_file_writes_need_a_test_task() -> None:
    engine = PolicyEngine()
    assert not engine.write_path("tests/test_calc.py").allowed
    assert engine.write_path("tests/test_calc.py", task_is_tests=True).allowed
    assert engine.write_path("pricing/calc.py").allowed
    assert not engine.write_path(".git/config").allowed


def test_authority_fails_closed() -> None:
    engine = PolicyEngine()
    assert engine.authority("open_draft_pull_request").allowed
    assert not engine.authority("merge_pull_request").allowed
    assert not engine.authority("deploy_production").allowed
    assert not engine.authority("launch_rockets").allowed


def test_policy_loads_from_repository_yaml(tmp_path: Path) -> None:
    (tmp_path / ".ase").mkdir()
    (tmp_path / ".ase" / "policy.yaml").write_text(
        "version: 1\n"
        "authority:\n  open_draft_pull_request: human_approval\n"
        "  merge_pull_request: denied\n  deploy_production: allowed\n"
        "execution:\n  network: allowed\n  max_seconds: 30\n  max_changed_files: 3\n"
        "  denied_paths: [.git, secrets]\n  allowed_commands: [python, pytest]\n",
        encoding="utf-8",
    )
    policy = Policy.for_repository(tmp_path)
    assert policy.require_pr_approval and not policy.allow_merge and policy.allow_deploy
    assert policy.network_enabled and policy.max_command_seconds == 30
    assert policy.max_changed_files == 3 and "secrets" in policy.denied_paths
    assert policy.allowed_commands == frozenset({"python", "pytest"})
    assert Policy.for_repository(tmp_path / "missing") == Policy()


def test_policy_rejects_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        Policy.from_file(path)
    with pytest.raises(ValueError):
        Policy.from_mapping({"authority": ["bad"]})


def test_repository_policy_file_matches_defaults() -> None:
    policy = Policy.for_repository(Path(__file__).resolve().parents[1])
    assert policy.require_pr_approval
    assert not policy.allow_merge and not policy.allow_deploy
    assert not policy.network_enabled
