"""workrun.HANDLERS for plan, build, check and fix actions (start_run, run_gate 1, ask_person)."""
from __future__ import annotations

from typing import Callable, Dict

HANDLERS: Dict[str, Callable] = {}
