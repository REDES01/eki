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
            "notifications": "settingsnotify", "self": "settingsself"}
ORDER = ["settingsform", "settingstarget", "settingsmodels", "settingsnotify", "settingsself",
         "settingsrows", "settingsproviders", "settings"]
CLASSES = ["#set-nav", ".set-group", ".set-line", ".set-label", ".set-sub", ".set-ctl", ".set-fold",
           ".set-fold.open", ".set-sum", ".set-pill", ".set-amber", ".set-target"]


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
    assert 'const SECTIONS = ["jobs", "rows", "providers", "notifications", "self"]' in js
    for want in ('id="set-nav"', 'id="set-body"', '"#settings/"', "S.sect", "window.settingsSections",
                 "/api/settings/", "mtime", "X-Eki", "problems", "409", 'nav.register("settings"', "window.settings",
                 "changed, not saved", "draft:", "got:", "note,", "render,", "clone,", "section,"):
        assert want in js, want
    assert "S.tab" not in js and "rowCard" not in js and "provCard" not in js


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
    assert "window.settingsForm = { group, line, fold, check, text, field, memo }" in js
    for cls in ("set-group", "set-line", "set-label", "set-sub", "set-ctl", "set-fold", "set-sum", "data-fold", "not saved — in memory"):
        assert cls in js


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
