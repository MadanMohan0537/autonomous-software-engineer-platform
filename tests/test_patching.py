import subprocess
from pathlib import Path

from ase.patching import PatchService


def test_applies_valid_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    patch = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    result = PatchService(tmp_path).apply(patch)
    assert result.applied
    assert result.changed_files == ["app.py"]
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_rejects_invalid_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    result = PatchService(tmp_path).apply("not a patch")
    assert not result.applied
