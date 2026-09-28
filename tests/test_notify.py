"""Notify core: headers, send, the notices table and the drain — never the real network."""
import base64
import json
import logging
import socket
import urllib.error
import urllib.request

import pytest

from eki import db, housekeep, notify

TOPIC = "xz-secret-topic-9f3k"


def set_notify(home, **kw):
    path = home / "routing.json"
    data = json.loads(path.read_text())
    data["notify"] = kw
    path.write_text(json.dumps(data))


class Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status


class Posts:
    """Every POST made; each is answered with `reply` (a status, or an exception to raise)."""

    def __init__(self):
        self.made = []
        self.reply = 200

    def __call__(self, req, timeout=None):
        self.made.append({"url": req.full_url, "data": req.data, "headers": dict(req.header_items()),
                          "method": req.get_method(), "timeout": timeout})
        if isinstance(self.reply, BaseException):
            raise self.reply
        return Resp(self.reply)


@pytest.fixture
def posts(monkeypatch):
    p = Posts()
    monkeypatch.setattr(urllib.request, "urlopen", p)
    return p


@pytest.fixture
def lit(home):
    set_notify(home, topic=TOPIC)
    return home


def test_defaults_and_a_broken_file(home):
    assert notify.settings() == notify.DEFAULTS
    (home / "routing.json").write_text("{not json")
    assert notify.settings() == notify.DEFAULTS
    assert not notify.on("goal_done")


def test_on_needs_topic_and_event(home):
    set_notify(home, topic=TOPIC, events=["needs_you"])
    assert notify.on("needs_you") and not notify.on("goal_done")
    set_notify(home, topic="", events=["needs_you"])
    assert not notify.on("needs_you")


def test_masked():
    assert notify.masked("") == "off"
    assert notify.masked(TOPIC) == "xz…"
    assert TOPIC not in notify.masked(TOPIC)


def test_headers_for_each_kind():
    h = notify.headers("needs_you", "eki · needs you")
    assert h["Priority"] == "high" and h["Tags"] == "bell" and "Click" not in h
    h = notify.headers("goal_done", "eki · goal done", click="https://github.com/o/r/pull/1")
    assert h["Priority"] == "default" and h["Tags"] == "white_check_mark"
    assert h["Click"] == "https://github.com/o/r/pull/1"
    h = notify.headers("test", "plain")
    assert h == {"Title": "plain", "Priority": "default", "Tags": "test_tube"}


def test_a_non_ascii_title_is_rfc2047_encoded():
    t = notify.headers("goal_done", "eki · goal done")["Title"]
    assert t.isascii() and t.startswith("=?UTF-8?B?") and t.endswith("?=")
    assert base64.b64decode(t[len("=?UTF-8?B?"):-2]).decode("utf-8") == "eki · goal done"
    t.encode("latin-1")                                  # what http.client needs


def test_send_builds_url_body_and_headers(lit, posts):
    assert notify.send("needs_you", "eki · needs you", "item x locked — eki self apply x --yes")
    p = posts.made[0]
    assert p["url"] == f"https://ntfy.sh/{TOPIC}" and p["method"] == "POST" and p["timeout"] == 5
    assert p["data"] == "item x locked — eki self apply x --yes".encode("utf-8")
    assert p["headers"]["Priority"] == "high"
    set_notify(lit, topic=TOPIC, server="https://ntfy.example/")
    assert notify.send("goal_done", "t", "b")
    set_notify(lit, topic=TOPIC, server="https://ntfy.example")
    assert notify.send("goal_done", "t", "b")
    assert [p["url"] for p in posts.made[1:]] == [f"https://ntfy.example/{TOPIC}"] * 2


@pytest.mark.parametrize("reply", [
    socket.timeout("timed out"),
    urllib.error.URLError("no route"),
    urllib.error.HTTPError(f"https://ntfy.sh/{TOPIC}", 500, "boom", {}, None),
    RuntimeError(f"weird failure at https://ntfy.sh/{TOPIC}"),
])
def test_send_fails_quietly(lit, posts, reply):
    posts.reply = reply
    assert notify.send("goal_done", "t", "b") is False


