# SPDX-License-Identifier: Apache-2.0
"""eki working on itself, around the clock: the roadmap it reads and ticks,
the choice of what to take next, and the whole pipeline in the engine —
a chat message, the goal's turns, apply, undo, the weekly note — against a
throwaway repo, a stand-in agent and a stand-in checker."""
import json
import subprocess
import time
from pathlib import Path

import pytest

from eki import builds, candidate, goals, roadmap, selfloop, selfwork, shift, watch
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from eki.quota.base import Reading, Window
from tests.test_runs import echo_config, settle

PLAN = """# Roadmap

Where eki is going.

## Where it stands

- [ ] **Commit the working tree:** the image edits.
- [ ] Confirm the swipe fix on a real trackpad *(for a person)*

## Stage 1 — One set of skills

Why stage one exists: one copy of everything.

- [x] **One skill store.** Done long ago.
- [ ] **One standing context.** AGENTS.md is canonical,
      CLAUDE.md imports it.
- *Evolve:* not an item.

## Not planned

- Asking two models the same thing.
"""


# ---- the roadmap ------------------------------------------------------------------------------

def test_items_are_the_checkboxes_in_file_order():
    items = roadmap.parse(PLAN)
    assert [i.title for i in items] == ["Commit the working tree", "Confirm the swipe fix on a real trackpad",
                                        "One skill store", "One standing context"]
    assert items[3].text == "**One standing context.** AGENTS.md is canonical, CLAUDE.md imports it."
    assert items[3].section == "Stage 1 — One set of skills" and (items[3].start, items[3].end) == (14, 15)
    assert items[1].person and items[2].done
    assert [i.title for i in roadmap.workable(items)] == ["Commit the working tree", "One standing context"]
    assert roadmap.intro(PLAN, "Stage 1 — One set of skills") == "Why stage one exists: one copy of everything."
    assert roadmap.counts(items) == {"done": 1, "open": 3, "person": 1}


def test_the_key_survives_reordering_and_rewording_below_the_title():
    moved = PLAN.replace("CLAUDE.md imports it.", "and CLAUDE.md is an import of it.")
    a = {i.title: i.key for i in roadmap.parse(PLAN)}
    b = {i.title: i.key for i in roadmap.parse(moved)}
    assert a == b


def test_a_tick_names_the_change_and_leaves_the_rest_alone():
    key = roadmap.parse(PLAN)[3].key
    after = roadmap.tick(PLAN, key, "*(eki: self/ab12)*")
    assert "- [x] **One standing context.** AGENTS.md is canonical,\n" in after
    assert "      CLAUDE.md imports it. *(eki: self/ab12)*\n" in after
    assert after.replace("- [x] **One standing", "- [ ] **One standing").replace(" *(eki: self/ab12)*", "") == PLAN
    assert roadmap.tick(after, key, "again") == after                    # ticked once
    one = roadmap.tick(PLAN, roadmap.parse(PLAN)[0].key, "*(x)*")
    assert "- [x] **Commit the working tree:** the image edits. *(x)*\n" in one


def test_a_picked_suggestion_goes_under_its_stage_or_an_inbox():
    after = roadmap.add(PLAN, "Stage 1 — One set of skills", "**Gemini CLI.** Read the same store.")
    items = roadmap.parse(after)
    assert items[4].title == "Gemini CLI" and items[4].section == "Stage 1 — One set of skills"
    inbox = roadmap.add(PLAN, "", "**Add Exa.** For research.")
    assert "## Inbox" in inbox and inbox.index("## Inbox") < inbox.index("## Not planned")
    assert roadmap.parse(inbox)[-1].section == "Inbox"
    again = roadmap.add(inbox, "", "**Add Tavily.**")
    assert again.count("## Inbox") == 1 and [i.title for i in roadmap.parse(again)][-2:] == ["Add Exa", "Add Tavily"]


# ---- what to take next ------------------------------------------------------------------------

def test_the_order_is_carry_on_asked_faults_note_then_the_roadmap():
    it, why = selfloop.pick(PLAN)
    assert it.source == "roadmap" and it.title == "Commit the working tree" and "Where it stands" in why
    note = selfloop.add("note", "eki's weekly note")
    fault = selfloop.add("fault", "Fix KeyError", "the traceback", key="KeyError in eki/x.py:f")
    assert selfloop.pick(PLAN)[0].id == fault.id
    asked = selfloop.add("asked", "rename a thing", "rename it")
    assert selfloop.pick(PLAN)[0].id == asked.id
    selfloop.update(fault.id, state="working", run="r1")
    assert selfloop.pick(PLAN, live=["r1"]) == (None, "working on “Fix KeyError”")
    assert selfloop.pick(PLAN)[0].id == fault.id                          # its run died: carry on
    selfloop.update(fault.id, state="done")
    selfloop.update(asked.id, state="done")
    assert selfloop.pick(PLAN)[0].id == note.id


