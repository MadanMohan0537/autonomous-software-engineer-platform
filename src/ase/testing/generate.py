"""Coverage-guided test generation with mutation-backed validation.

A generated test is kept only when it passes on the current code AND kills at least one
mutant of the file it targets. A test that passes but kills nothing is a tautology, and
tautologies are worse than no test: they raise the coverage number without checking
anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ase.agent.tools import python_syntax_error
from ase.contracts import looks_like_test
from ase.llm.client import LLMClient, Message
from ase.sandbox import Sandbox
from ase.testing.coverage_report import CoverageReport, describe_uncovered
from ase.testing.mutation import MutationRunner
from ase.testrun import PytestRunner

GENERATOR_SYSTEM = """You write pytest tests for uncovered branches of a Python module.
Tests must import from the repository's own modules, must be deterministic, must not skip,
and must check behaviour with precise assertions (values, raised exceptions), not just call
functions. Reply with a JSON array only:
[{"path": "tests/test_generated_<module>.py", "target": "<module path>",
  "code": "<file contents>"}]"""


class GeneratedTest(BaseModel):
    path: str
    target: str
    code: str
    status: str = "pending"  # pending | kept | failed | tautology | rejected
    reason: str = ""
    killed: int = 0


class GenerationResult(BaseModel):
    tests: list[GeneratedTest] = Field(default_factory=list)
    prompt_tokens: int = 0
    output_tokens: int = 0

    @property
    def kept(self) -> list[GeneratedTest]:
        return [item for item in self.tests if item.status == "kept"]


def parse_generated(text: str) -> list[GeneratedTest]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start, end = stripped.find("["), stripped.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        payload: Any = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return []
    tests: list[GeneratedTest] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("path") and item.get("code"):
                tests.append(
                    GeneratedTest(
                        path=str(item["path"]),
                        target=str(item.get("target", "")),
                        code=str(item["code"]),
                    )
                )
    return tests


class TestGenerator:
    __test__ = False  # not a pytest test class

    def __init__(
        self, llm: LLMClient, model: str, sandbox: Sandbox, python: str | None = None
    ) -> None:
        self.llm = llm
        self.model = model
        self.sandbox = sandbox
        self.python = python or sys.executable

    def generate(
        self, report: CoverageReport, targets: list[str], hints: str = ""
    ) -> GenerationResult:
        root = self.sandbox.root
        uncovered = describe_uncovered(report, root)
        sources = "\n\n".join(
            f"### {path}\n" + (root / path).read_text(encoding="utf-8", errors="replace")[:6000]
            for path in targets
            if (root / path).is_file()
        )
        request = (
            f"# Uncovered code\n{uncovered or '(everything is covered; strengthen assertions)'}\n\n"
            f"# Target modules\n{sources}\n\n# Hints\n{hints or '-'}"
        )
        completion = self.llm.complete(
            model=self.model,
            system=GENERATOR_SYSTEM,
            messages=[Message.user(request)],
            max_tokens=4096,
        )
        result = GenerationResult(
            tests=parse_generated(completion.text),
            prompt_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
        )
        return self.validate(result, targets)

    def validate(self, result: GenerationResult, targets: list[str]) -> GenerationResult:
        root = self.sandbox.root
        runner = PytestRunner(self.sandbox, self.python)
        mutation = MutationRunner(self.sandbox, self.python)
        for test in result.tests:
            relative = test.path.strip().lstrip("/")
            if not looks_like_test(relative) or not relative.endswith(".py"):
                test.status, test.reason = "rejected", "path is not a test file"
                continue
            if python_syntax_error(test.code):
                test.status, test.reason = "rejected", "does not parse"
                continue
            target = root / relative
            if target.exists():
                test.status, test.reason = "rejected", "file already exists"
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(test.code, encoding="utf-8")
            _, statuses = runner.run([relative])
            if not statuses or any(status != "passed" for status in statuses.values()):
                target.unlink()
                test.status, test.reason = "failed", "does not pass on the current code"
                continue
            paths = [test.target] if test.target in targets else targets
            outcome = mutation.run(
                paths, test_ids=[relative], budget_s=120, max_mutants_per_file=12
            )
            test.killed = outcome.killed
            if outcome.killed == 0:
                target.unlink()
                test.status, test.reason = "tautology", "passes but kills no mutant"
                continue
            test.status, test.reason = (
                "kept",
                f"kills {outcome.killed} of {len(outcome.mutants)} mutants",
            )
        return result


def generated_paths(result: GenerationResult) -> list[Path]:
    return [Path(item.path) for item in result.kept]
