"""Progressive-delivery decisions: canary weights, SLO windows, promote or roll back.

This is a decision engine and a simulator, not a deployer. Repository policy denies
`deploy_production`, and `CanaryController.deploy` fails closed against that policy. What
the module teaches is the mechanism: how a canary weight steps up, what an SLO window
measures, and why a rollback decision must be automatic.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from ase.policy import PolicyEngine


class AuthorityDenied(RuntimeError):
    pass


class Slo(BaseModel):
    max_error_rate: float = Field(default=0.01, ge=0, le=1)
    max_p95_ms: float = Field(default=500.0, gt=0)
    min_requests: int = Field(default=50, ge=1)
    window_s: int = Field(default=120, ge=1)


class Window(BaseModel):
    """Observed metrics for the canary over one SLO window."""

    requests: int = Field(ge=0)
    errors: int = Field(ge=0)
    p95_ms: float = Field(ge=0)

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0


class Decision(BaseModel):
    action: str  # "promote" | "hold" | "rollback"
    weight: int
    reason: str


class CanaryReport(BaseModel):
    image: str
    steps: list[int]
    decisions: list[Decision] = Field(default_factory=list)
    final: str = "hold"  # "promoted" | "rolled_back" | "hold"


class CanaryController:
    def __init__(
        self, policy: PolicyEngine, slo: Slo | None = None, steps: Sequence[int] = (10, 25, 50, 100)
    ) -> None:
        self.policy = policy
        self.slo = slo or Slo()
        self.steps = list(steps)

    def evaluate(self, weight: int, window: Window) -> Decision:
        if window.requests < self.slo.min_requests:
            return Decision(
                action="hold",
                weight=weight,
                reason=f"only {window.requests} requests in the window",
            )
        if window.error_rate > self.slo.max_error_rate:
            return Decision(
                action="rollback",
                weight=0,
                reason=f"error rate {window.error_rate:.2%} above {self.slo.max_error_rate:.2%}",
            )
        if window.p95_ms > self.slo.max_p95_ms:
            return Decision(
                action="rollback",
                weight=0,
                reason=f"p95 {window.p95_ms:.0f}ms above {self.slo.max_p95_ms:.0f}ms",
            )
        position = self.steps.index(weight) if weight in self.steps else -1
        if position == -1 or position + 1 >= len(self.steps):
            return Decision(action="promote", weight=100, reason="SLO met at full traffic")
        return Decision(
            action="promote", weight=self.steps[position + 1], reason="SLO met, stepping up"
        )

    def simulate(self, image: str, windows: Sequence[Window]) -> CanaryReport:
        """Step through the weight ladder using one observed window per step."""
        report = CanaryReport(image=image, steps=self.steps)
        weight = self.steps[0]
        for window in windows:
            decision = self.evaluate(weight, window)
            report.decisions.append(decision)
            if decision.action == "rollback":
                report.final = "rolled_back"
                return report
            if decision.action == "promote":
                if weight == self.steps[-1]:
                    report.final = "promoted"
                    return report
                weight = decision.weight
        report.final = "hold"
        return report

    def deploy(self, image: str) -> None:
        """Real deployments need explicit authority, which repository policy denies by default."""
        decision = self.policy.authority("deploy_production")
        if not decision.allowed:
            raise AuthorityDenied("; ".join(decision.reasons))
        raise NotImplementedError("deployment backends are not part of this release")
