"""Conservative symbol extraction for languages not handled by Python AST."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSymbol:
    name: str
    kind: str
    line: int


PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "typescript": [
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
        (
            "function",
            re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("),
        ),
    ],
    "javascript": [
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
    ],
    "go": [
        ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(")),
        ("type", re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b")),
    ],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)")),
        ("type", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)")),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:public\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)")),
        (
            "function",
            re.compile(
                r"^\s*(?:public|private|protected)\s+(?:static\s+)?"
                r"[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\("
            ),
        ),
    ],
}


def parse_symbols(language: str, text: str) -> list[ParsedSymbol]:
    results: list[ParsedSymbol] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS.get(language, []):
            match = pattern.search(line)
            if match:
                results.append(ParsedSymbol(match.group(1), kind, line_number))
                break
    return results
