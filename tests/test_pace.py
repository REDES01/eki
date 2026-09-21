# SPDX-License-Identifier: Apache-2.0
"""A window spent ahead of pace makes its provider dearer; behind, cheaper."""
from eki.quota import QuotaBoard
from eki.quota.base import Reading, Window
from eki.quota.pace import CEIL, FLOOR, provider_pace, window_pace
from eki.router import Need, Router
from tests.test_routing_scenarios import Mac, Spent

NOW = 1_000_000.0
H = 3600


def five_hour(used, hours_left):
    return Window("five_hour", "5H", used, resets_at=int(NOW + hours_left * H), window_seconds=5 * H)


def test_ahead_of_pace_is_dear_and_behind_is_cheap():
    # 90% used with four of five hours to go: burning far too fast
    p = window_pace(five_hour(0.9, 4), NOW)
    assert p.elapsed == 0.2 and p.ahead == 0.7 and p.factor == 3.1
    assert p.why == "5H ahead of pace ×3.1"
    # 90% used with ten minutes to go: about to refill, spend it
    p = window_pace(five_hour(0.9, 1 / 6), NOW)
    assert p.factor < 1.0 and "behind" in p.why
    # on pace says nothing
    assert window_pace(five_hour(0.5, 2.5), NOW).why == ""
    # a week barely touched by Thursday is cheaper than list, down to the floor
    week = Window("seven_day", "WEEK", 0.1, resets_at=int(NOW + 3 * 24 * H), window_seconds=7 * 24 * H)
    assert window_pace(week, NOW).factor == FLOOR
    # and nothing goes above the ceiling
    assert window_pace(five_hour(1.0, 5), NOW).factor == CEIL


def test_a_window_without_a_reset_time_only_speaks_when_nearly_gone():
    assert window_pace(Window("five_hour", "5H", 0.5), NOW).factor == 1.0
    assert window_pace(Window("five_hour", "5H", 0.9), NOW).factor == 1.6
    # a credits pool is paced against its month
    credits = Window("credits", "CREDITS", 0.94, resets_at=int(NOW + 10 * 24 * H), kind="credits")
    p = window_pace(credits, NOW)
    assert p.elapsed is not None and 0.6 < p.elapsed < 0.7 and p.factor > 1.5


def test_the_tightest_account_window_sets_the_pace_and_a_models_window_its_own():
    reading = Reading("claude", [
        five_hour(0.2, 4),                                            # behind
        Window("seven_day", "WEEK", 0.8, resets_at=int(NOW + 5 * 24 * H),
               window_seconds=7 * 24 * H),                             # ahead
        Window("seven_day_fable", "FABLE WEEK", 0.95, resets_at=int(NOW + 5 * 24 * H),
               window_seconds=7 * 24 * H, primary=False),
    ])
    p = provider_pace(reading, NOW)
    assert p.pace.window == "WEEK" and p.factor > 1
    # a credits pool nearly spent doesn't price the provider: it's only
    # touched once the windows are gone
    reading.windows.append(Window("credits", "CREDITS", 0.99, kind="credits"))
    assert provider_pace(reading, NOW).pace.window == "WEEK"
    assert p.model_factor("fable") > p.model_factor("opus") == 1.0
    assert p.model_factor("Claude Fable 5.1") == p.model_factor("fable")


def test_the_board_reports_a_pace_per_provider():
    import time
    board = QuotaBoard([])
    board.latest["claude"] = Reading("claude", [
        Window("five_hour", "5H", 0.9, resets_at=int(time.time() + 4 * H), window_seconds=5 * H)])
    assert board.pace()["claude"].factor == 3.1
    assert board.exhausted() == {}                     # ahead of pace is not spent


# ---- what it does to routing ----------------------------------------------

class Paced(Spent):
    def __init__(self, paces, **spent):
        super().__init__(**spent)
        self.paces = paces

    def pace(self):
        return self.paces


def _router(mac, paces):
    return Router([mac.local, mac.small, mac.claude, mac.codex, mac.flux], quota=Paced(paces),
                  policy=mac.policy, is_up=lambda k: mac.up.get(k), reserved={"qwen3-5-2b"},
                  models_for=lambda k: mac.records.get(k, []))


def test_claude_burning_fast_sends_hard_repo_work_to_codex_and_says_why():
    mac = Mac()
    claude = provider_pace(Reading("claude", [five_hour(0.9, 4)]), NOW)
    codex = provider_pace(Reading("codex", [five_hour(0.1, 1)]), NOW)
    need = Need(repo=True, task="repo", difficulty="hard")
    # on pace, the policy order picks claude
    assert _router(mac, {}).choose(need).backend.key == "claude"
    c = _router(mac, {"claude": claude, "codex": codex}).choose(need)
    assert c.backend.key == "codex"
    assert "codex" in c.reason and "5H behind pace" in c.reason
    assert "claude 5H ahead of pace ×3.1" in c.reason


def test_pacing_never_moves_work_off_a_free_local_model():
    mac = Mac()
    codex = provider_pace(Reading("codex", [five_hour(0.0, 1)]), NOW)   # as cheap as it gets
    c = _router(mac, {"codex": codex}).choose(Need(task="chat", difficulty="easy"))
    assert c.backend.key == "qwen"


def test_fables_own_window_prices_fable_not_opus():
    mac = Mac()
    mac.records["claude"][2].public["scores"]["math/hard"] = 0.85     # opus below the bar → fable
    need = Need(task="math", difficulty="hard")
    assert _router(mac, {}).choose(need).model == "fable"
    # with Fable's week nearly gone early on, fable is dear enough that Codex
    # (which clears hard maths on its class alone) takes the work instead
    claude = provider_pace(Reading("claude", [
        five_hour(0.3, 2.5),
        Window("seven_day_fable", "FABLE WEEK", 0.95, resets_at=int(NOW + 6 * 24 * H),
               window_seconds=7 * 24 * H, primary=False)]), NOW)
    c = _router(mac, {"claude": claude}).choose(need)
    assert c.backend.key == "codex"
    # …while opus, on the account-wide window that's behind pace, is untouched:
    # once it clears the bar it's picked even when dearer than fable on paper
    mac.records["claude"][2].public["scores"]["math/hard"] = 0.98
    mac.records["claude"][2].cost_weight = 11.0
    assert _router(mac, {}).choose(need).model == "fable"
    c = _router(mac, {"claude": claude}).choose(need)
    assert c.backend.key == "claude" and c.model == "opus"
    assert "5H behind pace" in c.reason
