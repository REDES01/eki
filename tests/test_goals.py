# SPDX-License-Identifier: Apache-2.0
"""A goal is a sentence left running: turns in its own thread when the machine
has room, within what it may spend, until it says it's done — or every day."""
import asyncio
import time

import pytest

from eki import goals, shift, watch
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from eki.quota.base import Reading, Window
from tests.test_runs import echo_config, settle


# ---- the goal itself ------------------------------------------------------------------------

def test_a_goal_is_a_sentence_and_when(tmp_path):
    g = goals.create("Make 20 NPCs for Ashfall", folder=str(tmp_path))
    assert g.when == {"kind": "once"} and g.state == "active" and goals.due(g, time.time())
    d = goals.create("Every morning propose 3 posts", when={"kind": "daily", "at": "8:30"})
    assert d.repeats and goals.describe(d.when) == "every day at 8:30"
    assert goals.due(d, time.time())                                          # its first turn right away
    with pytest.raises(goals.GoalError):
        goals.create("   ")
    with pytest.raises(goals.GoalError, match="15 minutes"):
        goals.create("x", when={"kind": "interval", "minutes": 5})
    with pytest.raises(goals.GoalError, match="no folder"):
        goals.create("x", folder=str(tmp_path / "missing"))
    assert goals.get(g.id[:4]).id == g.id                                    # by the start of its id


def test_the_one_line_protocol():
    assert goals.outcome("Made 4 of them.\n\nGOAL: continue") == "continue"
    assert goals.outcome("All 20 are there.\n**GOAL: done**") == "done"
    assert goals.outcome("Which style — painterly or pixel?\nGOAL: waiting") == "waiting"
    assert goals.outcome("no line at all") == "continue"


def test_after_a_turn():
    once = goals.create("Make 20 NPCs")
    once = goals.update(once.id, turns=1)
    assert goals.after_turn(once, "4 made.\nGOAL: continue", True).state == "active"
    assert goals.after_turn(once, "Painterly or pixel?\nGOAL: waiting", True).state == "waiting"
    assert goals.after_turn(goals.update(once.id, state="active"), "All there.\nGOAL: done", True).state == "done"
    daily = goals.create("Propose 3 posts", when={"kind": "daily", "at": "09:00"})
    after = goals.after_turn(daily, "1… 2… 3…\nGOAL: done", True, now=1_000_000)
    assert after.state == "active" and after.next_at > 1_000_000                # tomorrow, not done for good
    failing = goals.create("x")
    for _ in range(goals.MAX_FAILURES):
        failing = goals.after_turn(failing, "", False)
    assert failing.state == "stuck"
    long = goals.update(goals.create("y").id, turns=goals.MAX_TURNS)
    assert goals.after_turn(long, "still going", True).state == "stuck"


def test_the_prompt_says_the_goal_and_the_line_to_end_with():
    g = goals.create("Make 20 NPCs")
    first = goals.prompt(g)
    assert first.startswith("[eki · goal] Make 20 NPCs") and "GOAL: done" in first
    assert goals.prompt(goals.update(g.id, turns=1)).startswith("[eki · goal] Carry on")
    daily = goals.create("Propose 3 posts", when={"kind": "daily", "at": "09:00"})
    assert "this time's run" in goals.prompt(daily)


def test_a_reply_after_the_goal_asked_counts():
    g = goals.update(goals.create("x").id, last_turn=5)
    turns = [{"id": 5, "role": "user", "content": "[eki · goal] x"},
             {"id": 6, "role": "assistant", "content": "which?\nGOAL: waiting"}]
    assert not goals.replied(g, turns)
    assert goals.replied(g, turns + [{"id": 7, "role": "user", "content": "the painterly one"}])


# ---- in the engine -----------------------------------------------------------------------------

