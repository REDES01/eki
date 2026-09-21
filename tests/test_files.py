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
