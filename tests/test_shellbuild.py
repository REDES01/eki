"""Eki.app rebuilt when a live build changes mac/ — never swiftc, never the real ~/eki."""
import json
import plistlib
import threading
import urllib.request

import pytest

from eki import builds, integration, observe, paths, server, shellbuild, store, workspace
from conftest import run_inline

FAKE_BUILD_MAC = """#!/bin/sh
mkdir -p "$EKI_APP_OUT/Contents"
printf '<plist version="1.0"><dict><key>EkiCommit</key><string>%s</string>' "$EKI_APP_COMMIT" \
  > "$EKI_APP_OUT/Contents/Info.plist"
printf '<key>EkiRoot</key><string>%s</string></dict></plist>' "$EKI_APP_ROOT" >> "$EKI_APP_OUT/Contents/Info.plist"
echo "built $EKI_APP_OUT"
"""


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    for d in ("eki", "mac", "bin", "docs"):
        (r / d).mkdir(parents=True)
    (r / "eki" / "engine.py").write_text("# the engine\n")
    (r / "mac" / "main.swift").write_text("// the shell\n")
    (r / "docs" / "design.md").write_text("# design\n")
    (r / "bin" / "build-mac").write_text(FAKE_BUILD_MAC)
    (r / "bin" / "build-mac").chmod(0o755)
    workspace.git(r, "init", "-q", "-b", "main")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    monkeypatch.setattr(shellbuild, "_quiet", [None])
    monkeypatch.setattr(shellbuild, "LSREGISTER", str(tmp_path / "no-lsregister"))
    return r


def plist(app, commit):
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    data = {"EkiRoot": "/x"}
    if commit is not None:
        data["EkiCommit"] = commit
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(data))
    return app


def installed(src, commit):
    return plist(src / "Eki.app", commit)


def commit(path, text="changed\n"):
    """A commit on integration main changing `path`."""
    r = integration.repo()
    (r / path).parent.mkdir(parents=True, exist_ok=True)
    (r / path).write_text(text)
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", f"change {path}")
    return workspace.head(r)


def live(monkeypatch, sha, healthy=True):
    """A build of `sha` that this process runs from."""
    bid = sha[:12]
    d = builds.root() / bid
    (d / "bin").mkdir(parents=True)
    (d / "bin" / "build-mac").write_text(FAKE_BUILD_MAC)
    (d / "bin" / "build-mac").chmod(0o755)
    (d / "eki").mkdir()
    (d / ".eki-build.json").write_text(json.dumps(
        {"id": bid, "commit": sha, "source": str(builds.source()), "made_at": 1.0}))
    if healthy:
        (d / ".healthy").write_text("1\n")
    monkeypatch.setenv("EKI_BUILD_DIR", str(d))
    return d


def first(src):
    return workspace.head(src)


def shell_runs(conn):
    return conn.execute("SELECT * FROM runs WHERE provider='command'").fetchall()


def faults(conn):
    return observe.entries(conn, kind="fault")


def building(conn, src, monkeypatch, path="mac/main.swift"):
    installed(src, first(src))
    sha = commit(path)
    d = live(monkeypatch, sha)
    said = shellbuild.tick(conn)
    return sha, d, said


def test_a_live_build_that_changes_mac_queues_one_build(conn, src, monkeypatch):
    sha, d, said = building(conn, src, monkeypatch)
    runs = shell_runs(conn)
    assert len(runs) == 1 and any("building Eki.app" in s for s in said)
    run = runs[0]
    assert run["priority"] == "background"
    assert store.thread(conn, run["thread_id"])["title"] == f"shell build: build {d.name}"
    assert store.cwd_of(conn, run["thread_id"]) == str(d)
    assert "mac/main.swift" in run["why"]
    argv = json.loads(run["prompt"])
    assert argv[:2] == ["/bin/sh", "-c"] and argv[2].endswith("bin/build-mac")
    assert f"EKI_APP_COMMIT={sha}" in argv[2]
    assert str(paths.home() / "shell" / d.name / "Eki.app") in argv[2]
    assert f"EKI_APP_ROOT={src}" in argv[2]
    rec = shellbuild.status()
    assert rec["state"] == "building" and rec["commit"] == sha and rec["run"] == run["id"]
    assert shellbuild.view()["state"] == "building"


@pytest.mark.parametrize("path", ["eki/engine.py", "docs/design.md"])
def test_a_build_that_leaves_the_shell_alone_queues_nothing(conn, src, monkeypatch, path):
    building(conn, src, monkeypatch, path)
    assert shell_runs(conn) == []
    assert not (paths.home() / "shell.json").exists()
    assert shellbuild.tick(conn) == []


def test_an_app_without_ekicommit_is_rebuilt_once(conn, src, monkeypatch):
    installed(src, None)
    sha = commit("eki/engine.py")
    live(monkeypatch, sha)
    shellbuild.tick(conn)
    assert len(shell_runs(conn)) == 1
    assert "no EkiCommit" in shell_runs(conn)[0]["why"]


