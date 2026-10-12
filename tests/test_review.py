"""The review: an independent judge run beside gate 1 (eki/review.py, eki/judge.py)."""
import json

import pytest

from eki import chores, digest, judge, paths, queue, review, selfview, selfwork, store, verdicts, workspace
from conftest import run_inline
from test_self import src, says  # noqa: F401 — the source fixture and what the fake says

#: the real judge (the home fixture turns per-item judging off for every other test)
START = judge.start_item


@pytest.fixture(autouse=True)
def judging(monkeypatch):
    monkeypatch.setattr(judge, "start_item", START)


def settle(**kw):
    rp = paths.config("routing")
    data = json.loads(rp.read_text())
    data["self"] = {**(data.get("self") or {}), **kw}
    rp.write_text(json.dumps(data))


def item(conn, iid):
    return selfwork.store_item(conn, iid)


def goal_with(conn, text, files, checks=(("c1", "a() returns 1"), ("c2", "the docs say so")), carries=("c1",)):
    """An unplanned goal whose one item carries some of the goal's checks."""
    gid = selfwork.submit(conn, text, plan=False, files=list(files))
    verdicts.store_checks(conn, gid, checks)
    conn.execute("UPDATE items SET checks=? WHERE goal_id=?", (json.dumps(list(carries)), gid))
    return gid


def build(conn, tmp_path, monkeypatch, files=("eki/a.py",)):
    """Build the items that are waiting (disjoint files start together); returns them judging."""
    selfwork.tick(conn)
    items = selfwork.items_in(conn, ("building",))
    says(tmp_path, monkeypatch, "SUMMARY: made a, and it is perfect.\n\nITEM: done\n")
    for it, f in zip(items, files):
        monkeypatch.setenv("EKI_FAKE_TOUCH", f)
        run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    items = [item(conn, it["id"]) for it in items]
    assert all(it["state"] == "judging" for it in items)
    return items


def rec(cid, verdict="passed", **kw):
    """A well-formed verdict on `cid`."""
    r = dict(check_id=cid, verdict=verdict, evidence="ran `eki slot -- bin/check tests/test_a.py`: 3 passed",
             reasoning="the output shows it", counter_evidence="none found", limitation="", suggestion="")
    if verdict != "passed":
        r["suggestion"] = f"make {cid} hold"
    if verdict == "uncertain":
        r["limitation"] = f"could not run what {cid} needs"
    r.update(kw)
    return r


def filed(conn, tmp_path, monkeypatch, rid, *records):
    """The judge run files `records` with `eki verdict` while it runs, and ends."""
    for r in records:
        verdicts.submit(conn, rid, r)
    says(tmp_path, monkeypatch, f"I looked.\n\nJUDGED: {len(records)}\n")
    return run_inline(conn, rid)


def gate(conn, it):
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    return item(conn, it["id"])


def judged(conn, tmp_path, monkeypatch, it, *records):
    """Gate 1 ends first, then the judge: the item moves only on the second."""
    it = gate(conn, it)
    assert it["state"] == "judging"
    filed(conn, tmp_path, monkeypatch, it["review_run"], *records)
    selfwork.tick(conn)
    return item(conn, it["id"])


def rebuild(conn, tmp_path, monkeypatch, it):
    assert it["state"] == "building"
    says(tmp_path, monkeypatch, "SUMMARY: made a.\n\nITEM: done\n")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    return item(conn, it["id"])


def test_ask_starts_a_registered_judge_run_beside_gate_one_and_all_passed_proposes(conn, src, tmp_path,
                                                                                   monkeypatch):
    gid = goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    assert it["state"] == "judging" and it["run_id"] and it["review_run"] and it["review"] is None
    assert store.run(conn, it["run_id"])["provider"] == "command"
    run = store.run(conn, it["review_run"])
    assert run["provider"] == "fake2" and run["row"] == "code" and run["fresh"] == 1   # never the builder's
    assert run["thread_id"] != it["thread_id"] and run["priority"] == "now"
    cwd = store.cwd_of(conn, run["thread_id"])
    assert cwd.endswith(f"judge-{it['id']}") and workspace.head(cwd) == it["commit_sha"]
    j = verdicts.judging(conn, run["id"])
    assert (j["goal_id"], j["item_id"], j["scope"], j["round"]) == (gid, it["id"], "item", 0)
    assert json.loads(j["checks"]) == ["spec", "c1"] and json.loads(j["avoid"]) == ["fake"]
    p = run["prompt"]
    assert "add a" in p and "c1: a() returns 1" in p and "c2:" not in p
    assert f"git diff {it['base'][:12]}..{it['commit_sha'][:12]}" in p
    assert "The builder's claim — not evidence:\n\nmade a, and it is perfect." in p
    assert "eki verdict '" in p and "eki slot --" in p and "JUDGED: <n>" in p and "REVIEW:" not in p
    assert not chores.latest(conn, "review", it["id"])                  # no review chore any more
    assert not review.ended(conn, it)
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec"), rec("c1"))
    assert it["state"] == "proposed" and it["review"] == "ok"
    assert "review ok" in selfview.said(it)
    assert not any(it["id"] in line for line in selfwork.tick(conn))     # moved once


