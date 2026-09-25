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
    assert "change a — The chat list shows the project (why: the next ROADMAP item)" in text
    assert "change e" not in text
    assert "**Tried, didn't land**" in text and "change b — tests failed: 3" in text
    assert "2.5 h of work (the week's average: 1.2 h a day)" in text and "4 goal turns finished" in text
    assert "change c — proposed" in text and "change d — touches what eki may not change alone" in text
    assert "Try it on a trackpad — needs a trackpad" in text
    assert (page["changed"], page["missed"], page["waiting"], page["quiet"]) == (1, 1, 3, False)
    assert page["id"] == "2026-09-24"
    said = digest.notification(page)
    assert said["title"] == "eki's day" and said["body"].startswith("1 change · 1 didn't land · 3 wait")


def test_a_quiet_day_is_kept_but_not_said():
    page = digest.build([], since=NOW - 86400, now=NOW)
    assert page["quiet"] and "quiet day" in page["text"]
    assert digest.notification(page) is None


def test_a_long_list_stays_short():
    rows = [change(str(n), "applied") for n in range(10)]
    text = digest.build(rows, since=NOW - 86400, now=NOW)["text"]
    assert text.count("\n- change") == digest.SHOWN and "and 4 more" in text


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
