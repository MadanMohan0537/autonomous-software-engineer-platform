import json
import sys
from pathlib import Path

from ase.cli import main, parser


def test_parser_requires_command() -> None:
    assert parser().prog == "ase"


def test_index_command_writes_manifest(tmp_path: Path, monkeypatch: object, capsys: object) -> None:
    (tmp_path / "app.py").write_text("def run(): pass\n", encoding="utf-8")
    output = tmp_path / "index.json"
    monkeypatch.setattr(  # type: ignore[attr-defined]
        sys, "argv", ["ase", "index", str(tmp_path), "--output", str(output)]
    )
    main()
    rendered = capsys.readouterr().out  # type: ignore[attr-defined]
    assert json.loads(rendered)["files"]["app.py"]["language"] == "python"
    assert output.exists()
