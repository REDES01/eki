"""Scheduled prompts: the clock, firing once after a sleep, skips, and the done notice."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from eki import db, housekeep, projectchats, schedule, store

TOKYO = ZoneInfo("Asia/Tokyo")


def ts(*parts):
    return datetime(*parts, tzinfo=TOKYO).timestamp()


def test_next_fire_in_tokyo():
    daily = {"every": "daily", "at": "09:00"}
    assert schedule.next_fire(daily, ts(2026, 10, 9, 8, 59), "Asia/Tokyo") == ts(2026, 10, 9, 9, 0)
    assert schedule.next_fire(daily, ts(2026, 10, 9, 9, 0), "Asia/Tokyo") == ts(2026, 10, 10, 9, 0)
    # 2026-10-09 is a Friday
    weekdays = {"every": "weekdays", "at": "09:00"}
    assert schedule.next_fire(weekdays, ts(2026, 10, 9, 10, 0), "Asia/Tokyo") == ts(2026, 10, 12, 9, 0)
    weekly = {"every": "weekly", "day": "sun", "at": "18:30"}
    assert schedule.next_fire(weekly, ts(2026, 10, 11, 18, 30), "Asia/Tokyo") == ts(2026, 10, 18, 18, 30)
    hourly = {"every": "hourly", "minute": 15}
    assert schedule.next_fire(hourly, ts(2026, 10, 9, 10, 20), "Asia/Tokyo") == ts(2026, 10, 9, 11, 15)
    # the UTC instant: 09:00 in Tokyo is 00:00 UTC
    utc = datetime(2026, 10, 9, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    assert schedule.next_fire(daily, ts(2026, 10, 9, 8, 59), "Asia/Tokyo") == utc


def test_cadence_check_and_text():
    assert schedule.cadence_text({"every": "hourly", "minute": 15}) == "Hourly at :15"
    assert schedule.cadence_text({"every": "daily", "at": "9:00"}) == "Daily at 09:00"
    assert schedule.cadence_text({"every": "weekdays", "at": "09:00"}) == "Weekdays at 09:00"
    assert schedule.cadence_text({"every": "weekly", "day": "mon", "at": "09:00"}) == "Weekly on Mon at 09:00"
    for bad in ({"every": "daily", "at": "25:00"}, {"every": "hourly", "minute": 60},
                {"every": "weekly", "day": "funday", "at": "09:00"}, {"every": "monthly"}, "nope"):
        with pytest.raises(ValueError):
            schedule.check(bad)


def test_local_tz(monkeypatch):
    monkeypatch.setenv("EKI_TZ", "Asia/Tokyo")
    assert schedule.local_tz() == "Asia/Tokyo"
    monkeypatch.setenv("EKI_TZ", "Not/AZone")
    assert schedule.local_tz()           # falls through to the link or UTC


@pytest.fixture
def tokyo(monkeypatch):
    monkeypatch.setenv("EKI_TZ", "Asia/Tokyo")


def _runs(conn):
    return conn.execute("SELECT * FROM runs").fetchall()


def test_add_checks(conn, tokyo, tmp_path):
    with pytest.raises(ValueError):
        schedule.add(conn, "hi", {"every": "daily", "at": "9am"})
    with pytest.raises(KeyError):
        schedule.add(conn, "hi", {"every": "daily", "at": "09:00"}, project="nowhere")
    with pytest.raises(ValueError):
        schedule.add(conn, "hi", {"every": "daily", "at": "09:00"}, target="nobody:x")
    sid = schedule.add(conn, "hi", {"every": "daily", "at": "09:00"}, target="fake")
    row = schedule.find(conn, sid[:6])
    assert row["tz"] == "Asia/Tokyo" and row["state"] == "on" and row["next_at"] > db.now()
    assert [s["id"] for s in schedule.listing(conn)] == [sid]


def test_missed_while_asleep_fires_once(conn, tokyo, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    pid = projectchats.create(conn, "work", folder)
    sid = schedule.add(conn, "Check the build\nand say what broke", {"every": "daily", "at": "09:00"},
                       project=pid, target={"provider": "fake", "model": "m1", "effort": "high"})
    now = ts(2026, 10, 9, 12, 0)
    conn.execute("UPDATE schedules SET next_at=? WHERE id=?", (now - 3 * 86400, sid))
    assert len(schedule.tick(conn, now)) == 1
    runs = _runs(conn)
    assert len(runs) == 1
    r = runs[0]
    assert (r["priority"], r["provider"], r["pinned"], r["model"], r["effort"]) == ("now", "fake", 1, "m1", "high")
    t = store.thread(conn, r["thread_id"])
    assert t["schedule"] == sid and t["project"] == pid and t["cwd"] == projectchats.norm(folder)
    assert t["title"] == "Check the build"
    s = schedule.find(conn, sid)
    assert s["next_at"] == ts(2026, 10, 10, 9, 0)
    assert s["last_thread"] == t["id"] and s["last_at"] == now
    assert schedule.tick(conn, now) == []
    assert len(_runs(conn)) == 1


def test_auto_target_is_left_to_routing(conn, tokyo):
    sid = schedule.add(conn, "hello", {"every": "hourly", "minute": 0})
    tid = schedule.run_now(conn, sid)
    r = store.thread_runs(conn, tid)[0]
    assert r["provider"] is None and r["pinned"] == 0 and store.thread(conn, tid)["cwd"] is None


def test_still_running_is_skipped(conn, tokyo):
    sid = schedule.add(conn, "hello", {"every": "daily", "at": "09:00"})
    tid = schedule.run_now(conn, sid)                     # its run is queued: still active
    now = ts(2026, 10, 9, 9, 0, 5)
    conn.execute("UPDATE schedules SET next_at=? WHERE id=?", (ts(2026, 10, 9, 9, 0), sid))
    schedule.tick(conn, now)
    s = schedule.find(conn, sid)
    assert len(_runs(conn)) == 1 and s["last_thread"] == tid
    assert s["why"] == "skipped 09:00: the last one is still running"
    assert s["next_at"] == ts(2026, 10, 10, 9, 0)


def test_paused_and_dropped_never_fire(conn, tokyo):
    a = schedule.add(conn, "a", {"every": "daily", "at": "09:00"})
    b = schedule.add(conn, "b", {"every": "daily", "at": "09:00"})
    schedule.pause(conn, a)
    schedule.drop(conn, b)
    conn.execute("UPDATE schedules SET next_at=0")
    assert schedule.tick(conn, db.now()) == []
    assert _runs(conn) == []
    assert [s["id"] for s in schedule.listing(conn)] == [a]
    schedule.resume(conn, a)
    s = schedule.find(conn, a)
    assert s["state"] == "on" and s["next_at"] > db.now()


def test_run_now_keeps_next_at(conn, tokyo):
    sid = schedule.add(conn, "hello", {"every": "daily", "at": "09:00"})
    before = schedule.find(conn, sid)["next_at"]
    schedule.run_now(conn, sid)
    assert schedule.find(conn, sid)["next_at"] == before
    assert len(_runs(conn)) == 1


def test_settle_queues_one_notice(conn, tokyo, home):
    routing = json.loads((home / "routing.json").read_text())
    (home / "routing.json").write_text(json.dumps({**routing, "notify": {"topic": "t"}}))
    sid = schedule.add(conn, "Morning summary\nmore", {"every": "daily", "at": "09:00"})
    tid = schedule.run_now(conn, sid)
    assert schedule.settle(conn) == []                   # still queued
    rid = store.thread_runs(conn, tid)[0]["id"]
    store.update_run(conn, rid, state="failed", ended_at=db.now())
    assert len(schedule.settle(conn)) == 1
    assert schedule.settle(conn) == []
    n = conn.execute("SELECT * FROM notices").fetchall()
    assert len(n) == 1
    assert (n[0]["key"], n[0]["kind"], n[0]["title"], n[0]["body"]) == (
        f"schedule:{tid}:done", "goal_done", "eki · scheduled: failed", "Morning summary")


def test_housekeeping_runs_both_before_notify():
    names = [n for n, _ in housekeep.STEPS]
    assert names.index("schedule") < names.index("schedulenotify") < names.index("notify")
