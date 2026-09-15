"""Source parsers that turn one file into symbols, relationships and definitions.

Two Python backends share one output shape:

* `TreeSitterPythonParser` uses tree-sitter. It is error-tolerant (a syntax error in one
  function still yields every other definition) and the same code path extends to other
  grammars later.
* `AstPythonParser` uses the standard library. It is Python-only and stops at the first
  syntax error, but it needs no compiled dependency.

`select_parser()` prefers tree-sitter when it is installed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ase.contracts import Relationship, Symbol

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass(frozen=True)
class Definition:
    """A top-level or nested definition with its exact source span."""

    path: str
    name: str  # qualified, e.g. "Class.method"
    kind: str  # "class" | "function"
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    docstring: str = ""
    parent: str | None = None

    @property
    def symbol_id(self) -> str:
        return f"{self.path}:{self.name}"


@dataclass
class ParsedFile:
    path: str
    language: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    parse_errors: int = 0


class SourceParser(Protocol):
    name: str

    def parse(self, path: str, text: str) -> ParsedFile: ...


def _register(parsed: ParsedFile, definition: Definition) -> None:
    parsed.definitions.append(definition)
    parsed.symbols[definition.symbol_id] = Symbol(
        id=definition.symbol_id,
        path=definition.path,
        name=definition.name,
        kind=definition.kind,
        start_line=definition.start_line,
        end_line=definition.end_line,
        language="python",
    )


class AstPythonParser:
    """Standard-library parser. Kept as the zero-dependency fallback."""

    name = "ast"

    def parse(self, path: str, text: str) -> ParsedFile:
        parsed = ParsedFile(path=path, language="python")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            parsed.parse_errors = 1
            return parsed
        self._walk(tree, path, [], parsed)
        return parsed

    def _walk(self, node: ast.AST, path: str, stack: list[str], parsed: ParsedFile) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                qualified = ".".join([*stack, child.name])
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                _register(
                    parsed,
                    Definition(
                        path=path,
                        name=qualified,
                        kind=kind,
                        start_line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        docstring=ast.get_docstring(child) or "",
                        parent=".".join(stack) or None,
                    ),
                )
                self._walk(child, path, [*stack, child.name], parsed)
            elif isinstance(child, ast.Import):
                source = ".".join(stack) if stack else path
                for alias in child.names:
                    parsed.relationships.append(
                        Relationship(source=f"{path}:{source}", target=alias.name, kind="imports")
                    )
            elif isinstance(child, ast.ImportFrom):
                source = ".".join(stack) if stack else path
                parsed.relationships.append(
                    Relationship(
                        source=f"{path}:{source}", target=child.module or "", kind="imports"
                    )
                )
            elif isinstance(child, ast.Call):
                target = self._call_name(child.func)
                if stack and target:
                    parsed.relationships.append(
                        Relationship(
                            source=f"{path}:{'.'.join(stack)}", target=target, kind="calls"
                        )
                    )
                self._walk(child, path, stack, parsed)
            else:
                self._walk(child, path, stack, parsed)

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = AstPythonParser._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return None


class TreeSitterPythonParser:
    """tree-sitter backed parser. Error-tolerant, multi-language ready."""

    name = "tree-sitter"

    def __init__(self) -> None:
        import tree_sitter
        import tree_sitter_python

        self._language = tree_sitter.Language(tree_sitter_python.language())
        self._parser = tree_sitter.Parser(self._language)

    def parse(self, path: str, text: str) -> ParsedFile:
        parsed = ParsedFile(path=path, language="python")
        tree = self._parser.parse(text.encode("utf-8"))
        parsed.parse_errors = self._count_errors(tree.root_node)
        self._walk(tree.root_node, path, [], parsed)
        return parsed

    @staticmethod
    def _count_errors(node: Node) -> int:
        count = 0
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type == "ERROR" or current.is_missing:
                count += 1
            stack.extend(current.children)
        return count

    def _walk(self, node: Node, path: str, stack: list[str], parsed: ParsedFile) -> None:
        for child in node.children:
            kind = child.type
            if kind in {"function_definition", "class_definition"}:
                name = self._text(child.child_by_field_name("name"))
                if not name:
                    continue
                qualified = ".".join([*stack, name])
                _register(
                    parsed,
                    Definition(
                        path=path,
                        name=qualified,
                        kind="class" if kind == "class_definition" else "function",
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        docstring=self._docstring(child),
                        parent=".".join(stack) or None,
                    ),
                )
                body = child.child_by_field_name("body")
                if body is not None:
                    self._walk(body, path, [*stack, name], parsed)
            elif kind == "import_statement":
                source = ".".join(stack) if stack else path
                for name_node in child.children:
                    if name_node.type in {"dotted_name", "aliased_import"}:
                        target_node = name_node.child_by_field_name("name") or name_node
                        parsed.relationships.append(
                            Relationship(
                                source=f"{path}:{source}",
                                target=self._text(target_node),
                                kind="imports",
                            )
                        )
            elif kind == "import_from_statement":
                source = ".".join(stack) if stack else path
                module = child.child_by_field_name("module_name")
                parsed.relationships.append(
                    Relationship(
                        source=f"{path}:{source}", target=self._text(module), kind="imports"
                    )
                )
            elif kind == "call":
                function = child.child_by_field_name("function")
                target = self._text(function)
                if stack and function is not None and function.type in {"identifier", "attribute"}:
                    parsed.relationships.append(
                        Relationship(
                            source=f"{path}:{'.'.join(stack)}", target=target, kind="calls"
                        )
                    )
                self._walk(child, path, stack, parsed)
            else:
                self._walk(child, path, stack, parsed)

    def _docstring(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        if body is None:
            return ""
        for statement in body.children:
            if statement.type == "comment":
                continue
            candidate = statement
            if statement.type == "expression_statement" and statement.children:
                candidate = statement.children[0]
            if candidate.type == "string":
                return self._text(candidate).strip("\"' \n").strip()
            break
        return ""

    @staticmethod
    def _text(node: Node | None) -> str:
        if node is None or node.text is None:
            return ""
        return node.text.decode("utf-8", errors="replace")


def tree_sitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except ImportError:
        return False
    return True


def select_parser(prefer: str | None = None) -> SourceParser:
    if prefer == "ast":
        return AstPythonParser()
    if prefer == "tree-sitter" or (prefer is None and tree_sitter_available()):
        return TreeSitterPythonParser()
    return AstPythonParser()
