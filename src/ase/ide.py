"""Secure repository browsing and IDE-facing workspace operations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ase.contracts import CommandResult
from ase.sandbox import LocalSandbox

IGNORED_NAMES = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}
MAX_FILE_BYTES = 512_000


class WorkspacePathError(ValueError):
    pass


@dataclass(frozen=True)
class TreeEntry:
    path: str
    name: str
    kind: str
    size: int | None = None


@dataclass(frozen=True)
class SearchHit:
    path: str
    line: int
    preview: str


class IDEWorkspace:
    RECIPES: dict[str, list[str]] = {
        "tests": ["pytest", "-q"],
        "lint": ["ruff", "check", "."],
        "types": ["mypy", "src"],
    }

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise WorkspacePathError("workspace root does not exist")
        self.sandbox = LocalSandbox(self.root)

    def resolve(self, relative: str = "") -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspacePathError("path escapes workspace root")
        if any(part in IGNORED_NAMES for part in candidate.relative_to(self.root).parts):
            raise WorkspacePathError("path is excluded from the workspace")
        return candidate

    def tree(self, relative: str = "") -> list[TreeEntry]:
        directory = self.resolve(relative)
        if not directory.is_dir():
            raise WorkspacePathError("path is not a directory")
        entries: list[TreeEntry] = []
        children = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        for child in children:
            if child.name in IGNORED_NAMES or child.name.startswith(".ase.db"):
                continue
            path = child.relative_to(self.root).as_posix()
            entries.append(
                TreeEntry(
                    path=path,
                    name=child.name,
                    kind="directory" if child.is_dir() else "file",
                    size=child.stat().st_size if child.is_file() else None,
                )
            )
        return entries

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise WorkspacePathError("path is not a file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise WorkspacePathError("file exceeds IDE preview limit")
        content = path.read_bytes()
        if b"\x00" in content:
            raise WorkspacePathError("binary files cannot be previewed")
        return content.decode("utf-8", errors="replace")

    def diff(self, relative: str | None = None) -> str:
        argv = ["git", "diff", "--no-ext-diff", "--"]
        if relative:
            path = self.resolve(relative)
            argv.append(path.relative_to(self.root).as_posix())
        result = subprocess.run(
            argv,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode:
            raise WorkspacePathError(result.stderr.strip() or "git diff failed")
        return result.stdout

    def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        needle = query.strip().lower()
        if len(needle) < 2:
            return []
        hits: list[SearchHit] = []
        for path in sorted(self.root.rglob("*")):
            if len(hits) >= limit:
                break
            if not path.is_file() or any(part in IGNORED_NAMES for part in path.parts):
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(lines, start=1):
                if needle in line.lower():
                    hits.append(
                        SearchHit(
                            path=path.relative_to(self.root).as_posix(),
                            line=line_number,
                            preview=line.strip()[:240],
                        )
                    )
                    if len(hits) >= limit:
                        break
        return hits

    def run_recipe(self, recipe: str) -> CommandResult:
        argv = self.RECIPES.get(recipe)
        if argv is None:
            raise WorkspacePathError("unknown command recipe")
        return self.sandbox.run(argv)
