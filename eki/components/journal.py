"""Sessions of a component call kept under ~/.eki/components/sessions/<name>/<session>/.

keep() reads the lines an agent wrote as it went — `NOTE: …`, `FEEL <emotion>: …`
and a closing `REVIEW: …` — into notes.md, emotions.jsonl and review.md beside
the full transcript.md. Screenshots stay in <session>/screenshots.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

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


def keep(session: Path, texts: List[str]) -> Result:
    """Write the transcript, notes, emotion log and review; the result is the review and the folder."""
    session.mkdir(parents=True, exist_ok=True)
    notes, feels, review = parse(texts)
    (session / 'transcript.md').write_text('\n\n---\n\n'.join(texts) + ('\n' if texts else ''))
    (session / 'notes.md').write_text(''.join(f'- {n}\n' for n in notes))
    with (session / 'emotions.jsonl').open('w') as f:
        for emotion, why in feels:
            t = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            f.write(json.dumps({'t': t, 'emotion': emotion, 'why': why}) + '\n')
    (session / 'review.md').write_text(review + ('\n' if review else ''))
    return Result(text=f'{review}\n\nsession: {session}', paths=[str(session)])
