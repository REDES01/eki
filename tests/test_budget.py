import json
import time

from eki import budget, capacity, paths, quota


def at(h, m=0):
    """An epoch at this local time of day."""
    return time.mktime((2026, 9, 27, h, m, 0, 0, 0, -1))


def quota_settings(**q):
    p = paths.config("routing")
    cfg = json.loads(p.read_text())
    cfg["quota"] = q
    p.write_text(json.dumps(cfg))


def test_day_and_night_by_the_clock():
    assert budget.ceiling(at(22, 59)) == (0.5, "day")
    assert budget.ceiling(at(23, 0)) == (0.9, "night")
    assert budget.ceiling(at(6, 59)) == (0.9, "night")
    assert budget.ceiling(at(7, 0)) == (0.5, "day")


def test_a_night_that_does_not_wrap_midnight():
    quota_settings(night="01:00-05:00")
    assert budget.ceiling(at(0, 59))[1] == "day"
    assert budget.ceiling(at(1, 0))[1] == "night"
    assert budget.ceiling(at(5, 0))[1] == "day"


def test_the_last_tenth_is_always_yours():
    quota_settings(background_day=0.95, background_night=0.99)
    assert budget.ceiling(at(12))[0] == budget.NEVER_ABOVE
    assert budget.ceiling(at(2))[0] == budget.NEVER_ABOVE


def test_an_old_background_up_to_is_the_day_ceiling():
    quota_settings(background_up_to=0.7)
    assert budget.ceiling(at(12)) == (0.7, "day")
    assert "background_up_to" in json.loads(paths.config("routing").read_text())["quota"]
    quota_settings(background_up_to=0.7, background_day=0.4)
    assert budget.ceiling(at(12))[0] == 0.4


def test_over_the_ceiling_says_both_halves_of_the_day():
    ok, why = budget.allows("five_hour", 0.52, at(14), at(12))
    assert not ok and why == "5h at 52% (day budget 50%; night from 23:00 up to 90%)"
    assert budget.allows("five_hour", 0.52, at(14), at(23, 30))[0]


def test_a_week_ahead_of_pace_is_refused():
    now = at(12)
    resets = now + 0.65 * budget.WINDOW_SECONDS["seven_day"]
    ok, why = budget.allows("seven_day", 0.40, resets, now)
    assert not ok and why == "week ahead of pace (40% used, 35% of it gone)"
    assert budget.allows("seven_day", 0.30, resets, now)[0]
    assert budget.allows("five_hour", 0.40, now + 60, now)[0]      # 5h is not paced


def test_describe():
    assert budget.describe(at(12)) == \
        "budget: day, background up to 50% of each window (night 23:00-07:00 up to 90%)"
    assert budget.describe(at(2)).startswith("budget: night, background up to 90%")


def test_limits_keep_the_old_keys_and_add_the_budget():
    lim = capacity.limits()
    assert lim["stop_at"] == 0.98 and lim["save_from"] == 0.8
    assert lim["background_day"] == 0.5 and lim["background_night"] == 0.9
    assert lim["night"] == "23:00-07:00"


def test_background_is_refused_on_a_window_that_is_not_the_fullest(conn):
    t = time.time()
    quota.record(conn, "fake", {
        "five_hour": {"used": 0.45, "resets_at": t + 3600},
        "seven_day": {"used": 0.30, "resets_at": t + 0.8 * budget.WINDOW_SECONDS["seven_day"]}})
    assert quota.fullest(conn, "fake")["window"] == "five_hour"
    quota_settings(background_day=0.9, background_night=0.9)     # 45% of 5h is fine at any hour
    ok, why = capacity.status(conn, background=True)["fake"]
    assert not ok and why.startswith("kept for you: week ahead of pace (30% used, 20% of it gone)")
    assert capacity.status(conn, background=True)["fake2"][0]


def test_work_you_wait_for_ignores_the_budget_but_not_stop_at(conn):
    t = time.time()
    quota.record(conn, "fake", {"five_hour": {"used": 0.95, "resets_at": t + 3600},
                                "seven_day": {"used": 0.5, "resets_at": t + 86400 * 6}})
    assert capacity.status(conn)["fake"][0]
    assert not capacity.status(conn, background=True)["fake"][0]
    quota.record(conn, "fake", {"five_hour": {"used": 0.99, "resets_at": t + 3600}})
    ok, why = capacity.status(conn)["fake"]
    assert not ok and "used up" in why
