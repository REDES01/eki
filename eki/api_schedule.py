"""The window's Scheduled page over HTTP: its routes for `apiroutes`."""
from __future__ import annotations

from typing import Callable, List, Tuple

#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = []
