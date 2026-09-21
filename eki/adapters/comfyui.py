# SPDX-License-Identifier: Apache-2.0
"""ComfyUI as a backend you can route to.

A picture instead of a paragraph: the same router that picks who answers a
question picks this one when the request needs an image out. The graph below is
FLUX.2-klein — 4 steps, cfg 1.0, ~10s, 7.4GB — which beats FLUX.1-schnell here
on every axis that matters.

Distilled models ignore CFG and get worse with more steps, so those numbers are
fixed rather than exposed as knobs.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .base import Backend, BackendError, Health, Message, register


def flux2_graph(prompt: str, width: int, height: int, steps: int,
                seed: int, prefix: str) -> Dict[str, Any]:
    """The FLUX.2-klein text-to-image graph, as ComfyUI wants it posted."""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "flux-2-klein-4b.safetensors",
                         "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_3_4b.safetensors",
                         "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["2", 0]}},
        # distilled: no real negative prompt, just a zeroed conditioning
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "CFGGuider",
              "inputs": {"model": ["1", 0], "positive": ["4", 0],
                         "negative": ["5", 0], "cfg": 1.0}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "8": {"class_type": "Flux2Scheduler",
              "inputs": {"steps": steps, "width": width, "height": height}},
        "9": {"class_type": "EmptyFlux2LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["10", 0], "guider": ["6", 0], "sampler": ["7", 0],
                          "sigmas": ["8", 0], "latent_image": ["9", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": prefix, "images": ["12", 0]}},
    }


def flux2_edit_graph(prompt: str, image: str, steps: int, seed: int,
                     prefix: str) -> Dict[str, Any]:
    """FLUX.2-klein told to change a picture it is shown, rather than draw one.

    The picture rides along as a reference latent on the conditioning; the
    latent being denoised is still empty, so this takes real instructions
    ("remove the hat") instead of img2img's blend. The source is brought to
    about a megapixel on a 64px grid — the grid is not optional — and the
    output takes its size from that, so an edit keeps the original's shape."""
    graph = flux2_graph(prompt, 1024, 1024, steps, seed, prefix)
    graph.update({
        "20": {"class_type": "LoadImage", "inputs": {"image": image}},
        "21": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"image": ["20", 0], "upscale_method": "lanczos",
                          "megapixels": 1.0, "resolution_steps": 64}},
        "22": {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["3", 0]}},
        "23": {"class_type": "ReferenceLatent",
               "inputs": {"conditioning": ["4", 0], "latent": ["22", 0]}},
        "24": {"class_type": "GetImageSize", "inputs": {"image": ["21", 0]}},
    })
    graph["5"]["inputs"]["conditioning"] = ["23", 0]
    graph["6"]["inputs"]["positive"] = ["23", 0]
    for node in ("8", "9"):
        graph[node]["inputs"]["width"] = ["24", 0]
        graph[node]["inputs"]["height"] = ["24", 1]
    return graph


