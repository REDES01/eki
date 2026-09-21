"""What a backend is, and what the router is allowed to ask about it.

A backend is anything that can answer a turn: a local model over HTTP, an
official CLI driven as a subprocess, a paid API. They differ enormously in what
they can do, so capabilities are declared rather than guessed — the router
needs to eliminate candidates before trying them, not discover a mismatch from
an error.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Type


@dataclass
class Message:
    role: str                    # "user" | "assistant" | "system"
    content: str


@dataclass
class Capabilities:
    """Facts the router filters on. Conservative defaults: a backend opts in."""

    context_tokens: int = 8_000
    text: bool = True            # answers in words; false for an image-only model
    vision: bool = False
    tools: bool = False          # can call tools / edit files on its own
    repo: bool = False           # can be pointed at a working directory
    images_out: bool = False
    streaming: bool = True


@dataclass
class Cost:
    """Ordering hint, not accounting. Lower sorts first."""

    tier: int = 50               # 0 local/free, 50 subscription, 100 metered
    note: str = ""


@dataclass
class BackendInfo:
    key: str                     # "qwen", "claude"
    kind: str                    # adapter kind: "mlx", "claude_code"
    label: str
    capabilities: Capabilities = field(default_factory=Capabilities)
    cost: Cost = field(default_factory=Cost)
    #: provider key in tokenbar, when this backend spends a tracked quota
    quota_source: Optional[str] = None


@dataclass
class Health:
    ok: bool
    detail: str = ""


class BackendError(RuntimeError):
    """Adapter failed. The router logs it and moves to the next candidate."""


class Backend(abc.ABC):
    def __init__(self, info: BackendInfo, options: Dict[str, Any]):
        self.info = info
        self.options = options or {}

    @property
    def key(self) -> str:
        return self.info.key

    @abc.abstractmethod
    async def health(self) -> Health:
        """Cheap reachability check. Must not spend quota."""

    @abc.abstractmethod
    def stream(self, messages: List[Message], **kw) -> AsyncIterator[str]:
        """Yield response text as it arrives.

        Implementations are async generators. Non-streaming backends yield once.
        """

    async def close(self) -> None:
        pass


_REGISTRY: Dict[str, Type[Backend]] = {}


def register(kind: str) -> Callable[[Type[Backend]], Type[Backend]]:
    def deco(cls: Type[Backend]) -> Type[Backend]:
        _REGISTRY[kind] = cls
        cls.kind = kind
        return cls
    return deco


def build(info: BackendInfo, options: Dict[str, Any]) -> Backend:
    if info.kind not in _REGISTRY:
        raise KeyError(f"unknown backend kind {info.kind!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[info.kind](info, options)


def kinds() -> List[str]:
    return sorted(_REGISTRY)
