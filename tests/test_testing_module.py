import json
import sys
from pathlib import Path

from ase.contracts import TaskSource
from ase.llm import ScriptedClient, text_completion
from ase.sandbox import LocalSandbox
from ase.testing import (
    CoverageRunner,
    MutationRunner,
    TestGenerator,
    generate_mutants,
    mutation_paths_for,
    parse_coverage_json,
    parse_generated,
    survivors_to_tasks,
)
from ase.testing.coverage_report import coverage_argv, describe_uncovered

COVERAGE_JSON = json.dumps(
    {
        "files": {
            "pricing/calc.py": {
                "summary": {"percent_covered": 60.0},
                "executed_lines": [1, 2, 3],
                "missing_lines": [5, 6, 7, 10],
            },
            "pricing/__init__.py": {"summary": {"percent_covered": 100.0}, "executed_lines": [1]},
        },
        "totals": {"percent_covered": 75.5},
    }
)


def test_parse_coverage_json_and_ranges(fixture_repo: Path) -> None:
    report = parse_coverage_json(COVERAGE_JSON)
    assert report.percent == 75.5
    calc = report.files["pricing/calc.py"]
    assert calc.missing_ranges == [(5, 7), (10, 10)]
    assert report.weakest(1)[0].path == "pricing/calc.py"
    rendered = describe_uncovered(report, fixture_repo)
    assert "uncovered lines 5-7" in rendered and "*" in rendered
    argv = coverage_argv("python", ["pricing"], ["tests/test_calc.py"])
    assert argv[0][:4] == ["python", "-m", "coverage", "run"] and argv[1][3] == "json"


def test_coverage_runner_live_or_absent(fixture_repo: Path) -> None:
    report = CoverageRunner(LocalSandbox(fixture_repo), sys.executable).run(["pricing"])
    if report is None:  # coverage.py is not installed in this interpreter
        return
    assert "pricing/calc.py" in report.files and report.percent > 0


def test_generate_mutants_single_point_changes() -> None:
    source = "def f(a, b):\n    if a < b and b > 0:\n        return a + b\n    return True\n"
    mutants = generate_mutants(source, "m.py")
    operators = [mutant.operator for mutant, _ in mutants]
    assert operators.count("compare") == 2 and "arith" in operators
    assert "boolop" in operators and "bool" in operators and "negate" in operators
    for mutant, mutated in mutants:
        assert mutated != source and mutant.path == "m.py" and mutant.line >= 1
    assert generate_mutants("def broken(:", "b.py") == []
    assert len(generate_mutants(source, "m.py", limit=2)) == 2


def test_mutation_runner_scores_the_fixture_suite(fixture_repo: Path) -> None:
    runner = MutationRunner(LocalSandbox(fixture_repo), sys.executable)
    original = (fixture_repo / "pricing" / "calc.py").read_text(encoding="utf-8")
    result = runner.run(
        ["pricing/calc.py", "missing.py"], test_ids=["tests/test_calc.py"], budget_s=120
    )
    assert (fixture_repo / "pricing" / "calc.py").read_text(encoding="utf-8") == original
    assert result.mutants and result.killed >= 1
    assert result.score is not None and 0 < result.score <= 1
    assert result.elapsed_s >= 0 and not result.budget_exhausted
    tasks = survivors_to_tasks(result, "owner/pricing", base_sha="abc")
    assert all(task.source == TaskSource.MUTANT for task in tasks)
    if tasks:
        assert tasks[0].metadata["task_is_tests"] is True and "mutant" in tasks[0].issue.labels

    clock = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])
    capped = MutationRunner(LocalSandbox(fixture_repo), sys.executable, clock=lambda: next(clock))
    limited = capped.run(["pricing/calc.py"], budget_s=1)
    assert limited.budget_exhausted and len(limited.mutants) <= 1
    assert mutation_paths_for(
        ["pricing/calc.py", "tests/test_calc.py", "pricing/__init__.py", "README.md"]
    ) == ["pricing/calc.py"]


def test_parse_generated_tolerates_fences_and_junk() -> None:
    text = (
        '```json\n[{"path": "tests/test_generated_calc.py", '
        '"target": "pricing/calc.py", "code": "x"}]\n```'
    )
    assert parse_generated(text)[0].path == "tests/test_generated_calc.py"
    assert parse_generated("no json here") == []
    assert parse_generated('[{"bad": 1}]') == []
    assert parse_generated("[1, 2") == []


def test_generator_keeps_only_tests_that_kill_mutants(fixture_repo: Path) -> None:
    good = (
        "from pricing.calc import apply_discount\nimport pytest\n\n\n"
        "def test_rejects_out_of_range():\n    with pytest.raises(ValueError):\n"
        "        apply_discount(10, 150)\n"
        "    assert apply_discount(200, 50) == 100\n"
    )
    tautology = "def test_nothing():\n    assert 1 == 1\n"
    failing = (
        "from pricing.calc import apply_discount\n\n\n"
        "def test_wrong():\n    assert apply_discount(10, 10) == 1\n"
    )
    script = [
        text_completion(
            json.dumps(
                [
                    {
                        "path": "tests/test_generated_good.py",
                        "target": "pricing/calc.py",
                        "code": good,
                    },
                    {
                        "path": "tests/test_generated_taut.py",
                        "target": "pricing/calc.py",
                        "code": tautology,
                    },
                    {
                        "path": "tests/test_generated_bad.py",
                        "target": "pricing/calc.py",
                        "code": failing,
                    },
                    {
                        "path": "pricing/helper.py",
                        "target": "pricing/calc.py",
                        "code": "x = 1\n",
                    },
                    {
                        "path": "tests/test_generated_broken.py",
                        "target": "pricing/calc.py",
                        "code": "def (:\n",
                    },
                    {
                        "path": "tests/test_calc.py",
                        "target": "pricing/calc.py",
                        "code": "def test_dup(): pass\n",
                    },
                ]
            )
        )
    ]
    generator = TestGenerator(
        ScriptedClient(script), "m", LocalSandbox(fixture_repo), sys.executable
    )
    report = parse_coverage_json(COVERAGE_JSON)
    result = generator.generate(report, ["pricing/calc.py"], hints="focus on discounts")
    statuses = {test.path: test.status for test in result.tests}
    assert statuses["tests/test_generated_good.py"] == "kept"
    assert statuses["tests/test_generated_taut.py"] == "tautology"
    assert statuses["tests/test_generated_bad.py"] == "failed"
    assert statuses["pricing/helper.py"] == "rejected"
    assert statuses["tests/test_generated_broken.py"] == "rejected"
    assert statuses["tests/test_calc.py"] == "rejected"
    assert [test.path for test in result.kept] == ["tests/test_generated_good.py"]
    assert (fixture_repo / "tests/test_generated_good.py").exists()
    assert not (fixture_repo / "tests/test_generated_taut.py").exists()
    assert result.prompt_tokens > 0
