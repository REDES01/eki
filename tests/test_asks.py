import os
import signal
import time

from eki import asks, db, engine, store


def wait(cond, t=20):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def start(conn, prompt):
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "t", None), prompt)
    engine.spawn(conn, rid)
    return rid


def test_a_question_waits_for_you_and_the_answer_reaches_the_program(conn):
    rid = start(conn, "steps=1 ask")
    assert wait(lambda: asks.open_asks(conn))
    a = asks.view(asks.open_asks(conn)[0])
    assert a["kind"] == "question" and a["questions"][0]["question"] == "Which colour?"
    assert store.run(conn, rid)["state"] == "running"
    assert asks.answer(conn, a["id"], {"allow": True, "answers": {"Which colour?": "blue"}}) == "answered"
    assert wait(lambda: store.run(conn, rid)["state"] == "done")
    assert "picked blue" in store.answer(conn, rid)
    assert asks.view(asks.get(conn, a["id"]))["state"] == "delivered"
    assert asks.answer(conn, a["id"], {"allow": True}) == "delivered"      # answered once only


def test_a_worker_dying_while_asking_asks_again(conn):
    rid = start(conn, "steps=1 ask")
    assert wait(lambda: asks.open_asks(conn))
    first = asks.open_asks(conn)[0]["id"]
    os.kill(store.run(conn, rid)["pid"], signal.SIGKILL)
    assert wait(lambda: not engine.alive(store.run(conn, rid)["pid"]) or _reap(store.run(conn, rid)["pid"]))
    engine.interrupted(conn, store.run(conn, rid))
    assert store.run(conn, rid)["state"] == "queued"
    engine.spawn(conn, rid)
    assert wait(lambda: any(a["id"] != first for a in asks.open_asks(conn)))
    assert asks.view(asks.get(conn, first))["state"] == "withdrawn"
    again = [a for a in asks.open_asks(conn) if a["id"] != first][0]["id"]
    asks.answer(conn, again, {"allow": True, "answers": {"Which colour?": "red"}})
    assert wait(lambda: store.run(conn, rid)["state"] == "done")
    assert "picked red" in store.answer(conn, rid)


def _reap(pid):
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    return not engine.alive(pid)