class Local(Backend):
    """The model on this machine: works, says where things stand."""
    seen, script, delay = [], [], 0.0

    async def health(self):
        return Health(True)

    systems = []

    async def stream(self, messages, **kw):
        Local.seen.append((self.info.key, messages[-1].content, kw.get("cwd")))
        Local.systems.append(" ".join(m.content for m in messages if m.role == "system"))
        if Local.delay:
            await asyncio.sleep(Local.delay)
        yield Local.script.pop(0) if Local.script else "Did some.\nGOAL: continue"


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
    cfg.backends.append(BackendInfo(key="codex-qwen", kind="codex", label="Qwen with Codex's hands",
                                    capabilities=Capabilities(tools=True, repo=True), cost=Cost(tier=0)))
    cfg.options["codex-qwen"] = {"binary": "/bin/echo", "local_model": "qwen"}
    watch.save({"vendors": {"claude_code": {"vendor": "Anthropic", "ladder": {"default": "opus"}}}})
    monkeypatch.setattr(adapters, "build", lambda info, opts: Local(info, opts))
    e = Engine(cfg, owner=True)
    e.settings = {**e.settings, "skills_learn": "off", "notify_learned": False, "skills_local": False,
                  "worktrees": False, "notify_goals": False}
    monkeypatch.setattr(e, "_lives", lambda b: False)
    e.router.is_up = lambda k: True
    monkeypatch.setattr(shift, "check", lambda **kw: shift.Gate(True))
    monkeypatch.setattr(shift, "must_stop", lambda *a: shift.Gate(True))
    Local.seen, Local.script, Local.delay, Local.systems = [], [], 0.0, []
    return e


async def turn(e):
    state = await e.shift_tick()
    if e._shift_run:
        await settle(e.runs, e._shift_run, timeout=5)
        e._goal_finished()                                        # how it ended, as the next tick would
    return state


@pytest.mark.asyncio
async def test_a_goal_gets_turns_in_its_own_thread_until_it_says_done(eng):
    g = goals.create("Write a haiku about snow, then one about rain")
    Local.script = ["Snow on the rails…\nGOAL: continue", "Rain on the rails…\nGOAL: done"]
    state = await turn(eng)
    assert state["state"] == "working" and Local.seen[0][0] == "qwen"          # writing: the local model
    g = goals.get(g.id)
    assert g.turns == 1 and g.state == "active" and g.conversation
    await turn(eng)
    g = goals.get(g.id)
    assert g.state == "done" and g.turns == 2
    assert Local.seen[1][1].startswith("[eki · goal] Carry on")
    thread = eng.store.turns(g.conversation)
    assert [t["role"] for t in thread] == ["user", "assistant", "user", "assistant"]
    assert (await eng.shift_tick())["why"] == "nothing due"
    assert eng.goals_report()["finished"] == 1
    await eng.runner.stop()


def test_useful_local_work_is_measured_by_the_day(eng):
    """Stage 3's measure: hours a day finished runs kept this Mac's models busy,
    against the 1% before the idle shift — subscriptions and failures left out."""
    now = time.mktime(time.strptime("2026-09-24 12:00", "%Y-%m-%d %H:%M"))
    yesterday = now - 86400

    def ran(backend, start, seconds, state="done"):
        rid = eng.runs.create("x", requested=backend)
        eng.runs.update(rid, backend=backend, state=state, started_at=int(start), ended_at=int(start + seconds))

    ran("qwen", yesterday, 3 * 3600)
    ran("codex-qwen", now - 3600, 1800)
    ran("qwen", now - 7200, 3600, state="failed")                 # not useful
    ran("claude_code", now - 7200, 3600)                           # not local
    ran("qwen", now - 8 * 86400, 3600)                             # before the week
    w = eng.local_work(now=now)
    assert w["local_hours"] == 3.5 and w["baseline_share"] == 0.01
    assert [d["hours"] for d in w["by_day"]] == [0, 0, 0, 0, 0, 3.0, 0.5]
    assert w["by_day"][-1]["day"] == "2026-09-24"
    span = 6 * 86400 + 12 * 3600                                   # six days and this morning
    assert w["share"] == round(3.5 * 3600 / span, 4) and w["hours_a_day"] == round(3.5 * 86400 / span, 2)
    assert "local_work" in eng.goals_view() and "local_work" in eng.goals_report()


def test_the_command_line_says_the_measure():
    from eki.cli import _local_work_line
    line = _local_work_line({"days": 7, "hours_a_day": 3.4, "share": 0.1417, "baseline_share": 0.01})
    assert line == ("local models, last 7 days: 3.4 h of useful work a day "
                    "(14.2% of the time; 1% before the idle shift)")


