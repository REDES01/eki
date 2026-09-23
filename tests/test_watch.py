# SPDX-License-Identifier: Apache-2.0
"""Which models are out there, read from the vendors and from Ollama — and
routing that follows the vendor's own ladder."""
import asyncio
import json

import httpx
import pytest

from eki import watch
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from eki.router import Need, Router

SEARCH = """
<ul>
<li  class="flex items-baseline border-b border-neutral-200 py-6">
  <a href="/library/qwen4.0" class="group w-full">
    <div class="flex flex-col mb-1" title="qwen4.0"><h2><span >qwen4.0</span></h2>
      <p class="max-w-lg break-words text-neutral-800 text-md">The next Qwen.</p></div>
    <div class="flex flex-col"><div class="flex flex-wrap space-x-2">
      <span  class="inline-flex bg-indigo-50">vision</span>
      <span  class="inline-flex bg-indigo-50">tools</span>
      <span  class="inline-flex bg-[#ddf4ff]">27b</span>
      <span  class="inline-flex bg-[#ddf4ff]">35b</span></div>
      <p class="my-1 flex"><span class="flex items-center"><svg></svg><span >1.2M</span>
        <span class="hidden sm:flex">&nbsp;Pulls</span></span>
        <span class="flex items-center" title="Sep 20, 2026 5:21 AM UTC"><span >3 days ago</span></span></p>
    </div>
  </a>
</li>
<li  class="flex">
  <a href="/library/kimi-k3" class="group w-full">
    <div><h2><span >kimi-k3</span></h2><p class="x">Big and hosted.</p></div>
    <div><span  class="inline-flex">tools</span><span  class="inline-flex">cloud</span>
    <p><span class="flex"><span >90K</span><span class="hidden sm:flex">&nbsp;Pulls</span></span></p></div>
  </a>
</li>
</ul>"""

LIBRARY = """<html><body><img src="/assets/library/qwen4.0/logo" width="360"/>
<div>Models View all Name 12 models Size / Usage Context Input
qwen4.0:latest 23GB · 256K context window · Text, Image · 3 days ago
qwen4.0:27b 18GB · 256K context window · Text, Image · 3 days ago
qwen4.0:27b-mlx MLX 19GB · 256K context window · Text, Image · 3 days ago
qwen4.0:122b 76GB · 256K context window · Text · 3 days ago
Readme Qwen4.0 is here.</div>
<img src="/assets/library/qwen4.0/abc" alt="qwen4.0_27b_score.png"/></body></html>"""


# ---- reading pages ---------------------------------------------------------------------

def test_ollamas_list_is_read_in_its_order():
    got = watch.parse_search(SEARCH)
    assert [m["name"] for m in got] == ["qwen4.0", "kimi-k3"]
    q = got[0]
    assert q["sizes"] == ["27b", "35b"] and q["pulls"] == 1_200_000 and not q["cloud_only"]
    assert q["updated"].startswith("Sep 20, 2026") and q["about"] == "The next Qwen."
    assert got[1]["cloud_only"] and got[1]["pulls"] == 90_000


def test_a_models_page_gives_its_builds_and_its_charts():
    page = watch.parse_library(LIBRARY, "qwen4.0")
    tags = {t["tag"]: t for t in page["tags"]}
    assert tags["27b"]["gb"] == 18 and tags["27b-mlx"]["mlx"] and tags["122b"]["gb"] == 76
    pics = watch.charts(page["images"])
    assert pics[0]["alt"] == "qwen4.0_27b_score.png" and all("logo" not in p["url"] for p in pics)
    assert page["readme"].startswith("Qwen4.0 is here")


def test_the_build_that_fits_near_what_you_run_mlx_first():
    page = watch.parse_library(LIBRARY, "qwen4.0")
    t = watch.pick_tag(page, ceiling_gb=37.4, current_params=27)
    assert t["tag"] == "27b-mlx"
    assert watch.pick_tag(page, ceiling_gb=10, current_params=27) is None


def test_names_versions_and_sizes():
    assert watch.family("qwen3.8") == ("qwen", "3.8")
    assert watch.family("mlx-community/Qwen3.8-27B-4bit") == ("qwen", "3.8")
    assert watch.newer("4.0", "3.8") and not watch.newer("3.8", "3.10")
    assert watch.params_of("mlx-community/Qwen3.8-27B-4bit") == 27


