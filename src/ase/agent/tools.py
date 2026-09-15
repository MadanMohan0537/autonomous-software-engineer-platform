"""Constrained tools the coding model can call.

Every tool is a pure function of the workspace plus the sandbox: no shell, no network,
no credentials. `edit_file` uses search/replace blocks (an edit that fails to match fails
loudly, unlike a malformed diff), re-parses Python files before writing so a syntax error
never reaches the test step, and refuses test files unless the task is about tests.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ase.contracts import ToolCall
from ase.llm.client import ToolSpec
from ase.policy import PolicyEngine
from ase.repo_intelligence.index import KnowledgeIndex
from ase.sandbox import CommandDenied, Sandbox

MAX_OUTPUT_LINES = 100
MAX_FILE_BYTES = 200_000
IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ase-run", ".mypy_cache"}


class ToolError(RuntimeError):
    pass


def python_syntax_error(text: str) -> str | None:
    """Return a message when the text is not valid Python, else None."""
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return f"syntax error at line {exc.lineno}: {exc.msg}"
    return None


def clip_lines(text: str, limit: int = MAX_OUTPUT_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    hidden = len(lines) - limit
    return "\n".join(
        [f"... [{hidden} earlier lines omitted; full output is in the trace]", *lines[-limit:]]
    )


STRUCTURED_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="read_file",
        description=(
            "Read a file from the repository with line numbers. Prefer a line range for big files."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repository root"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="list_files",
        description="List repository files matching a glob such as 'src/**/*.py'.",
        input_schema={
            "type": "object",
            "properties": {"pattern": {"type": "string", "default": "**/*.py"}},
        },
    ),
    ToolSpec(
        name="search_code",
        description=(
            "Search the code index (lexical + semantic) for definitions relevant to a query."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "k": {"type": "integer", "default": 6}},
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="edit_file",
        description=(
            "Replace exactly one occurrence of `search` with `replace` in a file. `search` must "
            "match the file verbatim, including indentation. To create a new file pass an empty "
            "`search`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "search": {"type": "string"},
                "replace": {"type": "string"},
            },
            "required": ["path", "search", "replace"],
        },
    ),
    ToolSpec(
        name="run_command",
        description=(
            "Run an allowlisted program (python, pytest, ruff, mypy, git read-only) as an argument "
            "array inside the sandbox. No shell, no network."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
            },
            "required": ["argv"],
        },
    ),
]

COMMAND_ONLY_TOOLS: list[ToolSpec] = [
    tool for tool in STRUCTURED_TOOLS if tool.name == "run_command"
]


class AgentTools:
    def __init__(
        self,
        root: Path,
        sandbox: Sandbox,
        policy: PolicyEngine,
        index: KnowledgeIndex | None = None,
        task_is_tests: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = root.resolve()
        self.sandbox = sandbox
        self.policy = policy
        self.index = index
        self.task_is_tests = task_is_tests
        self._clock = clock
        self.calls: list[ToolCall] = []

    # -- dispatch ------------------------------------------------------------------------
    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        started = self._clock()
        handlers: dict[str, Callable[[dict[str, Any]], tuple[str, int | None]]] = {
            "read_file": self._read_file,
            "list_files": self._list_files,
            "search_code": self._search_code,
            "edit_file": self._edit_file,
            "run_command": self._run_command,
        }
        try:
            handler = handlers.get(name)
            if handler is None:
                raise ToolError(f"unknown tool: {name}")
            output, exit_code = handler(arguments)
        except (ToolError, CommandDenied, ValueError, OSError) as exc:
            output, exit_code = f"error: {exc}", 1
        call = ToolCall(
            name=name,
            arguments=arguments,
            output=output,
            exit_code=exit_code,
            duration_ms=int((self._clock() - started) * 1000),
        )
        self.calls.append(call)
        return call

    # -- helpers -------------------------------------------------------------------------
    def _resolve(self, raw: str) -> Path:
        if not raw or raw.startswith("/") or raw.startswith("~"):
            raise ToolError("paths must be relative to the repository root")
        candidate = (self.root / raw).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError(f"path escapes the repository: {raw}")
        return candidate

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # -- tools ---------------------------------------------------------------------------
    def _read_file(self, arguments: dict[str, Any]) -> tuple[str, int | None]:
        path = self._resolve(str(arguments.get("path", "")))
        if not path.is_file():
            raise ToolError(f"no such file: {self._relative(path)}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ToolError("file is too large to read whole; pass a line range")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = int(arguments.get("start_line") or 1)
        end = int(arguments.get("end_line") or len(lines))
        start, end = max(1, start), min(len(lines), end)
        if start > end:
            raise ToolError("start_line is after end_line")
        width = len(str(end))
        rendered = "\n".join(f"{n:>{width}} | {lines[n - 1]}" for n in range(start, end + 1))
        return rendered or "(empty file)", 0

    def _list_files(self, arguments: dict[str, Any]) -> tuple[str, int | None]:
        pattern = str(arguments.get("pattern") or "**/*.py")
        matches: list[str] = []
        for path in sorted(self.root.rglob("*")):
            relative = self._relative(path)
            if not path.is_file() or any(part in IGNORED_DIRS for part in path.parts):
                continue
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
                matches.append(relative)
            if len(matches) >= 200:
                matches.append("... (truncated at 200 files)")
                break
        return "\n".join(matches) or "(no matches)", 0

    def _search_code(self, arguments: dict[str, Any]) -> tuple[str, int | None]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ToolError("query is required")
        k = int(arguments.get("k") or 6)
        if self.index is None:
            return self._grep(query, k), 0
        hits = self.index.search(query, k=k)
        if not hits:
            return "(no results)", 0
        parts = []
        for hit in hits:
            chunk = hit.chunk
            heading = f"{chunk.path}:{chunk.start_line}-{chunk.end_line}"
            if chunk.symbol:
                heading += f"  [{chunk.symbol}]"
            snippet = "\n".join(chunk.text.splitlines()[:25])
            parts.append(f"### {heading}  ({', '.join(hit.reasons)})\n{snippet}")
        return "\n\n".join(parts), 0

    def _grep(self, query: str, k: int) -> str:
        needle = query.lower()
        found: list[str] = []
        for path in sorted(self.root.rglob("*.py")):
            if any(part in IGNORED_DIRS for part in path.parts):
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                if needle in line.lower():
                    found.append(f"{self._relative(path)}:{number}: {line.strip()}")
                    if len(found) >= k * 4:
                        return "\n".join(found)
        return "\n".join(found) or "(no results)"

    def _edit_file(self, arguments: dict[str, Any]) -> tuple[str, int | None]:
        path = self._resolve(str(arguments.get("path", "")))
        relative = self._relative(path)
        decision = self.policy.write_path(relative, task_is_tests=self.task_is_tests)
        if not decision.allowed:
            raise ToolError("; ".join(decision.reasons))
        search = str(arguments.get("search", ""))
        replace = str(arguments.get("replace", ""))
        if search == "":
            if path.exists():
                raise ToolError("file exists; provide a non-empty `search` to edit it")
            updated = replace
        else:
            if not path.is_file():
                raise ToolError(f"no such file: {relative}")
            original = path.read_text(encoding="utf-8")
            count = original.count(search)
            if count == 0:
                raise ToolError(
                    "search text not found; re-read the file and copy the exact lines, "
                    "including indentation"
                )
            if count > 1:
                raise ToolError(f"search text matches {count} times; include more context")
            updated = original.replace(search, replace, 1)
        if path.suffix == ".py":
            problem = python_syntax_error(updated)
            if problem:
                raise ToolError(f"edit rejected, file would not parse: {problem}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
        return f"edited {relative}", 0

    def _run_command(self, arguments: dict[str, Any]) -> tuple[str, int | None]:
        argv = arguments.get("argv")
        if isinstance(argv, str):
            try:
                argv = json.loads(argv)
            except json.JSONDecodeError:
                argv = argv.split()
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ToolError("argv must be a list of strings")
        timeout = arguments.get("timeout")
        result = self.sandbox.run(list(argv), timeout=int(timeout) if timeout else None)
        output = result.stdout
        if result.stderr:
            output = (
                f"{output}\n[stderr]\n{result.stderr}" if output else f"[stderr]\n{result.stderr}"
            )
        if result.timed_out:
            output += "\n[timed out]"
        return clip_lines(output.strip() or "(no output)"), result.exit_code
