# SPDX-License-Identifier: Apache-2.0
"""What applying really is, shown: each change at its real stage, the
go-live as one group, a timeline per change (eki/pipeline.py)."""
import subprocess
import time
from pathlib import Path

from eki import cli, pipeline, selfwork

from test_selfwork import agent, propose, repo, verdict

NOW = 1_800_000_000.0


def at(**kw):
    """A stage, with nothing going on unless a test says so."""
    c = {"id": "c1", "state": "proposed", "title": "one", **kw.pop("c", {})}
    args = dict(evs=[], queue=[], running=(), train={}, leaves_at=None, swap={}, swap_alive=False,
                running_build="b0", now=NOW, boot="me")
    args.update(kw)
    return pipeline.stage(c, **args)


def test_in_line_says_behind_whom_and_a_cut_off_one_waits_to_be_resumed():
    queue = [{"change": "a", "run": "ra"}, {"change": "dead", "run": "gone"}, {"change": "c1", "run": "r1"}]
    got = at(queue=queue, running={"ra", "r1"})
    assert got["stage"] == "in line" and got["text"] == "in line behind self/a" and not got["live"]
    assert at(queue=queue[2:], running={"r1"})["text"] == "in line — next"
    cut = at(queue=queue, running={"ra"})
    assert cut["stalled"] and cut["text"].endswith("waiting to be resumed")


def test_its_turn_says_what_it_is_doing_and_spins_only_while_its_run_is_live():
    queue = [{"change": "c1", "run": "r1", "applying": True}]
    evs = [{"event": "queued", "at": NOW - 90}, {"event": "turn", "at": NOW - 60},
           {"event": "rebasing", "at": NOW - 50}]
    got = at(queue=queue, running={"r1"}, evs=evs)
    assert (got["stage"], got["text"], got["live"]) == ("rebasing", "rebasing", True)
    evs.append({"event": "checking", "at": NOW - 10})
    assert at(queue=queue, running={"r1"}, evs=evs)["text"] == "checking again"
    cut = at(queue=queue, running=(), evs=evs)
    assert not cut["live"] and cut["text"] == "checking again — waiting to be resumed"
    assert "applying" not in cut["text"]


def test_fixing_conflicts_names_the_files_the_run_and_how_long():
    c = {"state": "conflicts", "resolving": "run9", "resolving_at": NOW - 300,
         "rebase": {"files": ["README.md", "eki/x.py"]}}
    got = at(c=c, running={"run9"})
    assert got["text"] == "fixing conflicts in README.md, eki/x.py (run run9, 5 min)" and got["live"]
    assert at(c=c)["text"].endswith("— waiting to be resumed") and not at(c=c)["live"]


def test_landed_says_when_it_goes_live_and_with_how_many_others():
    train = {"cars": [{"self": "a"}, {"self": "c1"}, {"self": "b"}]}
    leaves = NOW + 600
    got = at(c={"state": "applying"}, train=train, leaves_at=leaves)
    assert got["stage"] == "landed"
    assert got["text"] == f"landed, goes live at {time.strftime('%H:%M', time.localtime(leaves))} with 2 others"


def test_after_it_leaves_going_live_then_watching_then_live_or_rolled_back():
    target = "/b/new1"
    train = {"departed": {"build": target, "cars": ["c1", "c2"], "at": NOW - 100}}
    c = {"state": "applying", "build": target}
    waiting = {"state": "waiting", "target": target, "deadline": NOW + 30}
    assert at(c=c, train=train, swap=waiting, swap_alive=True)["text"] == "going live"
    lost = at(c=c, train=train, swap=waiting, swap_alive=False)
    assert lost["stalled"] and "waiting to be resumed" in lost["text"]
    swapping = {"state": "swapping", "target": target, "at": NOW - 40}
    old = pipeline.STARTED
    pipeline.STARTED = NOW - 30
    try:
        got = at(c=c, train=train, swap=swapping, swap_alive=True, running_build="new1")
    finally:
        pipeline.STARTED = old
    assert got["stage"] == "watching" and got["text"] == f"watching ({pipeline.WATCH - 30} s left)"
    assert at(c=c, train=train, swap={"state": "healthy", "target": target})["text"] == "live"
    back = at(c=c, train=train, swap={"state": "rolled back", "target": target, "why": "it didn't come up"})
    assert back["text"] == "rolled back (it didn't come up)"
    assert at(c={"state": "rolled back", "why": "unhealthy"})["text"] == "rolled back (unhealthy)"


def test_a_change_not_on_its_way_in_has_no_stage():
    assert at() == {} and at(c={"state": "unfit"}) == {}


def test_the_journal_keeps_each_point_once_and_starts_over_when_queued_again(tmp_path):
    for e in ("queued", "turn", "turn", "rebasing", "rebased", "rolled back", "queued", "turn"):
        pipeline.mark("c1", e, tmp_path)
    pipeline.mark("c2", "queued", tmp_path)
    assert [e["event"] for e in pipeline.events("c1", tmp_path)] == ["queued", "turn"]
    assert len([e for e in pipeline.journal(tmp_path) if e["change"] == "c1"]) == 7


