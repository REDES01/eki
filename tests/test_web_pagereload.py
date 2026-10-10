"""The page reloads itself when a new build of eki serves it."""
import json

from test_web import get, web  # noqa: F401  (the fixture)


def test_the_page_reloads_itself_when_a_new_build_serves_it(web):
    code, body = get(f"{web}/api/status")
    assert code == 200 and json.loads(body)["build"]
    _, page = get(f"{web}/")
    assert "/ui/pagereload.js" in page
    _, js = get(f"{web}/ui/pagereload.js")
    assert "location.reload()" in js and "busy()" in js and '"/api/status"' in js
