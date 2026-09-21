# SPDX-License-Identifier: Apache-2.0
"""Any ComfyUI workflow is an image model once eki knows where the prompt goes."""
import json

import pytest

from eki import workflow
from eki.adapters.comfyui import flux2_graph, flux2_edit_graph


def test_the_flux2_graph_binds_itself():
    g = flux2_graph("a cat", 1024, 768, 4, 7, "x")
    b = workflow.infer(g)
    assert (b.prompt.node, b.prompt.input) == ("4", "text")
    assert b.negative is None                                   # zeroed conditioning, not a text
    assert (b.width.node, b.height.node) == ("9", "9")          # the latent; fill() sets the scheduler too
    assert [(s.node, s.input) for s in b.seed] == [("10", "noise_seed")]
    assert (b.steps.node, b.steps.input) == ("8", "steps")
    assert b.image is None and (b.output.node, b.output.input) == ("13", "filename_prefix")
    assert b.describe() == "prompt, size, seed, steps"


def test_the_checkpoint_graph_binds_positive_and_negative():
    g = workflow.sdxl_graph("juggernautXL.safetensors")
    b = workflow.infer(g)
    assert b.prompt.node == "2" and b.negative.node == "3"
    assert b.width.node == "4" and b.seed[0].node == "5" and b.steps.node == "5"
    assert "negative prompt" in b.describe()


def test_an_edit_graph_has_a_source_picture():
    b = workflow.infer(flux2_edit_graph("remove the hat", "src.png", 4, 1, "x"))
    assert b.image and b.image.node == "20"
    assert "source picture (edits)" in b.describe()


def test_fill_puts_the_request_into_the_slots():
    g = workflow.sdxl_graph("m.safetensors")
    b = workflow.infer(g)
    out = workflow.fill(g, b, prompt="a red door", negative="cartoon", width=768, height=1024,
                        seed=42, steps=12, prefix="eki_1")
    assert out["2"]["inputs"]["text"] == "a red door" and out["3"]["inputs"]["text"] == "cartoon"
    assert out["4"]["inputs"]["width"] == 768 and out["4"]["inputs"]["height"] == 1024
    assert out["5"]["inputs"]["seed"] == 42 and out["5"]["inputs"]["steps"] == 12
    assert out["7"]["inputs"]["filename_prefix"] == "eki_1"
    assert g["2"]["inputs"]["text"] == ""                       # the original is untouched


def test_problems_name_missing_nodes_and_files():
    g = workflow.sdxl_graph("nope.safetensors")
    info = {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["real.safetensors"]]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}]}}},
            "EmptyLatentImage": {"input": {"required": {}}}, "KSampler": {"input": {"required": {}}},
            "VAEDecode": {"input": {"required": {}}}, "SaveImage": {"input": {"required": {}}}}
    got = workflow.problems(g, info)
    assert got == ["node 1: CheckpointLoaderSimple has no ckpt_name called 'nope.safetensors'"]
    del info["KSampler"]
    assert any("no KSampler" in p for p in workflow.problems(g, info))
    assert workflow.files_available(info)["checkpoints"] == ["real.safetensors"]


def test_parse_refuses_the_editor_format():
    with pytest.raises(ValueError, match="Export \\(API\\)"):
        workflow.parse(json.dumps({"nodes": [], "links": []}))
    with pytest.raises(ValueError, match="not a ComfyUI"):
        workflow.parse(json.dumps({"a": 1}))
    g = workflow.parse(json.dumps(workflow.sdxl_graph("x")))
    assert g["1"]["class_type"] == "CheckpointLoaderSimple"


def test_bindings_round_trip_and_family():
    b = workflow.infer(workflow.sdxl_graph("x"))
    again = workflow.Bindings.from_dict(json.loads(json.dumps(b.as_dict())))
    assert again == b
    assert workflow.family_of("flux-2-klein-4b.safetensors") == "flux2-klein"
    assert workflow.family_of("juggernautXL_v9.safetensors") == "checkpoint"


def test_saved_where_eki_keeps_them(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "HOME", tmp_path)
    path = workflow.save("sdxl", workflow.sdxl_graph("x"))
    assert workflow.load(path)["1"]["inputs"]["ckpt_name"] == "x"
