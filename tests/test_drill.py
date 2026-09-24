# SPDX-License-Identifier: Apache-2.0
"""The restart drill (eki/drill.py): the rule "a restart at any moment loses
nothing", proven by restarting a sandboxed engine in the middle of work —
and kept proven: the quick drill is the candidate check's 'restart', the full
one runs weekly and its table goes in the weekly note."""
import json
import sys
import time
from pathlib import Path

import pytest

from eki import candidate, drill, workers

ROOT = Path(__file__).resolve().parent.parent


def test_the_table_says_what_went_wrong_where():
    rows = [drill.Result("chat", "mid-answer"),
            drill.Result("self-work", "base check", ["said 3 lines twice (d1…)"])]
    lines = drill.table(rows)
    assert lines[0].split() == ["work", "restart", "result"]
    assert "chat" in lines[2] and lines[2].rstrip().endswith("ok")
    assert "base check" in lines[3] and "said 3 lines twice" in lines[3]
    assert lines[-1] == "1 of 2 ok"


def test_lost_and_doubled_lines_are_told_apart():
    words = ["d1", "d2", "d3"]
    assert drill._once("d1 d2 d3", words) == []
    assert drill._once("d1 d2", words) == ["lost 1 of 3 lines (d3…)"]
    assert drill._once("d1 d2 d3 d1 d2", words) == ["said 2 lines twice (d1…)"]
    assert drill._once("d10 d11", ["d1"]) == ["lost 1 of 1 lines (d1…)"]     # d10 isn't d1


def test_a_result_is_kept_and_a_week_later_its_due_again(tmp_path):
    path = tmp_path / "drill.json"
    assert drill.due(path=path)                         # never run
    drill.save([drill.Result("chat", "mid-answer")], path=path)
    got = drill.last(path)
    assert got["ok"] and got["results"][0]["work"] == "chat"
    assert not drill.due(path=path)
    assert drill.due(now=time.time() + drill.EVERY + 1, path=path)


def test_the_weekly_note_gets_the_table_as_it_ran():
    got = {"at": time.time(), "results": [
        {"work": "chat", "point": "mid-answer", "problems": []},
        {"work": "resolve", "point": "resolving", "problems": ["the thread says 'couldn't resolve'"]}]}
    text = drill.for_note(got)
    assert text.startswith("**Restart drill**") and "1 not ok" in text
    assert "resolving" in text and "couldn't resolve" in text and "```" in text
    assert drill.for_note({"results": []}) == ""
    assert drill.for_note({**got, "quick": True}) == ""  # the quick one is the candidate check's


def test_the_weekly_drill_starts_once_and_is_held(tmp_path):
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\nsleep 30\n")             # stands in for `python -m eki.drill`
    fake.chmod(0o755)
    path = tmp_path / "drill.json"
    assert drill.weekly(str(fake), ROOT, path=path) == "started"
    (w,) = workers.find(key=drill.WEEKLY)
    assert w.spec["kind"] == "drill" and w.spec["argv"][1:4] == ["-m", "eki.drill", "--save"]
    workers.release(w.id)                               # a restart: the next engine…
    assert drill.weekly(str(fake), ROOT, path=path) == "running"
    assert workers.held(w.id)                           # …holds it: no orphan to sweep
    w.kill(grace=1)
    # one that died without a result isn't started again at once…
    assert drill.weekly(str(fake), ROOT, path=path) == ""
    # …nor once it has one, until a week has passed
    drill.save([drill.Result("chat", "mid-answer")], path=path)
    assert drill.weekly(str(fake), ROOT, path=path, now=time.time() + drill.RETRY + 1) == ""


def test_a_drill_that_finds_something_makes_the_checkout_unfit(monkeypatch, tmp_path):
    monkeypatch.setattr(drill, "quick", lambda code, python=None: [
        drill.Result("chat", "mid-answer"),
        drill.Result("self-work", "candidate check · tests", ["said 6 lines twice (d1…)"])])
    with pytest.raises(RuntimeError) as e:
        candidate.check_restart(tmp_path, sys.executable)
    assert "self-work, restarted candidate check · tests: said 6 lines twice" in str(e.value)
    monkeypatch.setattr(drill, "quick", lambda code, python=None: [drill.Result("chat", "mid-answer")])
    assert "nothing lost or doubled" in candidate.check_restart(tmp_path, sys.executable)


def test_the_drills_own_engines_dont_drill(monkeypatch):
    monkeypatch.setenv("EKI_CHECK_SKIP", "restart, app")
    assert candidate.skipped_here() == {"restart", "app"}
    monkeypatch.delenv("EKI_CHECK_SKIP")
    assert candidate.skipped_here() == set()
    assert candidate.ORDER[-1] == "restart"


def test_a_sandbox_touches_nothing_of_yours(tmp_path):
    sb = drill.Sandbox(ROOT, sys.executable, launchd=False)
    try:
        env = sb.job.env
        assert env["HOME"] == str(sb.home) and sb.root.name.startswith("eki-drill-")
        assert env["EKI_SOURCE"] == str(sb.source) and env["EKI_CHECK_SKIP"] == "restart"
        assert env["EKI_NO_NOTIFY"] == "1"
        # the supervisor it may run restarts this sandbox's job, never yours
        assert "local.eki.engine" not in env["EKI_JOB"]
        assert env["EKI_HEALTH_URL"].endswith(f":{sb.port}/api/health")
        assert (sb.source / ".git").exists() and (sb.source / "eki" / "cli.py").exists()
        assert (sb.eki / "builds" / "current").resolve() == sb.source
        assert json.loads((sb.eki / "settings.json").read_text())["notify_learned"] is False
        assert "sleep" in (sb.source / "tests" / "test_drill.py").read_text()
    finally:
        sb.close()
    assert not sb.root.exists()


def test_a_sandbox_left_behind_is_swept(tmp_path):
    left = tmp_path / "eki-drill-left"
    (left / "home" / ".eki" / "work").mkdir(parents=True)
    (left / "job.json").write_text(json.dumps({"job": "", "pid": 0}))
    assert drill.sweep_stale(base=tmp_path) == []       # young: it may be running
    assert drill.sweep_stale(older=0, now=time.time() + 1, base=tmp_path) == [str(left)]
    assert not left.exists()


def test_the_quick_drill_passes_on_this_checkout():
    """The candidate check's 'restart', for real: an engine of this checkout,
    restarted mid-answer and mid-check. Takes about fifteen seconds."""
    results = drill.quick(ROOT)
    assert [(r.work, r.point) for r in results] == [("chat", "mid-answer"),
                                                   ("self-work", "candidate check · tests")]
    assert all(r.ok for r in results), drill.table(results)
