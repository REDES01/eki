"""The Settings frame: sections at #settings/<section>, one file per section, the shared pieces."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from eki import targets
from test_web import get, web  # noqa: F401  (the fixture)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
SECTIONS = {"jobs": "settingsmodels", "rows": "settingsrows", "providers": "settingsproviders",
            "notifications": "settingsnotify", "machine": "settingsmachine", "self": "settingsself",
            "components": "settingscomponents", "sync": "settingssync"}
ORDER = ["settingsform", "settingstarget", "settingsmodels", "settingsnotify", "settingsmachine", "settingsself",
         "settingsrows", "settingsproviders", "settingscomponents", "settingssync", "settings"]
CLASSES = ["#set-nav", ".set-group", ".set-line", ".set-label", ".set-sub", ".set-ctl", ".set-fold",
           ".set-fold.open", ".set-sum", ".set-pill", ".set-amber", ".set-target",
           ".set-flines", ".set-ftitle", ".set-block", ".set-wide", ".set-danger"]


def _node():
    if not shutil.which("node"):
        pytest.skip("node is not installed")


def test_nav_routes_a_settings_section_and_never_as_a_thread(tmp_path):
    js = (WEB / "nav.js").read_text()
    assert '(settings|customize)' in js
    _node()
    body = re.search(r"function route\(name\) \{.*?\n  \}", js, re.S).group(0)
    stub = tmp_path / "route.js"
    stub.write_text('const RESERVED = ["pictures", "station", "settings", "projects"];\n' + body +
                    '\nconsole.log(JSON.stringify(["settings/rows", "settings", "settings/self", "abc"].map(route)));')
    out = json.loads(subprocess.run(["node", str(stub)], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert out == [["settings", "rows"], ["settings", None], ["settings", "self"], ["chat", None]]


def test_settings_js_is_the_frame(web):
    _, js = get(f"{web}/ui/settings.js")
    assert ('const SECTIONS = ["jobs", "rows", "providers", "notifications", "machine", "self", "components", "sync", '
            '"pictures"]') in js
    for want in ('id="set-nav"', 'id="set-body"', '"#settings/"', "S.sect", "window.settingsSections",
                 "/api/settings/", "mtime", "X-Eki", "problems", "409", 'nav.register("settings"', "window.settings",
                 "saving…", "not saved", "set-status", "draft:", "got:", "note,", "render,", "clone,", "section,"):
        assert want in js, want
    assert "S.tab" not in js and "rowCard" not in js and "provCard" not in js
    assert 'data-act="save"' not in js and "changed, not saved" not in js


@pytest.mark.parametrize("name, path", SECTIONS.items())
def test_each_section_file_is_served_and_registers(web, name, path):
    code, js = get(f"{web}/ui/{path}.js")
    assert code == 200
    assert f'settings.section("{name}"' in js and f'push(["{name}"' in js
    assert re.search(r'file: "(routing|providers)"', js)


def test_the_page_loads_the_section_files_before_the_frame(web):
    _, page = get(web + "/")
    at = [page.index(f'<script src="/ui/{n}.js"></script>') for n in ORDER]
    assert at == sorted(at)


def test_the_form_helpers_are_shared(web):
    code, js = get(f"{web}/ui/settingsform.js")
    assert code == 200
    assert "window.settingsForm = { group, line, fold, check, text, field, memo, block, lines, danger }" in js
    for cls in ("set-group", "set-line", "set-label", "set-sub", "set-ctl", "set-fold", "set-sum", "data-fold", "not saved — in memory"):
        assert cls in js


def test_lines_block_and_danger_draw_a_fold_body(tmp_path):
    _node()
    stub = tmp_path / "form.js"
    stub.write_text('global.window = global; global.md = { esc: (s) => String(s) };\nrequire(process.argv[2]);\n'
                    'const f = settingsForm;\n'
                    'console.log(JSON.stringify([f.lines("T", "<i>"), f.lines("", "<i>"),'
                    ' f.block("Examples", "<textarea>", "s"), f.danger("Delete", "delrow")]));')
    out = subprocess.run(["node", str(stub), str(WEB / "settingsform.js")], capture_output=True, text=True, timeout=30, check=True)
    titled, bare, block, danger = json.loads(out.stdout)
    assert "set-flines" in titled and "set-ftitle" in titled and ">T<" in titled and "<i>" in titled
    assert "set-flines" in bare and "set-ftitle" not in bare
    assert "set-line set-block" in block and "set-wide" in block and "set-sub" in block and "<textarea>" in block
    assert 'data-act="delrow"' in danger and "set-danger" in danger and "set-red-label" in danger and "set-line" in danger


def test_settings_css_has_every_class_and_no_hex_colour(web):
    _, css = get(f"{web}/ui/settings.css")
    for cls in CLASSES:
        assert re.search(re.escape(cls) + r"[\s,{:>]", css), cls
    assert "max-width: 820px" in css and "var(--amber)" in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"var\([^)]*\)", "", css))
    target = re.search(r"\.set-target \{([^}]*)\}", css).group(1)
    assert "inline-flex" in target and "wrap" in target
    assert re.search(r"\.set-target[^{]*select[^{]*\{[^}]*width: auto", css)


def test_no_settings_script_paints_amber_inline():
    for f in WEB.glob("settings*.js"):
        assert 'style="color: var(--amber)"' not in f.read_text(), f.name
    assert 'class="dim set-amber"' in (WEB / "settingstarget.js").read_text()


def test_every_settings_file_stays_small():
    for f in list(WEB.glob("settings*.js")) + list(WEB.glob("settings*.css")):
        assert len(f.read_text().splitlines()) < 400, f.name
    assert len((WEB / "settings.js").read_text().splitlines()) < 300


LISTED = [{"model": "claude-opus-5-5", "label": "Opus 5.5"}, {"model": "default", "label": "Default"}]


def test_the_page_labels_a_target_as_targets_label_does(tmp_path):
    js = (WEB / "settingstarget.js").read_text()
    body = re.search(r"function label\(cur, models\) \{.*?\n  \}", js, re.S).group(0)
    assert 'join(" · ")' in body and '"its default"' in body
    assert re.search(r"window\.settingsTarget = \{[^}]*\blabel\b", js)
    cases = [({"provider": "claude", "model": "claude-opus-5-5", "effort": "high"}, "claude", "claude-opus-5-5", "high"),
             ("claude:claude-haiku-9", "claude", "claude-haiku-9", None),
             ("codex", "codex", None, None)]
    want = [targets.label(p, m, e, LISTED if p == "claude" else None) for _, p, m, e in cases]
    assert want == ["claude · Opus 5.5 · high", "claude · claude-haiku-9", "codex · its default"]
    _node()
    stub = tmp_path / "label.js"
    stub.write_text('global.window = global; global.md = { esc: (s) => String(s) };\nrequire(process.argv[2]);\n'
                    'const [cases, models] = JSON.parse(process.argv[3]);\n'
                    'console.log(JSON.stringify(cases.map((c) => settingsTarget.label(c, models))));')
    models = {"claude": {"listed": LISTED}, "codex": {"listed": None}}
    out = subprocess.run(["node", str(stub), str(WEB / "settingstarget.js"), json.dumps([[c[0] for c in cases], models])],
                         capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(out.stdout) == want


@pytest.mark.parametrize("link", [True, False], ids=["sidebar-link", "no-link"])
def test_sections_register_though_window_settings_is_the_sidebar_link(tmp_path, link):
    """The page's <a id="settings"> is window.settings until settings.js runs; every section still registers."""
    _node()
    stub = tmp_path / "page.js"
    stub.write_text(
        'const vm = require("vm"), fs = require("fs"), path = require("path");\n'
        'const [dir, order, link] = [process.argv[2], JSON.parse(process.argv[3]), process.argv[4] === "1"];\n'
        'const ctx = vm.createContext({});\n'
        'vm.runInContext("var window = this;", ctx);\n'
        'if (link) ctx.settings = { tagName: "A", id: "settings" };\n'
        'Object.assign(ctx, { md: { esc: String }, nav: { register() {} }, document: { getElementById: () => null } });\n'
        'const errors = [];\n'
        'for (const n of order) {\n'
        '  const f = path.join(dir, n + ".js");\n'
        '  try { vm.runInContext(fs.readFileSync(f, "utf8"), ctx, { filename: f }); }\n'
        '  catch (e) { errors.push(n + ": " + e.message); }\n'
        '}\n'
        'let sections = null;\n'
        'try { sections = vm.runInContext("window.settings.sections()", ctx); } catch (e) { errors.push("sections: " + e.message); }\n'
        'console.log(JSON.stringify({ errors, sections: sections && Array.from(sections) }));')
    out = subprocess.run(["node", str(stub), str(WEB), json.dumps(ORDER), "1" if link else "0"],
                         capture_output=True, text=True, timeout=30, check=True)
    got = json.loads(out.stdout)
    assert got["errors"] == []
    assert got["sections"] == ["jobs", "rows", "providers", "notifications", "machine", "self", "components", "sync"]


