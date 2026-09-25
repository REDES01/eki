# SPDX-License-Identifier: Apache-2.0
"""`eki remember` / `eki recall`: the memory every harness reads (eki/notes.py)."""
from __future__ import annotations

import json
import os
import sys

from .. import grant as grant_mod

ORDER = 50


def register(sub) -> None:
    for name, helptext in (("remember", "keep a note in memory every harness reads (here: the project's)"),
                           ("recall", "read eki's memory: every note, one by name, or search it")):
        mm = sub.add_parser(name, help=helptext)
        if name == "remember":
            mm.add_argument("text", help="the note, in plain words (- reads stdin)")
            mm.add_argument("-n", "--name", default="", help="its name (default: from its first line)")
            mm.add_argument("-d", "--description", default="", help="one line on what it is")
            mm.add_argument("-a", "--append", action="store_true", help="add to the note of that name")
        else:
            mm.add_argument("what", nargs="*", default=[], help="a note's name, or words to look for")
            mm.add_argument("-s", "--search", action="store_true", help="search even if a note has that name")
        where = mm.add_mutually_exclusive_group()
        where.add_argument("-g", "--global", dest="global_", action="store_true",
                           help="only your own notes, ~/.eki/memory")
        where.add_argument("-p", "--project", action="store_true",
                           help="only the project's, its .eki/memory")
        mm.add_argument("--json", action="store_true")
        mm.set_defaults(func=cmd_remember if name == "remember" else cmd_recall)


def cmd_remember(args) -> int:
    """Keep a note in eki's memory: the project's when run inside one,
    yours otherwise (eki/notes.py). Refused inside a read-only run."""
    from .. import notes
    if grant_mod.from_env().level == "read":
        print("a read-only run can't write to memory", file=sys.stderr)
        return 1
    text = sys.stdin.read() if args.text == "-" else args.text
    run = os.environ.get("EKI_RUN", "")             # an agent's shell under an eki run
    try:
        note = notes.write(text, name=args.name, where=os.getcwd(), scope=_scope(args),
                           description=args.description,
                           source=f"run {run}" if run else "eki remember",
                           append=args.append)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(note))
    else:
        print(f"{'remembered' if note['made'] else 'updated'} {note['name']} ({note['scope']}) — {note['path']}")
    return 0


def cmd_recall(args) -> int:
    """What eki's memory holds here: every note, one by name, or the ones
    that mention some words."""
    from .. import notes
    where, scope, what = os.getcwd(), _scope(args), " ".join(args.what).strip()
    try:
        note = notes.read(what, where, scope) if what and not args.search else None
        found = [] if note or not what else notes.search(what, where, scope)
        rows = notes.listing(where, scope) if not what else found
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(note if note else rows))
        return 0
    if note:
        print(note["text"])
        return 0
    for n in rows:
        print(f"{n['name']}  [{n['scope']}]  {n['description']}")
        for ln in n.get("lines") or ():
            print(f"    {ln[:120]}")
    if not rows:
        print(f"nothing in memory mentions {what!r}" if what else
              "nothing in memory yet — eki remember \"…\" keeps a note")
        return 1 if what else 0
    return 0


def _scope(args) -> str:
    return "global" if args.global_ else "project" if args.project else ""
