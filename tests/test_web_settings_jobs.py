"""Settings → Jobs: one grouped line per role (name, "now …", picker) and the checker switch."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eki import roles
from test_web import get, web  # noqa: F401  (the fixture)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def test_the_jobs_section_is_grouped_lines(web):
    code, js = get(f"{web}/ui/settingsmodels.js")
    assert code == 200 and 'settings.section("jobs"' in js
    assert "settingsForm.line(" in js and "settingsForm.group(" in js and "ROLES.map(" in js
    assert '"now "' in js and '"nobody"' in js and "set-amber" in js and "checker_wakes" in js
    assert 'style="color: var(--amber)"' not in js
    assert len((WEB / "settingsmodels.js").read_text().splitlines()) < 400


STUB = """
global.window = global; global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
require(process.argv[2]); require(process.argv[3]); require(process.argv[4]);
const got = JSON.parse(process.argv[5]);
global.fetch = async () => ({ ok: true, json: async () => got });
settingsModels.load().then(() => console.log(JSON.stringify(settingsModels.roles({ roles: { chores: "off" } }))));
"""


def test_every_role_is_a_line_with_who_has_it_now(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    who = {"router": ("claude", "claude · Opus 5.5 · high", "router local can't run (local: down) → claude"),
           "planner": ("claude", "claude · Opus 5.5 · high", ""),
           "reviewer": ("codex", "codex · its default", ""),
           "chores": (None, "", "off"),
           "judge": ("claude", "claude · Opus 5.5 · high", "")}
    got = {"providers": {"claude": {"kind": "claude_code", "lists": False, "listed": None, "read_at": None},
                         "codex": {"kind": "codex", "lists": False, "listed": None, "read_at": None}},
           "roles": {r: {"target": p, "provider": p, "model": None, "effort": None, "shown": s,
                         "default": "claude", "why": why, "set": r == "chores"} for r, (p, s, why) in who.items()}}
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "settingsform.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "settingsmodels.js"), json.dumps(got)],
                         capture_output=True, text=True, timeout=30, check=True)
    html = json.loads(out.stdout)
    assert html.count('class="set-line"') == len(roles.ROLES) and 'class="set-group"' in html
    at = [html.index(f'data-role="{r}"') for r in roles.ROLES]
    assert at == sorted(at)                                         # in ROLES order, all shown
    assert "now claude · Opus 5.5 · high" in html and "now codex · its default" in html and "nobody" in html
    assert '<div class="set-amber">router local can' in html
    assert html.count("set-amber") == 1                             # "off" is not a fallback
    assert ">default (claude)<" in html and 'value="off" selected' in html
