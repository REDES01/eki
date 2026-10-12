"""The window's project pages: sections, the pictures filter, clone to your home, instructions."""
import json
import os
import threading
import urllib.parse
import urllib.request

import pytest

from eki import api_projects, asking, db, gallery, paths, projectchats, projects, server, store, workspace


@pytest.fixture
def src(tmp_path, monkeypatch):
    r = tmp_path / "src"
    r.mkdir()
    workspace.git(r, "init", "-q", "-b", "main")
    monkeypatch.setenv("EKI_SOURCE", str(r))
    return r


@pytest.fixture
def proj(conn, src):
    return projectchats.create(conn, "tokenbar")


def github_row(conn, repo="me/proj"):
    pid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO projects(id, name, path, branch, created_at, repo, state, setup) "
                     "VALUES (?,?,?,'',?,?,'on','ready')",
                     (pid, repo, str(projects.clone_path(repo)), db.now(), repo))
    return pid


def event(conn, rid, t, data, kind="tool"):
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES (?,?,?,?,?)",
                 (rid, 1, t, kind, json.dumps(data)))


def picture(conn, rid, t):
    pic = paths.home() / "images" / rid / f"eki_{int(t)}.png"
    pic.parent.mkdir(parents=True, exist_ok=True)
    pic.write_bytes(b"\x89PNG fake")
    event(conn, rid, t, {"name": "image", "detail": "Drew 1 picture", "path": str(pic)})
    return str(pic)


def test_list_has_eki_first_and_your_home(conn, proj):
    got = api_projects.projects_list(conn)
    assert [p["id"] for p in got["projects"]] == ["eki", proj]
    assert got["user_home"] == str(paths.user_home())


def test_sections_count_what_is_in_the_project(conn, proj, tmp_path):
    folder = projectchats.folder_of(conn, proj)
    t1, r1 = asking.submit(conn, "first", project=proj)
    t2, r2 = asking.submit(conn, "second", project=proj)
    asking.submit(conn, "outside")                                  # a chat in no project
    written = tmp_path / "user" / "tokenbar" / "notes.md"
    written.write_text("hi")
    gone = tmp_path / "user" / "tokenbar" / "gone.md"
    event(conn, r1, 10.0, {"name": "Write", "path": str(written)})
    event(conn, r2, 11.0, {"name": "Write", "path": str(written)})  # the same file twice: once
    event(conn, r2, 12.0, {"name": "Write", "path": str(gone)})     # not on disk: left out
    pic = picture(conn, r2, 13.0)
    gid = store.new_id()
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, state, project, created_at) VALUES (?,?,?,?,?)",
                     (gid, "make it fast", "planned", proj, db.now()))
        conn.execute("INSERT INTO items(id, goal_id, title, state, pr, pr_state, created_at, updated_at) "
                     "VALUES ('i1', ?, 'speed up', 'proposed', 'https://github.com/me/x/pull/1', 'open', ?, ?)",
                     (gid, db.now(), db.now()))
    got = api_projects.show(conn, proj)
    s = got["sections"]
    assert got["chats"] == 2 and [c["id"] for c in s["chats"]] == [t2, t1]
    assert s["files"] == [pic, str(written)]
    assert s["pictures"]["count"] == 1 and s["pictures"]["items"][0]["path"] == pic
    assert [(g["text"], g["state"]) for g in s["background"]["goals"]] == [("make it fast", "planned")]
    assert s["background"]["prs"] == [{"item": "i1", "title": "speed up",
                                       "url": "https://github.com/me/x/pull/1"}]
    assert got["header"] == (f"Chats work in {folder} — your real folder. "
                             f"Background: worktrees of {folder}; eki/<id> branches you merge")
    assert got["cloning"] is False


def test_header_says_none_without_background(conn, proj):
    assert api_projects.show(conn, proj)["header"].endswith("Background: none")


def test_unknown_project_is_not_found(conn, src):
    with pytest.raises(KeyError):
        api_projects.show(conn, "nope")


def test_pictures_filter_on_the_thread_project(conn, proj):
    _, inside = asking.submit(conn, "draw here", project=proj)
    _, outside = asking.submit(conn, "draw elsewhere")
    a = picture(conn, inside, 1.0)
    b = picture(conn, outside, 2.0)
    assert [p["path"] for p in gallery.pictures(conn, project=proj)] == [a]
    assert [p["path"] for p in gallery.pictures(conn)] == [b, a]


