"""Sessions of a component call kept under ~/.eki/components/sessions/<name>/<session>/."""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import List

from . import Result, sessions_dir


def new_session(name: str) -> Path:
    """A fresh folder for one call of `name`."""
    while True:
        stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
        d = sessions_dir() / name / f'{stamp}-{secrets.token_hex(2)}'
        try:
            d.mkdir(parents=True)
            return d
        except FileExistsError:
            continue


def keep(session: Path, texts: List[str]) -> Result:
    """Write what the session said to transcript.md; the result is its last word and the folder."""
    session.mkdir(parents=True, exist_ok=True)
    (session / 'transcript.md').write_text('\n\n---\n\n'.join(texts) + ('\n' if texts else ''))
    return Result(text=texts[-1] if texts else '', paths=[str(session)])
