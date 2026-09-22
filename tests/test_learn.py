# SPDX-License-Identifier: Apache-2.0
"""Skills eki drafts for itself after a run: when it looks, what it may
change, and that everything it learns is a commit that says why."""
import asyncio
import json

import pytest

from eki import learn, skills
from eki.adapters.base import Backend, BackendInfo, Health, register
from eki.engine import Engine
from tests.test_runs import echo_config, settle


def turn(role, content, meta=None, backend="echo", id=0):
    return {"id": id, "role": role, "content": content, "backend": backend,
            "meta": json.dumps(meta or {})}


# ---- when to look -------------------------------------------------------------

def test_a_plain_request_teaches_nothing():
    assert learn.signals("write a script that doesn't print anything", []) == []
    before = [turn("user", "hi"), turn("assistant", "hello")]
    assert learn.signals("now write a haiku about autumn", before) == []


def test_being_asked_to_remember_is_a_signal_even_first():
    assert learn.signals("From now on, answer with metric units", []) == ["asked"]
    assert "asked" in learn.signals("make this a skill please", [])
    assert "asked" in learn.signals("以后都用中文回答，记住", [])


def test_a_correction_needs_an_answer_to_correct():
    before = [turn("user", "set up the project"), turn("assistant", "ran npm install")]
    assert learn.signals("no, use pnpm — never npm here", before) == ["corrected"]
    assert learn.signals("Don't add comments to the code", before) == ["corrected"]
    assert learn.signals("no, use pnpm", []) == []
    # a correction opens the message; "no" deep in a sentence isn't one
    assert learn.signals("make a list of words with no vowels", before) == []


def test_a_failure_then_a_success_is_a_signal():
    before = [turn("user", "build it"), turn("assistant", "[failed: exit 1]", {"failed": True})]
    assert learn.signals("try again with the right python", before) == ["recovered"]


# ---- reading the review ---------------------------------------------------------

def test_the_answer_is_found_behind_thinking_and_fences():
    text = '<think>{"action": "new"} hmm</think>Sure:\n```json\n{"action": "none", "why": "one-off"}\n```'
    assert learn.parse(text) == {"action": "none", "why": "one-off"}
    assert learn.parse("no json here") is None


GOOD = {"action": "new", "name": "Use_PNPM", "description": "Setting up or changing JS projects: use pnpm, never npm or yarn.",
        "body": "Use `pnpm install` and `pnpm add`. Never create package-lock.json.", "why": "corrected npm to pnpm"}


def test_a_good_change_is_normalised():
    change, why = learn.check(GOOD)
    assert why == "" and change["name"] == "use-pnpm" and change["action"] == "new"


@pytest.mark.parametrize("patch,reason", [
    ({"action": "none", "why": "nothing reusable"}, "nothing reusable"),
    ({"name": "eki"}, "bad name"),
    ({"name": "../etc"}, "bad name"),
    ({"description": "short"}, "description"),
    ({"body": "tiny"}, "body"),
    ({"body": "Export OPENAI key: sk-abcdefghijklmnopqrstuvwx and go"}, "secret"),
    ({"body": "Then set api_key = hunter2hunter2 in the file."}, "secret"),
])
def test_a_bad_change_is_refused(patch, reason):
    change, why = learn.check({**GOOD, **patch})
    assert change is None and reason in why


def test_eki_never_rewrites_a_skill_you_wrote():
    skills.put("use-pnpm", description="mine, about pnpm and more", body="My rules.")
    change, why = learn.check(GOOD)
    assert change is None and "yours" in why
    assert "My rules." in skills.source("use-pnpm")


# ---- the store -------------------------------------------------------------------

