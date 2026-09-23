# SPDX-License-Identifier: Apache-2.0
"""A project says what should exist; the machine makes what's missing when it
has room, gives way to you, and never spends the last of a subscription."""
import asyncio
import json
import time
from pathlib import Path

import pytest

from eki import goals, shift, watch
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from eki.quota.base import Reading, Window
from tests.test_runs import echo_config, settle

GOALS = """
bible: [world.md]
goals:
  - name: npcs
    count: 3
    id: "npc-{n:02}"
    dir: "npcs/{id}"
    parts:
      bio:
        kind: text
        file: bio.md
        prompt: "Invent character {n}. Already made: {others}"
      portrait:
        kind: image
        file: portrait.png
        from: [bio]
        prompt: "Portrait: {bio:20}"
      lines:
        kind: text
        file: lines.md
        from: [bio]
        prompt: "Three lines for: {bio}"
"""


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "rpg"
    root.mkdir()
    (root / "goals.yaml").write_text(GOALS)
    (root / "world.md").write_text("The kingdom of Ashfall, where it always snows.")
    return root


# ---- what's missing ---------------------------------------------------------------------

def test_the_backlog_is_what_isnt_there_in_dependency_order(game):
    spec = goals.load(str(game))
    missing = goals.pieces(spec)
    assert len(missing) == 9
    assert [p.part for p in missing[:3]] == ["bio", "portrait", "lines"]
    assert [p.ready for p in missing[:3]] == [True, False, False]     # a portrait needs its bio
    (game / "npcs/npc-01").mkdir(parents=True)
    (game / "npcs/npc-01/bio.md").write_text("# Mira the smith\nShe forges in the snow.\n")
    missing = goals.pieces(goals.load(str(game)))
    assert len(missing) == 8
    assert all(p.ready for p in missing if p.item == "npc-01")
    assert goals.status(goals.load(str(game)))[0]["done"] == 1


def test_a_prompt_carries_the_bible_its_sources_and_the_others(game):
    (game / "npcs/npc-01").mkdir(parents=True)
    (game / "npcs/npc-01/bio.md").write_text("# Mira the smith\nShe forges in the snow.\n")
    spec = goals.load(str(game))
    todo = {p.key: p for p in goals.pieces(spec)}
    assert goals.render(spec, todo["npcs/npc-01/portrait"]) == "Portrait: # Mira the smith\nShe"
    assert "Mira the smith" in goals.render(spec, todo["npcs/npc-02/bio"])   # not the same one again
    assert "Ashfall" in goals.instructions(spec, todo["npcs/npc-02/bio"])


def test_a_goal_that_goes_in_a_circle_or_names_nothing_is_refused(tmp_path):
    (tmp_path / "goals.yaml").write_text(
        "goals:\n  - name: a\n    count: 1\n    parts:\n"
        "      x: {kind: text, file: x.md, prompt: p, from: [y]}\n"
        "      y: {kind: text, file: y.md, prompt: p, from: [x]}\n")
    with pytest.raises(goals.GoalError, match="circle"):
        goals.load(str(tmp_path))
    (tmp_path / "goals.yaml").write_text(
        "goals:\n  - name: a\n    count: 1\n    parts:\n"
        "      x: {kind: text, file: x.md, prompt: p, from: [nope]}\n")
    with pytest.raises(goals.GoalError, match="isn't a part"):
        goals.load(str(tmp_path))
    with pytest.raises(goals.GoalError, match="no goals.yaml"):
        goals.load(str(tmp_path / "missing"))


def test_thinking_stays_out_of_the_file():
    assert goals.clean("<think>who is she</think>\n# Mira") == "# Mira\n"


# ---- room on the machine and on the subscription ----------------------------------------

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
    assert shift.check(model_gb=40, model_loaded=True).ok                  # already there
    monkeypatch.setattr(shift, "gpu_busy", lambda: 0.8)
    assert shift.check().why == "the GPU is 80% busy"
    monkeypatch.setattr(shift, "cpu_busy", lambda exclude=(): 0.9)
    assert "90% of the CPU" in shift.check().why
    monkeypatch.setattr(memory, "snapshot",
                        lambda: memory.Snapshot(total_gb=64, available_gb=2, used_gb=60, level=15))
    assert shift.check().why == "memory pressure is warning"
    assert not shift.must_stop().ok


def test_away_mode_waits_for_nobody_at_the_keyboard(monkeypatch):
    monkeypatch.setattr(shift, "on_battery", lambda: False)
    monkeypatch.setattr(shift, "idle_seconds", lambda: 12.0)
    assert "you're here" in shift.check(when="away").why


