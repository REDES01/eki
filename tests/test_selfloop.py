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

from eki import builds, candidate, goals, observe, roadmap, selfloop, selfwork, shift, watch
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


def test_an_item_waiting_on_something_else_isnt_taken_until_the_mark_goes():
    waiting = PLAN.replace("- [ ] **One standing context.**",
                           "- [ ] **One standing context.** *(waiting on Stage 5)*")
    assert [i.title for i in roadmap.workable(roadmap.parse(waiting))] == ["Commit the working tree"]
    assert [i.title for i in roadmap.workable(roadmap.parse(PLAN))][-1] == "One standing context"


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
    assert part.state == "person" and part.changes == ["c1", "c2"]      # a slice landed: not re-taken
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
    quiet = selfloop.add("roadmap", "Quiet", key="q")                    # changed nothing, said nothing
    left = selfloop.after_change(quiet, {"id": "c6", "state": "no change", "why": "it needs your hands"})
    assert left.state == "person" and left.note == "it needs your hands"


def test_an_item_left_for_you_comes_back_only_when_its_entry_changes():
    first, _ = selfloop.pick(PLAN)
    assert first.seen == selfloop.entry_print("**Commit the working tree:** the image edits.")
    selfloop.after_change(first, {"id": "c1", "state": "no change", "said": "person", "why": "your hands"})
    assert selfloop.pick(PLAN)[0].title == "One standing context"          # not taken again as it is
    reworded = PLAN.replace("the image edits.", "the image edits, and the icon.")
    again, why = selfloop.pick(reworded)
    assert again.id == first.id and again.state == "queued" and "changed since it was left for you" in why
    assert again.seen == selfloop.entry_print("**Commit the working tree:** the image edits, and the icon.")
    # one you said you'd do yourself (seen cleared) stays yours, whatever the file says
    selfloop.update(first.id, state="person", note="you're doing this one", seen="")
    assert selfloop.pick(PLAN.replace("the image edits.", "the edits."))[0].title == "One standing context"


def test_an_item_whose_change_was_applied_isnt_taken_again_ticked_or_not():
    first, _ = selfloop.pick(PLAN)
    selfloop.update(first.id, state="queued")                             # say, put back by an old engine
    got, _ = selfloop.pick(PLAN, landed=[first.key])
    assert got.title == "One standing context"
    selfloop.remove(first.id)                                             # no item left for it at all
    assert selfloop.pick(PLAN, landed=[first.key])[0].title == "One standing context"
    # a first slice landed and the rest was left for you: back only once you reword it
    part, _ = selfloop.pick(PLAN)
    selfloop.after_change(part, {"id": "c1", "state": "applied", "said": "partial"})
    assert selfloop.pick(PLAN, landed=[part.key])[0].title == "One standing context"
    reworded = PLAN.replace("the image edits.", "the image edits, and the rest.")
    assert selfloop.pick(reworded, landed=[part.key])[0].id == part.id


STAGED = """# Roadmap

## Stage 7 — Project memory

- [ ] **Remember and recall.** List, read, search and write notes.
- [ ] **Auto-memory lands there.** Instead of in skills.
- [ ] **Memory in the app.** Edit or remove it.
- [ ] **A memory size meter** *(independent)*: how big the folder is.

## Stage 8 — Orchestration

- [ ] **A task graph over runs.**

## Alongside every stage — eki builds eki

- [ ] **A daily digest.**
- [ ] **Work picked by impact.**
"""


def test_inside_a_stage_an_item_waits_for_the_open_ones_above_it():
    plan = roadmap.parse(STAGED)
    first, second, third, meter, graph, digest, impact = plan
    assert roadmap.after(plan, first) is None
    assert roadmap.after(plan, second) is first and roadmap.after(plan, third) is second
    assert roadmap.after(plan, meter) is None                              # (independent)
    assert roadmap.after(plan, graph) is None                              # a later stage doesn't wait
    assert roadmap.after(plan, impact) is None                             # outside the stages
    assert roadmap.after(plan, second, landed=[first.key]) is None         # applied, not ticked yet
    ticked = roadmap.parse(roadmap.tick(STAGED, first.key, "*(eki: self/ab12)*"))
    assert roadmap.after(ticked, ticked[1]) is None and roadmap.after(ticked, ticked[2]).title == second.title


def test_a_stage_gives_only_its_first_open_item_and_the_independent_one_beside_it(monkeypatch):
    monkeypatch.setattr(selfloop, "area_of", lambda text, files, root: [text.split("\n")[0]])  # order only
    taken, live = [], []
    while True:
        it, why = selfloop.pick(STAGED, parallel=9, live=live)
        if it is None:
            break
        live.append(f"r{len(live)}")
        selfloop.update(it.id, state="working", run=live[-1])
        taken.append(it.title)
    assert taken == ["Remember and recall", "A memory size meter", "A task graph over runs",
                     "A daily digest", "Work picked by impact"]
    assert why == "“Auto-memory lands there” waits — after: “Remember and recall”"


def test_landing_the_first_item_frees_the_second():
    first, _ = selfloop.pick(STAGED)
    selfloop.update(first.id, state="review")                              # proposed, not landed yet
    for _ in range(4):                                                     # the rest of the file, done
        selfloop.update(selfloop.pick(STAGED)[0].id, state="done")
    assert selfloop.pick(STAGED) == (None, "“Auto-memory lands there” waits — after: “Remember and recall”")
    second, _ = selfloop.pick(STAGED, landed=[first.key])                  # applied: the next may go
    assert second.title == "Auto-memory lands there"
    assert selfloop.pick(roadmap.tick(STAGED, first.key, "*(eki: self/ab12)*"))[0].id == second.id


RANKED = """# Roadmap

**The first public release** is Stages 1–2: an engine and a command line.

## Where it stands

**Left for the first release, in order** (the loop takes them
first):

1. *Handoff between backends* (Stage 2)
2. *Commands shared under Auto* (Stage 1) *(for a person)*

## Stage 1 — Skills

- [ ] **A skill reader.** Reads skills.

## Stage 2 — Runs

- [ ] **Handoff between backends** *(independent)*: on long threads.

## Stage 4 — Later

- [ ] **A late thing.** After the release.

## Alongside every stage — eki builds eki

- [ ] **A daily digest.** One page a day.
- [ ] **Score every change.** The local share before and after.

## What eki keeps current about itself

- [ ] **Outcomes from real use.** Redos and overrides adjust the scores.
- [ ] **Engine manifests refreshed.**
"""


def test_items_are_ranked_by_what_theyre_worth_for_the_release_not_file_order():
    assert roadmap.release_stages(RANKED) == range(1, 3)
    assert roadmap.left_for_release(RANKED) == ["Handoff between backends", "Commands shared under Auto"]
    got = roadmap.ranked(RANKED)
    assert [i.title for i, _ in got] == [
        "Handoff between backends",          # named first in what's left
        "Outcomes from real use",            # moves the score
        "A skill reader",                    # a release stage
        "Engine manifests refreshed",        # another section
        "A daily digest", "Score every change",   # the builder: the score words don't lift it
        "A late thing"]                      # after the release
    why = dict((i.title, w) for i, w in got)
    assert "named #1" in why["Handoff between backends"]
    assert "moves the score" in why["Outcomes from real use"]
    assert "part of the next release (Stages 1–2)" in why["A skill reader"]
    assert "self-build machinery" in why["A daily digest"]
    assert "after the release" in why["A late thing"]


