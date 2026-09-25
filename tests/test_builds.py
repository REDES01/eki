import json
import os

from eki import builds, workspace


def _repo(tmp_path):
    r = tmp_path / "src"
    (r / "eki").mkdir(parents=True)
    (r / "eki" / "engine.py").write_text("# engine\n")
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def test_a_build_is_an_export_of_one_commit(tmp_path):
    r = _repo(tmp_path)
    b = builds.make(r, "HEAD")
    sha = workspace.head(r)
    assert b.name == sha[:12] and (b / "eki" / "engine.py").exists()
    info = json.loads((b / ".eki-build.json").read_text())
    assert info["commit"] == sha and info["source"] == str(r.resolve())
    (r / "eki" / "engine.py").write_text("# changed after\n")
    assert builds.make(r, "HEAD") == b                       # same commit, same build, untouched
    assert (b / "eki" / "engine.py").read_text() == "# engine\n"


def test_swap_moves_the_links_and_back_returns(tmp_path):
    r = _repo(tmp_path)
    a = builds.make(r, "HEAD")
    workspace.git(r, "commit", "-q", "--allow-empty", "-m", "second")
    b = builds.make(r, "HEAD")
    assert builds.swap_to(a)["state"] == "swapping" and builds.current() == a
    rec = builds.swap_to(b, "test")
    assert builds.current() == b and builds.previous() == a and rec["why"] == "test"
    assert builds.swap_to(b)["state"] == "already"
    builds.back()
    assert builds.current() == a and builds.previous() == b
    st = builds.status()
    assert {x["id"] for x in st["builds"]} == {a.name, b.name} and st["swap"]["state"] == "swapping"


def test_step_aside_only_under_the_launcher(tmp_path, monkeypatch):
    r = _repo(tmp_path)
    a = builds.make(r, "HEAD")
    builds.swap_to(a)
    monkeypatch.delenv("EKI_LAUNCHED", raising=False)
    assert not builds.step_aside()
    monkeypatch.setenv("EKI_LAUNCHED", "1")
    monkeypatch.setenv("EKI_BUILD_DIR", str(a))
    assert not builds.step_aside()                          # current is what runs
    monkeypatch.setenv("EKI_BUILD_DIR", str(tmp_path))
    assert builds.step_aside()                              # something else is current


def test_a_healthy_mark_and_the_sweep(tmp_path, monkeypatch):
    r = _repo(tmp_path)
    a = builds.make(r, "HEAD")
    builds.swap_to(a)
    monkeypatch.setenv("EKI_BUILD_DIR", str(a))
    assert builds.mark_healthy() == a.name and builds.mark_healthy() is None
    assert builds.status()["swap"]["state"] == "healthy"
    workspace.git(r, "commit", "-q", "--allow-empty", "-m", "second")
    old = builds.make(r, "HEAD")
    os.utime(old / ".eki-build.json", (0, 0))
    assert builds.sweep(in_use=[old.name], days=1) == []     # a worker still runs from it
    assert builds.sweep(in_use=[], days=1) == [old.name]
    assert builds.sweep(in_use=[], days=1) == []             # current is never swept
