"""Running a card through the engine and listing its uses: routes for apiroutes."""
from __future__ import annotations

from typing import Callable, List, Tuple

#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = []
