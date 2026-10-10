import json
import os

import pytest

from eki import paths, providers
from eki.routing import table
from test_web import get, post, web  # noqa: F401  (the fixture)


def _read(web, name):
    code, body = get(f"{web}/api/settings/{name}")
    assert code == 200
    return json.loads(body)


def test_read_routing_gives_the_file_and_the_added_picture_rows(web, home):
    got = _read(web, "routing")
    assert got["data"] == json.loads((home / "routing.json").read_text())
    assert got["path"] == str(home / "routing.json")
    assert got["mtime"] == os.stat(home / "routing.json").st_mtime
    rows = {r["key"]: r for r in got["effective"]["rows"]}
    assert "added" not in rows["general"] and "added" not in rows["code"]
    assert all(rows[k]["added"] is True for k in table.PICTURE_ROWS)


def test_read_providers_marks_the_comfyui_default_and_command(web, home, tmp_path, monkeypatch):
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()
    (comfy / "main.py").write_text("")
    monkeypatch.setenv("EKI_COMFYUI_DIR", str(comfy))
    got = _read(web, "providers")
    assert got["data"] == json.loads((home / "providers.json").read_text())
    assert "comfyui" not in got["data"]
    e = got["effective"]["entries"]
    assert e["comfyui"]["unwritten"] is True and e["comfyui"]["can_effective"] == ["image", "image-edit"]
    assert e["command"]["builtin"] is True and e["fake"]["builtin"] is False
    assert e["fake"]["unwritten"] is False and e["bare"]["can_effective"] == ["text"]


def test_a_valid_save_writes_keeps_a_backup_and_is_seen_next(web, home):
    old = (home / "routing.json").read_text()
    got = _read(web, "routing")
    data = {**got["data"], "rows": got["data"]["rows"] + [
        {"key": "draw", "title": "pictures", "needs": ["text"], "targets": ["fake2"]}],
        "self": {"autonomy": "propose"}}
    code, out = post(web + "/api/settings/routing", {"data": data, "mtime": got["mtime"]})
    assert code == 200 and out["ok"] is True
    assert (home / "routing.json.bak").read_text() == old
    assert (home / "routing.json").read_text().endswith("}\n")
    assert any(r["key"] == "draw" and r["targets"] == ["fake2"] for r in table.rows())
    assert out["mtime"] == os.stat(home / "routing.json").st_mtime
    data["self"] = {"autonomy": "apply"}   # the next save carries the mtime the last one gave, no read between
    code, out = post(web + "/api/settings/routing", {"data": data, "mtime": out["mtime"]})
    assert code == 200 and json.loads((home / "routing.json").read_text())["self"] == {"autonomy": "apply"}

    p = _read(web, "providers")
    pdata = {**p["data"], "third": {"kind": "fake", "can": ["text"], "label": "Third"}}
    code, out = post(web + "/api/settings/providers", {"data": pdata, "mtime": p["mtime"]})
    assert code == 200 and out["backup"] == str(home / "providers.json.bak")
    assert providers.config()["third"]["label"] == "Third"


def test_bad_data_is_refused_with_every_problem_and_the_file_untouched(web, home):
    before = (home / "routing.json").read_text()
    rows = [{"key": "code", "targets": ["nobody"]},
            {"key": "code", "needs": ["telepathy"], "targets": ["fake"]}]
    code, out = post(web + "/api/settings/routing", {"data": {"rows": rows}})
    assert code == 400
    text = "\n".join(out["problems"])
    assert "'nobody'" in text and "'telepathy'" in text and "general" in text and "twice" in text
    assert len(out["problems"]) == 4 and out["error"]
    assert (home / "routing.json").read_text() == before and not (home / "routing.json.bak").exists()

    before = (home / "providers.json").read_text()
    data = {"x": {"kind": "wizard"}, "command": {"kind": "command"}, "y": {"kind": "command"},
            "z": {"kind": "fake", "can": ["flying"]}}
    code, out = post(web + "/api/settings/providers", {"data": data})
    assert code == 400
    text = "\n".join(out["problems"])
    assert "'wizard'" in text and "'command' is built in" in text and "'y'" in text and "'flying'" in text
    assert (home / "providers.json").read_text() == before


