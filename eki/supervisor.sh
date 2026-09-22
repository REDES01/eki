#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# eki's supervisor: moves the engine onto another build, watches it, and
# moves it back if it isn't healthy (docs/self-build.md).
#
# A person installs this, once (`eki agent install` copies it to
# ~/.eki/bin/eki-supervisor). eki may propose changes to this file but never
# installs them itself, and nothing in here imports eki — so no change to eki
# can break the way back.
#
#   eki-supervisor <build-dir> [wait-seconds] [watch-seconds] [self-id]
#
# 1. waits (up to wait-seconds) for the engine to have no runs going
# 2. repoints ~/.eki/builds/current (the old one becomes `previous`) and
#    restarts the engine through launchd
# 3. the engine must answer /api/health as the new build within a minute,
#    and still be the same process watch-seconds later
# 4. otherwise the old build goes back, and the engine is restarted on it
# The outcome is ~/.eki/self/swap.json; the story is ~/.eki/self/swap.log.
set -u
TARGET="${1:?usage: eki-supervisor <build-dir> [wait] [watch] [self-id]}"
WAIT="${2:-600}"
WATCH="${3:-180}"
SELF="${4:-}"
B="${EKI_BUILDS:-$HOME/.eki/builds}"
S="${EKI_SELF_HOME:-$HOME/.eki/self}"
URL="${EKI_HEALTH_URL:-http://127.0.0.1:8787/api/health}"
JOB="${EKI_JOB:-gui/$(id -u)/local.eki.engine}"
LAUNCHCTL="${EKI_LAUNCHCTL:-launchctl}"
UP="${EKI_UP_SECONDS:-60}"
mkdir -p "$S"

say() { echo "$(date '+%F %T') $*" >> "$S/swap.log"; }
record() {
    printf '{"state": "%s", "target": "%s", "previous": "%s", "self": "%s", "why": "%s", "at": %s}\n' \
        "$1" "$TARGET" "${OLD:-}" "$SELF" "$2" "$(date +%s)" > "$S/swap.json.tmp"
    mv -f "$S/swap.json.tmp" "$S/swap.json"
}
health() { curl -s -m 3 "$URL" 2>/dev/null; }
pid() { "$LAUNCHCTL" print "$JOB" 2>/dev/null | awk '$1 == "pid" && $2 == "=" {print $3; exit}'; }
point() { ln -sfh "$1" "$2" 2>/dev/null || ln -sfn "$1" "$2"; }

if ! mkdir "$S/swap.lockdir" 2>/dev/null; then
    say "another swap is in progress; not starting one for $TARGET"
    exit 3
fi
trap 'rmdir "$S/swap.lockdir" 2>/dev/null' EXIT

if [ ! -d "$TARGET" ]; then
    say "no build at $TARGET"
    record failed "no build there"
    exit 1
fi
WANT=$(sed -n 's/.*"id": *"\([^"]*\)".*/\1/p' "$TARGET/.eki-build.json" 2>/dev/null | head -1)
[ -n "$WANT" ] || WANT=dev

# 1. let what's running finish
t=0
while [ "$t" -lt "$WAIT" ]; do
    h=$(health)
    case "$h" in
        ""|*'"running":[]'*|*'"running": []'*) break ;;
    esac
    sleep 5
    t=$((t + 5))
done
[ "$t" -ge "$WAIT" ] && say "runs still going after ${WAIT}s; swapping anyway (the new engine resumes them)"

# 2. swap
OLD=$(readlink "$B/current")
point "$OLD" "$B/previous"
point "$TARGET" "$B/current"
say "swap: $OLD -> $TARGET (build $WANT)"
record swapping ""
"$LAUNCHCTL" kickstart -k "$JOB" >/dev/null 2>&1

# 3. up as the new build, and staying up
up=""
i=0
while [ "$i" -lt "$UP" ]; do
    sleep 1
    i=$((i + 1))
    case "$(health)" in
        *"\"build\":\"$WANT\""*|*"\"build\": \"$WANT\""*) up=1; break ;;
    esac
done
if [ -n "$up" ]; then
    first=$(pid)
    sleep "$WATCH"
    case "$(health)" in
        *"\"build\":\"$WANT\""*|*"\"build\": \"$WANT\""*) still=1 ;;
        *) still="" ;;
    esac
    if [ -n "$still" ] && [ "$(pid)" = "$first" ]; then
        say "healthy on $WANT after ${WATCH}s"
        record healthy ""
        exit 0
    fi
    why="it stopped or was restarted within ${WATCH}s"
else
    why="it didn't come up as build $WANT within ${UP}s"
fi

# 4. back
point "$OLD" "$B/current"
point "$TARGET" "$B/previous"
"$LAUNCHCTL" kickstart -k "$JOB" >/dev/null 2>&1
say "rolled back to $OLD: $why"
record "rolled back" "$why"
exit 2