def test_spare_never_touches_the_last_of_a_window_or_runs_ahead_of_the_week():
    now = 1_000_000.0
    week = 7 * 86400

    def reading(five, weekly, week_gone):
        return Reading("claude", [
            Window("five_hour", "5H", five, resets_at=int(now + 3600), window_seconds=5 * 3600),
            Window("seven_day", "WEEK", weekly, resets_at=int(now + week * (1 - week_gone)),
                   window_seconds=week)])

    assert shift.spare_room(reading(0.2, 0.3, 0.5), now=now).ok
    assert "the last 30% is yours" in shift.spare_room(reading(0.75, 0.3, 0.5), now=now).why
    assert "ahead of pace" in shift.spare_room(reading(0.2, 0.5, 0.3), now=now).why
    assert not shift.spare_room(None).ok


# ---- the shift, in the engine ------------------------------------------------------------

class Writer(Backend):
    seen = []
    delay = 0.0

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Writer.seen.append([m.content for m in messages])
        if Writer.delay:
            await asyncio.sleep(Writer.delay)
        ask = messages[-1].content
        if ask.startswith("Invent"):
            yield f"<think>hmm</think># Character {ask.split()[2]}\nA smith of Ashfall."
        else:
            yield "“Mind the forge.” “Snow again.” “Coin first.”"


class Painter(Backend):
    seen = []
    source = ""

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Painter.seen.append(kw.get("prompt"))
        yield "flux · 1024×1024\n"
        yield f"\n![portrait]({Painter.source})\n"


@pytest.fixture
def eng(tmp_path, monkeypatch):
    from eki.engine import Engine
    from eki.adapters import base as adapters
    cfg = echo_config(tmp_path)
    cfg.backends, cfg.options = [], {}
    cfg.backends.append(BackendInfo(key="claude_code", kind="claude_code", label="Claude Code",
                                    capabilities=Capabilities(tools=True, repo=True), cost=Cost(tier=50),
                                    quota_source="claude"))
    cfg.options["claude_code"] = {"binary": "/bin/echo"}
    cfg.backends.append(BackendInfo(key="qwen", kind="mlx", label="Qwen (local)",
                                    capabilities=Capabilities(), cost=Cost(tier=0)))
    cfg.options["qwen"] = {"model": "q"}
    cfg.backends.append(BackendInfo(key="flux", kind="comfyui", label="FLUX",
                                    capabilities=Capabilities(text=False, images_out=True), cost=Cost(tier=0)))
    cfg.options["flux"] = {}
    watch.save({"vendors": {}})
    kinds = {"mlx": Writer, "comfyui": Painter}
    monkeypatch.setattr(adapters, "build", lambda info, opts: kinds.get(info.kind, Writer)(info, opts))
    e = Engine(cfg, owner=True)
    e.settings = {**e.settings, "skills_learn": "off", "notify_learned": False, "skills_local": False}
    monkeypatch.setattr(e, "_lives", lambda b: False)
    e.router.is_up = lambda k: True
    monkeypatch.setattr(shift, "check", lambda **kw: shift.Gate(True))
    monkeypatch.setattr(shift, "must_stop", lambda: shift.Gate(True))
    pic = tmp_path / "drawn.png"
    pic.write_bytes(b"\x89PNG fake")
    Painter.source = str(pic)
    Writer.seen, Painter.seen, Writer.delay = [], [], 0.0
    return e


async def work(e, ticks=30):
    for _ in range(ticks):
        state = await e.shift_tick()
        if e._shift_run:
            await settle(e.runs, e._shift_run, timeout=5)
        elif state["state"] in ("idle", "off"):
            return state
    return state