def test_the_ladder_keeps_only_what_the_program_can_run():
    runnable = [{"id": "fable"}, {"id": "opus"}, {"id": "sonnet"}]
    answer = {"ladder": {"default": "opus", "top": "fable", "fast": "haiku"}}
    assert watch.clean_ladder(answer, runnable) == {"default": "opus", "top": "fable"}
    assert watch.clean_ladder({"ladder": {"top": "gpt-6-astra"}}, [{"id": "gpt-5.5"}]) == {"default": ""}


def test_what_the_program_says_it_runs_decides_the_mapping():
    runnable = [{"id": "fable", "resolved": "claude-fable-5-1"}, {"id": "opus", "resolved": "claude-opus-5"},
                {"id": "sonnet", "resolved": "claude-sonnet-5"}, {"id": "haiku", "resolved": "claude-haiku-4-5-20251001"}]
    answer = {"ladder": {"default": None, "top": None, "fast": None},      # a reader that gave up
              "vendor_models": [{"id": "claude-opus-5-5", "role": "default"},
                                {"id": "claude-fable-5-1", "role": "top"},
                                {"id": "claude-haiku-4-5-20251001", "role": "fast"}]}
    assert watch.clean_ladder(answer, runnable) == {"default": "opus", "top": "fable", "fast": "haiku"}


def test_the_readers_answer_is_found():
    assert watch.parse_json('Sure:\n```json\n{"ladder": {"default": "opus"}}\n```') == {"ladder": {"default": "opus"}}


# ---- comparing -------------------------------------------------------------------------

CHART = {"is_chart": True, "subject": "Qwen4.0-27B", "benchmarks": [
    {"name": "SWE-bench", "scores": {"Qwen4.0-27B": 71.0, "Qwen3.8-27B": 64.2, "Other-30B": 60}},
    {"name": "GPQA", "scores": {"Qwen4.0-27B": 80.1, "Qwen3.8-27B": 78.0}},
    {"name": "AIME", "scores": {"Qwen4.0-27B": 88.0, "Qwen3.8-27B": 90.5}},
    {"name": "LiveCodeBench", "scores": {"Qwen4.0-27B": 70.0, "Qwen3.8-27B": 61.0}},
    {"name": "Only-new", "scores": {"Qwen4.0-27B": 50}}]}


def test_a_candidate_is_compared_on_the_benchmarks_both_appear_in():
    c = watch.compare(CHART, "Qwen4.0-27B", "mlx-community/Qwen3.8-27B-4bit")
    assert c["shared"] == 4 and c["wins"] == 3
    v, why = watch.verdict({"name": "qwen4.0"}, "Qwen3.8-27B", c)
    assert v == "suggest" and "3 of 4" in why


def test_losing_is_skipped_and_no_chart_means_a_test_only_for_a_newer_version():
    lose = {"shared": 4, "wins": 1, "rows": []}
    assert watch.verdict({"name": "qwen4.0"}, "Qwen3.8-27B", lose)[0] == "skip"
    v, why = watch.verdict({"name": "qwen4.0"}, "mlx-community/Qwen3.8-27B-4bit", None)
    assert v == "test" and "test it after download" in why
    assert watch.verdict({"name": "granite4.1"}, "mlx-community/Qwen3.8-27B-4bit", None)[0] == "skip"
    v, why = watch.verdict({"name": "qwen3.6"}, "mlx-community/Qwen3.8-27B-4bit", None)
    assert v == "skip" and "older than the qwen 3.8" in why


def test_the_mlx_build_is_found_four_bit_first():
    rows = [{"id": "mlx-community/Qwen4.0-27B-8bit"}, {"id": "mlx-community/Qwen4.0-27B-4bit"},
            {"id": "mlx-community/Qwen4.0-122B-4bit"}]
    assert watch.match_build("qwen4.0", "27b-mlx", rows) == "mlx-community/Qwen4.0-27B-4bit"
    assert watch.match_build("qwen4.0", "9b", rows) is None


def test_whats_new_is_said_in_words():
    before = {"vendors": {"claude_code": {"vendor": "Anthropic", "ladder": {"default": "sonnet", "top": "fable"}}}}
    after = {"vendors": {"claude_code": {"vendor": "Anthropic", "ladder": {"default": "opus", "top": "fable"},
                                         "not_offered": ["claude-opus-6"]}},
             "suggestions": [{"name": "qwen4.0", "why": "beats Qwen3.8-27B on 3 of 4"}]}
    said = watch.news(before, after)
    assert "Anthropic: default is now opus (was sonnet)" in said
    assert any("claude-opus-6" in s for s in said) and any("qwen4.0" in s for s in said)
    assert watch.news({}, after)[:1] != ["Anthropic: default is now opus"]   # first reading: no "changed"


