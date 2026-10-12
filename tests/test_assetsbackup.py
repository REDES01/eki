"""The media pool's backup: new and changed files are copied, nothing is ever deleted."""
import json
import os

import pytest

from eki import components, db, housekeep
from eki.components import assets, assetsbackup


@pytest.fixture(autouse=True)
def fresh():
    assetsbackup._last[0] = 0.0
    yield
    t = assetsbackup._thread[0]
    if t is not None:
        t.join(10)
    assetsbackup._thread[0] = None


def put(root, spec):
    d = root / "components" / "art"
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps(spec))
    return components.get("art", root)


def card(proj, backup):
    return put(proj, {"kind": "assets", "about": "art", "path": "assets", "backup": str(backup)})


def test_new_file_copied_once_then_replaced_and_kept(proj, tmp_path):
    comp = card(proj, tmp_path / "drive")
    pool = assets.pool(comp)
    (pool / "sfx").mkdir()
    (pool / "sfx" / "hit.wav").write_bytes(b"one")
    copy = tmp_path / "drive" / proj.name / "assets" / "sfx" / "hit.wav"
    assert assetsbackup.backup(comp) == 1
    assert copy.read_bytes() == b"one"
    assert copy.stat().st_mtime == pytest.approx((pool / "sfx" / "hit.wav").stat().st_mtime, abs=0.01)
    assert assetsbackup.backup(comp) == 0
    (pool / "sfx" / "hit.wav").write_bytes(b"two, longer")
    later = (pool / "sfx" / "hit.wav").stat().st_mtime + 5
    os.utime(pool / "sfx" / "hit.wav", (later, later))
    assert assetsbackup.backup(comp) == 1
    assert copy.read_bytes() == b"two, longer"
    assert not [p for p in copy.parent.iterdir() if p.name != "hit.wav"]     # no temp left behind
    (pool / "sfx" / "hit.wav").unlink()
    assert assetsbackup.backup(comp) == 0
    assert copy.read_bytes() == b"two, longer"                             # the undo for media


def test_symlinks_not_followed(proj, tmp_path):
    comp = card(proj, tmp_path / "drive")
    (tmp_path / "elsewhere.txt").write_text("not the pool's")
    os.symlink(tmp_path / "elsewhere.txt", assets.pool(comp) / "link.txt")
    assert assetsbackup.backup(comp) == 0


def test_unreachable_target(proj, tmp_path):
    gone = tmp_path / "Volumes" / "Drive" / "eki"
    comp = card(proj, gone)
    (assets.pool(comp) / "a.png").write_bytes(b"abc")
    with pytest.raises(OSError, match="backup target not reachable"):
        assetsbackup.backup(comp)
    assert not (tmp_path / "Volumes").exists()


def test_notes_and_validate(proj, tmp_path):
    comp = put(proj, {"kind": "assets", "about": "art", "path": "assets"})
    (proj / ".gitignore").write_text("assets/\n")
    assert assets.notes(comp)[0] == assets.NO_HISTORY
    assert assets.notes(comp)[0].startswith("No version history: the pool is kept out of git")
    comp = card(proj, tmp_path / "drive")
    assert assets.notes(comp)[0] == f"No version history in git; backed up to {tmp_path / 'drive'}"
    spec = {"kind": "assets", "path": "assets"}
    assert assets.validate(spec, proj) == []
    assert assets.validate({**spec, "backup": ""}, proj) == []
    assert assets.validate({**spec, "backup": str(tmp_path)}, proj) == []
    assert assets.validate({**spec, "backup": "backups/art"}, proj) == ["backup must be an absolute folder path"]
    assert assets.validate({**spec, "backup": 3}, proj) == ["backup must be an absolute folder path"]


def test_pool_component_goes_under_its_name(tmp_path):
    d = components.pool_dir() / "shared"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "assets", "about": "x", "path": "files",
                                                  "backup": str(tmp_path / "drive")}))
    (d / "files").mkdir()
    (d / "files" / "a.png").write_bytes(b"abc")
    assert assetsbackup.run([]) == 1
    assert (tmp_path / "drive" / "shared" / "a.png").read_bytes() == b"abc"


def test_housekeeping_backs_up_on_a_thread(proj, tmp_path):
    comp = card(proj, tmp_path / "drive")
    (assets.pool(comp) / "a.png").write_bytes(b"abc")
    conn = db.connect()
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at) VALUES ('p1','proj',?,'trunk',0)",
                 (str(proj),))
    assert ("poolbackup", assetsbackup.tick) in housekeep.STEPS
    assetsbackup.tick(conn)
    assetsbackup._thread[0].join(10)
    assert (tmp_path / "drive" / proj.name / "assets" / "a.png").read_bytes() == b"abc"
    assert assetsbackup.tick(conn) == [] and assetsbackup._thread[0] is not None
