from eki import projects, selfwork, store, workspace
from test_self import src  # noqa: F401 — the source fixture


def goal(conn, gid):
    return conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()


def project(conn, tmp_path, check):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "app.py").write_text("print('hi')\n")
    workspace.git(repo, "init", "-q", "-b", "trunk")
    workspace.git(repo, "add", "-A")
    workspace.git(repo, "commit", "-q", "-m", "first")
    return repo, projects.add(conn, repo, check=check)


def test_a_building_check_gets_a_dry_build_beside_the_plan(conn, src, tmp_path):
    repo, pid = project(conn, tmp_path, "npm run build")
    gid = selfwork.submit(conn, "greet people", project=pid)
    g = goal(conn, gid)
    assert g["state"] == "planning" and g["plan_run"] and g["dry_run"]
    run, plan = store.run(conn, g["dry_run"]), store.run(conn, g["plan_run"])
    assert run["provider"] == "command" and run["priority"] == plan["priority"]
    assert "npm run build" in run["prompt"]
    wt = store.cwd_of(conn, run["thread_id"])
    assert wt.endswith(f"dry-{gid}") and workspace.belongs(wt, repo)
    assert workspace.git(wt, "rev-parse", "HEAD") == projects.base(projects.get(conn, pid))
    assert workspace.git(repo, "branch", "--list", f"eki/dry-{gid}")
    title = conn.execute("SELECT title FROM threads WHERE id=?", (run["thread_id"],)).fetchone()[0]
    assert title == "dry build: greet people"


def test_a_check_that_doesnt_build_gets_none(conn, src, tmp_path):
    _, pid = project(conn, tmp_path, "pytest")
    g = goal(conn, selfwork.submit(conn, "greet people", project=pid))
    assert g["plan_run"] and g["dry_run"] is None


def test_ekis_own_goal_gets_none(conn, src):
    g = goal(conn, selfwork.submit(conn, "make eki nicer"))
    assert g["plan_run"] and g["dry_run"] is None


def test_a_failure_leaves_the_plan_started(conn, src, tmp_path, monkeypatch):
    _, pid = project(conn, tmp_path, "npm run build")
    real = workspace.add

    def add(repo, key, **kw):
        if key.startswith("dry-"):
            raise RuntimeError("no room")
        return real(repo, key, **kw)
    monkeypatch.setattr(workspace, "add", add)
    gid = selfwork.submit(conn, "greet people", project=pid)
    g = goal(conn, gid)
    assert g["state"] == "planning" and g["plan_run"] and g["dry_run"] is None
    titles = [r[0] for r in conn.execute("SELECT title FROM threads")]
    assert not any(t.startswith("dry build") for t in titles)


def test_a_failure_after_the_worktree_removes_it(conn, src, tmp_path, monkeypatch):
    repo, pid = project(conn, tmp_path, "npm run build")
    real = store.create_run

    def create_run(conn, tid, prompt, **kw):
        if kw.get("provider") == "command":
            raise RuntimeError("no run")
        return real(conn, tid, prompt, **kw)
    monkeypatch.setattr(store, "create_run", create_run)
    gid = selfwork.submit(conn, "greet people", project=pid)
    g = goal(conn, gid)
    assert g["plan_run"] and g["dry_run"] is None
    assert not workspace.path_for(f"dry-{gid}").exists()
    assert not workspace.git(repo, "branch", "--list", f"eki/dry-{gid}")
    titles = [r[0] for r in conn.execute("SELECT title FROM threads")]
    assert not any(t.startswith("dry build") for t in titles)
