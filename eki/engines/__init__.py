# SPDX-License-Identifier: Apache-2.0
"""The engines eki knows, by name and by the format they serve."""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import HOME, Engine, EngineInfo, ServeSpec
from .llamacpp import LlamaCppEngine
from .mlx import MlxEngine

ENGINES: Dict[str, Engine] = {e.info.name: e for e in (MlxEngine(), LlamaCppEngine())}


def get(name: str) -> Engine:
    return ENGINES[name]


def for_format(fmt: str) -> Optional[Engine]:
    """The engine for a model format; installed ones first."""
    able = [e for e in ENGINES.values() if fmt in e.info.formats]
    able.sort(key=lambda e: not e.installed())
    return able[0] if able else None


def statuses() -> List[dict]:
    return [e.status() for e in ENGINES.values()]


__all__ = ["HOME", "Engine", "EngineInfo", "ServeSpec", "ENGINES", "get", "for_format", "statuses"]