@pytest.mark.asyncio
async def test_the_shift_makes_every_missing_piece_on_local_models(eng, game):
    goals.add(str(game))
    state = await work(eng)
    assert state["why"] == "nothing left to make"
    for n in (1, 2, 3):
        d = game / f"npcs/npc-0{n}"
        assert (d / "bio.md").read_text().startswith(f"# Character {n}")      # no thinking in it
        assert (d / "portrait.png").read_bytes() == b"\x89PNG fake"
        assert "Mind the forge" in (d / "lines.md").read_text()
    assert "Ashfall" in Writer.seen[0][0]                                     # the bible, every time
    assert any("# Character 1" in (p or "") for p in Painter.seen)           # drawn from its bio
    assert any(s[-1].startswith("Three lines for: # Character") for s in Writer.seen)   # lines from the bio
    report = eng.goals_report()
    assert report["made"] == 9 and report["on_subscription"] == 0
    assert set(report["by_backend"]) == {"qwen", "flux"}
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_your_request_comes_first(eng, game):
    goals.add(str(game))
    Writer.delay = 2.0
    await eng.shift_tick()
    rid = eng._shift_run
    await asyncio.sleep(0.2)
    assert rid in eng.runner.running
    Writer.delay = 0.0
    await eng.ask("hello")
    run = await settle(eng.runs, rid, timeout=5)
    assert run["state"] == "cancelled"
    assert not (game / "npcs/npc-01/bio.md").exists()                       # redone later, not half-written
    assert eng.goals_report()["stepped_out"] == 1
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_part_worth_a_subscription_waits_for_spare_mode(eng, game):
    (game / "goals.yaml").write_text(GOALS.replace(
        'prompt: "Invent character {n}. Already made: {others}"',
        'prompt: "Invent character {n}. Already made: {others}"\n        line: frontier'))
    goals.add(str(game))
    state = await eng.shift_tick()
    assert state["state"] == "idle" and "waits for `spare` mode" in state["why"]
    eng.settings = {**eng.settings, "background": "spare"}
    eng.quota.latest["claude"] = Reading("claude", [Window("five_hour", "5H", 0.8)])
    state = await eng.shift_tick()
    assert "the last 30% is yours" in state["why"]
    eng.quota.latest["claude"] = Reading("claude", [Window("five_hour", "5H", 0.1)])
    state = await eng.shift_tick()
    assert state["state"] == "working" and "claude_code" in state["why"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_off_is_off_and_no_room_means_waiting(eng, game, monkeypatch):
    goals.add(str(game))
    eng.settings = {**eng.settings, "background": "off"}
    assert (await eng.shift_tick())["state"] == "off"
    eng.settings = {**eng.settings, "background": "local"}
    monkeypatch.setattr(shift, "check", lambda **kw: shift.Gate(False, "the GPU is 80% busy"))
    state = await eng.shift_tick()
    assert state == {**state, "state": "waiting", "why": "the GPU is 80% busy"}
    assert not eng._shift_run
    await eng.runner.stop()


def test_only_on_power_unless_you_say_otherwise(monkeypatch):
    monkeypatch.setattr(shift, "on_battery", lambda: True)
    assert shift.check().why == "on battery — waiting for power"
    assert shift.must_stop().why == "on battery — waiting for power"      # a piece in progress stops


def test_the_gpu_counts_other_apps_not_ekis_own_model(monkeypatch):
    samples = iter([{1: 0, 2: 0}, {1: 900_000_000, 2: 100_000_000}])    # pid 1 is eki's model
    monkeypatch.setattr(shift, "gpu_times", lambda: next(samples))
    monkeypatch.setattr(shift.time, "sleep", lambda s: None)
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(shift.time, "monotonic", lambda: next(ticks))
    assert shift.gpu_busy_others(exclude=[1]) == pytest.approx(0.1)


# ---- the review board --------------------------------------------------------------------

def made(game, n, bio="# Mira, smith\nForges.", lines="* Hello."):
    d = game / f"npcs/npc-0{n}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "bio.md").write_text(bio)
    (d / "portrait.png").write_bytes(b"png")
    (d / "lines.md").write_text(lines)
    return d


def test_the_board_sees_each_item_as_it_stands(game):
    made(game, 1)
    goals.add(str(game))
    view = goals.items(goals.load(str(game)))[0]
    first, second = view["items"][0], view["items"][1]
    assert first["title"] == "Mira, smith" and all(p["exists"] for p in first["parts"])
    assert second["title"] == "npc-02" and [p.get("ready") for p in second["parts"]] == [True, False, False]


def test_redo_keeps_the_old_files_and_remakes_what_came_from_them(game):
    d = made(game, 1)
    spec = goals.load(str(game))
    moved = goals.redo(spec, "npcs", "npc-01", "bio", note="make her older")
    assert moved == ["npcs/npc-01/bio", "npcs/npc-01/portrait", "npcs/npc-01/lines"]
    assert not (d / "bio.md").exists() and not (d / "portrait.png").exists()
    kept = list((game / ".eki" / "trash").rglob("bio.md"))
    assert kept and kept[0].read_text().startswith("# Mira")                   # nothing is lost
    piece = next(p for p in goals.pieces(goals.load(str(game))) if p.key == "npcs/npc-01/bio")
    assert goals.render(goals.load(str(game)), piece).endswith("This time: make her older")
    goals.forget_note(str(game), piece.key)
    assert "This time" not in goals.render(goals.load(str(game)), piece)
    assert goals.redo(spec, "npcs", "npc-01", "lines") == []                   # already gone
    with pytest.raises(goals.GoalError):
        goals.redo(spec, "npcs", "npc-99", "bio")


def test_only_files_inside_a_project_are_served(game, tmp_path):
    made(game, 1)
    assert goals.inside(str(game), "npcs/npc-01/portrait.png") is None          # not added yet
    goals.add(str(game))
    assert goals.inside(str(game), "npcs/npc-01/portrait.png") is not None
    (tmp_path / "secret.txt").write_text("no")
    assert goals.inside(str(game), "../secret.txt") is None
    assert goals.inside(str(game), "/etc/passwd") is None


@pytest.mark.asyncio
async def test_a_redone_piece_is_made_again_with_the_note(eng, game):
    goals.add(str(game))
    await work(eng)
    first = (game / "npcs/npc-02/bio.md").read_text()
    eng.goals_redo(str(game), "npcs", "npc-02", "bio", "make them a child")
    assert not (game / "npcs/npc-02/lines.md").exists()
    await work(eng)
    assert (game / "npcs/npc-02/lines.md").exists()
    asked = [s[-1] for s in Writer.seen if "make them a child" in s[-1]]
    assert asked and asked[0].startswith("Invent character 2")
    assert goals.notes(str(game)) == {}                                         # used once
    assert (game / "npcs/npc-02/bio.md").read_text() == first                  # the fake writes the same
    await eng.runner.stop()



def test_a_deleted_item_stays_deleted_until_you_want_it_again(game):
    made(game, 1)
    goals.add(str(game))
    spec = goals.load(str(game))
    done = goals.delete(spec, "npcs", "npc-01")
    assert done["moved"] == 3 and not (game / "npcs/npc-01/bio.md").exists()
    assert not [p for p in goals.backlog() if p.item == "npc-01"]           # not remade
    row = goals.status(goals.load(str(game)))[0]
    assert row["deleted"] == 3 and row["total"] == 6 and row["done"] == 0
    view = goals.items(goals.load(str(game)))[0]["items"][0]
    assert view["deleted"] and all(p["deleted"] for p in view["parts"])
    goals.restore(goals.load(str(game)), "npcs", "npc-01")
    assert [p.part for p in goals.backlog() if p.item == "npc-01"] == ["bio", "portrait", "lines"]


def test_deleting_a_part_takes_what_was_made_from_it(game):
    made(game, 1)
    goals.add(str(game))
    goals.delete(goals.load(str(game)), "npcs", "npc-01", "bio")
    assert not any((game / f"npcs/npc-01/{f}").exists() for f in ("bio.md", "portrait.png", "lines.md"))
    assert not [p for p in goals.backlog() if p.item == "npc-01"]
    goals.restore(goals.load(str(game)), "npcs", "npc-01", "portrait")        # just the portrait back…
    back = [p for p in goals.backlog() if p.item == "npc-01"]
    assert [(p.part, p.ready) for p in back] == [("portrait", False)]         # …waits for its bio


def test_deleting_a_whole_goal_empties_it_and_pauses_it(game):
    made(game, 1)
    made(game, 2)
    goals.add(str(game))
    done = goals.delete(goals.load(str(game)), "npcs")
    assert done == {"moved": 6, "paused": True}
    assert goals.backlog() == []                                               # paused: nothing remade
    assert goals.status(goals.load(str(game)))[0]["paused"]
    goals.pause(str(game), "npcs", False)
    assert len(goals.backlog()) == 9
    assert len(list((game / ".eki" / "trash").rglob("*.*"))) == 6              # all kept


@pytest.mark.asyncio
async def test_the_view_says_what_each_goal_is_doing(eng, game):
    goals.add(str(game))
    Writer.delay = 2.0
    await eng.shift_tick()
    await asyncio.sleep(0.2)
    g = eng.goals_view()["projects"][0]["goals"][0]
    assert g["state"] == "working" and g["working"] == "npc-01/bio"
    eng.goals_pause(str(game), "npcs", True)                                  # steps out of it
    run = await settle(eng.runs, eng._shift_run, timeout=5)
    assert run["state"] == "cancelled"
    Writer.delay = 0.0
    assert eng.goals_view()["projects"][0]["goals"][0]["state"] == "paused"
    assert (await eng.shift_tick())["why"] == "nothing left to make"
    await eng.runner.stop()
