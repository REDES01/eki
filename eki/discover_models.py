# SPDX-License-Identifier: Apache-2.0
"""Which models each provider can actually run, from the provider itself.

Nothing is hard-coded: Claude Code names its aliases in its own --help,
Codex keeps the list it fetched in its cache, and an API or local server
answers /models. What comes back lands in the registry; what the user
turned off or eki measured about a model is kept across listings.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from . import priors
from . import public_scores
from .adapters.base import Backend
from .capability import Registry

CODEX_CACHE = Path("~/.codex/models_cache.json").expanduser()


def claude_aliases(binary: str) -> List[str]:
    """The aliases `claude --help` mentions for --model, e.g. fable, opus, sonnet."""
    try:
        out = subprocess.run([binary, "--help"], capture_output=True, text=True,
                             timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    m = re.search(r"--model <model>(.*?)(?:\n\s*--|\Z)", out, re.S)
    if not m:
        return []
    # "an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet') or a
    # model's full name (e.g. 'claude-fable-5')" — the aliases, not the example
    text = m.group(1).split("full name")[0]
    return list(dict.fromkeys(re.findall(r"'([a-z][a-z0-9-]*)'", text)))


def codex_models() -> List[Tuple[str, str, int]]:
    """(slug, display name, context) for the models Codex lists in its picker."""
    try:
        data = json.loads(CODEX_CACHE.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for m in data.get("models") or []:
        if not isinstance(m, dict) or m.get("visibility") == "hide":
            continue
        slug = m.get("slug") or m.get("id")
        if slug:
            out.append((slug, m.get("display_name") or slug, int(m.get("context_window") or 0)))
    return out


def codex_default() -> Tuple[str, str]:
    """(model, reasoning effort) from ~/.codex/config.toml, if set."""
    try:
        text = Path("~/.codex/config.toml").expanduser().read_text()
    except OSError:
        return "", ""
    model = re.search(r'^model\s*=\s*"([^"]+)"', text, re.M)
    effort = re.search(r'^model_reasoning_effort\s*=\s*"([^"]+)"', text, re.M)
    return (model.group(1) if model else ""), (effort.group(1) if effort else "")


async def discover(backend: Backend, registry: Registry,
                   default_model: str = "") -> List[str]:
    """Record every model this backend offers. Returns their ids.

    `default_model` is what the provider runs when none is named — Claude
    Code's current model from its status line, Codex's config — so the
    default entry can be looked up on the public boards too.
    """
    info, key, kind = backend.info, backend.key, backend.info.kind
    options = getattr(backend, "options", {}) or {}
    caps = info.capabilities
    found: List[Tuple[str, str, int]] = [("", f"{info.label} (default)", caps.context_tokens)]
    if kind == "claude_code":
        found += [(a, a.capitalize(), caps.context_tokens)
                  for a in claude_aliases(getattr(backend, "bin", "") or "claude")]
    elif kind == "codex":
        found += codex_models()
    elif kind == "mlx":
        pass                                        # one server, one model: the default
    elif hasattr(backend, "list_models"):
        try:
            names = await backend.list_models()
        except Exception:                           # noqa: BLE001
            names = []
        # a local server with one thing loaded is that thing; a hosted API
        # lists dozens, and the default entry is the one the user configured
        found += [(n, n.split("/")[-1], caps.context_tokens) for n in names[:60]]
    effort = ""
    if kind == "codex":
        cfg_model, effort = codex_default()
        default_model = default_model or cfg_model
    default_model = default_model or str(options.get("model") or "")
    ids = []
    for model, label, context in found:
        klass = priors.class_of_model(kind, model or default_model, options, caps)
        public = public_scores.lookup(kind, model, default_model, effort) or {}
        registry.seen(key, model, label=label, context_tokens=context, klass=klass,
                      public=public)
        ids.append(model)
    return ids