@pytest.mark.asyncio
async def test_work_that_needs_tools_stays_local_unless_the_goal_may_spend(eng, tmp_path):
    g = goals.create("Fix the failing test in this repo", folder=str(tmp_path))
    await turn(eng)
    assert Local.seen[-1][0] == "codex-qwen"                               # local, with hands
    assert Local.seen[-1][2] == str(tmp_path)
    eng.goals_remove(g.id)
    spend = goals.create("Fix the failing test in this repo", folder=str(tmp_path), spare=True)
    eng.quota.latest["claude"] = Reading("claude", [Window("five_hour", "5H", 0.9)])
    await turn(eng)
    assert Local.seen[-1][0] == "codex-qwen"                               # Claude had no spare room
    eng.goals_remove(spend.id)                                             # (its thread now stays local)
    eng.quota.latest["claude"] = Reading("claude", [Window("five_hour", "5H", 0.1)])
    goals.create("Fix the failing test in this repo", folder=str(tmp_path), spare=True)
    await turn(eng)
    assert Local.seen[-1][0] == "claude_code"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_waiting_for_you_then_carrying_on_after_your_reply(eng):
    g = goals.create("Make 20 NPCs")
    Local.script = ["Painterly or pixel?\nGOAL: waiting", "Painterly it is.", "Made 5.\nGOAL: continue"]
    await turn(eng)
    assert goals.get(g.id).state == "waiting"
    assert (await eng.shift_tick())["why"] == "nothing due"
    s = await eng.ask("painterly", conversation=goals.get(g.id).conversation)
    await settle(eng.runs, s["run"], timeout=5)
    await turn(eng)                                                         # your reply wakes it
    assert goals.get(g.id).state == "active" and goals.get(g.id).turns == 2
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_your_request_comes_first_and_a_paused_goal_waits(eng):
    g = goals.create("Make 20 NPCs")
    Local.delay = 2.0
    await eng.shift_tick()
    rid = eng._shift_run
    await asyncio.sleep(0.2)
    Local.delay = 0.0
    await eng.ask("hello")
    assert (await settle(eng.runs, rid, timeout=5))["state"] == "cancelled"
    eng.goals_update(g.id, state="paused")
    assert (await eng.shift_tick())["why"] == "nothing due"
    assert goals.get(g.id).failures == 0                                    # stepping aside isn't failing
    assert eng.goals_view()["goals"][0]["status"] == "paused"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_off_and_no_room(eng, monkeypatch):
    goals.create("Make 20 NPCs")
    eng.settings = {**eng.settings, "background": "off"}
    assert (await eng.shift_tick())["state"] == "off"
    eng.settings = {**eng.settings, "background": "local"}
    monkeypatch.setattr(shift, "check", lambda **kw: shift.Gate(False, "the GPU is 80% busy"))
    state = await eng.shift_tick()
    assert state["state"] == "waiting" and state["why"] == "the GPU is 80% busy" and not eng._shift_run
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_goals_turns_dont_teach_routing(eng):
    from eki import table
    goals.create("Write a haiku")
    for _ in range(4):
        eng.goals_run_now(goals.all_goals()[0].id)
        await turn(eng)
    assert not table.load().get("overrides")                                # eki's pick, not yours
    await eng.runner.stop()


# ---- room on the machine and on the subscription ------------------------------------------

def test_the_gate_waits_for_memory_cpu_and_gpu(monkeypatch):
    from eki import memory
    snap = memory.Snapshot(total_gb=64, available_gb=30, used_gb=30, level=60)
    monkeypatch.setattr(memory, "snapshot", lambda: snap)
    monkeypatch.setattr(shift, "cpu_busy", lambda exclude=(): 0.1)
    monkeypatch.setattr(shift, "gpu_busy_others", lambda exclude=(), interval=1.0: None)
    monkeypatch.setattr(shift, "gpu_busy", lambda: 0.0)
    monkeypatch.setattr(shift, "on_battery", lambda: False)
    assert shift.check(model_gb=20, model_loaded=False).ok
    assert "needs 40 GB" in shift.check(model_gb=40, model_loaded=False).why
    monkeypatch.setattr(shift, "gpu_busy", lambda: 0.8)
    assert shift.check().why == "the GPU is 80% busy"
    monkeypatch.setattr(shift, "cpu_busy", lambda exclude=(): 0.9)
    assert "90% of the CPU" in shift.check().why
    monkeypatch.setattr(memory, "snapshot",
                        lambda: memory.Snapshot(total_gb=64, available_gb=2, used_gb=60, level=15))
    assert shift.check().why == "memory pressure is warning"


