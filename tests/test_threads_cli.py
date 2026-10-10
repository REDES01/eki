"""`eki threads`: your chats (eki's own with --all), and pin/rename/archive/delete by id."""
from eki import asking, store
from eki.cli import main


def listed(capsys, *extra):
    capsys.readouterr()
    assert main(["threads", *extra]) == 0
    return capsys.readouterr().out


def test_eki_own_threads_hidden_unless_all(conn, capsys):
    mine, _ = asking.submit(conn, "hello there")
    own = store.create_thread(conn, "a background check", None, by="eki")
    out = listed(capsys)
    assert mine in out and own not in out
    out = listed(capsys, "--all")
    assert mine in out and own in out


def test_pin_by_prefix(conn, capsys):
    tid, _ = asking.submit(conn, "hello there")
    assert main(["threads", "pin", tid[:6]]) == 0
    assert store.thread(conn, tid)["pinned_at"] is not None
    assert "*" in [l for l in listed(capsys).splitlines() if tid in l][0]
    assert main(["threads", "unpin", tid]) == 0
    assert store.thread(conn, tid)["pinned_at"] is None


def test_rename_sets_title_by_you(conn, capsys):
    tid, _ = asking.submit(conn, "hello there")
    assert main(["threads", "rename", tid, "My", "haiku", "chat"]) == 0
    t = store.thread(conn, tid)
    assert (t["title"], t["title_by"]) == ("My haiku chat", "you")
    assert "My haiku chat" in listed(capsys)


def test_archive_hides_and_unarchive_brings_back(conn, capsys):
    tid, _ = asking.submit(conn, "hello there")
    assert main(["threads", "archive", tid]) == 0
    assert tid not in listed(capsys)
    assert main(["threads", "unarchive", tid]) == 0
    assert tid in listed(capsys)


def test_delete_y_removes_it(conn, capsys):
    tid, _ = asking.submit(conn, "hello there")
    store.cancel(conn, store.thread_runs(conn, tid)[0]["id"])
    assert main(["threads", "delete", tid, "-y"]) == 0
    assert tid not in listed(capsys)
    assert main(["threads", tid]) == 1
    assert f"no thread {tid}" in capsys.readouterr().err


def test_delete_of_eki_own_is_refused(conn, capsys):
    own = store.create_thread(conn, "a background check", None, by="eki")
    assert main(["threads", "delete", own, "-y"]) == 1
    assert "eki's own thread" in capsys.readouterr().err
    assert store.thread(conn, own) is not None
