"""Shared fixtures: a tiny git repository with a seeded bug and a failing test."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

CALC_SOURCE = '''"""Tiny pricing helpers used by the fixture repository."""


def apply_discount(price: float, percent: float) -> float:
    """Return the price after a percentage discount. Zero is a valid discount."""
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (1 - percent / 100), 2)


def refund_amount(paid: float, adjustment: float) -> float:
    """Refund the paid amount minus a non-negative adjustment."""
    if adjustment <= 0:
        raise ValueError("adjustment must be non-negative")
    return round(paid - adjustment, 2)
'''

TEST_SOURCE = """from pricing.calc import apply_discount, refund_amount
import pytest


def test_discount_basic():
    assert apply_discount(100, 10) == 90


def test_discount_zero():
    assert apply_discount(50, 0) == 50


def test_refund_zero_adjustment_is_allowed():
    assert refund_amount(20, 0) == 20


def test_refund_negative_adjustment_rejected():
    with pytest.raises(ValueError):
        refund_amount(20, -1)
"""


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60
    )
    return completed.stdout


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A git repo containing package `pricing` with a seeded bug (`adjustment <= 0`)."""
    root = tmp_path / "repo"
    (root / "pricing").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pricing" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pricing" / "calc.py").write_text(CALC_SOURCE, encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_calc.py").write_text(TEST_SOURCE, encoding="utf-8")
    (root / "README.md").write_text("# pricing fixture\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8"
    )
    git(root, "init", "-q", "-b", "main")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    return root
