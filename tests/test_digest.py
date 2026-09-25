# SPDX-License-Identifier: Apache-2.0
"""One short page a day instead of a stream: what changed, why, what helped,
what waits for you — and one notification for it."""
import time

import pytest

from eki import digest, selfloop
from tests.test_goals import eng  # noqa: F401 — the engine fixture

NOW = time.mktime((2026, 9, 24, 10, 0, 0, 0, 0, -1))


def change(cid, state, at=NOW - 3600, **kw):
    return {"id": cid, "state": state, "state_at": at, "title": f"change {cid}", **kw}


def test_the_page_says_what_changed_why_and_what_waits():
    rows = [change("a", "applied", source="roadmap", summary="- The chat list shows the project\n- more"),
            change("b", "unfit", verdict="tests failed: 3"),
            change("c", "proposed", fit=True, at=NOW - 30 * 86400),        # old, but it still waits
            change("d", "proposed", fit=True, protected=True),
            change("e", "applied", at=NOW - 3 * 86400)]                     # before the window
    items = [{"title": "Try it on a trackpad", "state": "person", "note": "needs a trackpad",
              "updated_at": NOW - 600}]
    page = digest.build(rows, items, since=NOW - 86400, now=NOW,
                        work={"local_hours": 2.5, "week_hours_a_day": 1.2, "goal_turns": 4})
    text = page["text"]
    assert "[change a](#/self/a) — The chat list shows the project (why: the next ROADMAP item)" in text
    assert "change e" not in text
    assert "**Went wrong**" in text and "[change b](#/self/b) — didn't pass its checks: tests failed: 3" in text
    assert "2.5 h of work (the week's average: 1.2 h a day)" in text and "4 goal turns finished" in text
    assert "[change c](#/self/c) — proposed" in text
    assert "[change d](#/self/d) — touches what eki may not change alone" in text
    assert "Try it on a trackpad — needs a trackpad" in text
    assert (page["changed"], page["missed"], page["waiting"], page["quiet"]) == (1, 1, 3, False)
    assert page["id"] == "2026-09-24"
    said = digest.notification(page)
    assert said["title"] == "eki's day" and said["body"].startswith("1 change · 1 went wrong · 3 wait")


def test_a_quiet_day_is_kept_but_not_said():
    page = digest.build([], since=NOW - 86400, now=NOW)
    assert page["quiet"] and "quiet day" in page["text"]
    assert digest.notification(page) is None


def test_every_change_of_the_day_is_on_the_page_in_its_group():
    states = ["applied", "applying", "undone", "rolled back", "unfit", "stopped", "not started",
              "no change", "discarded", "gone"]
    rows = [change(f"x{n}", s) for n, s in enumerate(states)]
    rows += [change(f"a{n}", "applied") for n in range(12)]                  # a long day: nothing cut
    rows += [change("w", "conflicts", fit=True)]
    page = digest.build(rows, since=NOW - 86400, now=NOW)
    text = page["text"]
    assert "more" not in text
    for c in rows:
        assert text.count(f"](#/self/{c['id']})") == 1, c["id"]            # each once, linked
    landed, wrong, waits = (text.split(f"**{h}**")[1].split("\n\n")[0]
                            for h in ("Landed", "Went wrong", "Waits for you"))
    assert landed.count("\n- ") == 15 and "#/self/x2) · taken back" in landed
    assert wrong.count("\n- ") == 7 and "#/self/x3) — rolled back" in wrong
    assert waits.count("\n- ") == 1 and "conflicts with your checkout" in waits
    assert (page["changed"], page["missed"], page["waiting"]) == (15, 7, 1)


def test_the_page_reads_plainly_in_a_terminal():
    text = digest.build([change("ab12", "applied", title="Make [it] so")], since=NOW - 86400, now=NOW)["text"]
    assert "[Make (it) so](#/self/ab12)" in text
    assert "- Make (it) so (self/ab12)" in digest.plain(text)


def test_once_a_day_after_its_time():
    early = time.mktime((2026, 9, 24, 8, 0, 0, 0, 0, -1))
    assert not digest.due(early, at="09:00")
    assert digest.due(NOW, at="09:00")
    digest.save(digest.build([], since=NOW - 86400, now=NOW))
    assert not digest.due(NOW + 3600, at="09:00")                           # written today
    assert digest.due(NOW + 86400, at="09:00")
    assert digest.since(NOW + 86400) == NOW                                 # from where the last ended
    assert not digest.due(early, at="nonsense")                             # unreadable: 09:00


def test_only_what_needs_you_is_its_own_notification():
    assert digest.needs_you({"state": "proposed", "fit": True})
    assert digest.needs_you({"state": "unfit"}, item_state="gave up")
    assert not digest.needs_you({"state": "applied", "fit": True})
    assert not digest.needs_you({"state": "applying", "fit": True})
    assert not digest.needs_you({"state": "unfit"}, item_state="queued")   # tried again later


@pytest.mark.asyncio
async def test_the_engine_writes_it_once_and_says_it_once(eng, monkeypatch):   # noqa: F811
    told = []

    async def notify(title, body):
        told.append((title, body))
    monkeypatch.setattr(eng, "_notify", notify)
    eng.settings = {**eng.settings, "notify_learned": True, "digest_at": "00:00"}
    selfloop.update(selfloop.add("asked", "Fix the rail").id, state="gave up", note="twice unfit")
    page = await eng.self_digest()
    assert page and "Fix the rail — twice unfit" in page["text"]
    assert told == [("eki's day", "1 waits for you — Goals → Self")]
    assert await eng.self_digest() is None                                  # once a day
    assert eng.self_view()["digest"]["id"] == page["id"]
    eng.settings["digest"] = "off"
    assert await eng.self_digest(force=False) is None
    assert (await eng.self_digest(force=True))["id"] == page["id"]         # now, when asked
    await eng.runner.stop()


def test_the_board_gets_every_change_the_older_ones_slim(eng):   # noqa: F811
    from eki import selfengine, selfwork
    for n in range(selfengine.CHANGES_FULL + 5):
        selfwork.record(selfwork.Proposal(id=f"c{n:02d}", request=f"change {n}", root="/tmp",
                                          report={"checks": []}, at=int(NOW) + n))
    rows = eng.self_view()["changes"]
    assert len(rows) == selfengine.CHANGES_FULL + 5                         # none cut off
    assert "report" in rows[0] and "report" not in rows[-1]
    assert {"id", "title", "state", "state_at"} <= set(rows[-1])
