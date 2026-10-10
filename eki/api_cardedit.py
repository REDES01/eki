"""Editing a card: its settings and an agent's prompt, validated by its kind: routes for apiroutes."""
from __future__ import annotations

from typing import Callable, List, Tuple

#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = []
