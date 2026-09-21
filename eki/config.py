"""YAML in, typed objects out. Backends are config, not code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .adapters.base import BackendInfo, Capabilities, Cost


@dataclass
class Config:
    db_path: str = "~/.eki/eki.db"
    quota_url: str = "http://127.0.0.1:8777"
    quota_ceiling: float = 0.99
    backends: List[BackendInfo] = field(default_factory=list)
    options: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    local_models: List[Any] = field(default_factory=list)
    #: 0 means "work it out from this Mac's installed memory"
    memory_ceiling_gb: float = 0.0

    @property
    def db(self) -> Path:
        return Path(os.path.expanduser(self.db_path))


def load(path: str | os.PathLike) -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    cfg = Config()
    cfg.db_path = raw.get("db_path", cfg.db_path)

    q = raw.get("quota") or {}
    cfg.quota_url = q.get("url", cfg.quota_url)
    cfg.quota_ceiling = float(q.get("ceiling", cfg.quota_ceiling))

    for entry in raw.get("backends") or []:
        if entry.get("enabled") is False:
            continue
        key = entry.get("key") or entry["kind"]
        caps = entry.get("capabilities") or {}
        cost = entry.get("cost") or {}
        cfg.backends.append(BackendInfo(
            key=key,
            kind=entry["kind"],
            label=entry.get("label", key.title()),
            capabilities=Capabilities(
                context_tokens=int(caps.get("context_tokens", 8000)),
                text=bool(caps.get("text", True)),
                vision=bool(caps.get("vision", False)),
                tools=bool(caps.get("tools", False)),
                repo=bool(caps.get("repo", False)),
                images_out=bool(caps.get("images_out", False)),
                streaming=bool(caps.get("streaming", True)),
            ),
            cost=Cost(tier=int(cost.get("tier", 50)), note=cost.get("note", "")),
            quota_source=entry.get("quota_source"),
        ))
        cfg.options[key] = entry.get("options") or {}

    from .models import from_config as models_from_config
    memory = raw.get("memory") or {}
    cfg.memory_ceiling_gb = float(memory.get("ceiling_gb", 0) or 0)
    cfg.local_models = models_from_config(raw.get("local_models") or [])
    return cfg


def default_path() -> Path:
    """Config next to the package, unless EKI_CONFIG says otherwise."""
    env = os.environ.get("EKI_CONFIG")
    if env:
        return Path(os.path.expanduser(env))
    return Path(__file__).resolve().parent.parent / "config.yaml"
