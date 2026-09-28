"""Every test runs in a throwaway EKI_HOME. The suite refuses to touch your real one."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# What a running eki puts in its children's environment; a check run by eki
# must not see the live build, source or run through them.
AMBIENT = [k for k in os.environ if k.startswith("EKI_") and k != "EKI_PYTHON"
           and not k.startswith("EKI_CHECK_")]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    for k in AMBIENT:
        monkeypatch.delenv(k, raising=False)
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("EKI_HOME", str(h))
    for name in ("EKI_BUILD_DIR", "EKI_LAUNCHED", "EKI_RUN", "EKI_THREAD"):
        monkeypatch.delenv(name, raising=False)      # a suite run by eki must not see eki's own build
    monkeypatch.setenv("EKI_AGENTS_SKILLS", str(tmp_path / "agents-skills"))
    monkeypatch.setenv("EKI_MACHINE", "ok")
    monkeypatch.setenv("EKI_PORT", "0")
    monkeypatch.setenv("EKI_MEMORY_PRESSURE", "1")
    monkeypatch.setenv("EKI_COMFYUI_DIR", str(tmp_path / "no-comfyui"))   # never the real ~/flux
    for k in ("EKI_SOURCE", "EKI_BUILD_DIR", "EKI_LAUNCHED", "EKI_PYTHON", "EKI_WATCH"):
        monkeypatch.delenv(k, raising=False)   # a check run under the engine must not see its world
    bindir = tmp_path / "bin"                         # no test reaches the real gh
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text("#!/bin/sh\necho 'gh: not in tests' >&2\nexit 4\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    from eki import github
    monkeypatch.setattr(github, "_last", {})
    real = Path("~").expanduser()
    assert not str(h).startswith(str(real / ".eki")), "tests must never use the real eki home"
    (h / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"}, "fake2": {"kind": "fake"},
        "bare": {"kind": "fake", "harness": False}}))
    (h / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "general", "title": "all", "targets": ["fake", "fake2"]},
        {"key": "code", "title": "tools", "targets": ["fake", "fake2"]}]}))
    yield h


@pytest.fixture
def conn(home):
    from eki import db
    return db.connect()


def run_inline(conn, rid):
    """What the engine does, without the engine: mark it starting, run the worker here."""
    from eki import store, worker
    store.update_run(conn, rid, state="starting")
    worker.main(rid)
    return store.run(conn, rid)


@pytest.fixture
def proj(tmp_path):
    """A person's own git folder: branch trunk, one commit, untracked .venv and notes."""
    from eki import workspace
    r = tmp_path / "proj"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    (r / "README.md").write_text("# proj\n")
    (r / ".venv").mkdir()                               # untracked, not ignored
    (r / ".venv" / "marker").write_text("venv\n")
    (r / "notes.txt").write_text("the person's own work in progress\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "app.py", "README.md")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


FAKE_GH = """#!{python}
import json, os, re, sys
d = {dir!r}
args = sys.argv[1:]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\\n")
repos = os.path.join(d, "repos.json")
if args[:2] == ["repo", "clone"] and len(args) >= 4 and os.path.exists(repos):
    bare = json.load(open(repos)).get(args[2])
    if bare:
        import subprocess
        dest, url = args[3], "https://github.com/%s.git" % args[2]
        if not os.path.isdir(bare) or subprocess.call(["git", "clone", "-q", bare, dest]) != 0:
            sys.stderr.write("fake gh: can't clone %s\\n" % args[2])
            sys.exit(1)
        for k, v in (("remote.origin.url", url), ("url.%s.insteadOf" % bare, url),
                     ("remote.origin.pushurl", bare)):
            subprocess.check_call(["git", "-C", dest, "config", k, v])
        sys.exit(0)
for k in ("_".join(args[:3]), "_".join(args[:2])):
    base = os.path.join(d, "answers", re.sub(r"[^A-Za-z0-9]", "_", k))
    if os.path.exists(base + ".out"):
        out = open(base + ".out").read()
        code = int(open(base + ".code").read()) if os.path.exists(base + ".code") else 0
        (sys.stdout if code == 0 else sys.stderr).write(out)
        sys.exit(code)
"""


class FakeGh:
    def __init__(self, where: Path):
        self.dir = where
        self.bare = None

    def calls(self):
        f = self.dir / "calls.jsonl"
        return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []

    def repo(self, name, bare):
        """`gh repo clone <name> <dest>` from now on really clones `bare`, origin github.com/<name>."""
        f = self.dir / "repos.json"
        got = json.loads(f.read_text()) if f.exists() else {}
        got[name] = str(bare)
        f.write_text(json.dumps(got))

    def answer(self, args_prefix, out, code=0):
        key = re.sub(r"[^A-Za-z0-9]", "_", "_".join(args_prefix))
        (self.dir / "answers" / f"{key}.out").write_text(out)
        (self.dir / "answers" / f"{key}.code").write_text(str(code))


@pytest.fixture
def fake_gh(tmp_path, home):
    """A gh that records each argv and answers from files: .answer(prefix, out, code)."""
    d = tmp_path / "gh"
    (d / "answers").mkdir(parents=True, exist_ok=True)
    gh = tmp_path / "bin" / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable, dir=str(d)))
    gh.chmod(0o755)
    return FakeGh(d)


@pytest.fixture
def gh_repo(proj, fake_gh, tmp_path):
    """`proj` as if its origin were github.com/me/proj, pushing and fetching to a local bare repo."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(proj), str(bare)], check=True, capture_output=True)
    url = "https://github.com/me/proj.git"
    for k, v in (("remote.origin.url", url), ("remote.origin.pushurl", str(bare)),
                 ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
                 (f"url.{bare}.insteadOf", url)):
        subprocess.run(["git", "-C", str(proj), "config", k, v], check=True)
    fake_gh.answer(["auth", "status"], "")
    fake_gh.answer(["api", "user"], "me")
    fake_gh.bare = bare
    return fake_gh
