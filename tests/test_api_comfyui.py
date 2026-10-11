"""/api/comfyui/workflows: Settings' list of ComfyUI workflows, over a fake ComfyUI folder (no network)."""
import json

import pytest

from eki import apiroutes, models, store
from eki.providers import comfyui_workflows as wf

GRAPH = {"3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 9, "sampler_name": "euler",
                                                     "model": ["4", 0]}},
         "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "mine.safetensors"}},
         "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}", "clip": ["4", 1]}},
         "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}}}


@pytest.fixture
def comfy(tmp_path, monkeypatch):
    d = tmp_path / "ComfyUI"
    (d / "models" / "checkpoints").mkdir(parents=True)
    (d / "main.py").write_text("")
    (d / "models" / "checkpoints" / "mine.safetensors").write_text("")
    monkeypatch.setenv("EKI_COMFYUI_DIR", str(d))
    return d


def call(conn, method, path, body=None):
    fn, m = apiroutes.match(method, path)
    return fn(conn, m, {}, body or {})


def ids(got):
    return [w["id"] for w in got["workflows"]]


def test_lists_the_starting_workflows(conn, comfy):
    got = call(conn, "GET", "/api/comfyui/workflows")
    assert ids(got) == [w for w, _ in wf.STARTING]
    assert got["folder"] == str(wf.folder()) and got["comfyui_dir"] == str(comfy)
    assert got["workflows"][0]["label"] == "FLUX.2 klein — draft"
    json.dumps(got)


def test_add_a_file(conn, comfy, tmp_path):
    src = tmp_path / "portrait.json"
    src.write_text(json.dumps(GRAPH))
    got = call(conn, "POST", "/api/comfyui/workflows/add", {"path": str(src), "label": "Portrait"})
    assert got["added"] == ["portrait"] and got["skipped"] == []
    d = next(w for w in got["workflows"] if w["id"] == "portrait")
    assert d["label"] == "Portrait" and d["steps"] == 9 and d["sampler"] == "euler"
    assert [i["name"] for i in d["inputs"]] == ["seed", "prompt"]
    assert [(m["name"], m["found"]) for m in d["models"]] == [("mine.safetensors", True)]
    assert (wf.folder() / "portrait.json").is_file()


def test_import_a_folder_skips_ui_format_and_unreadable(conn, comfy, tmp_path):
    tools = tmp_path / "tools"
    (tools / "art").mkdir(parents=True)
    (tools / "card.json").write_text(json.dumps(GRAPH))
    (tools / "art" / "backdrop.json").write_text(json.dumps(GRAPH))
    (tools / "art" / "editor.json").write_text(json.dumps({"nodes": [], "links": []}))
    (tools / "broken.json").write_text("{nope")
    got = call(conn, "POST", "/api/comfyui/workflows/add", {"path": str(tools)})
    assert sorted(got["added"]) == ["backdrop", "card"]
    why = {s["path"].rsplit("/", 1)[1]: s["why"] for s in got["skipped"]}
    assert set(why) == {"editor.json", "broken.json"}
    assert "Save (API)" in why["editor.json"] and "can't read" in why["broken.json"]
    assert {"backdrop", "card"} <= set(ids(got))


def test_add_refuses_a_missing_path(conn, comfy, tmp_path):
    with pytest.raises(ValueError, match="neither"):
        call(conn, "POST", "/api/comfyui/workflows/add", {"path": str(tmp_path / "gone")})


def test_edit_reload_remove(conn, comfy):
    got = call(conn, "POST", "/api/comfyui/workflows/edit",
               {"id": "draft", "label": "Draft", "defaults": {"width": 768}})
    assert got["label"] == "Draft"
    assert next(i for i in got["inputs"] if i["name"] == "width")["default"] == 768
    (wf.folder() / "draft.json").write_text("{nope")
    got = call(conn, "POST", "/api/comfyui/workflows/reload", {"id": "draft"})
    assert got["error"].startswith("can't read") and got["inputs"] == []
    got = call(conn, "POST", "/api/comfyui/workflows/remove", {"id": "draft"})
    assert "draft" not in ids(got)
    assert (wf.folder() / "removed" / "draft.json").is_file()
    with pytest.raises(ValueError, match="no ComfyUI workflow"):
        call(conn, "POST", "/api/comfyui/workflows/reload", {"id": "draft"})


def test_open_copies_the_graph_and_starts_comfyui(conn, comfy, monkeypatch):
    started = []
    monkeypatch.setattr(models, "ensure", lambda name, timeout=180: started.append(name) or True)
    got = call(conn, "POST", "/api/comfyui/workflows/open", {"id": "hq"})
    dest = comfy / "user" / "default" / "workflows" / "eki-hq.json"
    assert got == {"url": "http://127.0.0.1:8188/", "copied": str(dest)}
    assert started == ["comfyui"]
    assert json.loads(dest.read_text()) == json.loads((wf.folder() / "hq.json").read_text())


def test_open_without_comfyui_says_why(conn, monkeypatch):
    monkeypatch.setattr(models, "ensure", lambda name, timeout=180: pytest.fail("must not start"))
    with pytest.raises(ValueError, match="no ComfyUI provider"):
        call(conn, "POST", "/api/comfyui/workflows/open", {"id": "draft"})


def test_try_creates_the_pinned_run(conn, comfy):
    got = call(conn, "POST", "/api/comfyui/workflows/try", {"id": "draft", "inputs": {"prompt": "a fox", "seed": 7}})
    r = store.run(conn, got["run"])
    assert r["thread_id"] == got["thread"] and r["pinned"] == 1
    assert (r["provider"], r["model"]) == ("comfyui", "draft")
    assert json.loads(r["prompt"]) == {"inputs": {"prompt": "a fox", "seed": 7}}
    assert store.thread(conn, got["thread"])["title"] == "try FLUX.2 klein — draft"


def test_try_refuses_an_unknown_input(conn, comfy):
    with pytest.raises(ValueError, match="no input colour"):
        call(conn, "POST", "/api/comfyui/workflows/try", {"id": "draft", "inputs": {"colour": "red"}})
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