def test_the_loop_takes_the_item_worth_most_and_says_why():
    it, why = selfloop.pick(RANKED)
    assert it.title == "Handoff between backends" and "named #1 in what's left for the release" in why
    assert selfloop.get(it.id).why == why                                 # kept, for its thread and the board
    selfloop.update(it.id, state="done")
    it, why = selfloop.pick(RANKED)
    assert it.title == "Outcomes from real use" and "moves the score" in why
    fault = selfloop.add("fault", "Fix KeyError", "the traceback", key="KeyError in eki/x.py:f")
    it, why = selfloop.pick(RANKED)
    assert it.id == fault.id and selfloop.get(fault.id).why == "a fault in eki's own code"


def test_the_note_says_so_when_the_week_went_into_the_builder():
    week = [{"title": "merge queue retries", "files": ["eki/selfengine.py", "tests/test_selfloop.py"]},
            {"title": "ticks", "files": ["eki/roadmap.py", "docs/self-build.md"]},
            {"title": "handoff", "files": ["eki/handoff.py", "eki/engine.py"]},
            {"title": "a docs fix", "files": ["README.md"]}]                # no code: not counted
    share = selfloop.builder_share(week)
    assert (share["changes"], share["on_the_builder"], share["share"]) == (3, 2, 0.67)
    assert share["titles"] == ["merge queue retries", "ticks"]
    line = selfloop.builder_line(share)
    assert "2 of 3 changes" in line and "only worth it if the rest moves" in line
    assert selfloop.builder_line(selfloop.builder_share(week[1:])) == ""   # half isn't most
    assert selfloop.builder_line(selfloop.builder_share(week[:1])) == ""   # one change says little
    assert selfloop.on_the_builder(["eki/selfloop.py", "eki/engine.py"]) is False   # a tie isn't most


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
    first = next(i for i in selfloop.items() if i.source == "roadmap")
    assert "*Picked because: worth most for the next release" in eng.store.turns(first.conversation)[0]["content"]
    told = Agent.seen[-1][1]
    assert "the image edits" in told and "ITEM: done" in told and "Read ROADMAP.md first" in told
    text = (eng.root / "ROADMAP.md").read_text()                            # ticked: in your checkout
    assert "- [x] **Commit the working tree:** the image edits. *(eki: self/" in text
    assert git(eng.root, "log", "-1", "--format=%s").startswith("roadmap: tick “Commit the working tree”")
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
async def test_a_tick_survives_your_roadmap_moving_on_before_you_apply(eng):
    eng.self_on(True)
    Agent.edits = {"README.md": "eki, committed\n"}
    Agent.answers = ["Committed.\nITEM: done"]
    await turn(eng)
    first = next(i for i in selfloop.items() if i.source == "roadmap")
    cid = first.change
    assert selfwork.change(cid)["state"] == "proposed"
    (eng.root / "ROADMAP.md").write_text(PLAN.replace("the image edits.", "the image edits and the icon."))
    git(eng.root, "commit", "-qam", "yours: the roadmap reworded")
    got = await eng.self_apply(cid)
    assert got["state"] == "applied"                                     # no conflict left to resolve
    text = (eng.root / "ROADMAP.md").read_text()
    assert f"- [x] **Commit the working tree:** the image edits and the icon. *(eki: self/{cid})*" in text
    assert selfloop.get(first.id).state == "done"
    selfloop.remove(first.id)                                            # even with its item gone
    Agent.answers = ["Nothing to do here.\nITEM: person"]
    await turn(eng)
    assert "CLAUDE.md imports it" in Agent.seen[-1][1]                   # the next one, not it again
    await eng.runner.stop()


def test_an_old_copy_of_a_ticked_line_gets_its_tick_back_the_rest_kept():
    key = roadmap.parse(PLAN)[0].key
    ticked = roadmap.tick(PLAN, key, roadmap.mark("c1"))
    old_copy = PLAN.replace("## Not planned", "- [ ] **A new item.** Added.\n\n## Not planned")
    kept = roadmap.keep_ticks(ticked, old_copy)
    assert "- [x] **Commit the working tree:** the image edits. *(eki: self/c1)*\n" in kept
    assert "- [ ] **A new item.** Added." in kept and "- [x] **One skill store.**" in kept
    assert roadmap.keep_ticks(ticked, kept) == kept
    assert selfwork.asks_to_untick("Update ROADMAP.md: untick 'Commit the working tree'")
    assert not selfwork.asks_to_untick("Update ROADMAP.md: tick 'Commit the working tree'")
    assert not selfwork.asks_to_untick("a change must never untick the item it didn't finish")


async def _landed_and_ticked(eng):
    """"Commit the working tree", landed and ticked by the merge queue."""
    eng.self_on(True)
    Agent.edits = {"README.md": "eki, committed\n"}
    Agent.answers = ["Committed.\nITEM: done"]
    await turn(eng)
    first = next(i for i in selfloop.items() if i.source == "roadmap")
    assert (await eng.self_apply(first.change))["state"] == "applied"
    assert roadmap.find((eng.root / "ROADMAP.md").read_text(), first.key).done
    return first


@pytest.mark.asyncio
async def test_a_roadmap_only_change_carrying_an_old_ticked_line_lands_with_the_tick(eng):
    first = await _landed_and_ticked(eng)
    # a docs change written from an older copy of the file: the tick is gone from it
    old_copy = PLAN.replace("## Not planned", "- [ ] **A new item.** Added.\n\n## Not planned")
    Agent.edits, Agent.answers = {"ROADMAP.md": old_copy}, ["Added an item."]
    started = await eng.self_ask("Update ROADMAP.md only (no code): add 'A new item'")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfloop.get(started["item"]).change
    assert selfwork.change(cid)["state"] == "proposed"
    assert (await eng.self_apply(cid))["state"] == "applied"
    text = (eng.root / "ROADMAP.md").read_text()
    assert roadmap.find(text, first.key).done and roadmap.mark(first.change) in text
    assert "- [ ] **A new item.** Added." in text
    assert not git(eng.root, "log", "-1", "--format=%s").startswith("roadmap:")   # fixed in the change itself
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_tick_taken_back_in_your_checkout_is_put_back_and_the_item_not_taken(eng):
    first = await _landed_and_ticked(eng)
    # landed some other way (merged by hand, a conflict resolved to the older side)
    (eng.root / "ROADMAP.md").write_text(PLAN.replace("## Not planned", "- [ ] **A new item.**\n\n## Not planned"))
    git(eng.root, "commit", "-qam", "self: Update ROADMAP.md only")
    assert "Commit the working tree" not in [e["title"] for e in eng.self_view()["roadmap"]["next"]]
    Agent.answers = ["Nothing to do here.\nITEM: person"]
    await turn(eng)
    assert "CLAUDE.md imports it" in Agent.seen[-1][1]                     # the next one, not it again
    text = (eng.root / "ROADMAP.md").read_text()
    assert roadmap.find(text, first.key).done and "- [ ] **A new item.**" in text
    assert git(eng.root, "log", "-1", "--format=%s").startswith("roadmap: tick again “Commit the working tree”")
    assert eng._self_ticks() == []                                         # once is enough
    await eng.runner.stop()


