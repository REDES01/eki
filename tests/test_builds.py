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
    (src / "eki" / "__init__.py").write_text("# yours, not committed\n")
    b = builds.make(src, "self/abc")
    builds.SELF_HOME.mkdir(parents=True)
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(
        {"state": "healthy", "target": str(b), "self": "abc", "at": 1}))
    assert "uncommitted" in builds.settle_swap()["merged"]
    assert (src / "README.md").read_text() == "v1\n"


def test_files_you_never_added_to_git_dont_stop_it(src):
    sh(src, "checkout", "-q", "-b", "self/abc")
    (src / "README.md").write_text("v2\n")
    sh(src, "commit", "-q", "-am", "self: v2")
    sh(src, "checkout", "-q", "main")
    (src / "notes.txt").write_text("yours, untracked\n")
    b = builds.make(src, "self/abc")
    builds.SELF_HOME.mkdir(parents=True)
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(
        {"state": "healthy", "target": str(b), "self": "abc", "at": 1}))
    assert builds.settle_swap()["merged"] == "merged into your checkout"
    assert (src / "README.md").read_text() == "v2\n" and (src / "notes.txt").exists()


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


def test_cmd_builds_says_so_before_any_swap_has_happened(src, capsys):
    from eki.cli import builds as cli_builds
    builds.ensure_layout(src)
    assert cli_builds.cmd_builds(None) == 0
    assert "no swap yet" in capsys.readouterr().out.strip().splitlines()[-1]


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
        if os.path.exists(os.path.join(root, "hang")):
            import time; time.sleep(30)            # a stuck engine: listening, not answering
        busy = os.path.exists(os.path.join(root, "busy"))
        body = json.dumps({"ok": True, "running": ["r1"] if busy else [], "build": bid}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
import time
for _ in range(100):                              # launchd would start it again and again
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        break
    except OSError:
        time.sleep(0.1)
else:
    sys.exit(1)
server.serve_forever()
'''

LAUNCHCTL = r'''#!/bin/bash
root="{root}"
case "$1" in
  kickstart)
    # "abandon": the old engine is left running, as a killed launcher left it
    [ -f "$root/pid" ] && [ ! -f "$root/abandon" ] && kill "$(cat "$root/pid")" 2>/dev/null
    sleep 0.3
    nohup "{python}" "$root/engine.py" "$root" {port} eki.cli serve >/dev/null 2>&1 &
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
    for f in ("abandon", "hang"):
        (root / f).unlink(missing_ok=True)
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
def test_the_supervisor_swaps_anyway_after_a_short_wait(stage):
    time.sleep(0.5)
    (stage["root"] / "busy").write_text("")            # work never stops
    got = supervise(stage, stage["two"], wait=5, watch=1)
    assert got.returncode == 0, got.stderr
    assert Path(os.readlink(builds.BUILDS / "current")) == stage["two"]
    assert "swapping anyway" in (builds.SELF_HOME / "swap.log").read_text()


@pytest.mark.real_processes
def test_one_swap_at_a_time(stage):
    (builds.SELF_HOME).mkdir(parents=True, exist_ok=True)
    (builds.SELF_HOME / "swap.lockdir").mkdir()
    got = supervise(stage, stage["two"])
    assert got.returncode == 3
    assert Path(os.readlink(builds.BUILDS / "current")) == stage["one"]


def _listening(port):
    got = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True)
    return [int(p) for p in got.stdout.split()]


@pytest.mark.real_processes
def test_an_old_engine_left_holding_the_port_is_stopped_by_the_swap(stage):
    """2026-09-24: `kickstart -k` killed the launcher, not the engine under
    it; the old engine kept the port and every new one died. The swap now
    makes sure the old engine is gone."""
    time.sleep(0.5)
    old = _listening(stage["port"])
    assert old
    (stage["root"] / "abandon").write_text("")
    got = subprocess.run(["bash", str(builds.TEMPLATE), str(stage["two"]), "5", "2", "s1"],
                         env={**stage["env"], "EKI_GONE_SECONDS": "2"},
                         capture_output=True, text=True, timeout=90)
    assert got.returncode == 0, got.stderr
    assert "outlived the restart" in (builds.SELF_HOME / "swap.log").read_text()
    assert not set(old) & set(_listening(stage["port"]))
    assert health(stage)["build"] == stage["two"].name


@pytest.mark.real_processes
def test_the_watchdog_restarts_an_engine_that_stops_answering(stage):
    env = {**stage["env"], "EKI_WATCHDOG_SECONDS": "1"}
    watchdog = lambda: subprocess.run(["bash", str(builds.TEMPLATE), "--watchdog"], env=env,  # noqa: E731
                                      capture_output=True, text=True, timeout=60)
    time.sleep(0.5)
    before = (stage["root"] / "pid").read_text().strip()
    (stage["root"] / "hang").write_text("")
    assert watchdog().returncode == 0                   # first seen now: perhaps just starting
    assert (stage["root"] / "pid").read_text().strip() == before
    log = builds.SELF_HOME / "swap.log"
    assert "watchdog" not in (log.read_text() if log.exists() else "")
    watchdog()                                          # still not answering a look later: restarted
    (stage["root"] / "hang").unlink()
    assert "didn't answer /api/health" in log.read_text()
    assert (stage["root"] / "pid").read_text().strip() != before
    time.sleep(0.8)
    assert health(stage)["ok"]


