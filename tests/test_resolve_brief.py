"""The resolve brief: every side's intent, for eki and for projects (eki/resolvebrief.py)."""
import json

import pytest

from eki import db, projects, rebase, resolve, resolvebrief, selfwork, store, workspace
from eki.workspace import git
from conftest import run_inline


def says(tmp_path, monkeypatch, text):
    p = tmp_path / "says.txt"
    p.write_text(text)
    monkeypatch.setenv("EKI_FAKE_SAYS_FILE", str(p))


def commit(where, name, text, *msg):
    (where / name).write_text(text)
    git(where, "add", "-A")
    git(where, "commit", "-q", *[a for m in msg for a in ("-m", m)])
    return workspace.head(where)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "a.txt").write_text("one\ntwo\nthree\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "first")
    return r


def goal(conn, gid, text, project=None):
    with db.tx(conn):
        conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project) "
                     "VALUES (?,?,?,?,?,?,?)", (gid, text, "ask", "you", "planned", db.now(), project))


def landed_item(conn, repo, n, text):
    """An eki item of goal g0 that landed on main, touching a.txt."""
    sha = commit(repo, "a.txt", text, f"commit of item {n}")
    with db.tx(conn):
        iid = selfwork.new_item(conn, "g0", f"Landed {n}", f"landed spec {n}", ["a.txt"], [], False)
        selfwork._set(conn, iid, summary=f"landed summary {n}", rebased=sha, state="applied")
    return iid


def mine(conn, repo, base, project=None, landed=None):
    """An item of goal g1 built from `base`, stopped mid-rebase on a.txt onto main's tip."""
    goal(conn, "g1", "make a.txt better", project)
    with db.tx(conn):
        iid = selfwork.new_item(conn, "g1", "Change line two", "put mine on line two", ["a.txt"], [], False)
    wt = workspace.add(repo, iid, base=base, branch=f"eki/{iid}")
    sha = commit(wt, "a.txt", "one\nmine\nthree\n", "Change line two")
    with db.tx(conn):
        tid = store.create_thread(conn, "Change line two", str(wt))
        selfwork._set(conn, iid, worktree=str(wt), branch=f"eki/{iid}", base=base, thread_id=tid,
                      commit_sha=sha, summary="line two says mine", state="queued")
    if landed:
        landed()
    head = workspace.head(repo)
    assert rebase.onto(wt, head) == ["a.txt"]
    return selfwork.store_item(conn, iid), head


def brief(conn, it, head):
    rid = resolve.start(conn, it, head, ["a.txt"])
    return rid, store.run(conn, rid)["prompt"]


def test_two_landed_items_are_named_with_their_intent(conn, repo):
    goal(conn, "g0", "the other goal")
    base = workspace.head(repo)
    it, head = mine(conn, repo, base, landed=lambda: (landed_item(conn, repo, 1, "one\nfirst\nthree\n"),
                                                      landed_item(conn, repo, 2, "one\nsecond\nthree\n")))
    _, text = brief(conn, it, head)
    for s in ("Landed 1", "landed spec 1", "landed summary 1", "Landed 2", "landed spec 2",
              "landed summary 2", "the other goal", "put mine on line two", "line two says mine"):
        assert s in text, s
    assert "every intent survives" in text and "ITEM: person <what contradicts>" in text
    assert "how each intent" in text
    assert "eki — the program that is running you" in text and "bin/check" in text


def test_a_persons_commit_shows_its_subject_and_body(conn, repo):
    base = workspace.head(repo)
    it, head = mine(conn, repo, base, landed=lambda: commit(
        repo, "a.txt", "one\nby hand\nthree\n", "Fix line two by hand", "Because the old one was wrong."))
    _, text = brief(conn, it, head)
    assert "Fix line two by hand" in text and "Because the old one was wrong." in text
    assert "a person's own commit" in text


def test_nothing_landed_says_so():
    text = resolvebrief.resolve(goal="g", title="t", spec="s", summary="", head="abc", conflicted=["x"],
                                head_changes="", landed="")
    assert resolvebrief.NOTHING_LANDED in text and text.startswith(resolvebrief.FIRST_LINE)


def test_a_projects_brief_names_it_and_its_check(conn, repo):
    pid = projects.add(conn, repo, check="make test-fast")
    p = projects.get(conn, pid)
    it, head = mine(conn, repo, workspace.head(repo), pid,
                    landed=lambda: commit(repo, "a.txt", "one\ntheirs\nthree\n", "theirs"))
    _, text = brief(conn, it, head)
    flat = " ".join(text.split())
    assert p["name"] in text and str(p["path"]) in text
    assert "`make test-fast`" in flat
    assert json.loads(projects.check_argv(p))[2] == "make test-fast"
    assert "eki — the program that is running you" not in text and "running you" not in flat
    assert "bin/check" not in text and text.startswith(resolvebrief.FIRST_LINE)


def test_a_project_without_a_check_has_no_command(conn, repo):
    pid = projects.add(conn, repo)
    p = projects.get(conn, pid)
    assert projects.check_argv(p) is None
    text = resolvebrief.resolve(goal="g", title="t", spec="s", summary="", head="abc", conflicted=["a.txt"],
                                head_changes="", landed="", project=p)
    assert resolvebrief.CHECK_NONE in text and "no check" in text and "own tests" in text
    assert "the project's check, `" not in text
    assert "bin/check" not in text and "running you" not in " ".join(text.split())


def test_a_contradiction_is_left_for_the_person(conn, repo, home, tmp_path, monkeypatch):
    data = json.loads((home / "routing.json").read_text())
    data["notify"] = {"topic": "t-resolve", "events": ["needs_you"]}
    (home / "routing.json").write_text(json.dumps(data))
    it, head = mine(conn, repo, workspace.head(repo),
                    landed=lambda: commit(repo, "a.txt", "one\ntheirs\nthree\n", "theirs"))
    rid, _ = brief(conn, it, head)
    says(tmp_path, monkeypatch, "SUMMARY: both cannot hold.\nITEM: person both want line two\n")
    run_inline(conn, rid)
    resolve.tick(conn)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "left" and cur["error"] == "both want line two"
    assert not rebase.in_progress(cur["worktree"])
    notes = conn.execute("SELECT key, body FROM notices").fetchall()
    assert any(n["key"].startswith(f"item:{it['id']}:left") and "both want line two" in n["body"]
               for n in notes)


def test_resolve_runs_counts_eki_and_project_runs(conn, repo):
    pid = projects.add(conn, repo)
    it, head = mine(conn, repo, workspace.head(repo), pid,
                    landed=lambda: commit(repo, "a.txt", "one\ntheirs\nthree\n", "theirs"))
    brief(conn, it, head)
    assert resolve.resolve_runs(conn, it) == 1
    eki = resolvebrief.resolve(goal="g", title="t", spec="s", summary="", head="abc", conflicted=["a.txt"],
                               head_changes="", landed="")
    with db.tx(conn):
        store.create_run(conn, it["thread_id"], eki)
        store.create_run(conn, it["thread_id"], "something else")
    assert resolve.resolve_runs(conn, it) == 2
