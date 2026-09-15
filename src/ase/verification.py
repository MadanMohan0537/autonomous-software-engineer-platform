"""Patch verification and test-integrity checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ase.contracts import CheckResult, EvaluationReport
from ase.policy import PolicyEngine
from ase.sandbox import LocalSandbox


@dataclass(frozen=True)
class VerificationCommand:
    name: str
    argv: list[str]


DEFAULT_COMMANDS = [
    VerificationCommand("lint", ["ruff", "check", "."]),
    VerificationCommand("types", ["mypy", "src"]),
    VerificationCommand("tests", ["pytest"]),
]


class PatchVerifier:
    def __init__(self, root: Path, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self.sandbox = LocalSandbox(root, self.policy)

    def verify(
        self,
        changed_files: list[str],
        commands: list[VerificationCommand] | None = None,
    ) -> EvaluationReport:
        policy_decision = self.policy.changed_files(changed_files)
        checks: list[CheckResult] = [
            CheckResult(
                name="patch-policy",
                passed=policy_decision.allowed,
                details="; ".join(policy_decision.reasons) or "patch scope accepted",
            )
        ]
        if not policy_decision.allowed:
            return EvaluationReport(
                passed=False,
                checks=checks,
                changed_files=changed_files,
                policy_violations=policy_decision.reasons,
            )
        for item in commands or DEFAULT_COMMANDS:
            result = self.sandbox.run(item.argv)
            checks.append(
                CheckResult(
                    name=item.name,
                    passed=result.exit_code == 0,
                    details=result.stderr or result.stdout,
                    command=result,
                )
            )
        return EvaluationReport(
            passed=all(item.passed for item in checks),
            checks=checks,
            changed_files=changed_files,
        )

    @staticmethod
    def test_integrity(diff: str) -> CheckResult:
        suspicious = (
            "@pytest.mark.skip",
            "pytest.skip(",
            ".skip(",
            "test.only(",
            "describe.only(",
        )
        added = [
            line[1:]
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        findings = [token for token in suspicious if any(token in line for line in added)]
        return CheckResult(
            name="test-integrity",
            passed=not findings,
            details="no skipped or focused tests added" if not findings else f"found {findings}",
        )
