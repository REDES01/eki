"""The project page's Changes section (projecthistory.js): changes and going back in plain words, with
branches and commits only inside each row's closed Details fold."""
import json
import re
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from eki import server

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def test_the_page_loads_the_history_script_after_project_js(web):
    page = get(web + "/")[1]
    assert page.index("/ui/project.js") < page.index("/ui/projecthistory.js") < page.index("/ui/app.js")
    js = get(web + "/ui/project.js")[1]
    assert "projectHistory.load(id)" in js and "when }" in js


def test_the_history_js_names_the_routes_and_words(web):
    code, js = get(web + "/ui/projecthistory.js")
    assert code == 200
    for name in ('"/history"', '"/goback"', "Go back to before this", "Changes", '"X-Eki": "1"', "change: c.id",
                 "This change and every one after it are undone; going back can itself be undone.",
                 'data-sect="history"', "pj-sect", "pj-fold", "<summary", "Details"):
        assert name in js, name


def test_the_row_template_says_nothing_of_git_outside_details():
    js = (WEB / "projecthistory.js").read_text()
    template = js[js.index("function row("):js.index("function html(")]
    outside = re.sub(r"<details.*?</details>", "", template, flags=re.S)
    assert "<details" in template and "Go back to before this" in outside
    for word in ("branch", "commit", "worktree", "sha"):
        assert word not in outside.lower(), word


STUB = """
const els = {};
const mk = (tag) => {
  const e = { tag, id: "", className: "", innerHTML: "", dataset: {}, children: [], disabled: false,
    classList: { set: new Set(), toggle() {}, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); } },
    appendChild(c) { this.children.push(c); if (c.id) els[c.id] = c; return c; },
    addEventListener(t, f) { this["on" + t] = f; } };
  return e;
};
els["pj-page"] = mk("div");
global.md = { esc: (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") };
global.window = { projects: { when: (t) => "at " + t } };
global.document = {
  getElementById: (id) => {
    if (els[id]) return els[id];
    for (const s of Object.values(els)) {      // the section's inner parts appear once it is made
      if (s.innerHTML && s.innerHTML.includes(`id="${id}"`)) return (els[id] = mk("x"));
    }
    return null;
  },
  createElement: mk,
};
const answer = JSON.parse(process.argv[3]);
const asked = [];
global.fetch = async (url, opt) => { asked.push([url, opt && opt.method || "GET"]); return { ok: true, json: async () => answer }; };
require(process.argv[2]);
window.projectHistory.load("ab12").then(() => {
  const body = els["pj-history-body"].innerHTML, sum = els["pj-history-sum"].innerHTML;
  process.stdout.write(JSON.stringify({ asked, sum, body, section: els["pj-history"].innerHTML }));
});
"""


def test_a_history_answer_renders_its_line_and_buttons(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    answer = {"today": 2, "line": "2 changes landed today", "changes": [
        {"id": "a" * 40, "title": "Add the <intro>", "when": 1760000000, "by": "eki"},
        {"id": "b" * 40, "title": "Fix typo", "when": 1760000100, "by": "you"}]}
    out = subprocess.run(["node", str(stub), str(WEB / "projecthistory.js"), json.dumps(answer)],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["asked"] == [["/api/projects/ab12/history", "GET"]]
    assert got["sum"] == "2 changes landed today"
    assert "Changes" in got["section"]
    body = got["body"]
    assert body.count("Go back to before this") == 2 and body.count("<details") == 2
    assert "by eki" in body and "by you" in body and "at 1760000000" in body
    assert "Add the &lt;intro&gt;" in body and "aaaaaaa" in body and "a" * 8 not in body
    outside = re.sub(r"<details.*?</details>", "", body, flags=re.S).lower()
    assert not any(w in outside for w in ("branch", "commit", "worktree"))
