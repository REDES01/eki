"""The review: a second reader between gate 1 and the queue (eki/review.py)."""
import json

import pytest

from eki import chores, digest, paths, queue, review, selfview, selfwork, store
from conftest import run_inline
from test_self import src, says  # noqa: F401 — the source fixture and what the fake says


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


def build_and_judge(conn, tmp_path, monkeypatch):
    return judge(conn, build(conn, tmp_path, monkeypatch)[0])


def reviewed(conn, tmp_path, monkeypatch, it, answer):
    says(tmp_path, monkeypatch, answer)
    run_inline(conn, it["review_run"])
    selfwork.tick(conn)
    return item(conn, it["id"])


def test_ok_proposes_the_item(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = build_and_judge(conn, tmp_path, monkeypatch)
    assert it["state"] == "reviewing" and it["review_run"]
    run = store.run(conn, it["review_run"])
    assert run["provider"] == "bare" and run["row"] == "chore" and run["priority"] == "now"
    assert "add a" in run["prompt"] and "made a." in run["prompt"] and "+++ b/eki/a.py" in run["prompt"]
    assert "REVIEW: ok" in run["prompt"]
    assert selfview.where(it) == f"review {it['review_run']}"
    it = reviewed(conn, tmp_path, monkeypatch, it, "Looks right.\n\nREVIEW: ok\n")
    assert it["state"] == "proposed" and it["review"] == "ok"
    assert chores.latest(conn, "review", it["id"])["state"] == "done"
    assert "review ok" in selfview.said(it)
    monkeypatch.setattr(review, "DIFF_MAX", 50)
    assert "only the first 50 are shown" in review.prompt(conn, it)
    assert not any(it["id"] in line for line in selfwork.tick(conn))     # moved once


def test_a_first_no_rebuilds_with_the_objection_and_a_second_no_waits_for_you(conn, src, tmp_path,
                                                                               monkeypatch):
    settle(local="bare", autonomy="apply")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = build_and_judge(conn, tmp_path, monkeypatch)
    it = reviewed(conn, tmp_path, monkeypatch, it, "Hmm.\nREVIEW: no there is no test for a\n")
    assert it["state"] == "building"            # back to waiting, and started again in the same tick
    assert it["review"] == "no: there is no test for a" and it["reviews"] == 1
    prompt = store.run(conn, it["run_id"])["prompt"]
    assert "A second reader objected: there is no test for a. Answer it in the change" in prompt
    assert "failed its checks" not in prompt
    says(tmp_path, monkeypatch, "SUMMARY: made a.\n\nITEM: done\n")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = judge(conn, item(conn, it["id"]))
    assert it["state"] == "reviewing" and it["review"] is None
    it = reviewed(conn, tmp_path, monkeypatch, it, "REVIEW: no still no test\n")
    assert it["state"] == "proposed" and it["review"] == "no: still no test"
    queue.tick(conn)
    assert item(conn, it["id"])["state"] == "proposed"          # never queued by itself, even under apply
    assert "eki self apply" in selfview.said(it)
    group, what = digest.happened(it, "apply")
    assert group == "Waits for you" and "still no test" in what and f"eki self apply {it['id']}" in what
    queue.apply(conn, it["id"])                                 # the person's call still works
    assert item(conn, it["id"])["state"] == "queued"


def test_an_unreadable_or_failed_review_proposes_with_none_and_drop_cancels(conn, src, tmp_path,
                                                                            monkeypatch):
    settle(local="bare")
    for f in ("a", "b", "c"):
        selfwork.submit(conn, f"add {f}", plan=False, files=[f"eki/{f}.py"])
    a, b, c = [judge(conn, it) for it in build(conn, tmp_path, monkeypatch, ("eki/a.py", "eki/b.py", "eki/c.py"))]
    assert a["state"] == b["state"] == c["state"] == "reviewing"
    a = reviewed(conn, tmp_path, monkeypatch, a, "I read it and it seems fine I guess.\n")
    assert a["state"] == "proposed" and a["review"] == "none: the review has no REVIEW: line"
    assert chores.latest(conn, "review", a["id"])["state"] == "failed"
    store.update_run(conn, b["review_run"], state="failed", error="the model fell over")
    selfwork.tick(conn)
    b = item(conn, b["id"])
    assert b["state"] == "proposed" and b["review"].startswith("none: the review run ended failed")
    with pytest.raises(ValueError, match="reviewing"):
        selfwork.retry(conn, c["id"])
    selfwork.drop(conn, c["id"])
    assert store.run(conn, c["review_run"])["state"] == "cancelled"
    assert item(conn, c["id"])["state"] == "dropped"


def test_no_local_model_or_review_off_proposes_with_none(conn, src, tmp_path, monkeypatch):
    for f in ("a", "b"):
        selfwork.submit(conn, f"add {f}", plan=False, files=[f"eki/{f}.py"])
    a, b = build(conn, tmp_path, monkeypatch, ("eki/a.py", "eki/b.py"))
    a = judge(conn, a)
    assert a["state"] == "proposed" and a["review"] == "none: no local model"
    settle(local="bare", review=False)
    b = judge(conn, b)
    assert b["state"] == "proposed" and b["review"] == "none: self.review is off"


def test_the_review_rebuild_keeps_gate_ones_retry(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    selfwork.submit(conn, "add a", plan=False, files=["eki/a.py"])
    it = build_and_judge(conn, tmp_path, monkeypatch)
    it = reviewed(conn, tmp_path, monkeypatch, it, "REVIEW: no it's wrong\n")
    assert it["state"] == "building" and it["tries"] == 2
    monkeypatch.setenv("EKI_TEST_CHECK_EXIT", "1")
    says(tmp_path, monkeypatch, "SUMMARY: made a.\n\nITEM: done\n")
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = item(conn, it["id"])
    run_inline(conn, it["run_id"])                              # gate 1 fails
    selfwork.tick(conn)
    it = item(conn, it["id"])
    assert it["state"] == "building" and it["tries"] == 3       # its one retry, not unfit
    prompt = store.run(conn, it["run_id"])["prompt"]
    assert "failed its checks" in prompt and "A second reader objected: it's wrong" in prompt


def test_verdict_reads_the_last_review_line():
    assert review.verdict("REVIEW: no a\nmore\n**REVIEW: ok**\n") == ("ok", "no reason given")
    assert review.verdict("`REVIEW: no — misses the test`") == ("no", "misses the test")
    assert review.verdict("the REVIEW: ok is inline") == (None, "")
