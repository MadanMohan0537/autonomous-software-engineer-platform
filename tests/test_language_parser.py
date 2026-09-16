from ase.repo_intelligence.language_parser import parse_symbols


def test_extracts_symbols_across_languages() -> None:
    cases = {
        "typescript": "export function analyze(input: string) {}",
        "javascript": "class Runner {}",
        "go": "func Execute() {}",
        "rust": "pub fn evaluate() {}",
        "java": "public class Controller {}",
    }
    for language, source in cases.items():
        assert parse_symbols(language, source), language
