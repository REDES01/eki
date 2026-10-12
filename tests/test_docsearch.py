"""Finding markdown for "Open file…" (eki/docsearch.py, GET /api/doc/search) and `eki open <path>`
queueing the file for the window (GET /api/doc/opens)."""
import argparse
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from eki import apiroutes, docs, docsearch, paths, projectchats, server
from eki.cli import open as cli_open


def make(folder, files):
    for rel in files:
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n")


@pytest.fixture
def game(conn):
    folder = paths.user_home() / "game"
    make(folder, ["notes/playtest.md", "AGENTS.md", ".git/x.md", "node_modules/y.md", "src/main.py",
                  ".hidden/z.md", "docs/design.markdown"])
    return projectchats.create(conn, "game", folder, history="none"), os.path.realpath(folder)


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read())


def rels(found):
    return [f["rel"] for f in found]


def test_substring_and_subsequence(conn, game):
    pid, root = game
    got = docsearch.search(conn, "play", pid)
    assert got == [{"path": os.path.join(root, "notes/playtest.md"), "rel": "notes/playtest.md", "root": root,
                    "mtime": os.path.getmtime(os.path.join(root, "notes/playtest.md"))}]
    assert rels(docsearch.search(conn, "PTST", pid)) == ["notes/playtest.md"]
    assert rels(docsearch.search(conn, "nothing-like-this", pid)) == []


def test_substring_ranks_before_subsequence_then_shorter(conn, game):
    pid, root = game
    make(paths.user_home() / "game", ["agents/old.md", "a/g/e/n/t/s.md"])
    got = rels(docsearch.search(conn, "agents", pid))
    assert got == ["AGENTS.md", "agents/old.md", "a/g/e/n/t/s.md"]


def test_skips_git_node_modules_and_dot_folders(conn, game):
    pid, _ = game
    every = rels(docsearch.search(conn, "", pid)) + rels(docsearch.search(conn, "md", pid))
    assert every and not any(r.startswith((".git", "node_modules", ".hidden")) for r in every)
    assert "src/main.py" not in every
    assert "docs/design.markdown" in every


def test_empty_query_is_newest_first(conn, game):
    pid, root = game
    os.utime(os.path.join(root, "AGENTS.md"), (2e9, 2e9))
    got = docsearch.search(conn, "", pid)
    assert got[0]["rel"] == "AGENTS.md" and len(got) == 3


def test_no_project_searches_every_project(conn, game):
    other = paths.user_home() / "book"
    make(other, ["chapters/one.md"])
    projectchats.create(conn, "book", other, history="none")
    got = rels(docsearch.search(conn, "", None))
    assert "chapters/one.md" in got and "notes/playtest.md" in got


def test_unknown_project_is_keyerror(conn):
    with pytest.raises(KeyError):
        docsearch.search(conn, "x", "nope")


def test_route_over_http(web, game):
    pid, _ = game
    assert apiroutes.match("GET", "/api/doc/search")[0].__module__ == "eki.api_docsearch"
    code, got = get(f"{web}/api/doc/search?q=play&project={pid}")
    assert code == 200 and rels(got["files"]) == ["notes/playtest.md"]
    try:
        urllib.request.urlopen(f"{web}/api/doc/search?q=x&project=nope", timeout=10)
        assert False, "an unknown project is a 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


@pytest.fixture
def no_window(monkeypatch):
    opened = []
    monkeypatch.setattr(cli_open, "window", lambda browser: opened.append(browser))
    return opened


def test_eki_open_queues_the_file(web, conn, no_window):
    f = paths.user_home() / "DECISIONS.md"
    f.write_text("# Decisions\n")
    assert cli_open.run(argparse.Namespace(path=str(f), browser=False)) == 0
    assert no_window == [False]
    _, got = get(f"{web}/api/doc/opens?after=0")
    assert [o["path"] for o in got["opens"]] == [os.path.realpath(f)]


def test_eki_open_tilde_is_the_user_home(conn, no_window):
    (paths.user_home() / "n.md").write_text("n\n")
    assert cli_open.run(argparse.Namespace(path="~/n.md", browser=False)) == 0
    assert docs.opens(conn, 0)["opens"][0]["path"] == os.path.realpath(paths.user_home() / "n.md")


def test_eki_open_refused_exits_1(conn, no_window, capsys):
    key = paths.user_home() / ".ssh" / "id"
    key.parent.mkdir()
    key.write_text("secret\n")
    assert cli_open.run(argparse.Namespace(path=str(key), browser=False)) == 1
    assert cli_open.run(argparse.Namespace(path=str(paths.user_home() / "gone.md"), browser=False)) == 1
    assert "credentials" in capsys.readouterr().err
    assert docs.opens(conn, 0)["opens"] == [] and no_window == []


def test_eki_open_without_path_just_opens(conn, no_window):
    assert cli_open.run(argparse.Namespace(path=None, browser=True)) == 0
    assert no_window == [True] and docs.opens(conn, 0)["opens"] == []
