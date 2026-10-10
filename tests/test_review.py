"""The review: a second reader between gate 1 and the queue (eki/review.py)."""
import json

import pytest

from eki import chores, digest, paths, queue, review, selfview, selfwork, store
from conftest import run_inline
from test_self import src, says  # noqa: F401 — the source fixture and what the fake says

#: the fake stands in for the local model on chores
pytestmark = pytest.mark.usefixtures("text_fake")


def settle(**kw):
    rp = paths.config("routing")
    data = json.loads(rp.read_text())
    data["self"] = {**(data.get("self") or {}), **kw}
    rp.write_text(json.dumps(data))


def item(conn, iid):
    return selfwork.store_item(conn, iid)


def build(conn, tmp_path, monkeypatch, files=("eki/a.py",)):
    """Build the items that are waiting (disjoint files start together); returns them judging."""
    selfwork.tick(conn)
    items = selfwork.items_in(conn, ("building",))
    says(tmp_path, monkeypatch, "SUMMARY: made a.\n\nITEM: done\n")
    for it, f in zip(items, files):
        monkeypatch.setenv("EKI_FAKE_TOUCH", f)
        run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    items = [item(conn, it["id"]) for it in items]
    assert all(it["state"] == "judging" for it in items)
    return items


def judge(conn, it):
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    return item(conn, it["id"])


def reviewed(conn, tmp_path, monkeypatch, it, answer):
    says(tmp_path, monkeypatch, answer)
    run_inline(conn, it["review_run"])
    selfwork.tick(conn)
    return item(conn, it["id"])


def both(conn, tmp_path, monkeypatch, it, answer):
    """Gate 1 ends first, then the review: the item moves only on the second."""
    it = judge(conn, it)
    assert it["state"] == "judging"
    return reviewed(conn, tmp_path, monkeypatch, it, answer)


def rebuild(conn, tmp_path, monkeypatch, it):
    assert it["state"] == "building"
    says(tmp_path, monkeypatch, "SUMMARY: made a.\n\nITEM: done\n")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    return item(conn, it["id"])


def test_gate_one_and_the_review_start_together_and_ok_proposes(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]                  # one tick: both runs
    assert it["state"] == "judging" and it["run_id"] and it["review_run"] and it["review"] is None
    assert store.run(conn, it["run_id"])["provider"] == "command"
    run = store.run(conn, it["review_run"])
    assert run["provider"] == "bare" and run["row"] == "chore" and run["priority"] == "now"
    assert "add a" in run["prompt"] and "made a." in run["prompt"] and "+++ b/eki/a.py" in run["prompt"]
    assert "REVIEW: ok" in run["prompt"] and "running beside you" in run["prompt"]
    assert "checks passed" not in run["prompt"]
    assert selfview.where(it) == f"gate 1 run {it['run_id']} · review run {it['review_run']}"
    assert "side by side" in selfview.said(it)
    assert not review.ended(conn, it)
    it = both(conn, tmp_path, monkeypatch, it, "Looks right.\n\nREVIEW: ok\n")
    assert it["state"] == "proposed" and it["review"] == "ok"
    assert chores.latest(conn, "review", it["id"])["state"] == "done"
    assert "review ok" in selfview.said(it)
    monkeypatch.setattr(review, "DIFF_MAX", 50)
    assert "only the first 50 are shown" in review.prompt(conn, it)
    assert not any(it["id"] in line for line in selfwork.tick(conn))     # moved once


