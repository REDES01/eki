"""Needs-you hook on asks: an agent's question or permission queues one push — never the network."""
import json

from eki import asks, db, notify, store

TOPIC = "xz-secret-topic-9f3k"


def set_topic(home, topic=TOPIC):
    path = home / "routing.json"
    data = json.loads(path.read_text())
    data["notify"] = {"topic": topic}
    path.write_text(json.dumps(data))


def run_ids(conn):
    with db.tx(conn):
        tid = store.create_thread(conn, "t", None)
        rid = store.create_run(conn, tid, "p")
    return rid, tid


def create(conn, kind, payload):
    rid, tid = run_ids(conn)
    with db.tx(conn):
        return asks.create(conn, rid, tid, kind, payload)


def notices(conn):
    return [dict(r) for r in conn.execute("SELECT key, kind, title, body, click FROM notices ORDER BY created_at")]


def test_a_question_queues_one_notice_keyed_by_the_ask(home, conn):
    set_topic(home)
    aid = create(conn, "question", {"questions": [{"question": "Which colour?", "header": "Pick"}]})
    [n] = notices(conn)
    assert n["key"] == f"ask:{aid}" and n["kind"] == "needs_you" and n["title"] == "eki · needs you"
    assert n["body"] == f"Which colour? — eki answer {aid}" and n["click"] is None
    assert TOPIC not in n["body"]


def test_a_permission_names_the_tool(home, conn):
    set_topic(home)
    aid = create(conn, "permission", {"tool": "Bash", "title": "Run the tests", "detail": "pytest"})
    [n] = notices(conn)
    assert n["key"] == f"ask:{aid}"
    assert n["body"] == f"permission for Bash: Run the tests — eki answer {aid}"


def test_a_form_uses_its_message_and_empty_payloads_say_something_generic(home, conn):
    set_topic(home)
    f = create(conn, "form", {"server": "s", "message": "Log in\nplease"})
    q = create(conn, "question", {"questions": []})
    p = create(conn, "permission", {"tool": "", "title": "  "})
    bodies = {n["key"]: n["body"] for n in notices(conn)}
    assert bodies[f"ask:{f}"] == f"Log in please — eki answer {f}"
    assert bodies[f"ask:{q}"] == f"a question — eki answer {q}"
    assert bodies[f"ask:{p}"] == f"a permission — eki answer {p}"


def test_the_body_is_one_short_line(home, conn):
    set_topic(home)
    aid = create(conn, "question", {"questions": [{"question": "why\n" * 200}]})
    [n] = notices(conn)
    assert len(n["body"]) <= 200 and "\n" not in n["body"]
    assert n["body"].endswith(f"… — eki answer {aid}")


def test_answering_withdrawing_or_taking_answers_queues_nothing_more(home, conn):
    set_topic(home)
    a = create(conn, "question", {"questions": [{"question": "Which?"}]})
    b = create(conn, "permission", {"tool": "Edit"})
    assert len(notices(conn)) == 2
    asks.answer(conn, a, {"allow": True, "answers": {"Which?": "x"}})
    asks.take_answers(conn, [a])
    with db.tx(conn):
        asks.withdraw(conn, b)
        asks.withdraw_run(conn, asks.get(conn, b)["run_id"])
    assert len(notices(conn)) == 2


def test_an_empty_topic_queues_nothing(home, conn):
    set_topic(home, "")
    create(conn, "question", {"questions": [{"question": "Which?"}]})
    assert notices(conn) == []


def test_create_survives_notify_failing(home, conn, monkeypatch):
    set_topic(home)

    def boom(*a, **kw):
        raise RuntimeError("notify is broken")

    monkeypatch.setattr(notify, "queue", boom)
    aid = create(conn, "question", {"questions": [{"question": "Which?"}]})
    assert asks.get(conn, aid)["state"] == "open"
    assert notices(conn) == []
