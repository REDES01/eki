"""An agent card's sessions, files served only from inside the session folder: routes for apiroutes."""
from __future__ import annotations

from typing import Callable, List, Tuple

#: (method, regex, fn) — see eki/apiroutes.py
ROUTES: List[Tuple[str, str, Callable]] = []
