# SPDX-License-Identifier: Apache-2.0
"""@file completion: names first, then paths, then anywhere; folders too."""
from eki import files


def test_matches_rank_names_then_paths_then_anywhere(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "live.py").write_text("")
    (tmp_path / "src" / "olive.md").write_text("")
    (tmp_path / "LIVE_NOTES.txt").write_text("")
    (tmp_path / "other.py").write_text("")
    got = [s["path"] for s in files.suggest(str(tmp_path), "li")]
    assert got[:2] == ["LIVE_NOTES.txt", "src/live.py"] or got[:2] == ["src/live.py", "LIVE_NOTES.txt"]
    assert "src/olive.md" in got and "other.py" not in got
    assert [s["path"] for s in files.suggest(str(tmp_path), "src/")][0] == "src/"
    assert files.suggest("", "x") == [] and files.suggest(str(tmp_path / "nope"), "x") == []
    assert len(files.suggest(str(tmp_path), "")) >= 4


def test_made_keeps_what_the_gallery_can_show_and_is_still_there(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "page.html").write_text("<html></html>")
    (tmp_path / "main.py").write_text("")
    paths = [str(tmp_path / n) for n in ("a.png", "page.html", "main.py", "gone.svg", "a.png")]
    assert files.made(paths) == [str(tmp_path / "a.png"), str(tmp_path / "page.html")]


def test_made_since_finds_what_changed_in_a_plain_folder(tmp_path):
    import os
    old, new = tmp_path / "old.png", tmp_path / "art" / "new.svg"
    old.write_bytes(b"x")
    os.utime(old, (1000, 1000))
    new.parent.mkdir()
    new.write_text("<svg></svg>")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.png").write_bytes(b"x")
    assert files.made_since(str(tmp_path), 2000) == [str(new)]
    assert files.made_since("", 0) == []
