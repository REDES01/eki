# SPDX-License-Identifier: Apache-2.0
"""A copy of the folder per thread: parallel runs don't collide, and what a
run changed comes back — or is kept on a branch, never lost, never forced."""
import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from eki import workspace
from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health, register
from eki.engine import Engine
from tests.test_runs import echo_config, settle


def sh(where, *args):
    return subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    (r / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
    (r / "README.md").write_text("hello\n")
    (r / ".gitignore").write_text("node_modules/\n.env\n")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "start")
    return r


# ---- which folders get a copy ------------------------------------------------------

def test_only_a_repo_with_a_commit_gets_a_copy(tmp_path, repo):
    assert workspace.repo_of(str(repo)) == str(repo.resolve())
    (repo / "sub").mkdir()
    assert workspace.repo_of(str(repo / "sub")) == str(repo.resolve())
    plain = tmp_path / "plain"
    plain.mkdir()
    assert workspace.repo_of(str(plain)) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    sh(empty, "init", "-q")
    assert workspace.repo_of(str(empty)) is None                # no commit yet
    (repo / ".gitmodules").write_text("")
    assert workspace.repo_of(str(repo)) is None                 # submodules: take turns
    (repo / ".gitmodules").unlink()
    ours = workspace.EKI_HOME / "self" / "x"
    ours.mkdir(parents=True)
    sh(ours, "init", "-q")
    assert workspace.repo_of(str(ours)) is None                 # eki's own copies aren't copied


def test_a_plain_folder_is_worked_in_place(tmp_path):
    ws = workspace.open(str(tmp_path), "c1")
    assert ws.mode == "lock" and ws.path == str(tmp_path)
    assert workspace.close(ws, "r1") == {"state": "in place"}


# ---- the copy matches your folder --------------------------------------------------

def test_the_copy_has_your_uncommitted_edits_and_new_files(repo):
    (repo / "app.py").write_text("a = 1\nb = 20\nc = 3\n")      # uncommitted edit
    (repo / "notes.txt").write_text("new file\n")               # untracked
    (repo / "node_modules" / "left-pad").mkdir(parents=True)    # ignored deps
    (repo / ".env").write_text("KEY=1\n")
    ws = workspace.open(str(repo), "c1")
    copy = Path(ws.path)
    assert ws.mode == "worktree" and copy != repo and copy.is_relative_to(workspace.ROOT)
    assert (copy / "app.py").read_text() == "a = 1\nb = 20\nc = 3\n"
    assert (copy / "notes.txt").read_text() == "new file\n"
    assert (copy / "node_modules").is_symlink() and (copy / ".env").is_symlink()
    assert ws.start != ws.base                                  # the snapshot is its own commit
    assert sh(repo, "status", "--porcelain")                    # yours is untouched
    assert sh(repo, "branch", "--list") == "* main"             # and gets no branch from it


