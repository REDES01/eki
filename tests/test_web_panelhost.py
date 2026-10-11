"""The side panel hosting a non-file view (panel.host in panel.js): swap, back link, close, Esc, [data-file] clicks."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"

# Just enough page to load panel.js + docview.js, host owners, click and press keys, and see what happened.
STUB = """
const els = {};
const mk = (id) => ({ id, tagName: "DIV", innerHTML: "", textContent: "", href: "", hidden: true, dataset: {},
  listeners: {}, parent: null, scrollTop: 0, classList: { add() {}, remove() {} },
  addEventListener(ev, fn) { this.listeners[ev] = fn; },
  contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; },
  closest(sel) { if (sel === "[data-file]") { for (let n = this; n; n = n.parent) if (n.dataset.file) return n; }
    return null; },
  blur() { out.blurred.push(this.id); if (document.activeElement === this) document.activeElement = null; },
  querySelector() { return null; } });
const el = (id) => els[id] || (els[id] = mk(id));
const out = { log: [], blurred: [], fetched: [] };
const doc = {};
global.Event = class { constructor(type) { this.type = type; } };
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } };
global.window = global;
global.addEventListener = () => {};
global.dispatchEvent = () => {};
global.setInterval = () => 1; global.clearInterval = () => {};
global.document = { getElementById: el, addEventListener(ev, fn) { doc[ev] = fn; }, hidden: false, activeElement: null };
global.md = { esc: (s) => String(s), render: (t) => t, outline: () => [] };
global.fetch = async (url) => { out.fetched.push(url);
  return { ok: true, text: async () => "plain", json: async () => ({ path: "/a/b.md", name: "b.md", text: "x",
    hash: "h", mtime: 0, editable: false, reason: "" }) }; };
const owner = (name) => ({ name, sub: name + " · skill",
  draw(body) { out.log.push(["draw", name, body.id]); body.innerHTML = "drawn " + name; },
  close(why) { out.log.push(["close", name, why]); } });
