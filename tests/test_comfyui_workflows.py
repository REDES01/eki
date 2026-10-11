"""The ComfyUI workflow library: the list under the provider, read from each graph."""
import json

import pytest

from eki.providers import comfyui_read as rd
from eki.providers import comfyui_workflows as wf

LOADERS = {"UNETLoader": "unet_name", "UnetLoaderGGUF": "unet_name", "CheckpointLoaderSimple": "ckpt_name"}
MAIN = {"draft": "flux-2-klein-4b.safetensors", "hq": "qwen-image-2.1-Q8_0.gguf",
        "anime": "NoobAI-XL-v1.1.safetensors", "edit": "flux-2-klein-4b.safetensors",
        "upscale": "flux-2-klein-4b.safetensors"}


def write(tmp_path, name, graph):
    p = tmp_path / name
    p.write_text(json.dumps(graph))
    return p


def test_ensure_seeds_the_five_and_never_overwrites(home):
    wf.ensure()
    got = json.loads(wf.index_path().read_text())["workflows"]
    assert [(e["id"], e["label"]) for e in got] == wf.STARTING
    assert sorted(p.name for p in wf.folder().glob("*.json")) == sorted(f"{w}.json" for w, _ in wf.STARTING)
    (wf.folder() / "draft.json").write_text("{}")
    wf.index_path().unlink()
    wf.ensure()
    assert (wf.folder() / "draft.json").read_text() == "{}"


@pytest.mark.parametrize("wid", sorted(MAIN))
def test_each_shipped_graph_has_sound_links_and_its_model(wid):
    graph = json.loads((wf.SHIPPED / f"{wid}.json").read_text())
    assert any(n["class_type"] == "SaveImage" for n in graph.values())
    for nid, node in graph.items():
        for name, value in node["inputs"].items():
            if isinstance(value, list):
                assert len(value) == 2 and value[0] in graph and isinstance(value[1], int), f"{nid}.{name}"
    assert [n["inputs"][LOADERS[n["class_type"]]] for n in graph.values() if n["class_type"] in LOADERS] == [MAIN[wid]]


def test_describe_reads_the_draft():
    d = wf.describe("draft")
    assert d["label"] == "FLUX.2 klein — draft" and d["error"] == ""
    assert [(i["name"], i["source"]) for i in d["inputs"]] == [
        ("prompt", "placeholder"), ("width", "placeholder"), ("height", "placeholder"), ("seed", "placeholder")]
    defaults = {i["name"]: i["default"] for i in d["inputs"]}
    assert defaults == {"prompt": "", "width": 1024, "height": 1024, "seed": "random"}
    assert d["sampler"] == "euler" and d["steps"] == 4 and (d["width"], d["height"]) == (1024, 1024)
    assert {m["name"] for m in d["models"]} == {"flux-2-klein-4b.safetensors", "qwen_3_4b.safetensors",
                                                 "flux2-vae.safetensors"}
    assert all(m["found"] is None for m in d["models"])           # no ComfyUI here
    assert not d["needs_source"] and wf.describe("edit")["needs_source"]


def test_models_found_in_comfyuis_folders(tmp_path, monkeypatch):
    root = tmp_path / "ComfyUI"
    (root / "models" / "diffusion_models").mkdir(parents=True)
    (root / "main.py").write_text("")
    (root / "models" / "diffusion_models" / "flux-2-klein-4b.safetensors").write_text("")
    monkeypatch.setenv("EKI_COMFYUI_DIR", str(root))
    found = {m["name"]: m["found"] for m in wf.describe("draft")["models"]}
    assert found["flux-2-klein-4b.safetensors"] is True and found["flux2-vae.safetensors"] is False


