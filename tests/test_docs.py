"""The side panel's local files: what may be read and written, atomic saves, conflicts, who wrote
it last, and the `eki open` queue — all under the fixture's EKI_USER_HOME, never the real home."""
import json
import os
import time

import pytest

from eki import docs, files, paths


@pytest.fixture
def uhome():
    return paths.user_home()


def test_reads_a_markdown_file_under_home(conn, uhome):
    f = uhome / "notes" / "playtest.md"
    f.parent.mkdir()
    f.write_text("# Playtest\n- jump feels late\n")
    got = docs.read(conn, str(f))
    assert set(got) == {"path", "name", "text", "mtime", "hash", "size", "editable", "reason", "writer"}
    assert got["text"].startswith("# Playtest") and got["name"] == "playtest.md"
    assert got["path"] == os.path.realpath(f) and got["editable"] and got["reason"] is None
    assert got["size"] == len(f.read_bytes()) and len(got["hash"]) == 64 and got["writer"] is None


def test_tilde_expands_to_home(conn, uhome, monkeypatch):
    monkeypatch.setenv("HOME", str(uhome))
    (uhome / "a.md").write_text("a\n")
    assert docs.read(conn, "~/a.md")["text"] == "a\n"


def test_refuses_outside_home(conn, tmp_path):
    f = tmp_path / "elsewhere.md"
    f.write_text("x\n")
    with pytest.raises(ValueError, match="under your home folder"):
        docs.read(conn, str(f))


def test_a_known_file_outside_home_is_read_only(conn, tmp_path):
    f = tmp_path / "made.md"
    f.write_text("made\n")
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES ('r1', 0, ?, 'tool', ?)",
                 (time.time(), json.dumps({"path": str(f)})))
    got = docs.read(conn, str(f))
    assert got["text"] == "made\n" and not got["editable"] and "read-only" in got["reason"]
    with pytest.raises(ValueError):
        docs.write(conn, str(f), "x", got["hash"])


def test_refuses_a_symlink_leading_out_of_home(conn, uhome, tmp_path):
    target = tmp_path / "outside.md"
    target.write_text("secret\n")
    link = uhome / "link.md"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="link"):
        docs.read(conn, str(link))


@pytest.mark.parametrize("rel", [".ssh/x.txt", "Library/x.md", ".aws/notes.md", "work/.netrc"])
def test_refuses_credential_places(conn, uhome, rel):
    f = uhome / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x\n")
    with pytest.raises(ValueError, match="credentials"):
        docs.read(conn, str(f))
    assert files.read(conn, str(f)) is None
    # even a file a run named
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES ('r1', 0, ?, 'tool', ?)",
                 (time.time(), json.dumps({"path": str(f)})))
    with pytest.raises(ValueError):
        docs.read(conn, str(f))


def test_refuses_eki_internals(conn, home):
    with pytest.raises(ValueError, match="eki's own"):
        docs.read(conn, str(home / "providers.json"))


def test_too_large_binary_and_missing(conn, uhome):
    big = uhome / "big.md"
    big.write_bytes(b"a" * (docs.MAX + 1))
    with pytest.raises(ValueError, match="too large"):
        docs.read(conn, str(big))
    nul = uhome / "nul.txt"
    nul.write_bytes(b"ab\0cd")
    with pytest.raises(ValueError, match="not a text file"):
        docs.read(conn, str(nul))
    latin = uhome / "latin.md"
    latin.write_bytes(b"caf\xe9")
    with pytest.raises(ValueError, match="not a text file"):
        docs.read(conn, str(latin))
    with pytest.raises(KeyError):
        docs.read(conn, str(uhome / "gone.md"))


def test_other_text_under_home_is_read_only(conn, uhome):
    f = uhome / "tool.py"
    f.write_text("print(1)\n")
    got = docs.read(conn, str(f))
    assert not got["editable"] and got["reason"] == "only .md, .markdown and .txt files can be edited"
    with pytest.raises(ValueError, match="only .md"):
        docs.write(conn, str(f), "x", got["hash"])


