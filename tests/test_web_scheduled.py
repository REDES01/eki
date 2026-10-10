"""The Scheduled page: scheduleform.js builds schedule.py's cadence JSON, and scheduled.js draws
/api/schedule's prompts and standing goals, asking before Drop."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# The two scripts need a page; this stub is just enough to load them and call what they expose.
STUB = """
const el = () => ({ innerHTML: "", textContent: "", value: "", hidden: false, style: {}, classList: { toggle() {} },
  addEventListener() {}, appendChild() {}, querySelector: () => el(), insertBefore() {} });
global.window = { addEventListener() {} };
global.document = { getElementById: el, addEventListener() {}, createElement: el, body: el() };
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;") };
global.nav = { register() {} };
global.settingsTarget = { html: () => "", value: () => "" };
const posted = [];
global.fetch = async (path, o) => { if (o && o.method === "POST") posted.push(path);
  return { ok: true, json: async () => ({ schedules: [], standing: [], tz: "UTC" }) }; };
const [mode, arg, answer] = JSON.parse(process.argv[4]);
global.confirm = () => { posted.push("asked"); return answer; };
require(process.argv[2]);
require(process.argv[3]);
(async () => {
  let out;
  if (mode === "cadence") out = arg.map((f) => window.scheduleForm.cadence(f));
  else if (mode === "render") out = window.scheduled.render(arg);
  else { await window.scheduled.act(...arg); out = posted; }
  process.stdout.write(JSON.stringify(out));
})();
"""


def run(tmp_path, mode, arg, answer=True):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "scheduleform.js"), str(WEB / "scheduled.js"),
                          json.dumps([mode, arg, answer])], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_form_builds_the_documented_cadence(tmp_path):
    got = run(tmp_path, "cadence", [
        {"every": "hourly", "minute": "15", "at": "09:00", "day": "mon"},
        {"every": "daily", "at": "09:00", "minute": "0", "day": "mon"},
        {"every": "weekdays", "at": "7:30", "minute": "0", "day": "mon"},
        {"every": "weekly", "at": "18:30", "minute": "0", "day": "sun"},
    ])
    assert got == [{"every": "hourly", "minute": 15}, {"every": "daily", "at": "09:00"},
                   {"every": "weekdays", "at": "07:30"}, {"every": "weekly", "day": "sun", "at": "18:30"}]


def sched(**kw):
    s = {"id": "s1", "text": "Summarise my inbox\nand more", "project": "p1", "project_name": "tokenbar",
         "cadence": {"every": "weekdays", "at": "09:00"}, "cadence_text": "Weekdays at 09:00", "tz": "Asia/Tokyo",
         "target": None, "target_label": "", "state": "on", "next_at": time.time() + 3600,
         "last_at": time.time() - 3600, "last_thread": "t42", "why": "skipped 09:00: the last one is still running"}
    s.update(kw)
    return s


def goal(gid, project, name, **kw):
    g = {"id": gid, "project": project, "project_name": name, "text": f"goal {gid}", "state": "on",
         "why": "", "rounds": 3, "last_round_at": time.time() - 60}
    g.update(kw)
    return g


def test_the_page_renders_prompts_with_the_plain_cadence_and_the_last_chat(tmp_path):
    html = run(tmp_path, "render", {"tz": "Asia/Tokyo", "schedules": [sched(), sched(id="gone", state="dropped",
                                                                                     text="dropped one")],
                                    "standing": []})
    assert "Scheduled prompts" in html and "Weekdays at 09:00" in html
    assert 'href="#t42"' in html and "tokenbar" in html and "still running" in html
    assert 'data-verb="pause"' in html and 'data-verb="run"' in html and 'data-verb="drop"' in html
    assert "dropped one" not in html
    paused = run(tmp_path, "render", {"schedules": [sched(state="paused")], "standing": []})
    assert 'data-verb="resume"' in paused and 'data-verb="pause"' not in paused


def test_standing_goals_group_by_project_eki_first(tmp_path):
    html = run(tmp_path, "render", {"schedules": [], "standing": [
        goal("a", "p1", "tokenbar"), goal("b", "eki", "eki", state="stuck", why="tests fail"),
        goal("c", "p1", "tokenbar", state="paused"), goal("d", "eki", "eki", state="dropped")]})
    assert "Standing goals" in html and html.index(">eki<") < html.index(">tokenbar<")
    assert "goal d" not in html and "tests fail" in html and "3 rounds" in html
    assert 'data-kind="standing"' in html and 'data-verb="now"' in html and 'data-verb="resume"' in html


def test_drop_asks_first(tmp_path):
    assert run(tmp_path, "act", ["schedule", "s1", "drop"], answer=False) == ["asked"]
    assert run(tmp_path, "act", ["schedule", "s1", "drop"], answer=True) == ["asked", "/api/schedule/s1/drop"]
    assert run(tmp_path, "act", ["standing", "g1", "drop"], answer=True) == ["asked", "/api/standing/g1/drop"]
    assert run(tmp_path, "act", ["schedule", "s1", "pause"]) == ["/api/schedule/s1/pause"]   # no question


def test_the_form_posts_the_schedule_and_registers_the_page():
    form = (WEB / "scheduleform.js").read_text()
    for name in ('"/api/schedule"', '"/api/projects"', '"/api/models"', "No project", '"Auto"', 'className = "sheet"',
                 "settingsTarget.html", "showModal", "window.scheduleForm"):
        assert name in form, name
    page = (WEB / "scheduled.js").read_text()
    for name in ('nav.register("scheduled"', "New scheduled task", 'class="btn-primary"', 'h1 class="page-title"',
                 "Standing goals", "Scheduled prompts", "scheduleForm.open"):
        assert name in page, name


def test_the_files_stay_small():
    for f in ("scheduled.js", "scheduleform.js"):
        assert len((WEB / f).read_text().splitlines()) <= 400, f