def test_declared_inputs_and_defaults(tmp_path):
    wid = wf.add(str(write(tmp_path, "Portrait Kit.json", {
        "1": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "sampler_name": "dpmpp_2m"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "portrait of {{prompt}}"}},
        "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216}}})))
    assert wid == "portrait-kit"
    d = wf.edit(wid, label="Portraits", inputs={"steps": {"node": "1", "field": "steps"},
                                                "seed": {"node": "1", "field": "seed"}},
                defaults={"steps": 12})
    assert d["label"] == "Portraits" and (d["width"], d["height"]) == (832, 1216) and d["steps"] == 20
    got = {i["name"]: (i["source"], i["default"]) for i in d["inputs"]}
    assert got == {"prompt": ("placeholder", ""), "steps": ("declared", 12), "seed": ("declared", "random")}
    graph = wf.build(wid, {"prompt": "an old sailor"})
    assert graph["2"]["inputs"]["text"] == "portrait of an old sailor"
    assert graph["1"]["inputs"]["steps"] == 12 and isinstance(graph["1"]["inputs"]["seed"], int)
    assert graph["3"]["inputs"]["width"] == 832                   # not an input: the graph's own
    assert wf.build(wid, {"prompt": "x", "seed": "5", "steps": 30})["1"]["inputs"] == {
        "seed": 5, "steps": 30, "sampler_name": "dpmpp_2m"}
    with pytest.raises(ValueError, match="width"):
        wf.edit(wid, inputs={"width": {"node": "9", "field": "width"}})


def test_build_refuses_unknown_inputs_and_keeps_numbers():
    with pytest.raises(ValueError) as e:
        wf.build("draft", {"prompt": "fox", "cfg": 2})
    assert "cfg" in str(e.value) and "prompt" in str(e.value) and "seed" in str(e.value)
    graph = wf.build("draft", {"prompt": "fox", "width": "768", "seed": 3})
    assert graph["9"]["inputs"] == {"width": 768, "height": 1024, "batch_size": 1}
    assert graph["10"]["inputs"]["noise_seed"] == 3 and "{{" not in json.dumps(graph)


def test_fill_keeps_numbers():
    assert rd.fill({"a": "{{seed}}", "b": ["x {{prompt}} y", "{{ width }}"], "c": 3, "d": "{{other}}"},
                   {"seed": 42, "prompt": "fox", "width": 768}) == {
        "a": 42, "b": ["x fox y", 768], "c": 3, "d": "{{other}}"}


def test_add_refuses_the_editor_format(tmp_path):
    ui = write(tmp_path, "ui.json", {"nodes": [{"id": 1, "type": "KSampler"}], "links": []})
    with pytest.raises(ValueError, match=r"Save \(API\)"):
        wf.add(str(ui))
    with pytest.raises(ValueError, match=r"Save \(API\)"):
        wf.add(str(write(tmp_path, "x.json", {"a": 1})))
    graph = {"1": {"class_type": "X", "inputs": {}}}
    assert wf.add(str(write(tmp_path, "draft.json", graph))) == "draft-2"   # made unique
    assert [e["id"] for e in wf.entries()][-1] == "draft-2"


def test_remove_moves_the_file():
    wf.remove("anime")
    assert "anime" not in [e["id"] for e in wf.entries()]
    assert not (wf.folder() / "anime.json").exists() and (wf.folder() / "removed" / "anime.json").exists()
    with pytest.raises(KeyError):
        wf.entry("anime")
    assert wf.for_row("image-anime") == "draft"                    # its row draws a draft now


def test_a_missing_file_is_an_error_not_a_crash():
    wf.ensure()
    (wf.folder() / "hq.json").unlink()
    d = wf.describe("hq")
    assert "can't read" in d["error"] and d["inputs"] == []
    assert [r["description"] for r in wf.listed() if r["model"] == "hq"] == [d["error"]]


def test_listed_rows_are_like_a_model_list():
    rows = wf.listed()
    assert [r["model"] for r in rows] == [w for w, _ in wf.STARTING]
    draft = rows[0]
    assert draft == {"model": "draft", "label": "FLUX.2 klein — draft",
                     "description": "flux-2-klein-4b · euler · 4 steps · 1024×1024", "resolved": None,
                     "efforts": None, "default_effort": None, "is_default": True, "floating": False}
    assert rows[1]["description"] == "qwen-image-2.1-Q8_0 · euler · 30 steps · 1024×1024"


def test_for_row():
    assert wf.for_row("image-hq") == "hq" and wf.for_row("image") == "draft"
    assert wf.for_row(None) == "draft" and wf.for_row("chat") == "draft"


def test_targets_choose_against_the_workflows():
    from eki import targets
    rows = wf.listed()
    assert targets.choose("comfyui:hq", {}, rows).model == "hq"
    c = targets.choose("comfyui:gone", {}, rows)
    assert c.model is None and "gone isn't in comfyui's list" in c.note
    assert targets.label("comfyui", "anime", None, rows) == "comfyui · NoobAI-XL — anime"