def test_the_timeline_reads_the_points_with_their_notes():
    evs = [{"event": "queued", "at": 1}, {"event": "turn", "at": 2}, {"event": "conflicts", "at": 3,
           "files": ["README.md"]}, {"event": "fixed", "at": 4}, {"event": "rechecked", "at": 5, "fit": True},
           {"event": "landed", "at": 6}, {"event": "leaving", "at": 7, "with": ["c2"]}, {"event": "live", "at": 8}]
    tl = pipeline.timeline("c1", evs=evs)
    assert [r["step"] for r in tl] == ["queued", "conflicts", "conflicts fixed", "rechecked", "landed",
                                       "left to go live", "live"]
    assert tl[1]["note"] == "README.md" and tl[5]["note"] == "with self/c2"
    # a change from before the journal: the queue and the train say when
    tl = pipeline.timeline("old", evs=[], queued_at=10, landed_at=20)
    assert [(r["step"], r["at"]) for r in tl] == [("queued", 10), ("landed", 20)]


def test_the_go_live_is_one_group_what_it_carries_when_it_leaves_and_how_it_came_out(tmp_path):
    rows = [{"id": "a", "title": "A", "state": "applying", "state_at": NOW},
            {"id": "b", "title": "B", "state": "applying", "state_at": NOW},
            {"id": "old", "title": "Old", "state": "applying", "state_at": NOW, "build": "/b/x"}]
    train = {"cars": [{"self": "a", "at": NOW - 5}, {"self": "b", "at": NOW - 2}],
             "departed": {"build": "/b/x", "cars": ["old"], "at": NOW - 900}}
    swap = {"state": "rolled back", "target": "/b/x", "why": "it stopped", "at": NOW - 600}
    v = pipeline.view(rows, queue=[], running=(), train=train, leaves_at=NOW + 120, swap=swap,
                      swap_alive=False, running_build="b0", now=NOW, home=tmp_path)
    g = v["golive"]
    assert [c["id"] for c in g["next"]["carrying"]] == ["a", "b"] and g["next"]["in"] == 120
    assert g["last"]["carrying"] == [{"id": "old", "title": "Old"}]
    assert g["last"]["text"] == "rolled back (it stopped)" and g["last"]["settled_at"] == NOW - 600
    stages = {r["id"]: r["text"] for r in v["changes"]}
    assert stages["old"] == "rolled back (it stopped)" and stages["a"].startswith("landed, goes live at")
    said = "\n".join(pipeline.lines(v, now=NOW))
    assert "next go-live at" in said and "self/a, self/b" in said
    assert "last go-live left" in said and "rolled back (it stopped)" in said
    assert "applying" not in said


def test_applying_writes_down_each_point_it_passes(tmp_path):
    root = repo(tmp_path)
    p = propose(tmp_path, root, agent({"eki/thing.py": "VALUE = 2\n"}))
    home = tmp_path / "self"
    (root / "README.md").write_text("moved on\n")
    selfwork.git(root, "add", "-A")
    selfwork.git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "yours")
    got = selfwork.apply(p.id, home=home, check=verdict(True), swap=lambda b, **kw: None)
    assert got["state"] == "applying"
    assert [e["event"] for e in pipeline.events(p.id, home)] == ["rebasing", "rebased", "checking",
                                                                 "rechecked", "landed"]
    selfwork.settled(p.id, "healthy", home=home)
    assert pipeline.events(p.id, home)[-1]["event"] == "live"


def test_a_rebase_that_conflicts_is_written_down_too(tmp_path):
    root = repo(tmp_path)
    p = propose(tmp_path, root, agent({"eki/thing.py": "VALUE = 2\n"}))
    home = tmp_path / "self"
    (root / "eki" / "thing.py").write_text("VALUE = 3\n")
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "3"],
                   check=True)
    assert selfwork.apply(p.id, home=home, check=verdict(True), swap=lambda b, **kw: None)["state"] == "conflicts"
    assert [e["event"] for e in pipeline.events(p.id, home)] == ["rebasing", "conflicts"]


def test_eki_self_show_prints_where_it_stands_and_its_timeline(monkeypatch, capsys):
    c = {"id": "c1", "lines": ["self/c1  one"], "stage": {"text": "rebasing", "live": True},
         "timeline": [{"step": "queued", "at": NOW, "note": ""},
                      {"step": "conflicts", "at": NOW + 60, "note": "README.md"}]}
    monkeypatch.setattr(cli, "call", lambda *a, **kw: c)

    class Args:
        service = "http://x"
    assert cli._self_verb(Args(), "show", ["c1"]) == 0
    out = capsys.readouterr().out
    assert "now: rebasing" in out and "timeline:" in out and "queued" in out
    assert "conflicts — README.md" in out


def test_eki_self_watch_draws_the_pipeline_until_stopped(monkeypatch, capsys):
    v = {"pipeline": [{"id": "c1", "title": "one", "text": "in line behind self/a", "live": False,
                       "timeline": [{"step": "queued", "at": NOW, "note": ""}]}], "golive": {}}
    monkeypatch.setattr(cli, "call", lambda *a, **kw: v)
    naps = []

    def nap(s):
        naps.append(s)
        if len(naps) == 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(cli.time, "sleep", nap)
    assert cli._self_watch("http://x") == 0
    out = capsys.readouterr().out
    assert out.count("self/c1  one — in line behind self/a") == 2 and "queued" in out
    # an engine from before the pipeline: said, not a crash
    assert cli._pipeline_lines({"working": []}) is None