def test_a_learned_skill_is_a_commit_that_says_why():
    s = skills.learn("use-pnpm", GOOD["description"], GOOD["body"], why="corrected npm to pnpm",
                     run="r123", conversation="c9")
    assert s["enabled"] and s["learned"]["run"] == "r123" and s["learned"]["times"] == 1
    assert (skills.VIEWS["claude"] / "use-pnpm").is_symlink()
    log = skills.history(5, "use-pnpm")
    assert log[0]["message"] == "learn use-pnpm: corrected npm to pnpm"
    full = skills._git("log", "-1", "--format=%B")
    assert "Run: r123" in full and "Conversation: c9" in full
    # a long reason keeps the subject short and the whole of it in the body
    long = "The person corrected npm to pnpm. " + "They were very clear about it. " * 5
    skills.learn("long-why", GOOD["description"], GOOD["body"], why=long)
    subject = skills.history(1, "long-why")[0]["message"]
    assert subject == "learn long-why: The person corrected npm to pnpm"
    assert long.strip() in skills._git("log", "-1", "--format=%B")
    # a second lesson improves it, and says so
    skills.learn("use-pnpm", GOOD["description"], GOOD["body"] + "\nUse pnpm dlx.", why="dlx too")
    assert skills.get("use-pnpm")["learned"]["times"] == 2
    assert skills.history(1, "use-pnpm")[0]["message"].startswith("improve use-pnpm")


def test_an_edit_made_through_the_link_is_its_own_commit():
    """Claude Code edited a skill through ~/.claude/skills during a run."""
    skills.learn("use-pnpm", GOOD["description"], GOOD["body"], why="w")
    link = skills.VIEWS["claude"] / "use-pnpm" / "SKILL.md"
    link.write_text(link.read_text() + "\n- One-off CLIs: `pnpm dlx`, never `npx`.\n")
    assert skills.settle_stray("claude_code", "r9") == ["use-pnpm"]
    assert skills.history(1)[0]["message"] == "edit use-pnpm by claude_code, outside eki"
    assert "Run: r9" in skills._git("log", "-1", "--format=%B")
    assert skills.settle_stray() == []                # nothing left over
    assert skills.learnable("use-pnpm")               # still eki's to improve


def test_once_you_edit_it_it_is_yours():
    skills.learn("use-pnpm", GOOD["description"], GOOD["body"], why="w")
    assert skills.learnable("use-pnpm")
    skills.put("use-pnpm", description=GOOD["description"], body="My version.")
    assert not skills.learnable("use-pnpm")
    with pytest.raises(ValueError):
        skills.learn("use-pnpm", GOOD["description"], "Eki's version again.", why="w")


def test_propose_mode_adds_it_off_and_never_changes_one_that_is_on():
    change, _ = learn.check(GOOD)
    done = learn.apply(change, "propose")
    assert done["applied"] and not skills.get("use-pnpm")["enabled"]
    assert not (skills.VIEWS["claude"] / "use-pnpm").exists()
    skills.set_enabled("use-pnpm", True)
    again, _ = learn.check({**GOOD, "body": GOOD["body"] + " Also pnpm dlx."})
    assert again["action"] == "edit"
    done = learn.apply(again, "propose")
    assert not done["applied"] and "dlx" not in skills.source("use-pnpm")
    assert learn.apply(again, "apply")["applied"] and "dlx" in skills.source("use-pnpm")


def test_the_review_sees_what_it_may_edit_and_what_it_may_not():
    skills.put("house-style", description="How we write docs here", body="Short sentences.")
    skills.learn("use-pnpm", GOOD["description"], GOOD["body"], why="w")
    p = learn.build_prompt([turn("user", "set it up"), turn("assistant", "done")], ["corrected"])
    assert "- house-style: How we write docs here" in p
    assert "may edit" in p and "pnpm install" in p and "Short sentences." not in p
    assert "corrected the answer" in p


def test_the_daily_budget_counts_only_reviews_eki_started():
    for _ in range(3):
        learn.record({"signals": ["corrected"], "result": "none"})
    learn.record({"signals": ["asked"], "result": "none"})
    assert learn.budget_left(4) == 1


# ---- in the engine -------------------------------------------------------------

@register("tutor")
class Tutor(Backend):
    answer = ""
    prompts: list = []
    kws: list = []

    async def health(self):
        return Health(True, "tutor")

    async def stream(self, messages, **kw):
        Tutor.prompts.append(messages[-1].content)
        Tutor.kws.append(kw)
        if isinstance(Tutor.answer, Exception):
            raise Tutor.answer
        yield Tutor.answer


