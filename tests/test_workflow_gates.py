"""The engine follows a project's workflow card: its plan and the gates after a build (eki/workflowgates.py)."""
import json

import pytest

from eki import projects, selfwork, store, workflow, workflowgates
from conftest import run_inline
from test_project_goals import built, planned, says, src  # noqa: F401 — the source fixture
from test_resolve_project import conflicted, repo, resolved  # noqa: F401 — the repo fixture
from test_review import settle
from test_workflow_card import card

#: the fake stands in for the local model on the review chore
pytestmark = pytest.mark.usefixtures("text_fake")
#: the real reader of eki's own card (the home fixture hides this checkout's card from every test)
OWN = workflowgates.own


def commands(conn):
    return conn.execute("SELECT * FROM runs WHERE provider='command'").fetchall()


def test_self_only_proposes_with_no_gate_run_and_no_review(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self"])
    pid, it = planned(conn, tmp_path, monkeypatch, proj, "true")
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "proposed" and it["verdict"] == "checks: self only"
    assert it["review_run"] is None and it["review"] == "none: not in this project's workflow"
    assert it["judged_by"] is None and not commands(conn)


def test_command_is_gate_one_and_no_reviewer_without_review(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command"], command="echo judged")
    pid, it = planned(conn, tmp_path, monkeypatch, proj, "false")     # the card's command, not the row's
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging" and it["judged_by"] == "echo judged"
    assert json.loads(store.run(conn, it["run_id"])["prompt"]) == ["/bin/sh", "-c", "echo judged", "eki-judge"]
    assert it["review_run"] is None and it["review"] == "none: not in this project's workflow"
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "proposed"


def test_review_alone_starts_the_reader_and_judges_by_it(conn, src, proj, tmp_path, monkeypatch):
    settle(local="bare")                   # a local model to read with
    card(proj, checks=["self", "review"])
    pid, it = planned(conn, tmp_path, monkeypatch, proj, "false")
    it = built(conn, tmp_path, monkeypatch, it)
    assert it["state"] == "judging" and it["review_run"]
    gate = json.loads(store.run(conn, it["run_id"])["prompt"])
    assert gate[:2] == ["/bin/sh", "-c"] and gate[2] == "echo 'checks: self, review'"
    run_inline(conn, it["run_id"])
    says(tmp_path, monkeypatch, "Looked.\nREVIEW: ok\n")
    run_inline(conn, it["review_run"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed" and it["review"] == "ok"


def test_playtester_plays_the_change_after_the_command(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command", "playtester"], command="make test || exit 1")
    pid, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built(conn, tmp_path, monkeypatch, it)
    line = json.loads(store.run(conn, it["run_id"])["prompt"])[2]
    assert line == "( make test || exit 1\n) && eki use playtester 'play the change: Add greet'"


def test_plan_single_is_one_item_with_no_planner_and_says_its_checks_once(conn, src, proj, tmp_path, monkeypatch):
    card(proj, plan="single", checks=["self"])
    pid = projects.add(conn, proj, check=None)
    gid = selfwork.submit(conn, "add a greeting\n\nmake greet.py say hi", project=pid)
    g = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    assert g["state"] == "planned" and g["plan_run"] is None
    items = conn.execute("SELECT * FROM items WHERE goal_id=?", (gid,)).fetchall()
    assert len(items) == 1 and items[0]["spec"] == "add a greeting\n\nmake greet.py say hi"
    said = selfwork.tick(conn)
    assert [s for s in said if "building" in s] == [
        f"item {items[0]['id']}: building (add a greeting) — plan single; checks: self only"]
    assert not any("checks:" in s for s in selfwork.tick(conn))


def test_a_split_plan_says_its_checks_when_it_concludes(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self"])
    seen = []
    real = selfwork.tick
    monkeypatch.setattr(selfwork, "tick", lambda c: seen.extend(real(c)) or seen)
    planned(conn, tmp_path, monkeypatch, proj, None)
    assert any(s.endswith("1 item(s) planned — checks: self only") for s in seen)
    assert not any("checks:" in s for s in seen if "building" in s)


def test_eki_settings_follow_its_own_card(conn, src, monkeypatch):
    monkeypatch.setattr(workflowgates, "own", OWN)
    assert selfwork.settings()["autonomy"] == "propose"            # no card: today's settings
    card(src, parallel=2, merge="auto", checks=["self", "batch", "train"])
    st = selfwork.settings()
    assert (st["autonomy"], st["parallel"], st["review"]) == ("apply", 2, False)


#: the game's two rules: gate 1 runs only what a change touched
GAME = [{"name": "story files", "paths": ["design/script/**", "design/quests/**", "slice/data/**"],
         "run": "npm run check:story"},
        {"name": "code", "paths": ["slice/src/**", "slice/tools/**", "tools/**"],
         "run": "npm run typecheck && npm run build"}]


def built_touching(conn, tmp_path, monkeypatch, it, files):
    says(tmp_path, monkeypatch, "SUMMARY: made it.\n\nITEM: done\n")
    monkeypatch.setenv("EKI_FAKE_TOUCH", ",".join(files))
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    return selfwork.store_item(conn, it["id"])


def game(runs=None):
    return [{**r, "run": runs[k]} for k, r in enumerate(GAME)] if runs else GAME


def test_a_story_only_item_runs_only_the_story_rule(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command"], command=game())
    pid, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built_touching(conn, tmp_path, monkeypatch, it, ["design/script/a.yaml"])
    line = json.loads(store.run(conn, it["run_id"])["prompt"])[2]
    assert "npm run check:story" in line and "typecheck" not in line
    assert it["judged_by"] == "story files: npm run check:story"


def test_a_mixed_item_runs_both_rules_once_in_card_order(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command"], command=game(["echo story ran", "echo code ran"]))
    pid, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built_touching(conn, tmp_path, monkeypatch, it, ["tools/x.ts", "slice/data/b.json", "tools/y.ts"])
    assert it["judged_by"] == "story files: echo story ran · code: echo code ran"
    out = store.answer(conn, run_inline(conn, it["run_id"])["id"])
    heads = [ln for ln in out.splitlines() if ln.startswith("== ")]
    assert heads == ["== story files: echo story ran", "== code: echo code ran"]
    assert out.count("story ran") == 2 and out.count("code ran") == 2     # the heading and the output
    selfwork.tick(conn)
    assert selfwork.store_item(conn, it["id"])["state"] == "proposed"


def test_a_red_rule_doesnt_stop_the_next_and_the_item_retries(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command"], command=game(["echo story broke; exit 3", "echo code fine"]))
    pid, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built_touching(conn, tmp_path, monkeypatch, it, ["design/quests/q.yaml", "slice/src/main.ts"])
    run = run_inline(conn, it["run_id"])
    assert run["state"] == "failed"
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "building" and it["tries"] == 2               # the one more try, started
    assert "story broke" in it["verdict"] and "code fine" in it["verdict"]
    brief = store.run(conn, it["run_id"])["prompt"]
    assert "story broke" in brief and "code fine" in brief


def test_a_docs_only_item_is_green_with_nothing_to_check(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command"], command=game())
    pid, it = planned(conn, tmp_path, monkeypatch, proj, None)
    it = built_touching(conn, tmp_path, monkeypatch, it, ["README.md"])
    assert it["judged_by"] == "nothing to check for these files"
    run_inline(conn, it["run_id"])
    selfwork.tick(conn)
    it = selfwork.store_item(conn, it["id"])
    assert it["state"] == "proposed" and "nothing to check for these files" in it["verdict"]


def test_rules_and_the_playtester_join_as_today(conn, src, proj):
    card(proj, checks=["self", "command", "playtester"], command=game())
    wf = workflow.of_root(proj)
    argv, by = workflowgates.gate(wf, "Story", ["design/script/a.yaml"])
    line = json.loads(argv)[2]
    assert line.startswith("( s=0\n") and line.endswith("\n) && eki use playtester 'play the change: Story'")
    assert by == "story files: npm run check:story && eki use playtester 'play the change: Story'"


def test_a_string_command_gate_is_as_today_whatever_was_touched(conn, src, proj):
    card(proj, checks=["self", "command"], command="make test")
    wf = workflow.of_root(proj)
    want = (json.dumps(["/bin/sh", "-c", "make test", "eki-judge"]), "make test")
    assert workflowgates.gate(wf, "x", ["design/script/a.yaml"]) == want == workflowgates.gate(wf, "x")


def test_the_builder_brief_names_the_rules(conn, src, proj, tmp_path, monkeypatch):
    card(proj, checks=["self", "command"], command=game())
    pid, it = planned(conn, tmp_path, monkeypatch, proj, None)
    brief = store.run(conn, it["run_id"])["prompt"]
    assert "run the rules whose paths match the files you touch" in brief
    assert "story files (design/script/**, design/quests/**, slice/data/**) → `npm run check:story`" in brief


def test_a_rebuild_after_a_conflict_goes_to_gate_2_without_running_the_rules(conn, repo, tmp_path, monkeypatch):
    pid = projects.add(conn, repo)
    it, head = conflicted(conn, repo, pid)
    card(repo, checks=["self", "command", "batch"], batch_command="echo together",
         command=[{"name": "text", "paths": ["*.txt"], "run": "echo text"},
                  {"name": "code", "paths": ["src/"], "run": "echo code"}])
    said = resolved(conn, it, head, tmp_path, monkeypatch)
    cur = selfwork.store_item(conn, it["id"])
    assert cur["state"] == "queued" and cur["resolved_on"] == head, (cur["error"], said)
    assert not commands(conn)                    # gate 2 judges it with the changes under it


def test_the_resolve_brief_names_the_rules(conn, proj):
    from eki import resolvebrief
    card(proj, checks=["self", "command"], command=game())
    p = projects.get(conn, projects.add(conn, proj))
    text = resolvebrief.resolve(goal="g", title="t", spec="s", summary="", head="0" * 40, conflicted=["a"],
                                head_changes="", landed="", project=p)
    assert "rules whose paths match the files your change touches" in text
    assert "code (slice/src/**, slice/tools/**, tools/**) → `npm run typecheck && npm run build`" in text
