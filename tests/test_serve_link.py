"""The serving link reaches the agent: EKI_SERVE_URL, one brief line, the playtester plays the caller's link."""
import json
from pathlib import Path

import pytest

from eki import db, store, worker, workspace
from eki.components import agent, serve, servers
from eki.providers.base import Turn
from eki.providers.fake import Fake

TEMPLATE_URL = "http://127.0.0.1:4200/chapter1/"


@pytest.fixture
def game(proj, home):
    """`proj` with an index in git and a serve card serving it under /chapter1/."""
    (proj / "index.html").write_text("<h1>trunk</h1>\n")
    workspace.git(proj, "add", "index.html")
    workspace.git(proj, "commit", "-q", "-m", "web")
    card(proj, "web", {"kind": "serve", "about": "the game", "path": "/chapter1/"})
    yield proj
    for st in servers.states():                   # never leave a real server behind
        servers.stop_pid(st.get("pid"), Path(st["state"]), st.get("name", ""))


def card(proj, name, spec, prompt=None):
    d = proj / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps(spec))
    if prompt is not None:
        (d / "prompt.md").write_text(prompt)


def run_in(conn, cwd, prompt="look at the game", **kw):
    with db.tx(conn):
        tid = store.create_thread(conn, prompt, str(cwd))
        rid = store.create_run(conn, tid, prompt, **kw)
    return store.run(conn, rid)


def test_a_run_in_a_worktree_gets_its_own_link(game, conn):
    wt = workspace.add(game, "wa")
    r = run_in(conn, wt)
    turn = worker.build_turn(conn, r, "fake")
    url = turn.extra["env"]["EKI_SERVE_URL"]
    assert url.startswith("http://127.0.0.1:") and url.endswith("/chapter1/")
    assert url in turn.extra["note"] and "EKI_SERVE_URL" in turn.extra["note"] and ":4200" in turn.extra["note"]
    import urllib.request
    with urllib.request.urlopen(url, timeout=5) as resp:          # the real server, serving the worktree
        assert resp.read() == b"<h1>trunk</h1>\n"


def test_a_run_without_a_serve_card_gets_neither(proj, conn):
    r = run_in(conn, proj)
    turn = worker.build_turn(conn, r, "fake")
    assert "EKI_SERVE_URL" not in (turn.extra.get("env") or {})
    assert "served at" not in (turn.extra.get("note") or "")


def test_a_failing_link_never_fails_the_run(game, conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(serve, "for_run", boom)
    turn = worker.build_turn(conn, run_in(conn, game), "fake")
    assert "env" not in turn.extra


def test_a_chore_gets_no_link(game, conn, monkeypatch):
    monkeypatch.setattr(serve, "for_run", lambda *a, **k: pytest.fail("a chore asked for a link"))
    turn = worker.build_turn(conn, run_in(conn, game, row="chore"), "fake")
    assert "env" not in turn.extra


def test_program_env_carries_the_turns_env(home):
    turn = Turn(prompt="x", history=[], run_id="r1", thread_id="t1",
                extra={"env": {"EKI_SERVE_URL": "http://127.0.0.1:47213/chapter1/", "BAD": 3}})
    env = Fake("fake", {"kind": "fake"}).env(turn)
    assert env["EKI_SERVE_URL"] == "http://127.0.0.1:47213/chapter1/" and "BAD" not in env
    assert env["EKI_RUN"] == "r1" and env["PYTHONPATH"]
    assert "EKI_SERVE_URL" not in Fake("fake", {"kind": "fake"}).env(Turn(prompt="x", history=[]))


def playtester_in(proj):
    card(proj, "playtester", {"kind": "agent", "about": "plays", "bare": True, "url": TEMPLATE_URL},
         prompt="Play the game at {url}.")
    from eki import components
    return components.get("playtester", proj)


def call_from(folder):
    return agent.Call(args=[], caller_dir=str(folder), caller_run="r0", run_id="r1",
                      emit=lambda k, d: None, extra={})


def test_the_playtester_plays_the_callers_link(game, home, monkeypatch):
    wt = workspace.add(game, "wa")
    seen = []
    monkeypatch.setattr(serve, "link_for", lambda folder, conn=None, by="": (seen.append((folder, by))
                        or "http://127.0.0.1:47213/chapter1/", ""))
    s = home / "s"
    s.mkdir()
    _, turn = agent.turn_for(playtester_in(game), call_from(wt), s)
    assert turn.extra["system_prompt"] == "Play the game at http://127.0.0.1:47213/chapter1/."
    assert seen == [(str(wt), "r0")]


def test_the_playtester_falls_back_to_its_own_url(proj, home):
    s = home / "s"
    s.mkdir()
    comp = playtester_in(proj)
    _, turn = agent.turn_for(comp, call_from(proj), s)
    assert turn.extra["system_prompt"] == f"Play the game at {TEMPLATE_URL}."
    assert agent.prompt_text(comp) == f"Play the game at {TEMPLATE_URL}."


def test_the_journal_keeps_the_callers_link(game, home, monkeypatch):
    link = "http://127.0.0.1:47213/chapter1/"
    monkeypatch.setattr(serve, "link_for", lambda *a, **k: (link, ""))

    class Done:
        def take(self, turn, emit):
            from eki.providers.base import Outcome
            emit("text", {"text": "REVIEW: fine"})
            return Outcome(state="done")
    from eki import providers
    monkeypatch.setattr(providers, "get", lambda name: Done())
    monkeypatch.setattr(agent, "turn_for", lambda comp, c, session, url=None: ("claude", Turn(prompt="", history=[])))
    res = agent.call(playtester_in(game), call_from(game))
    kept = json.loads((Path(res.paths[0]) / "session.json").read_text())
    assert kept["url"] == link


def test_open_names_the_page_a_run_opens(proj, home, conn):
    """A serve card's `open` page is what EKI_SERVE_URL and the playtester point at (the game's slice)."""
    (proj / "slice").mkdir()
    (proj / "slice" / "index.html").write_text("<h1>slice</h1>\n")
    workspace.git(proj, "add", "slice")
    workspace.git(proj, "commit", "-q", "-m", "slice")
    card(proj, "web", {"kind": "serve", "about": "the game", "path": "/chapter1/", "open": "slice/index.html"})
    try:
        wt = workspace.add(proj, "wo")
        url = serve.for_run(str(wt), conn)
        assert url.endswith("/chapter1/slice/index.html")
        import urllib.request
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.read() == b"<h1>slice</h1>\n"
    finally:
        for st in servers.states():
            servers.stop_pid(st.get("pid"), Path(st["state"]), st.get("name", ""))


def test_open_must_stay_inside_the_folder(tmp_path):
    for bad in ("/etc/passwd", "../x.html", "http://x/y"):
        assert any('"open"' in e for e in serve.validate({"kind": "serve", "about": "x", "open": bad}, tmp_path))
    assert not any('"open"' in e for e in serve.validate({"kind": "serve", "about": "x", "open": "slice/index.html"},
                                                         tmp_path))
