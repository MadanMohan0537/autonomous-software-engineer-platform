"""Deterministic repository indexer with Python AST support and text fallbacks."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ase.contracts import Relationship, Symbol
from ase.repo_intelligence.language_parser import parse_symbols

SUPPORTED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}
IGNORED_PARTS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}


@dataclass
class FileRecord:
    path: str
    language: str
    digest: str
    text: str
    tokens: set[str] = field(default_factory=set)


@dataclass
class RepositoryIndex:
    root: Path
    files: dict[str, FileRecord] = field(default_factory=dict)
    symbols: dict[str, Symbol] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "files": {
                key: {"language": value.language, "digest": value.digest}
                for key, value in self.files.items()
            },
            "symbols": {key: value.model_dump() for key, value in self.symbols.items()},
            "relationships": [item.model_dump() for item in self.relationships],
        }

    def write_manifest(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


class RepositoryIndexer:
    def index(self, root: Path) -> RepositoryIndex:
        resolved = root.resolve()
        index = RepositoryIndex(root=resolved)
        for path in sorted(resolved.rglob("*")):
            if not path.is_file() or path.suffix not in SUPPORTED_SUFFIXES:
                continue
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            relative = path.relative_to(resolved).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            language = self._language(path.suffix)
            index.files[relative] = FileRecord(
                path=relative,
                language=language,
                digest=hashlib.sha256(text.encode()).hexdigest(),
                text=text,
                tokens=self._tokens(text),
            )
            if path.suffix == ".py":
                self._index_python(relative, text, index)
            else:
                self._index_generic(relative, text, language, index)
        return index

    @staticmethod
    def _language(suffix: str) -> str:
        return {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
        }[suffix]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
        return {token for token in normalized.split() if len(token) > 2}

    def _index_python(self, path: str, text: str, index: RepositoryIndex) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return
        symbol_stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(visitor, node: ast.ClassDef) -> None:
                self._add_symbol(path, node, "class", symbol_stack, index)
                symbol_stack.append(node.name)
                visitor.generic_visit(node)
                symbol_stack.pop()

            def visit_FunctionDef(visitor, node: ast.FunctionDef) -> None:
                self._add_symbol(path, node, "function", symbol_stack, index)
                symbol_stack.append(node.name)
                visitor.generic_visit(node)
                symbol_stack.pop()

            def visit_AsyncFunctionDef(visitor, node: ast.AsyncFunctionDef) -> None:
                self._add_symbol(path, node, "function", symbol_stack, index)
                symbol_stack.append(node.name)
                visitor.generic_visit(node)
                symbol_stack.pop()

            def visit_Import(visitor, node: ast.Import) -> None:
                source = symbol_stack[-1] if symbol_stack else path
                for alias in node.names:
                    index.relationships.append(
                        Relationship(source=f"{path}:{source}", target=alias.name, kind="imports")
                    )

            def visit_ImportFrom(visitor, node: ast.ImportFrom) -> None:
                source = symbol_stack[-1] if symbol_stack else path
                module = node.module or ""
                index.relationships.append(
                    Relationship(source=f"{path}:{source}", target=module, kind="imports")
                )

            def visit_Call(visitor, node: ast.Call) -> None:
                if symbol_stack:
                    target = self._call_name(node.func)
                    if target:
                        index.relationships.append(
                            Relationship(
                                source=f"{path}:{symbol_stack[-1]}", target=target, kind="calls"
                            )
                        )
                visitor.generic_visit(node)

        Visitor().visit(tree)

    @staticmethod
    def _index_generic(path: str, text: str, language: str, index: RepositoryIndex) -> None:
        for parsed in parse_symbols(language, text):
            identifier = f"{path}:{parsed.name}"
            index.symbols[identifier] = Symbol(
                id=identifier,
                path=path,
                name=parsed.name,
                kind=parsed.kind,
                start_line=parsed.line,
                end_line=parsed.line,
                language=language,
            )

    @staticmethod
    def _add_symbol(
        path: str,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
        stack: list[str],
        index: RepositoryIndex,
    ) -> None:
        qualified = ".".join([*stack, node.name])
        identifier = f"{path}:{qualified}"
        index.symbols[identifier] = Symbol(
            id=identifier,
            path=path,
            name=qualified,
            kind=kind,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            language="python",
        )

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = RepositoryIndexer._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return None
