"""bin/check finds an interpreter with pytest, even when EKI_PYTHON is a bare runtime."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_check_skips_an_eki_python_without_pytest(tmp_path):
    bare = tmp_path / "bare-python"
    bare.write_text("#!/bin/sh\nexit 1\n")          # a runtime with no pytest: every import fails
    bare.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python3").symlink_to(sys.executable)  # one that has pytest, if .venv doesn't
    env = {**os.environ, "EKI_PYTHON": str(bare), "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
    out = subprocess.run(["/bin/sh", str(ROOT / "bin" / "check"), "--collect-only", "tests/test_skill_doc.py"],
                         cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "test_skill_mentions_every_command" in out.stdout
