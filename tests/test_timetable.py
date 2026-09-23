# SPDX-License-Identifier: Apache-2.0
"""When a goal repeats."""
import time
from datetime import datetime, timedelta

from eki import timetable as sm


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