# settings.js over a stub page: fake timers, a fetch whose POSTs we answer by hand, one section
SAVES = r"""
const vm = require("vm"), fs = require("fs");
const posts = [], timers = [];
let now = 0, nid = 0;
const el = (id) => ({ id, textContent: "", innerHTML: "", hidden: false, scrollTop: 0, dataset: {},
  classList: { toggle(c, on) { this[c] = on; }, add() {}, remove() {} },
  querySelectorAll: () => [], querySelector: () => null, insertBefore() {}, addEventListener(t, f) { (this.on = this.on || {})[t] = f; } });
const els = {};
let view = null;
const document = { getElementById: (id) => id === "setview" ? view : (els[id] = els[id] || el(id)),
  createElement: () => (view = el("setview")) };
const answer = (status, body) => ({ ok: status === 200, status, statusText: "", json: async () => body });
const fetch = (url, o) => {
  if (!o) return Promise.resolve(answer(200, { data: { v: "" }, mtime: 1, path: "/x/routing.json" }));
  return new Promise((res) => posts.push({ body: JSON.parse(o.body), res }));
};
const ctx = vm.createContext({ document, fetch, console, location: {}, confirm: () => true,
  md: { esc: String }, nav: { register() {} },
  setTimeout: (f, ms) => (timers.push({ id: ++nid, at: now + ms, f }), nid),
  clearTimeout: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); } });
vm.runInContext("var window = this;", ctx);
ctx.settingsSections = [["rows", { title: "Rows", file: "routing", html: () => "",
  edit: (e, d) => { d.v = e.value; return false; } }]];
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const flush = () => new Promise((r) => setImmediate(r));
const tick = async (ms) => {
  now += ms;
  for (let t; (t = timers.filter((x) => x.at <= now).sort((a, b) => a.at - b.at)[0]);) { timers.splice(timers.indexOf(t), 1); t.f(); }
  for (let i = 0; i < 5; i++) await flush();
};
const type = (value, kind = "input") => view.on[kind]({ type: kind,
  target: { id: "f", value, type: "text", tagName: "INPUT", matches: () => true, closest: () => null } });
(async () => {
  await ctx.settings.show("rows");
  const out = {};
  type("a"); await tick(300); type("ab"); await tick(300);
  out.waited = posts.length;
  await tick(300);
  out.one = posts.map((p) => p.body);
  out.during = els["set-status"].textContent + " | " + els.meta.textContent;
  type("abc"); await tick(600);
  out.inflight = posts.length;
  posts[0].res(answer(200, { ok: true, mtime: 2 })); await tick(0);
  out.two = posts.map((p) => p.body);
  posts[1].res(answer(200, { ok: true, mtime: 3 })); await tick(0);
  out.after = [posts.length, els["set-status"].textContent, els.meta.textContent];
  type("", "change"); await tick(0);
  posts[2].res(answer(400, { problems: ["rows[2] has no key"] })); await tick(0);
  out.refused = [els["set-status"].textContent, els["set-status"].classList.bad, els.meta.textContent, els["set-note"].innerHTML];
  type("abc", "change"); await tick(0);
  out.back = [posts.length, els["set-status"].textContent, els["set-note"].innerHTML];
  console.log(JSON.stringify(out));
})();
"""


def test_changes_save_themselves_one_post_per_file_at_a_time(tmp_path):
    _node()
    stub = tmp_path / "saves.js"
    stub.write_text(SAVES)
    run = subprocess.run(["node", str(stub), str(WEB / "settings.js")], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["waited"] == 0                                          # still typing: nothing sent
    assert out["one"] == [{"data": {"v": "ab"}, "mtime": 1}]            # two edits within 600 ms, one POST
    assert out["during"] == "Saving… | /x/routing.json · saving…"
    assert out["inflight"] == 1                                         # an edit in flight waits for the answer
    assert out["two"][1] == {"data": {"v": "abc"}, "mtime": 2}          # then goes once, with the mtime it gave
    assert out["after"] == [2, "Saved", "/x/routing.json"]
    status, bad, meta, note = out["refused"]
    assert status == "Not saved" and bad is True and meta.endswith(" · not saved")
    assert "Not saved yet — 1 problem:<ul><li>rows[2] has no key</li></ul>" in note
    assert out["back"] == [3, "", ""]                                   # back to what's on disk: nothing to send, note gone