def test_clone_sets_the_folder_right_away(conn, src):
    pid = github_row(conn)
    got = api_projects.clone_home(conn, pid)
    dest = str(paths.user_home() / "proj")
    assert got["folder"] == dest
    assert conn.execute("SELECT folder FROM projects WHERE id=?", (pid,)).fetchone()[0] == dest
    t = store.thread(conn, got["thread"])
    assert t["title"] == f"clone me/proj to {dest}" and t["cwd"] == str(paths.user_home())
    r = store.run(conn, got["run"])
    assert r["provider"] == "command"
    assert json.loads(r["prompt"]) == ["gh", "repo", "clone", "me/proj", dest]
    assert api_projects.show(conn, pid)["cloning"] is True       # the run is queued, no .git yet
    (paths.user_home() / "proj" / ".git").mkdir(parents=True)
    assert api_projects.show(conn, pid)["cloning"] is False


def test_clone_refused_when_dest_exists(conn, src):
    pid = github_row(conn)
    (paths.user_home() / "proj").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        api_projects.clone_home(conn, pid)
    assert conn.execute("SELECT folder FROM projects WHERE id=?", (pid,)).fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_clone_refused_for_a_folder_project(conn, proj):
    with pytest.raises(ValueError, match="not on GitHub"):
        api_projects.clone_home(conn, proj)


def test_instructions_run_names_agents_md_and_the_pointer(conn, proj):
    got = api_projects.write_instructions(conn, proj)
    r = store.run(conn, got["run"])
    assert "AGENTS.md" in r["prompt"] and "`@AGENTS.md`" in r["prompt"] and "CLAUDE.md" in r["prompt"]
    t = store.thread(conn, got["thread"])
    assert t["project"] == proj and t["cwd"] == projectchats.folder_of(conn, proj)


def test_instructions_without_a_folder_say_why(conn, src):
    with pytest.raises(ValueError, match="has no folder yet"):
        api_projects.write_instructions(conn, github_row(conn))


def test_create_and_set_folder_take_tilde_as_your_home(conn, src, tmp_path):
    got = api_projects.create(conn, {"name": "notes", "folder": "~/notes"})
    assert got["folder"] == projectchats.folder_of(conn, got["id"]) == os.path.realpath(paths.user_home() / "notes")
    (paths.user_home() / "elsewhere").mkdir()
    moved = api_projects.set_folder(conn, got["id"], {"folder": "~/elsewhere"})
    assert moved["folder"] == projectchats.folder_of(conn, got["id"]) == os.path.realpath(paths.user_home() / "elsewhere")
    with pytest.raises(ValueError):
        api_projects.set_folder(conn, got["id"], {"folder": ""})


def test_routes(conn, src):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Eki": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        code, made = call("/api/projects", {"name": "web"})
        assert code == 200
        pid = made["id"]
        assert call("/api/projects")[1]["projects"][1]["id"] == pid
        assert call(f"/api/projects/{pid}")[1]["sections"]["chats"] == []
        assert call(f"/api/projects/{pid}/folder", {"folder": "/no/such/place"})[0] == 400
        assert call(f"/api/projects/{pid}/clone", {})[0] == 400
        code, got = call(f"/api/projects/{pid}/instructions", {})
        assert code == 200 and store.thread(db.connect(), got["thread"])["project"] == pid
        assert call("/api/projects/nope")[0] == 404
        assert made["folder"] == os.path.realpath(paths.user_home() / "Projects" / "web")
        assert made["existing"] is False
        assert call("/api/projects", {"name": "again", "folder": made["folder"]})[1] == {**made, "existing": True}
        code, got = call("/api/projectstart?name=next")
        assert code == 200 and got["folder"] == os.path.realpath(paths.user_home() / "Projects" / "next")
        assert got["recent"] == [made["folder"]] and got["project"] is None
        code, got = call("/api/projectstart?folder=" + urllib.parse.quote("~/Projects/web"))
        assert code == 200 and got["exists"] is True and got["project"] == pid
        assert call("/api/projectstart?name=a%2Fb")[0] == 400
        (paths.user_home() / "Projects" / "web" / "README.md").write_text("# web")
        code, got = call(f"/api/projects/{pid}/starter")
        assert code == 200 and [s["template"] for s in got["suggestions"]] == ["instructions"]
        assert call("/api/projects/nope/starter")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_delete_route(conn, src):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, method="GET", eki=True):
        req = urllib.request.Request(base + path, method=method, headers={"X-Eki": "1"} if eki else {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        pid = projectchats.create(conn, "web")
        assert call(f"/api/projects/{pid}", "DELETE", eki=False)[0] == 403
        code, got = call("/api/projects/eki", "DELETE")
        assert code == 400 and "eki can't be deleted" in got["error"]
        assert call("/api/projects/nope", "DELETE")[0] == 404
        code, got = call(f"/api/projects/{pid}", "DELETE")
        assert code == 200 and got["id"] == pid and got["name"] == "web"
        assert {"runs", "goals", "items", "standing", "asks", "chats", "removed", "folder"} <= set(got)
        assert pid not in [p["id"] for p in call("/api/projects")[1]["projects"]]
        assert call(f"/api/projects/{pid}", "DELETE")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
