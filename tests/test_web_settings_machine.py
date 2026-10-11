"""Settings → Machine: the two machine caps in routing.json, and the live line at /api/machine."""
import json

from eki import machinecaps
from test_web import get, post, web  # noqa: F401  (the fixture)
from test_web_settings import _read

AGENTS_WHY = ("how many agents may work at the same time across all projects; each mostly waits on the model, "
              "so this mostly costs subscription quota, not CPU")
CHECKS_WHY = ("how many test runs — eki's checks and the agents' own tests — may run at the same time; each takes "
              "several cores, so keep this low enough that load stays near the core count")


def test_the_page_loads_the_machine_section(web):
    code, js = get(f"{web}/ui/settingsmachine.js")
    assert code == 200
    _, page = get(web + "/")
    assert page.index("/ui/settingsmachine.js") < page.index("/ui/settings.js")
    _, frame = get(f"{web}/ui/settings.js")
    assert '"machine"' in frame.split("const SECTIONS = ")[1].split("\n")[0]


def test_the_section_explains_both_caps_and_reads_the_live_line(web):
    _, js = get(f"{web}/ui/settingsmachine.js")
    for want in (AGENTS_WHY, CHECKS_WHY, 'settings.section("machine"', 'title: "Machine"', 'file: "routing"',
                 "Agents at once", "Test runs at once", 'data-f="${f}"', '"agents"', '"checks"', 'min="1"',
                 "settingsForm.fold", "/api/machine", "delete d.machine", "at once"):
        assert want in js, want


def test_a_machine_block_is_written_and_read_back(web, home):
    got = _read(web, "routing")
    code, out = post(web + "/api/settings/routing",
                     {"data": {**got["data"], "machine": {"agents": 16, "checks": 4}}, "mtime": got["mtime"]})
    assert code == 200 and out["ok"] is True
    assert json.loads((home / "routing.json").read_text())["machine"] == {"agents": 16, "checks": 4}
    assert machinecaps.settings() == {"agents": 16, "checks": 4}
    live = json.loads(get(web + "/api/machine")[1])
    assert live["agents"] == 16 and live["checks"] == 4


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
    assert {"agents", "checks", "load", "cores", "memory"} <= set(got["now"])
    assert got["line"].startswith("now: ")


def test_eki_self_no_longer_edits_parallel(web):
    _, js = get(f"{web}/ui/settingsself.js")
    assert 'data-f="parallel"' not in js and "autonomy" in js


def test_the_files_stay_small():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "eki"
    for p in (root / "web" / "settingsmachine.js", root / "machinenow.py"):
        assert len(p.read_text().splitlines()) < 400, p