def test_a_slice_ticked_by_hand_is_ticked_again_when_a_later_commit_opens_it(tmp_path, monkeypatch):
    root = forge(tmp_path)
    home = tmp_path / "self"
    monkeypatch.setattr(selfwork, "HOME", home)
    key = roadmap.parse(PLAN)[3].key
    (root / "AGENTS.md").write_text("# eki\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "self: One standing context (a slice)")
    commit = git(root, "rev-parse", "HEAD")
    selfwork.record(selfwork.Proposal(id="c1", request="x", root=str(root), commit=commit, fit=True,
                                      source="roadmap", said="partial", ticks=key), home)
    selfwork.set_state("c1", "applied", home)
    assert selfwork.write_ticks(root, home) == []                          # a slice: eki doesn't tick it
    (root / "ROADMAP.md").write_text(roadmap.tick(PLAN, key, ""))
    git(root, "commit", "-qam", "yours: tick it, it's enough")
    (root / "ROADMAP.md").write_text(PLAN)
    git(root, "commit", "-qam", "an old copy of the file")
    assert selfwork.write_ticks(root, home) == ["retick self/c1"]
    assert roadmap.find((root / "ROADMAP.md").read_text(), key).done
    assert selfwork.landed_keys(selfwork.changes(home)) == [key]


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
async def test_an_item_cut_off_after_its_change_was_judged_isnt_started_over(eng):
    eng.self_on(True)
    eng.settings = {**eng.settings, "self_parallel": 1}
    Agent.answers = ["Someone has to try it on a real trackpad.\nITEM: person"]
    await turn(eng)
    first = next(i for i in selfloop.items() if i.source == "roadmap")
    asked = len(Agent.seen)
    # as if the engine restarted between the verdict and the item following it
    c = selfwork.change(first.change)
    selfloop.update(first.id, state="working", run="gone", phase="checking",
                    open={**{k: c[k] for k in ("id", "request", "root", "base")},
                          "worktree": str(eng.root.parent / "gone"), "reason": "a real trackpad"})
    await turn(eng)
    assert len(Agent.seen) == asked                                        # no second attempt
    it = selfloop.get(first.id)
    assert it.state == "person" and it.changes == [c["id"]] and not it.open
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_run_cut_off_after_its_base_passed_doesnt_check_a_new_one(eng, monkeypatch):
    """A swap cut it off between the base check and the agent, and moved
    main: it starts again from the base that passed, without testing again."""
    passed = selfwork.git(eng.root, "rev-parse", "HEAD")
    (eng.root / "README.md").write_text("eki, moved on\n")
    git(eng.root, "commit", "-q", "-am", "landed meanwhile")
    tested = []
    monkeypatch.setattr(candidate, "check_tests", lambda where, python: tested.append(where))
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    it = selfloop.add("asked", "make VALUE two", "make VALUE two")
    it = selfloop.update(it.id, open={"base_passed": passed})
    started = await eng._self_start(it)
    await settle(eng.runs, started["run"], timeout=20)
    c = selfwork.changes()[0]
    assert c["base"] == passed and tested == []
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_carrying_on_after_a_restart_adds_no_second_message(eng, monkeypatch):
    """Every swap cuts off the changes waiting in line; carrying one on is
    the same request, not a new "Carrying on" message in the thread each time."""
    async def held(rid):
        return None
    monkeypatch.setattr(eng.runner, "submit", held)
    it = selfloop.add("asked", "make VALUE two", "make VALUE two")
    first = await eng._self_start(it)
    cid = first["conversation"]
    said = [t for t in eng.store.turns(cid) if t["role"] == "user"]
    again = await eng._self_start(selfloop.get(it.id))          # state "working": carried on
    assert again["conversation"] == cid and again["run"] != first["run"]
    assert [t for t in eng.store.turns(cid) if t["role"] == "user"] == said
    assert eng.runs.get(again["run"])["user_turn"] == eng.runs.get(first["run"])["user_turn"]
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
async def test_a_protected_change_waits_for_you_even_with_apply_and_goes_in_when_you_confirm(eng):
    eng.settings = {**eng.settings, "self_autonomy": "apply"}
    Agent.edits = {"eki/agent.py": "# what launchd runs, changed\n"}
    started = await eng.self_ask("change what launchd runs", apply=True)
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfloop.get(started["item"]).change
    c = selfwork.change(cid)
    assert c["state"] == "proposed" and c["protected"] == ["eki/agent.py"] and not eng.swaps
    said = eng.store.turns(started["conversation"])[-1]["content"]
    assert "won't apply it on its own" in said and f"eki self apply {cid}" in said
    with pytest.raises(selfwork.SelfWorkError, match="confirm"):
        await eng.self_apply(cid)                                          # not without a yes
    assert not eng.swaps
    got = await eng.self_apply(cid, confirmed=True)
    assert got["state"] == "applying" and eng.swaps[0][1] == {"self_id": cid}
    eng.self_settled({"self": cid, "state": "healthy", "merged": "merged into your checkout"})
    c = selfwork.change(cid)
    assert c["state"] == "applied" and c["applied_by"] == "you"
    await eng.runner.stop()


def _fully_checked(where, **kw):
    """The candidate check with the restart check run — a guarded change needs it."""
    return candidate.Report(str(where), [candidate.Check("tests", True, "stand-in"),
                                         candidate.Check("restart", True, "stand-in")])


@pytest.mark.asyncio
async def test_a_guarded_change_is_applied_alone_under_autonomy_apply(eng):
    eng.settings = {**eng.settings, "self_autonomy": "apply"}
    eng.self_check = _fully_checked
    Agent.edits = {"eki/steps.py": "# how steps survive a restart, better\n"}
    started = await eng.self_ask("make steps sturdier")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfloop.get(started["item"]).change
    c = selfwork.change(cid)
    assert c["state"] == "applying" and c["alone"] and c["protected"] == ["eki/steps.py"]
    # live by itself, not on the release train
    assert eng.swaps[-1][1] == {"self_id": cid} and not builds.train().get("cars")
    said = eng.store.turns(started["conversation"])[-1]["content"]
    assert "applied alone, as a guarded change" in said and "rollback undoes only it" in said
    assert any("applied alone as a guarded change" in (e.get("what") or "")
               for e in observe.entries(0, "history"))
    # the train waits while it settles; then it's applied, and the train may go
    builds.board(eng.root / "another-build", self_id="other")
    assert await eng.self_release() == {}
    eng.self_settled({"self": cid, "state": "healthy", "merged": "merged into your checkout"})
    assert selfwork.change(cid)["state"] == "applied" and not selfwork.guarded_applying()
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_hard_locked_file_in_a_guarded_change_makes_it_wait_even_under_apply(eng):
    eng.settings = {**eng.settings, "self_autonomy": "apply"}
    eng.self_check = _fully_checked
    Agent.edits = {"eki/steps.py": "# steps\n", "eki/candidate.py": "# a gentler judge\n"}
    started = await eng.self_ask("make steps sturdier, and the judge gentler")
    await settle(eng.runs, started["run"], timeout=20)
    c = selfwork.change(selfloop.get(started["item"]).change)
    assert c["state"] == "proposed" and c["locked"] == ["eki/candidate.py"] and not eng.swaps
    said = eng.store.turns(started["conversation"])[-1]["content"]
    assert "never change alone" in said
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_guarded_change_waits_for_you_when_autonomy_is_propose(eng):
    eng.self_check = _fully_checked
    Agent.edits = {"eki/workers.py": "# workers\n"}
    started = await eng.self_ask("make workers sturdier")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfloop.get(started["item"]).change
    assert selfwork.change(cid)["state"] == "proposed" and not eng.swaps
    said = eng.store.turns(started["conversation"])[-1]["content"]
    assert "a guarded change, waiting for you" in said and "only when autonomy is apply" in said
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_protected_change_you_apply_that_conflicts_is_resolved_and_applied_as_yours(eng):
    Agent.edits = {"eki/agent.py": "# the agent's way\n"}
    started = await eng.self_ask("change what launchd runs")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfloop.get(started["item"]).change
    (eng.root / "eki" / "agent.py").write_text("# your way\n")
    git(eng.root, "add", "-A")
    git(eng.root, "commit", "-qm", "yours, meanwhile")
    Agent.edits = {"eki/agent.py": "# your way — and the agent's\n"}      # the resolution
    got = await eng.self_apply(cid, confirmed=True)
    assert got["state"] == "conflicts" and got["resolving"]
    await settle(eng.runs, got["resolving"], timeout=20)
    c = selfwork.change(cid)
    assert c["state"] == "applying" and c["applied_by"] == "you" and eng.swaps[-1][1] == {"self_id": cid}
    await eng.runner.stop()


async def _conflicting_twice(eng):
    """A change of two commits — the README, then NOTES.md — and your own
    commit touching both after it, so the rebase stops on each."""
    cid = await _conflicting(eng)
    (eng.root / "NOTES.md").write_text("notes\n")
    git(eng.root, "add", "-A")
    git(eng.root, "commit", "-qm", "notes")
    c = selfwork.change(cid)
    where = selfwork.ensure_worktree(c)
    git(where, "rebase", "-q", c["base"])                                 # its base, before your commits
    (where / "NOTES.md").write_text("notes, the agent's way\n")
    (where / "eki" / "extra.py").write_text("EXTRA = 1\n")
    git(where, "add", "-A")
    git(where, "commit", "-qm", "the second commit")
    p = selfwork.Proposal(**{k: v for k, v in c.items() if k in selfwork.Proposal.__dataclass_fields__})
    p.commit = git(where, "rev-parse", "HEAD")
    selfwork.record(p)
    (eng.root / "NOTES.md").write_text("notes, your way\n")
    git(eng.root, "commit", "-qam", "yours, meanwhile, again")
    return cid


@pytest.mark.asyncio
async def test_a_change_that_conflicts_twice_is_resolved_commit_by_commit_in_one_run(eng):
    cid = await _conflicting_twice(eng)
    # the first round also edits a file that didn't conflict — the rebase only
    # goes on if eki adds that too
    Agent.per = {"conflicts in: README.md": {"README.md": "eki, your way — and the agent's\n",
                                             "eki/thing.py": "VALUE = 1  # both\n"},
                 "conflicts in: NOTES.md": {"NOTES.md": "notes, your way — and the agent's\n"}}
    got = await eng.self_apply(cid)
    run = await settle(eng.runs, got["resolving"], timeout=20)
    assert run["state"] == "done"
    told = [t for _, t, _ in Agent.seen if "resolving conflicts" in t]
    assert len(told) == 2 and "conflicts in: README.md" in told[0]
    assert "stopped again, at its commit “the second commit”" in told[1] and "NOTES.md" in told[1]
    c = selfwork.change(cid)
    assert c["state"] == "applying", c.get("why")
    assert c["resolved"]["files"] == ["NOTES.md", "README.md"]
    where = Path(c["worktree"])
    assert not selfwork.rebasing(where)
    assert git(where, "log", "--format=%s", f"{git(eng.root, 'rev-parse', 'HEAD')}..HEAD").splitlines()[0] \
        == "the second commit"
    assert (where / "README.md").read_text() == "eki, your way — and the agent's\n"
    assert (where / "NOTES.md").read_text() == "notes, your way — and the agent's\n"
    assert (where / "eki" / "thing.py").read_text() == "VALUE = 1  # both\n"
    assert git(where, "status", "--porcelain") == ""
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_later_commit_left_with_markers_puts_the_whole_change_back(eng):
    cid = await _conflicting_twice(eng)
    was = selfwork.change(cid)["commit"]
    Agent.per = {"conflicts in: README.md": {"README.md": "eki, your way — and the agent's\n"},
                 "conflicts in: NOTES.md": {}}                            # gives up on the second
    got = await eng.self_apply(cid)
    await settle(eng.runs, got["resolving"], timeout=20)
    c = selfwork.change(cid)
    assert c["state"] == "conflicts" and "conflict markers are still in NOTES.md" in c["why"]
    where = Path(c["worktree"])
    assert not selfwork.rebasing(where) and git(where, "rev-parse", "HEAD") == was
    await eng.runner.stop()


def test_continue_rebase_adds_what_the_agent_touched_and_stops_at_the_next_conflict(tmp_path):
    root = forge(tmp_path)
    git(root, "checkout", "-qb", "change")
    for name in ("README.md", "NOTES.md"):
        (root / name).write_text(f"{name}, the change's way\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", f"change {name}")
    was = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    (root / "README.md").write_text("README.md, yours\n")
    (root / "NOTES.md").write_text("NOTES.md, yours\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "yours")
    onto = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "change")
    subprocess.run(["git", "-C", str(root), "rebase", "-q", onto], capture_output=True)
    info = {"where": str(root), "onto": onto, "files": selfwork.unmerged(root), "commit": was}
    assert info["files"] == ["README.md"]
    (root / "README.md").write_text("README.md, both\n")
    (root / "eki" / "thing.py").write_text("VALUE = 2\n")                # not a conflict, touched anyway
    assert selfwork.continue_rebase(info) == ["NOTES.md"]
    assert info["step"] == "change NOTES.md" and info["resolved"] == ["README.md"]
    assert git(root, "show", "HEAD:eki/thing.py") == "VALUE = 2"
    (root / "NOTES.md").write_text("NOTES.md, both\n")
    assert selfwork.continue_rebase(info) == [] and not selfwork.rebasing(root)
    assert info["resolved"] == ["NOTES.md", "README.md"]
    assert selfwork.is_in(root, onto, "HEAD") and git(root, "status", "--porcelain") == ""


def _ticked_elsewhere(tmp_path, readme_too=False):
    """A change that ticks "Commit the working tree", and your checkout
    rewording that same entry meanwhile — their ROADMAP.md hunks conflict."""
    root = forge(tmp_path)
    key = roadmap.parse(PLAN)[0].key
    tick = (key, roadmap.mark("c1"))
    git(root, "checkout", "-qb", "change")
    (root / "README.md").write_text("eki, the change's way\n")
    roadmap.tick_file(root, *tick)
    git(root, "commit", "-qam", "the change, ticked")
    was = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    (root / "ROADMAP.md").write_text(PLAN.replace("the image edits.", "the image edits and the icon."))
    if readme_too:
        (root / "README.md").write_text("eki, yours\n")
    git(root, "commit", "-qam", "yours, meanwhile")
    onto = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "change")
    return root, tick, was, onto