def test_routing_may_name_a_provider_being_saved_alongside():
    from eki import api_settings
    data = {"rows": [{"key": "general", "targets": ["new"]}]}
    assert api_settings.check("routing", data) == ["row 'general': unknown provider 'new' in target 'new'"]
    assert api_settings.check("routing", data, {"new": {"kind": "fake"}}) == []


def test_a_stale_mtime_is_refused(web, home):
    got = _read(web, "routing")
    code, out = post(web + "/api/settings/routing", {"data": got["data"], "mtime": got["mtime"] - 10})
    assert code == 409 and "changed on disk" in out["error"]


def test_no_header_is_forbidden_and_an_unknown_name_is_not_found(web, home):
    code, _ = post(web + "/api/settings/routing", {"data": {}}, header=False)
    assert code == 403
    code, _ = post(web + "/api/settings/mcp", {"data": {}})
    assert code == 404
    import urllib.error
    with pytest.raises(urllib.error.HTTPError) as e:
        get(web + "/api/settings/secrets")
    assert e.value.code == 404
    assert paths.config("secrets").exists() is False


def test_routing_targets_roles_and_provider_tiers_are_checked(home):
    from eki import api_settings
    rows = [{"key": "general", "targets": ["fake:fast", "fake2:some-model"]}]
    assert api_settings.check("routing", {"rows": rows, "roles": {"router": "fake:fast"}}) == []
    bad = api_settings.check("routing", {"rows": [{"key": "general", "targets": ["nope:fast", "fake:"]}],
                                         "roles": {"planner": "off", "boss": "fake"}})
    text = "\n".join(bad)
    assert "unknown provider 'nope' in target 'nope:fast'" in text
    assert "nothing after the colon" in text and "planner can't be off" in text and "'boss'" in text
    ok = {"claude": {"kind": "claude_code", "models": {"fast": "claude-haiku-4-5", "top": "claude-opus-4-1"}}}
    assert api_settings.check("providers", ok) == []
    got = api_settings.check("providers", {"claude": {"kind": "claude_code",
                                                      "models": {"quick": "x", "top": 4}}})
    assert got == ["'claude': models: 'quick' is not a tier (one of fast, default, top)",
                   "'claude': models.top must be a model name"]


def test_object_targets_in_rows_and_roles_save_and_a_bad_one_is_refused(web, home):
    got = _read(web, "routing")
    rows = [dict(r) for r in got["data"]["rows"]]
    general = next(r for r in rows if r["key"] == "general")
    general["targets"] = [{"provider": "fake", "model": "m1", "effort": "high"}, "fake:m2", "fake2",
                          {"provider": "fake", "effort": "low"}, {"provider": "fake", "model": "fast"}]
    roles = {"planner": {"provider": "fake", "model": "m1", "effort": "high"}, "reviewer": "off"}
    data = {**got["data"], "rows": rows, "roles": roles}
    code, out = post(web + "/api/settings/routing", {"data": data, "mtime": got["mtime"]})
    assert code == 200 and out["ok"] is True
    again = _read(web, "routing")
    assert again["data"]["roles"]["planner"] == roles["planner"]
    assert next(r for r in again["data"]["rows"] if r["key"] == "general")["targets"] == general["targets"]

    before = (home / "routing.json").read_text()
    general["targets"] = [{"provider": "fake", "effort": 3}, {"provider": "fake", "speed": "x"},
                          {"provider": ""}, {"provider": "nope"}, 7]
    bad = {**data, "rows": rows, "roles": {"router": {"provider": "fake", "model": ["m"]}}}
    code, out = post(web + "/api/settings/routing", {"data": bad, "mtime": again["mtime"]})
    assert code == 400
    problems = "\n".join(out["problems"])
    for want in ("effort 3", "unknown keys: speed", "names no provider", "unknown provider 'nope'",
                 "7 is not text or an object", "roles.router: model ['m']"):
        assert want in problems
    assert (home / "routing.json").read_text() == before
