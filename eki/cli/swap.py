# SPDX-License-Identifier: Apache-2.0
"""`eki swap`: move the engine onto another build, watched, with a way back."""
from __future__ import annotations

import sys
import time

ORDER = 180


def register(sub) -> None:
    sp = sub.add_parser("swap", help="move the engine onto another build, watched, with a way back")
    sp.add_argument("ref", nargs="?", default="HEAD", help="a commit or branch in your checkout")
    sp.add_argument("--back", action="store_true", help="to the previous build")
    sp.add_argument("--dev", action="store_true", help="back to running your checkout itself")
    sp.add_argument("--no-check", action="store_true", help="skip the candidate check")
    sp.add_argument("--skip-tests", action="store_true", help="candidate check without the test suite")
    sp.add_argument("--force", action="store_true",
                    help="swap even if it drops something the engine runs now")
    sp.add_argument("--wait", type=int, default=120,
                    help="seconds to wait for a moment with no runs before swapping anyway")
    sp.add_argument("--watch", type=int, default=180, help="seconds it must stay healthy")
    sp.set_defaults(func=cmd_swap)


def cmd_swap(args) -> int:
    """Move the engine onto another build, through the supervisor."""
    from .. import builds, candidate
    src = builds.source()
    why = builds.cant_change_code(src)
    if why:
        print(f"! {why}", file=sys.stderr)
        return 1
    if args.back:
        target = builds.BUILDS / "previous"
        if not target.is_symlink():
            print("nothing to go back to", file=sys.stderr)
            return 1
        target = target.resolve()
    elif args.dev:
        target = src
    else:
        # what the engine runs goes into your checkout first, if it can; a
        # build that would still drop it is refused (eki/builds.py, the line)
        caught = builds.catch_up(src)
        if caught:
            print(f"· {caught}", file=sys.stderr)
        lost = builds.behind(src, args.ref)
        if lost and not args.force:
            subject = builds._g(src, "log", "-1", "--format=%s", lost).stdout.strip()
            print(f"! the engine runs {lost[:10]} ({subject}), which {args.ref} doesn't have — "
                  f"swapping would drop it. Commit your edits so eki can bring it into your "
                  f"checkout, or merge {lost[:10]}; --force swaps anyway", file=sys.stderr)
            return 1
        try:
            target = builds.make(src, args.ref)
        except (ValueError, RuntimeError) as e:
            print(f"! {e}", file=sys.stderr)
            return 1
        if not args.no_check:
            say = lambda line: print(f"· {line}", file=sys.stderr, flush=True)   # noqa: E731
            report = candidate.check(target, python=sys.executable, say=say,
                                     skip=("tests",) if args.skip_tests else ())
            if not report.fit:
                print(f"! {target.name} isn't fit to run — not swapping", file=sys.stderr)
                return 1
    got = builds.swap(target, wait=args.wait, watch=args.watch)
    mins = max(0, got["deadline"] - int(time.time())) // 60
    print(f"swapping to {target} — at a quiet moment, or in {mins} min anyway (runs still going "
          f"carry on in the new engine); watched {args.watch}s, rolled back if unhealthy. "
          f"Follow it: tail -f {got['log']}")
    return 0
