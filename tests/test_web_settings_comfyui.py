"""Settings → Providers → ComfyUI: its workflows listed like a provider's models, with their buttons."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_web import get, web  # noqa: F401  (the fixture)

WEB = Path(__file__).resolve().parent.parent / "eki" / "web"


def test_the_script_is_loaded_after_the_providers_section(web):
    code, html = get(f"{web}/")
    assert code == 200
    assert html.index("/ui/settingsproviders.js") < html.index("/ui/settingscomfyui.js") < html.index("/ui/settings.js")
    code, js = get(f"{web}/ui/settingscomfyui.js")
    assert code == 200
    assert js.startswith("// Settings → Providers → a ComfyUI entry")
    assert len(js.splitlines()) < 400
    for path in ('"/api/comfyui/workflows"', '"/add"', '"/edit"', '"/reload"', '"/remove"', '"/open"', '"/try"'):
        assert path in js, path
    assert "window.settingsComfy = { load, line, body, act, edit }" in js
    assert "Save (API)" in js and "Open in ComfyUI" in js and "Reload from file" in js


def test_the_providers_section_hooks_it_in_only_for_kind_comfyui():
    js = (WEB / "settingsproviders.js").read_text()
    assert 'cfg.kind === "comfyui" && window.settingsComfy' in js
    assert len(re.findall(r"settingsComfy\.(line|body)\(", js)) == 2
    assert "settingsComfy.act(" in js and "settingsComfy.edit(" in js


SCRIPT = r"""
global.window = global; global.md = { esc: (s) => String(s) };
global.confirm = () => true;
const opened = [], posts = [];
const WF = [
  { id: "klein-draft", label: "FLUX.2 klein — draft", file: "klein-draft.json", path: "/w/klein-draft.json", error: "",
    inputs: [{ name: "seed", node: "3", field: "seed", default: 7, source: "" },
             { name: "prompt", node: "6", field: "text", default: "", source: "{{prompt}}" }],
    models: [{ name: "flux2-klein.safetensors", node: "1", field: "unet_name", found: true },
             { name: "qwen-vae.safetensors", node: "2", field: "vae_name", found: false }],
    sampler: "euler", steps: 4, width: 1024, height: 768, needs_source: false },
  { id: "upscale", label: "klein — upscale", file: "upscale.json", path: "/w/upscale.json", error: "bad node 9",
    inputs: [], models: [], sampler: "", steps: 0, width: 0, height: 0, needs_source: true }];
global.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  if (body) posts.push([url, body]);
  const ok = (x) => ({ ok: true, json: async () => x });
  if (url.endsWith("/try")) return ok({ run: "r42", thread: "t9" });
  if (url.endsWith("/open")) return ok({ url: "http://127.0.0.1:8188/?w=x", copied: false });
  if (url.endsWith("/add")) return ok({ added: ["new"], skipped: [{ path: "/g/x.json", why: "not API format" }], workflows: WF });
  if (url.endsWith("/remove")) return { ok: false, statusText: "x", json: async () => ({ error: "in use by card portrait" }) };
  return ok({ workflows: WF, folder: "/h/.eki/comfyui/workflows", comfyui_dir: "/c" });
};
window.open = (u) => opened.push(u);
const got = { effective: { entries: {
  pics: { kind: "comfyui", label: "ComfyUI", can_effective: ["image"], unwritten: true },
  claude: { kind: "claude_code", label: "Claude", can_effective: ["text"] } } } };
const draft = {}, secs = {};
global.settings = { section: (n, s) => (secs[n] = s), draft: () => draft, got: () => got,
  note: () => {}, clone: (x) => JSON.parse(JSON.stringify(x)), render: () => {} };
