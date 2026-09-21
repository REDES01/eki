# SPDX-License-Identifier: Apache-2.0
"""Pick a backend for a request, and say why.

Filters in order: your policy, whether it is actually running, hard
requirements, live quota, then cost and declared order.
Every decision carries its reasoning — automatic selection you can't inspect is
worse than none, and the first question of any surprising answer is "which
model was that?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import priors
from .adapters.base import Backend
from .policy import Policy


@dataclass
class Need:
    """What a request demands of a backend."""

    repo: bool = False           # must be able to edit files where it runs
    tools: bool = False
    vision: bool = False
    images_out: bool = False
    context_tokens: int = 0
    backend: Optional[str] = None       # explicit override, wins over everything
    #: what the request is and how demanding it is, from eki.classify
    task: str = ""
    difficulty: str = ""


@dataclass
class Choice:
    backend: Optional[Backend]
    reason: str
    rejected: List[str] = field(default_factory=list)


class QuotaSource:
    """What the router needs from quota: which providers have a spent window.

    The engine's QuotaBoard is the real one. A backend with no headroom is
    skipped rather than attempted and failed — the point of tracking quota is
    to stop discovering it the hard way. Unknown quota is never held against
    a backend.
    """

    def __init__(self, ceiling: float = 0.99):
        self.ceiling = ceiling

    def exhausted(self) -> Dict[str, str]:
        """{provider key: why}."""
        return {}


class Router:
    def __init__(self, backends: List[Backend], quota: Optional[QuotaSource] = None,
                 policy: Optional["Policy"] = None,
                 is_up: Optional[Callable[[str], Optional[bool]]] = None,
                 reserved: Optional[set] = None):
        self.backends = backends
        self.quota = quota
        #: current preferences; replaced wholesale when the user edits them
        self.policy = policy or Policy()
        #: True / False / None for "don't know", which is never held against it
        self.is_up = is_up or (lambda key: None)
        #: backends kept out of automatic routing because they have another
        #: job — the router model itself. Still available by name.
        self.reserved = reserved or set()

    def _quality(self, backend: Backend, task: str) -> float:
        return priors.quality(backend.info.kind, getattr(backend, "options", {}) or {},
                              task, backend.info.capabilities)

    def choose(self, need: Need) -> Choice:
        rejected: List[str] = []

        if need.backend:
            for b in self.backends:
                if b.key == need.backend:
                    return Choice(b, f"{b.key}: asked for by name")
            return Choice(None, f"no backend named {need.backend!r}")

        spent = self.quota.exhausted() if self.quota else {}
        candidates: List[Backend] = []

        for b in self.backends:
            if b.key in self.reserved:
                rejected.append(f"{b.key}: reserved as the router model")
                continue
            if self.policy.is_disabled(b.key):
                rejected.append(f"{b.key}: turned off in policy")
                continue
            if self.is_up(b.key) is False:
                # a server that isn't listening is not a candidate; routing to
                # it produces a failed request instead of a different answer
                rejected.append(f"{b.key}: not running")
                continue
            caps = b.info.capabilities
            if not need.images_out and not caps.text:
                # an image model asked a question draws a picture of it; that
                # is a wrong answer, not a cheap one
                rejected.append(f"{b.key}: draws images, doesn't answer in words")
                continue
            missing = [
                name for name, wanted, have in (
                    ("repo", need.repo, caps.repo),
                    ("tools", need.tools, caps.tools),
                    ("vision", need.vision, caps.vision),
                    ("images", need.images_out, caps.images_out),
                ) if wanted and not have
            ]
            if missing:
                rejected.append(f"{b.key}: lacks {', '.join(missing)}")
                continue
            if need.context_tokens and caps.context_tokens < need.context_tokens:
                rejected.append(
                    f"{b.key}: context {caps.context_tokens} < {need.context_tokens}")
                continue
            source = b.info.quota_source
            if source and source in spent:
                rejected.append(f"{b.key}: {spent[source]}")
                continue
            candidates.append(b)

        if not candidates:
            return Choice(None, "no backend can serve this request", rejected)

        # Cheap is not the same as good enough. A labelled request keeps only
        # the backends whose class is believed to handle that kind of work at
        # that difficulty; if none clears the bar, the best available wins
        # instead of the cheapest.
        note = ""
        if need.task and need.difficulty:
            bar = priors.NEED.get(need.difficulty, 0.72)
            scored = [(self._quality(b, need.task), b) for b in candidates]
            good = [b for q, b in scored if q >= bar]
            if good:
                note = f" for {need.task}/{need.difficulty}"
                for q, b in scored:
                    if q < bar:
                        rejected.append(f"{b.key}: likely not good enough for "
                                        f"{need.difficulty} {need.task}")
                candidates = good
            else:
                best = max(q for q, _ in scored)
                candidates = [b for q, b in scored if q == best]
                note = f" — the best eki has for {need.difficulty} {need.task}"

        # cheapest first; ties go to the policy's order, then config order,
        # which is the user's standing preference
        def tier(b: Backend) -> int:
            return self.policy.tier_for(b.key, b.info.cost.tier)

        candidates.sort(key=lambda b: (tier(b), self.policy.rank(b.key)))
        pick = candidates[0]
        why = f"{pick.key}: cheapest fit (tier {tier(pick)}){note}"
        if tier(pick) != pick.info.cost.tier:
            why += " by policy"
        if pick.info.cost.note:
            why += f", {pick.info.cost.note}"
        return Choice(pick, why, rejected)