def test_while_changes_wait_for_you_only_what_you_asked_goes_ahead():
    got, why = selfloop.pick(PLAN, waiting=3, review_max=3)
    assert got is None and "3 changes waiting for you" in why
    asked = selfloop.add("asked", "x", "x")
    assert selfloop.pick(PLAN, waiting=3, review_max=3)[0].id == asked.id


def test_a_roadmap_item_isnt_taken_again_once_it_has_settled():
    first, _ = selfloop.pick(PLAN)
    for state in ("review", "person", "gave up", "dropped", "done"):
        selfloop.update(first.id, state=state)
        assert selfloop.pick(PLAN)[0].title == "One standing context"
    selfloop.update(first.id, state="queued")
    assert selfloop.pick(PLAN)[0].id == first.id                          # a retry is the same item


def test_after_the_verdict():
    it = selfloop.add("roadmap", "One standing context", key="k")
    assert selfloop.after_change(it, {"id": "c1", "state": "proposed", "said": "done"}).state == "review"
    assert selfloop.after_change(it, {"id": "c1", "state": "applied", "said": "done"}).state == "done"
    part = selfloop.after_change(it, {"id": "c2", "state": "applied", "said": "partial"})
    assert part.state == "queued" and part.changes == ["c1", "c2"]
    unfit = {"id": "c3", "state": "unfit", "report": {"checks": [{"name": "tests", "ok": False,
                                                                   "skipped": False, "detail": "2 failed"}]}}
    once = selfloop.after_change(selfloop.get(it.id), unfit)
    assert once.state == "queued" and once.attempts == 1 and once.note == "tests: 2 failed"
    assert selfloop.after_change(once, unfit).state == "gave up"
    who = selfloop.add("roadmap", "Swipe", key="s")
    assert selfloop.after_change(who, {"id": "c4", "state": "no change", "said": "person",
                                       "why": "needs a trackpad"}).state == "person"
    mine = selfloop.add("asked", "x", "x")
    assert selfloop.after_change(mine, {"id": "c5", "state": "unfit"}).state == "done"   # asked: tried once


def test_a_chat_message_that_asks_eki_to_change_itself():
    yes = ["eki, make the chat list show the project name", "improve yourself so the board loads faster",
           "change eki's code so runs show how long they took", "eki: hide the dock icon"]
    no = ["eki, make me an app for tracking habits", "eki, add a settings page to my app in ~/proj",
          "write a haiku about snow", "eki, why did that go to Fable?", "fix the tests in this repo"]
    assert [selfloop.addressed(t) for t in yes] == ["now"] * 4
    assert [selfloop.addressed(t) for t in no] == [""] * 5
    assert selfloop.addressed("eki, fix the router tonight") == "later"


def test_how_far_it_goes_alone_per_area():
    areas = {"docs/": "apply", "ROADMAP.md": "apply", "eki/": "propose", "eki/web/": "apply"}
    s = {"self_autonomy": "propose", "self_autonomy_areas": areas}
    assert selfloop.autonomy_for(["docs/a.md", "ROADMAP.md"], s) == "apply"
    assert selfloop.autonomy_for(["docs/a.md", "eki/x.py"], s) == "propose"
    assert selfloop.autonomy_for(["eki/web/goals.html"], s) == "apply"          # the longest match
    assert selfloop.autonomy_for(["README.md"], s) == "propose"
    assert selfloop.autonomy_for(["README.md"], {"self_autonomy": "apply"}) == "apply"


def test_the_item_line_and_the_note():
    assert selfloop.said("Did it.\n**ITEM: done**") == "done" and selfloop.said("no line") == ""
    assert selfloop.reason("It needs a trackpad.\nA person has to swipe.\nITEM: person") == \
        "It needs a trackpad. A person has to swipe."
    text, sugg = selfloop.parse_note('Busy week.\n\n```json\n[{"title": "Add Exa", "why": "5 gaps", '
                                     '"kind": "server", "do": "add the Exa MCP server"}, {"nope": 1}]\n```')
    assert text == "Busy week." and len(sugg) == 1 and sugg[0]["kind"] == "server" and sugg[0]["picked"] == ""
    assert selfloop.parse_note("no json at all") == ("no json at all", [])
    assert not selfloop.note_due(since=time.time())
    assert selfloop.note_due(since=time.time() - 8 * 86400)
    selfloop.save_note("x", [], now=time.time() - 86400)
    assert not selfloop.note_due(since=0)


# ---- a throwaway eki, a stand-in agent, a stand-in checker -------------------------------------

def git(where, *args):
    return subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def forge(tmp_path: Path) -> Path:
    root = tmp_path / "eki-src"
    (root / "eki").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "eki" / "__init__.py").write_text("")
    (root / "eki" / "thing.py").write_text("VALUE = 1\n")
    (root / "tests" / "test_thing.py").write_text("def test_it():\n    assert True\n")
    (root / "README.md").write_text("eki\n")
    (root / "ROADMAP.md").write_text(PLAN)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


