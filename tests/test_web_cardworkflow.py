"""The workflow card in the window: cardworkflow.js gives the kind a short part in the open card
(projectcardpanel.js loads it like cardknowledge.js) — one line, the same words as the server's "line"
(eki/api_workflow.py), and "Open workflow →" to the project's Workflow page; every workflow setting is
kept out of the panel's form and there's no Settings section — and the card JSON carries `locked`."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eki import api_cards, api_workflow, apiroutes, builds, components, workflow
from test_web_choicemenu import DOM

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "cardworkflow.js").read_text()
PANEL = (WEB / "projectcardpanel.js").read_text()
KEYS = ["router", "target", "plan", "parallel", "checks", "command", "batch_command", "merge", "playtester", "after_merge", "access"]


# ---- the API --------------------------------------------------------------------------------------

def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def _project(conn, pid, folder):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, pid, str(folder), "trunk", 0, str(folder)))
    conn.commit()


def _card(conn, pid, name):
    return api_cards.card_of(conn, pid, name)


def test_the_card_json_carries_locked_on_eki_and_none_elsewhere(conn, proj, tmp_path, monkeypatch):
    eki = tmp_path / "eki"
    shutil.copytree(builds.source() / "components" / "workflow", eki / "components" / "workflow")
    monkeypatch.setenv("EKI_SOURCE", str(eki))
    _project(conn, "eki", eki)
    shutil.copytree(components.templates_dir() / "workflow", proj / "components" / "workflow")
    shutil.copytree(components.templates_dir() / "knowledge", proj / "components" / "notes")
    _project(conn, "game", proj)
    mine = _card(conn, "eki", "workflow")
    assert mine["locked"] == list(workflow.LOCKED) == ["batch", "train"]
    assert mine["choices"]["router"] == ["on", "off"]
    assert _card(conn, "game", "workflow")["locked"] == []
    assert "locked" not in _card(conn, "game", "notes")                     # only a workflow card has it
    edited = call(conn, "POST", "/api/projects/eki/cards/workflow", {"settings": {"parallel": 2}})["card"]
    assert edited["spec"]["parallel"] == 2 and edited["locked"] == ["batch", "train"]


# ---- the files ------------------------------------------------------------------------------------

def test_files_are_short_and_the_panel_loads_the_part():
    assert '"cardworkflow.js"' in PANEL and "kindPart().keys" in PANEL
    assert len(PANEL.splitlines()) < 400 and len(JS.splitlines()) < 400
    assert 'href="/ui/cardworkflow.css"' in JS and (WEB / "cardworkflow.css").is_file()
    for gone in ("data-cw-check", "settingsTarget", "data-cw=", "/api/models"):   # on the Workflow page now
        assert gone not in JS, gone


def test_both_parse():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    for f in ("cardworkflow.js", "projectcardpanel.js"):
        out = subprocess.run(["node", "--check", str(WEB / f)], capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr


# ---- the part, run --------------------------------------------------------------------------------

STUB = r"""
const fs = require("fs");
const [, , part, args] = process.argv;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}) };
eval(fs.readFileSync(part, "utf8"));
const box = { innerHTML: "" };
const k = window.projectCardKinds.workflow;
k.draw(box, JSON.parse(args), "my game", "", { save() {}, problems: {}, editable: true });
k.close();
process.stdout.write(JSON.stringify({ html: box.innerHTML, keys: k.keys, title: k.title, form: k.form }));
"""


def card(**spec):
    return {"name": "workflow", "kind": "workflow", "editable": True, "locked": [],
            "spec": {"kind": "workflow", **spec}}


def run(tmp_path, c):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "cardworkflow.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def server_line(spec):
    return api_workflow.line(api_workflow._values(spec, False))


def test_the_line_for_a_default_card(tmp_path):
    got = run(tmp_path, card())
    assert '<div class="cw-line">plan split · 4 parallel · checks: self · merge auto</div>' in got["html"]
    assert "cw-help" not in got["html"]                         # no field_help in the JSON: no line
    assert server_line({}) == "plan split · 4 parallel · checks: self · merge auto"
    assert got["title"] == "Workflow" and got["keys"] == KEYS and got["form"] is False


@pytest.mark.parametrize("spec, want", [
    ({"router": "off", "target": "claude:opus"},
     "router off → claude:opus · plan split · 4 parallel · checks: self · merge auto"),
    ({"router": "off", "target": {"provider": "claude", "model": "opus", "effort": "high"}},
     "router off → claude:opus@high · plan split · 4 parallel · checks: self · merge auto"),
    ({"plan": "single", "parallel": 1, "merge": "person"},
     "plan single · 1 parallel · checks: self · merge person"),
    ({"checks": ["self", "review", "command"], "command": "make test", "parallel": 2, "merge": "pr"},
     "plan split · 2 parallel · checks: self, review, command · merge pr"),
])
def test_the_line_matches_the_server_word_for_word(tmp_path, spec, want):
    html = run(tmp_path, card(**spec))["html"]
    assert f'<div class="cw-line">{want}</div>' in html
    assert server_line(spec) == want


def test_the_link_opens_the_workflow_page(tmp_path):
    html = run(tmp_path, card())["html"]
    assert '<a class="cw-open" href="#project/my%20game/workflow">Open workflow →</a>' in html


def test_a_broken_card_shows_no_line_but_the_link(tmp_path):
    c = card()
    c.update(spec=None, error="component.json: not valid JSON")
    html = run(tmp_path, c)["html"]
    assert "cw-line" not in html and "Open workflow →" in html


def test_the_panel_draws_the_line_no_workflow_keys_and_no_settings(tmp_path):
    out = panel(tmp_path, dict(card(checks=["self", "review"], after_merge="npm run build", access="all"),
                               choices=components.choices("workflow"),
                               choice_defaults=components.choice_defaults("workflow"),
                               choice_help=components.choice_help("workflow"),
                               field_help=components.field_help("workflow")))
    assert '<h4 class="pp-h">Workflow</h4>' in out["frame"]
    for h in ("Settings", "Run", "Uses"):
        assert f'<h4 class="pp-h">{h}' not in out["frame"], h
    assert out["settings"] is None and "data-key=" not in out["frame"] + out["kind"]
    assert "plan split · 4 parallel · checks: self, review · merge auto" in out["kind"]
    assert "parallel: this project's ceiling; the machine setting can be lower" in out["kind"]  # under the line
    assert out["kind"].index("cw-line") < out["kind"].index("cw-help")


def test_a_broken_workflow_card_still_shows_its_error(tmp_path):
    c = dict(card(), spec=None, error="component.json: not valid JSON", choices=components.choices("workflow"))
    out = panel(tmp_path, c)
    assert "component.json: not valid JSON" in out["head"] and "broken" in out["head"]


def panel(tmp_path, c):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "panel.js"
    stub.write_text(PANEL_STUB)
    got = subprocess.run(["node", str(stub), str(WEB / "choicemenu.js"), str(WEB / "settingstarget.js"),
                          str(WEB / "cardworkflow.js"), str(WEB / "projectcardpanel.js"), json.dumps(c)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


PANEL_STUB = DOM + r"""
function mk(parent) {
  const e = { parent, dataset: {}, listeners: {}, kids: {}, _html: "",
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); this.kids = {}; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    contains() { return false; }, closest() { return null; }, matches() { return false; }, focus() {},
    querySelectorAll() { return []; },
    querySelector(sel) {
      const m = /^\.([\w-]+)$/.exec(sel);
      if (!m || !new RegExp('class="[^"]*\\b' + m[1] + '\\b').test(this._html)) return null;
      return this.kids[sel] || (this.kids[sel] = mk(this));
    } };
  return e;
}
global.CSS = { escape: (s) => s };
document.querySelector = () => ({});
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") };
for (const f of process.argv.slice(2, 6)) require(f);
const card = JSON.parse(process.argv[6]);
const box = mk(null);
projectCardPanel.open(box, "p1", card, "", { ago: () => "", reload() {}, api: async () => ({}) });
const settings = box.querySelector(".pp-settings");
process.stdout.write(JSON.stringify({ frame: box.innerHTML, settings: settings ? settings.innerHTML : null,
  head: box.querySelector(".pp-head").innerHTML,
  kind: box.querySelector(".pp-kind").innerHTML }));