const O = { a: owner("Alpha"), b: owner("Beta") };
require(process.argv[2]); require(process.argv[3]);
const tick = () => new Promise((r) => setTimeout(r, 0));
const field = (tag) => { const f = mk("field"); f.tagName = tag; f.parent = el("panel-body"); return f; };
(async () => {
  for (const s of JSON.parse(process.argv[4])) {
    if (s[0] === "host") await panel.host(O[s[1]]);
    if (s[0] === "show") await panel.show(s[1], s[2]);
    if (s[0] === "close") await panel.close();
    if (s[0] === "back") { el("panel-back").listeners.click({ preventDefault() {} }); await tick(); }
    if (s[0] === "closex") { el("panel-close").listeners.click(); await tick(); }
    if (s[0] === "esc") { doc.keydown({ key: "Escape" }); await tick(); }
    if (s[0] === "focus") document.activeElement = field(s[1]);
    if (s[0] === "retitle") panel.retitle(s[1], s[2]);
    if (s[0] === "click") {
      const f = mk("link"); f.dataset.file = s[1]; f.parent = s[2] ? el("panel-body") : null;
      doc.click({ target: f, preventDefault() {} }); await tick(); await tick();
    }
    if (s[0] === "snap") out.log.push(["snap", el("panel-name").textContent, el("panel-path").textContent,
      el("panel-open").hidden, el("panel-back").hidden, el("panel-back").textContent, el("panel").hidden,
      panel.owner() && panel.owner().name]);
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def run(tmp_path, steps):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    out = subprocess.run(["node", str(stub), str(WEB / "panel.js"), str(WEB / "docview.js"), json.dumps(steps)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_page_has_the_back_link_before_the_title():
    page = (WEB / "index.html").read_text()
    assert 'id="panel-back"' in page and page.index('id="panel-back"') < page.index('class="pt"')
    assert "#panel-back" in (WEB / "style.css").read_text()


def test_host_draws_into_the_body_with_name_sub_and_no_open(tmp_path):
    log = run(tmp_path, [["host", "a"], ["snap"]])["log"]
    assert log[0] == ["draw", "Alpha", "panel-body"]
    assert log[1] == ["snap", "Alpha", "Alpha · skill", True, True, "", False, "Alpha"]


def test_host_again_redraws_and_another_owner_swaps(tmp_path):
    log = run(tmp_path, [["host", "a"], ["host", "a"], ["host", "b"], ["snap"]])["log"]
    assert log[:4] == [["draw", "Alpha", "panel-body"], ["draw", "Alpha", "panel-body"],
                       ["close", "Alpha", "swap"], ["draw", "Beta", "panel-body"]]
    assert log[4][1] == "Beta" and log[4][7] == "Beta"


def test_retitle_only_while_hosted(tmp_path):
    log = run(tmp_path, [["host", "a"], ["retitle", "Alpha 2", "new"], ["snap"],
                         ["show", "/x/a.log"], ["retitle", "no", "no"], ["snap"]])["log"]
    assert log[1][1:3] == ["Alpha 2", "new"]
    assert log[-1][1:3] == ["a.log", "/x/a.log"]


def test_show_with_back_then_the_back_link_hosts_again(tmp_path):
    log = run(tmp_path, [["host", "a"], ["show", "/x/notes.log", {"back": True}], ["snap"], ["back"], ["snap"]])["log"]
    assert log[1] == ["close", "Alpha", "back"]
    assert log[2] == ["snap", "notes.log", "/x/notes.log", False, False, "← back to Alpha", False, None]
    assert log[3] == ["draw", "Alpha", "panel-body"]
    assert log[4] == ["snap", "Alpha", "Alpha · skill", True, True, "", False, "Alpha"]


def test_a_doc_with_back_also_gets_the_link(tmp_path):
    log = run(tmp_path, [["host", "a"], ["show", "/a/b.md", {"back": True}], ["snap"]])["log"]
    assert log[1] == ["close", "Alpha", "back"] and log[2][4:6] == [False, "← back to Alpha"]


def test_plain_show_closes_the_owner_and_hides_back(tmp_path):
    log = run(tmp_path, [["host", "a"], ["show", "/x/a.log", {"back": True}], ["host", "a"],
                         ["show", "/x/b.log"], ["snap"]])["log"]
    assert log[3] == ["close", "Alpha", "show"]
    assert log[4][3:5] == [False, True] and log[4][7] is None


def test_close_button_and_function_and_esc_pass_close(tmp_path):
    for how in ("close", "closex", "esc"):
        log = run(tmp_path, [["host", "a"], [how], ["snap"]])["log"]
        assert log[1] == ["close", "Alpha", "close"], how
        assert log[2][4] is True and log[2][6] is True and log[2][7] is None


def test_esc_in_a_field_of_the_body_only_blurs(tmp_path):
    got = run(tmp_path, [["host", "a"], ["focus", "TEXTAREA"], ["esc"], ["snap"], ["esc"], ["snap"]])
    assert got["blurred"] == ["field"]
    assert got["log"][1][6] is False and got["log"][1][7] == "Alpha"
    assert got["log"][2] == ["close", "Alpha", "close"] and got["log"][3][6] is True


def test_a_file_click_inside_the_hosted_body_passes_back(tmp_path):
    log = run(tmp_path, [["host", "a"], ["click", "/x/k.log", True], ["snap"]])["log"]
    assert log[1] == ["close", "Alpha", "back"] and log[2][5] == "← back to Alpha"


def test_a_file_click_outside_the_body_is_a_plain_show(tmp_path):
    log = run(tmp_path, [["host", "a"], ["click", "/x/k.log", False], ["snap"]])["log"]
    assert log[1] == ["close", "Alpha", "show"] and log[2][4] is True


def test_panel_stays_small():
    assert len((WEB / "panel.js").read_text().splitlines()) <= 400
