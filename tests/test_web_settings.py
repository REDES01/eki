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
    assert api_settings.check("routing", data) == ["row 'general': no provider named 'new'"]
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