def test_a_subfolder_maps_to_the_same_place_in_the_copy(repo):
    (repo / "web").mkdir()
    (repo / "web" / "index.js").write_text("x\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "web")
    ws = workspace.open(str(repo / "web"), "c1")
    assert ws.path == str(Path(ws.tree) / "web") and (Path(ws.path) / "index.js").exists()


def test_the_next_run_starts_from_your_folder_as_it_is_then(repo):
    ws = workspace.open(str(repo), "c1")
    (Path(ws.path) / "scratch.txt").write_text("left by the agent")
    workspace.close(ws, "r1")
    (repo / "README.md").write_text("changed by you\n")
    again = workspace.open(str(repo), "c1")
    assert again.tree == ws.tree                                # the thread keeps its copy
    assert (Path(again.path) / "README.md").read_text() == "changed by you\n"
    assert (Path(again.path) / "scratch.txt").read_text() == "left by the agent"  # it came back to you


# ---- bringing it back ---------------------------------------------------------------

def test_changes_come_back_uncommitted_as_if_worked_in_place(repo):
    ws = workspace.open(str(repo), "c1")
    (Path(ws.path) / "app.py").write_text("a = 1\nb = 2\nc = 30\n")
    (Path(ws.path) / "new.py").write_text("print(1)\n")
    got = workspace.close(ws, "r1", "change c")
    assert got["state"] == "applied" and sorted(got["files"]) == ["app.py", "new.py"]
    assert (repo / "app.py").read_text().endswith("c = 30\n") and (repo / "new.py").exists()
    assert sh(repo, "log", "--oneline").count("\n") == 0         # no commit you didn't ask for
    assert "applied 2 files" in workspace.summary(ws, got)


def test_commits_the_agent_made_stay_commits_when_you_hadnt_moved(repo):
    ws = workspace.open(str(repo), "c1")
    (Path(ws.path) / "app.py").write_text("a = 10\nb = 2\nc = 3\n")
    sh(ws.path, "commit", "-q", "-am", "agent: a is 10")
    got = workspace.close(ws, "r1")
    assert got["state"] == "applied" and got["kept_commits"] and got["commits"] == 1
    assert sh(repo, "log", "-1", "--format=%s") == "agent: a is 10"
    assert not sh(repo, "status", "--porcelain")


def test_two_threads_at_once_both_come_back(repo):
    one = workspace.open(str(repo), "c1")
    two = workspace.open(str(repo), "c2")
    assert one.tree != two.tree
    (Path(one.path) / "app.py").write_text("a = 100\nb = 2\nc = 3\n")
    (Path(two.path) / "README.md").write_text("hello from two\n")
    assert workspace.close(one, "r1")["state"] == "applied"
    assert workspace.close(two, "r2")["state"] == "applied"
    assert (repo / "app.py").read_text().startswith("a = 100")
    assert (repo / "README.md").read_text() == "hello from two\n"


def test_the_same_lines_changed_twice_is_kept_on_a_branch_not_forced(repo):
    one = workspace.open(str(repo), "c1")
    two = workspace.open(str(repo), "c2")
    (Path(one.path) / "app.py").write_text("a = 1\nb = 'one'\nc = 3\n")
    (Path(two.path) / "app.py").write_text("a = 1\nb = 'two'\nc = 3\n")
    assert workspace.close(one, "r1")["state"] == "applied"
    got = workspace.close(two, "r2")
    assert got["state"] == "conflicted" and got["branch"] == "eki/kept/r2"
    assert "b = 'one'" in (repo / "app.py").read_text()          # yours isn't touched
    assert "b = 'two'" in sh(repo, "show", "eki/kept/r2:app.py")
    assert "git merge eki/kept/r2" in workspace.summary(two, got)


def test_a_run_that_failed_leaves_its_changes_on_a_branch(repo):
    ws = workspace.open(str(repo), "c1")
    (Path(ws.path) / "app.py").write_text("half done\n")
    got = workspace.close(ws, "r9", ok=False)
    assert got["state"] == "kept" and (repo / "app.py").read_text() == "a = 1\nb = 2\nc = 3\n"
    assert sh(repo, "show", "eki/kept/r9:app.py") == "half done"


def test_nothing_changed_says_so(repo):
    ws = workspace.open(str(repo), "c1")
    assert workspace.close(ws, "r1") == {"state": "unchanged"}
    assert workspace.summary(ws, {"state": "unchanged"}) == ""


def test_old_copies_are_swept_but_not_one_in_use(repo):
    idle = workspace.open(str(repo), "c1")
    workspace.close(idle, "r1")
    busy = workspace.open(str(repo), "c2")
    later = time.time() + 8 * 86400
    gone = workspace.sweep(now=later)
    assert gone == [idle.tree] and not Path(idle.tree).exists() and Path(busy.tree).exists()
    assert str(Path(idle.tree)) not in sh(repo, "worktree", "list")


def test_the_agent_is_told_where_it_is():
    ws = workspace.Workspace(folder="/p/proj", mode="worktree", path="/c/proj")
    assert "working in /c/proj, eki's copy of /p/proj" in workspace.brief(ws)
    assert workspace.brief(workspace.Workspace(folder="/p", mode="lock", path="/p")) == ""


# ---- in the engine ----------------------------------------------------------------------

@register("scribe")
class Scribe(Backend):
    """Writes the file its request names, where it was sent, slowly."""
    seen: list = []
    live: list = []
    overlap = False

    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        Scribe.seen.append({"cwd": kw.get("cwd"), "prompt": messages[-1].content})
        Scribe.live.append(1)
        if len(Scribe.live) > 1:
            Scribe.overlap = True
        try:
            name, _, text = messages[-1].content.split("\n")[-1].partition(":")
            await asyncio.sleep(0.2)
            Path(kw["cwd"], name.strip()).write_text(text.strip() + "\n")
            yield f"wrote {name.strip()}"
        finally:
            Scribe.live.pop()


def desk(tmp_path, **settings):
    cfg = echo_config(tmp_path)
    cfg.backends.append(BackendInfo(key="scribe", kind="scribe", label="scribe",
                                    capabilities=Capabilities(repo=True, tools=True),
                                    cost=Cost(tier=0)))
    cfg.options["scribe"] = {}
    Scribe.seen, Scribe.live, Scribe.overlap = [], [], False
    eng = Engine(cfg, owner=True)
    eng.settings = {**eng.settings, "skills_learn": "off", **settings}
    return eng


@pytest.mark.asyncio
async def test_a_folder_run_works_in_its_copy_and_brings_it_back(tmp_path, repo):
    eng = desk(tmp_path)
    started = await eng.ask("out.txt: done", repo=str(repo), backend_key="scribe")
    run = await settle(eng.runs, started["run"], timeout=10)
    assert run["state"] == "done"
    assert Scribe.seen[0]["cwd"] != str(repo) and "eki's copy of" in Scribe.seen[0]["prompt"]
    assert (repo / "out.txt").read_text() == "done\n"
    answer = eng.store.turns(started["conversation"])[-1]
    assert "eki: applied 1 file" in answer["content"] and '"state": "applied"' in answer["meta"]
    await eng.runner.stop()


def test_the_answer_points_at_your_files_not_the_copy(repo):
    ws = workspace.open(str(repo), "c1")
    said = f"Wrote [util.py]({ws.path}/util.py) in {ws.tree}."
    assert workspace.home_paths(ws, said) == f"Wrote [util.py]({ws.repo}/util.py) in {ws.repo}."


@pytest.mark.asyncio
async def test_parallel_threads_in_one_repo_run_at_once_and_both_land(tmp_path, repo):
    eng = desk(tmp_path)
    a = await eng.ask("a.txt: from a", repo=str(repo), backend_key="scribe")
    b = await eng.ask("b.txt: from b", repo=str(repo), backend_key="scribe")
    for s in (a, b):
        await settle(eng.runs, s["run"], timeout=10)
    assert Scribe.overlap                                       # really at the same time
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_plain_folder_makes_runs_take_turns(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    eng = desk(tmp_path)
    a = await eng.ask("a.txt: 1", repo=str(plain), backend_key="scribe")
    b = await eng.ask("b.txt: 2", repo=str(plain), backend_key="scribe")
    for s in (a, b):
        await settle(eng.runs, s["run"], timeout=10)
    assert not Scribe.overlap and Scribe.seen[0]["cwd"] == str(plain)
    assert (plain / "a.txt").exists() and (plain / "b.txt").exists()
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_a_page_an_agent_wrote_reaches_the_gallery(tmp_path, repo):
    plain = tmp_path / "plain"
    plain.mkdir()
    eng = desk(tmp_path)
    a = await eng.ask("index.html: <html><body>hi</body></html>", repo=str(repo), backend_key="scribe")
    b = await eng.ask("fox.svg: <svg></svg>", repo=str(plain), backend_key="scribe")
    c = await eng.ask("notes.txt: words", repo=str(plain), backend_key="scribe")
    for s in (a, b, c):
        await settle(eng.runs, s["run"], timeout=10)
    files = {m["conversation_id"]: m["files"] for m in eng.store.made()}
    assert files[a["conversation"]] == [str(Path(workspace.repo_of(str(repo))) / "index.html")]
    assert files[b["conversation"]] == [str(plain / "fox.svg")]
    assert c["conversation"] not in files                       # words aren't made things
    await eng.runner.stop()


@pytest.mark.asyncio
async def test_worktrees_off_means_in_place_taking_turns(tmp_path, repo):
    eng = desk(tmp_path, worktrees=False)
    a = await eng.ask("a.txt: 1", repo=str(repo), backend_key="scribe")
    b = await eng.ask("b.txt: 2", repo=str(repo), backend_key="scribe")
    for s in (a, b):
        await settle(eng.runs, s["run"], timeout=10)
    assert not Scribe.overlap and all(x["cwd"] == str(repo) for x in Scribe.seen)
    await eng.runner.stop()
