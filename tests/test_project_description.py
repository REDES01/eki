"""A project's description, its last activity, AGENTS.md from it, and folder completion."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from eki import api_projects, asking, db, paths, projectchats, workspace
from eki.cli import main


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def srv(home, src):
    from eki import server
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % s.server_address[1]
    s.shutdown()


def call(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Eki": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def listed(conn, pid):
    return next(e for e in projectchats.listing(conn) if e["id"] == pid)


def test_create_stores_the_description_and_listing_shows_it(conn, src):
    pid = projectchats.create(conn, "x", description="  Ship it \n")
    assert listed(conn, pid)["description"] == "Ship it"
    blank = projectchats.create(conn, "y", description="  ")
    assert conn.execute("SELECT description FROM projects WHERE id=?", (blank,)).fetchone()[0] is None
    assert listed(conn, "eki")["description"] == "eki itself — its own source"


def test_last_active_moves_when_a_chat_runs(conn, src):
    pid = projectchats.create(conn, "x")
    made = listed(conn, pid)["last_active"]
    assert made == conn.execute("SELECT created_at FROM projects WHERE id=?", (pid,)).fetchone()[0]
    tid, rid = asking.submit(conn, "hello", project=pid)
    with db.tx(conn):
        conn.execute("UPDATE runs SET created_at=? WHERE id=?", (made + 100, rid))
    assert listed(conn, pid)["last_active"] == made + 100
    assert listed(conn, "eki")["last_active"] is None or listed(conn, "eki")["last_active"] > 0


def test_set_description_changes_clears_and_refuses_eki(conn, src):
    pid = projectchats.create(conn, "x")
    assert projectchats.set_description(conn, "x", " New aim ") == "New aim"
    assert listed(conn, pid)["description"] == "New aim"
    assert projectchats.set_description(conn, pid, "") is None
    with pytest.raises(ValueError):
        projectchats.set_description(conn, "eki", "anything")
    with pytest.raises(KeyError):
        projectchats.set_description(conn, "nope", "anything")


def test_create_writes_nothing_but_git_and_its_ignore_file(conn, src):
    pid = projectchats.create(conn, "fresh", description="Ship it")
    assert sorted(os.listdir(projectchats.folder_of(conn, pid))) == [".git", ".gitignore"]


def test_the_instructions_prompt_leads_with_the_description(conn, src):
    pid = projectchats.create(conn, "tokenbar", description="Count tokens.\nShow them in the bar.")
    prompt = api_projects.instructions_prompt(conn, pid)
    first = prompt.index("# tokenbar")
    assert prompt.index("Count tokens.\nShow them in the bar.") > first
    assert prompt.endswith(api_projects.INSTRUCTIONS_PROMPT)
    assert prompt.index("first section") < first
    plain = projectchats.create(conn, "other")
    assert api_projects.instructions_prompt(conn, plain) == api_projects.INSTRUCTIONS_PROMPT
    got = api_projects.write_instructions(conn, pid)
    text = conn.execute("SELECT prompt FROM runs WHERE id=?", (got["run"],)).fetchone()[0]
    assert text == prompt
    assert sorted(os.listdir(projectchats.folder_of(conn, pid))) == [".git", ".gitignore"]


def test_post_create_and_describe_round_trip(srv):
    code, got = call(srv + "/api/projects", {"name": "web", "description": "Ship it"})
    assert code == 200
    pid = got["id"]
    _, lst = call(srv + "/api/projects")
    entry = next(p for p in lst["projects"] if p["id"] == pid)
    assert entry["description"] == "Ship it" and entry["last_active"]
    code, got = call(srv + f"/api/projects/{pid}/describe", {"description": "Ship it twice"})
    assert code == 200 and got["description"] == "Ship it twice"
    assert call(srv + "/api/projects/eki/describe", {"description": "x"})[0] == 400
    assert call(srv + "/api/projects/nope/describe", {"description": "x"})[0] == 404


def test_folders_complete_under_your_home_only(srv, tmp_path):
    user = paths.user_home()
    for d in ("apple", "avocado", ".amber", "banana", "apple/core"):
        (user / d).mkdir(parents=True, exist_ok=True)
    (user / "afile").write_text("x")
    code, got = call(srv + "/api/folders?prefix=~/a")
    assert code == 200 and got == {"folders": ["~/apple", "~/avocado"]}
    assert call(srv + "/api/folders?prefix=~/.a")[1] == {"folders": ["~/.amber"]}
    assert call(srv + "/api/folders?prefix=~/apple/")[1] == {"folders": ["~/apple/core"]}
    assert call(srv + f"/api/folders?prefix={user}/b")[1] == {"folders": [f"{user}/banana"]}
    outside = tmp_path / "elsewhere"
    (outside / "aa").mkdir(parents=True)
    assert call(srv + f"/api/folders?prefix={outside}/a")[1] == {"folders": []}
    assert call(srv + "/api/folders?prefix=~/../")[1] == {"folders": []}


def test_cli_new_with_description_and_describe(conn, src, capsys):
    assert main(["project", "--new", "cli", "--description", "Ship it"]) == 0
    pid = projectchats.find(conn, "cli")
    assert listed(conn, pid)["description"] == "Ship it"
    assert main(["project", "describe", "cli", "Something else"]) == 0
    assert listed(conn, pid)["description"] == "Something else"
    assert main(["project", "describe", "eki", "x"]) == 1
    assert "fixed" in capsys.readouterr().err
