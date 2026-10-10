"""The window's newer API as a table: each page's `api_*.py` lists its own
routes, so pages built side by side never meet in `server.py`.

An entry is `(method, regex, fn)`. The regex must match the whole path;
`fn(conn, m, q, body)` gets the match, the query (one value per key) and
the POST's JSON body (`{}` on a GET), and returns JSON data. `server._call`
turns a KeyError into 404 and a ValueError into 400.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

from . import api_artifacts, api_components, api_customize, api_projects, api_schedule

Route = Tuple[str, str, Callable]


def routes() -> List[Route]:
    """Read on every call, so a route added to an `api_*.ROUTES` is live."""
    return (api_projects.ROUTES + api_artifacts.ROUTES + api_schedule.ROUTES + api_customize.ROUTES
            + api_components.ROUTES)


ROUTES: List[Route] = routes()


def match(method: str, path: str) -> Optional[Tuple[Callable, "re.Match[str]"]]:
    """The first route for this method whose regex matches the whole path."""
    for meth, regex, fn in routes():
        if meth == method:
            m = re.fullmatch(regex, path)
            if m:
                return fn, m
    return None
