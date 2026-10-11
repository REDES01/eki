"""The workflow card's after_merge (eki/aftermerge.py): one build in the person's folder after eki lands."""
import json

from eki import aftermerge, projectqueue, selfview, store, workflow
from eki.components import workflow as kind
from conftest import run_inline
from test_notify_items import set_topic
from test_projectqueue import folder, get, item, project, tip  # noqa: F401  (folder is a fixture)
from test_workflow_queue import card


def builds(conn):
    return conn.execute("SELECT r.* FROM runs r JOIN threads t ON t.id=r.thread_id "
                        "WHERE t.title LIKE 'after merge %' ORDER BY r.created_at").fetchall()


def land(conn, p, title, files):
    iid = item(conn, p, title, files)
    projectqueue.tick(conn)
    assert get(conn, iid)["state"] == "applied"
    return iid


def test_the_card_reads_after_merge_and_checks_it_is_text(conn, folder):
    where = card(folder, after_merge="npm run build")
    assert workflow.of_root(folder).after_merge == "npm run build"
    assert kind.validate({"after_merge": 3}, where) == ["after_merge must be text"]
    assert kind.validate({"after_merge": ""}, where) == []
    card(folder)
    assert workflow.of_root(folder).after_merge == ""


def test_a_land_starts_one_build_in_the_person_s_folder(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="echo built > built.txt")
    land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    said = aftermerge.tick(conn)
    runs = builds(conn)
    assert len(runs) == 1 and any("after-merge" in s for s in said)
    run = runs[0]
    assert run["provider"] == "command" and run["priority"] == "background"
    assert store.cwd_of(conn, run["thread_id"]) == str(folder)
    assert json.loads(run["prompt"]) == ["/bin/sh", "-c", "echo built > built.txt"]
    assert aftermerge.tick(conn) == []                              # in flight: nothing new
    assert run_inline(conn, run["id"])["state"] == "done"
    assert (folder / "built.txt").read_text() == "built\n"
    aftermerge.tick(conn)
    assert aftermerge.status(p["id"])["state"] == "done"
    assert len(builds(conn)) == 1


def test_no_build_without_after_merge_or_under_merge_person(conn, folder):
    p = project(conn, folder)
    card(folder)
    land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    assert aftermerge.tick(conn) == [] and builds(conn) == []
    card(folder, merge="person", after_merge="echo hi")
    assert aftermerge.tick(conn) == [] and builds(conn) == []


def test_no_build_under_merge_pr(conn, folder, monkeypatch):
    p = project(conn, folder)
    land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    wf = workflow.Workflow(router="on", target="", plan="split", parallel=4, checks=("self",), command="",
                           merge="pr", playtester="playtester", card="", after_merge="echo hi")
    monkeypatch.setattr(workflow, "of_project", lambda c, pr: wf)
    assert aftermerge.tick(conn) == [] and builds(conn) == []


def test_a_failed_build_is_said_and_never_undoes_the_land(conn, folder, home):
    set_topic(home)
    p = project(conn, folder)
    card(folder, after_merge="echo boom; exit 1")
    iid = land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    landed = tip(p)
    aftermerge.tick(conn)
    run = run_inline(conn, builds(conn)[0]["id"])
    assert run["state"] == "failed"
    aftermerge.tick(conn)
    it = get(conn, iid)
    assert it["state"] == "applied" and tip(p) == landed == it["rebased"]
    line = selfview.said(it, c=conn)
    assert line.startswith(f"✓ landed on master ({landed[:12]}); after-merge build failed")
    assert "boom" in line
    notes = conn.execute("SELECT * FROM notices").fetchall()
    assert [n["key"] for n in notes] == [f"aftermerge:{run['id']}"] and notes[0]["kind"] == "needs_you"
    assert p["name"] in notes[0]["body"] and "boom" in notes[0]["body"]
    assert len(builds(conn)) == 1                                   # nothing new landed: not built again
    card(folder, after_merge="true")                                # the next land builds, and passes
    land(conn, p, "Bee", {"b.txt": "bee two\n"})
    aftermerge.tick(conn)
    run_inline(conn, builds(conn)[1]["id"])
    aftermerge.tick(conn)
    assert get(conn, iid)["why"] == "after-merge build failed: boom"   # not covered by the new run
    assert len(builds(conn)) == 2


def test_a_passing_build_clears_the_failure_it_covers(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="exit 1")
    iid = land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    aftermerge.tick(conn)
    run_inline(conn, builds(conn)[0]["id"])
    aftermerge.tick(conn)
    assert get(conn, iid)["why"].startswith("after-merge build failed")
    rec = aftermerge.status(p["id"])
    aftermerge._write(p["id"], {**rec, "upto": rec["from"]})      # cover it again
    card(folder, after_merge="true")
    aftermerge.tick(conn)
    run_inline(conn, builds(conn)[1]["id"])
    aftermerge.tick(conn)
    assert get(conn, iid)["why"] is None
    assert selfview.said(get(conn, iid), c=conn).startswith("✓ landed on master")