class Agent(Backend):
    """Edits the folder it's given, says what it did. `per`: edits for a
    request that says a word, for work going on side by side."""
    edits, answers, seen, fit, per = {}, [], [], True, {}

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        cwd = kw.get("cwd")
        Agent.seen.append((self.info.key, messages[-1].content, cwd))
        edits = next((e for word, e in Agent.per.items() if word in messages[-1].content), Agent.edits)
        for name, text in edits.items():
            if cwd:
                path = Path(cwd) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        yield Agent.answers.pop(0) if Agent.answers else "Done."


@pytest.fixture
def eng(tmp_path, monkeypatch):
    from eki.engine import Engine
    from eki.adapters import base as adapters
    cfg = echo_config(tmp_path)
    cfg.backends, cfg.options = [], {}
    cfg.backends.append(BackendInfo(key="claude_code", kind="claude_code", label="Claude Code",
                                    capabilities=Capabilities(tools=True, repo=True, web=True, vision=True),
                                    cost=Cost(tier=50), quota_source="claude"))
    cfg.options["claude_code"] = {"binary": "/bin/echo"}
    cfg.backends.append(BackendInfo(key="qwen", kind="mlx", label="Qwen (local)",
                                    capabilities=Capabilities(), cost=Cost(tier=0)))
    cfg.options["qwen"] = {"model": "q"}
    watch.save({"vendors": {"claude_code": {"vendor": "Anthropic", "ladder": {"default": "opus"}}}})
    monkeypatch.setattr(adapters, "build", lambda info, opts: Agent(info, opts))
    e = Engine(cfg, owner=True)
    e.settings = {**e.settings, "skills_learn": "off", "notify_learned": False, "skills_local": False,
                  "worktrees": False, "notify_goals": False}
    monkeypatch.setattr(e, "_lives", lambda b: False)
    e.router.is_up = lambda k: True
    monkeypatch.setattr(shift, "check", lambda **kw: shift.Gate(True))
    monkeypatch.setattr(shift, "must_stop", lambda *a: shift.Gate(True))
    root = forge(tmp_path)
    monkeypatch.setattr(e, "_self_root", lambda: root)
    # the base check and the candidate check, stood in for (the real ones run pytest and an engine)
    monkeypatch.setattr(candidate, "check_tests", lambda where, python: None)
    e.self_check = lambda where, **kw: candidate.Report(str(where), [candidate.Check("tests", Agent.fit, "stand-in")])
    e.quota.latest["claude"] = Reading("claude", [Window("five_hour", "5H", 0.1)])
    swaps = []
    monkeypatch.setattr(builds, "swap", lambda target, **kw: swaps.append((Path(target), kw)) or {})
    e.swaps, e.root = swaps, root
    Agent.edits, Agent.answers, Agent.seen, Agent.fit, Agent.per = {}, [], [], True, {}
    return e


async def turn(e):
    state = await e.shift_tick()
    if e._shift_run:
        await settle(e.runs, e._shift_run, timeout=20)
        e._goal_finished()
    return state


