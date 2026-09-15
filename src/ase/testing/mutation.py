"""Mutation testing with a built-in AST mutator.

A mutant is the program with one small, plausible mistake inserted: a `<` that became
`<=`, a `+` that became `-`, a `True` that became `False`. A test suite that still passes
on a mutant would not have caught that mistake. The mutation score is the fraction of
mutants the suite kills, and the survivors are the concrete weak spots.

The runner is scoped and capped on purpose: mutation testing is quadratic in the wrong
places, so it runs on the files a patch touched, with a time budget.
"""

from __future__ import annotations

import ast
import copy
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from ase.contracts import Issue, Task, TaskSource
from ase.sandbox import Sandbox
from ase.testrun import pytest_argv

COMPARE_FLIPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}
ARITH_FLIPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}
BOOL_FLIPS: dict[type[ast.boolop], type[ast.boolop]] = {ast.And: ast.Or, ast.Or: ast.And}


class Mutant(BaseModel):
    id: str
    path: str
    line: int
    operator: str
    description: str
    status: str = "pending"  # pending | killed | survived | timeout | error


class MutationResult(BaseModel):
    paths: list[str]
    mutants: list[Mutant] = Field(default_factory=list)
    elapsed_s: float = 0.0
    budget_exhausted: bool = False

    @property
    def killed(self) -> int:
        return sum(1 for item in self.mutants if item.status == "killed")

    @property
    def survived(self) -> int:
        return sum(1 for item in self.mutants if item.status == "survived")

    @property
    def timeouts(self) -> int:
        return sum(1 for item in self.mutants if item.status == "timeout")

    @property
    def score(self) -> float | None:
        judged = self.killed + self.survived
        return round(self.killed / judged, 4) if judged else None

    @property
    def survivors(self) -> list[Mutant]:
        return [item for item in self.mutants if item.status == "survived"]


@dataclass
class _Candidate:
    node: ast.AST
    apply: Callable[[ast.AST], None]
    operator: str
    description: str


def _make_compare_flip(position: int, chosen: type[ast.cmpop]) -> Callable[[ast.AST], None]:
    def apply(target: ast.AST) -> None:
        assert isinstance(target, ast.Compare)
        target.ops[position] = chosen()

    return apply


def _make_arith_flip(chosen: type[ast.operator]) -> Callable[[ast.AST], None]:
    def apply(target: ast.AST) -> None:
        assert isinstance(target, ast.BinOp)
        target.op = chosen()

    return apply


def _make_bool_flip(chosen: type[ast.boolop]) -> Callable[[ast.AST], None]:
    def apply(target: ast.AST) -> None:
        assert isinstance(target, ast.BoolOp)
        target.op = chosen()

    return apply


def _flip_constant(target: ast.AST) -> None:
    assert isinstance(target, ast.Constant)
    target.value = not target.value


def _negate_condition(target: ast.AST) -> None:
    assert isinstance(target, ast.If | ast.While)
    target.test = ast.UnaryOp(op=ast.Not(), operand=target.test)