def test_only_on_power_and_away_mode(monkeypatch):
    monkeypatch.setattr(shift, "on_battery", lambda: True)
    assert shift.check().why == "on battery — waiting for power"
    monkeypatch.setattr(shift, "on_battery", lambda: False)
    monkeypatch.setattr(shift, "idle_seconds", lambda: 12.0)
    assert "you're here" in shift.check(when="away").why


def test_spare_never_touches_the_last_of_a_window_or_runs_ahead_of_the_week():
    now, week = 1_000_000.0, 7 * 86400

    def reading(five, weekly, gone):
        return Reading("claude", [Window("five_hour", "5H", five, resets_at=int(now + 3600), window_seconds=5 * 3600),
                                  Window("seven_day", "WEEK", weekly, resets_at=int(now + week * (1 - gone)),
                                         window_seconds=week)])
    assert shift.spare_room(reading(0.2, 0.3, 0.5), now=now).ok
    assert "the last 30% is yours" in shift.spare_room(reading(0.75, 0.3, 0.5), now=now).why
    assert "ahead of pace" in shift.spare_room(reading(0.2, 0.5, 0.3), now=now).why


def test_the_gpu_counts_other_apps_not_ekis_own_model(monkeypatch):
    samples = iter([{1: 0, 2: 0}, {1: 900_000_000, 2: 100_000_000}])
    monkeypatch.setattr(shift, "gpu_times", lambda: next(samples))
    monkeypatch.setattr(shift.time, "sleep", lambda s: None)
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(shift.time, "monotonic", lambda: next(ticks))
    assert shift.gpu_busy_others(exclude=[1]) == pytest.approx(0.1)


# ---- the bug: a goal stepping aside for its own agent -----------------------------------

@pytest.mark.asyncio
async def test_what_a_goals_own_agent_asks_through_eki_isnt_your_request(eng):
    g = goals.create("Make 20 NPCs")
    Local.delay = 1.0
    await eng.shift_tick()
    rid = eng._shift_run
    await asyncio.sleep(0.1)
    Local.delay = 0.0
    nested = await eng.ask("Say the single word: hello", backend_key="qwen", via="agent")   # eki_ask from inside
    assert not eng._asks_running() or (eng.runs.get(nested["run"]) or {}).get("state") != "running"
    assert (await eng.shift_tick())["state"] == "working"                  # carries on, doesn't step aside
    assert (await settle(eng.runs, rid, timeout=5))["state"] == "done"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_stepping_aside_isnt_a_turn_and_it_waits_a_moment(eng):
    g = goals.create("Make 20 NPCs")
    Local.delay = 2.0
    await eng.shift_tick()
    rid = eng._shift_run
    await asyncio.sleep(0.1)
    Local.delay = 0.0
    await eng.ask("hello")                                                  # yours: it steps aside
    await settle(eng.runs, rid, timeout=5)
    eng._goal_finished()
    g = goals.get(g.id)
    assert g.turns == 0 and g.failures == 0
    assert g.next_at > time.time() + 30                                     # no thrashing
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_model_without_tools_hands_over_inside_the_goals_budget(eng):
    g = goals.create("Write a short poem about the sea")                  # plain writing: the local model…
    Local.script = ["[[handoff: codex-qwen | save it as sea.md]]", "Saved sea.md.\nGOAL: done"]
    await turn(eng)                                                          # …which decides it needs hands
    assert [k for k, _, _ in Local.seen] == ["qwen", "codex-qwen"]         # handed over, still local
    assert "codex-qwen" in Local.systems[0] and "claude_code" not in Local.systems[0]
    assert goals.get(g.id).state == "done"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_the_screen_is_off_in_a_goals_thread(eng, monkeypatch):
    from eki import mcpbridge
    made = {}
    monkeypatch.setattr(mcpbridge, "Bridge", lambda *a, **kw: made.update(kw) or None)
    g = goals.update(goals.create("x").id, conversation="c-goal")
    eng._new_live(["claude"], None, "c-goal")
    assert made["screen"] is False
    eng._new_live(["claude"], None, "c-chat")
    assert made["screen"] is True
    goals.update(goals.create("look at my X", screen=True).id, conversation="c-screen")
    eng._new_live(["claude"], None, "c-screen")
    assert made["screen"] is True                                           # one that may
    await eng.runner.stop()


