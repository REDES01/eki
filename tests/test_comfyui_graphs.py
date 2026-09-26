"""The shipped ComfyUI graphs and how a row picks one."""
import json

import pytest

from eki.providers import comfyui_graphs as cg

LOADERS = {"UNETLoader": "unet_name", "UnetLoaderGGUF": "unet_name",
           "CheckpointLoaderSimple": "ckpt_name"}
MODEL_FILES = {"image": "flux-2-klein-4b.safetensors", "image-hq": "qwen-image-2.1-Q8_0.gguf",
               "image-anime": "NoobAI-XL-v1.1.safetensors", "image-edit": "flux-2-klein-4b.safetensors",
               "image-upscale": "flux-2-klein-4b.safetensors"}


def shipped(row):
    return json.loads((cg.FOLDER / cg.GRAPHS[row][0]).read_text())


@pytest.mark.parametrize("row", sorted(cg.GRAPHS))
def test_each_graph_is_json_with_sound_links(row):
    graph = shipped(row)
    assert any(n["class_type"] == "SaveImage" for n in graph.values())
    for nid, node in graph.items():
        for name, value in node["inputs"].items():
            if isinstance(value, list):
                assert len(value) == 2 and value[0] in graph, f"{nid}.{name} -> {value}"
                assert isinstance(value[1], int)


@pytest.mark.parametrize("row", sorted(cg.GRAPHS))
def test_each_graph_loads_its_model(row):
    names = [n["inputs"][LOADERS[n["class_type"]]] for n in shipped(row).values()
             if n["class_type"] in LOADERS]
    assert names == [MODEL_FILES[row]]


@pytest.mark.parametrize("row", ["image-edit", "image-upscale"])
def test_edit_and_upscale_load_the_source(row):
    loads = [n for n in shipped(row).values() if n["class_type"] == "LoadImage"]
    assert [n["inputs"]["image"] for n in loads] == ["{{image}}"]


def test_draft_is_the_old_default():
    old = cg.FOLDER.parent / "comfyui_default.json"
    if old.exists():
        assert shipped("image") == json.loads(old.read_text())


def test_kind_of():
    assert cg.kind_of("image-hq") == ("hq", "qwen-image-2.1-Q8")
    assert cg.kind_of("image-anime") == ("anime", "NoobAI-XL-v1.1")
    assert cg.kind_of(None) == ("draft", "flux-2-klein-4b")
    assert cg.kind_of("chat") == ("draft", "flux-2-klein-4b")


def test_graph_path_order(tmp_path):
    mine, old = tmp_path / "mine.json", tmp_path / "old.json"
    assert cg.graph_path("image-hq", {}) == cg.FOLDER / "hq.json"
    assert cg.graph_path("image-hq", {"graphs": {"image-hq": str(mine)}}) == mine
    home = cg.graph_path("image-anime", {"graphs": {"image-anime": "~/a.json"}})
    assert "~" not in str(home) and home.name == "a.json"
    # the old single workflow is the image graph, and only that
    assert cg.graph_path("image", {"workflow": str(old)}) == old
    assert cg.graph_path("image-edit", {"workflow": str(old)}) == cg.FOLDER / "edit.json"
    assert cg.graph_path("image", {"workflow": str(old), "graphs": {"image": str(mine)}}) == mine
    # a row eki doesn't know draws a draft
    assert cg.graph_path("nonsense", {}) == cg.FOLDER / "draft.json"
    assert cg.graph_path(None, {"workflow": str(old)}) == old
    assert cg.graph_path("nonsense", {"graphs": {"image": str(mine)}}) == mine


def test_fill_keeps_numbers():
    got = cg.fill({"a": "{{seed}}", "b": ["x {{prompt}} y", "{{width}}"], "c": 3},
                  {"{{seed}}": 42, "{{prompt}}": "fox", "{{width}}": 768})
    assert got == {"a": 42, "b": ["x fox y", 768], "c": 3}


def test_load_fills_the_rows_graph():
    graph = cg.load("image-edit", {}, {"{{prompt}}": "make it blue", "{{seed}}": 7,
                                       "{{width}}": 1024, "{{height}}": 1024,
                                       "{{image}}": "eki/eki_src_r1_a.png"})
    text = json.dumps(graph)
    assert "{{" not in text and "eki/eki_src_r1_a.png" in text
    assert graph["10"]["inputs"]["noise_seed"] == 7
