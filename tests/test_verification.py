from pathlib import Path

from ase.verification import PatchVerifier, VerificationCommand


def test_detects_added_skip_markers() -> None:
    result = PatchVerifier.test_integrity("+@pytest.mark.skip\n+def test_real(): pass")
    assert not result.passed


def test_accepts_normal_test_diff() -> None:
    result = PatchVerifier.test_integrity("+def test_real():\n+    assert True")
    assert result.passed


def test_runs_custom_verification_command(tmp_path: Path) -> None:
    report = PatchVerifier(tmp_path).verify(
        ["src/app.py"],
        [VerificationCommand("smoke", ["python", "-c", "print('verified')"])],
    )
    assert report.passed
    assert report.checks[-1].command
    assert "verified" in report.checks[-1].details


def test_stops_when_patch_policy_fails(tmp_path: Path) -> None:
    report = PatchVerifier(tmp_path).verify([".env"])
    assert not report.passed
    assert report.policy_violations
