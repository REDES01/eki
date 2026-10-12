"""The browser shim: Playwright's browsers folder, mirrored so each browser
executable waits for a browser slot (browserslots.py) before it starts.

Every run eki starts gets PLAYWRIGHT_BROWSERS_PATH=mirror(), so Playwright's
normal lookup — in scripts and in the Playwright MCP — goes through it. The
executable is the one place every browser launch passes, whoever asked for it.
After its slot, the wrapper also waits for memory (eki/memadmit.py's rule).

The mirror: every entry of real() is a symlink, except the browser folders
(chromium-*, chromium_headless_shell-*, chrome-*, firefox-*, webkit-*). In
those, the directories down to each browser executable are real, everything
beside them is a symlink, and the executable itself is a wrapper (WRAPPER):
a standalone script that takes a browser slot and then execs the real binary,
so the browser keeps the pid and the slot until it exits.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Set

from . import browserslots, paths

log = logging.getLogger("eki.browsershim")

#: top-level folders that hold a browser
BROWSER_PREFIXES = ("chromium", "chromium_headless_shell", "chrome", "firefox", "webkit")
#: the names a browser executable goes by
EXECUTABLES = {"chrome-headless-shell", "Chromium", "Google Chrome for Testing", "chrome",
               "headless_shell", "firefox", "pwebkit"}
#: in each browser folder the shim builds; a browser folder without it is an install
MARK = ".eki-shim"
#: Playwright's install lock in the browsers folder: an install is under way
INSTALL_LOCK = "__dirlock"

WRAPPER = '''#!{python} -I
"""eki's browser shim: wait for a free browser slot and for memory, then become the
real browser. Settings -> Machine sets how many may be open and the memory floor;
the rule is eki/memadmit.py's, kept here: this script stands alone."""
import fcntl, json, os, random, re, sqlite3, subprocess, sys, time, urllib.parse

REAL = {real!r}
SLOTS = {slots!r}
ROUTING = {routing!r}
DB = {db!r}
COUNT, DEFAULT, MOST = 32, 3, 16
ESTIMATE, GRACE = 1.1, 60


def setting(key, default, most):
    try:
        v = json.load(open(ROUTING))["machine"][key]
        if isinstance(v, bool):
            return default
        return min(most, max(1, int(v)))
    except Exception:
        return default


def cap():
    return setting("browsers", DEFAULT, MOST)


def locked(i):
    """The slot's mtime when someone holds it, else None."""
    path = os.path.join(SLOTS, "%d.lock" % i)
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return None
    except OSError:
        return os.fstat(fd).st_mtime
    finally:
        os.close(fd)


def held():
    return sum(locked(i) is not None for i in range(COUNT))


def take(limit):
    """(index, fd) of a slot, or None when none is free or taking one puts us over limit."""
    for i in range(COUNT):
        try:
            fd = os.open(os.path.join(SLOTS, "%d.lock" % i), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            continue
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        if held() > limit:                    # someone else took one at the same moment
            os.close(fd)
            return None
        return i, fd
    return None


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def free_gb():
    """free + inactive + purgeable + speculative pages, in GB; EKI_FREE_GB overrides."""
    if os.environ.get("EKI_FREE_GB"):
        return float(os.environ["EKI_FREE_GB"])
    size, pages = 4096, 0
    for line in run("vm_stat").splitlines():
        if "page size of" in line:
            size = int(re.sub(r"\\D", "", line.split("page size of")[1]) or 4096)
        elif line.startswith(("Pages free:", "Pages inactive:", "Pages purgeable:",
                              "Pages speculative:")):
            pages += int(re.sub(r"\\D", "", line.split(":")[1]) or 0)
    return pages * size / 2.0 ** 30 if pages else float("inf")


def swap_pct():
    """Percent of swap in use, from sysctl vm.swapusage; EKI_SWAP_PCT overrides."""
    if os.environ.get("EKI_SWAP_PCT"):
        return int(float(os.environ["EKI_SWAP_PCT"]))
    unit = {{"": 1.0, "K": 1 / 1024.0, "M": 1.0, "G": 1024.0, "T": 1024.0 ** 2}}
    got = {{m.group(1): float(m.group(2)) * unit[m.group(3)]
           for m in re.finditer(r"(total|used)\\s*=\\s*([\\d.]+)([KMGT]?)",
                                run("sysctl", "-n", "vm.swapusage"))}}
    if not got.get("total"):
        return 0
    return int(round(100 * got.get("used", 0.0) / got["total"]))


def estimate():
    """The browser's learned GB in the database at DB, read-only; else ESTIMATE."""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(DB), uri=True, timeout=2)
        try:
            row = conn.execute("SELECT gb FROM mem_estimates WHERE kind='browser'").fetchone()
        finally:
            conn.close()
        return float(row[0]) if row else ESTIMATE
    except Exception:
        return ESTIMATE


