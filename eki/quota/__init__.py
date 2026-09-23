# SPDX-License-Identifier: Apache-2.0
"""Usage limits, inside the engine.

What used to be tokenbar — a second server on :8777 that the router asked over
HTTP — is now a board the engine keeps in memory. The router and the menu bar
read the same numbers, so they can't disagree.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

from .base import QuotaProvider, Reading, Window, label_for  # noqa: F401
from .pace import ProviderPace, credits_left, provider_pace

log = logging.getLogger("eki.quota")


class QuotaBoard:
    def __init__(self, providers: List[QuotaProvider], ceiling: float = 0.99,
                 poll_seconds: float = 15.0):
        self.providers = {p.key: p for p in providers}
        self.ceiling = ceiling
        self.poll_seconds = poll_seconds
        self.latest: Dict[str, Reading] = {}
        # {provider: (until, why)}: a limit a program hit mid-run, held until
        # the readings can say so themselves (they're polled, and cached)
        self.walls: Dict[str, tuple] = {}
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

    def pace(self) -> Dict[str, "ProviderPace"]:
        """{provider: how fast its windows are being spent} — see quota.pace."""
        return {key: provider_pace(reading, ceiling=self.ceiling)
                for key, reading in self.latest.items()}

    def exhausted(self) -> Dict[str, str]:
        """{provider: why} for providers that can't take a request now.

        A spent window is a wall only when nothing pays past it: with credits
        left the provider stays in, at the dearest pace (see quota.pace), and
        a credit meter running high on its own means money, not a wall.
        """
        out: Dict[str, str] = {}
        now = time.time()
        for key, (until, why) in list(self.walls.items()):
            if until > now:
                out[key] = why
            else:
                self.walls.pop(key, None)
        for key, reading in self.latest.items():
            credits = credits_left(reading)
            for w in reading.windows:
                if w.kind == "window" and w.used >= self.ceiling and not w.primary:
                    continue                        # one model's window: priced, not a wall
                if w.kind == "window" and w.used >= self.ceiling:
                    if credits:
                        continue
                    why = f"{w.label} at {round(w.used * 100)}%"
                    if credits is not None:
                        why += ", credits spent too"
                    out[key] = why
        return out

    def mark_exhausted(self, key: str, why: str = "hit its usage limit",
                       seconds: float = 900.0) -> None:
        """A program said it's out, mid-run: out for a while, whatever the
        last reading said."""
        self.walls[key] = (time.time() + max(60.0, seconds), why)