def test_an_app_whose_commit_isnt_in_the_repo_is_rebuilt(conn, src, monkeypatch):
    installed(src, "0" * 40)
    live(monkeypatch, commit("eki/engine.py"))
    shellbuild.tick(conn)
    assert len(shell_runs(conn)) == 1


def test_an_unhealthy_build_queues_nothing(conn, src, monkeypatch):
    installed(src, first(src))
    live(monkeypatch, commit("mac/main.swift"), healthy=False)
    assert shellbuild.tick(conn) == []
    assert shell_runs(conn) == [] and shellbuild.status() is None


def test_dev_mode_does_nothing(conn, src, monkeypatch):
    installed(src, None)
    monkeypatch.setenv("EKI_BUILD_DIR", str(src))      # a checkout, no .eki-build.json
    assert shellbuild.tick(conn) == [] and shell_runs(conn) == []


def test_a_second_tick_while_building_queues_nothing_more(conn, src, monkeypatch):
    building(conn, src, monkeypatch)
    assert shellbuild.tick(conn) == []
    assert len(shell_runs(conn)) == 1


def test_a_restart_with_the_run_gone_starts_it_again(conn, src, monkeypatch):
    sha, d, _ = building(conn, src, monkeypatch)
    old = shellbuild.status()["run"]
    conn.execute("DELETE FROM runs WHERE id=?", (old,))
    said = shellbuild.tick(conn)
    rec = shellbuild.status()
    assert rec["state"] == "building" and rec["run"] != old and rec["commit"] == sha
    assert store.run(conn, rec["run"])["provider"] == "command" and any("again" in s for s in said)


def test_a_done_build_is_swapped_in(conn, src, monkeypatch):
    sha, d, _ = building(conn, src, monkeypatch)
    rid = shellbuild.status()["run"]
    plist(paths.home() / "shell" / d.name / "Eki.app", sha)
    store.update_run(conn, rid, state="done")
    said = shellbuild.tick(conn)
    assert any("installed" in s for s in said)
    assert shellbuild.view()["installed"] == sha and shellbuild.status()["state"] == "installed"
    assert not (src / "Eki.app.old").exists()
    assert not (paths.home() / "shell" / d.name).exists()
    assert shellbuild.tick(conn) == [] and len(shell_runs(conn)) == 1


def test_the_run_itself_builds_the_app_it_swaps_in(conn, src, monkeypatch):
    sha, d, _ = building(conn, src, monkeypatch)
    run = run_inline(conn, shellbuild.status()["run"])
    assert run["state"] == "done", store.answer(conn, run["id"])
    shellbuild.tick(conn)
    assert shellbuild.installed_commit() == sha
    with open(src / "Eki.app" / "Contents" / "Info.plist", "rb") as f:
        assert plistlib.load(f)["EkiRoot"] == str(src)


def test_a_failed_build_is_one_fault_and_not_retried(conn, src, monkeypatch):
    sha, d, _ = building(conn, src, monkeypatch)
    rid = shellbuild.status()["run"]
    store.add_event(conn, rid, 0, "text", {"text": "".join(f"line {i}\n" for i in range(60))
                                           + "main.swift:3: error: nope\n"})
    store.update_run(conn, rid, state="failed", error="exit 1")
    shellbuild.tick(conn)
    got = faults(conn)
    assert len(got) == 1
    data = got[0]["data"]
    assert data["where"] == "shell build" and data["ours"] is False and data["build"] == d.name
    assert "error: nope" in data["output"] and "line 0\n" not in data["output"]
    assert shellbuild.installed_commit() == first(src)
    assert shellbuild.status()["state"] == "failed" and shellbuild.view()["error"]
    assert shellbuild.tick(conn) == []
    assert len(shell_runs(conn)) == 1 and len(faults(conn)) == 1


def test_a_next_build_that_still_touches_mac_tries_again(conn, src, monkeypatch):
    sha, d, _ = building(conn, src, monkeypatch)
    store.update_run(conn, shellbuild.status()["run"], state="failed", error="exit 1")
    shellbuild.tick(conn)
    live(monkeypatch, commit("eki/engine.py", "again\n"))
    shellbuild.tick(conn)
    assert len(shell_runs(conn)) == 2 and shellbuild.status()["state"] == "building"


def test_a_swap_cut_short_puts_the_old_app_back(conn, src, monkeypatch):
    installed(src, first(src))
    (src / "Eki.app").rename(src / "Eki.app.old")
    shellbuild.tick(conn)
    assert shellbuild.installed_commit() == first(src)
    assert not (src / "Eki.app.old").exists()


def test_get_api_shell_is_the_view(conn, src):
    installed(src, first(src))
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{s.server_address[1]}/api/shell"
        with urllib.request.urlopen(url, timeout=10) as r:
            assert json.loads(r.read()) == shellbuild.view()
    finally:
        s.shutdown()