def memory(own):
    """(admitted, free GB, swap %): a browser fits above the floor, counting each other
    slot taken under GRACE seconds ago as one more browser that hasn't grown yet."""
    free, swap, each = free_gb(), swap_pct(), estimate()
    now = time.time()
    fresh = 0
    for i in range(COUNT):
        if i != own:
            at = locked(i)
            fresh += at is not None and now - at < GRACE
    ok = (swap <= setting("swap_max", 60, 100)
          and free - fresh * each - each >= setting("floor_gb", 6, 64))
    return ok, free, swap


def main():
    try:
        os.makedirs(SLOTS, exist_ok=True)
    except OSError:
        pass
    said = told = False
    while True:
        limit = cap()
        now = held()
        if now < limit:
            ok, free, swap = memory(None)     # plainly short: don't even take a slot
            got = take(limit) if ok else None
            if got is not None:
                ok, free, swap = memory(got[0])
                if ok:
                    fd = got[1]
                    break
                os.close(got[1])
            if not ok and not told:
                sys.stderr.write("eki: waiting for memory (%.1f GB free, swap %d%%) \\u2014 "
                                 "Settings \\u2192 Machine sets the floor; "
                                 "Playwright's launch timeout still counts\\n" % (free, swap))
                sys.stderr.flush()
                told = True
        elif not said:
            sys.stderr.write("eki: waiting for a browser slot \\u2014 %d of %d browsers open "
                             "(Settings \\u2192 Machine \\u2192 Browsers at once); "
                             "Playwright's launch timeout still counts\\n" % (now, limit))
            sys.stderr.flush()
            said = True
        time.sleep(0.5 + random.random() * 0.5)
    os.set_inheritable(fd, True)              # the browser holds the slot until it exits
    try:
        os.execv(REAL, [REAL] + sys.argv[1:])
    except OSError as e:
        sys.stderr.write("eki: can't start %s: %s\\n" % (REAL, e))
        sys.exit(127)


