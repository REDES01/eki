#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Fetch a self-contained CPython and install eki's dependencies into it.
#
# The app ships its own interpreter so that installing eki is dragging one
# thing into /Applications — no system Python, no Homebrew, no venv, and
# nothing that breaks when macOS replaces python3 out from under it. Apple's
# developer support recommends exactly this shape: the interpreter lives in
# Contents/Helpers, signed as part of the bundle.
#
# Output: mac/build/python (an install_only CPython with eki's requirements).
# Re-running is cheap; it only refetches when the version changes.
set -euo pipefail
cd "$(dirname "$0")"

PY_VERSION="${EKI_PY_VERSION:-3.12}"
ARCH="$(uname -m)"          # arm64 → aarch64 in python-build-standalone's names
[ "$ARCH" = "arm64" ] && ARCH=aarch64
BUILD="build"
TARGET="$BUILD/python"
STAMP="$BUILD/python.version"

mkdir -p "$BUILD"

echo "looking up the latest python-build-standalone…"
read -r URL NAME <<<"$(curl -sL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
  | ARCH="$ARCH" PY_VERSION="$PY_VERSION" python3 -c '
import json, os, sys
release = json.load(sys.stdin)
want = "cpython-" + os.environ["PY_VERSION"] + "."
tail = os.environ["ARCH"] + "-apple-darwin-install_only_stripped.tar.gz"
hit = next((a for a in release["assets"]
            if a["name"].startswith(want) and a["name"].endswith(tail)), None)
if hit is None:
    sys.exit("no " + want + "* build for " + os.environ["ARCH"] + " in " + release["tag_name"])
print(hit["browser_download_url"], hit["name"])
')"

if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$NAME" ] && [ -x "$TARGET/bin/python3" ]; then
  echo "already have $NAME"
else
  echo "fetching ${NAME}…"
  rm -rf "$TARGET" "$BUILD/python.tar.gz"
  curl -fL --progress-bar -o "$BUILD/python.tar.gz" "$URL"
  mkdir -p "$TARGET"
  tar -xzf "$BUILD/python.tar.gz" -C "$TARGET" --strip-components=1
  rm -f "$BUILD/python.tar.gz"
  echo "$NAME" > "$STAMP"
fi

echo "installing eki's dependencies…"
"$TARGET/bin/python3" -m pip install --quiet --upgrade pip
"$TARGET/bin/python3" -m pip install --quiet -r ../requirements.txt

# Trim what a running engine never needs. Tests, pip and caches are most of
# the weight and none of the use.
find "$TARGET" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$TARGET" -name "*.pyc" -delete 2>/dev/null || true
rm -rf "$TARGET/lib/python$PY_VERSION/test" "$TARGET/lib/python$PY_VERSION/idlelib" \
       "$TARGET/lib/python$PY_VERSION/tkinter" "$TARGET/lib/python$PY_VERSION/turtledemo" \
       "$TARGET/share" 2>/dev/null || true
# codesign reads include/python3.12 and the build config as nested bundles and
# refuses the whole app. Nothing at runtime needs them — they are for building
# C extensions, which a shipped app does not do.
rm -rf "$TARGET/include" "$TARGET/lib/pkgconfig" \
       "$TARGET/lib/python$PY_VERSION/config-$PY_VERSION-darwin" 2>/dev/null || true
# Tcl/Tk ships with the standalone build; their versioned folders look like
# bundles to codesign, which then refuses the app. Nothing here draws a Tk
# window, so they go.
rm -rf "$TARGET"/lib/tcl* "$TARGET"/lib/tk* "$TARGET"/lib/itcl* "$TARGET"/lib/libtcl* \
       "$TARGET"/lib/libtk* "$TARGET"/lib/thread* 2>/dev/null || true
rm -f "$TARGET/lib/python$PY_VERSION/lib-dynload/_tkinter"*.so 2>/dev/null || true

echo "bundled python: $("$TARGET/bin/python3" -V) ($(du -sh "$TARGET" | cut -f1))"
