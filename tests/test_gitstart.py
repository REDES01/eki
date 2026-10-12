"""A folder gets a history: the survey, the first commit, and the folders refused (eki/gitstart.py)."""
import subprocess

import pytest

from eki import gitstart, paths


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def sparse(p, mib):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.truncate(mib * 1024 * 1024)


def plain(tmp_path):
    f = tmp_path / "song"
    (f / "src").mkdir(parents=True)
    (f / "a.txt").write_text("a\n")
    (f / "src" / "b.py").write_text("print(1)\n")
    sparse(f / "big.wav", 30)
    return f


def test_a_plain_folder_gets_a_history_without_its_media(tmp_path):
    f = plain(tmp_path)
    seen = gitstart.survey(f)
    assert seen["state"] == "plain" and seen["fit"] and seen["why"] is None
    assert seen["offer"] == gitstart.OFFER and seen["left_out"] == ["big.wav"]
    assert seen["ignore"][:2] == [gitstart.MEDIA_BLOCK, "big.wav"] and gitstart.OTHER_BLOCK in seen["ignore"]
    assert not (f / ".git").exists() and not (f / ".gitignore").exists()      # the survey only reads
    got = gitstart.start(f)
    tracked = git(f, "ls-files").split("\n")
    assert "a.txt" in tracked and "src/b.py" in tracked and "big.wav" not in tracked
    lines = (f / ".gitignore").read_text().split("\n")
    assert lines[lines.index(gitstart.MEDIA_BLOCK) + 1] == "big.wav"
    assert "node_modules/" in lines and ".DS_Store" in lines
    assert git(f, "log", "--format=%s").split("\n") == [gitstart.FIRST_COMMIT]
    assert got["commit"] == git(f, "rev-parse", "HEAD") and got["branch"] == git(f, "branch", "--show-current")
    assert got["left_out"] == ["big.wav"] and gitstart.left_out(f) == ["big.wav"]
    assert gitstart.survey(f)["state"] == "git"


def test_the_persons_gitignore_keeps_its_lines_first(tmp_path):
    f = plain(tmp_path)
    (f / ".gitignore").write_text("secret.txt\nnode_modules/\n")
    gitstart.start(f)
    lines = (f / ".gitignore").read_text().split("\n")
    assert lines[:2] == ["secret.txt", "node_modules/"]
    assert lines.count("node_modules/") == 1 and lines.count("big.wav") == 1


def test_a_media_folder_is_left_out_whole(tmp_path):
    f = plain(tmp_path)
    sparse(f / "audio" / "one.wav", 1)
    sparse(f / "audio" / "two.flac", 1)
    (f / "docs").mkdir()
    (f / "docs" / "notes.md").write_text("n\n")
    sparse(f / "docs" / "cover.psd", 1)
    assert gitstart.survey(f)["left_out"] == ["big.wav", "audio/", "docs/cover.psd"]


def test_a_repo_with_no_commit_gets_the_first(tmp_path):
    f = tmp_path / "empty"
    f.mkdir()
    git(f, "init", "-q")
    (f / "a.txt").write_text("a\n")
    assert gitstart.survey(f)["state"] == "no-commit"
    gitstart.start(f)
    assert git(f, "log", "--format=%s") == gitstart.FIRST_COMMIT
    assert "a.txt" in git(f, "ls-files")


def test_a_repo_with_commits_is_left_alone(tmp_path):
    f = tmp_path / "repo"
    f.mkdir()
    git(f, "init", "-q", "-b", "trunk")
    (f / "a.txt").write_text("a\n")
    git(f, "add", "a.txt")
    git(f, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "mine")
    sparse(f / "big.wav", 30)
    head = git(f, "rev-parse", "HEAD")
    got = gitstart.start(f)
    assert got == {"branch": "trunk", "commit": head, "left_out": []}
    assert not (f / ".gitignore").exists() and git(f, "log", "--format=%s") == "mine"


def test_a_missing_folder_is_made(tmp_path):
    f = paths.user_home() / "fresh"
    assert gitstart.survey(f)["state"] == "missing"
    gitstart.start(f)
    assert git(f, "log", "--format=%s") == gitstart.FIRST_COMMIT


def test_icloud_drive_is_chat_only():
    f = paths.user_home() / "Library/Mobile Documents/com~apple~CloudDocs/x"
    f.mkdir(parents=True)
    (f / "a.txt").write_text("a\n")
    seen = gitstart.survey(f)
    assert not seen["fit"] and "iCloud Drive" in seen["why"]
    assert seen["offer"] == f"{seen['why']} {gitstart.CHAT_ONLY}"
    with pytest.raises(gitstart.Unfit) as err:
        gitstart.start(f)
    assert gitstart.CHAT_ONLY in str(err.value)
    assert sorted(p.name for p in f.iterdir()) == ["a.txt"]


@pytest.mark.parametrize("where,name", [("Library/CloudStorage/GoogleDrive-me@x.com/My Drive/p", "Google Drive"),
                                        ("Library/CloudStorage/OneDrive-Personal/p", "OneDrive"),
                                        ("Dropbox/p", "Dropbox")])
def test_synced_folders_are_named(where, name):
    f = paths.user_home() / where
    f.mkdir(parents=True)
    assert name in gitstart.survey(f)["why"]


def test_a_dropbox_root_anywhere_marks_its_folders(tmp_path):
    (tmp_path / "box").mkdir()
    (tmp_path / "box" / ".dropbox").write_text("")
    (tmp_path / "box" / "p").mkdir()
    assert "Dropbox" in gitstart.survey(tmp_path / "box" / "p")["why"]
    assert gitstart.survey(paths.user_home())["fit"]


@pytest.mark.parametrize("lock", ["~$doc.docx", ".~lock.sheet.ods#"])
def test_a_file_open_in_another_app_is_chat_only(tmp_path, lock):
    f = tmp_path / "office"
    f.mkdir()
    (f / "doc.docx").write_text("d")
    (f / lock).write_text("")
    seen = gitstart.survey(f)
    assert not seen["fit"] and lock in seen["why"]


def test_a_folder_of_big_media_is_chat_only(tmp_path):
    f = tmp_path / "footage"
    for i in range(5):
        sparse(f / f"take{i}.mov", 50)
    (f / "notes.txt").write_text("n\n")
    seen = gitstart.survey(f)
    assert not seen["fit"] and "media" in seen["why"]
    with pytest.raises(gitstart.Unfit):
        gitstart.start(f)
    assert not (f / ".git").exists()


def test_left_out_reads_the_block_back(tmp_path):
    (tmp_path / ".gitignore").write_text(
        f"mine\n\n{gitstart.MEDIA_BLOCK}\naudio/\nbig.wav\n\n{gitstart.OTHER_BLOCK}\n.DS_Store\n")
    assert gitstart.left_out(tmp_path) == ["audio/", "big.wav"]
    (tmp_path / ".gitignore").write_text("mine\n")
    assert gitstart.left_out(tmp_path) == []
    assert gitstart.left_out(tmp_path / "nowhere") == []
