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
