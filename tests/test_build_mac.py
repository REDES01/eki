"""bin/build-mac builds where EKI_APP_OUT says and records EkiRoot and EkiCommit.

Never runs swiftc: the source is checked, and the part of the script that
works out the three settings is run on its own."""
import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "build-mac"


def src():
    return SCRIPT.read_text()


def test_script_parses():
    subprocess.run(["sh", "-n", str(SCRIPT)], check=True)


def test_names_the_settings_and_the_plist_key():
    s = src()
    for name in ("EKI_APP_OUT", "EKI_APP_ROOT", "EKI_APP_COMMIT"):
        assert name in s
    assert "<key>EkiCommit</key><string>$commit</string>" in s
    assert "<key>EkiRoot</key><string>$root</string>" in s
    assert 'root="${EKI_APP_ROOT:-$here}"' in s
    assert 'app="${EKI_APP_OUT:-$here/Eki.app}"' in s
    assert ".eki-build.json" in s
    assert 'lsregister -f "$app"' in s and 'echo "built $app"' in s


def _settings(tmp_path, env=None, info=None):
    """Run the script up to the build itself, from a copy in a fake checkout."""
    here = tmp_path / "checkout"
    (here / "bin").mkdir(parents=True)
    prelude = src().split('rm -rf "$app"')[0]
    (here / "bin" / "build-mac").write_text(prelude + 'echo "$app|$root|$commit"\n')
    if info is not None:
        (here / ".eki-build.json").write_text(json.dumps(info))
    e = {k: v for k, v in os.environ.items() if not k.startswith("EKI_APP_")}
    e.update(env or {})
    out = subprocess.run(["sh", str(here / "bin" / "build-mac")], env=e, check=True,
                         capture_output=True, text=True).stdout.strip()
    return here.resolve(), out.split("|")


def test_defaults_are_as_by_hand(tmp_path):
    here, (app, root, commit) = _settings(tmp_path)
    assert app == f"{here}/Eki.app"
    assert root == str(here)
    assert commit == ""          # no build info, not a git checkout


def test_commit_comes_from_the_build_info(tmp_path):
    sha = "a" * 40
    _, (_, _, commit) = _settings(tmp_path, info={"id": "7", "commit": sha, "source": "/x"})
    assert commit == sha


def test_worktree_build_has_no_commit(tmp_path):
    _, (_, _, commit) = _settings(tmp_path, info={"id": "7", "commit": "worktree", "source": "/x"})
    assert commit == ""


def test_settings_win(tmp_path):
    env = {"EKI_APP_OUT": "/tmp/x/Eki.app", "EKI_APP_ROOT": "/src", "EKI_APP_COMMIT": "b" * 40}
    _, got = _settings(tmp_path, env=env, info={"commit": "a" * 40})
    assert got == ["/tmp/x/Eki.app", "/src", "b" * 40]
