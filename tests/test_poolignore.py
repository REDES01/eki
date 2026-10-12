"""Adding an assets card git-ignores its pool and untracks what git tracked inside it."""
import re

from eki import api_cardadd, components, projectchats, projects, workspace
from eki.components import assets, poolignore


def add(conn, pid, template="assets"):
    return api_cardadd.add_card(conn, re.match(r"(.+)", pid), {}, {"template": template})


def commits(root):
    return int(workspace.git(root, "rev-list", "--count", "HEAD"))


def test_adding_assets_untracks_and_ignores_the_pool(conn, proj):
    (proj / "assets").mkdir()
    (proj / "assets" / "a.png").write_bytes(b"png")
    workspace.git(proj, "add", "assets")
    workspace.git(proj, "commit", "-q", "-m", "tracked art")
    (proj / "app.py").write_text("print('changed')\n")         # the person's own, unstaged
    before = commits(proj)
    pid = projects.add(conn, proj)
    got = add(conn, pid)
    assert "wrote /assets/ to .gitignore" in got["said"]
    assert "took 1 file in assets/ out of git (kept on disk)" in got["said"]
    assert (proj / "assets" / "a.png").read_bytes() == b"png"
    assert workspace.git(proj, "ls-files", "--", "assets") == ""
    workspace.git(proj, "check-ignore", "-q", "assets/")        # raises when not ignored
    assert assets.problems(components.get(got["card"]["name"], proj)) == []
    assert commits(proj) == before + 1
    assert workspace.git(proj, "log", "-1", "--format=%s") == "eki: keep assets/ out of git (media pool)"
    assert workspace.git(proj, "diff", "--name-only") == "app.py"   # still unstaged, not committed
    assert workspace.git(proj, "diff", "--cached", "--name-only") == ""
    # a second settle has nothing to do
    assert poolignore.settle(proj, "assets") == []
    assert commits(proj) == before + 1


def test_an_existing_gitignore_gets_the_line_after_a_newline_and_keeps_its_own(proj):
    (proj / ".gitignore").write_text("*.log")
    assert poolignore.settle(proj, "media/art") == ["wrote /media/art/ to .gitignore"]
    assert (proj / ".gitignore").read_text() == "*.log\n/media/art/\n"
    # only the pool's line is committed: the person's own uncommitted *.log stays theirs
    assert workspace.git(proj, "show", "HEAD:.gitignore") == "/media/art/"
    assert workspace.git(proj, "diff", "--name-only") == ".gitignore"


def test_no_repo_gets_the_card_and_no_error(conn, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert poolignore.settle(folder, "assets") == []
    pid = projectchats.create(conn, "plain", folder)
    got = add(conn, pid)
    assert got["card"]["name"] == "assets"
    assert not (folder / ".gitignore").exists()