# ---- a goal that uses the screen: only while you're away ---------------------------------------

def test_your_input_is_told_from_the_screen_tools(monkeypatch):
    p = shift.Presence()
    now = 10_000.0
    assert p.update(20.0, 0.0, now) == 20.0                                 # you, 20 s ago
    assert p.update(400.0, 0.0, now + 380) == 400.0                         # away for a while
    shift.note_input(now + 390)                                             # eki clicks…
    assert shift.eki_input_at() == now + 390
    assert p.update(0.5, now + 390, now + 390.5) >= 390                     # …and isn't taken for you
    assert not p.since(now + 100)
    assert p.update(0.2, now + 390, now + 400) < 1                          # a move after it: you're back
    assert p.since(now + 100)


def away(monkeypatch, seconds, locked=False):
    monkeypatch.setattr(shift, "idle_seconds", lambda: seconds)
    monkeypatch.setattr(shift, "screen_locked", lambda: locked)


@pytest.mark.asyncio
async def test_a_screen_goal_waits_for_you_to_be_away(eng, monkeypatch):
    from eki.engine import SCREEN_WAIT
    g = goals.create("look at my X and propose 3 posts", screen=True)
    away(monkeypatch, 12.0)
    await turn(eng)
    assert not Local.seen and goals.get(g.id).note == SCREEN_WAIT
    assert [x for x in eng.goals_view()["goals"] if x["id"] == g.id][0]["status"] == "away"
    eng._presence = shift.Presence()
    away(monkeypatch, 900.0, locked=True)
    await turn(eng)
    assert not Local.seen and "unlocked" in goals.get(g.id).note
    eng._presence = shift.Presence()
    away(monkeypatch, 900.0)
    held = []
    monkeypatch.setattr(shift.Awake, "hold", lambda self, display=False: held.append(display))
    eng.quota.latest["claude"] = Reading("claude", [Window("five_hour", "5H", 0.1)])
    await eng.shift_tick()
    rid = eng._shift_run
    assert held[-1] is True                                                 # the screen stays on
    assert '"screen": true' in eng.runs.get(rid)["payload"]
    await settle(eng.runs, rid, timeout=5)
    eng._goal_finished()
    # a harness, on a subscription — a goal that may use the screen may use their spare room
    assert Local.seen[-1][0] == "claude_code"
    assert "screen" in Local.seen[-1][1] and "Don't post" in Local.seen[-1][1]
    assert goals.get(g.id).note == ""
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_screen_goal_steps_out_when_youre_back_not_for_its_own_clicks(eng, monkeypatch):
    g = goals.create("look at my X", screen=True)
    away(monkeypatch, 900.0)
    eng.quota.latest["claude"] = Reading("claude", [Window("five_hour", "5H", 0.1)])
    Local.delay = 3.0
    await eng.shift_tick()
    rid = eng._shift_run
    assert rid and eng._shift_screen
    shift.note_input()                                                      # its own click…
    away(monkeypatch, 0.3)
    await eng.shift_tick()
    assert eng._shift_run == rid and rid in eng.runner.running              # …carries on
    await asyncio.sleep(2.0)
    away(monkeypatch, 0.2)                                                  # you, well after it
    await eng.shift_tick()
    await settle(eng.runs, rid, timeout=5)
    assert eng.runs.get(rid)["state"] == "cancelled"
    eng._goal_finished()
    g = goals.get(g.id)
    assert g.turns == 0 and g.state == "active"
    await eng.shift_tick()
    assert not eng._shift_run                                               # and waits till you leave
    Local.delay = 0.0
    await eng.runner.stop()


