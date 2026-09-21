# SPDX-License-Identifier: Apache-2.0
"""The image backend answers with a picture the app can show."""
import asyncio
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