def test_landings_close_together_cost_one_build(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="true")
    a = item(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    b = item(conn, p, "Bee", {"b.txt": "bee two\n"})
    projectqueue.tick(conn)
    assert get(conn, a)["state"] == get(conn, b)["state"] == "applied"
    aftermerge.tick(conn)
    assert len(builds(conn)) == 1
    assert sorted(aftermerge.status(p["id"])["items"]) == sorted([a, b])


def test_a_land_during_a_build_leads_to_exactly_one_more(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="true")
    land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    aftermerge.tick(conn)
    first = builds(conn)[0]["id"]
    c = land(conn, p, "Bee", {"b.txt": "bee two\n"})
    d = land(conn, p, "Log", {"log.txt": "started\nmore\n"})
    aftermerge.tick(conn)
    assert len(builds(conn)) == 1                                   # one at a time
    run_inline(conn, first)
    aftermerge.tick(conn)
    aftermerge.tick(conn)
    runs = builds(conn)
    assert len(runs) == 2
    assert sorted(aftermerge.status(p["id"])["items"]) == sorted([c, d])
    run_inline(conn, runs[1]["id"])
    aftermerge.tick(conn)
    assert len(builds(conn)) == 2


def test_a_restart_finds_the_run_again(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="true")
    land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    aftermerge.tick(conn)
    aftermerge.tick(conn)                                           # a restart: the file is all there is
    assert len(builds(conn)) == 1


def test_a_kill_before_the_file_is_written_adopts_the_run(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="true")
    iid = land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    aftermerge.tick(conn)
    aftermerge._path(p["id"]).unlink()                              # killed between the run and the file
    said = aftermerge.tick(conn)
    assert len(builds(conn)) == 1, said
    rec = aftermerge.status(p["id"])
    assert rec["run"] == builds(conn)[0]["id"] and rec["items"] == [iid]


def test_a_run_that_is_gone_is_started_again(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="true")
    iid = land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    aftermerge.tick(conn)
    first = builds(conn)[0]["id"]
    with conn:
        conn.execute("DELETE FROM runs WHERE id=?", (first,))
    aftermerge.tick(conn)
    runs = builds(conn)
    assert len(runs) == 1 and runs[0]["id"] != first
    assert aftermerge.status(p["id"])["items"] == [iid]


def test_no_checked_out_folder_no_build(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="true")
    land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    from eki.workspace import git
    git(folder, "checkout", "-q", "--detach")
    assert aftermerge.tick(conn) == [] and builds(conn) == []


def touch(conn, iid, files):
    with conn:
        conn.execute("UPDATE items SET touched=? WHERE id=?", (json.dumps(files), iid))


def test_a_changed_package_json_installs_before_the_build(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="npm run build")
    iid = land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    touch(conn, iid, ["package.json", "src/app.js"])
    said = aftermerge.tick(conn)
    assert json.loads(builds(conn)[0]["prompt"]) == ["/bin/sh", "-c", "npm install --include=dev && npm run build"]
    assert any("npm install --include=dev && npm run build" in s for s in said)


def test_only_source_files_build_as_before(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="npm run build")
    iid = land(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    touch(conn, iid, ["src/app.js", "src/package.json.bak", "README.md"])
    aftermerge.tick(conn)
    assert json.loads(builds(conn)[0]["prompt"]) == ["/bin/sh", "-c", "npm run build"]


def test_a_lockfile_in_a_subfolder_counts_and_one_item_is_enough(conn, folder):
    p = project(conn, folder)
    card(folder, after_merge="npm run build")
    a = item(conn, p, "Line two", {"a.txt": "one\nTWO\nthree\n"})
    b = item(conn, p, "Bee", {"b.txt": "bee two\n"})
    projectqueue.tick(conn)
    touch(conn, a, ["src/app.js"])
    touch(conn, b, ["web/client/pnpm-lock.yaml"])
    aftermerge.tick(conn)
    assert json.loads(builds(conn)[0]["prompt"]) == ["/bin/sh", "-c", "npm install --include=dev && npm run build"]


def test_package_files_are_matched_by_name():
    rows = lambda *t: [{"touched": json.dumps(list(t))}]  # noqa: E731
    for name in ("package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
                 "bun.lockb", "a/b/yarn.lock"):
        assert aftermerge.packages_changed(rows(name)), name
    assert not aftermerge.packages_changed(rows("src/index.js", "mypackage.json", "package.json/x"))
    assert not aftermerge.packages_changed([{"touched": "[]"}, {"touched": None}])
