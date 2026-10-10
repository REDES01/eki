"""Sessions of a component call kept under ~/.eki/components/sessions/<name>/<session>/.

keep() reads the lines an agent wrote as it went — `NOTE: …`, `FEEL <emotion>: …`
and a closing `REVIEW: …` — into notes.md, emotions.jsonl and review.md beside
the full transcript.md. Screenshots stay in <session>/screenshots. It also reads
the session's tool calls: a navigate anywhere but the component's url (same
origin, under its path) taints the session — said in session.json and on the
result, so its review isn't trusted blindly.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from . import Result, sessions_dir

# A marker may sit behind list bullets, quotes or bold: "- **NOTE:** …".
_LEAD = r'^[\s>*_#\-]*'
NOTE = re.compile(_LEAD + r'NOTE\s*[*_]*:[*_]*\s*(.*)$')
FEEL = re.compile(_LEAD + r'FEEL\s+([^:\n]+?)\s*[*_]*:[*_]*\s*(.*)$')
REVIEW = re.compile(_LEAD + r'REVIEW\s*[*_]*:[*_]*[ \t]*', re.M)


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


def parse(texts: List[str]) -> Tuple[List[str], List[Tuple[str, str]], str]:
    """The NOTE lines, the (emotion, why) of each FEEL line, and the review, in order."""
    notes: List[str] = []
    feels: List[Tuple[str, str]] = []
    for text in texts:
        for line in text.splitlines():
            m = NOTE.match(line)
            if m:
                notes.append(m.group(1).strip())
                continue
            m = FEEL.match(line)
            if m:
                feels.append((m.group(1).strip(' *_').lower(), m.group(2).strip()))
    review = ''
    for text in reversed(texts):
        found = list(REVIEW.finditer(text))
        if found:
            review = text[found[-1].end():].strip()
            break
    if not review:
        review = texts[-1].strip() if texts else ''
    return notes, feels, review


def _inside(target: str, url: str) -> bool:
    t, u = urlsplit(target), urlsplit(url)
    if (t.scheme.lower(), t.netloc.lower()) != (u.scheme.lower(), u.netloc.lower()):
        return False
    base = u.path if u.path.endswith('/') else u.path.rsplit('/', 1)[0] + '/'
    path = unquote(t.path or '/')
    if {'..', '.'} & set(path.split('/')):
        return False
    return path == u.path or path.startswith(base)


def opened_elsewhere(calls: List[dict], url: str) -> List[str]:
    """Each URL a navigate call opened outside `url`, in order. A call is a tool event:
    {"name", "url"} (or {"name", "input": {"url"}})."""
    out = []
    for c in calls or []:
        if not str(c.get('name', '')).endswith('browser_navigate'):
            continue
        target = c.get('url') or (c.get('input') or {}).get('url') or ''
        if not url or not _inside(str(target), url):
            out.append(str(target))
    return out


def keep(session: Path, texts: List[str], calls: Optional[List[dict]] = None, url: str = '') -> Result:
    """Write the transcript, notes, emotion log and review; the result is the review and the folder."""
    session.mkdir(parents=True, exist_ok=True)
    away = opened_elsewhere(calls or [], url)
    tainted = f'opened {away[0]}' if away else ''
    (session / 'session.json').write_text(json.dumps({'url': url, 'tainted': tainted, 'opened': away}) + '\n')
    notes, feels, review = parse(texts)
    (session / 'transcript.md').write_text('\n\n---\n\n'.join(texts) + ('\n' if texts else ''))
    (session / 'notes.md').write_text(''.join(f'- {n}\n' for n in notes))
    with (session / 'emotions.jsonl').open('w') as f:
        for emotion, why in feels:
            t = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            f.write(json.dumps({'t': t, 'emotion': emotion, 'why': why}) + '\n')
    (session / 'review.md').write_text(review + ('\n' if review else ''))
    head = f'tainted: {tainted}\n\n' if tainted else ''
    return Result(text=f'{head}{review}\n\nsession: {session}', paths=[str(session)])
