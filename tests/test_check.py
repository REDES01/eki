"""bin/check finds an interpreter with pytest, runs in parallel unless told not to, and leaves the drills out in fast mode."""
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
    bindir.mkdir(exist_ok=True)
    (bindir / "python3").symlink_to(sys.executable)  # one that has pytest, if .venv doesn't
    env = {**os.environ, "EKI_PYTHON": str(bare), "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
    out = subprocess.run(["/bin/sh", str(ROOT / "bin" / "check"), "--collect-only", "tests/test_skill_doc.py"],
                         cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "test_skill_mentions_every_command" in out.stdout


def check(*args, **env):
    """bin/check with EKI_CHECK_* taken only from `env`."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("EKI_CHECK_")}
    return subprocess.run(["/bin/sh", str(ROOT / "bin" / "check"), *args],
                          cwd=str(ROOT), env={**base, **env}, capture_output=True, text=True)


def test_fast_mode_leaves_the_drills_out():
    out = check("--collect-only", "tests/test_engine.py", EKI_CHECK_FAST="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "test_restart_drill" not in out.stdout and "test_swap_drill" not in out.stdout
    assert "test_only_one_engine" in out.stdout and "2 deselected" in out.stdout


def test_without_fast_mode_the_drills_are_collected():
    out = check("--collect-only", "tests/test_engine.py")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "test_restart_drill" in out.stdout and "test_swap_drill" in out.stdout


def test_serial_starts_no_workers():
    one = "tests/test_skill_doc.py::test_skill_mentions_every_command"
    out = check("-v", one, EKI_CHECK_SERIAL="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "workers" not in out.stdout and "[gw" not in out.stdout
    try:
        import xdist  # noqa: F401
    except ImportError:
        return                                        # a bare interpreter is always serial
    out = check("-v", one)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "workers [1 item]" in out.stdout


def workers(tmp_path, cores, **env):
    """The -n bin/check picks on a machine of `cores` (stub sysctl/nproc, and a
    stub python that has pytest and xdist and prints what it was asked to run)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("sysctl", "nproc"):
        (bindir / name).write_text(f"#!/bin/sh\necho {cores}\n")
        (bindir / name).chmod(0o755)
    py = bindir / "stub-python"
    py.write_text('#!/bin/sh\n[ "$1" = -c ] && exit 0\necho "$@"\n')
    py.chmod(0o755)
    out = check(EKI_PYTHON=str(py), PATH=f"{bindir}:{os.environ.get('PATH', '')}", **env)
    assert out.returncode == 0, out.stdout + out.stderr
    args = out.stdout.split()
    return int(args[args.index("-n") + 1])


def test_wide_takes_all_but_two_cores_and_workers_always_win(tmp_path):
    assert workers(tmp_path, 18, EKI_CHECK_WIDE="1") == 16
    assert workers(tmp_path, 3, EKI_CHECK_WIDE="1") == 2
    assert workers(tmp_path, 18, EKI_CHECK_WIDE="1", EKI_CHECK_WORKERS="5") == 5
    assert workers(tmp_path, 18) == 6
    assert workers(tmp_path, 4) == 2
    assert workers(tmp_path, 18, EKI_CHECK_WORKERS="5") == 5
