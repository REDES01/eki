# SPDX-License-Identifier: Apache-2.0
"""What the engine runs, the swap, and the way back — including the real
supervisor script against a stand-in engine and launchd."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from eki import builds


def sh(where, *args):
    return subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def src(tmp_path):
    r = tmp_path / "eki-src"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    (r / "eki").mkdir()
    (r / "eki" / "__init__.py").write_text("")
    (r / "README.md").write_text("v1\n")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "v1")
    return r


def test_a_build_is_an_export_of_one_commit_made_once(src):
    b = builds.make(src)
    mark = json.loads((b / builds.MARK).read_text())
    assert mark["commit"] == sh(src, "rev-parse", "HEAD") and b.name == mark["id"]
    assert (b / "README.md").read_text() == "v1\n" and not (b / ".git").exists()
    (src / "README.md").write_text("dirty, uncommitted\n")
    assert builds.make(src) == b and (b / "README.md").read_text() == "v1\n"
    with pytest.raises(ValueError):
        builds.make(src, "no-such-ref")


def test_a_checkout_is_dev_and_a_build_says_what_it_is(src):
    assert builds.info(src)["id"] == "dev" and builds.info(src)["commit"]
    b = builds.make(src)
    assert builds.info(b)["id"] == b.name and builds.info(b)["source"] == str(src)


def test_current_starts_as_your_checkout_and_the_listing_marks_it(src):
    cur = builds.ensure_layout(src)
    assert os.readlink(cur) == str(src)
    b = builds.make(src)
    builds._point(builds.BUILDS / "previous", src)
    builds._point(cur, b)
    rows = {r["id"]: r for r in builds.listing()}
    assert rows[b.name]["current"] and rows["dev"]["previous"]
    builds.ensure_layout(src)                         # never undoes a swap
    assert Path(os.readlink(cur)) == b


def test_a_healthy_self_change_is_fast_forwarded_into_your_checkout_once(src):
    sh(src, "checkout", "-q", "-b", "self/abc")
    (src / "README.md").write_text("v2\n")
    sh(src, "commit", "-q", "-am", "self: v2")
    sh(src, "checkout", "-q", "main")
    b = builds.make(src, "self/abc", note="self/abc")
    builds.SELF_HOME.mkdir(parents=True)
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(
        {"state": "healthy", "target": str(b), "self": "abc", "at": 1}))
    done = builds.settle_swap()
    assert done["merged"] == "merged into your checkout"
    assert (src / "README.md").read_text() == "v2\n"
    assert builds.settle_swap() == {}                 # said once


def test_a_self_change_waits_if_your_checkout_has_work_in_it(src):
    sh(src, "checkout", "-q", "-b", "self/abc")
    (src / "README.md").write_text("v2\n")
    sh(src, "commit", "-q", "-am", "self: v2")
    sh(src, "checkout", "-q", "main")
    (src / "notes.txt").write_text("yours\n")
    b = builds.make(src, "self/abc")
    builds.SELF_HOME.mkdir(parents=True)
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(
        {"state": "healthy", "target": str(b), "self": "abc", "at": 1}))
    assert "uncommitted" in builds.settle_swap()["merged"]
    assert (src / "README.md").read_text() == "v1\n"


def test_old_builds_go_but_never_current_or_previous(src):
    a = builds.make(src)
    (src / "README.md").write_text("v2\n")
    sh(src, "commit", "-q", "-am", "v2")
    b = builds.make(src)
    (src / "README.md").write_text("v3\n")
    sh(src, "commit", "-q", "-am", "v3")
    c = builds.make(src)
    builds._point(builds.BUILDS / "current", c)
    builds._point(builds.BUILDS / "previous", b)
    gone = builds.prune(now=time.time() + 8 * 86400)
    assert gone == [str(a)] and b.exists() and c.exists()


def test_swap_needs_the_supervisor_a_person_installed(src):
    with pytest.raises(RuntimeError, match="eki agent install"):
        builds.swap(builds.make(src))
    assert builds.install_supervisor() == builds.SUPERVISOR
    assert os.access(builds.SUPERVISOR, os.X_OK)


# ---- the supervisor itself, against a stand-in engine and launchd -----------------

ENGINE = r'''
import http.server, json, os, sys
root, port = sys.argv[1], int(sys.argv[2])
cur = os.path.realpath(os.path.join(root, "builds", "current"))
if os.path.exists(os.path.join(cur, "broken")):
    sys.exit(1)                                   # a build that can't start
try:
    bid = json.load(open(os.path.join(cur, ".eki-build.json")))["id"]
except OSError:
    bid = "dev"
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        busy = os.path.exists(os.path.join(root, "busy"))
        body = json.dumps({"ok": True, "running": ["r1"] if busy else [], "build": bid}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
'''

LAUNCHCTL = r'''#!/bin/bash
root="{root}"
case "$1" in
  kickstart)
    [ -f "$root/pid" ] && kill "$(cat "$root/pid")" 2>/dev/null
    sleep 0.3
    nohup "{python}" "$root/engine.py" "$root" {port} >/dev/null 2>&1 &
    echo $! > "$root/pid" ;;
  print)
    if [ -f "$root/pid" ] && kill -0 "$(cat "$root/pid")" 2>/dev/null; then
      printf '\tpid = %s\n' "$(cat "$root/pid")"
    fi ;;
esac
'''


@pytest.fixture
def stage(tmp_path, src):
    """builds/, a stand-in engine on a free port and a launchctl that restarts it."""
    root = builds.BUILDS.parent
    root.mkdir(parents=True, exist_ok=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (root / "engine.py").write_text(ENGINE)
    lc = root / "launchctl"
    lc.write_text(LAUNCHCTL.format(root=root, python=sys.executable, port=port))
    lc.chmod(0o755)
    one = builds.make(src)
    (src / "README.md").write_text("v2\n")
    sh(src, "commit", "-q", "-am", "v2")
    two = builds.make(src)
    builds._point(builds.BUILDS / "current", one)
    subprocess.run([str(lc), "kickstart"], check=True)
    env = {**os.environ, "EKI_BUILDS": str(builds.BUILDS), "EKI_SELF_HOME": str(builds.SELF_HOME),
           "EKI_HEALTH_URL": f"http://127.0.0.1:{port}/api/health", "EKI_LAUNCHCTL": str(lc),
           "EKI_JOB": "test", "EKI_UP_SECONDS": "8"}
    yield {"root": root, "one": one, "two": two, "env": env, "port": port}
    subprocess.run([str(lc), "kickstart"], capture_output=True)       # restart…
    pid = (root / "pid").read_text().strip()
    subprocess.run(["kill", pid], capture_output=True)               # …then stop it


def supervise(stage, target, wait=5, watch=2):
    return subprocess.run(["bash", str(builds.TEMPLATE), str(target), str(wait), str(watch), "s1"],
                          env=stage["env"], capture_output=True, text=True, timeout=60)


def health(stage):
    import urllib.request
    return json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{stage['port']}/api/health", timeout=3).read())


@pytest.mark.real_processes
def test_the_supervisor_swaps_to_a_healthy_build(stage):
    time.sleep(0.5)
    assert health(stage)["build"] == stage["one"].name
    got = supervise(stage, stage["two"])
    assert got.returncode == 0, got.stderr
    assert Path(os.readlink(builds.BUILDS / "current")) == stage["two"]
    assert Path(os.readlink(builds.BUILDS / "previous")) == stage["one"]
    assert health(stage)["build"] == stage["two"].name
    out = json.loads((builds.SELF_HOME / "swap.json").read_text())
    assert out["state"] == "healthy" and out["self"] == "s1"


@pytest.mark.real_processes
def test_a_build_that_cant_start_is_rolled_back(stage):
    (stage["two"] / "broken").write_text("")
    got = supervise(stage, stage["two"])
    assert got.returncode == 2
    assert Path(os.readlink(builds.BUILDS / "current")) == stage["one"]
    time.sleep(0.8)
    assert health(stage)["build"] == stage["one"].name             # the old one is back up
    out = json.loads((builds.SELF_HOME / "swap.json").read_text())
    assert out["state"] == "rolled back" and "didn't come up" in out["why"]
    assert "rolled back" in (builds.SELF_HOME / "swap.log").read_text()


@pytest.mark.real_processes
def test_the_supervisor_waits_for_runs_to_finish(stage):
    time.sleep(0.5)
    (stage["root"] / "busy").write_text("")
    proc = subprocess.Popen(["bash", str(builds.TEMPLATE), str(stage["two"]), "30", "1"],
                            env=stage["env"])
    time.sleep(3)
    assert Path(os.readlink(builds.BUILDS / "current")) == stage["one"]   # still waiting
    (stage["root"] / "busy").unlink()
    assert proc.wait(timeout=40) == 0
    assert Path(os.readlink(builds.BUILDS / "current")) == stage["two"]


@pytest.mark.real_processes
def test_one_swap_at_a_time(stage):
    (builds.SELF_HOME).mkdir(parents=True, exist_ok=True)
    (builds.SELF_HOME / "swap.lockdir").mkdir()
    got = supervise(stage, stage["two"])
    assert got.returncode == 3
    assert Path(os.readlink(builds.BUILDS / "current")) == stage["one"]
