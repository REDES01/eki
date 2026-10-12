"""Browser slots and the shim's folders: held locks and their pids, the cap,
where Playwright's own browsers are and where the mirror lives."""
import json
import subprocess
import sys

from eki import browsershim, browserslots

HOLD = """
import fcntl, os, sys, time
fds = []
for p in sys.argv[1:]:
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    fds.append(fd)
print("held", flush=True)
sys.stdin.read()
"""


def hold(*names):
    paths = [str(browserslots.folder() / n) for n in names]
    child = subprocess.Popen([sys.executable, "-c", HOLD, *paths],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


def test_folder_is_under_the_test_home(home):
    assert browserslots.folder() == home / "browsers" / "slots"
    assert browserslots.folder().is_dir()


def test_limit_follows_machine_browsers(home):
    assert browserslots.limit() == 3
    data = json.loads((home / "routing.json").read_text())
    data["machine"] = {"browsers": 7}
    (home / "routing.json").write_text(json.dumps(data))
    assert browserslots.limit() == 7


def test_held_and_pids_follow_a_child_holding_locks(home):
    assert browserslots.held() == 0 and browserslots.pids() == set()
    (browserslots.folder() / "5.lock").write_text("12345")          # left behind, nobody holds it
    child = hold("0.lock", "3.lock")
    try:
        assert browserslots.held() == 2
        assert browserslots.pids() == {child.pid}
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    assert browserslots.held() == 0 and browserslots.pids() == set()


def test_only_slot_names_count(home):
    child = hold("other.lock", "32.lock")
    try:
        assert browserslots.held() == 0
    finally:
        child.stdin.close()
        child.wait(timeout=10)


def test_mirror_and_real_stay_in_the_test_home(home, tmp_path, monkeypatch):
    monkeypatch.delenv("EKI_PLAYWRIGHT_CACHE", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    assert browsershim.mirror() == home / "browsers" / "playwright"
    assert browsershim.real() == tmp_path / "user" / "Library/Caches/ms-playwright"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsershim.mirror()))
    assert browsershim.real() == tmp_path / "user" / "Library/Caches/ms-playwright"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "pw"))
    assert browsershim.real() == tmp_path / "pw"
    monkeypatch.setenv("EKI_PLAYWRIGHT_CACHE", str(tmp_path / "cache"))
    assert browsershim.real() == tmp_path / "cache"


def test_ensure_makes_the_mirror(home):
    assert not browsershim.mirror().exists()
    assert browsershim.ensure() == browsershim.mirror()
    assert browsershim.mirror().is_dir()