main()
'''


def mirror() -> Path:
    return paths.home() / "browsers" / "playwright"


def real() -> Path:
    """Playwright's own browsers folder: $EKI_PLAYWRIGHT_CACHE, else eki's own
    $PLAYWRIGHT_BROWSERS_PATH unless it is the mirror, else the default cache."""
    given = os.environ.get("EKI_PLAYWRIGHT_CACHE")
    if given:
        return Path(given).expanduser()
    given = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if given and given != "0" and Path(given).expanduser() != mirror():
        return Path(given).expanduser()
    return paths.user_home() / "Library/Caches/ms-playwright"


def _stamp_file() -> Path:
    return paths.home() / "browsers" / "playwright.stamp.json"


def _is_browser(name: str) -> bool:
    return name.startswith(BROWSER_PREFIXES)


def _bake() -> List[str]:
    """What the wrappers have baked in; a change rebuilds them."""
    return [sys.executable, str(browserslots.folder()), str(paths.config("routing")),
            str(paths.db()), str(len(WRAPPER))]


def _entries(folder: Path) -> Dict[str, int]:
    """Top-level names in folder and their mtimes (of the entry itself)."""
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        try:
            out[name] = os.lstat(folder / name).st_mtime_ns
        except OSError:
            pass
    return out


def _stamp(src: Path, dst: Path) -> dict:
    return {"real": str(src), "entries": _entries(src), "mirror": sorted(_entries(dst)),
            "bake": _bake()}


def _wrapper(target: Path) -> str:
    return WRAPPER.format(python=sys.executable, real=str(target),
                          slots=str(browserslots.folder()),
                          routing=str(paths.config("routing")), db=str(paths.db()))


def executables(folder: Path) -> List[Path]:
    """The browser executables in a browser folder, not looking into *.framework."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.endswith(".framework")]
        for name in files:
            p = Path(root) / name
            if name in EXECUTABLES and not p.is_symlink() and p.is_file() \
                    and os.access(p, os.X_OK):
                out.append(p)
    return out


def _remove(p: Path) -> None:
    if p.is_symlink() or not p.is_dir():
        p.unlink()
    else:
        shutil.rmtree(p)


def _build(src: Path, dst: Path, exes: Set[Path], down: Set[Path]) -> None:
    """dst as a copy of src's shape: wrappers for exes, real folders for the
    directories on the way down to one, symlinks for everything else."""
    dst.mkdir()
    for entry in sorted(os.listdir(src)):
        s, d = src / entry, dst / entry
        if s in exes:
            d.write_text(_wrapper(s))
            d.chmod(0o755)
        elif s in down:
            _build(s, d, exes, down)
        else:
            d.symlink_to(s)


def _mirror_browser(src: Path, dst: Path) -> None:
    """Build dst for the browser folder src beside it, then swap it in."""
    exes = set(executables(src))
    down = {parent for e in exes for parent in e.parents if src in parent.parents}
    tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}")
    if tmp.exists() or tmp.is_symlink():
        _remove(tmp)
    _build(src, tmp, exes, down)
    (tmp / MARK).write_text(f"{src}\n")
    old = dst.with_name(f".{dst.name}.old-{os.getpid()}")
    if dst.exists() or dst.is_symlink():
        os.rename(dst, old)
    os.rename(tmp, dst)
    if old.exists() or old.is_symlink():
        _remove(old)


def _adopt(src: Path, dst: Path) -> None:
    """Move browser folders an install put straight into the mirror into Playwright's folder."""
    if (dst / INSTALL_LOCK).exists():
        return                      # an install is under way; next time
    try:
        names = os.listdir(dst)
    except OSError:
        return
    for name in names:
        p = dst / name
        if not _is_browser(name) or p.is_symlink() or not p.is_dir() or (p / MARK).exists():
            continue
        target = src / name
        if target.exists():
            log.info("browser shim: %s is already in %s; dropping the copy in the mirror",
                     name, src)
            shutil.rmtree(p)
        else:
            src.mkdir(parents=True, exist_ok=True)
            log.info("browser shim: moving %s from the mirror into %s", name, src)
            shutil.move(str(p), str(target))


def _sync(src: Path, dst: Path, before: dict) -> None:
    """Make dst follow src; before is the last stamp, to rebuild only what changed."""
    have = _entries(src)
    rebuild_all = before.get("bake") != _bake() or before.get("real") != str(src)
    old = before.get("entries", {}) if not rebuild_all else {}
    for name in os.listdir(dst):
        p = dst / name
        if name.startswith(".") and (".tmp-" in name or ".old-" in name):
            _remove(p)                                  # left by a crash mid-swap
        elif name not in have and (p.is_symlink() or (p / MARK).exists()):
            _remove(p)                                  # its source is gone
    for name, mtime in sorted(have.items()):
        if name == INSTALL_LOCK:
            continue
        s, d = src / name, dst / name
        try:
            if _is_browser(name) and s.is_dir():
                if old.get(name) != mtime or not (d / MARK).exists():
                    _mirror_browser(s, d)
            elif not d.is_symlink() and not d.exists():
                d.symlink_to(s)
            elif d.is_symlink() and os.readlink(d) != str(s):
                d.unlink()
                d.symlink_to(s)
        except OSError as e:
            log.warning("browser shim: can't mirror %s: %s", s, e)


def ensure() -> Path:
    """Make mirror() follow real(), cheaply when nothing changed. Never raises:
    a problem is logged and the mirror is returned as it stands."""
    dst = mirror()
    try:
        dst.mkdir(parents=True, exist_ok=True)
        src = real()
        _adopt(src, dst)
        try:
            before = json.loads(_stamp_file().read_text())
        except (OSError, ValueError):
            before = {}
        if before == _stamp(src, dst):
            return dst
        _sync(src, dst, before)
        _stamp_file().write_text(json.dumps(_stamp(src, dst)))
    except Exception as e:                              # never fail a caller over the shim
        log.warning("browser shim: %s", e)
    return dst