def test_codex_under_a_goal_gets_eki_without_the_screen(tmp_path, monkeypatch):
    from eki import mcpregistry
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(mcpregistry, "CODEX_CONFIG", cfg)
    assert mcpregistry.codex_screen_off() == []                             # no eki server: nothing to change
    cfg.write_text(mcpregistry.codex_block({}, ["python", "-m", "eki.cli", "mcp"]))
    flags = mcpregistry.codex_screen_off()
    assert flags[0] == "-c" and 'EKI_SCREEN = "0"' in flags[1] and "PYTHONPATH" in flags[1]


@pytest.mark.asyncio
async def test_a_memory_spike_passes_a_lasting_warning_doesnt(eng, monkeypatch):
    goals.create("Make 20 NPCs")
    Local.delay = 3.0
    await eng.shift_tick()
    rid = eng._shift_run
    monkeypatch.setattr(shift, "must_stop", lambda *a: shift.Gate(False, "memory pressure is warning"))
    await eng.shift_tick()
    await eng.shift_tick()
    assert rid in eng.runner.running                                          # a spike: carries on
    await eng.shift_tick()
    assert (await settle(eng.runs, rid, timeout=5))["state"] == "cancelled"   # it lasted: steps aside
    eng._goal_finished()
    assert goals.history(0)[-1]["why"] == "memory pressure is warning"       # and says why
    await eng.runner.stop()


def test_eki_ask_from_inside_an_agent_says_so(monkeypatch):
    from eki import cli
    sent = {}
    monkeypatch.setattr(cli, "call", lambda m, p, s, **kw: sent.update(kw["json"]) or {"run": "r", "conversation": "c"})
    monkeypatch.setenv("EKI_INSIDE", "1")
    args = type("A", (), {"continue_": False, "prompt": "hello", "backend": "qwen", "repo": "", "image": False,
                          "service": "x", "detach": True})()
    cli.cmd_ask(None, args)
    assert sent["via"] == "agent"


# ---- goals are eki's timetable: on time, fresh each time -----------------------------------------

@pytest.mark.asyncio
async def test_an_on_time_goal_doesnt_wait_for_room_or_for_you(eng, monkeypatch):
    monkeypatch.setattr(shift, "check", lambda **kw: shift.Gate(False, "the GPU is 80% busy"))
    monkeypatch.setattr(shift, "on_battery", lambda: False)
    loose = goals.create("Tidy my notes")
    await turn(eng)
    assert not Local.seen                                                   # no room: it waits
    goals.remove(loose.id)
    report = goals.create("Summarise yesterday's commits", when={"kind": "daily", "at": "08:00"},
                          on_time=True)
    await turn(eng)
    assert len(Local.seen) == 1                                             # its time: it runs
    g = goals.get(report.id)
    assert g.next_at > time.time() and g.state == "active"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_an_on_time_goal_goes_first(eng, monkeypatch):
    monkeypatch.setattr(shift, "on_battery", lambda: False)
    slow = goals.create("Make 20 NPCs")
    Local.delay = 3.0
    await eng.shift_tick()
    rid = eng._shift_run
    goals.create("The 8:00 report", when={"kind": "daily", "at": "08:00"}, on_time=True)
    await eng.shift_tick()                                                  # its time: the other steps aside
    await settle(eng.runs, rid, timeout=5)
    assert eng.runs.get(rid)["state"] == "cancelled"
    Local.delay = 0.0
    await turn(eng)
    assert goals.get(slow.id).turns == 0                                    # not held against it
    assert "8:00 report" in Local.seen[-1][1]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_fresh_each_time_but_your_answer_stays_in_its_thread(eng):
    g = goals.create("Propose 3 posts", when={"kind": "interval", "minutes": 60}, fresh=True)
    Local.script = ["Here are three.\nGOAL: done"]
    await turn(eng)
    first = goals.get(g.id).conversation
    goals.update(g.id, next_at=0)                                           # its next time
    Local.script = ["Which tone?\nGOAL: waiting"]
    await turn(eng)
    second = goals.get(g.id).conversation
    assert second and second != first
    titles = {c["id"]: c.get("title") or "" for c in eng.store.conversations(limit=50)}
    assert "Propose 3 posts" in titles.get(second, "")
    eng.store.add_turn(second, "user", "dry")                               # you answer…
    Local.script = ["Dry it is.\nGOAL: done"]
    await turn(eng)
    assert goals.get(g.id).conversation == second                           # …in the same thread
    await eng.runner.stop()


