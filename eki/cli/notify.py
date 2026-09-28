"""`eki notify` — pushes to the phone through ntfy: show them, set the topic, send a test.

The topic is the secret (whoever knows it reads the pushes), so no output
here ever carries it whole — only notify.masked().
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from .. import notify, paths
from ..routing import table
from .common import err

NAME = "notify"
HELP = "pushes to your phone through ntfy: show them, set the topic (or off), send a test"

TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def add(p) -> None:
    p.add_argument("action", nargs="?", choices=["topic", "off", "test"],
                   help="topic NAME sets the topic ('' is off); off clears it; test sends one push")
    p.add_argument("name", nargs="?", help="the ntfy topic, for `topic`")


def show() -> int:
    s = notify.settings()
    topic = str(s.get("topic") or "")
    print(f"notify: {'on' if topic else 'off'}")
    print(f"  server: {s.get('server') or notify.DEFAULTS['server']}")
    print(f"  topic:  {notify.masked(topic)}")
    print(f"  events: {', '.join(s.get('events') or []) or 'none'}")
    if not topic:
        print("  turn it on: eki notify topic <name>")
    return 0


def write_topic(topic: str) -> None:
    """notify.topic in routing.json, every other key kept; written whole or not at all."""
    data = table.settings()
    got = data.get("notify")
    data["notify"] = {**(got if isinstance(got, dict) else {}), "topic": topic}
    path = paths.config("routing")
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def set_topic(name: Optional[str]) -> int:
    if name is None:
        err("eki notify topic: give a topic name ('' turns notify off)")
        return 2
    if name and not TOPIC.match(name):
        err("eki notify topic: a topic is 1–64 letters, digits, '-' or '_'")
        return 2
    write_topic(name)
    print(f"notify: on, topic {notify.masked(name)}" if name else "notify: off")
    return 0


def test() -> int:
    if not notify.settings().get("topic"):
        print("not sent — off (eki notify topic <name>)")
        return 1
    if notify.send("test", "eki · test", "a test from eki"):
        print("sent")
        return 0
    print("not sent — failed (the server did not take it)")
    return 1


def run(args) -> int:
    if args.action == "topic":
        return set_topic(args.name)
    if args.name is not None:
        err(f"eki notify {args.action}: takes no name")
        return 2
    if args.action == "off":
        return set_topic("")
    if args.action == "test":
        return test()
    return show()