def test_the_ladder_is_read_from_the_state_file():
    assert watch.ladder("claude_code") == {}
    watch.save({"vendors": {"claude_code": {"vendor": "Anthropic",
                                            "ladder": {"default": "opus", "top": "fable", "fast": "sonnet"}}}})
    assert watch.ladder("claude_code") == {"default": "opus", "top": "fable", "fast": "sonnet",
                                           "vendor": "Anthropic"}


# ---- routing by the ladder ----------------------------------------------------------------

class B(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        yield ""


def backend(key, kind, tier, **caps):
    return B(BackendInfo(key=key, kind=kind, label=key,
                         capabilities=Capabilities(**{"tools": True, "repo": True, **caps}),
                         cost=Cost(tier=tier)), {})


class Rec:
    def __init__(self, model, q):
        self.model, self.q, self.label, self.cost = model, q, model, 0.2

    def quality(self, task, difficulty=""):
        return self.q


LADDER = {"default": "opus", "top": "fable", "fast": "sonnet", "vendor": "Anthropic"}


def router(ladder=LADDER, local_q=0.8, pace=None):
    claude = backend("claude_code", "claude_code", 50)
    local = backend("codex-qwen", "codex", 0)

    class Q:
        def exhausted(self):
            return {}

        def pace(self):
            return {"claude": pace} if pace else {}
    claude.info.quota_source = "claude"
    return Router([claude, local], quota=Q(),
                  models_for=lambda k: [Rec("", local_q)] if k == "codex-qwen" else [],
                  ladder_for=lambda k: ladder if k == "claude_code" else {})


def test_work_goes_to_the_vendors_default_hard_work_too():
    r = router(local_q=0.5)
    c = r.choose(Need(tools=True, task="code", difficulty="medium"))
    assert (c.backend.key, c.model) == ("claude_code", "opus") and "Anthropic's default" in c.reason
    c = r.choose(Need(tools=True, task="code", difficulty="hard"))
    assert c.model == "opus"                        # Fable only when Opus fell short


def test_easy_work_a_good_enough_local_model_still_takes_it():
    c = router(local_q=0.8).choose(Need(tools=True, task="chat", difficulty="easy"))
    assert c.backend.key == "codex-qwen"
    c = router(local_q=0.3).choose(Need(tools=True, task="chat", difficulty="easy"))
    assert (c.backend.key, c.model) == ("claude_code", "sonnet")


def test_after_a_correction_the_top_model_and_not_the_local_one():
    c = router(local_q=0.99).choose(Need(tools=True, task="chat", difficulty="easy", escalate=True))
    assert (c.backend.key, c.model) == ("claude_code", "fable") and "corrected" in c.reason
    assert any("stronger model" in x for x in c.rejected)


def test_a_top_model_whose_window_is_running_out_rests():
    class Pace:
        factor = 1.0
        pace = type("P", (), {"why": ""})()

        def model_factor(self, m):
            return 2.0 if m == "fable" else 1.0
    c = router(local_q=0.5, pace=Pace()).choose(Need(tools=True, task="code", difficulty="hard",
                                                      escalate=True))
    assert c.model == "opus" and "being spent fast" in c.reason


def test_without_a_ladder_routing_is_as_before():
    c = router(ladder={}, local_q=0.5).choose(Need(tools=True, task="code", difficulty="medium"))
    assert c.backend.key == "claude_code" and "cheapest fit" in c.reason


# ---- the daily reading, in the engine -------------------------------------------------------

@pytest.mark.asyncio
async def test_a_reading_writes_ladders_and_suggestions(tmp_path, monkeypatch):
    from eki.engine import Engine
    from tests.test_runs import echo_config
    cfg = echo_config(tmp_path)
    cfg.backends.append(BackendInfo(key="claude_code", kind="claude_code", label="Claude Code",
                                    capabilities=Capabilities(tools=True, repo=True, vision=True),
                                    cost=Cost(tier=50)))
    cfg.options["claude_code"] = {"binary": "/bin/echo"}
    cfg.backends.append(BackendInfo(key="qwen", kind="mlx", label="Qwen", capabilities=Capabilities(),
                                    cost=Cost(tier=0)))
    cfg.options["qwen"] = {"model": "mlx-community/Qwen3.8-27B-4bit"}
    eng = Engine(cfg, owner=True)
    eng.settings = {**eng.settings, "notify_learned": False}
    for m in ("fable", "opus", "sonnet"):
        eng.registry.seen("claude_code", m, label=m.title())

    async def no_discovery():
        return {}
    monkeypatch.setattr(eng, "discover_models", no_discovery)
    monkeypatch.setattr(eng.models, "memory", lambda: type("M", (), {"ceiling_gb": 37.4})())

    def pages(request):
        u = str(request.url)
        if "claude.com" in u:
            return httpx.Response(200, text="<p>Start with Claude Opus 5.5 for most workloads.</p>")
        if u.endswith("/search"):
            return httpx.Response(200, text=SEARCH)
        if "/library/qwen4.0" in u:
            return httpx.Response(200, text=LIBRARY)
        if "/assets/" in u:
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        if "huggingface" in u:
            return httpx.Response(200, json=[{"id": "mlx-community/Qwen4.0-27B-4bit",
                                              "pipeline_tag": "text-generation"}])
        return httpx.Response(404)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(pages), **{k: v for k, v in kw.items()}))
    asked = []

    async def read(reader, prompt):
        asked.append(prompt)
        if "model overview page" in prompt:
            return {"ladder": {"default": "opus", "top": "fable", "fast": "sonnet"},
                    "guidance": "Start with Claude Opus 5.5 for most workloads.",
                    "not_offered": ["claude-haiku-4-5"]}
        return CHART
    monkeypatch.setattr(eng, "_read", read)
    state = await eng.watch_refresh(force=True)
    assert state["vendors"]["claude_code"]["ladder"] == {"default": "opus", "top": "fable", "fast": "sonnet"}
    assert "Opus 5.5" in state["vendors"]["claude_code"]["guidance"]
    s = state["suggestions"][0]
    assert s["name"] == "qwen4.0" and s["verdict"] == "suggest" and s["build"] == "mlx-community/Qwen4.0-27B-4bit"
    assert "kimi-k3" not in state["candidates"]                     # cloud only
    assert any("qwen4.0" in p for p in asked)
    assert eng.router.ladder_for("claude_code")["default"] == "opus"
    # unchanged since: not read again
    asked.clear()
    await eng.watch_refresh(force=True)
    assert not any("qwen4.0" in p and "benchmark" in p for p in asked)
    await eng.runner.stop()