def test_an_on_time_goal_the_mac_slept_through_is_skipped(eng):
    g = goals.create("The 8:00 report", when={"kind": "daily", "at": "08:00"}, on_time=True)
    g = goals.update(g.id, next_at=int(time.time()) - 10 * 3600)
    assert goals.stale(g, time.time())
    assert not goals.stale(goals.update(g.id, on_time=False), time.time())   # one that waits for room just runs late


@pytest.mark.asyncio
async def test_skipped_then_its_next_time(eng):
    g = goals.create("The 8:00 report", when={"kind": "daily", "at": "08:00"}, on_time=True)
    goals.update(g.id, next_at=int(time.time()) - 10 * 3600)
    await turn(eng)
    assert not Local.seen and goals.get(g.id).next_at > time.time()
    assert goals.history(0)[-1]["state"] == "skipped"
    await eng.runner.stop()


def test_schedules_become_goals(tmp_path):
    import json
    import sqlite3
    db = tmp_path / "eki.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE schedules (id TEXT, name TEXT, prompt TEXT, cwd TEXT, backend TEXT, spec TEXT,"
              " enabled INTEGER, created_at INTEGER)")
    c.execute("INSERT INTO schedules VALUES ('a', 'Morning', 'Summarise my inbox', '', 'claude_code', ?, 1, 1)",
              (json.dumps({"kind": "daily", "at": "08:00", "days": [0, 1, 2, 3, 4]}),))
    c.execute("INSERT INTO schedules VALUES ('b', 'Often', 'Check the build', ?, '', ?, 0, 2)",
              (str(tmp_path), json.dumps({"kind": "interval", "minutes": 5})))
    c.commit()
    c.close()
    made = goals.adopt_schedules(db)
    assert [g.text for g in made] == ["Summarise my inbox", "Check the build"]
    inbox, build = made
    assert inbox.on_time and inbox.fresh and inbox.spare and inbox.when["days"] == [0, 1, 2, 3, 4]
    assert inbox.next_at > time.time()                                      # at its time, not now
    assert build.state == "paused" and build.when["minutes"] == 15 and build.folder == str(tmp_path)
    assert goals.adopt_schedules(db) == []                                  # once
    tables = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master")}
    assert "schedules_moved_to_goals" in tables and "schedules" not in tables


@pytest.mark.asyncio
async def test_it_tells_you_when_a_goal_needs_you(eng, monkeypatch):
    told = []

    async def notify(title, body):
        told.append((title, body))
    monkeypatch.setattr(eng, "_notify", notify)
    eng.settings["notify_goals"] = True
    goals.create("Make 20 NPCs")
    Local.script = ["Made 5.\nGOAL: continue"]
    await turn(eng)
    Local.script = ["Painterly or pixel?\nGOAL: waiting"]
    await turn(eng)
    await asyncio.sleep(0.05)
    assert told == [("Needs you: Make 20 NPCs", "Painterly or pixel?")]    # not for carrying on
    await eng.runner.stop()



@pytest.mark.asyncio
async def test_choose_a_folder_in_macos_own_dialog(tmp_path, monkeypatch):
    from eki import service
    seen = []

    class Proc:
        def __init__(self, code, out):
            self.returncode, self.out = code, out

        async def communicate(self):
            return self.out, b""

    answers = [Proc(0, f"{tmp_path}/\n".encode()), Proc(1, b"")]

    async def fake(*argv, **kw):
        seen.append(argv)
        return answers.pop(0)
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", fake)
    assert await service.choose_folder(str(tmp_path / "nope")) == str(tmp_path)   # starts from what exists
    assert f'POSIX file "{tmp_path}"' in " ".join(seen[0])
    assert await service.choose_folder() == ""                                   # cancelled