def test_the_watchdog_leaves_an_engine_mid_swap_alone(stage):
    (builds.SELF_HOME).mkdir(parents=True, exist_ok=True)
    (builds.SELF_HOME / "swap.lockdir").mkdir()
    before = (stage["root"] / "pid").read_text().strip()
    for _ in range(2):
        subprocess.run(["bash", str(builds.TEMPLATE), "--watchdog"],
                       env={**stage["env"], "EKI_HEALTH_URL": "http://127.0.0.1:9/none"},
                       capture_output=True, timeout=30)
    assert (stage["root"] / "pid").read_text().strip() == before


# ---- the line: nothing the engine runs is dropped by the next build ---------------------

def _running(src, branch="self/abc", text="v2\n"):
    """A healthy self-change the engine runs, that your checkout didn't take."""
    sh(src, "checkout", "-q", "-b", branch)
    (src / "README.md").write_text(text)
    sh(src, "commit", "-q", "-am", f"{branch}: running")
    sh(src, "checkout", "-q", "main")
    b = builds.make(src, branch)
    builds._point(builds.BUILDS / "current", b)
    return sh(src, "rev-parse", branch)


def test_what_the_engine_runs_but_your_checkout_lacks_is_noticed(src):
    assert builds.behind(src) == ""                              # nothing running from a build yet
    running = _running(src)
    assert builds.behind(src) == running
    assert builds.behind(src, "self/abc") == ""                  # that ref has it
    assert builds.base(src) == running                           # your checkout is only behind


def test_new_work_goes_on_your_commits_and_what_runs_together(src):
    running = _running(src)
    (src / "notes.md").write_text("mine\n")
    sh(src, "add", "notes.md")
    sh(src, "commit", "-q", "-m", "yours, since")
    mine = sh(src, "rev-parse", "HEAD")
    both = builds.base(src)
    assert sh(src, "rev-list", "--parents", "-n1", both).split()[1:] == [mine, running]
    assert sh(src, "show", f"{both}:README.md") == "v2" and sh(src, "show", f"{both}:notes.md") == "mine"
    assert sh(src, "rev-parse", "HEAD") == mine and not (src / ".git" / "MERGE_HEAD").exists()


def test_a_checkout_that_conflicts_with_what_runs_is_named_not_guessed(src):
    _running(src)
    (src / "README.md").write_text("yours\n")
    sh(src, "commit", "-q", "-am", "yours")
    with pytest.raises(ValueError, match="README.md"):
        builds.base(src)
    from eki import selfwork
    with pytest.raises(selfwork.SelfWorkError, match="conflict"):
        selfwork.line(src)


def test_the_missed_merge_goes_in_once_your_checkout_allows(src):
    running = _running(src)
    (src / "eki" / "__init__.py").write_text("# editing\n")
    assert builds.catch_up(src).startswith("not merged: your checkout has uncommitted changes")
    sh(src, "commit", "-q", "-am", "yours, committed")
    assert builds.catch_up(src) == "merged into your checkout, alongside your commits since"
    assert (src / "README.md").read_text() == "v2\n" and (src / "eki" / "__init__.py").read_text() == "# editing\n"
    assert builds.behind(src) == "" and builds.catch_up(src) == ""
    assert sh(src, "merge-base", "--is-ancestor", running, "HEAD") == ""


def test_a_swap_still_waiting_counts_as_running_and_is_superseded(src, tmp_path):
    sup = tmp_path / "sup.sh"
    sup.write_text("#!/bin/sh\nsleep 30\n")
    sup.chmod(0o755)
    first = builds.make(src)
    builds.swap(first, supervisor=sup)
    pid = builds._swap_pid()
    assert pid and builds.live()["commit"] == builds.info(first)["commit"]
    (src / "README.md").write_text("v3\n")
    sh(src, "commit", "-q", "-am", "v3")
    second = builds.make(src)
    builds.swap(second, supervisor=sup)
    for _ in range(50):
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            break
    else:
        pytest.fail("the waiting swap was left running")
    assert builds._swap_pid() != pid and builds.live()["commit"] == builds.info(second)["commit"]
    assert "superseded the waiting swap" in (builds.SELF_HOME / "swap.log").read_text()
    os.kill(builds._swap_pid(), 15)


def _recording_supervisor(tmp_path):
    """A supervisor that writes down what it was asked, then waits."""
    sup = tmp_path / "sup.sh"
    sup.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/asked"\nsleep 30\n')
    sup.chmod(0o755)
    return sup, tmp_path / "asked"