def school(tmp_path, monkeypatch, answer) -> Engine:
    eng = Engine(echo_config(tmp_path), owner=True)
    eng.settings = {**eng.settings, "skills_learn": "apply", "notify_learned": False,
                    "skills_learn_backend": "", "skills_learn_daily": 8}
    Tutor.answer, Tutor.prompts, Tutor.kws = answer, [], []
    monkeypatch.setattr(eng, "_reviewer",
                        lambda did: Tutor(BackendInfo(key="tutor", kind="tutor", label="t"), {}))
    return eng


async def say(eng, prompt, cid=""):
    started = await eng.ask(prompt, conversation=cid)
    await settle(eng.runs, started["run"])
    for _ in range(100):                       # the review runs beside the run
        if not eng._side_tasks:
            break
        await asyncio.sleep(0.02)
    return started


@pytest.mark.asyncio
async def test_a_correction_becomes_a_skill(tmp_path, monkeypatch):
    eng = school(tmp_path, monkeypatch, json.dumps(GOOD))
    first = await say(eng, "set up the web project")
    assert Tutor.prompts == []                         # nothing to learn from that
    second = await say(eng, "no, use pnpm here, never npm", first["conversation"])
    assert len(Tutor.prompts) == 1 and "never npm" in Tutor.prompts[0]
    s = skills.get("use-pnpm")
    assert s and s["enabled"] and s["learned"]["run"] == second["run"]
    assert skills.history(1)[0]["message"].startswith("learn use-pnpm")
    assert s["learned"]["conversation"] == first["conversation"]
    row = learn.reviews(1)[0]
    assert row["result"] == "new" and row["skill"] == "use-pnpm" and row["signals"] == ["corrected"]
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_none_is_recorded_and_nothing_is_written(tmp_path, monkeypatch):
    eng = school(tmp_path, monkeypatch, '{"action": "none", "why": "one-off"}')
    await say(eng, "From now on call me Captain")
    assert skills.get("use-pnpm") is None
    assert learn.reviews(1)[0]["result"] == "none"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_reviewer_that_fails_never_fails_the_run(tmp_path, monkeypatch):
    from eki.adapters.base import BackendError
    eng = school(tmp_path, monkeypatch, BackendError("claude timed out"))
    started = await say(eng, "remember this as a skill: answers in metric")
    assert eng.runs.get(started["run"])["state"] == "done"
    assert learn.reviews(1)[0]["result"] == "error"
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_off_means_no_review(tmp_path, monkeypatch):
    eng = school(tmp_path, monkeypatch, json.dumps(GOOD))
    eng.settings["skills_learn"] = "off"
    await say(eng, "remember this as a skill: use pnpm")
    assert Tutor.prompts == [] and learn.reviews() == []
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_you_can_ask_for_a_review_of_any_thread(tmp_path, monkeypatch):
    eng = school(tmp_path, monkeypatch, json.dumps(GOOD))
    started = await say(eng, "set up the web project with pnpm")
    assert Tutor.prompts == []
    done = await eng.learn_now(started["conversation"])
    assert done["result"] == "new" and skills.get("use-pnpm")
    assert learn.reviews(1)[0]["manual"] is True
    with pytest.raises(KeyError):
        await eng.learn_now("no-such-thread")
    await eng.runner.stop()


def test_a_cli_reviewer_runs_in_its_own_folder(tmp_path, monkeypatch):
    """Claude Code and Codex review from ~/.eki/learn, never from a repo."""
    eng = Engine(echo_config(tmp_path), owner=True)
    info = BackendInfo(key="cc", kind="claude_code", label="cc")
    eng.cfg.backends.append(info)
    monkeypatch.setattr(eng, "get", lambda k: type("B", (), {"info": info})() if k == "cc" else None)
    rev = eng._reviewer("cc")
    assert rev is not None and rev.info.kind == "claude_code"
    assert eng._reviewer("nope") is None
