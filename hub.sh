#!/usr/bin/env bash
# hub launcher: venv on first run, then the CLI.
set -euo pipefail
cd "$(dirname "$0")"
PY="${HUB_PYTHON:-python3}"
venv_ok() { [ -x .venv/bin/python ] && .venv/bin/python -c "import sys" >/dev/null 2>&1; }
if [ -d .venv ] && ! venv_ok; then rm -rf .venv; fi
[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install -q -r requirements.txt
exec ./.venv/bin/python -m hub.cli "$@"