def _asked(path, n):
    for _ in range(100):
        if path.exists() and len(path.read_text().splitlines()) >= n:
            return [ln.split() for ln in path.read_text().splitlines()]
        time.sleep(0.05)
    pytest.fail("the supervisor wasn't started")


def test_a_superseding_swap_keeps_the_first_ones_deadline(src, tmp_path):
    sup, asked = _recording_supervisor(tmp_path)
    first = builds.make(src)
    got = builds.swap(first, wait=100, supervisor=sup)
    deadline = got["deadline"]
    assert 99 <= int(_asked(asked, 1)[0][1]) <= 100
    assert 0 < builds.going_live()["in"] <= 100
    for n in (3, 4):                                  # applies keep coming…
        (src / "README.md").write_text(f"v{n}\n")
        sh(src, "commit", "-q", "-am", f"v{n}")
        again = builds.swap(builds.make(src), wait=600, supervisor=sup)
        assert again["deadline"] == deadline          # …and the wait doesn't start over
    rows = _asked(asked, 3)
    assert all(int(r[1]) <= 100 for r in rows)
    assert json.loads((builds.SELF_HOME / "swap.json").read_text())["deadline"] == deadline
    os.kill(builds._swap_pid(), 15)


def test_a_swap_past_its_deadline_goes_at_once(src, tmp_path):
    sup, asked = _recording_supervisor(tmp_path)
    builds.swap(builds.make(src), wait=100, supervisor=sup)
    _asked(asked, 1)
    s = json.loads((builds.SELF_HOME / "swap.json").read_text())
    s["deadline"] = int(time.time()) - 5              # the first one's wait ran out
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(s))
    (src / "README.md").write_text("v3\n")
    sh(src, "commit", "-q", "-am", "v3")
    builds.swap(builds.make(src), supervisor=sup)
    assert _asked(asked, 2)[1][1] == "0"
    assert builds.going_live()["in"] == 0
    os.kill(builds._swap_pid(), 15)


def test_a_swap_behind_one_mid_swap_waits_it_out(src, tmp_path):
    sup, asked = _recording_supervisor(tmp_path)
    mid = subprocess.Popen(["sleep", "30"])
    builds.SELF_HOME.mkdir(parents=True, exist_ok=True)
    (builds.SELF_HOME / "swap.pid").write_text(str(mid.pid))
    (builds.SELF_HOME / "swap.json").write_text(json.dumps({"state": "swapping", "target": "x"}))
    builds.swap(builds.make(src), supervisor=sup)
    time.sleep(0.5)
    assert not asked.exists()                          # not while the other is swapping
    s = json.loads((builds.SELF_HOME / "swap.json").read_text())
    assert s["state"] == "waiting" and s["behind"] == mid.pid
    (src / "README.md").write_text("v3\n")           # superseded meanwhile: still behind it
    sh(src, "commit", "-q", "-am", "v3")
    builds.swap(builds.make(src), supervisor=sup)
    assert json.loads((builds.SELF_HOME / "swap.json").read_text())["behind"] == mid.pid
    mid.kill()
    mid.wait()
    assert len(_asked(asked, 1)) == 1
    os.kill(builds._swap_pid(), 15)


def test_nothing_going_live_says_nothing(src):
    assert builds.going_live() == {}


def test_the_same_change_under_another_id_is_not_lost(src):
    running = _running(src)
    (src / "notes.md").write_text("mine\n")
    sh(src, "add", "notes.md")
    sh(src, "commit", "-q", "-m", "yours, since")
    assert builds.behind(src) == running
    sh(src, "cherry-pick", running)                              # main has it, as another commit
    assert sh(src, "rev-parse", "HEAD") != running
    assert builds.behind(src) == ""


def test_eki_self_says_when_a_new_version_goes_live():
    from eki.cli.common import _going_live
    assert _going_live({}) == ""
    assert _going_live({"state": "waiting", "self": "ab12", "in": 95}).startswith(
        "new version (self/ab12) going live in 2 min")
    assert _going_live({"state": "swapping", "in": 0}) == "new version going live now"


def test_a_build_healthy_after_its_watch_is_a_known_good_base(src):
    """Its change passed the candidate check before it went in: new work on
    that commit doesn't run the tests all over again."""
    from eki import selfwork
    sh(src, "checkout", "-q", "-b", "self/abc")
    (src / "README.md").write_text("v2\n")
    sh(src, "commit", "-q", "-am", "self: v2")
    sh(src, "checkout", "-q", "main")
    b = builds.make(src, "self/abc", note="self/abc")
    commit = sh(src, "rev-parse", "self/abc")
    builds.SELF_HOME.mkdir(parents=True)
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(
        {"state": "rolled back", "target": str(b), "self": "abc", "at": 1}))
    builds.settle_swap()
    assert not selfwork.base_known_good(commit, builds.SELF_HOME)       # not a rolled-back one
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(
        {"state": "healthy", "target": str(b), "self": "abc", "at": 2}))
    builds.settle_swap()
    assert selfwork.base_known_good(commit, builds.SELF_HOME)
