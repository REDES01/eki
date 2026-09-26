import time

from eki import capacity, db, quota, routing, store


def later(h):
    return time.time() + h * 3600


def test_a_reading_is_kept_and_a_reset_window_is_empty_again(conn):
    quota.record(conn, "fake", {"five_hour": {"used": 0.5, "resets_at": later(1)},
                                "seven_day": {"used": 0.9, "resets_at": time.time() - 5}}, "pro")
    r = quota.reading(conn, "fake")
    assert r["plan"] == "pro" and r["windows"]["seven_day"]["used"] == 0
    assert quota.fullest(conn, "fake")["label"] == "5h"


def test_a_used_up_plan_takes_nothing(conn):
    quota.record(conn, "fake", {"five_hour": {"used": 0.99, "resets_at": later(2)}})
    ok, why = capacity.status(conn)["fake"]
    assert not ok and "5h window used up" in why


def test_background_work_leaves_the_rest_for_you(conn):
    quota.record(conn, "fake", {"seven_day": {"used": 0.75, "resets_at": later(50)}})
    assert capacity.status(conn)["fake"][0]
    ok, why = capacity.status(conn, background=True)["fake"]
    assert not ok and "kept for you" in why


def test_a_plan_near_its_end_goes_last_in_its_row(conn):
    quota.record(conn, "fake", {"seven_day": {"used": 0.85, "resets_at": later(50)}})
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "t", None), "x")
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider == "fake2" and "→ fake2 (fake last: week 85%)" in d.why


def test_background_run_skips_a_plan_past_its_share(conn):
    quota.record(conn, "fake", {"seven_day": {"used": 0.72, "resets_at": later(50)}})
    quota.record(conn, "fake2", {"seven_day": {"used": 0.72, "resets_at": later(50)}})
    with db.tx(conn):
        rid = store.create_run(conn, store.create_thread(conn, "t", None), "x", priority="background")
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider is None and "kept for you" in d.why
