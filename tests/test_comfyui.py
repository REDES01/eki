# SPDX-License-Identifier: Apache-2.0
"""The image backend answers with a picture the app can show."""
import asyncio
import json
import re

import httpx

from eki.adapters.base import BackendInfo, Capabilities, Cost, Message
from eki.adapters.comfyui import ComfyBackend


def make(handler, **options):
    info = BackendInfo(key="flux", kind="comfyui", label="flux",
                       capabilities=Capabilities(text=False, images_out=True), cost=Cost())
    b = ComfyBackend(info, {"output_dir": "/pics/out", **options})
    b._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return b


def collect(backend, prompt):
    async def go():
        return [c async for c in backend.stream([Message("user", prompt)])]
    return asyncio.run(go())


def test_the_answer_is_markdown_pointing_at_the_file(monkeypatch):
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_wait)       # the poll interval
    posted = {}

    def handler(request):
        if request.method == "POST":
            posted["graph"] = request.read()
            return httpx.Response(200, json={"prompt_id": "p1"})
        return httpx.Response(200, json={"p1": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"13": {"images": [
                {"filename": "eki_1_00001_.png", "subfolder": "", "type": "output"},
                {"filename": "tmp.png", "subfolder": "", "type": "temp"}]}}}})

    chunks = collect(make(handler), "a [brass] compass")
    text = "".join(chunks)
    assert b"a [brass] compass" in posted["graph"]
    found = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", text)
    assert found == [("a (brass) compass", "/pics/out/eki_1_00001_.png")]


def test_an_edit_uploads_the_picture_and_draws_from_it(monkeypatch, tmp_path):
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    source = tmp_path / "eki_1_00001_.png"
    source.write_bytes(b"\x89PNG fake")
    seen = {}

    def handler(request):
        if request.url.path == "/upload/image":
            seen["upload"] = request.read()
            return httpx.Response(200, json={"name": "eki_src_eki_1_00001_.png",
                                             "subfolder": "", "type": "input"})
        if request.method == "POST":
            seen["graph"] = json.loads(request.read())["prompt"]
            return httpx.Response(200, json={"prompt_id": "p2"})
        return httpx.Response(200, json={"p2": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"13": {"images": [
                {"filename": "eki_2_00001_.png", "subfolder": "", "type": "output"}]}}}})

    backend = make(handler)

    async def go():
        return [c async for c in backend.stream([Message("user", "make it bluer")],
                                                edit=str(source))]
    text = "".join(asyncio.run(go()))
    graph = seen["graph"]
    assert b"PNG fake" in seen["upload"]
    assert graph["20"]["inputs"]["image"] == "eki_src_eki_1_00001_.png"
    assert graph["4"]["inputs"]["text"] == "make it bluer"
    # the reference rides on the conditioning, both sides of the guider
    assert graph["6"]["inputs"]["positive"] == ["23", 0]
    assert graph["5"]["inputs"]["conditioning"] == ["23", 0]
    assert graph["21"]["inputs"]["resolution_steps"] == 64
    assert "editing eki_1_00001_.png" in text and "eki_2_00001_.png" in text


def test_a_prompt_handed_over_wins_over_the_last_message(monkeypatch):
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    seen = {}

    def handler(request):
        if request.method == "POST":
            seen["graph"] = json.loads(request.read())["prompt"]
            return httpx.Response(200, json={"prompt_id": "p3"})
        return httpx.Response(200, json={"p3": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"13": {"images": [
                {"filename": "x.png", "subfolder": "", "type": "output"}]}}}})

    backend = make(handler)

    async def go():
        return [c async for c in backend.stream([Message("user", "try again")],
                                                prompt="a red fox in snow")]
    asyncio.run(go())
    assert seen["graph"]["4"]["inputs"]["text"] == "a red fox in snow"
    assert "20" not in seen["graph"]
