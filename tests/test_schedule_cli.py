"""`eki schedule`: add, list, pause, resume, drop and run round-trip; clear errors."""
import pytest

from eki import projectchats, schedule, store
from eki.cli import main


@pytest.fixture(autouse=True)
def tokyo(monkeypatch):
    monkeypatch.setenv("EKI_TZ", "Asia/Tokyo")


def out(capsys, *argv):
    assert main(["schedule", *argv]) == 0
    return capsys.readouterr().out


def test_round_trip(conn, capsys, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    pid = projectchats.create(conn, "work", folder)
    said = out(capsys, "add", "Check the build", "--every", "weekly", "--day", "mon", "--at", "9:00",
               "--project", "work", "--to", "fake:m1", "--effort", "high")
    sid = said.splitlines()[0].strip()
    assert "Weekly on Mon at 09:00 (Asia/Tokyo), next Mon" in said
    s = schedule.find(conn, sid)
    assert s["project"] == pid and s["state"] == "on"
    assert schedule.listing(conn)[0]["target"] == {"provider": "fake", "model": "m1", "effort": "high"}

    listed = out(capsys, "list")
    assert sid in listed and "Weekly on Mon at 09:00" in listed and "Check the build" in listed

    assert "paused" in out(capsys, "pause", sid[:6])
    assert schedule.find(conn, sid)["state"] == "paused"
    assert ": on" in out(capsys, "resume", sid)
    assert schedule.find(conn, sid)["state"] == "on"

    next_at = schedule.find(conn, sid)["next_at"]
    said = out(capsys, "run", sid[:6])
    tid = said.strip().rsplit(" ", 1)[1]
    t = store.thread(conn, tid)
    assert t["schedule"] == sid
    s = schedule.find(conn, sid)
    assert s["last_thread"] == tid and s["next_at"] == next_at
    assert tid in out(capsys, "list")

    assert "dropped" in out(capsys, "drop", sid)
    assert sid not in out(capsys, "list")


def test_hourly_and_auto(conn, capsys):
    sid = out(capsys, "add", "tidy", "--every", "hourly", "--minute", "15").splitlines()[0].strip()
    s = schedule.listing(conn)[0]
    assert s["id"] == sid and s["cadence"] == {"every": "hourly", "minute": 15} and s["target"] is None


@pytest.mark.parametrize("argv, words", [
    (["--every", "daily", "--at", "25:00"], "25:00"),
    (["--every", "daily", "--at", "9am"], "HH:MM"),
    (["--every", "daily"], "needs --at"),
    (["--every", "weekly", "--at", "09:00"], "needs --day"),
    (["--every", "daily", "--at", "09:00", "--project", "nowhere"], "nowhere"),
    (["--every", "daily", "--at", "09:00", "--effort", "high"], "--effort needs --to"),
])
def test_clear_errors(conn, capsys, argv, words):
    assert main(["schedule", "add", "hi", *argv]) == 1
    err = capsys.readouterr().err
    assert err.startswith("eki: ") and words in err
    assert schedule.listing(conn) == []


def test_unknown_id(conn, capsys):
    assert main(["schedule", "pause", "nope"]) == 1
    assert "no schedule nope" in capsys.readouterr().err
