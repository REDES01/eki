import json

import pytest

from eki.checkguess import Guess, guess


def _put(folder, name, text=""):
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _pkg(folder, *scripts):
    _put(folder, "package.json",
         json.dumps({"scripts": {s: "x" for s in scripts}}))


def test_bin_check_executable(tmp_path):
    _put(tmp_path, "bin/check", "#!/bin/sh\n").chmod(0o755)
    assert guess(tmp_path) == Guess("bin/check", None, "bin/check")


def test_bin_check_not_executable_does_not_match(tmp_path):
    _put(tmp_path, "bin/check", "#!/bin/sh\n").chmod(0o644)
    assert guess(tmp_path) == Guess(None, None, "no check found")


def test_bin_check_beats_package_json(tmp_path):
    _put(tmp_path, "bin/check", "#!/bin/sh\n").chmod(0o755)
    _pkg(tmp_path, "test")
    assert guess(tmp_path).check == "bin/check"


@pytest.mark.parametrize("scripts,want", [
    (("check", "test", "build"), "check"),
    (("test", "build"), "test"),
    (("build",), "build"),
])
def test_package_json_script_order(tmp_path, scripts, want):
    _pkg(tmp_path, *scripts)
    g = guess(tmp_path)
    assert g.check == f"npm run {want}"
    assert g.why == f"package.json script {want} via npm"


@pytest.mark.parametrize("lock,manager,install", [
    ("pnpm-lock.yaml", "pnpm", "pnpm install --frozen-lockfile"),
    ("yarn.lock", "yarn", "yarn install --frozen-lockfile"),
    ("bun.lockb", "bun", "bun install --frozen-lockfile"),
    ("bun.lock", "bun", "bun install --frozen-lockfile"),
    ("package-lock.json", "npm", "npm ci"),
    (None, "npm", "npm install"),
])
def test_package_json_lockfiles(tmp_path, lock, manager, install):
    _pkg(tmp_path, "test")
    if lock:
        _put(tmp_path, lock)
    assert guess(tmp_path) == Guess(
        f"{manager} run test", install,
        f"package.json script test via {manager}")


def test_package_json_without_scripts_falls_through(tmp_path):
    _pkg(tmp_path, "lint")
    _put(tmp_path, "Cargo.toml")
    assert guess(tmp_path).check == "cargo test"


def test_pytest_uv(tmp_path):
    _put(tmp_path, "pyproject.toml")
    _put(tmp_path, "uv.lock")
    assert guess(tmp_path) == Guess(
        ".venv/bin/python -m pytest -q", "uv sync", "pytest via uv")


def test_pytest_pip_pyproject(tmp_path):
    _put(tmp_path, "pyproject.toml")
    g = guess(tmp_path)
    assert g.install == ("python3 -m venv .venv && "
                         ".venv/bin/pip install -q -e . pytest")
    assert g.check == ".venv/bin/python -m pytest -q"
    assert g.why == "pytest via pip"


def test_pytest_pip_appends_requirements(tmp_path):
    _put(tmp_path, "tests/test_x.py")
    _put(tmp_path, "setup.py")
    _put(tmp_path, "requirements.txt")
    assert guess(tmp_path).install == (
        "python3 -m venv .venv && "
        ".venv/bin/pip install -q -e . pytest -r requirements.txt")


def test_pytest_requirements_only(tmp_path):
    _put(tmp_path, "pytest.ini")
    _put(tmp_path, "requirements.txt")
    g = guess(tmp_path)
    assert g.install == ("python3 -m venv .venv && "
                         ".venv/bin/pip install -q pytest -r requirements.txt")
    assert g.check == ".venv/bin/python -m pytest -q"


def test_pytest_bare(tmp_path):
    (tmp_path / "tests").mkdir()
    assert guess(tmp_path) == Guess("python3 -m pytest -q", None, "pytest")


def test_pytest_src_layout(tmp_path):
    _put(tmp_path, "pyproject.toml")
    _put(tmp_path, "uv.lock")
    (tmp_path / "src").mkdir()
    g = guess(tmp_path)
    assert g.check == "PYTHONPATH=src .venv/bin/python -m pytest -q"
    assert "src" in g.why


def test_makefile_check_before_test(tmp_path):
    _put(tmp_path, "Makefile", "test:\n\ttrue\ncheck: test\n\ttrue\n")
    assert guess(tmp_path) == Guess("make check", None, "Makefile target check")


def test_makefile_test(tmp_path):
    _put(tmp_path, "Makefile", "all:\n\ttrue\ntest:\n\ttrue\n")
    assert guess(tmp_path).check == "make test"


def test_makefile_without_targets_falls_through(tmp_path):
    _put(tmp_path, "Makefile", "all:\n\ttrue\n")
    assert guess(tmp_path).check is None


def test_cargo(tmp_path):
    _put(tmp_path, "Cargo.toml")
    assert guess(tmp_path) == Guess("cargo test", None, "Cargo.toml")


def test_go(tmp_path):
    _put(tmp_path, "go.mod")
    assert guess(tmp_path) == Guess("go test ./...", None, "go.mod")


def test_nothing(tmp_path):
    assert guess(tmp_path) == Guess(None, None, "no check found")
