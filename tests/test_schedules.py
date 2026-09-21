# SPDX-License-Identifier: Apache-2.0
"""Things eki does on a timetable."""
import asyncio
import time
from datetime import datetime, timedelta

from eki import schedules as sm


def test_next_time_for_intervals_and_daily():
    now = time.time()
    assert sm.next_time({"kind": "interval", "minutes": 30}, now) == now + 1800
    # counted from the last firing, never drifting past "now"
    assert sm.next_time({"kind": "interval", "minutes": 30}, now, last=now - 3000) == now + 600
    at = datetime.fromtimestamp(now).replace(hour=9, minute=0, second=0, microsecond=0)
    nxt = sm.next_time({"kind": "daily", "at": "09:00"}, now)
    assert nxt > now and datetime.fromtimestamp(nxt).hour == 9
    assert nxt - now < 86400 + 1
    # weekdays only: a Saturday morning waits for Monday
    sat = at
    while sat.weekday() != 5:
        sat += timedelta(days=1)
    nxt = sm.next_time({"kind": "daily", "at": "09:00", "days": [0, 1, 2, 3, 4]}, sat.timestamp())
    assert datetime.fromtimestamp(nxt).weekday() == 0
    assert sm.next_time({"kind": "never"}, now) is None


def test_describe():
    assert sm.describe({"kind": "interval", "minutes": 120}) == "every 2 hours"
    assert sm.describe({"kind": "interval", "minutes": 1440}) == "every 1 day"
    assert sm.describe({"kind": "interval", "minutes": 45}) == "every 45 min"
    assert sm.describe({"kind": "daily", "at": "07:30", "days": [0, 1, 2, 3, 4]}) == "weekdays at 07:30"
    assert sm.describe({"kind": "daily", "at": "10:00", "days": [1, 3]}) == "Tue, Thu at 10:00"
    assert sm.describe({"kind": "daily", "at": "10:00"}) == "every day at 10:00"


def test_the_table_keeps_and_advances_schedules(tmp_path):
    table = sm.Schedules(tmp_path / "eki.db")
    s = table.create("Morning review", "Summarise the open PRs", {"kind": "interval", "minutes": 60},
                     cwd="/tmp/repo", backend="claude")
    assert s.next_run_at and s.next_run_at > time.time()
    assert table.due() == []
    # its time comes
    table.update(s.id, spec={"kind": "interval", "minutes": 1})
    got = table.get(s.id)
    got.next_run_at = int(time.time()) - 10
    table._put(got)
    assert [d.id for d in table.due()] == [s.id]
    table.advance(s.id, ran=True, conversation="c1", run="r1")
    again = table.get(s.id)
    assert again.last_conversation == "c1" and again.last_run == "r1"
    assert again.next_run_at > time.time() and table.due() == []
    # missed by a long sleep: skipped, not run stale
    again.next_run_at = int(time.time()) - sm.CATCH_UP_SECONDS - 60
    table._put(again)
    assert table.due() == [] and table.get(s.id).next_run_at > time.time()
    # off means never due
    table.update(s.id, enabled=False)
    assert table.get(s.id).next_run_at is None and table.due() == []
    assert table.delete(s.id) and table.get(s.id) is None


def test_a_firing_is_an_ordinary_run_in_a_named_thread(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from eki import settings
    from eki.engine import Engine
    from eki.store import Store
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    settings.save({"notify_scheduled": False})
    table = sm.Schedules(tmp_path / "eki.db")
    s = table.create("Nightly tidy", "Run the tests and fix what fails", {"kind": "interval", "minutes": 60},
                     cwd="/tmp/repo", backend="codex")
    asked = {}

    async def ask(prompt, conversation="", backend_key="", repo="", images=False):
        asked.update(prompt=prompt, conversation=conversation, backend_key=backend_key, repo=repo)
        return {"run": "r9", "conversation": conversation}

    fake = NS(store=Store(tmp_path / "eki.db"), schedules=table, ask=ask,
              settings=settings.load(), _side_tasks=set())
    started = asyncio.run(Engine.fire(fake, s))
    assert started["run"] == "r9" and asked["backend_key"] == "codex" and asked["repo"] == "/tmp/repo"
    rows = fake.store.conversations()
    assert rows[0]["title"].startswith("Nightly tidy · ")
    after = table.get(s.id)
    assert after.last_run == "r9" and after.last_conversation == rows[0]["id"]