require(process.argv[2]); require(process.argv[3]); require(process.argv[4]);
const P = secs.providers, out = {};
const el = (cls, fold) => ({ dataset: { fold }, classList: { contains: (c) => c === cls, remove() {} }, querySelector: () => null });
const tick = () => new Promise((r) => setTimeout(r, 0));
(async () => {
  out.before = P.html(draft);
  await tick();
  out.closed = P.html(draft);
  P.act("fold", null, el("", "pics"));
  out.list = P.html(draft);
  P.act("fold", null, el("", "claude"));
  out.claude = P.html(draft);
  P.act("fold", null, el("", "pics"));
  P.act("fold", null, el("set-wf", "wf:klein-draft"));
  out.open = P.html(draft);
  const inWf = { closest: (s) => (s === ".set-wf" ? { dataset: { fold: "wf:klein-draft" } } : null), matches: () => false };
  out.tookTry = P.edit({ ...inWf, dataset: { wftry: "prompt" }, value: "a fox" }, draft, null, { type: "input" });
  await P.act("wftry", { dataset: { wf: "klein-draft" } }, null);
  P.edit({ ...inWf, dataset: { f: "wflabel" }, value: " Draft " }, draft, null, { type: "change" });
  P.edit({ ...inWf, dataset: { wfdef: "seed" }, value: "9" }, draft, null, { type: "change" });
  await P.act("wfopen", { dataset: { wf: "klein-draft" } }, null);
  await P.act("wfremove", { dataset: { wf: "klein-draft" } }, null);
  await tick();
  out.after = P.html(draft);
  P.edit({ matches: (s) => s === "[data-wfaddpath]", dataset: {}, value: "~/game/tools" }, draft, null, { type: "input" });
  await P.act("wfadd", { dataset: {} }, null);
  out.added = P.html(draft);
  out.posts = posts; out.opened = opened; out.draft = draft;
  console.log(JSON.stringify(out));
})();
"""


def test_workflows_fold_open_and_their_buttons_post_the_endpoints(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    stub = tmp_path / "comfy.js"
    stub.write_text(SCRIPT)
    out = json.loads(subprocess.run(["node", str(stub), str(WEB / "settingsform.js"), str(WEB / "settingsproviders.js"),
                                     str(WEB / "settingscomfyui.js")], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert "2 workflows" in out["closed"]                                     # the count on the comfyui line
    assert out["closed"].count("workflows") == 1                              # and not on claude's
    lst = out["list"]
    assert "FLUX.2 klein — draft" in lst and "klein — upscale" in lst and "/h/.eki/comfyui/workflows" in lst
    assert "set-wf open" not in lst and "Save (API)" in lst and 'data-act="wfadd"' in lst
    assert "set-amber" in lst                                                 # upscale's error marked on its line
    assert "set-wf" not in out["claude"]                                      # no workflows under another kind
    body = out["open"].split('data-fold="wf:klein-draft"', 1)[1].split('data-fold="wf:upscale"', 1)[0]
    for want in ("<code>{{prompt}}</code>", "node 3 · seed", 'data-wfdef="seed" value="7"', "not found",
                 "euler · 4 steps", "1024×768", 'data-act="wfopen"', 'data-act="wfreload"', 'data-act="wftry"',
                 'data-act="wfremove" data-wf="klein-draft"', 'data-wftry="prompt"'):
        assert want in body, want
    assert body.index('data-wftry="prompt"') < body.index('data-wftry="seed"')   # prompt first
    assert body.count("not found") == 1                                       # only the missing model
    assert out["tookTry"] is False                                            # no redraw, no draft change
    posts = out["posts"]
    assert ["/api/comfyui/workflows/try", {"id": "klein-draft", "inputs": {"prompt": "a fox"}}] in posts
    assert ["/api/comfyui/workflows/edit", {"id": "klein-draft", "label": "Draft"}] in posts
    assert ["/api/comfyui/workflows/edit", {"id": "klein-draft", "defaults": {"seed": "9"}}] in posts
    assert ["/api/comfyui/workflows/open", {"id": "klein-draft"}] in posts
    assert ["/api/comfyui/workflows/remove", {"id": "klein-draft"}] in posts
    assert ["/api/comfyui/workflows/add", {"path": "~/game/tools"}] in posts
    assert out["opened"] == ["http://127.0.0.1:8188/?w=x"]
    after = out["after"]
    assert "Started run <code>r42</code>" in after and 'href="#t9"' in after
    assert "in use by card portrait" in after                                 # the refused remove, in amber
    assert "Added <code>new</code>" in out["added"] and "not API format" in out["added"]
    assert out["draft"] == {}                                                 # nothing went into providers.json
