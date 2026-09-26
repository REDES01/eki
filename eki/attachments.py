"""Files sent with a request: made safe at intake, and uploads from the window.

A HEIC becomes a JPEG and a picture over 5 MB is scaled down, both with
macOS `sips`, into a dated folder under EKI_HOME/attachments — the original
is never changed. Anything else (a PDF, a text file) passes through as a
path. When `sips` is missing or fails, the original is used as it is.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from . import paths
from .routing.needs import IMAGE_EXT, is_picture

BIG = 5 * 1024 * 1024          # a picture over this is scaled down at intake
MAX_UPLOAD = 20 * 1024 * 1024  # an upload over this is refused


def _folder() -> Path:
    p = paths.attachments() / time.strftime("%Y-%m-%d")
    p.mkdir(exist_ok=True)
    return p


def _sips(args: list, src: str, dest: Path) -> bool:
    if not shutil.which("sips"):
        return False
    try:
        subprocess.run(["sips", *args, src, "--out", str(dest)], capture_output=True,
                       timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return dest.is_file() and dest.stat().st_size > 0


def intake(path: str) -> str:
    """The path to send: a safe copy of the picture if one was needed, else `path`."""
    if not is_picture(path) and not path.lower().endswith(".heif"):
        return path
    src = path
    stem = f"{Path(path).stem}-{secrets.token_hex(3)}"
    if path.lower().endswith((".heic", ".heif")):
        dest = _folder() / f"{stem}.jpg"
        if not _sips(["-s", "format", "jpeg"], src, dest):
            return path
        src = str(dest)
    if os.path.getsize(src) > BIG:
        dest = _folder() / f"{stem}-small{Path(src).suffix.lower()}"
        if _sips(["-Z", "2000"], src, dest):
            src = str(dest)
    return src


def save(data: bytes, name: str) -> str:
    """Write an uploaded picture; its absolute path. ValueError for a non-picture or too big."""
    ext = Path(name or "").suffix.lower()
    if ext not in IMAGE_EXT:
        raise ValueError(f"not a picture: {name!r}")
    if len(data) > MAX_UPLOAD:
        raise ValueError("picture over 20 MB")
    dest = _folder() / f"{secrets.token_hex(6)}{ext}"
    dest.write_bytes(data)
    return str(dest.resolve())
