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
# 1. waits (up to wait-seconds, 2 min by default) for a moment with no runs
#    going — work isn't paused for it; runs still going then are cut off and
#    the new engine carries them on
# 2. repoints ~/.eki/builds/current (the old one becomes `previous`) and
#    restarts the engine through launchd
# 3. the engine must answer /api/health as the new build within a minute,
#    and still be the same process watch-seconds later
# 4. otherwise the old build goes back, and the engine is restarted on it
# The outcome is ~/.eki/self/swap.json; the story is ~/.eki/self/swap.log.
# After every restart it makes sure the old engine is gone: one left behind
# holds the port, and every new engine dies with "address already in use"
# (2026-09-24, 22:46–22:56).
#
#   eki-supervisor --watchdog
#
# One look, every 30 seconds (launchd's local.eki.watchdog, eki/agent.py): an
# engine that doesn't answer /api/health within 10 seconds, and was already
# there the look before (not one just starting), is noted in swap.log and
# restarted.
set -u
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
# the engine's own pid (not its launcher's): what /api/health says, or — for
# one too stuck to answer — whoever listens on its port
engine_pid() {
    local p port
    p=$(curl -s -m 3 "$URL" 2>/dev/null | sed -n 's/.*"pid": *\([0-9][0-9]*\).*/\1/p')
    if [ -z "$p" ]; then
        port=$(echo "$URL" | sed -n 's#^[a-z]*://[^/:]*:\([0-9][0-9]*\).*#\1#p')
        [ -n "$port" ] && p=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
    fi
    echo "$p"
}
is_engine() { ps -o command= -p "$1" 2>/dev/null | grep -Eq 'eki\.cli .*serve|eki\.service'; }
# the engine before a restart is gone — stopped here if it was left behind
gone() {
    local old="$1" n=0
    [ -n "$old" ] || return 0
    while [ "$n" -lt "${EKI_GONE_SECONDS:-30}" ] && kill -0 "$old" 2>/dev/null; do
        sleep 1
        n=$((n + 1))
    done
    kill -0 "$old" 2>/dev/null || return 0
    is_engine "$old" || return 0
    say "the old engine (pid $old) outlived the restart; stopping it"
    kill -TERM "$old" 2>/dev/null
    sleep 5
    kill -KILL "$old" 2>/dev/null
    return 0
}
restart() {
    local old
    old=$(engine_pid)
    "$LAUNCHCTL" kickstart -k "$JOB" >/dev/null 2>&1
    gone "$old"
}

if [ "${1:-}" = "--watchdog" ]; then
    now=$(pid)
    [ -n "$now" ] || exit 0                        # not running: launchd starts it
    [ -d "$S/swap.lockdir" ] && exit 0             # a swap watches for itself
    before=$(cat "$S/watchdog.pid" 2>/dev/null)
    echo "$now" > "$S/watchdog.pid"
    curl -s -f -m "${EKI_WATCHDOG_SECONDS:-10}" "$URL" >/dev/null 2>&1 && exit 0
    [ "$before" = "$now" ] || exit 0               # just started: give it a look more
    say "watchdog: the engine (pid $now) didn't answer /api/health in ${EKI_WATCHDOG_SECONDS:-10}s; restarting it"
    restart
    rm -f "$S/watchdog.pid"
    exit 0
fi

TARGET="${1:?usage: eki-supervisor <build-dir> [wait] [watch] [self-id] | --watchdog}"
WAIT="${2:-120}"
WATCH="${3:-180}"
SELF="${4:-}"

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

# 1. a quiet moment, if one comes soon
quiet() {
    case "$(health)" in
        ""|*'"running":[]'*|*'"running": []'*) return 0 ;;
    esac
    return 1
}
t=0
while [ "$t" -lt "$WAIT" ] && ! quiet; do
    sleep 5
    t=$((t + 5))
done
quiet || say "runs still going after ${t}s; swapping anyway (the new engine carries them on)"
# from here on it isn't superseded: a newer swap waits for this one to finish
trap '' TERM

# 2. swap
OLD=$(readlink "$B/current")
point "$OLD" "$B/previous"
point "$TARGET" "$B/current"
say "swap: $OLD -> $TARGET (build $WANT)"
record swapping ""
restart

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
restart
say "rolled back to $OLD: $why"
record "rolled back" "$why"
exit 2
