# SPDX-License-Identifier: Apache-2.0
"""eki — tell it to do something; it keeps doing it until it's done.

    eki ask "why is this slow?"          routed; the answer streams here
    eki ask "..." --backend claude       force a backend
    eki ask "..." --repo .               may edit files there
    eki ask "a brass compass" --image    draw it
    eki ask "..." --continue             same thread as last time
    eki ask "..." --detach               start it and return straight away

For an agent with a shell — blocking, quiet, a path out, real exit codes:

    eki capabilities                     what this Mac can do right now
    eki image "a brass compass" -o art/  a picture, saved; its path printed
    eki write "a sea shanty" -m qwen     text from that model into a file
    eki submit "…" [-m key] [--image]    start it, print its run id, return
    eki wait <run> [-o path]             block until it ends; its path or text
    ... --json                           one JSON object instead

Every ask is a run in the engine, not in this terminal: Ctrl-C stops
*watching*, never the work. Pick it back up with `eki watch <run>`.

    eki runs [--here]                    what's running and what finished
    eki project [init]                   a folder with .eki/: calls inside it belong to it
    eki watch <run>                      follow one, from the start
    eki cancel <run>                     stop it (its CLI child dies with it)
    eki diff <run>                       what it changed in the repo

    eki history [-q text]                conversations, or a search of them
    eki show <conversation>              one thread, with who answered
    eki cost <conversation>              who answered, and what they reported
    eki summary <conversation>           a long thread's summary, as the next model reads it
    eki backends | models | policy       what exists, what's loaded, the rules
    eki agent install|uninstall|status   run the engine from login, always

    eki self "change yourself so…"       eki works on its own source: a branch,
                                         a diff and a verdict — never a merge
    eki self -r "…" -r "…"               several changes, queued; side by side as room allows
    eki self                             what it has proposed so far
    eki self --watch                     each change going in, at its stage, as it moves
    eki serve                            run the engine in the foreground
"""
# One module per command (or small family of commands) in this package, each
# with a `register(sub)` that adds its subparser, its flags and its `func`,
# and an ORDER that places it in `eki --help`. main() finds them by looking
# at the package, so a new command is a new file here — there is no shared
# list to edit. What several commands need is in common.py.
from __future__ import annotations

import argparse
import importlib
import pkgutil
from pathlib import Path
from typing import List, Optional

from .. import config as config_mod
from .. import migrate
from .common import DEFAULT_SERVICE

#: modules here that aren't commands
NOT_COMMANDS = ("common", "__main__")


def commands() -> list:
    """Every command module in the package, in the order `eki --help` lists them."""
    found = [importlib.import_module(f"{__name__}.{m.name}") for m in pkgutil.iter_modules(__path__)
             if m.name not in NOT_COMMANDS]
    return sorted((m for m in found if hasattr(m, "register")),
                  key=lambda m: (getattr(m, "ORDER", 1000), m.__name__))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="eki", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=str(config_mod.default_path()))
    ap.add_argument("--service", default=DEFAULT_SERVICE, help=argparse.SUPPRESS)
    from .. import __version__
    ap.add_argument("--version", action="version", version=f"eki {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for module in commands():
        module.register(sub)
    return ap


def _intermixed(ap: argparse.ArgumentParser, args, extra: List[str]):
    """`eki self apply --yes abc` puts the id after a flag, where argparse
    stops filling `request` and leaves the rest over. A command that says so
    (`leftover`: `eki self`'s request) takes those leftover words, in order,
    so flags work anywhere; anything else left over (or an unknown flag) is
    still an error."""
    unknown = [w for w in extra if w.startswith("-") and w != "-"]
    leftover = getattr(args, "leftover", "")
    if not leftover or unknown:
        ap.error("unrecognized arguments: " + " ".join(unknown or extra))
    setattr(args, leftover, list(getattr(args, leftover) or []) + extra)
    return args


def main(argv: Optional[List[str]] = None) -> int:
    migrate.run(Path(__file__).resolve().parent.parent.parent)
    ap = parser()
    args, extra = ap.parse_known_args(argv)
    if extra:
        args = _intermixed(ap, args, extra)
    return args.func(args)
