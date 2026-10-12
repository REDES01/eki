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

from . import (api, api_artifacts, api_cardadd, api_carddetail, api_cardedit, api_cardinstructions, api_cardknowledge,
               api_cardmcp, api_cardrun, api_cards, api_cardsessions, api_cardskills, api_comfyui, api_components,
               api_customize, api_doc, api_docsearch, api_projectlands, api_projects, api_schedule, api_sensitive, api_sync,
               api_workflow)

Route = Tuple[str, str, Callable]

#: Settings → Machine: the caps and the live line (eki/machinenow.py)
MACHINE: List[Route] = [("GET", r"/api/machine", lambda conn, m, q, body: api.machine_view(conn))]


def routes() -> List[Route]:
    """Read on every call, so a route added to an `api_*.ROUTES` is live."""
    return (api_projects.ROUTES + api_artifacts.ROUTES + api_schedule.ROUTES + api_customize.ROUTES
            + api_components.ROUTES + api_cards.ROUTES + api_cardadd.ROUTES + api_cardedit.ROUTES
            + api_carddetail.ROUTES + api_cardinstructions.ROUTES + api_cardrun.ROUTES + api_cardsessions.ROUTES
            + api_cardskills.ROUTES + api_cardknowledge.ROUTES + api_cardmcp.ROUTES + api_doc.ROUTES + api_docsearch.ROUTES
            + api_workflow.ROUTES + api_projectlands.ROUTES + api_comfyui.ROUTES + api_sync.ROUTES + api_sensitive.ROUTES
            + MACHINE)


ROUTES: List[Route] = routes()


def match(method: str, path: str) -> Optional[Tuple[Callable, "re.Match[str]"]]:
    """The first route for this method whose regex matches the whole path."""
    for meth, regex, fn in routes():
        if meth == method:
            m = re.fullmatch(regex, path)
            if m:
                return fn, m
    return None
