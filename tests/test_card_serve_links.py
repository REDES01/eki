"""A Preview links (serve) card's live part: each running copy with the task it belongs to, when it started
and when it stops if idle, and the person's own copy — never starting a server or allotting a port."""
import json
import os
import time

import pytest

from eki import components
from eki.components import ports, serve, servelive, servers


@pytest.fixture
def card(proj):
    d = proj / "components" / "web"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "serve", "about": "the game", "path": "/chapter1/",
                                                  "idle_stop": 30}))
    return components.get("web", proj)


@pytest.fixture
def up(monkeypatch):
    """Folders whose server answers now; every other one is down."""
    answering = set()
    monkeypatch.setattr(serve, "answers", lambda port, folder: ports.norm(folder) in answering)
    return answering


def thread(conn, tid, title, cwd=""):
    conn.execute("INSERT INTO threads(id, title, cwd, created_at) VALUES (?,?,?,?)", (tid, title, cwd, 0))


def run(conn, rid, tid, prompt="", state="done", created=0.0, ended=None):
    conn.execute("INSERT INTO runs(id, thread_id, seq, prompt, state, created_at, ended_at) VALUES (?,?,?,?,?,?,?)",
                 (rid, tid, 1, prompt, state, created, ended))


def served(comp, folder, **st):
    folder = ports.norm(folder)
    ports.allot(folder)
    servers.write(comp, folder, pid=os.getpid(), **st)
    return folder


def test_two_copies_with_their_tasks_started_and_stop(conn, card, proj, tmp_path, up):
    a, b = tmp_path / "wa", tmp_path / "wb"
    a.mkdir(), b.mkdir()
    now = time.time()
    thread(conn, "t1", "Convert dialogue to the new format")
    run(conn, "r1", "t1", state="done", ended=now - 120)
    thread(conn, "t2", "", cwd=ports.norm(b))
    run(conn, "r2", "t2", prompt="Fix the river scene\nmore words", state="running", created=now - 50)
    fa = served(card, a, started_at=now - 600, last_run="r1", by="r1")
    fb = served(card, b, started_at=now - 60)
    up.update({fa, fb})
    conn.commit()
    got = serve.live(card, conn, proj)
    by = {x["folder"]: x for x in got["links"]}
    assert set(by) == {fa, fb}
    assert set(by[fa]) == {"folder", "url", "task", "run", "started", "stops_at"}
    assert by[fa]["task"] == "Convert dialogue to the new format" and by[fa]["run"] == "r1"
    assert by[fa]["started"] == pytest.approx(now - 600)
    assert by[fa]["stops_at"] == pytest.approx(now - 120 + 30 * 60)           # the run's end + idle_stop
    assert by[fa]["url"] == f"http://127.0.0.1:{ports.get(fa)}/chapter1/"
    assert by[fb]["task"] == "Fix the river scene" and by[fb]["run"] == "r2"  # the newest run in that folder
    assert by[fb]["stops_at"] == pytest.approx(now - 60 + 30 * 60)


def test_an_items_worktree_names_its_task_and_idle_stop_zero_never_stops(conn, card, proj, tmp_path, up):
    a = tmp_path / "wa"
    a.mkdir()
    conn.execute("INSERT INTO items(id, goal_id, title, worktree, run_id, created_at, updated_at) "
                 "VALUES ('i1','g1','Preview links card',?,'rx',0,0)", (ports.norm(a),))
    conn.commit()
    card.spec["idle_stop"] = 0
    up.add(served(card, a, started_at=time.time()))
    (link,) = serve.live(card, conn, proj)["links"]
    assert link["task"] == "Preview links card" and link["run"] == "rx" and link["stops_at"] is None
    del card.spec["idle_stop"]                                                # absent: no stop time either
    assert serve.live(card, conn, proj)["links"][0]["stops_at"] is None


def test_a_gone_server_is_left_out_and_unknown_task_is_empty(conn, card, proj, tmp_path, up):
    a, b = tmp_path / "wa", tmp_path / "wb"
    a.mkdir(), b.mkdir()
    served(card, a, started_at=time.time())                                   # recorded, but doesn't answer
    up.add(served(card, b, started_at=time.time()))
    (link,) = serve.live(card, conn, proj)["links"]
    assert link["folder"] == ports.norm(b) and link["task"] == "" and link["run"] == ""


def test_own_copy_uses_the_roots_port_and_allots_none(conn, card, proj, up):
    got = serve.live(card, conn, proj)
    assert got == {"links": [], "own": {"folder": ports.norm(proj), "url": "", "up": False}}
    assert ports.get(proj) is None                                            # nothing allotted to show it
    port = ports.allot(proj)
    assert serve.live(card, conn, proj)["own"] == {"folder": ports.norm(proj),
                                                    "url": f"http://127.0.0.1:{port}/chapter1/", "up": False}
    up.add(served(card, proj, started_at=time.time()))
    got = serve.live(card, conn, proj)
    assert got["own"]["up"] is True and got["links"] == []                    # your copy isn't a preview link


def test_live_never_raises(conn, card, proj, monkeypatch):
    def boom(comp):
        raise RuntimeError("no")
    monkeypatch.setattr(serve, "links", boom)
    assert servelive.live(card, conn, proj)["links"] == []
