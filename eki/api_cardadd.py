"""Adding a card to a project (a template or a pool card) and removing one: routes for apiroutes."""
from __future__ import annotations

from typing import Callable, List, Tuple

#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = []
