"""A Tool card's "What it runs" part (cardtool.js), run against fixed card JSON through the tiny DOM of
test_web_choicemenu.py: every tool card shows its command and results; an images (ComfyUI) card lists
its graphs with models and sizes and marks the default, picks the default graph through choicemenu.js,
saves the output folder and size, shows problems beside the field, whether ComfyUI is up, and the
last pictures as thumbnails; read-only with ro or a pool card."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web_choicemenu import DOM

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"
JS = (WEB / "cardtool.js").read_text()

# draws the part into a box whose innerHTML is parsed into the tiny DOM, then plays steps against it
STUB = DOM + r"""
const fs = require("fs");
const [, , web, args] = process.argv;
const { card, ro, problems, steps } = JSON.parse(args);
global.md = { esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") };
document.querySelector = () => ({});
eval(fs.readFileSync(web + "/choicemenu.js", "utf8"));
eval(fs.readFileSync(web + "/cardtool.js", "utf8"));
const box = node("div", {}, null);
let inner = "";
Object.defineProperty(box, "innerHTML", { get: () => inner, set(h) { inner = h; box.children = parse(h).children;
  for (const k of box.children) k.parent = box; } });
box.dataset; box.attrs = {};
Object.defineProperty(box, "dataset", { value: {} });
const saved = [];
const ctx = { save(set) { saved.push(set); return Promise.resolve(); }, problems: problems || {}, editable: !ro };
const k = window.projectCardKinds.service;
k.draw(box, card, "game", ro ? "pool" : "", ctx);
const find = (pred) => { const out = []; const walk = (n) => { for (const c of n.children) { if (pred(c)) out.push(c); walk(c); } }; walk(box); return out; };
for (const s of steps || []) {
  if (s[0] === "pick") {
    fire(box, "click", box.querySelector(".cm-btn"));
    fire(box, "click", find((n) => n.tag === "li" && n.attrs["data-value"] === s[1])[0]);
  } else if (s[0] === "type") {
    const el = find((n) => n.attrs["data-ct"] === s[1])[0];
    el.value = s[2];
    fire(box, "change", el);
  }
}
const inputs = find((n) => n.tag === "input").map((n) => ({ key: n.attrs["data-ct"], value: n.attrs.value, off: "disabled" in n.attrs }));
const imgs = find((n) => n.tag === "img").map((n) => ({ src: n.attrs.src, href: n.parent.attrs.href }));
const rows = find((n) => n.tag === "tr" && n.attrs["data-row"]).map((n) => ({ row: n.attrs["data-row"], text: n.textContent.replace(/\s+/g, " ").trim(),
  dflt: n.classList.contains("ct-default") }));
const btn = box.querySelector(".cm-btn");
setTimeout(() => process.stdout.write(JSON.stringify({ html: inner, saved, inputs, imgs, rows, title: k.title, keys: k.keys,
  menuOff: btn ? btn.disabled : null })), 0);
"""

GRAPHS = [
    {"row": "draft", "label": "draft", "title": "Draft", "model": "flux1-schnell", "size": "1024×1024",
     "explain": "Fast first try in four steps.", "file": "flux_schnell.json", "default": True},
    {"row": "hq", "label": "hq", "title": "High quality", "model": "flux1-dev", "size": "1024×1024",
     "explain": "Slower, finer detail.", "file": "flux_dev.json", "default": False},
    {"row": "anime", "label": "anime", "title": "Anime", "model": "animagine-xl", "size": "832×1216",
     "explain": "Anime-style characters.", "file": "anime.json", "default": False},
    {"row": "edit", "label": "edit", "title": "Instruction edit", "model": "qwen-image-edit", "size": "1024×1024",
     "explain": "Changes a picture you give it.", "file": "edit.json", "default": False},
    {"row": "upscale", "label": "upscale", "title": "Upscale", "model": "4x-ultrasharp", "size": "×4",
     "explain": "Makes a picture four times bigger.", "file": "upscale.json", "default": False},
]


def images(**spec):
    return {"name": "images", "kind": "service", "editable": True,
            "spec": {"kind": "service", "provider": "comfyui", **spec},
            "live": {"tool": {"command": "eki use images <prompt>", "outputs": "PNG files in assets/generated/"},
                     "graphs": GRAPHS, "default": "draft", "out": "assets/generated", "out_path": "/p/game/assets/generated",
                     "comfy": {"up": True, "url": "http://127.0.0.1:8188", "model": "flux1-schnell"},
                     "recent": [{"path": "assets/generated/a.png", "url": "/api/projects/game/files/assets/generated/a.png", "when": "2 min ago"},
                                {"path": "assets/generated/b.png", "url": "/api/projects/game/files/assets/generated/b.png", "when": "1 h ago"}]}}


VOICE = {"name": "voice", "kind": "service", "editable": True, "spec": {"kind": "service", "script": "voice.sh"},
         "live": {"tool": {"command": "bin/voice.sh <text> <out.wav>", "outputs": "a WAV file in assets/voice/"}}}


def run(tmp_path, card, ro=False, problems=None, steps=()):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "stub.js"
    stub.write_text(STUB)
    got = subprocess.run(["node", str(stub), str(WEB), json.dumps({"card": card, "ro": ro, "problems": problems,
                                                                    "steps": list(steps)})],
                         capture_output=True, text=True, timeout=20)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_files_are_short_and_carry_their_look():
    assert len(JS.splitlines()) < 400
    assert 'href="/ui/cardtool.css"' in JS and (WEB / "cardtool.css").is_file()
    assert '"cardtool.js"' in (WEB / "projectcardpanel.js").read_text()


def test_a_voice_card_shows_its_command_and_results(tmp_path):
    got = run(tmp_path, VOICE)
    assert got["title"] == "What it runs" and got["keys"] == ["graph", "out", "width", "height"]
    assert "Runs:</span> <code class=\"ct-cmd\">bin/voice.sh &lt;text&gt; &lt;out.wav&gt;</code>" in got["html"]
    assert "Results:</span> a WAV file in assets/voice/" in got["html"]
    assert "ct-graphs" not in got["html"] and got["inputs"] == [] and got["imgs"] == []


def test_a_card_with_no_live_draws_only_what_it_has(tmp_path):
    got = run(tmp_path, {"name": "x", "kind": "service", "spec": {"kind": "service"}})
    assert "Runs:" not in got["html"] and "Nothing more to show" in got["html"]


def test_an_images_card_lists_its_graphs_and_marks_the_default(tmp_path):
    got = run(tmp_path, images())
    assert [r["row"] for r in got["rows"]] == ["draft", "hq", "anime", "edit", "upscale"]
    for r, g in zip(got["rows"], GRAPHS):
        assert g["title"] in r["text"] and g["model"] in r["text"] and g["size"] in r["text"] and g["explain"] in r["text"]
    assert [r["dflt"] for r in got["rows"]] == [True, False, False, False, False]
    assert "used by eki use images" in got["rows"][0]["text"]
    html = got["html"]
    assert "Default graph" in html and 'data-value="draft"' in html
    assert "ComfyUI: up at <code>http://127.0.0.1:8188</code> · last model <code>flux1-schnell</code>" in html
    assert "/p/game/assets/generated" in html
    assert {"key": "out", "value": "assets/generated", "off": False} in got["inputs"]
    assert got["menuOff"] is False


def test_comfyui_down_says_it_starts_on_the_next_picture(tmp_path):
    c = images()
    c["live"]["comfy"] = {"up": False, "url": "http://127.0.0.1:8188", "model": ""}
    assert "not running — starts on the next picture" in run(tmp_path, c)["html"]


def test_picking_a_graph_saves_it(tmp_path):
    got = run(tmp_path, images(), steps=[["pick", "anime"]])
    assert got["saved"] == [{"graph": "anime"}]


def test_editing_the_folder_and_size_saves_them(tmp_path):
    got = run(tmp_path, images(), steps=[["type", "out", " art/pictures "], ["type", "width", "768"], ["type", "height", ""]])
    assert got["saved"] == [{"out": "art/pictures"}, {"width": 768}, {"height": None}]


def test_the_saved_setting_wins_over_the_default(tmp_path):
    got = run(tmp_path, images(graph="hq", out="art", width=512))
    assert 'data-value="hq"' in got["html"].split('class="set-in cm-btn')[1].split(">")[0]
    assert {"key": "out", "value": "art", "off": False} in got["inputs"]
    assert {"key": "width", "value": "512", "off": False} in got["inputs"]


def test_problems_show_beside_the_field(tmp_path):
    got = run(tmp_path, images(), problems={"out": "must stay inside the project", "graph": "no such graph"})
    html = got["html"]
    assert 'data-ct-problem="out">must stay inside the project' in html
    assert 'data-ct-problem="graph">no such graph' in html
    assert html.index('data-ct="out"') < html.index('data-ct-problem="out"') < html.index('data-ct="width"')


def test_thumbnails_render_and_open_the_full_picture(tmp_path):
    got = run(tmp_path, images())
    assert got["imgs"] == [{"src": u, "href": u} for u in
                           ("/api/projects/game/files/assets/generated/a.png", "/api/projects/game/files/assets/generated/b.png")]
    assert "2 min ago" in got["html"]


@pytest.mark.parametrize("ro, extra", [(True, {}), (False, {"ref": "images"})])
def test_read_only_or_pool_card_changes_nothing(tmp_path, ro, extra):
    got = run(tmp_path, {**images(), **extra}, ro=ro, steps=[["type", "out", "x"], ["pick", "hq"]])
    assert got["saved"] == [] and got["menuOff"] is True
    assert all(i["off"] for i in got["inputs"])


def test_parses():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    out = subprocess.run(["node", "--check", str(WEB / "cardtool.js")], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
