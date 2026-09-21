# SPDX-License-Identifier: Apache-2.0
"""Claude Code status-line command that also records your usage limits.

Claude Code documents a `rate_limits` object in the JSON it pipes to a status
line command: the 5-hour and 7-day windows, percent used, and reset times.
That is the only sanctioned place a third-party tool can read them — the
alternative, reading Claude Code's OAuth token out of the Keychain and calling
Anthropic yourself, is against Anthropic's terms.

So this script sits where your status line command would. Every time Claude
Code refreshes the status line it:

  1. saves `rate_limits` (when present) to ~/.eki/quota/claude.json,
  2. hands the same input to the status line command you had before, if any,
     and prints what it prints — so installing this changes nothing you see,
  3. otherwise prints a short line of its own.

Stdlib only, and fast: Claude Code runs it on every refresh.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QUOTA_DIR = Path(os.environ.get("EKI_QUOTA_DIR", "~/.eki/quota")).expanduser()
READING = QUOTA_DIR / "claude.json"
CHAIN = QUOTA_DIR / "statusline-chain.json"


def record(data: dict) -> None:
    """Keep the last reading. Atomic, so a reader never sees half a file.

    Written on every call, even when Claude Code reported no limits — the
    difference between "you haven't used Claude Code since turning this on"
    and "it ran and told us nothing" is the whole diagnosis, and a file that
    only appears on success can't tell them apart.
    """
    limits = data.get("rate_limits")
    if not isinstance(limits, dict):
        limits = {}
    QUOTA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"observed_at": int(time.time()), "rate_limits": limits,
               "model": (data.get("model") or {}).get("id")}
    fd, tmp = tempfile.mkstemp(dir=QUOTA_DIR, prefix=".claude-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, READING)


def previous_command() -> str:
    try:
        return str(json.loads(CHAIN.read_text()).get("command") or "")
    except (OSError, ValueError):
        return ""


def own_line(data: dict) -> str:
    """Model and both windows, compact. Only used when nothing was chained."""
    model = (data.get("model") or {}).get("display_name") or "Claude"
    bits = [model]
    limits = data.get("rate_limits") or {}
    for key, label in (("five_hour", "5h"), ("seven_day", "week")):
        pct = (limits.get(key) or {}).get("used_percentage")
        if isinstance(pct, (int, float)):
            bits.append(f"{label} {pct:.0f}%")
    return " · ".join(bits)


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    try:
        record(data)
    except OSError:
        pass                            # never break the status line over this

    chained = previous_command()
    if chained:
        try:
            out = subprocess.run(chained, shell=True, input=raw, text=True,
                                 capture_output=True, timeout=5)
            sys.stdout.write(out.stdout)
            return 0
        except (OSError, subprocess.SubprocessError):
            pass
    print(own_line(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
