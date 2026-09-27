"""First drafts of fault items' specs: the `brief` chore (eki/faults.py)."""
import json

from eki import chores, db, faults, paths, selfwork, store
from conftest import run_inline
from test_faults import fault, goals, items_of, src  # noqa: F401 — the source fixture
from test_self import says


def settle(**kw):
    rp = paths.config("routing")
    data = json.loads(rp.read_text())
    data["self"] = {**(data.get("self") or {}), **kw}
    rp.write_text(json.dumps(data))


def opened(conn, src):
    (src / "eki" / "x.py").write_text("".join(f"line {n}\n" for n in range(1, 201)))
    now = db.now()
    fault(conn, now - 3600)
    fault(conn, now - 60)
    faults.tick(conn, now)
    (g,) = goals(conn)
    (it,) = items_of(conn, g["id"])
    return g, it


def item(conn, iid):
    return selfwork.store_item(conn, iid)


def test_the_item_waits_while_the_brief_is_open(conn, src):
    settle(local="bare")
    g, it = opened(conn, src)
    c = chores.latest(conn, "brief", g["id"])
    assert c["state"] == "open"
    run = store.run(conn, c["run_id"])
    assert run["provider"] == "bare" and run["row"] == "chore" and run["priority"] == "background"
    assert "KeyError: 'a'" in run["prompt"] and "BRIEF:" in run["prompt"]
    assert "   12 line 12" in run["prompt"] and "   72 line 72" in run["prompt"]
    assert "   73 line 73" not in run["prompt"] and "    1 line 1\n" in run["prompt"]
    selfwork.tick(conn)
    assert item(conn, it["id"])["state"] == "waiting"


def test_a_done_brief_gives_the_draft_and_the_verbatim_traceback(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    g, it = opened(conn, src)
    template = it["spec"]
    says(tmp_path, monkeypatch, "Thinking.\n\nBRIEF:\nThe dict has no 'a'. Look at go() in eki/x.py.\n"
                                "The test should call go() with an empty dict.\n")
    run_inline(conn, chores.latest(conn, "brief", g["id"])["run_id"])
    said = faults.tick(conn)
    assert any("spec drafted" in s for s in said)
    spec = item(conn, it["id"])["spec"]
    assert spec.startswith("The dict has no 'a'. Look at go() in eki/x.py.")
    assert spec.endswith(template[template.index("The latest traceback:"):])
    assert chores.latest(conn, "brief", g["id"])["state"] == "done"
    assert not any("spec drafted" in s for s in faults.tick(conn))      # once
    selfwork.tick(conn)
    it = item(conn, it["id"])
    assert it["state"] == "building" and "The dict has no 'a'" in store.run(conn, it["run_id"])["prompt"]


def test_a_failed_brief_keeps_the_template(conn, src):
    settle(local="bare")
    g, it = opened(conn, src)
    rid = chores.latest(conn, "brief", g["id"])["run_id"]
    conn.execute("UPDATE runs SET prompt='fail' WHERE id=?", (rid,))
    assert run_inline(conn, rid)["state"] == "failed"
    faults.tick(conn)
    assert chores.latest(conn, "brief", g["id"])["state"] == "failed"
    assert item(conn, it["id"])["spec"] == it["spec"]
    selfwork.tick(conn)
    assert item(conn, it["id"])["state"] == "building"


def test_an_answer_without_brief_keeps_the_template(conn, src, tmp_path, monkeypatch):
    settle(local="bare")
    g, it = opened(conn, src)
    says(tmp_path, monkeypatch, "I am not sure.\n")
    run_inline(conn, chores.latest(conn, "brief", g["id"])["run_id"])
    faults.tick(conn)
    assert chores.latest(conn, "brief", g["id"])["state"] == "failed"
    assert item(conn, it["id"])["spec"] == it["spec"]


def test_after_brief_wait_the_item_starts_on_the_template(conn, src):
    settle(local="bare", brief_wait=1)
    g, it = opened(conn, src)
    conn.execute("UPDATE chores SET created_at=created_at-61 WHERE subject=?", (g["id"],))
    selfwork.tick(conn)
    it2 = item(conn, it["id"])
    assert it2["state"] == "building" and it2["spec"] == it["spec"]


def test_no_local_model_means_no_wait(conn, src):
    g, it = opened(conn, src)
    assert chores.latest(conn, "brief", g["id"])["state"] == "skipped"
    selfwork.tick(conn)
    assert item(conn, it["id"])["state"] == "building"


def test_draft_reads_the_last_brief_block():
    assert faults.draft("x\nBRIEF: one\ntwo\n") == "one\ntwo"
    assert faults.draft("**BRIEF:**\nfirst\nBRIEF:\nsecond") == "second"
    assert faults.draft("no block") is None and faults.draft("BRIEF:\n  ") is None