# ---- in the engine -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_chat_message_asking_eki_to_change_itself_is_worked_in_that_thread(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n", "tests/test_two.py": "def test_two():\n    pass\n"}
    Agent.answers = ["The chat list shows the project name now."]
    started = await eng.ask("eki, make the chat list show the project name")
    assert (await settle(eng.runs, started["run"], timeout=20))["state"] == "done"
    who, told, cwd = Agent.seen[-1]
    assert who == "claude_code" and "eki's own source" in told and "make the chat list" in told
    assert cwd and Path(cwd) != eng.root and cwd in told                    # its own worktree, named
    thread = eng.store.turns(started["conversation"])
    assert [t["role"] for t in thread] == ["user", "assistant", "assistant"]
    assert thread[2]["backend"] == "eki" and "proposed, waiting for you" in thread[2]["content"]
    c = selfwork.changes()[0]
    assert c["state"] == "proposed" and c["fit"] and sorted(c["files"]) == ["eki/thing.py", "tests/test_two.py"]
    assert (eng.root / "eki" / "thing.py").read_text() == "VALUE = 1\n"      # nothing merged
    it = selfloop.get(c["item"])
    assert it.state == "review" and it.conversation == started["conversation"] and it.source == "asked"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_loop_works_through_the_roadmap_and_ticks_what_landed(eng):
    eng.self_on(True)
    eng.settings = {**eng.settings, "self_autonomy_areas": {"ROADMAP.md": "apply"}}
    Agent.answers = ["The image edits were committed in 3f2a9c1.\nITEM: already"]
    state = await turn(eng)
    assert state["state"] == "working" and "Commit the working tree" in state["why"]
    told = Agent.seen[-1][1]
    assert "the image edits" in told and "ITEM: done" in told and "Read ROADMAP.md first" in told
    text = (eng.root / "ROADMAP.md").read_text()                            # applied: in your checkout
    assert "- [x] **Commit the working tree:** the image edits. *(eki: self/" in text
    assert git(eng.root, "log", "-1", "--format=%s") == "self: Commit the working tree"
    assert not eng.swaps                                                    # documentation: no swap
    assert next(i for i in selfloop.items() if i.source == "roadmap").state == "done"
    # the item for a person is skipped; the next open one is taken
    Agent.edits = {"AGENTS.md": "# eki\n"}
    Agent.answers = ["AGENTS.md is canonical now; CLAUDE.md is next.\nITEM: partial"]
    await turn(eng)
    assert "CLAUDE.md imports it" in Agent.seen[-1][1]
    second = [i for i in selfloop.items() if i.source == "roadmap"][1]
    c = selfwork.change(second.change)
    assert c["state"] == "proposed" and c["said"] == "partial" and c["files"] == ["AGENTS.md"]
    assert goals.all_goals()[0].turns == 2
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_while_changes_wait_for_you_it_starts_nothing_new_but_what_you_asked(eng):
    eng.self_on(True)
    eng.settings = {**eng.settings, "self_review_max": 1}
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    Agent.answers = ["Done.\nITEM: done"]
    await turn(eng)
    assert len(selfwork.waiting()) == 1
    state = await turn(eng)
    assert state["state"] == "idle" and "waiting for you" in goals.all_goals()[0].note
    assert eng.goals_view()["goals"][0]["status"] == "waiting"
    got = await eng.self_ask("rename VALUE to COUNT", when="later")
    assert got["queued"] and got["goal"]
    Agent.edits = {"eki/thing.py": "COUNT = 1\n"}
    state = await turn(eng)
    assert state["state"] == "working" and "rename VALUE" in state["why"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_an_item_for_a_person_is_left_and_two_failed_attempts_give_up(eng):
    eng.self_on(True)
    Agent.answers = ["It needs someone at a trackpad.\nITEM: person"]
    await turn(eng)
    first = next(i for i in selfloop.items() if i.source == "roadmap")
    assert first.state == "person" and "trackpad" in first.note
    assert "Left for you" in eng.store.turns(first.conversation)[-1]["content"]
    Agent.fit = False
    Agent.edits = {"eki/thing.py": "VALUE = 'broken'\n"}
    Agent.answers = ["Tried.\nITEM: done", "Tried again.\nITEM: done"]
    await turn(eng)
    second = [i for i in selfloop.items() if i.source == "roadmap"][1]
    assert second.state == "queued" and second.attempts == 1 and second.note == "tests: stand-in"
    await turn(eng)
    assert "An earlier attempt at this didn't pass eki's checks: tests: stand-in" in Agent.seen[-1][1]
    assert selfloop.get(second.id).state == "gave up"
    assert "nothing to do" in (await turn(eng))["why"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_applying_code_asks_the_supervisor_and_its_outcome_settles_it(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    started = await eng.self_ask("make VALUE two")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfwork.changes()[0]["id"]
    got = await eng.self_apply(cid)
    assert got["state"] == "applying" and eng.swaps[0][1] == {"self_id": cid}
    assert json.loads((eng.swaps[0][0] / builds.MARK).read_text())["note"] == f"self/{cid}"
    eng.self_settled({"self": cid, "state": "healthy", "merged": "merged into your checkout"})
    assert selfwork.change(cid)["state"] == "applied"
    assert selfloop.get(selfwork.change(cid)["item"]).state == "done"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_change_made_before_your_checkout_moved_is_put_on_top_of_it(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    started = await eng.self_ask("make VALUE two")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfwork.changes()[0]["id"]
    (eng.root / "README.md").write_text("eki, moved on\n")
    git(eng.root, "commit", "-qam", "yours, meanwhile")
    got = await eng.self_apply(cid)
    c = selfwork.change(cid)
    assert got["state"] == "applying" and c["base"] == git(eng.root, "rev-parse", "HEAD")
    assert git(eng.root, "merge-base", "--is-ancestor", "HEAD", c["commit"]) == ""
    # with resolving off, one that no longer goes on top says so, and waits
    eng.settings = {**eng.settings, "self_resolve": False}
    Agent.edits = {"README.md": "eki, the agent's way\n"}
    started = await eng.self_ask("reword the README")
    await settle(eng.runs, started["run"], timeout=20)
    other = selfloop.get(started["item"]).change
    (eng.root / "README.md").write_text("eki, your way\n")
    git(eng.root, "commit", "-qam", "yours again")
    assert (await eng.self_apply(other))["state"] == "conflicts"
    assert selfwork.change(other)["state"] == "conflicts"
    await eng.runner.stop()


async def _conflicting(eng):
    """A change to the README, and your own README commit after it."""
    Agent.edits = {"README.md": "eki, the agent's way\n"}
    started = await eng.self_ask("reword the README")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfloop.get(started["item"]).change
    (eng.root / "README.md").write_text("eki, your way\n")
    git(eng.root, "commit", "-qam", "yours, meanwhile")
    return cid


@pytest.mark.asyncio
async def test_a_conflict_on_apply_is_resolved_by_an_agent_and_then_applied(eng):
    cid = await _conflicting(eng)
    was = selfwork.change(cid)["commit"]
    Agent.edits = {"README.md": "eki, your way — and the agent's\n"}      # the resolution
    Agent.answers = ["README.md: kept your wording and added the agent's."]
    got = await eng.self_apply(cid)
    assert got["state"] == "conflicts" and got["resolving"]
    run = await settle(eng.runs, got["resolving"], timeout=20)
    assert run["state"] == "done" and run["backend"] == "claude_code"   # the program that wrote it
    who, told, cwd = Agent.seen[-1]
    assert "README.md" in told and "<<<<<<<" in told and "Don't run `git rebase --continue`" in told
    assert cwd == selfwork.change(cid)["worktree"]                       # in the change's own worktree
    c = selfwork.change(cid)
    assert c["state"] == "applied" and not c.get("resolving")             # documentation: merged
    assert (eng.root / "README.md").read_text() == "eki, your way — and the agent's\n"
    assert c["commit"] != was and c["resolved"]["files"] == ["README.md"]
    said = eng.store.turns(got["conversation"])[-1]["content"]
    assert "Conflicts in `README.md` were resolved by claude_code" in said and "kept your wording" in said
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_resolution_that_leaves_markers_puts_the_change_back_as_it_was(eng):
    cid = await _conflicting(eng)
    was = selfwork.change(cid)["commit"]
    Agent.edits = {}                                                     # the agent does nothing
    got = await eng.self_apply(cid)
    await settle(eng.runs, got["resolving"], timeout=20)
    c = selfwork.change(cid)
    assert c["state"] == "conflicts" and "conflict markers are still in README.md" in c["why"]
    where = Path(c["worktree"])
    assert not selfwork.rebasing(where) and git(where, "rev-parse", "HEAD") == was
    assert (eng.root / "README.md").read_text() == "eki, your way\n"     # yours, untouched
    assert "still conflicts with your checkout" in eng.store.turns(got["conversation"])[-1]["content"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_change_says_whats_new_in_plain_words(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    Agent.answers = ["Set VALUE to 2 in eki/thing.py.\n\nSUMMARY:\n- VALUE is two now.\n"
                     "- Nothing to set up; `eki self apply` takes it."]
    started = await eng.self_ask("make VALUE two")
    await settle(eng.runs, started["run"], timeout=20)
    c = selfwork.changes()[0]
    assert c["summary"] == "- VALUE is two now.\n- Nothing to set up; `eki self apply` takes it."
    said = eng.store.turns(started["conversation"])[-1]["content"]
    assert "**What's new**" in said and "VALUE is two now" in said
    assert "VALUE is two now" in "\n".join(selfwork.Proposal(**{k: v for k, v in c.items()
                                                                if k in selfwork.Proposal.__dataclass_fields__}).lines())
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_undo_takes_an_applied_change_back(eng):
    Agent.edits = {"README.md": "eki\nand more\n"}
    started = await eng.self_ask("say more in the README")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfwork.changes()[0]["id"]
    assert (await eng.self_apply(cid))["state"] == "applied"                  # documentation: merged
    assert (eng.root / "README.md").read_text() == "eki\nand more\n"
    got = await eng.self_undo(cid)
    assert got["state"] == "applied" and (eng.root / "README.md").read_text() == "eki\n"
    assert selfwork.change(cid)["state"] == "undone"
    assert selfwork.change(got["undo"])["reverts"] == cid
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_discarding_leaves_nothing_behind_and_the_item_isnt_retaken(eng):
    eng.self_on(True)
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    Agent.answers = ["Done.\nITEM: done"]
    await turn(eng)
    c = selfwork.changes()[0]
    await eng.self_discard(c["id"])
    assert selfwork.change(c["id"])["state"] == "discarded"
    assert "self/" not in git(eng.root, "branch", "--list") and not Path(c["worktree"]).exists()
    item = selfloop.get(c["item"])
    assert item.state == "dropped"
    Agent.answers = ["Done.\nITEM: done"]
    await turn(eng)
    assert "One standing context" in Agent.seen[-1][1]                     # not the discarded one again
    eng.self_item_action(item.id, "retry")
    assert selfloop.get(item.id).state == "queued"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_weekly_note_and_what_you_do_with_its_suggestions(eng):
    Agent.answers = ["Qwen worked 2 h; Claude was corrected 5 times on writing.\n\n```json\n"
                     '[{"title": "Add the Exa search server", "why": "4 research requests had no web", '
                     '"kind": "server", "do": "add the Exa MCP server"},'
                     ' {"title": "Standing context first", "why": "3 corrections", "kind": "roadmap",'
                     ' "do": "one AGENTS.md", "stage": "Stage 1 — One set of skills"}]\n```']
    started = await eng.self_note_now()
    await settle(eng.runs, started["run"], timeout=20)
    note = selfloop.latest_note()
    assert note["text"].startswith("Qwen worked 2 h") and len(note["suggestions"]) == 2
    told = Agent.seen[-1][1]
    assert "weekly note" in told and '"local_share_of_the_week"' in told and "Commit the working tree" in told
    got = await eng.self_suggestion(note["id"], 1, "roadmap")
    assert got["section"] == "Stage 1 — One set of skills"
    added = roadmap.parse((eng.root / "ROADMAP.md").read_text())
    assert added[4].title == "Standing context first" and added[4].section == "Stage 1 — One set of skills"
    assert git(eng.root, "log", "-1", "--format=%s") == "roadmap: Standing context first"
    queued = await eng.self_suggestion(note["id"], 0, "ask")
    assert queued["queued"] and selfloop.get(queued["item"]).request == "add the Exa MCP server"
    assert [s["picked"] for s in selfloop.latest_note()["suggestions"]] == ["asked", "roadmap"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_view_says_what_it_is_doing_and_what_waits(eng):
    eng.self_on(True)
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    Agent.answers = ["Done.\nITEM: done"]
    await turn(eng)
    v = eng.self_view()
    assert v["can"] and v["goal"]["kind"] == "self" and v["autonomy"] == "propose"
    assert [c["title"] for c in v["waiting"]] == ["Commit the working tree"]
    assert v["roadmap"]["next"][0]["title"] == "One standing context"
    eng.self_on(False)
    assert goals.all_goals()[0].state == "paused"
    with pytest.raises(ValueError):
        eng.self_settings(autonomy="yolo")
    assert eng.self_settings(autonomy="apply", areas={"docs/": "propose"})["self_autonomy"] == "apply"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_changes_decided_outside_eki_are_noticed(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    first = await eng.self_ask("make VALUE two")
    await settle(eng.runs, first["run"], timeout=20)
    Agent.edits = {"README.md": "eki, reworded\n"}
    second = await eng.self_ask("reword the README")
    await settle(eng.runs, second["run"], timeout=20)
    merged, deleted = selfloop.get(first["item"]).change, selfloop.get(second["item"]).change
    git(eng.root, "merge", "-q", "--ff-only", f"self/{merged}")                # you merged one by hand
    git(eng.root, "worktree", "remove", "--force", selfwork.change(deleted)["worktree"])
    git(eng.root, "branch", "-D", f"self/{deleted}")                          # and deleted the other
    assert len(selfwork.waiting()) == 2
    eng._self_reconcile(force=True)
    assert selfwork.change(merged)["state"] == "applied" and selfwork.change(deleted)["state"] == "gone"
    assert selfwork.waiting() == [] and selfloop.get(second["item"]).state == "dropped"
    assert selfloop.get(first["item"]).state == "done"
    await eng.runner.stop()


def test_a_long_reason_is_cut_at_a_sentence():
    said = ("I didn't change anything. " + "This needs a person at the trackpad. " * 30) + "\nITEM: person"
    got = selfloop.reason(said)
    assert len(got) <= 600 and got.endswith("trackpad.") and got.startswith("I didn't")


@pytest.mark.asyncio
async def test_the_pictures_an_agent_took_are_shown_before_then_after(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    started = await eng.self_ask("make VALUE two")
    await settle(eng.runs, started["run"], timeout=20)
    c = selfwork.changes()[0]
    shots = selfwork.SHOTS / c["id"]
    shots.mkdir(parents=True)
    for name in ("after-chat-light.png", "before-chat-light.png"):
        (shots / name).write_bytes(b"\x89PNG")
    said = eng._self_says(selfwork.change(c["id"]), selfloop.get(c["item"]), {})
    assert said.index("before-chat-light") < said.index("after-chat-light") and "**Before / after**" in said
    await eng.runner.stop()


# ---- several at once ---------------------------------------------------------------------------

FILES = {"engine.py": "eki/engine.py", "router.py": "eki/router.py", "views.swift": "mac/Views.swift",
         "self-build.md": "docs/self-build.md"}


def test_an_items_area_comes_from_the_files_and_words_it_names():
    assert selfloop.area_of("make the sidebar in mac/Views.swift wider") == ["mac"]
    assert selfloop.area_of("Views.swift: a bigger title", FILES) == ["mac"]          # a look at the repo
    assert selfloop.area_of("routing should prefer the one with room; add tests") == ["routing"]
    assert selfloop.area_of("KeyError in eki/engine.py:412 and eki/router.py:9") == ["engine", "routing"]
    # docs and tests go with the code they're about; alone, they're a lane of their own
    assert selfloop.area_of("fix eki/cli.py and keep docs/self-build.md in step") == ["cli"]
    assert selfloop.area_of("reword docs/self-build.md") == ["docs"]
    assert selfloop.area_of("tidy things up") == ["*"]                                 # nothing to go on
    assert selfloop.area_of("") == ["note"]
    assert selfloop.overlaps(["mac"], ["engine", "mac"]) and not selfloop.overlaps(["mac"], ["engine"])
    assert selfloop.overlaps(["*"], ["docs"]) and not selfloop.overlaps(["note"], ["engine"])


def test_several_items_are_worked_on_at_once_each_in_its_own_area():
    app = selfloop.add("asked", "a bigger title", "mac/Views.swift: a bigger title")
    again = selfloop.add("asked", "the sidebar", "and the SwiftUI sidebar")
    route = selfloop.add("asked", "prefer room", "routing should prefer the one with room")
    # (no roadmap here: its open items would go ahead in areas of their own)
    it, why = selfloop.pick("", parallel=2)
    assert it.id == app.id and it.area == ["mac"] and why == "you asked for it"
    selfloop.update(app.id, state="working", run="r1")
    # the next one asked for touches the Mac app too: it waits, the routing one goes ahead
    it, _ = selfloop.pick("", parallel=2, live=["r1"])
    assert it.id == route.id and it.area == ["routing"]
    selfloop.update(route.id, state="working", run="r2")
    got, why = selfloop.pick("", parallel=3, live=["r1", "r2"])
    assert got is None and why.startswith("“the sidebar” waits: it touches the Mac app, like “a bigger title”")
    got, why = selfloop.pick("", parallel=2, live=["r1", "r2"])
    assert got is None and why == "working on 2 things at once: “a bigger title”, “prefer room”"
    # a change waiting its turn in the merge queue takes no room, and holds no area
    selfloop.update(app.id, phase="merging")
    assert selfloop.pick("", parallel=2, live=["r1", "r2"])[0].id == again.id
    # with one at a time, it's as it always was
    selfloop.update(app.id, phase="")
    assert selfloop.pick("", live=["r1", "r2"]) == (None, "working on 2 things at once: “a bigger title”, "
                                                           "“prefer room”")


def test_room_is_held_to_the_spare_room_and_the_machine_counts_apart():
    # two subscriptions: one whose spare room carries two changes, one with none left
    assert selfloop.room(4, {"claude": 2, "codex": 0}, {}) == (2, ["claude"])
    assert selfloop.room(4, {"claude": 2, "codex": 0}, {"claude": 2}) == (0, [])
    assert selfloop.room(2, {"claude": 5}, {"claude": 1}) == (1, ["claude"])            # the setting holds it
    # the models on this machine: one self run between them, apart from the subscriptions
    assert selfloop.room(4, {"claude": 1, "qwen": 1, "gemma": 1}, {"claude": 1}, local=["qwen", "gemma"]) \
        == (1, ["qwen", "gemma"])
    assert selfloop.room(4, {"claude": 2, "qwen": 1}, {"qwen": 1}, local=["qwen"]) == (2, ["claude"])


def test_spare_room_leaves_the_part_kept_for_you():
    from eki import capacity
    w = [Window("five_hour", "5H", 0.4)]
    assert capacity.spare({}, "claude", w, 0.3) is None                               # cost not known yet
    data = {"claude": {"five_hour": {"cost": 0.02, "n": 4}}}
    assert capacity.spare(data, "claude", w, 0.3) == 15.0                              # (0.7 - 0.4) / 0.02
    assert capacity.spare(data, "claude", [Window("five_hour", "5H", 0.8)], 0.3) == 0.0


def test_the_merge_queue_goes_in_the_order_changes_finished():
    assert selfloop.merge_join("a", run="ra") == 0
    assert selfloop.merge_join("b", run="rb") == 1
    assert selfloop.merge_join("c", run="rc") == 2
    assert selfloop.merge_join("a", run="ra2") == 0                     # carried on: keeps its place
    live = ["ra2", "rb", "rc"]
    assert selfloop.merge_turn("a", live) and not selfloop.merge_turn("b", live)
    selfloop.merge_leave("a")
    assert selfloop.merge_turn("b", live) and not selfloop.merge_turn("c", live)
    # one whose run was cut off keeps its place, but doesn't hold up the rest
    assert selfloop.merge_turn("c", ["rc"])
    assert [r["change"] for r in selfloop.merge_queue()] == ["b", "c"]


@pytest.mark.asyncio
async def test_the_loop_starts_several_when_theres_room_and_applies_them_one_by_one(eng):
    from eki import capacity
    eng.self_on(True)
    eng.settings = {**eng.settings, "self_autonomy": "apply", "self_parallel": 2}
    Agent.per = {"README": {"README.md": "eki, reworded\n"},
                 "thing.py": {"eki/thing.py": "VALUE = 2\n"}}
    await eng.self_ask("reword the README", when="later")
    await eng.self_ask("make VALUE two in eki/thing.py", when="later")
    # what a request costs on Claude isn't known yet: one change at a time there
    state = await eng.shift_tick()
    assert state["state"] == "working" and "more beside it" not in state["why"]
    assert len([i for i in selfloop.items() if i.state == "working"]) == 1
    await settle(eng.runs, eng._shift_run, timeout=20)
    eng._goal_finished()
    selfloop.items()
    # known, and the spare room carries several: the next starts beside
    capacity.save({"claude": {"five_hour": {"cost": 0.01, "n": 5}}})
    for it in selfloop.items():
        if it.state in ("review", "done"):
            selfloop.update(it.id, state="dropped")
    Agent.per = {"README": {"README.md": "eki, reworded again\n"},
                 "thing.py": {"eki/thing.py": "COUNT = 2\n"}}
    await eng.self_ask("rename VALUE in eki/thing.py", when="later")
    await eng.self_ask("reword the README again", when="later")
    state = await eng.shift_tick()
    assert "and 1 more beside it" in state["why"]
    working = [i for i in selfloop.items() if i.state == "working"]
    assert len(working) == 2 and sorted(a for i in working for a in i.area) == ["docs", "engine"]
    view = eng.self_view()
    assert view["parallel"] == 2 and sorted(a for i in view["working"] for a in i["areas"]) == \
        ["the docs", "the engine"]
    for i in working:
        await settle(eng.runs, i.run, timeout=30)
    eng._goal_finished()
    # both applied, one after the other, and the queue is empty again
    states = {selfwork.change(selfloop.get(i.id).change)["state"] for i in working}
    assert states == {"applied", "applying"} and not selfloop.merge_queue()
    assert (eng.root / "README.md").read_text() == "eki, reworded again\n"   # documentation: merged
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_finished_change_waits_its_turn_to_be_applied_and_isnt_failed(eng, monkeypatch):
    import asyncio
    from eki import selfengine
    monkeypatch.setattr(selfengine, "MERGE_POLL", 0.05)
    # a change that finished first, still in line (its run going)
    eng.runner.tasks["first"] = asyncio.create_task(asyncio.sleep(30))
    selfloop.merge_join("before", title="finished first", run="first")
    Agent.edits = {"README.md": "eki, reworded\n"}
    started = await eng.self_ask("reword the README", apply=True)
    for _ in range(200):
        it = selfloop.get(started["item"])
        if it.phase == "merging":
            break
        await asyncio.sleep(0.05)
    assert it.state == "working" and it.phase == "merging"               # in line: waiting, not failed
    assert [r["change"] for r in selfloop.merge_queue()] == ["before", it.change]
    assert selfloop.pick("", parallel=1, live=eng.runner.running)[1].startswith("nothing to do")  # holds no room
    assert eng.self_view()["merging"][1]["live"]
    await asyncio.sleep(0.3)
    assert selfwork.change(it.change)["state"] == "proposed"               # not before its turn
    selfloop.merge_leave("before")                                        # the first one is through
    eng.runner.tasks.pop("first").cancel()
    assert (await settle(eng.runs, started["run"], timeout=20))["state"] == "done"
    assert selfwork.change(it.change)["state"] == "applied" and not selfloop.merge_queue()
    assert "1 change finished before it" in eng.runs.get(started["run"])["output"]
    await eng.runner.stop()


def test_several_changes_asked_for_at_once_from_the_command_line(monkeypatch, tmp_path):
    from eki import cli, migrate
    sent = []
    monkeypatch.setattr(migrate, "run", lambda root: None)
    monkeypatch.setattr(cli, "ensure_engine", lambda s: None)
    monkeypatch.setattr(cli, "call", lambda m, p, s, **kw: sent.append(kw["json"]) or {"queued": True, "goal": True})
    assert cli.main(["self", "-r", "make the sidebar wider", "-r", "prefer room when routing"]) == 0
    assert [b["request"] for b in sent] == ["make the sidebar wider", "prefer room when routing"]
    assert {b["when"] for b in sent} == {"later"}
    batch = tmp_path / "asks.txt"
    batch.write_text("# this week\nfix the menu bar meter\nshow the area on the board\n")
    sent.clear()
    assert cli.main(["self", "--batch", str(batch), "and one more"]) == 0
    assert [b["request"] for b in sent] == ["fix the menu bar meter", "show the area on the board", "and one more"]
