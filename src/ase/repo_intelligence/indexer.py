"""Deterministic repository indexer: files, symbols, relationships and definitions.

Python files are parsed with tree-sitter when it is installed and with the standard
library otherwise (see `parsers.py`). Other languages are discovered and tokenised for
lexical retrieval; their symbol extraction is a planned extension.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ase.contracts import Relationship, Symbol
from ase.repo_intelligence.parsers import Definition, SourceParser, select_parser

SUPPORTED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".md"}
IGNORED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".ase",
    ".ase-run",
    "vendor",
}


@dataclass
class FileRecord:
    path: str
    language: str
    digest: str
    text: str
    tokens: set[str] = field(default_factory=set)
    definitions: list[Definition] = field(default_factory=list)
    parse_errors: int = 0


@dataclass
class RepositoryIndex:
    root: Path
    files: dict[str, FileRecord] = field(default_factory=dict)
    symbols: dict[str, Symbol] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    parser_name: str = "ast"

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "parser": self.parser_name,
            "files": {
                key: {
                    "language": value.language,
                    "digest": value.digest,
                    "definitions": len(value.definitions),
                    "parse_errors": value.parse_errors,
                }
                for key, value in self.files.items()
            },
            "symbols": {key: value.model_dump() for key, value in self.symbols.items()},
            "relationships": [item.model_dump() for item in self.relationships],
        }

    def write_manifest(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


class RepositoryIndexer:
    def __init__(self, parser: SourceParser | None = None) -> None:
        self._parser = parser

    @property
    def parser(self) -> SourceParser:
        if self._parser is None:
            self._parser = select_parser()
        return self._parser

    def index(self, root: Path) -> RepositoryIndex:
        resolved = root.resolve()
        index = RepositoryIndex(root=resolved, parser_name=self.parser.name)
        for path in sorted(resolved.rglob("*")):
            if not path.is_file() or path.suffix not in SUPPORTED_SUFFIXES:
                continue
            if any(part in IGNORED_PARTS for part in path.relative_to(resolved).parts):
                continue
            relative = path.relative_to(resolved).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            language = self._language(path.suffix)
            record = FileRecord(
                path=relative,
                language=language,
                digest=hashlib.sha256(text.encode()).hexdigest(),
                text=text,
                tokens=self._tokens(text),
            )
            if path.suffix == ".py":
                parsed = self.parser.parse(relative, text)
                record.definitions = parsed.definitions
                record.parse_errors = parsed.parse_errors
                index.symbols.update(parsed.symbols)
                index.relationships.extend(parsed.relationships)
            index.files[relative] = record
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
            ".md": "markdown",
        }[suffix]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
        return {token for token in normalized.split() if len(token) > 2}