def test_the_judge_ending_first_still_waits_for_gate_one(conn, src, tmp_path, monkeypatch):
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    filed(conn, tmp_path, monkeypatch, it["review_run"], rec("spec"), rec("c1"))
    selfwork.tick(conn)
    it = item(conn, it["id"])
    assert it["state"] == "judging" and review.ended(conn, it)
    it = gate(conn, it)
    assert it["state"] == "proposed" and it["review"] == "ok"


def test_a_failed_check_rebuilds_with_its_suggestion_and_a_second_no_waits_for_you(conn, src, tmp_path,
                                                                                   monkeypatch):
    settle(autonomy="apply")
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec"),
                rec("c1", "failed", suggestion="return 1 from a(), not 0"))
    assert it["state"] == "building"            # back to waiting, and started again in the same tick
    assert it["review"] == "no: c1: return 1 from a(), not 0" and it["reviews"] == 1 and it["tries"] == 2
    prompt = store.run(conn, it["run_id"])["prompt"]
    assert "A judge found check c1 failed: return 1 from a(), not 0" in prompt
    assert "failed its checks" not in prompt
    it = rebuild(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging" and it["review"] is None and it["review_run"]
    assert workspace.head(store.cwd_of(conn, store.run(conn, it["review_run"])["thread_id"])) == it["commit_sha"]
    it = judged(conn, tmp_path, monkeypatch, it, rec("spec", "failed", suggestion="add a test"), rec("c1"))
    assert it["state"] == "proposed" and it["review"] == "no: spec: add a test"
    queue.tick(conn)
    assert item(conn, it["id"])["state"] == "proposed"          # never queued by itself, even under apply
    assert "eki self apply" in selfview.said(it)
    group, what = digest.happened(it, "apply")
    assert group == "Waits for you" and "add a test" in what and f"eki self apply {it['id']}" in what


def test_a_missing_verdict_or_a_failed_judge_proposes_with_none(conn, src, tmp_path, monkeypatch):
    for f in ("a", "b", "c"):
        goal_with(conn, f"add {f}", [f"eki/{f}.py"])
    a, b, c = build(conn, tmp_path, monkeypatch, ("eki/a.py", "eki/b.py", "eki/c.py"))
    a = judged(conn, tmp_path, monkeypatch, a, rec("spec"))
    assert a["state"] == "proposed" and a["review"] == "none: the judge filed no verdict for c1"
    store.update_run(conn, b["review_run"], state="failed", error="the model fell over")
    b = gate(conn, b)
    assert b["state"] == "proposed" and b["review"] == "none: the judge run ended failed: the model fell over"
    selfwork.drop(conn, c["id"])
    assert store.run(conn, c["review_run"])["state"] == "cancelled"


def test_review_off_or_no_judge_lets_gate_one_alone_decide(conn, src, tmp_path, monkeypatch):
    settle(review=False)
    goal_with(conn, "add a", ["eki/a.py"])
    a = build(conn, tmp_path, monkeypatch)[0]
    assert a["review"] == "none: self.review is off" and a["review_run"] is None
    assert not conn.execute("SELECT 1 FROM judgings").fetchone()
    assert gate(conn, a)["state"] == "proposed"
    settle(review=True)
    rp = paths.config("routing")                 # only the builder's provider can judge: none
    rp.write_text(json.dumps({**json.loads(rp.read_text()), "rows": [
        {"key": "code", "title": "tools", "targets": ["fake"]}]}))
    goal_with(conn, "add b", ["eki/b.py"])
    b = build(conn, tmp_path, monkeypatch, ("eki/b.py",))[0]
    assert b["review_run"] is None and b["review"].startswith("none: no judge:")
    b = gate(conn, b)
    assert b["state"] == "proposed" and b["review"].startswith("none: no judge:")


@pytest.mark.usefixtures("text_fake")
def test_an_item_left_reviewing_by_an_older_eki_still_concludes(conn, src, tmp_path, monkeypatch):
    settle(local="bare")                   # the old review chore read with the local model
    goal_with(conn, "add a", ["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    store.cancel(conn, it["review_run"])
    it = gate(conn, it)
    rid = chores.start(conn, "review", it["id"], "read it", "now", role="reviewer")
    selfwork._set(conn, it["id"], state="reviewing", review_run=rid, review=None)
    says(tmp_path, monkeypatch, "REVIEW: ok\n")
    run_inline(conn, rid)
    selfwork.tick(conn)
    it = item(conn, it["id"])
    assert it["state"] == "proposed" and it["review"] == "ok"
    assert chores.latest(conn, "review", it["id"])["state"] == "done"


def test_objection_words_each_failed_check():
    def obj(review_text):
        return review.objection({"review": review_text})
    assert obj("no: c2: add the test\nspec: drop the stray file") == (
        "A judge found check c2 failed: add the test\nA judge found check spec failed: drop the stray file")
    assert obj("no: there is no test for a") == "there is no test for a"        # an older review's sentence
    assert obj("ok") is None and obj("uncertain: c1: no screen") is None and obj(None) is None


def test_verdict_reads_the_last_review_line():
    assert review.verdict("REVIEW: no a\nmore\n**REVIEW: ok**\n") == ("ok", "no reason given")
    assert review.verdict("`REVIEW: no — misses the test`") == ("no", "misses the test")
    assert review.verdict("the REVIEW: ok is inline") == (None, "")