@register("comfyui")
class ComfyBackend(Backend):
    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.base = self.options.get("base_url", "http://127.0.0.1:8188").rstrip("/")
        self.output_dir = os.path.expanduser(
            self.options.get("output_dir", "~/flux/output"))
        self.width = int(self.options.get("width", 1024))
        self.height = int(self.options.get("height", 1024))
        self.steps = int(self.options.get("steps", 0))
        self.timeout = float(self.options.get("timeout_seconds", 300))
        self._client: Optional[httpx.AsyncClient] = None
        # a workflow of the user's (see eki/workflow.py); without one, the
        # built-in FLUX.2-klein graph
        self.workflow_path = str(self.options.get("workflow") or "")
        self._graph: Optional[Dict[str, Any]] = None
        self._bindings = None
        self.title = str(self.options.get("title") or "")

    def _workflow(self):
        from .. import workflow
        if self._graph is None and self.workflow_path:
            self._graph = workflow.load(self.workflow_path)
            stored = self.options.get("bindings")
            self._bindings = workflow.Bindings.from_dict(stored) if stored else workflow.infer(self._graph)
        return self._graph, self._bindings

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def health(self) -> Health:
        try:
            r = await self.client().get(f"{self.base}/system_stats")
            r.raise_for_status()
        except Exception:                           # noqa: BLE001
            return Health(False, "ComfyUI not running on 8188")
        stats = r.json()
        name = (stats.get("devices") or [{}])[0].get("name", "")
        queued = await self.queue_depth()
        return Health(True, f"ComfyUI{(' · ' + name) if name else ''} · queue {queued}")

    async def queue_depth(self) -> int:
        try:
            r = await self.client().get(f"{self.base}/prompt")
            return int(r.json().get("exec_info", {}).get("queue_remaining", 0))
        except Exception:                           # noqa: BLE001
            return 0

    async def _upload(self, path: str) -> str:
        """Hand ComfyUI a picture to work from; returns the name LoadImage wants."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise BackendError(f"can't read the picture to change: {e}") from e
        name = "eki_src_" + os.path.basename(path)
        try:
            r = await self.client().post(
                f"{self.base}/upload/image",
                files={"image": (name, data, "image/png")}, data={"overwrite": "true"})
        except httpx.HTTPError as e:
            raise BackendError(f"ComfyUI unreachable: {e}") from e
        if r.status_code >= 400:
            raise BackendError(f"ComfyUI refused the picture: {r.text[:200]}")
        got = r.json()
        return os.path.join(got.get("subfolder") or "", got.get("name") or name)

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        """``prompt`` overrides the last user message (a "try again" is drawn
        from the request it repeats, not from those two words); ``edit`` is
        the path of a picture to change instead of starting from nothing."""
        prompt = kw.get("prompt") or next(
            (m.content for m in reversed(messages) if m.role == "user"), "")
        if not prompt:
            raise BackendError("no prompt to draw")

        seed = int(kw.get("seed") or random.randint(0, 2**31 - 1))
        prefix = f"eki_{int(time.time())}"
        source = kw.get("edit") or ""
        own, bindings = self._workflow()
        if own is not None:
            from .. import workflow
            if source and not bindings.image:
                raise BackendError(f"{self.title or 'this workflow'} has no LoadImage node, so it can't edit a picture")
            graph = workflow.fill(own, bindings, prompt=prompt, width=self.width, height=self.height,
                                  seed=seed, steps=self.steps,
                                  image=await self._upload(source) if source else "", prefix=prefix)
            what = self.title or "workflow"
        elif source:
            graph = flux2_edit_graph(prompt, await self._upload(source), self.steps or 4, seed, prefix)
            what = "flux-2-klein"
        else:
            graph = flux2_graph(prompt, self.width, self.height, self.steps or 4, seed, prefix)
            what = "flux-2-klein"

        try:
            r = await self.client().post(f"{self.base}/prompt",
                                         json={"prompt": graph})
        except httpx.HTTPError as e:
            raise BackendError(f"ComfyUI unreachable: {e}") from e
        if r.status_code >= 400:
            # ComfyUI validates the whole graph and says exactly what it hated
            raise BackendError(f"ComfyUI rejected the graph: {r.text[:300]}")
        pid = r.json().get("prompt_id")
        if not pid:
            raise BackendError("ComfyUI accepted nothing")

        steps = f" · {self.steps} steps" if self.steps else ""
        if source:
            yield f"{what} · editing {os.path.basename(source)}{steps} · seed {seed}\n"
        else:
            yield f"{what} · {self.width}×{self.height}{steps} · seed {seed}\n"

        images = await self._await_result(pid)
        if not images:
            raise BackendError("ComfyUI finished without producing an image")
        # Markdown, with the file's real path: the app shows the picture, the
        # terminal shows where it is, and the transcript stays plain text
        alt = " ".join(prompt.split())[:80].replace("[", "(").replace("]", ")")
        for name in images:
            path = await self._fetch(name)
            yield f"\n![{alt}]({path.replace(' ', '%20')})\n"

    async def _fetch(self, name: str) -> str:
        """The picture, from ComfyUI itself, into eki's own folder — so where
        ComfyUI writes its output is nobody's business. Falls back to the
        configured output folder when the download fails."""
        subfolder, filename = os.path.split(name)
        dest_dir = os.path.expanduser("~/.eki/images")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, filename)
        try:
            r = await self.client().get(f"{self.base}/view",
                                        params={"filename": filename, "subfolder": subfolder, "type": "output"})
            if r.status_code == 200 and r.content and r.headers.get("content-type", "").startswith("image/"):
                with open(dest, "wb") as f:
                    f.write(r.content)
                return dest
        except httpx.HTTPError:
            pass
        return os.path.join(self.output_dir, name)

    async def _await_result(self, pid: str) -> List[str]:
        """Poll the history until this prompt has outputs (or the clock runs out)."""
        deadline = time.time() + self.timeout
        unreachable = 0
        while time.time() < deadline:
            await asyncio.sleep(1.0)
            try:
                r = await self.client().get(f"{self.base}/history/{pid}")
                entry = r.json().get(pid)
                unreachable = 0
            except (httpx.HTTPError, json.JSONDecodeError):
                unreachable += 1
                if unreachable >= 10:               # gone, not busy
                    raise BackendError("ComfyUI stopped answering while drawing")
                continue
            if not entry:
                continue
            status = (entry.get("status") or {})
            if status.get("status_str") == "error":
                raise BackendError(self._why(status))
            names: List[str] = []
            for node in (entry.get("outputs") or {}).values():
                for image in node.get("images", []):
                    if image.get("filename") and image.get("type", "output") == "output":
                        names.append(os.path.join(image.get("subfolder") or "",
                                                  image["filename"]))
            if names:
                return names
            if status.get("completed"):
                return []
        raise BackendError("ComfyUI timed out")

    @staticmethod
    def _why(status: Dict[str, Any]) -> str:
        for kind, payload in status.get("messages", []):
            if kind == "execution_error":
                node = payload.get("node_type", "?")
                return f"{node}: {str(payload.get('exception_message'))[:200]}"
        return "ComfyUI reported an error"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