def test_a_change_made_before_ticks_moved_loses_its_tick_before_it_goes_on_top(tmp_path):
    """A change whose own commit ticks its item (made before the merge queue
    wrote the ticks): the tick is taken out of it, so your rewording of the
    same entry is no conflict — the tick comes afterwards, from the writer."""
    root, tick, was, onto = _ticked_elsewhere(tmp_path)
    assert selfwork._untick_commit(root, git(root, "rev-parse", "main~1"))
    assert "- [ ] **Commit the working tree:**" in git(root, "show", "HEAD:ROADMAP.md")
    assert git(root, "show", "HEAD:README.md") == "eki, the change's way"
    got, files = selfwork._rebase(root, "-q", onto)
    assert got.returncode == 0 and files == [] and not selfwork.rebasing(root)
    assert "the image edits and the icon." in git(root, "show", "HEAD:ROADMAP.md")
    assert not selfwork._untick_commit(root, onto)                    # nothing left to take out


def test_an_agents_own_tick_is_dropped_its_other_roadmap_edits_kept(tmp_path):
    root = forge(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    key = roadmap.parse(PLAN)[0].key
    text = roadmap.tick(PLAN, key, roadmap.mark("c1")).replace("## Not planned", "## Not planned\n\nMore.")
    (root / "ROADMAP.md").write_text(text)
    assert selfwork.drop_ticks(root, base)
    after = (root / "ROADMAP.md").read_text()
    assert "- [ ] **Commit the working tree:** the image edits.\n" in after and "More." in after
    assert not selfwork.drop_ticks(root, base)
    # a request that asks for a tick keeps it; one that only mentions ticks doesn't
    assert selfwork.asks_to_tick("Update ROADMAP.md only: tick 'Roots eki starts on its own'")
    assert not selfwork.asks_to_tick("Changes no longer tick the roadmap; the merge queue writes the tick")
    assert not selfwork.asks_to_tick("tick the item", source="roadmap")


def test_only_a_change_that_finished_its_item_carries_a_tick():
    assert selfwork.tick_of({"id": "c1", "ticks": "k", "said": "done"}) == ("k", "*(eki: self/c1)*")
    assert selfwork.tick_of({"id": "c1", "ticks": "k", "said": "already"})
    assert selfwork.tick_of({"id": "c1", "ticks": "k", "said": "partial"}) is None
    assert selfwork.tick_of({"id": "c1", "ticks": "", "said": "done"}) is None


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
async def test_the_weekly_note_carries_the_restart_drill_as_it_ran(eng):
    from eki import drill
    drill.save([drill.Result("chat", "mid-answer"),
                drill.Result("resolve", "resolving", ["the thread says 'couldn't resolve'"])])
    Agent.answers = ["A quiet week.\n\n```json\n[]\n```"]
    started = await eng.self_note_now()
    await settle(eng.runs, started["run"], timeout=20)
    note = selfloop.latest_note()
    assert note["text"].startswith("A quiet week.")
    assert "**Restart drill**" in note["text"] and "1 not ok" in note["text"]
    assert "resolving" in note["text"].split("**Restart drill**")[1]      # the table, not a model's words
    assert '"restart_drill"' in Agent.seen[-1][1]                          # and the writer knew it
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_weekly_note_says_when_the_week_went_into_the_builder(eng, monkeypatch):
    now = time.time()
    week = [{"id": f"c{n}", "state": "applied", "at": now - 3600, "title": t, "files": f}
            for n, (t, f) in enumerate([("merge queue", ["eki/selfengine.py"]),
                                        ("ticks", ["eki/roadmap.py"]), ("handoff", ["eki/handoff.py"])])]
    monkeypatch.setattr(selfwork, "changes", lambda *a, **k: week)
    Agent.answers = ["A busy week.\n\n```json\n[]\n```"]
    started = await eng.self_note_now()
    await settle(eng.runs, started["run"], timeout=20)
    text = selfloop.latest_note()["text"]
    assert "went into the self-build machinery** — 2 of 3 changes" in text   # counted, not a model's words
    assert '"self_build_share"' in Agent.seen[-1][1]                       # and the writer knew it
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_restart_drill_is_the_loops_weekly_housekeeping(eng, monkeypatch):
    from eki import drill
    started = []
    monkeypatch.setattr(drill, "weekly", lambda python, code: started.append(code) or "started")
    assert await eng.self_drill_tick() == ""                               # the loop isn't on
    eng.self_on(True)
    assert await eng.self_drill_tick() == "started" and len(started) == 1
    eng.self_on(False)
    assert await eng.self_drill_tick() == ""                               # paused: nothing new
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
    assert v["roadmap"]["next"][0]["after"] == ""
    (eng.root / "ROADMAP.md").write_text(STAGED)
    nexts = {n["title"]: n["after"] for n in eng.self_view()["roadmap"]["next"]}
    assert nexts["Auto-memory lands there"] == "Remember and recall" and nexts["A memory size meter"] == ""
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
    assert not selfloop.overlaps(["note"], ["engine"])
    # an area that couldn't be told waits only for another like it (and the app, if it hints at it)
    assert selfloop.overlaps(["*"], ["*"]) and not selfloop.overlaps(["*"], ["engine"])
    assert selfloop.overlaps(["*", "mac"], ["mac"]) and not selfloop.overlaps(["*", "mac"], ["routing"])


def small_repo(tmp_path: Path) -> Path:
    root = tmp_path / "look"
    for name, text in {"mac/Swipes.swift": "// a two-finger swipe on the trackpad\n",
                       "mac/ImageViewer.swift": "// swipe between pictures\n",
                       "mac/Chat.swift": "// the window\n", "mac/Rail.swift": "// the window\n",
                       "eki/router.py": "# picks a backend; the trackpad has nothing to do with it\n",
                       "eki/engine.py": "# the engine: bananas\n", "eki/runner.py": "# runs: bananas\n",
                       "ROADMAP.md": "- [ ] Confirm the swipe fix on a real trackpad\n",
                       "tests/test_swipe.py": "# swipe trackpad\n"}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git(root.parent, "init", "-q", str(root))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def test_an_item_that_names_no_file_is_looked_up_in_the_repo(tmp_path, monkeypatch):
    root = small_repo(tmp_path)
    selfloop._looked.clear()
    assert selfloop.words_of("Confirm the hold-then-release swipe fix on a real trackpad") == \
        ["hold", "release", "swipe", "trackpad"]
    # swipe: two files in the app, one named after it; trackpad: one in the app, one in routing
    assert selfloop.area_of("Confirm the hold-then-release swipe fix on a real trackpad", {}, root) == ["mac"]
    assert selfloop.area_of("bananas everywhere", {}, root) == ["engine"]
    assert selfloop.area_of("the trackpad", {}, root) == ["mac", "routing"]              # a tie: both
    assert selfloop.area_of("tidy things up", {}, root) == ["*"]                         # still can't tell
    # a word found all over says nothing on its own — but it hints at the app
    monkeypatch.setattr(selfloop, "DISTINCT", 1)
    assert selfloop.area_of("the window", {}, root) == ["*", "mac"]
    assert selfloop.area_of("tidy things up") == ["*"]                                  # no repo: no look


def test_an_item_whose_area_cant_be_told_goes_beside_the_rest():
    engine = selfloop.add("asked", "the runner", "the engine's runner, faster")
    vague = selfloop.add("asked", "tidy up", "tidy things up")
    vaguer = selfloop.add("asked", "polish", "polish it")
    assert selfloop.pick("", parallel=3)[0].id == engine.id
    selfloop.update(engine.id, state="working", run="r1")
    it, _ = selfloop.pick("", parallel=3, live=["r1"])
    assert it.id == vague.id and it.area == ["*"]                         # it doesn't wait for the engine
    selfloop.update(vague.id, state="working", run="r2")
    got, why = selfloop.pick("", parallel=3, live=["r1", "r2"])           # but two unknowns don't go together
    assert got is None and "“polish” waits" in why and "somewhere it couldn't tell" in why
    selfloop.update(vague.id, state="done")
    assert selfloop.pick("", parallel=3, live=["r1"])[0].id == vaguer.id


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


def test_the_merge_queue_keeps_the_order_changes_finished_in():
    assert selfloop.merge_join("a", run="ra") == 0
    assert selfloop.merge_join("b", run="rb") == 1
    assert selfloop.merge_join("c", person=True) == 2
    assert selfloop.merge_join("a", run="ra2", now=True) == 0            # carried on: keeps its place
    assert selfloop.merge_row("a")["run"] == "ra2" and selfloop.merge_row("a")["now"]
    assert selfloop.merge_row("c")["person"] and not selfloop.merge_row("b")["person"]
    selfloop.merge_leave("a")
    assert selfloop.merge_row("a") is None
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
async def test_finished_changes_wait_for_the_train_which_merges_them_as_a_tree(eng, monkeypatch):
    import asyncio
    from eki import selfengine, treemerge
    monkeypatch.setattr(selfengine, "MERGE_POLL", 0.05)
    builds._save_train({"cars": [], "last": int(time.time())})          # a train just left
    # the resolver is asked first: its brief names both changes
    Agent.per = {"resolving a merge": {"eki/thing.py": "VALUE = 5\n"},
                 "twenty": {"eki/thing.py": "VALUE = 20\n"}, "thirty": {"eki/thing.py": "VALUE = 30\n"}}
    one = await eng.self_ask("make VALUE twenty", apply=True)
    two = await eng.self_ask("make VALUE thirty", apply=True)
    for _ in range(400):
        items = [selfloop.get(x["item"]) for x in (one, two)]
        if all(i.phase == "merging" for i in items) and len(selfloop.merge_queue()) == 2:
            break
        await asyncio.sleep(0.05)
    assert all(i.state == "working" and i.phase == "merging" for i in items)   # in line: waiting, not failed
    assert selfloop.pick("", parallel=1, live=eng.runner.running)[1].startswith("nothing to do")  # holds no room
    assert [r["live"] for r in eng.self_view()["merging"]] == [True, True]
    await asyncio.sleep(0.3)
    assert {selfwork.change(i.change)["state"] for i in items} == {"proposed"}   # not before the train
    builds._save_train({"cars": [], "last": 0})                         # the train's time
    for x in (one, two):
        assert (await settle(eng.runs, x["run"], timeout=30))["state"] == "done"
    got = [selfwork.change(i.change) for i in items]
    assert {c["state"] for c in got} == {"applying"} and got[0]["build"] == got[1]["build"]   # one release
    assert "in one batch" in got[0]["how"] and not selfloop.merge_queue()
    assert len(eng.swaps) == 1                                          # it went live once, carrying both
    tree = eng.self_view()["tree"]
    [[pair]] = tree["rounds"]                                           # one round, one pair
    assert pair["state"] == "resolved" and {*pair["a"], *pair["b"]} == {i.change for i in items}
    assert tree["groups"][0]["files"] == ["eki/thing.py"] and tree["landed"]
    told = next(m for _, m, _ in Agent.seen if "resolving a merge" in m)
    assert "make VALUE twenty" in told and "make VALUE thirty" in told  # what both sides are for
    batch = treemerge.state()["batch"]
    assert git(eng.root, "show", f"{batch}:eki/thing.py") == "VALUE = 5"
    assert any("1 other change waits to land too" in eng.runs.get(x["run"])["output"] for x in (one, two))
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


# ---- roots eki starts on its own ---------------------------------------------------------------

def test_what_eki_starts_on_its_own_isnt_the_persons():
    fault = selfloop.Item(id="f", source="fault", title="Fix x")
    assert selfloop.owner(fault) == "eki"
    assert selfloop.owner(selfloop.Item(id="a", source="asked", title="t")) == "person"
    assert selfloop.owner(selfloop.Item(id="r", source="roadmap", title="t")) == "person"  # your goal's turn
    assert selfloop.owner(selfloop.Item(id="b", source="asked", title="t", by="eki")) == "eki"


def _eki_thread(eng) -> str:
    """A thread whose run is a fault's fix, working."""
    cid = eng.store.new_conversation("eki · Fix x")
    eng.runs.create("[eki · self] Fix x", conversation=cid, payload=json.dumps({"owner": "eki"}))
    return cid


@pytest.mark.asyncio
async def test_a_faults_fix_follows_the_autonomy_setting_even_if_marked_apply(eng):
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    it = selfloop.add("fault", "Fix thing", "VALUE is wrong", check_base=False, apply=True)
    started = await eng._self_start(it)
    assert json.loads(eng.runs.get(started["run"])["payload"])["owner"] == "eki"
    await settle(eng.runs, started["run"], timeout=20)
    assert selfwork.changes()[0]["state"] == "proposed" and not eng.swaps     # propose: not applied
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_below_eki_s_own_work_everything_is_eki_s_and_the_checkout_is_off_limits(eng, monkeypatch):
    monkeypatch.setattr(builds, "source", lambda: eng.root)
    parent = _eki_thread(eng)
    assert eng.owner_of(parent) == "eki" and eng.owner_of("") == "person"
    with pytest.raises(ValueError):
        await eng.ask("fix the supervisor", repo=str(eng.root / "eki"), via="agent", parent_thread=parent)
    with pytest.raises(ValueError):
        await eng.ask("tidy the builds", repo=str(builds.BUILDS), via="agent", parent_thread=parent)
    child = await eng.ask("review this", via="agent", parent_thread=parent)
    got = json.loads(eng.runs.get(child["run"])["payload"])
    assert got["owner"] == "eki" and got["parent_thread"] == parent
    await settle(eng.runs, child["run"], timeout=20)
    # yours: the same folder is fine
    mine = await eng.ask("fix the supervisor", repo=str(eng.root), via="agent", parent_thread="")
    assert "owner" not in json.loads(eng.runs.get(mine["run"])["payload"] or "{}")
    await settle(eng.runs, mine["run"], timeout=20)
    # a change asked for from inside it is eki's too: never applied on its say-so
    got = await eng.self_ask("make VALUE three", when="later", apply=True, parent=parent)
    it = selfloop.get(got["item"])
    assert it.by == "eki" and not it.apply and selfloop.owner(it) == "eki"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_eki_s_tools_say_which_thread_asks(eng):
    from eki import mcpbridge
    parent = _eki_thread(eng)
    bridge = mcpbridge.Bridge(eng, parent, depth=1)
    await bridge.ask("say hi")
    child = next(r for r in eng.runs.recent(5) if r["conversation_id"] != parent)
    assert json.loads(eng.runs.get(child["id"])["payload"])["owner"] == "eki"
    assert eng._harness_env(parent)["EKI_PARENT"] == parent and eng._harness_env("") is None
    await eng.runner.stop()


def test_codex_s_eki_server_is_told_the_thread(tmp_path, monkeypatch):
    from eki import mcpregistry
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(mcpregistry, "CODEX_CONFIG", cfg)
    assert mcpregistry.codex_eki_env(parent="abc") == []                    # no eki server: nothing to change
    cfg.write_text(mcpregistry.codex_block({}, ["python", "-m", "eki.cli", "mcp"]))
    assert mcpregistry.codex_eki_env() == []
    flags = mcpregistry.codex_eki_env(screen=False, parent="abc")
    assert 'EKI_PARENT = "abc"' in flags[1] and 'EKI_SCREEN = "0"' in flags[1]
    assert "EKI_SCREEN" not in mcpregistry.codex_eki_env(parent="abc")[1]


def test_the_persons_switches_are_refused_to_eki_s_own_work(monkeypatch):
    from fastapi.testclient import TestClient
    from eki import service

    class Fake:
        def owner_of(self, cid):
            return "eki" if cid == "mine-not" else "person"

        def self_settings(self, **kw):
            return {"ok": True, **kw}

    monkeypatch.setitem(service.STATE, "engine", Fake())
    client = TestClient(service.app)
    assert service.person_only("POST", "/api/self/changes/abc/apply")
    assert not service.person_only("POST", "/api/self/changes/abc/discard")
    r = client.put("/api/self/settings", json={"autonomy": "apply"}, headers={"X-Eki-Parent": "mine-not"})
    assert r.status_code == 403 and "only you" in r.json()["detail"]
    assert client.put("/api/self/settings", json={"autonomy": "apply"},
                      headers={"X-Eki-Parent": "yours"}).status_code == 200
    assert client.put("/api/self/settings", json={"autonomy": "apply"}).status_code == 200


def test_the_command_line_says_which_thread_asks(monkeypatch):
    from eki import cli
    monkeypatch.delenv("EKI_PARENT", raising=False)
    assert cli.parent_headers() == {}
    monkeypatch.setenv("EKI_PARENT", "abc")
    assert cli.parent_headers() == {"X-Eki-Parent": "abc"}


@pytest.mark.asyncio
async def test_applying_shows_each_real_stage_the_go_live_and_a_timeline(eng, monkeypatch):
    from eki import pipeline
    Agent.edits = {"eki/thing.py": "VALUE = 2\n"}
    started = await eng.self_ask("make VALUE two")
    await settle(eng.runs, started["run"], timeout=20)
    cid = selfwork.changes()[0]["id"]
    (eng.root / "README.md").write_text("eki, moved on\n")
    git(eng.root, "commit", "-qam", "yours, meanwhile")
    assert (await eng.self_apply(cid))["state"] == "applying"
    # the train left at once (the first after a quiet window): the supervisor has it
    build = builds.train()["departed"]["build"]
    (builds.SELF_HOME / "swap.json").write_text(json.dumps(
        {"state": "waiting", "target": build, "self": cid, "at": int(time.time()),
         "deadline": int(time.time()) + 30}))
    monkeypatch.setattr(builds, "_swap_pid", lambda: 4242)
    v = eng.self_view()
    row = next(r for r in v["pipeline"] if r["id"] == cid)
    assert row["text"] == "going live" and row["live"]
    assert v["golive"]["last"]["carrying"][0]["id"] == cid and v["golive"]["last"]["stage"] == "going live"
    assert next(c for c in v["changes"] if c["id"] == cid)["stage"]["label"] == "Going live"
    # your apply takes its turn on the release train (alone on it: rebased, as ever)
    assert [e["event"] for e in pipeline.events(cid)] == ["queued", "turn", "rebasing", "rebased", "checking",
                                                          "rechecked", "landed", "leaving"]
    eng.self_settled({"state": "healthy", "target": build, "cars": [cid]})
    got = eng.self_stage(cid)
    assert got["stage"]["text"] == "live"
    assert [r["step"] for r in got["timeline"]] == ["queued", "rebased", "rechecked", "landed",
                                                    "left to go live", "live"]
    await eng.runner.stop()


# ---- what landed is closed, whichever way it landed -------------------------------------------

def test_a_request_names_a_roadmap_item_by_its_whole_title_in_quotes():
    key = roadmap.parse(PLAN)[3].key                                   # One standing context
    assert roadmap.named(PLAN, "Bring 'One standing context' up to date and land it") == key
    assert roadmap.named(PLAN, "Finish “One standing context.” today") == key
    assert roadmap.named(PLAN, "make one standing context faster") == ""        # not quoted: in passing
    assert roadmap.named(PLAN, "Redo 'One standing' please") == ""               # half a title
    assert roadmap.named(PLAN, "Do 'One standing context' and 'Commit the working tree'") == ""   # two
    asked = {"id": "c9", "source": "asked", "request": "Bring 'One standing context' up to date and land it. "
             "Then tick the ROADMAP item."}
    assert selfwork.tick_of(asked, PLAN) == (key, "*(eki: self/c9)*")
    assert selfwork.tick_of(asked) is None                              # without the file, nothing to name
    assert selfwork.tick_of({**asked, "files": ["ROADMAP.md"]}, PLAN) is None
    assert selfwork.tick_of({**asked, "request": "Add 'One standing context' to ROADMAP.md"}, PLAN) is None
    assert selfwork.tick_of({**asked, "request": "Untick 'One standing context'"}, PLAN) is None
    assert selfwork.tick_of({**asked, "source": "fault"}, PLAN) is None
    assert selfwork.landed_keys([{**asked, "state": "applied"}], PLAN) == [key]
    assert selfwork.landed_keys([{**asked, "state": "proposed"}], PLAN) == []


def test_everything_a_landed_change_satisfies_is_closed_and_nothing_else():
    first, *_ = roadmap.parse(PLAN)
    item = selfloop.add("roadmap", first.title, key=first.key)
    selfloop.update(item.id, attempts=1, note="its run failed")        # put back by a run that failed after
    redo = selfloop.add("asked", "redo", request="Bring 'Commit the working tree' up to date and land it")
    gave_up = selfloop.add("roadmap", "One standing context", key=roadmap.parse(PLAN)[3].key, state="gave up")
    landed = {"id": "c1", "item": item.id, "state": "applied", "said": "done", "ticks": first.key,
              "state_at": int(time.time()) + 5}
    closed = selfloop.close_landed([(landed, first.key)], PLAN)
    assert len(closed) == 2 and all("self/c1 landed" in c for c in closed)
    assert selfloop.get(item.id).state == "done" and selfloop.get(redo.id).state == "done"
    assert selfloop.get(redo.id).note == "landed as self/c1"
    assert selfloop.get(gave_up.id).state == "gave up"                  # another item: untouched
    # a redo asked for after it landed is yours: left alone
    again = selfloop.add("asked", "again", request="Bring 'Commit the working tree' up to date and land it")
    assert selfloop.close_landed([({**landed, "state_at": again.created_at - 1}, first.key)], PLAN) == []
    assert selfloop.get(again.id).state == "queued"


def test_a_slice_put_back_is_left_but_a_ticked_line_closes_its_item():
    _, _, _, standing = roadmap.parse(PLAN)
    item = selfloop.add("roadmap", standing.title, key=standing.key, note="a first slice landed")
    part = {"id": "c2", "item": item.id, "state": "applied", "said": "partial", "ticks": standing.key}
    assert selfloop.close_landed([(part, "")], PLAN) == []
    assert selfloop.get(item.id).state == "queued"                      # more to do: taken again
    ticked = roadmap.tick(PLAN, standing.key, "")
    assert selfloop.close_landed([(part, "")], ticked) == ["“One standing context” done — ticked in ROADMAP.md"]
    assert selfloop.get(item.id).state == "done"


def _stale(eng, title="Commit the working tree"):
    """A roadmap item left waiting — its second attempt, say — and a redo
    you asked for later, both for the same item."""
    entry = next(e for e in roadmap.parse(PLAN) if e.title == title)
    it = selfloop.by_key(entry.key, "roadmap") or selfloop.add("roadmap", entry.title, key=entry.key)
    it = selfloop.update(it.id, state="queued", attempts=1, note="an earlier attempt failed")
    redo = selfloop.add("asked", "redo", request=f"Bring '{title}' up to date and land it", state="gave up")
    return entry, it, redo


def _ticks(eng):
    return [s for s in git(eng.root, "log", "--format=%s").splitlines() if s.startswith("roadmap: tick")]


@pytest.mark.asyncio
async def test_the_loop_landing_an_item_closes_every_item_for_it(eng):
    eng.self_on(True)
    eng.settings = {**eng.settings, "self_autonomy_areas": {"ROADMAP.md": "apply", "README.md": "apply"}}
    entry, it, redo = _stale(eng)
    Agent.edits, Agent.answers = {"README.md": "eki, committed\n"}, ["Committed.\nITEM: done"]
    await turn(eng)
    c = selfwork.change(selfloop.get(it.id).change)
    assert c["state"] == "applied"
    assert selfloop.get(it.id).state == "done" and selfloop.get(redo.id).state == "done"
    assert roadmap.find((eng.root / "ROADMAP.md").read_text(), entry.key).done and len(_ticks(eng)) == 1
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_merge_train_landing_a_redo_ticks_and_closes_the_roadmap_item(eng):
    eng.settings = {**eng.settings, "self_autonomy": "apply"}
    entry, it, _ = _stale(eng, "One standing context")
    Agent.edits = {"README.md": "eki, standing context\n"}
    got = await eng.self_ask("Bring 'One standing context' up to date and land it", apply=True)
    assert (await settle(eng.runs, got["run"], timeout=30))["state"] == "done"
    cid = selfloop.get(got["item"]).change
    assert selfwork.change(cid)["state"] == "applied"                     # documentation: the train merged it
    text = (eng.root / "ROADMAP.md").read_text()
    assert roadmap.find(text, entry.key).done and roadmap.mark(cid) in text and len(_ticks(eng)) == 1
    assert selfloop.get(it.id).state == "done" and selfloop.get(it.id).note == f"landed as self/{cid}"
    assert entry.title not in [e["title"] for e in eng.self_view()["roadmap"]["next"]]
    assert eng._self_ticks() == []                                        # once
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_your_apply_closes_every_item_for_it(eng):
    eng.self_on(True)
    Agent.edits, Agent.answers = {"README.md": "eki, committed\n"}, ["Committed.\nITEM: done"]
    await turn(eng)
    first = next(i for i in selfloop.items() if i.source == "roadmap")
    cid = first.change
    entry, it, redo = _stale(eng)                                         # the item put back while it waited
    assert it.id == first.id
    assert (await eng.self_apply(cid))["state"] == "applied"
    assert selfloop.get(it.id).state == "done" and selfloop.get(redo.id).state == "done"
    assert len(_ticks(eng)) == 1
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_state_put_right_by_hand_is_swept_when_the_engine_starts(eng):
    root = eng.root
    entry, it, redo = _stale(eng)
    selfwork.record(selfwork.Proposal(id="c7", request="x", root=str(root), commit=git(root, "rev-parse", "HEAD"),
                                      fit=True, source="roadmap", said="done", ticks=entry.key, item=it.id))
    selfwork.set_state("c7", "applied", how="merged by hand")            # put right by hand, no engine watching
    standing = roadmap.parse(PLAN)[3]
    other = selfloop.add("roadmap", standing.title, key=standing.key)
    (root / "ROADMAP.md").write_text(roadmap.tick(PLAN, standing.key, ""))  # and one you ticked yourself
    git(root, "commit", "-qam", "yours: tick it")
    assert eng.self_settle() == ["tick self/c7"]
    assert {selfloop.get(x.id).state for x in (it, redo, other)} == {"done"}
    assert roadmap.find((root / "ROADMAP.md").read_text(), entry.key).done
    # and the board never shows a ticked item waiting, even before the loop looks again
    left = selfloop.add("roadmap", "Commit the working tree", key=entry.key)
    view = eng.self_view()
    assert view["queue"] == [] and view["roadmap"]["next"] == [] and selfloop.get(left.id).state == "done"
    await eng.runner.stop()
