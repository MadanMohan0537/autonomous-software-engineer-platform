"""Syntax-aware chunking.

A chunk is one top-level definition with its docstring, or a module header (imports,
constants, module docstring), or a fixed window of lines for files without a parser.
Chunking at definition level is what keeps retrieval sharp: embedding whole files blurs
every query into "this file is vaguely about billing".
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from ase.repo_intelligence.indexer import FileRecord
from ase.repo_intelligence.parsers import Definition

DEFAULT_MAX_TOKENS = 1_500
WINDOW_LINES = 60


class Chunk(BaseModel):
    id: str
    path: str
    symbol: str | None = None
    kind: str = "window"  # "definition" | "header" | "window"
    start_line: int
    end_line: int
    text: str
    docstring: str = ""
    tokens: int = Field(ge=0)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _make(
    path: str, kind: str, start: int, end: int, text: str, symbol: str | None, doc: str
) -> Chunk:
    identifier = f"{path}:{start}-{end}"
    return Chunk(
        id=identifier,
        path=path,
        symbol=symbol,
        kind=kind,
        start_line=start,
        end_line=end,
        text=text,
        docstring=doc,
        tokens=estimate_tokens(text),
    )


def _split_definition(
    lines: list[str], definition: Definition, children: list[Definition], max_tokens: int
) -> list[Chunk]:
    """A class larger than the budget is split into one chunk per method."""
    chunks: list[Chunk] = []
    first_child = min((child.start_line for child in children), default=definition.end_line + 1)
    header_end = max(definition.start_line, first_child - 1)
    header = "\n".join(lines[definition.start_line - 1 : header_end])
    chunks.append(
        _make(
            definition.path,
            "definition",
            definition.start_line,
            header_end,
            header,
            definition.name,
            definition.docstring,
        )
    )
    for child in children:
        text = "\n".join(lines[child.start_line - 1 : child.end_line])
        chunks.extend(
            _windows(child.path, lines, child.start_line, child.end_line, max_tokens)
            if estimate_tokens(text) > max_tokens
            else [
                _make(
                    child.path,
                    "definition",
                    child.start_line,
                    child.end_line,
                    text,
                    child.name,
                    child.docstring,
                )
            ]
        )
    return chunks


def _windows(path: str, lines: list[str], start: int, end: int, max_tokens: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + WINDOW_LINES - 1)
        text = "\n".join(lines[cursor - 1 : window_end])
        while estimate_tokens(text) > max_tokens and window_end > cursor:
            window_end = cursor + (window_end - cursor) // 2
            text = "\n".join(lines[cursor - 1 : window_end])
        chunks.append(_make(path, "window", cursor, window_end, text, None, ""))
        cursor = window_end + 1
    return chunks


def chunk_file(record: FileRecord, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[Chunk]:
    lines = record.text.splitlines()
    if not lines:
        return []
    top_level = sorted(
        (item for item in record.definitions if item.parent is None), key=lambda d: d.start_line
    )
    if not top_level:
        return _windows(record.path, lines, 1, len(lines), max_tokens)

    chunks: list[Chunk] = []
    header_end = top_level[0].start_line - 1
    if header_end >= 1:
        header = "\n".join(lines[:header_end]).strip()
        if header:
            chunks.append(_make(record.path, "header", 1, header_end, header, None, ""))

    for definition in top_level:
        text = "\n".join(lines[definition.start_line - 1 : definition.end_line])
        if estimate_tokens(text) <= max_tokens:
            chunks.append(
                _make(
                    record.path,
                    "definition",
                    definition.start_line,
                    definition.end_line,
                    text,
                    definition.name,
                    definition.docstring,
                )
            )
            continue
        children = [item for item in record.definitions if item.parent == definition.name]
        if children:
            chunks.extend(_split_definition(lines, definition, children, max_tokens))
        else:
            chunks.extend(
                _windows(record.path, lines, definition.start_line, definition.end_line, max_tokens)
            )

    trailing_start = top_level[-1].end_line + 1
    if trailing_start <= len(lines):
        trailing = "\n".join(lines[trailing_start - 1 :]).strip()
        if trailing:
            chunks.append(
                _make(record.path, "header", trailing_start, len(lines), trailing, None, "")
            )
    return chunks
