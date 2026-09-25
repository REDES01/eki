# SPDX-License-Identifier: Apache-2.0
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
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set, Tuple, Type


#: the kinds of thing a backend can make; a request asks for one of them.
#: Routing starts here — a backend that can't make what was asked for is out
#: before quota or cost are looked at.
PRODUCTS = ("code", "prose", "image", "mesh", "audio")


@dataclass
class Message:
    role: str                    # "user" | "assistant" | "system" | "tool"
    content: str
    #: an assistant turn's calls, in OpenAI's shape, and the call a "tool"
    #: message answers — only in a local model's tool loop (eki/toolloop.py)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: str = ""


@dataclass
class ToolCall:
    """A function the model called, from a server that speaks OpenAI's
    `tools` (mlx_lm.server does). Yielded into the answer's stream once the
    call is complete; whoever offered the tool picks it out."""
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""                # the arguments as sent, when they weren't JSON
    id: str = ""                 # the server's id for it, answered by a "tool" message


@dataclass
class Capabilities:
    """Facts the router filters on. Conservative defaults: a backend opts in."""

    context_tokens: int = 8_000
    text: bool = True            # answers in words; false for an image-only model
    vision: bool = False
    tools: bool = False          # can call tools / edit files on its own
    repo: bool = False           # can be pointed at a working directory
    images_out: bool = False
    #: can look things up on the web by itself (a hosted search, a native
    #: one, or an MCP search server it has been given) — research needs it
    web: bool = False
    streaming: bool = True
    #: what it makes, from PRODUCTS; empty: what its adapter declares
    produces: Tuple[str, ...] = ()
    #: what a request must bring before it can start ("image" for an
    #: image-to-3D model, "folder"); empty: what its adapter declares
    needs: Tuple[str, ...] = ()

    def __post_init__(self):
        # rows from JSON and YAML hold lists; keep them hashable and fixed
        self.produces = tuple(self.produces or ())
        self.needs = tuple(self.needs or ())


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


#: the agent programs eki drives through their official binaries: each keeps
#: its own session, loads skills from its own folder, and has its own tools
AGENT_CLIS = ("claude_code", "codex", "gemini_cli")


class BackendError(RuntimeError):
    """Adapter failed. The router logs it and moves to the next candidate."""


class Backend(abc.ABC):
    #: what this kind of backend makes and needs, declared by the adapter so
    #: routing can rule it out without trying it; a provider's own
    #: capabilities override these (a ComfyUI workflow that makes meshes)
    PRODUCES: Tuple[str, ...] = ("code", "prose")
    NEEDS: Tuple[str, ...] = ()

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


def produces(backend: Backend) -> Set[str]:
    """What a backend makes: its provider's word if it gave one, else its
    adapter's. A text-only row of an adapter that also draws (or the other
    way round) is taken at its row's `text` / `images_out`."""
    caps = backend.info.capabilities
    if caps.produces:
        return set(caps.produces)
    made = set(getattr(type(backend), "PRODUCES", ()) or ())
    if not caps.text:
        made -= {"code", "prose"}
    if caps.images_out:
        made.add("image")
    return made


def needs(backend: Backend) -> Set[str]:
    """What a request must bring for this backend to start."""
    caps = backend.info.capabilities
    return set(caps.needs or getattr(type(backend), "NEEDS", ()) or ())


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
