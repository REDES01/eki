# SPDX-License-Identifier: Apache-2.0
"""Reading Claude Code's /usage panel, as it actually renders."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from eki.quota import claude as claude_quota
from eki.quota import claude_usage
from eki.quota.base import Reading, Window

TOKYO = ZoneInfo("Asia/Tokyo")
# 2026-09-21 10:30 in Tokyo
NOW = datetime(2026, 9, 21, 10, 30, tzinfo=TOKYO).timestamp()

# the panel exactly as a terminal showed it (bars and all)
PANEL = """
   Settings  Status   Config   Usage   Stats
   Session
   Total cost:            $0.0000
   Usage:                 0 input, 0 output, 0 cache read, 0 cache write
   Current session
   ██                                                 4% used
   Resets 4:10pm (Asia/Tokyo)
   Current week (all models)
   ███████                                            14% used
   Resets Sep 21 at 11pm (Asia/Tokyo)
   Current week (Fable)
   ████████▌                                          17% used
   Resets Sep 21 at 11pm (Asia/Tokyo)
   Usage credits
   ███████████████████████████████████████████████▍   94% used
   $113.74 / $120.00 spent · Resets Oct 1 (Asia/Tokyo)
   Esc to cancel
""".splitlines()


def test_every_block_is_read():
    blocks = {b.key: b for b in claude_usage.parse(PANEL, NOW)}
    assert set(blocks) == {"five_hour", "seven_day", "seven_day_fable", "credits"}
    assert blocks["five_hour"].used == 4
    assert blocks["seven_day_fable"].used == 17
    assert blocks["credits"].used == 94
    assert blocks["credits"].detail == "$113.74 / $120.00"


def test_reset_times_land_in_the_zone_claude_code_named():
    blocks = {b.key: b for b in claude_usage.parse(PANEL, NOW)}
    at = lambda k: datetime.fromtimestamp(blocks[k].resets_at, TOKYO)
    assert (at("five_hour").day, at("five_hour").hour, at("five_hour").minute) == (21, 16, 10)
    assert (at("seven_day").month, at("seven_day").day, at("seven_day").hour) == (9, 21, 23)
    assert (at("credits").month, at("credits").day) == (10, 1)


def test_a_time_already_past_today_means_tomorrow():
    late = datetime(2026, 9, 21, 17, 0, tzinfo=TOKYO).timestamp()
    stamp = claude_usage.when("4:10pm", "Asia/Tokyo", late)
    assert datetime.fromtimestamp(stamp, TOKYO).day == 22


def test_a_new_per_model_block_needs_no_code():
    blocks = claude_usage.parse(["Current week (Opus only)", "█ 9% used",
                                 "Resets Sep 25 at 3pm (Asia/Tokyo)"], NOW)
    assert blocks[0].key == "seven_day_opus"


def test_the_panel_becomes_meters_with_money_kept_out_of_them():
    payload = {"observed_at": int(NOW),
               "rate_limits": claude_usage.to_rate_limits(claude_usage.parse(PANEL, NOW))}
    reading = claude_quota.parse(payload, int(NOW))
    by = {w.key: w for w in reading.windows}
    assert by["five_hour"].label == "5H" and by["five_hour"].primary
    assert by["seven_day_fable"].label == "FABLE WEEK" and not by["seven_day_fable"].primary
    assert by["credits"].kind == "credits" and by["credits"].detail == "$113.74 / $120.00"
    assert not by["credits"].primary


def test_a_fresher_status_line_updates_only_what_it_knows():
    usage = Reading("claude", [Window("five_hour", "5H", 0.04), Window("seven_day", "WEEK", 0.14),
                               Window("seven_day_fable", "FABLE WEEK", 0.17, primary=False)],
                    observed_at=100)
    status = Reading("claude", [Window("five_hour", "5H", 0.20), Window("seven_day", "WEEK", 0.16)],
                     observed_at=200)
    merged = claude_quota.merge(usage, status)
    by = {w.key: w.used for w in merged.windows}
    assert by == {"five_hour": 0.20, "seven_day": 0.16, "seven_day_fable": 0.17}
    assert merged.observed_at == 200