def test_the_review_ending_first_still_waits_for_gate_one(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    it = reviewed(conn, tmp_path, monkeypatch, it, "REVIEW: ok\n")
    assert it["state"] == "judging" and review.ended(conn, it)
    assert chores.latest(conn, "review", it["id"])["state"] == "open"    # read only once both ended
    it = judge(conn, it)
    assert it["state"] == "proposed" and it["review"] == "ok"
    selfwork.tick(conn)
    assert item(conn, it["id"])["updated_at"] == it["updated_at"]         # ticked twice, moved once


def test_a_first_no_rebuilds_with_the_objection_and_a_second_no_waits_for_you(conn, src, tmp_path,
                                                                               monkeypatch):
    settle(local="bare", autonomy="apply")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    it = both(conn, tmp_path, monkeypatch, it, "Hmm.\nREVIEW: no there is no test for a\n")
    assert it["state"] == "building"            # back to waiting, and started again in the same tick
    assert it["review"] == "no: there is no test for a" and it["reviews"] == 1 and it["tries"] == 2
    prompt = store.run(conn, it["run_id"])["prompt"]
    assert "A second reader objected: there is no test for a. Answer it in the change" in prompt
    assert "failed its checks" not in prompt
    it = rebuild(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging" and it["review"] is None and it["review_run"]
    it = both(conn, tmp_path, monkeypatch, it, "REVIEW: no still no test\n")
    assert it["state"] == "proposed" and it["review"] == "no: still no test"
    queue.tick(conn)
    assert item(conn, it["id"])["state"] == "proposed"          # never queued by itself, even under apply
    assert "eki self apply" in selfview.said(it)
    group, what = digest.happened(it, "apply")
    assert group == "Waits for you" and "still no test" in what and f"eki self apply {it['id']}" in what
    queue.apply(conn, it["id"])                                 # the person's call still works
    assert item(conn, it["id"])["state"] == "queued"


def test_red_gate_one_retries_once_whatever_the_review_says(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    for f in ("a", "b"):
        selfwork.submit(conn, f"add {f}", plan=False, files=[f"eki/{f}.py"])
    a, b = build(conn, tmp_path, monkeypatch, ("eki/a.py", "eki/b.py"))
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    a = both(conn, tmp_path, monkeypatch, a, "REVIEW: ok\n")
    assert a["state"] == "building" and a["review"] == "ok" and a["tries"] == 2 and a["reviews"] == 0
    assert "failed its checks" in store.run(conn, a["run_id"])["prompt"]
    a = rebuild(conn, tmp_path, monkeypatch, a)
    a = both(conn, tmp_path, monkeypatch, a, "REVIEW: ok\n")
    assert a["state"] == "unfit" and a["review"] == "ok"
    assert chores.latest(conn, "review", a["id"])["state"] == "done"
    b = both(conn, tmp_path, monkeypatch, b, "REVIEW: no it misses a case\n")
    assert b["state"] == "building" and b["reviews"] == 0 and b["tries"] == 2    # gate 1's retry
    assert b["review"] == "no: it misses a case"
    prompt = store.run(conn, b["run_id"])["prompt"]
    assert "failed its checks" in prompt and "A second reader objected: it misses a case" in prompt


def test_an_unreadable_or_failed_review_proposes_with_none_and_drop_cancels(conn, src, tmp_path,
                                                                            monkeypatch):
    settle(local="bare")
    for f in ("a", "b", "c"):
        selfwork.submit(conn, f"add {f}", plan=False, files=[f"eki/{f}.py"])
    a, b, c = build(conn, tmp_path, monkeypatch, ("eki/a.py", "eki/b.py", "eki/c.py"))
    a = both(conn, tmp_path, monkeypatch, a, "I read it and it seems fine I guess.\n")
    assert a["state"] == "proposed" and a["review"] == "none: the review has no REVIEW: line"
    assert chores.latest(conn, "review", a["id"])["state"] == "failed"
    store.update_run(conn, b["review_run"], state="failed", error="the model fell over")
    b = judge(conn, b)
    assert b["state"] == "proposed" and b["review"].startswith("none: the review run ended failed")
    with pytest.raises(ValueError, match="judging"):
        selfwork.retry(conn, c["id"])
    selfwork.drop(conn, c["id"])
    assert store.run(conn, c["review_run"])["state"] == "cancelled"
    assert store.run(conn, c["run_id"])["state"] == "cancelled"
    assert item(conn, c["id"])["state"] == "dropped"


def test_no_local_model_or_review_off_lets_gate_one_alone_decide(conn, src, tmp_path, monkeypatch):
    for f in ("a", "b"):
        selfwork.submit(conn, f"add {f}", plan=False, files=[f"eki/{f}.py"])
    a, b = build(conn, tmp_path, monkeypatch, ("eki/a.py", "eki/b.py"))
    assert a["review"] == "none: no local model" and a["review_run"] is None
    assert selfview.where(a) == f"gate 1 run {a['run_id']}"
    a = judge(conn, a)
    assert a["state"] == "proposed" and a["review"] == "none: no local model"
    settle(local="bare", review=False)
    selfwork.submit(conn, "add c", plan=False, files=["eki/c.py"])
    c = build(conn, tmp_path, monkeypatch, ("eki/c.py",))[0]
    assert c["review"] == "none: self.review is off" and c["review_run"] is None
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    c = judge(conn, c)
    assert c["state"] == "building" and c["review"] == "none: self.review is off"
    monkeypatch.delenv("EKI_TEST_CHECK_EXIT")
    b = judge(conn, b)
    assert b["state"] == "proposed" and b["review"] == "none: no local model"


def test_an_item_left_reviewing_by_an_older_eki_still_concludes(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = judge(conn, build(conn, tmp_path, monkeypatch)[0])
    selfwork._set(conn, it["id"], state="reviewing")
    it = item(conn, it["id"])
    assert selfview.where(it) == f"review {it['review_run']}"
    it = reviewed(conn, tmp_path, monkeypatch, it, "REVIEW: ok\n")
    assert it["state"] == "proposed" and it["review"] == "ok"
    assert chores.latest(conn, "review", it["id"])["state"] == "done"
    assert not any(it["id"] in line for line in selfwork.tick(conn))


def test_a_project_item_with_a_check_is_reviewed_beside_it(conn, src, tmp_path, monkeypatch):
    from eki import projects, workspace
    settle(local="bare")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "app.py").write_text("print('hi')\n")
    workspace.git(proj, "init", "-q", "-b", "trunk")
    workspace.git(proj, "add", "-A")
    workspace.git(proj, "commit", "-q", "-m", "first")
    pid = projects.add(conn, proj, check="true")
    selfwork.submit(conn, "add b", plan=False, files=["b.py"], project=pid)
    it = build(conn, tmp_path, monkeypatch, ("b.py",))[0]
    assert it["review_run"] and json.loads(store.run(conn, it["run_id"])["prompt"])[2] == "true"
    assert "+++ b/b.py" in store.run(conn, it["review_run"])["prompt"]
    it = both(conn, tmp_path, monkeypatch, it, "REVIEW: ok\n")
    assert it["state"] == "proposed" and it["review"] == "ok"


def set_roles(**want):
    rp = paths.config("routing")
    rp.write_text(json.dumps({**json.loads(rp.read_text()), "roles": want}))


def test_the_review_runs_on_the_reviewer_role(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    set_roles(reviewer="fake2:some-model", chores="fake")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = build(conn, tmp_path, monkeypatch)[0]
    run = store.run(conn, it["review_run"])
    assert (run["provider"], run["model"], run["row"]) == ("fake2", "some-model", "chore")
    assert run["why"] == "reviewer → fake2:some-model"
    assert both(conn, tmp_path, monkeypatch, it, "REVIEW: ok\n")["state"] == "proposed"


def test_a_reviewer_that_cant_run_falls_back_to_local_or_proposes_with_why(conn, src, tmp_path, monkeypatch):
    from eki import capacity, providers
    settle(local="bare")
    set_roles(reviewer="fake2")
    down = {"fake2": (False, "limit reached")}
    monkeypatch.setattr(capacity, "status", lambda conn, background=False: {
        **{n: (True, "") for n in providers.config()}, **down})
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    a = build(conn, tmp_path, monkeypatch, ("eki/a.py",))[0]
    run = store.run(conn, a["review_run"])
    assert run["provider"] == "bare" and run["why"] == "reviewer fake2 can't run (fake2: limit reached) → bare"
    down["bare"] = (False, "not running")
    selfwork.submit(conn, "add b", plan=False, files=["eki/b.py"])
    b = build(conn, tmp_path, monkeypatch, ("eki/b.py",))[0]
    assert b["review_run"] is None
    assert b["review"].startswith("none: reviewer fake2 can't run (fake2: limit reached) → bare")
    b = judge(conn, b)
    assert b["state"] == "proposed" and b["review"].startswith("none: reviewer fake2 can't run")
    set_roles(reviewer="off")
    selfwork.submit(conn, "add c", plan=False, files=["eki/c.py"])
    c = judge(conn, build(conn, tmp_path, monkeypatch, ("eki/c.py",))[0])
    assert c["state"] == "proposed" and c["review"] == "none: the reviewer role is off"


def test_the_review_rebuild_keeps_gate_ones_retry(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = both(conn, tmp_path, monkeypatch, build(conn, tmp_path, monkeypatch)[0], "REVIEW: no it's wrong\n")
    assert it["state"] == "building" and it["tries"] == 2
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    it = rebuild(conn, tmp_path, monkeypatch, it)
    it = both(conn, tmp_path, monkeypatch, it, "REVIEW: ok\n")             # gate 1 fails
    assert it["state"] == "building" and it["tries"] == 3       # its one retry, not unfit
    prompt = store.run(conn, it["run_id"])["prompt"]
    assert "failed its checks" in prompt


def test_verdict_reads_the_last_review_line():
    assert review.verdict("REVIEW: no a\nmore\n**REVIEW: ok**\n") == ("ok", "no reason given")
    assert review.verdict("`REVIEW: no — misses the test`") == ("no", "misses the test")
    assert review.verdict("the REVIEW: ok is inline") == (None, "")
