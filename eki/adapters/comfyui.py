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


@register("comfyui")
class ComfyBackend(Backend):
    def __init__(self, info, options: Dict[str, Any]):
        super().__init__(info, options)
        self.base = self.options.get("base_url", "http://127.0.0.1:8188").rstrip("/")
        self.output_dir = os.path.expanduser(
            self.options.get("output_dir", "~/flux/output"))
        self.width = int(self.options.get("width", 1024))
        self.height = int(self.options.get("height", 1024))
        self.steps = int(self.options.get("steps", 4))
        self.timeout = float(self.options.get("timeout_seconds", 300))
        self._client: Optional[httpx.AsyncClient] = None

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

    async def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if not prompt:
            raise BackendError("no prompt to draw")

        seed = int(kw.get("seed") or random.randint(0, 2**31 - 1))
        prefix = f"eki_{int(time.time())}"
        graph = flux2_graph(prompt, self.width, self.height, self.steps, seed, prefix)

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

        yield f"flux-2-klein · {self.width}×{self.height} · {self.steps} steps · seed {seed}\n"

        images = await self._await_result(pid)
        if not images:
            raise BackendError("ComfyUI finished without producing an image")
        # Markdown, with the file's real path: the app shows the picture, the
        # terminal shows where it is, and the transcript stays plain text
        alt = " ".join(prompt.split())[:80].replace("[", "(").replace("]", ")")
        for name in images:
            path = os.path.join(self.output_dir, name)
            yield f"\n![{alt}]({path.replace(' ', '%20')})\n"

    async def _await_result(self, pid: str) -> List[str]:
        """Poll the history until this prompt has outputs (or the clock runs out)."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            await asyncio.sleep(1.0)
            try:
                r = await self.client().get(f"{self.base}/history/{pid}")
                entry = r.json().get(pid)
            except (httpx.HTTPError, json.JSONDecodeError):
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
