"""The Settings page's files are served, and name the API they use."""
from test_web import get, web  # noqa: F401  (the fixture)


def test_settings_files_are_served(web):
    for name in ("settings.js", "settings.css"):
        code, _ = get(f"{web}/ui/{name}")
        assert code == 200


def test_settings_js_uses_the_settings_api_and_nav(web):
    _, js = get(f"{web}/ui/settings.js")
    for want in ("/api/settings/", "mtime", 'nav.register("settings"', "window.settings",
                 "X-Eki", "problems", "409"):
        assert want in js
    _, page = get(web + "/")
    assert "/ui/settings.js" in page and "/ui/settings.css" in page


def test_settings_css_uses_the_theme_variables(web):
    _, css = get(f"{web}/ui/settings.css")
    assert "var(--line)" in css and "var(--card)" in css and "#set-raw" in css
