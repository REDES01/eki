# SPDX-License-Identifier: Apache-2.0
"""Several pictures at once, at the size asked for."""
import asyncio
import json

import httpx
import pytest

from eki import workflow
from eki.adapters.base import Message
from eki.adapters.comfyui import fit_size
from tests.test_comfyui import make


def comfy(posted, per_prompt=1):
    """A ComfyUI that accepts every graph and, asked, says each one drew
    ``per_prompt`` pictures (or as many as its batch_size said)."""
    def handler(request):
        if request.method == "POST":
            posted.append(json.loads(request.read())["prompt"])
            return httpx.Response(200, json={"prompt_id": f"p{len(posted)}"})
        if request.url.path == "/view":
            return httpx.Response(404)              # no file to hand over: the path is answered instead
        pid = request.url.path.rsplit("/", 1)[-1]
        graph = posted[int(pid[1:]) - 1]
        n = max([v["inputs"]["batch_size"] for v in graph.values()
                 if isinstance(v["inputs"].get("batch_size"), int)] or [per_prompt])
        return httpx.Response(200, json={pid: {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"13": {"images": [
                {"filename": f"eki_{pid}_{i:05}_.png", "subfolder": "", "type": "output"}
                for i in range(1, n + 1)]}}}})
    return handler


def draw(backend, prompt="a fox", **kw):
    async def go():
        return [c async for c in backend.stream([Message("user", prompt)], **kw)]
    return "".join(asyncio.run(go()))


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    async def none(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", none)


def test_four_at_a_size_is_one_graph_and_four_pictures():
    posted = []
    text = draw(make(comfy(posted)), width=1536, height=1024, batch=4)
    assert len(posted) == 1
    latent, scheduler = posted[0]["9"]["inputs"], posted[0]["8"]["inputs"]
    assert (latent["width"], latent["height"], latent["batch_size"]) == (1536, 1024, 4)
    assert (scheduler["width"], scheduler["height"]) == (1536, 1024)
    assert "4 × 1536×1024" in text
    assert text.count("![a fox](") == 4


def test_nothing_said_draws_what_the_provider_usually_draws():
    posted = []
    text = draw(make(comfy(posted), width=768, height=1152, batch=2))
    assert posted[0]["9"]["inputs"] == {"width": 768, "height": 1152, "batch_size": 2}
    assert "2 × 768×1152" in text
    # and one picture is announced the way it always was
    assert "flux-2-klein · 1024×1024 · seed" in draw(make(comfy([])))


def test_a_shape_is_cut_from_the_pixels_the_model_usually_draws():
    posted = []
    draw(make(comfy(posted)), aspect=16 / 9)
    w, h = posted[0]["9"]["inputs"]["width"], posted[0]["9"]["inputs"]["height"]
    assert (w, h) == (1376, 768)                    # 16:9, about a megapixel, on the grid
    assert w % 32 == 0 and h % 32 == 0


def test_a_size_is_brought_to_what_a_model_will_take():
    assert fit_size(1920, 1080, 2048 * 2048) == (1920, 1088)        # onto the grid
    assert fit_size(3840, 2160, 2048 * 2048) == (2720, 1536)        # too many pixels: same shape, fewer
    assert fit_size(100, 100, 2048 * 2048) == (256, 256)
    w, h = fit_size(4000, 4000, 1024 * 1024)
    assert w * h <= 1024 * 1024


def test_the_count_stops_at_the_providers_limit():
    posted = []
    text = draw(make(comfy(posted), max_batch=3), batch=50)
    assert posted[0]["9"]["inputs"]["batch_size"] == 3
    assert text.count("![") == 3


def test_a_workflow_with_nowhere_to_put_a_count_is_queued_that_many_times(tmp_path):
    # it starts from a loaded latent: there is no batch_size to set
    graph = workflow.sdxl_graph("x.safetensors")
    del graph["4"]["inputs"]["batch_size"]
    path = tmp_path / "w.json"
    path.write_text(json.dumps(graph))
    posted = []
    text = draw(make(comfy(posted), workflow=str(path), title="mine"), batch=3, seed=100)
    assert len(posted) == 3
    assert [g["5"]["inputs"]["seed"] for g in posted] == [100, 101, 102]    # or they'd be one picture, thrice
    assert "mine · 3 × 1024×1024" in text and text.count("![") == 3


def test_a_workflow_of_the_users_takes_the_count_without_being_bound_again(tmp_path):
    # bindings stored before eki drew in batches know nothing of batch_size
    graph = workflow.sdxl_graph("x.safetensors")
    path = tmp_path / "w.json"
    path.write_text(json.dumps(graph))
    old = workflow.infer(graph).as_dict()
    posted = []
    draw(make(comfy(posted), workflow=str(path), bindings=old), batch=4, width=832, height=1216)
    assert len(posted) == 1
    assert posted[0]["4"]["inputs"] == {"width": 832, "height": 1216, "batch_size": 4}
