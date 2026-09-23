# SPDX-License-Identifier: Apache-2.0
"""eki — one place to ask, whichever model answers."""
from pathlib import Path as _Path


def _version() -> str:
    try:
        return (_Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    except OSError:
        return "0+unknown"


__version__ = _version()
