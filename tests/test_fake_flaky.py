import json
import os
import subprocess
import sys
from pathlib import Path

from eki import providers
from eki.providers.base import Turn

ROOT = str(Path(__file__).resolve().parents[1])


def start(home, prompt, resume=None):
    argv = [sys.executable, "-m", "eki.providers.fake", "--name", "fake"]
    if resume:
        argv += ["--resume", resume]
    env = {**os.environ, "EKI_HOME": str(home), "PYTHONPATH": ROOT}
    p = subprocess.run(argv + [prompt], capture_output=True, text=True, env=env, cwd=home, timeout=30)
    events = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
    return p.returncode, events


def test_flaky_fails_once_then_resumes(home):
    code, events = start(home, "flaky steps=1 hi")
    assert code == 1
    sid = events[0]["id"]
    assert events[0]["type"] == "session"
    assert events[1] == {"type": "error", "message": "API Error: Connection dropped (ECONNRESET)"}
    assert not any(e["type"] == "text" for e in events)
    code, events = start(home, "carry on", resume=sid)
    assert code == 0
    assert events[0] == {"type": "session", "id": sid}
    assert any(e["type"] == "text" and e["text"].startswith("fake done") for e in events)
    assert events[-1] == {"type": "done"}


def test_flaky_n_fails_n_times(home):
    code, events = start(home, "flaky=2 steps=1 hi")
    assert code == 1
    sid = events[0]["id"]
    code, events = start(home, "carry on", resume=sid)
    assert code == 1 and events[1]["type"] == "error"
    code, events = start(home, "carry on", resume=sid)
    assert code == 0 and events[-1] == {"type": "done"}


def test_fresh_session_starts_from_zero(home):
    start(home, "flaky steps=1 hi")
    code, events = start(home, "flaky steps=1 hi")
    assert code == 1 and events[1]["type"] == "error"


def test_flaky_through_the_provider(home):
    out = providers.get("fake").take(Turn(prompt="flaky steps=1 hi", history=[]), lambda k, d: None)
    assert out.state == "failed"
    assert "ECONNRESET" in out.error
