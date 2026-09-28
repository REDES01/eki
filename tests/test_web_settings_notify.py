"""The notify block of routing.json on the Settings page: checked, written, served."""
import json

import pytest

from eki import notify
from test_web import get, post, web  # noqa: F401  (the fixture)
from test_web_settings import _read


def _save(web, **notify_block):
    got = _read(web, "routing")
    data = {**got["data"], "notify": notify_block}
    return post(web + "/api/settings/routing", {"data": data, "mtime": got["mtime"]})


def test_a_valid_notify_block_is_written_and_read_by_notify(web, home):
    code, out = _save(web, server="http://ntfy.local:8080", topic="eki-xz_42", events=["needs_you"])
    assert code == 200 and out["ok"] is True
    on_disk = json.loads((home / "routing.json").read_text())["notify"]
    assert on_disk == {"server": "http://ntfy.local:8080", "topic": "eki-xz_42", "events": ["needs_you"]}
    assert notify.settings()["topic"] == "eki-xz_42"
    assert notify.on("needs_you") and not notify.on("goal_done")


def test_an_empty_notify_block_and_no_events_are_fine(web, home):
    code, _ = _save(web)
    assert code == 200
    code, _ = _save(web, topic="", events=[])
    assert code == 200


@pytest.mark.parametrize("block, words", [
    ({"server": "ntfy.sh"}, "notify.server must be an http:// or https:// address"),
    ({"server": "ftp://ntfy.sh"}, "notify.server must be an http:// or https:// address"),
    ({"server": 7}, "notify.server must be an http:// or https:// address"),
    ({"topic": "has space"}, "notify.topic must be up to 64 letters"),
    ({"topic": "x" * 65}, "notify.topic must be up to 64 letters"),
    ({"topic": 12}, "notify.topic must be up to 64 letters"),
    ({"events": "goal_done"}, "notify.events must be a list of goal_done, needs_you"),
    ({"events": ["goal_done", "lunch"]}, "notify.events: 'lunch' is not an event"),
])
def test_each_bad_shape_is_refused_in_words(web, home, block, words):
    before = (home / "routing.json").read_text()
    code, out = _save(web, **block)
    assert code == 400
    assert len(out["problems"]) == 1 and words in out["problems"][0]
    assert (home / "routing.json").read_text() == before


def test_notify_that_is_not_an_object_is_refused(web, home):
    got = _read(web, "routing")
    code, out = post(web + "/api/settings/routing", {"data": {**got["data"], "notify": "on"}})
    assert code == 400 and out["problems"] == ["notify must be an object"]


def test_a_bad_topic_is_never_quoted_back(web, home):
    code, out = _save(web, topic="secret topic!")
    assert code == 400 and "secret" not in json.dumps(out)


def test_the_notifications_section_and_its_script_are_served(web):
    code, js = get(f"{web}/ui/settingsnotify.js")
    assert code == 200
    for want in ("Notifications", 'type="password"', "data-notify", "goal_done", "needs_you",
                 "delete d.notify", "window.settingsNotify"):
        assert want in js
    _, page = get(web + "/")
    assert page.index("/ui/settingsnotify.js") < page.index("/ui/settings.js")
    _, main = get(f"{web}/ui/settings.js")
    assert "settingsNotify.form(d)" in main and "settingsNotify.edit(el, d)" in main
