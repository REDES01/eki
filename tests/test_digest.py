import json
import time

from eki import db, digest

DAY = 86400.0


def local(y, mo, d, h, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


NOW = local(2026, 9, 26, 10)


def add_item(conn, iid, state, updated_at, title=None, **more):
    cols = {"id": iid, "goal_id": "g1", "title": title or f"title {iid}", "state": state,
            "created_at": updated_at - 100, "updated_at": updated_at, **more}
    conn.execute(f"INSERT INTO items({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 list(cols.values()))


def add_journal(conn, t, kind, data=None, build=None):
    conn.execute("INSERT INTO journal(t, kind, build, data) VALUES (?,?,?,?)",
                 (t, kind, build, json.dumps(data or {})))


def set_self(home, **self):
    p = home / "routing.json"
    cfg = json.loads(p.read_text())
    cfg["self"] = self
    p.write_text(json.dumps(cfg))


def lines_with(text, iid):
    return [ln for ln in text.splitlines() if ln.startswith(f"- {iid} ")]


def section(text, name):
    part = text.split(f"## {name}\n", 1)[1]
    return part.split("\n## ", 1)[0]


def test_every_changed_item_once_in_its_group(conn):
    t = NOW - 3600
    add_item(conn, "live1", "live", t, build="b7")
    add_item(conn, "land1", "landed", t, build="b8")
    add_item(conn, "app1", "applied", t)
    add_item(conn, "unfit1", "unfit", t, error="gate 2 failed", verdict="FAILED tests/test_x.py\nmore")
    add_item(conn, "train1", "unfit", t, error="the train's check failed", verdict="boom in check\n...")
    add_item(conn, "back1", "rolled back", t, error="rolled back from build b6: exit 1 after 3s")
    add_item(conn, "left1", "left", t, error="the rebase onto abc failed: conflict")
    add_item(conn, "lock1", "locked", t, locked=json.dumps(["eki/engine.py"]))
    add_item(conn, "prop1", "proposed", t)
    add_item(conn, "build1", "building", t)
    add_item(conn, "old1", "live", NOW - 3 * DAY, build="b1")     # unchanged: not on the page
    add_item(conn, "late1", "live", NOW + 60, build="b9")          # after the page: not on it
    text = digest.write(conn, NOW).read_text()

    assert lines_with(text, "old1") == [] and lines_with(text, "late1") == []
    want = {"Landed and live": ["live1", "land1", "app1"],
            "Went wrong": ["unfit1", "train1", "back1", "left1"],
            "Waits for you": ["lock1", "prop1"],
            "Still moving": ["build1"]}
    for group, ids in want.items():
        body = section(text, group)
        for iid in ids:
            assert len(lines_with(text, iid)) == 1, iid
            assert lines_with(body, iid), (group, iid)
    assert "live in build b7" in lines_with(text, "live1")[0]
    assert "b8" in lines_with(text, "land1")[0]
    assert "gate 2 failed" in lines_with(text, "unfit1")[0]
    assert "reverted by the train" in lines_with(text, "train1")[0]
    assert "boom in check" in lines_with(text, "train1")[0]
    assert "exit 1" in lines_with(text, "back1")[0]
    assert "waits for you" in lines_with(text, "left1")[0] and "conflict" in lines_with(text, "left1")[0]
    assert "eki/engine.py" in lines_with(text, "lock1")[0]
    assert "proposed; `eki self apply prop1`" in lines_with(text, "prop1")[0]
    assert "title live1" in lines_with(text, "live1")[0]


def test_empty_groups_say_so(conn):
    text = digest.write(conn, NOW).read_text()
    assert "## Landed and live\n\nNothing." in text
    assert "## Still moving" not in text
    assert "## Worse builds\n\nNone." in text


def test_score_section_and_worse_build(conn):
    for i in range(4):
        add_journal(conn, NOW - 3600 - i, "run", {"state": "done", "local": True, "first_text": 2.0})
    add_journal(conn, NOW - DAY - 3600, "run", {"state": "done", "local": False})
    add_journal(conn, NOW - 100, "fault", {"frame": "eki/x.py:1"})
    add_journal(conn, NOW - 200, "correction", {})
    add_journal(conn, NOW - 300, "correction", {})
    conn.execute("INSERT INTO build_scores(build, healthy_at, before, after, measured_at, verdict)"
                 " VALUES ('bad1', ?, '{}', '{}', ?, 'worse')", (NOW - 2 * DAY, NOW - 600))
    conn.execute("INSERT INTO build_scores(build, healthy_at, before, after, measured_at, verdict)"
                 " VALUES ('ok1', ?, '{}', '{}', ?, 'same')", (NOW - 2 * DAY, NOW - 600))
    text = digest.write(conn, NOW).read_text()
    score = section(text, "Score")
    assert "| runs | 4 | 1 |" in score
    assert "| local share | 100% | 0% |" in score
    assert "1 fault(s), 2 correction(s)" in score
    worse = section(text, "Worse builds")
    assert "`eki self undo bad1`" in worse and "ok1" not in worse


def test_due_before_after_and_once_a_day(conn, home):
    set_self(home, digest_at="09:30")
    assert not digest.due(local(2026, 9, 26, 9, 29))
    assert digest.due(local(2026, 9, 26, 9, 30))
    assert digest.tick(conn, local(2026, 9, 26, 8)) is None
    first = digest.tick(conn, local(2026, 9, 26, 9, 45))
    assert first is not None and first.name == "2026-09-26.md"
    assert not digest.due(local(2026, 9, 26, 12))
    assert digest.tick(conn, local(2026, 9, 26, 12)) is None
    assert digest.tick(conn, local(2026, 9, 27, 9, 31)).name == "2026-09-27.md"
    assert digest.latest().name == "2026-09-27.md"


def test_default_time_is_nine(conn):
    assert not digest.due(local(2026, 9, 26, 8, 59))
    assert digest.due(local(2026, 9, 26, 9, 0))


def test_next_page_starts_where_the_last_ended(conn):
    first_at = NOW
    digest.write(conn, first_at)
    assert digest.last() == first_at
    add_item(conn, "a", "live", first_at - 10, build="b1")      # on the first page's window only
    add_item(conn, "b", "live", first_at + 10, build="b2")
    second = digest.write(conn, first_at + DAY)
    text = second.read_text()
    assert lines_with(text, "a") == [] and len(lines_with(text, "b")) == 1
    assert digest.last() == first_at + DAY


def test_first_page_covers_the_last_day(conn):
    add_item(conn, "in", "live", NOW - DAY + 60)
    add_item(conn, "out", "live", NOW - DAY - 60)
    text = digest.write(conn, NOW).read_text()
    assert lines_with(text, "in") and not lines_with(text, "out")


def test_rewrite_today_and_restart_change_nothing(conn):
    add_item(conn, "x", "live", NOW - 60, build="b1")
    path = digest.write(conn, NOW)
    text = path.read_text()
    fresh = db.connect()                   # a restart: nothing held in memory
    assert not digest.due(NOW + 60)
    assert digest.tick(fresh, NOW + 60) is None
    assert path.read_text() == text and digest.last() == NOW
    assert digest.latest() == path
    assert not list(digest.folder().glob(".*.tmp"))