"""


GAME = [{"name": "story files", "paths": ["design/script/**", "design/quests/**", "slice/data/**"],
         "run": "npm run check:story"},
        {"name": "code", "paths": ["slice/src/**", "slice/tools/**", "tools/**"],
         "run": "npm run typecheck && npm run build"}]


@pytest.mark.parametrize("spec, each, together", [
    ({"checks": ["self", "command", "batch"], "command": GAME, "batch_command": "npm run build"},
     "Each change: story files → npm run check:story · code → npm run typecheck &amp;&amp; npm run build",
     "Together, before merging: npm run build"),
    ({"checks": ["self", "command"], "command": [{"paths": ["docs/**", "*.md"], "run": "make docs"}]},
     "Each change: docs/**, *.md → make docs", "Together, before merging: nothing (batch is off)"),
    ({"checks": ["self", "command", "batch"], "command": "make test"},
     "Each change: make test", "Together, before merging: make test"),
    ({}, "Each change: no check command", "Together, before merging: nothing (batch is off)"),
])
def test_the_panel_says_what_each_change_and_the_batch_run(tmp_path, spec, each, together):
    html = run(tmp_path, card(**spec))["html"]
    assert f'<div class="cw-each">{each}</div><div class="cw-together">{together}</div>' in html
    v = api_workflow._values(spec, False)                              # the server's words, the same
    assert (v["each"], v["together"]) == (each.replace("&amp;", "&"), together)


# ---- how work lands: the choice, run with fetch mocked ----------------------------------------------

LANDS_STUB = r"""
const fs = require("fs");
const [, , part, args] = process.argv;
const { card, answers, steps, ro } = JSON.parse(args);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
global.window = global; global.md = { esc };
global.document = { querySelector: () => ({}) };
const posted = [], asked = [];
global.fetch = async (url, opts) => {
  asked.push({ url, method: (opts && opts.method) || "GET", headers: (opts && opts.headers) || {} });
  if (opts && opts.body) posted.push(JSON.parse(opts.body));
  const a = answers.shift() || { status: 500, body: { error: "no answer" } };
  return { ok: a.status === 200, statusText: "x", json: async () => a.body };
};
eval(fs.readFileSync(part, "utf8"));
const fields = { ".cw-gh-repo": { value: "" }, ".cw-gh-priv": { checked: true } };
const box = { innerHTML: "", dataset: {}, listeners: {},
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
  querySelector(sel) { return this.innerHTML.includes(sel.slice(1)) ? fields[sel] : null; } };