def test_write_keeps_mode_and_leaves_no_temp(conn, uhome):
    f = uhome / "DECISIONS.md"
    f.write_text("old\n")
    f.chmod(0o600)
    got = docs.read(conn, str(f))
    out = docs.write(conn, str(f), "new\n", got["hash"])
    assert out["saved"] and f.read_text() == "new\n" and (f.stat().st_mode & 0o777) == 0o600
    assert out["hash"] == docs.read(conn, str(f))["hash"]
    assert sorted(p.name for p in uhome.iterdir()) == ["DECISIONS.md"]


def test_new_file_is_0644(conn, uhome):
    f = uhome / "fresh.md"
    assert docs.write(conn, str(f), "hi\n", "")["saved"]
    assert f.read_text() == "hi\n" and (f.stat().st_mode & 0o777) == 0o644


def test_conflict_when_the_file_moved_then_keep_mine(conn, uhome):
    f = uhome / "plan.md"
    f.write_text("v1\n")
    loaded = docs.read(conn, str(f))
    f.write_text("agent wrote this\n")
    out = docs.write(conn, str(f), "mine\n", loaded["hash"])
    assert out["conflict"] and out["text"] == "agent wrote this\n" and "saved" not in out
    assert f.read_text() == "agent wrote this\n"
    again = docs.write(conn, str(f), "mine\n", out["hash"])
    assert again["saved"] and f.read_text() == "mine\n"


def test_too_large_to_save(conn, uhome):
    with pytest.raises(ValueError, match="too large"):
        docs.write(conn, str(uhome / "x.md"), "a" * (docs.MAX + 1), "")


def test_writer_run_then_you(conn, uhome):
    f = uhome / "AGENTS.md"
    f.write_text("by agent\n")
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES ('r42', 0, ?, 'tool', ?)",
                 (time.time(), json.dumps({"path": str(f)})))
    got = docs.read(conn, str(f))
    assert got["writer"]["by"] == "run" and got["writer"]["run"] == "r42"
    time.sleep(0.01)
    docs.write(conn, str(f), "by me\n", got["hash"])
    w = docs.read(conn, str(f))["writer"]
    assert w["by"] == "you" and w["run"] is None


def test_an_old_event_is_not_the_writer(conn, uhome):
    f = uhome / "old.md"
    f.write_text("x\n")
    conn.execute("INSERT INTO events(run_id, attempt, t, kind, data) VALUES ('r1', 0, ?, 'tool', ?)",
                 (time.time() - 3600, json.dumps({"path": str(f)})))
    assert docs.read(conn, str(f))["writer"] is None


def test_open_queue_round_trip(conn, uhome):
    assert docs.opens(conn, None) == {"opens": [], "last": 0}
    f = uhome / "a.md"
    f.write_text("a\n")
    first = docs.request_open(conn, str(f))
    start = docs.opens(conn, None)
    assert start == {"opens": [], "last": first}
    second = docs.request_open(conn, str(f))
    got = docs.opens(conn, start["last"])
    assert [o["id"] for o in got["opens"]] == [second] and got["opens"][0]["path"] == os.path.realpath(f)
    assert got["last"] == second and docs.opens(conn, second) == {"opens": [], "last": second}
    with pytest.raises(KeyError):
        docs.request_open(conn, str(uhome / "gone.md"))
    with pytest.raises(ValueError):
        docs.request_open(conn, "/etc/hosts")


def test_files_read_serves_an_image_under_home_but_not_outside(conn, uhome, tmp_path):
    img = uhome / "shots" / "a.png"
    img.parent.mkdir()
    img.write_bytes(b"\x89PNG fake")
    body, ctype = files.read(conn, str(img))
    assert body == b"\x89PNG fake" and ctype == "image/png"
    other = tmp_path / "b.png"
    other.write_bytes(b"x")
    assert files.read(conn, str(other)) is None


def test_module_is_small():
    for name in ("docs.py", "api_doc.py"):
        with open(os.path.join(os.path.dirname(docs.__file__), name)) as f:
            assert len(f.read().splitlines()) < 400
