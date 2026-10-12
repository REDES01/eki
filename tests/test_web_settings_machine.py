"""Settings → Machine: the three machine caps in routing.json, and the live line at /api/machine."""
import json

from eki import db, machinecaps, memadmit
from test_web import get, post, web  # noqa: F401  (the fixture)
from test_web_settings import _read

AGENTS_WHY = ("how many agents may work at the same time across all projects; each mostly waits on the model, "
              "so this mostly costs subscription quota, not CPU")
CHECKS_WHY = ("how many test runs — eki's checks and the agents' own tests — may run at the same time; each takes "
              "several cores, so keep this low enough that load stays near the core count")
BROWSERS_WHY = ("how many browsers eki's runs may have open at once (Playwright, in an agent's script or the "
                "playtester's MCP); each renders the game, so a few already load the machine; a browser past the "
                "cap waits for one to close")

FLOOR_WHY = "new agents, test runs and browsers wait while free memory is below this; running work is never stopped"


def test_the_page_loads_the_machine_section(web):
    code, js = get(f"{web}/ui/settingsmachine.js")
    assert code == 200
    _, page = get(web + "/")
    assert page.index("/ui/settingsmachine.js") < page.index("/ui/settings.js")
    _, frame = get(f"{web}/ui/settings.js")
    assert '"machine"' in frame.split("const SECTIONS = ")[1].split("\n")[0]


def test_the_section_explains_the_three_caps_and_reads_the_live_line(web):
    _, js = get(f"{web}/ui/settingsmachine.js")
    for want in (AGENTS_WHY, CHECKS_WHY, BROWSERS_WHY, "Browsers at once", '"browsers"', "browsers: 16",
                 "BROWSERS = 3", '"browser")} at once', 'settings.section("machine"', 'title: "Machine"', 'file: "routing"',
                 "Agents at once", "Test runs at once", 'data-f="${f}"', '"agents"', '"checks"', 'min="1"',
                 "settingsForm.fold", "/api/machine", "delete d.machine", "at once", "Memory floor (GB)", "Swap at most (%)",
                 FLOOR_WHY, "floor_gb: 64", "swap_max: 100", "FLOOR_GB = 6", "SWAP_MAX = 60", "data-machine-mem",
                 "mem.estimates", "(default)", '"run"', "mem.waiting",
                 "the counts above stay as upper bounds"):
        assert want in js, want


def test_a_machine_block_is_written_and_read_back(web, home):
    got = _read(web, "routing")
    code, out = post(web + "/api/settings/routing",
                     {"data": {**got["data"], "machine": {"agents": 16, "checks": 4}}, "mtime": got["mtime"]})
    assert code == 200 and out["ok"] is True
    assert json.loads((home / "routing.json").read_text())["machine"] == {"agents": 16, "checks": 4}
    assert machinecaps.settings() == {"agents": 16, "checks": 4, "browsers": 3, "floor_gb": 6, "swap_max": 60}
    live = json.loads(get(web + "/api/machine")[1])
    assert live["agents"] == 16 and live["checks"] == 4


def test_browsers_at_once_is_written_and_read_back(web, home):
    got = _read(web, "routing")
    code, out = post(web + "/api/settings/routing",
                     {"data": {**got["data"], "machine": {"browsers": 5, "floor_gb": 6, "swap_max": 60}}, "mtime": got["mtime"]})
    assert code == 200 and out["ok"] is True
    assert json.loads((home / "routing.json").read_text())["machine"] == {"browsers": 5, "floor_gb": 6, "swap_max": 60}
    assert _read(web, "routing")["data"]["machine"] == {"browsers": 5, "floor_gb": 6, "swap_max": 60}
    assert machinecaps.settings()["browsers"] == 5
    live = json.loads(get(web + "/api/machine")[1])
    assert live["browsers"] == 5 and live["now"]["browsers"] == 0 and "0 browsers" in live["line"]


def test_browsers_out_of_range_is_refused(web, home):
    got = _read(web, "routing")
    code, out = post(web + "/api/settings/routing",
                     {"data": {**got["data"], "machine": {"browsers": 17, "floor_gb": 6, "swap_max": 60}}, "mtime": got["mtime"]})
    assert code == 400 and "machine.browsers" in out["problems"][0]


def test_a_cap_out_of_range_is_refused(web, home):
    got = _read(web, "routing")
    code, out = post(web + "/api/settings/routing",
                     {"data": {**got["data"], "machine": {"agents": 65}}, "mtime": got["mtime"]})
    assert code == 400 and "machine.agents" in out["problems"][0]


def test_api_machine_gives_the_caps_what_runs_now_and_the_line(web):
    code, text = get(web + "/api/machine")
    assert code == 200
    got = json.loads(text)
    assert {"agents", "checks", "now", "line"} <= set(got)
    assert {"agents", "checks", "browsers", "load", "cores", "memory"} <= set(got["now"])
    assert got["line"].startswith("now: ")


def test_eki_self_no_longer_edits_parallel(web):
    _, js = get(f"{web}/ui/settingsself.js")
    assert 'data-f="parallel"' not in js and "autonomy" in js


def test_the_files_stay_small():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "eki"
    for p in (root / "web" / "settingsmachine.js", root / "machinenow.py"):
        assert len(p.read_text().splitlines()) < 400, p


def test_api_machine_says_the_memory_floor_and_the_estimates(web, monkeypatch):
    conn = db.connect()
    with conn:
        memadmit.learn(conn, "browser", 1.1)
    conn.close()
    got = json.loads(get(web + "/api/machine")[1])["memory"]
    assert {k: got[k] for k in ("free_gb", "swap_pct", "floor_gb", "swap_max", "waiting")} == \
        {"free_gb": 64.0, "swap_pct": 0, "floor_gb": 6, "swap_max": 60, "waiting": None}
    est = {e["kind"]: e for e in got["estimates"]}
    assert est["browser"] == {"kind": "browser", "gb": 1.1, "samples": 1, "learned": True}
    assert est["agent"] == {"kind": "agent", "gb": 0.3, "samples": 0, "learned": False}
    monkeypatch.setenv("EKI_FREE_GB", "3")
    assert json.loads(get(web + "/api/machine")[1])["memory"]["waiting"] == \
        "waiting for memory (3.0 GB free, swap 0%)"


def test_the_station_line_says_when_work_waits_for_memory(web):
    _, js = get(f"{web}/ui/station.js")
    assert "m.memory" in js and "st-memory" in js