const button = (data) => ({ dataset: data, disabled: false, closest() { return this; } });
const tick = () => new Promise((r) => setTimeout(r, 0));
const k = window.projectCardKinds.workflow;
(async () => {
  k.draw(box, card, "game", ro ? "ro" : "", { editable: !ro });
  await tick();
  const html = [box.innerHTML];
  for (const s of steps) {
    if (s.repo !== undefined) fields[".cw-gh-repo"].value = s.repo;
    if (s.private !== undefined) fields[".cw-gh-priv"].checked = s.private;
    const b = button(s.lands ? { cwLands: s.lands } : { cwAct: s.act });
    for (const fn of box.listeners.click) fn({ type: "click", target: b });
    await tick();
    html.push(box.innerHTML);
  }
  process.stdout.write(JSON.stringify({ html, posted, asked, spec: card.spec }));
})();
"""


def lands(lands="here", repo=None, remote=None, signed_in=True, can_github=True, why=""):
    return {"status": 200, "body": {"lands": lands, "repo": repo, "remote": remote, "signed_in": signed_in,
                                    "can_github": can_github, "why": why}}


def run_lands(tmp_path, answers, steps=(), spec=None, ro=False):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "lands.js"
    stub.write_text(LANDS_STUB)
    arg = {"card": card(**(spec or {})), "answers": answers, "steps": list(steps), "ro": ro}
    got = subprocess.run(["node", str(stub), str(WEB / "cardworkflow.js"), json.dumps(arg)],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def opt(html, value):
    m = re.search(r'<button type="button" class="cw-opt( on)?" data-cw-lands="%s"[^>]*>' % value, html)
    assert m, html
    return {"on": bool(m.group(1)), "disabled": " disabled" in m.group(0)}


def test_the_choice_drawn_for_here(tmp_path):
    out = run_lands(tmp_path, [lands("here", remote="me/game")])
    html = out["html"][0]
    assert '<div class="cw-lands-h">How work lands</div>' in html
    assert html.index("cw-line") < html.index("cw-lands") < html.index("cw-each")     # under the card's line
    assert opt(html, "here") == {"on": True, "disabled": False}
    assert opt(html, "github") == {"on": False, "disabled": False}
    assert "eki merges here" in html and "eki opens pull requests on GitHub" in html and "cw-repo" not in html
    assert out["asked"][0] == {"url": "/api/projects/game/lands", "method": "GET", "headers": {}}


def test_the_choice_drawn_for_github_names_the_repo(tmp_path):
    html = run_lands(tmp_path, [lands("github", repo="me/game", remote="me/game")], spec={"merge": "pr"})["html"][0]
    assert opt(html, "github")["on"] and not opt(html, "here")["on"]
    assert 'eki opens pull requests on GitHub <span class="cw-repo">me/game</span>' in html
    assert "merge pr</div>" in html


def test_pull_requests_with_a_remote_posts_github_and_redraws_the_line(tmp_path):
    out = run_lands(tmp_path, [lands("here", remote="me/game"), lands("github", repo="me/game", remote="me/game")],
                    [{"lands": "github"}])
    assert out["posted"] == [{"lands": "github"}]
    assert out["asked"][1]["method"] == "POST" and out["asked"][1]["headers"]["X-Eki"] == "1"
    assert out["asked"][1]["url"] == "/api/projects/game/lands"
    after = out["html"][-1]
    assert opt(after, "github")["on"] and "merge pr</div>" in after and out["spec"]["merge"] == "pr"
    assert "cw-gh" not in after


def test_no_remote_opens_the_form_and_adding_posts_the_repo(tmp_path):
    out = run_lands(tmp_path, [lands("here"), lands("github", repo="me/new", remote="me/new")],
                    [{"lands": "github"}, {"act": "add", "repo": " https://github.com/me/new "}])
    form = out["html"][1]
    assert out["posted"] == [{"lands": "github", "repo": "https://github.com/me/new"}]
    assert "Add the GitHub repository" in form and 'placeholder="OWNER/REPO or URL"' in form
    assert 'Create one on GitHub' in form and '<input type="checkbox" class="cw-gh-priv" checked> Private' in form
    assert opt(out["html"][-1], "github")["on"] and "cw-gh-repo" not in out["html"][-1]


@pytest.mark.parametrize("private", [True, False])
def test_create_posts_create_and_private(tmp_path, private):
    out = run_lands(tmp_path, [lands("here"), lands("github", repo="me/game", remote="me/game")],
                    [{"lands": "github"}, {"act": "create", "private": private}])
    assert out["posted"] == [{"lands": "github", "create": True, "private": private}]


def test_signed_out_offers_no_create(tmp_path):
    out = run_lands(tmp_path, [lands("here", signed_in=True)], [{"lands": "github"}])
    assert "Create one on GitHub" in out["html"][1]
    out = run_lands(tmp_path, [{"status": 200, "body": dict(lands("here")["body"], signed_in=False)}],
                    [{"lands": "github"}])
    assert "Add the GitHub repository" in out["html"][1] and "Create one on GitHub" not in out["html"][1]


def test_an_empty_repo_is_not_posted(tmp_path):
    out = run_lands(tmp_path, [lands("here")], [{"lands": "github"}, {"act": "add", "repo": "  "}])
    assert out["posted"] == [] and "name the repository" in out["html"][-1]


def test_here_posts_here_and_the_line_goes_back_to_auto(tmp_path):
    out = run_lands(tmp_path, [lands("github", repo="me/game", remote="me/game"), lands("here", remote="me/game")],
                    [{"lands": "here"}], spec={"merge": "pr"})
    assert out["posted"] == [{"lands": "here"}]
    assert opt(out["html"][-1], "here")["on"] and "merge auto</div>" in out["html"][-1]


def test_cannot_github_is_disabled_with_why(tmp_path):
    why = "gh is not logged in: run `gh auth login`"
    out = run_lands(tmp_path, [lands("here", signed_in=False, can_github=False, why=why)], [{"lands": "github"}])
    html = out["html"][0]
    assert opt(html, "github")["disabled"] and f'<div class="cw-why">{why}</div>' in html
    assert opt(html, "here") == {"on": True, "disabled": False}


def test_the_apis_error_shows_in_the_card(tmp_path):
    err = "the folder has origin me/game, not you/other"
    out = run_lands(tmp_path, [lands("here"), {"status": 400, "body": {"error": err}}],
                    [{"lands": "github"}, {"act": "add", "repo": "you/other"}])
    after = out["html"][-1]
    assert f'<div class="pj-err cw-err">{err}</div>' in after
    assert opt(after, "here")["on"] and "cw-gh-repo" in after and "merge" not in out["spec"]


def test_read_only_cards_disable_both(tmp_path):
    html = run_lands(tmp_path, [lands("here", remote="me/game")], ro=True)["html"][0]
    assert opt(html, "here")["disabled"] and opt(html, "github")["disabled"]
