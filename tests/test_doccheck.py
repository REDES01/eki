import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import eki
from eki import doccheck, selfwork, store, workspace
from conftest import run_inline


@pytest.mark.parametrize("files, ok", [
    (["README.md"], True),
    (["a/b/NOTES.md"], True),
    (["docs/x.txt"], True),
    (["./docs/x.txt", "./README.md"], True),
    (["eki/x.py"], False),
    ([], False),
    (["README.md", "eki/x.py"], False),
])
def test_docs_only(files, ok):
    assert doccheck.docs_only(files) is ok


def test_of_item_reads_touched():
    assert doccheck.of_item({"touched": json.dumps(["docs/a.md"])})
    assert not doccheck.of_item({"touched": None})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    (r / "eki").mkdir(parents=True)
    pkg = Path(eki.__file__).parent
    for name in ("__init__.py", "doccheck.py"):          # the worktree is an eki checkout
        shutil.copy(pkg / name, r / "eki" / name)
    (r / "README.md").write_text("# hi\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def commit(r, files):
    for rel, data in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "change")
    return workspace.head(r)


def check(r, base, sha, monkeypatch, capsys):
    monkeypatch.chdir(r)
    code = doccheck.main([f"{base}..{sha}"])
    return code, capsys.readouterr().out


def test_main_is_green_for_a_markdown_edit(repo, monkeypatch, capsys):
    base = workspace.head(repo)
    sha = commit(repo, {"README.md": "# hello ✓\n".encode(), "docs/guide.txt": b"plain\n"})
    code, out = check(repo, base, sha, monkeypatch, capsys)
    assert code == 0 and "✓ README.md" in out and "✓ docs/guide.txt" in out


def test_main_is_red_for_code_in_the_range(repo, monkeypatch, capsys):
    base = workspace.head(repo)
    sha = commit(repo, {"README.md": b"# more\n", "eki/x.py": b"x = 1\n"})
    code, out = check(repo, base, sha, monkeypatch, capsys)
    assert code == 1 and "✗ eki/x.py: outside the docs lane" in out and "✓ README.md" in out


def test_main_is_red_for_invalid_utf8(repo, monkeypatch, capsys):
    base = workspace.head(repo)
    sha = commit(repo, {"docs/bad.md": b"caf\xe9\n"})
    code, out = check(repo, base, sha, monkeypatch, capsys)
    assert code == 1 and "✗ docs/bad.md: not UTF-8 text" in out


def test_deleted_docs_are_fine_but_an_empty_or_bad_range_is_not(repo, monkeypatch, capsys):
    base = workspace.head(repo)
    (repo / "README.md").unlink()
    sha = commit(repo, {})
    assert check(repo, base, sha, monkeypatch, capsys)[0] == 0
    assert check(repo, sha, sha, monkeypatch, capsys)[0] == 1
    assert check(repo, base, "nope", monkeypatch, capsys)[0] == 1
    assert doccheck.main(["no-range"]) == 1


def test_the_command_runs_as_a_subprocess(repo):
    base = workspace.head(repo)
    good = commit(repo, {"docs/a.md": b"# a\n"})
    bad = commit(repo, {"eki/y.py": b"y = 1\n"})
    env = {**os.environ, "EKI_PYTHON": sys.executable}

    def run(b, s):
        argv = json.loads(doccheck.command(b, s))
        return subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True)

    assert "eki.doccheck" in doccheck.command(base, good)
    ok = run(base, good)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "✓ docs/a.md" in ok.stdout
    red = run(good, bad)
    assert red.returncode == 1 and "outside the docs lane" in red.stdout


# ---- gate 1 --------------------------------------------------------------------------------

@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "x.py").write_text("# x\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


def judged(conn, tmp_path, monkeypatch, touch):
    selfwork.submit(conn, "x", plan=False, files=[touch])
    selfwork.tick(conn)
    it = selfwork.items_in(conn, ("building",))[0]
    says = tmp_path / "says.txt"
    says.write_text("SUMMARY: done.\n\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(says))
    monkeypatch.setenv("EKI_FAKE_TOUCH", touch)
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "judging"
    return it, store.run(conn, it["run_id"])


def test_gate_1_judges_a_docs_only_item_with_doccheck(conn, src, tmp_path, monkeypatch):
    it, judge = judged(conn, tmp_path, monkeypatch, "docs/x.md")
    assert judge["provider"] == "command" and "eki.doccheck" in judge["prompt"]
    assert judge["prompt"] == doccheck.command(it["base"], it["commit_sha"])


def test_gate_1_still_runs_the_checks_for_code(conn, src, tmp_path, monkeypatch):
    it, judge = judged(conn, tmp_path, monkeypatch, "eki/x.py")
    assert judge["prompt"] == selfwork.CHECK