# ---- is the program here up to date? ---------------------------------------------------

def test_an_older_program_runs_an_older_model_under_the_same_alias():
    vendor = [{"id": "claude-opus-5-5", "role": "default"}, {"id": "claude-fable-5-1", "role": "top"},
              {"id": "claude-haiku-4-5-20251001", "role": "fast"}]
    runs = {"opus": "claude-opus-5", "fable": "claude-fable-5-1", "haiku": "claude-haiku-4-5-20251001"}
    assert watch.stale(vendor, runs) == [{"role": "default", "vendor": "claude-opus-5-5",
                                          "alias": "opus", "runs": "claude-opus-5"}]
    assert watch.split_id("claude-opus-5-5") == ("claude-opus", "5.5")
    assert watch.split_id("claude-haiku-4-5-20251001") == ("claude-haiku", "4.5")


def test_versions_and_how_to_update():
    assert watch.behind("2.1.278", "2.1.280") and not watch.behind("2.1.280", "2.1.280")
    assert not watch.behind("", "2.1.280")
    assert watch.version_of("2.1.278 (Claude Code)") == "2.1.278"
    assert watch.update_command("claude_code", "/x/claude") == ["/x/claude", "update"]
    assert watch.update_command("codex", "")[:2] == ["npm", "install"]


def test_codex_is_updated_the_way_the_copy_eki_runs_was_installed(tmp_path):
    # the standalone binary is replaced from its release — npm would update another copy
    # (seen live: npm went to Homebrew's prefix, eki kept running 0.154)
    alone = tmp_path / "bin" / "codex"
    alone.parent.mkdir()
    alone.write_text("")
    assert watch.update_command("codex", str(alone))[1:] == ["-m", "eki.codex_host", "update",
                                                             str(alone.resolve())]
    js = tmp_path / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    js.parent.mkdir(parents=True)
    js.write_text("")
    link = tmp_path / "bin" / "codex-npm"
    link.symlink_to(js)
    assert watch.update_command("codex", str(link))[:2] == ["npm", "install"]


def test_news_says_the_program_is_behind():
    after = {"vendors": {"claude_code": {"vendor": "Anthropic", "ladder": {"default": "opus"},
                                         "program": {"installed": "2.1.278", "latest": "2.1.280",
                                                     "behind": True, "update": ["claude", "update"],
                                                     "stale": [{"role": "default", "vendor": "claude-opus-5-5",
                                                                "alias": "opus", "runs": "claude-opus-5"}]}}}}
    said = watch.news({}, after)
    assert any("runs claude-opus-5 as 'opus'" in x and "claude update" in x for x in said)