def test_a_non_2xx_reply_is_not_sent(lit, posts):
    posts.reply = 302
    assert notify.send("goal_done", "t", "b") is False


def test_off_does_nothing(conn, posts):
    assert notify.send("goal_done", "t", "b") is False
    assert notify.queue(conn, "g1:done", "goal_done", "t", "b") is False
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0
    assert notify.tick(conn) == []
    assert posts.made == []


def test_an_event_not_chosen_is_not_queued(home, conn):
    set_notify(home, topic=TOPIC, events=["goal_done"])
    assert notify.queue(conn, "a1", "needs_you", "t", "b") is False
    assert notify.queue(conn, "g1:done", "goal_done", "t", "b") is True


def test_queue_dedupes_and_works_inside_a_tx(lit, conn):
    with db.tx(conn):
        assert notify.queue(conn, "g1:done", "goal_done", "eki · goal done", "Goal one")
        assert not notify.queue(conn, "g1:done", "goal_done", "eki · goal done", "Goal one")
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 1


def test_queue_never_raises(lit, conn):
    conn.close()
    assert notify.queue(conn, "k", "goal_done", "t", "b") is False


def drain(conn):
    lines = notify.tick(conn)
    notify.wait()
    return lines


def test_drain_sends_once_across_a_restart(lit, posts):
    c = db.connect()
    assert notify.queue(c, "g1:done", "goal_done", "eki · goal done", "Goal one", click="https://x/pr/1")
    lines = drain(c)
    assert lines == ["notify: sending 1 notice"]
    c.close()
    c = db.connect()                                      # a restart
    assert not notify.queue(c, "g1:done", "goal_done", "eki · goal done", "Goal one")
    assert drain(c) == []
    assert len(posts.made) == 1
    assert posts.made[0]["headers"]["Click"] == "https://x/pr/1"
    row = c.execute("SELECT * FROM notices").fetchone()
    assert row["sent_at"] and row["sending_at"] is None and row["tries"] == 0


def test_drain_returns_before_sending(lit, conn, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(notify, "_post", lambda *a: gate.wait(5) and None)
    notify.queue(conn, "a", "needs_you", "t", "b")
    assert notify.tick(conn) == ["notify: sending 1 notice"]
    notify.queue(conn, "b", "needs_you", "t", "b")
    assert notify.tick(conn) == []                        # one drain at a time
    gate.set()
    notify.wait()
    assert drain(conn) == ["notify: sending 1 notice"]


def test_a_claimed_row_left_by_a_crash_is_sent_after_a_minute(lit, conn, posts):
    notify.queue(conn, "a", "needs_you", "t", "b")
    conn.execute("UPDATE notices SET sending_at=?", (db.now() - 10,))
    assert drain(conn) == [] and posts.made == []
    conn.execute("UPDATE notices SET sending_at=?", (db.now() - 61,))
    assert drain(conn) == ["notify: sending 1 notice"]
    assert len(posts.made) == 1
    assert conn.execute("SELECT sent_at FROM notices").fetchone()[0]


def test_failures_count_and_are_logged_once_without_the_topic(lit, conn, posts, caplog):
    posts.reply = RuntimeError(f"cannot reach https://ntfy.sh/{TOPIC}")
    notify.queue(conn, "item1:locked", "needs_you", "t", "b")
    caplog.set_level(logging.WARNING, logger="eki.notify")
    said = []
    for _ in range(5):
        said += drain(conn)
    row = conn.execute("SELECT * FROM notices").fetchone()
    assert row["tries"] == 3 and row["sent_at"] is None
    assert len(posts.made) == 3
    logged = [r.getMessage() for r in caplog.records if r.name == "eki.notify"]
    assert len(logged) == 1 and "item1:locked" in logged[0]
    for text in logged + said + [row["error"]]:
        assert TOPIC not in text
    assert "RuntimeError" in row["error"]


def test_the_drain_is_the_last_housekeeping_step():
    assert housekeep.STEPS[-1] == ("notify", notify.tick)
