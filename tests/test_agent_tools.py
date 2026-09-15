from pathlib import Path

from ase.agent.tools import COMMAND_ONLY_TOOLS, STRUCTURED_TOOLS, AgentTools, clip_lines
from ase.policy import PolicyEngine
from ase.repo_intelligence import KnowledgeIndex
from ase.sandbox import LocalSandbox


def _tools(root: Path, index: KnowledgeIndex | None = None, tests_ok: bool = False) -> AgentTools:
    return AgentTools(root, LocalSandbox(root), PolicyEngine(), index=index, task_is_tests=tests_ok)


def test_tool_specs_cover_both_modes() -> None:
    assert {tool.name for tool in STRUCTURED_TOOLS} == {
        "read_file",
        "list_files",
        "search_code",
        "edit_file",
        "run_command",
    }
    assert [tool.name for tool in COMMAND_ONLY_TOOLS] == ["run_command"]


def test_read_and_list_files(fixture_repo: Path) -> None:
    tools = _tools(fixture_repo)
    call = tools.dispatch("read_file", {"path": "pricing/calc.py", "start_line": 4, "end_line": 5})
    assert call.exit_code == 0 and call.output.startswith("4 | def apply_discount")
    assert "no such file" in tools.dispatch("read_file", {"path": "missing.py"}).output
    assert "escapes" in tools.dispatch("read_file", {"path": "../etc/passwd"}).output
    assert "relative" in tools.dispatch("read_file", {"path": "/etc/passwd"}).output
    assert (
        "after"
        in tools.dispatch(
            "read_file", {"path": "pricing/calc.py", "start_line": 9, "end_line": 2}
        ).output
    )
    listing = tools.dispatch("list_files", {"pattern": "pricing/*.py"}).output
    assert "pricing/calc.py" in listing and "tests/test_calc.py" not in listing
    assert tools.dispatch("list_files", {"pattern": "*.zzz"}).output == "(no matches)"
    assert "unknown tool" in tools.dispatch("teleport", {}).output


def test_search_code_with_and_without_index(fixture_repo: Path) -> None:
    grep = _tools(fixture_repo).dispatch("search_code", {"query": "adjustment"}).output
    assert "pricing/calc.py" in grep
    assert "required" in _tools(fixture_repo).dispatch("search_code", {"query": " "}).output
    index = KnowledgeIndex.build(fixture_repo, "sha")
    indexed = _tools(fixture_repo, index).dispatch(
        "search_code", {"query": "refund adjustment", "k": 2}
    )
    assert "### pricing/calc.py" in indexed.output and "[refund_amount]" in indexed.output
    empty = KnowledgeIndex.build(fixture_repo / "nothing", "sha")
    assert (
        _tools(fixture_repo, empty).dispatch("search_code", {"query": "x"}).output == "(no results)"
    )


def test_edit_file_rules(fixture_repo: Path) -> None:
    tools = _tools(fixture_repo)
    ok = tools.dispatch(
        "edit_file",
        {"path": "pricing/calc.py", "search": "adjustment <= 0", "replace": "adjustment < 0"},
    )
    assert ok.exit_code == 0 and "adjustment < 0" in (fixture_repo / "pricing/calc.py").read_text()
    missing = tools.dispatch(
        "edit_file", {"path": "pricing/calc.py", "search": "nope", "replace": "x"}
    )
    assert "not found" in missing.output
    ambiguous = tools.dispatch(
        "edit_file", {"path": "pricing/calc.py", "search": "return", "replace": "x"}
    )
    assert "matches" in ambiguous.output
    broken = tools.dispatch(
        "edit_file", {"path": "pricing/calc.py", "search": "def refund_amount", "replace": "def ("}
    )
    assert (
        "would not parse" in broken.output
        and "def refund_amount" in (fixture_repo / "pricing/calc.py").read_text()
    )
    denied = tools.dispatch(
        "edit_file", {"path": "tests/test_calc.py", "search": "import pytest", "replace": ""}
    )
    assert "test files are denied" in denied.output
    allowed = _tools(fixture_repo, tests_ok=True).dispatch(
        "edit_file",
        {"path": "tests/test_new.py", "search": "", "replace": "def test_x():\n    assert True\n"},
    )
    assert allowed.exit_code == 0 and (fixture_repo / "tests/test_new.py").exists()
    exists = tools.dispatch("edit_file", {"path": "pricing/calc.py", "search": "", "replace": "x"})
    assert "file exists" in exists.output
    assert (
        "no such file"
        in tools.dispatch("edit_file", {"path": "ghost.py", "search": "a", "replace": "b"}).output
    )


def test_run_command_is_policy_gated_and_clipped(fixture_repo: Path) -> None:
    tools = _tools(fixture_repo)
    denied = tools.dispatch("run_command", {"argv": ["bash", "-c", "ls"]})
    assert denied.exit_code == 1 and "shell" in denied.output
    ok = tools.dispatch("run_command", {"argv": ["python", "-c", "print('hi')"]})
    assert ok.exit_code == 0 and ok.output == "hi"
    as_string = tools.dispatch("run_command", {"argv": '["python", "-c", "print(1)"]'})
    assert as_string.output == "1"
    plain = tools.dispatch("run_command", {"argv": "python -c print(2)"})
    assert plain.output == "2"
    bad = tools.dispatch("run_command", {"argv": [1, 2]})
    assert "list of strings" in bad.output
    err = tools.dispatch(
        "run_command",
        {"argv": ["python", "-c", "import sys; sys.stderr.write('oops'); sys.exit(3)"]},
    )
    assert err.exit_code == 3 and "[stderr]" in err.output
    assert len(tools.calls) == 6
    assert clip_lines("\n".join(str(i) for i in range(150)), limit=10).startswith(
        "... [140 earlier lines omitted"
    )