def _candidates(tree: ast.AST) -> list[_Candidate]:
    found: list[_Candidate] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for position, op in enumerate(node.ops):
                replacement = COMPARE_FLIPS.get(type(op))
                if replacement is None:
                    continue
                found.append(
                    _Candidate(
                        node,
                        _make_compare_flip(position, replacement),
                        "compare",
                        f"{type(op).__name__} -> {replacement.__name__}",
                    )
                )
        elif isinstance(node, ast.BinOp):
            replacement_op = ARITH_FLIPS.get(type(node.op))
            if replacement_op is not None:
                found.append(
                    _Candidate(
                        node,
                        _make_arith_flip(replacement_op),
                        "arith",
                        f"{type(node.op).__name__} -> {replacement_op.__name__}",
                    )
                )
        elif isinstance(node, ast.BoolOp):
            replacement_bool = BOOL_FLIPS[type(node.op)]
            found.append(
                _Candidate(
                    node,
                    _make_bool_flip(replacement_bool),
                    "boolop",
                    f"{type(node.op).__name__} -> {replacement_bool.__name__}",
                )
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            found.append(
                _Candidate(node, _flip_constant, "bool", f"{node.value} -> {not node.value}")
            )
        elif isinstance(node, ast.If | ast.While):
            found.append(_Candidate(node, _negate_condition, "negate", "condition negated"))
    return found


def generate_mutants(source: str, path: str, limit: int | None = None) -> list[tuple[Mutant, str]]:
    """Return (mutant, mutated source) pairs, one single-point change each."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    candidates = _candidates(tree)
    if limit is not None:
        candidates = candidates[:limit]
    results: list[tuple[Mutant, str]] = []
    for index, candidate in enumerate(candidates):
        mutated_tree = copy.deepcopy(tree)
        # Locate the same node in the copy by walking both trees in lockstep.
        original_nodes = list(ast.walk(tree))
        copied_nodes = list(ast.walk(mutated_tree))
        target = copied_nodes[original_nodes.index(candidate.node)]
        candidate.apply(target)
        ast.fix_missing_locations(mutated_tree)
        line = int(getattr(candidate.node, "lineno", 0))
        mutant = Mutant(
            id=f"{path}:{line}:{index}",
            path=path,
            line=line,
            operator=candidate.operator,
            description=candidate.description,
        )
        results.append((mutant, ast.unparse(mutated_tree) + "\n"))
    return results


class MutationRunner:
    """Run the test suite against each mutant of the given files inside a sandbox."""

    def __init__(
        self,
        sandbox: Sandbox,
        python: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sandbox = sandbox
        self.python = python or sys.executable
        self._clock = clock

    def run(
        self,
        paths: Iterable[str],
        test_ids: Iterable[str] = (),
        budget_s: float = 600.0,
        max_mutants_per_file: int | None = 40,
        timeout_s: int = 60,
    ) -> MutationResult:
        started = self._clock()
        tests = list(test_ids)
        result = MutationResult(paths=list(paths))
        for relative in result.paths:
            target = self.sandbox.root / relative
            if not target.is_file():
                continue
            original = target.read_text(encoding="utf-8")
            try:
                for mutant, mutated in generate_mutants(original, relative, max_mutants_per_file):
                    if self._clock() - started > budget_s:
                        result.budget_exhausted = True
                        break
                    target.write_text(mutated, encoding="utf-8")
                    outcome = self.sandbox.run(
                        pytest_argv(self.python, tests, ["-x", "--no-header"]), timeout=timeout_s
                    )
                    if outcome.timed_out:
                        mutant.status = "timeout"
                    elif outcome.exit_code == 0:
                        mutant.status = "survived"
                    elif outcome.exit_code in (1, 2):
                        # 1: tests failed; 2: pytest interrupted (e.g. the mutant broke an import)
                        mutant.status = "killed"
                    else:
                        mutant.status = "error"
                    result.mutants.append(mutant)
            finally:
                target.write_text(original, encoding="utf-8")
            if result.budget_exhausted:
                break
        result.elapsed_s = round(self._clock() - started, 3)
        return result


def survivors_to_tasks(
    result: MutationResult, repository: str, base_sha: str | None = None
) -> list[Task]:
    """Every surviving mutant becomes a test-writing task with provenance `mutant`."""
    tasks: list[Task] = []
    for number, mutant in enumerate(result.survivors, start=1):
        issue = Issue(
            repository=repository,
            number=number,
            title=f"Surviving mutant in {mutant.path}:{mutant.line} ({mutant.operator})",
            body=(
                f"Mutation `{mutant.description}` at `{mutant.path}` line {mutant.line} is not "
                "detected by the test suite. Add a test that fails on the mutant and passes on "
                "the current code. Do not change production code."
            ),
            labels=["mutant", "tests"],
        )
        tasks.append(
            Task(
                source=TaskSource.MUTANT,
                issue=issue,
                base_sha=base_sha,
                metadata={"task_is_tests": True, "mutant": mutant.model_dump()},
            )
        )
    return tasks


def mutation_paths_for(changed_files: Iterable[str]) -> list[str]:
    """Only production Python files are mutated; tests and non-Python files are skipped."""
    from ase.contracts import looks_like_test

    return [
        path
        for path in changed_files
        if path.endswith(".py") and not looks_like_test(path) and Path(path).name != "__init__.py"
    ]
