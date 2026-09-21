# SPDX-License-Identifier: Apache-2.0
"""Usage limits, inside the engine.

What used to be tokenbar — a second server on :8777 that the router asked over
HTTP — is now a board the engine keeps in memory. The router and the menu bar
read the same numbers, so they can't disagree.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from .base import QuotaProvider, Reading, Window, label_for  # noqa: F401

log = logging.getLogger("eki.quota")


class QuotaBoard:
    def __init__(self, providers: List[QuotaProvider], ceiling: float = 0.99,
                 poll_seconds: float = 15.0):
        self.providers = {p.key: p for p in providers}
        self.ceiling = ceiling
        self.poll_seconds = poll_seconds
        self.latest: Dict[str, Reading] = {}
        self._task: Optional[asyncio.Task] = None

    async def refresh(self, force: bool = False) -> Dict[str, Reading]:
        results = await asyncio.gather(
            *(p.read(force=force) for p in self.providers.values()),
            return_exceptions=True)
        for provider, result in zip(self.providers.values(), results):
            if isinstance(result, Reading):
                self.latest[provider.key] = result
            else:
                log.warning("quota %s failed: %s", provider.key, result)
        return self.latest

    def start(self) -> None:
        async def loop() -> None:
            while True:
                try:
                    await self.refresh()
                except Exception as e:              # noqa: BLE001
                    log.warning("quota refresh failed: %s", e)
                await asyncio.sleep(self.poll_seconds)
        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def exhausted(self) -> Dict[str, str]:
        """{provider: why} for providers with a spent window.

        Only real windows count. A credit or spend meter running high means
        money, not a wall, and the router shouldn't treat it as one.
        """
        out: Dict[str, str] = {}
        for key, reading in self.latest.items():
            for w in reading.windows:
                if w.kind == "window" and w.used >= self.ceiling:
                    out[key] = f"{w.label} at {round(w.used * 100)}%"
        return out
