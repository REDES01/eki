"""The assets kind: a managed folder outside git, seen by every worktree by symlink."""
import json
import os

from eki import components, paths, workspace
from eki.components import Call, assets


def put_assets(root, name="art", path="assets"):
    d = root / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "assets", "about": "art", "path": path}))
    return d


def use(comp, *args):
    return assets.call(comp, Call(args=list(args), caller_dir="", caller_run=None, run_id="r",
                                  emit=lambda *_: None, extra={}))


def test_worktree_sees_pool_by_symlink(proj):
    put_assets(proj, path="media/assets")
    (proj / ".gitignore").write_text("media/assets/\n")
    wt = workspace.add(proj, "k1")
    seen = wt / "media" / "assets"
    assert os.path.islink(seen)
    pool = proj.resolve() / "media" / "assets"
    assert pool.is_dir() and not pool.is_symlink()
    (seen / "card.png").write_bytes(b"png")
    assert (pool / "card.png").read_bytes() == b"png"          # landed in the pool at once
    assert os.stat(pool / "card.png").st_nlink == 1             # not a hardlink
    # the link is never part of a change
    assert "media/assets" not in workspace.changed(wt, "HEAD")
    assert workspace.stage_all(wt) == []
    # a second add (already there) keeps it; a fresh link is left as it is
    assert assets.link(wt) == []


def test_existing_path_left_alone(proj):
    put_assets(proj)
    (proj / ".gitignore").write_text("assets/\n")
    wt = workspace.add(proj, "k2")
    os.unlink(wt / "assets")
    (wt / "assets").mkdir()                                     # something real in the way
    assert not os.path.islink(wt / "assets")
    assert "left alone" in assets.link(wt)[0]


def test_tracked_pool_reported_and_not_linked(proj):
    put_assets(proj)
    (proj / "assets").mkdir()
    for n in ("a.png", "b.png", "c.png"):
        (proj / "assets" / n).write_text(n)
    workspace.git(proj, "add", "assets")
    workspace.git(proj, "commit", "-q", "-m", "tracked assets")
    (proj / ".gitignore").write_text("assets/\n")              # ignored now, but still tracked
    comp = components.get("art", proj)
    said = "assets/ has 3 tracked files in git (e.g. assets/a.png); not linked into worktrees: untrack them first"
    assert said in assets.notes(comp)
    wt = workspace.add(proj, "k4")
    assert not os.path.islink(wt / "assets")                    # the normal checkout
    assert sorted(p.name for p in (wt / "assets").iterdir()) == ["a.png", "b.png", "c.png"]
    assert assets.link(wt) == [f"art: {said}"]


def test_not_ignored_pool_not_linked(proj):
    put_assets(proj)
    wt = workspace.add(proj, "k5")
    assert not (wt / "assets").exists() and not os.path.islink(wt / "assets")
    assert assets.link(wt) == [f"art: assets/ is not git-ignored in {proj.resolve()}; not linked into worktrees"]


def test_pool_size_and_writes_through(proj):
    put_assets(proj)
    (proj / ".gitignore").write_text("assets/\n")
    comp = components.get("art", proj)
    assert assets.pool_size(comp) == 0                          # missing: nothing
    wt = workspace.add(proj, "k6")
    assert os.path.islink(wt / "assets")
    (wt / "assets" / "song.ogg").write_bytes(b"x" * 3000)
    (wt / "assets" / "sub").mkdir()
    (wt / "assets" / "sub" / "a.bin").write_bytes(b"y" * 72)
    assert (proj / "assets" / "song.ogg").stat().st_size == 3000
    assert assets.pool_size(comp) == 3072
    assert assets.notes(comp) == ["pool: not measured yet"]     # never walked for a note
    assets.measure([proj])
    assert assets.notes(comp) == ["pool: 3.0 KB"]


def test_no_components_untouched(proj):
    wt = workspace.add(proj, "k3")
    assert sorted(p.name for p in wt.iterdir() if p.name != ".git") == [".venv", "README.md", "app.py"]


def test_ls_info_rm(proj):
    put_assets(proj)
    comp = components.get("art", proj)
    pool = assets.pool(comp)
    (pool / "sfx").mkdir()
    (pool / "sfx" / "hit.wav").write_bytes(b"x" * 2048)
    (pool / "a.png").write_bytes(b"abc")
    got = use(comp, "ls")
    assert not got.error and "a.png\t3 B" in got.text and "sfx/\t2.0 KB" in got.text
    assert "hit.wav" in use(comp, "ls", "sfx").text
    info = use(comp, "info", "sfx/hit.wav")
    assert "file" in info.text and "2048 bytes" in info.text and "modified" in info.text
    gone = use(comp, "rm", "a.png")
    assert not gone.error and not (pool / "a.png").exists()
    bin_ = paths.user_home() / ".Trash"
    assert (bin_ / "a.png").read_bytes() == b"abc"
    (pool / "a.png").write_bytes(b"again")
    again = use(comp, "rm", "a.png")                            # a name taken: a unique one
    assert not again.error and len(list(bin_.iterdir())) == 2
    assert use(comp, "info", "nope").error
    assert use(comp, "fly").error.startswith("art: use:")


def test_escapes_refused(proj, tmp_path):
    put_assets(proj)
    comp = components.get("art", proj)
    (tmp_path / "outside.txt").write_text("mine")
    assert "\"..\"" in use(comp, "rm", "../app.py").error
    assert use(comp, "info", str(tmp_path / "outside.txt")).error
    os.symlink(tmp_path / "outside.txt", assets.pool(comp) / "sneaky")
    assert "outside" in use(comp, "rm", "sneaky").error
    assert (tmp_path / "outside.txt").exists() and (proj / "app.py").exists()
    assert use(comp, "rm", ".").error


def test_not_ignored_note(proj):
    put_assets(proj)
    comp = components.get("art", proj)
    assert assets.notes(comp) == [f"assets/ is not git-ignored in {proj.resolve()}; not linked into worktrees",
                                  "pool: not measured yet"]
    (proj / ".gitignore").write_text("assets/\n")
    assert assets.notes(comp) == ["pool: not measured yet"]


def _raises(fn):
    try:
        fn()
    except KeyError as e:
        return e
    raise AssertionError("no KeyError")


def test_no_git_is_fine_and_pool_scope(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    put_assets(plain)
    assert assets.notes(components.get("art", plain)) == ["pool: not measured yet"]
    d = components.pool_dir() / "shared"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "assets", "about": "x", "path": "files"}))
    assert "no component shared" in str(_raises(lambda: components.get("shared", plain)))
    (plain / "components" / "shared").mkdir()
    (plain / "components" / "shared" / "component.json").write_text(json.dumps({"pool": "shared"}))
    comp = components.get("shared", plain)
    assert assets.pool(comp) == d / "files" and assets.notes(comp) == ["pool: not measured yet"]


def test_template_is_valid(tmp_path):
    spec = json.loads((components.templates_dir() / "assets" / "component.json").read_text())
    assert spec["kind"] == "assets" and spec["path"] == "assets" and "pictures" in spec["about"]
    assert assets.validate(spec, tmp_path) == []
