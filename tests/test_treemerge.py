# SPDX-License-Identifier: Apache-2.0
"""Changes landing together, merged in levels like a binary tree
(eki/treemerge.py): against a throwaway repo, a stand-in resolver and a
stand-in check."""
import asyncio
import subprocess
from pathlib import Path

import pytest

from eki import treemerge


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "src"
    (root / "eki").mkdir(parents=True)
    (root / "eki" / "cli.py").write_text("value = 0\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def change(root, name, files):
    """A change made on main as it is: its own branch, one commit."""
    git(root, "checkout", "-q", "-b", name, "main")
    for f, text in files.items():
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", name)
    sha = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    return treemerge.leaf(name, sha, list(files), title=f"change {name}", what=f"what {name} is for")


def show(root, commit, f):
    return git(root, "show", f"{commit}:{f}")


class Resolver:
    """Keeps both sides' lines; counts how many resolve at once."""

    def __init__(self):
        self.calls, self.active, self.most, self.briefs = 0, 0, 0, []

    async def __call__(self, where, a, b, files):
        self.calls += 1
        self.active += 1
        self.most = max(self.most, self.active)
        self.briefs.append(treemerge.pair_brief(a, b, files, "python", where))
        await asyncio.sleep(0.1)                    # long enough for a pair beside it to start
        for f in files:
            p = Path(where) / f
            p.write_text("".join(x for x in p.read_text().splitlines(True)
                                 if not x.startswith(("<<<<<<<", "=======", ">>>>>>>"))))
        self.active -= 1
        return "kept both"


async def passes(commit):
    return True, ""


def test_the_plan_groups_changes_by_the_files_they_share():
    a = treemerge.leaf("a", "1", ["eki/cli.py"])
    b = treemerge.leaf("b", "2", ["eki/engine.py"])
    c = treemerge.leaf("c", "3", ["eki/cli.py", "eki/service.py"])
    d = treemerge.leaf("d", "4", ["eki/service.py"])            # with a through c
    e = treemerge.leaf("e", "5", ["docs/x.md"])
    straight, groups = treemerge.plan([a, b, c, d, e])
    assert [x["ids"] for x in straight] == [["b"], ["e"]]
    assert [[x["ids"][0] for x in g] for g in groups] == [["a", "c", "d"]]
    assert treemerge.shared(groups[0]) == ["eki/cli.py", "eki/service.py"]
    assert treemerge.pairs([1, 2, 3]) == ([(1, 2)], 3)


@pytest.mark.asyncio
async def test_four_changes_to_cli_merge_in_two_rounds_three_merges_two_at_once(repo, tmp_path):
    leaves = [change(repo, n, {"eki/cli.py": f"value = {k}\n"}) for k, n in enumerate("abcd", 1)]
    base = git(repo, "rev-parse", "main")
    fix = Resolver()
    tree = treemerge.Tree(repo, leaves, base=base, resolve=fix, check=passes, workers=4, home=tmp_path / "self")
    got = await tree.run()
    view = treemerge.state(tmp_path / "self")
    rounds = view["rounds"]
    assert len(rounds) == 2 and [len(r) for r in rounds] == [2, 1]           # log2(4) rounds, 3 merges
    assert [(r["a"], r["b"]) for r in rounds[0]] == [(["a"], ["b"]), (["c"], ["d"])]
    assert rounds[1][0]["a"] == ["a", "b"] and rounds[1][0]["b"] == ["c", "d"]
    assert fix.calls == 3 and fix.most == 2                                   # the first two at once
    assert sorted(r["worker"] for r in rounds[0]) == [1, 2]
    assert all(r["state"] == "resolved" for rs in rounds for r in rs)
    assert view["groups"] == [{"files": ["eki/cli.py"],
                               "changes": [{"id": n, "title": f"change {n}"} for n in "abcd"]}]
    assert got["landed"] == list("abcd") and not got["dropped"]
    assert show(repo, got["batch"], "eki/cli.py").splitlines() == [f"value = {k}" for k in range(1, 5)]
    # the resolver was told what both sides are for
    first = next(b for b in fix.briefs if "self/a:" in b)
    assert "self/a: what a is for" in first and "self/b: what b is for" in first
    assert all(f"self/{n}: what {n} is for" in fix.briefs[-1] for n in "abcd")     # the last pair: all four
    assert len(view["checks"]) == 1 and view["checks"][0]["state"] == "passed"   # judged once


@pytest.mark.asyncio
async def test_disjoint_changes_need_no_merge_step(repo, tmp_path):
    leaves = [change(repo, n, {f"eki/{n}.py": f"{n} = 1\n"}) for n in "xyz"]
    git(repo, "commit", "-q", "--allow-empty", "-m", "your checkout moved on")
    fix = Resolver()
    tree = treemerge.Tree(repo, leaves, base=git(repo, "rev-parse", "main"), resolve=fix, check=passes,
                          home=tmp_path / "self")
    got = await tree.run()
    view = treemerge.state(tmp_path / "self")
    assert view["rounds"] == [] and view["groups"] == [] and fix.calls == 0
    assert [s["id"] for s in view["straight"]] == list("xyz")
    for n in "xyz":
        assert show(repo, got["batch"], f"eki/{n}.py") == f"{n} = 1"
    assert git(repo, "merge-base", "--is-ancestor", "main", got["batch"]) == ""


@pytest.mark.asyncio
async def test_a_failing_batch_bisects_to_the_bad_change_and_lands_the_rest(repo, tmp_path):
    leaves = [change(repo, n, {f"eki/{n}.py": "ok\n"}) for n in "abc"]
    leaves.insert(2, change(repo, "bad", {"eki/bad.py": "broken\n"}))
    judged = []

    async def check(commit):
        judged.append(commit)
        names = git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()
        return ("eki/bad.py" not in names), "tests: test_bad failed"

    tree = treemerge.Tree(repo, leaves, base=git(repo, "rev-parse", "main"), resolve=Resolver(), check=check,
                          home=tmp_path / "self")
    got = await tree.run()
    assert got["landed"] == ["a", "b", "c"]
    assert list(got["dropped"]) == ["bad"] and "test_bad failed" in got["dropped"]["bad"]
    names = git(repo, "ls-tree", "-r", "--name-only", got["batch"]).splitlines()
    assert {"eki/a.py", "eki/b.py", "eki/c.py"} <= set(names) and "eki/bad.py" not in names
    view = treemerge.state(tmp_path / "self")
    assert view["checks"][0]["state"] == "failed" and view["checks"][-1]["state"] == "passed"
    assert view["dropped"] == got["dropped"] and len(judged) <= 5


@pytest.mark.asyncio
async def test_a_pair_that_cant_be_resolved_drops_the_later_side_only(repo, tmp_path):
    leaves = [change(repo, n, {"eki/cli.py": f"value = {n}\n"}) for n in "ab"]

    async def gives_up(where, a, b, files):
        return "couldn't"                           # markers left in: it doesn't hold

    tree = treemerge.Tree(repo, leaves, base=git(repo, "rev-parse", "main"), resolve=gives_up, check=passes,
                          home=tmp_path / "self")
    got = await tree.run()
    assert got["landed"] == ["a"] and "couldn't be merged with self/a" in got["dropped"]["b"]
    assert "conflict markers" in got["dropped"]["b"]
    assert show(repo, got["batch"], "eki/cli.py") == "value = a"
    assert not list((tmp_path / "self" / "tree").glob("*"))          # its worktree is gone


def test_eki_self_shows_the_tree_of_the_train():
    from eki import cli
    t = {"state": "merging", "workers": 4, "straight": [{"id": "x1", "title": "x"}],
         "groups": [{"files": ["eki/cli.py"], "changes": [{"id": n, "title": n} for n in ("a1", "b2", "c3", "d4")]}],
         "rounds": [[{"a": ["a1"], "b": ["b2"], "state": "resolving", "worker": 1},
                     {"a": ["c3"], "b": ["d4"], "state": "clean", "worker": 2}]],
         "checks": [], "dropped": {}}
    got = "\n".join(cli._tree(t))
    assert "tree merge — 5 changes, merging · 1 round(s) · up to 4 at once" in got
    assert "straight in (no file shared): x1" in got and "group 1 — eki/cli.py: a1, b2, c3, d4" in got
    assert "worker 1: a1 ⨝ b2 — resolving" in got and "worker 2: c3 ⨝ d4 — clean" in got
    assert cli._tree({}) == ["\ntree merge: no merge running — the next go-live merges whatever is ready"]


@pytest.mark.asyncio
async def test_a_finished_train_is_kept_as_the_last_one(repo, tmp_path):
    leaves = [change(repo, n, {"eki/cli.py": f"value = {n}\n"}) for n in "ab"]
    home = tmp_path / "self"
    assert treemerge.last(home) == {}
    tree = treemerge.Tree(repo, leaves, base=git(repo, "rev-parse", "main"), resolve=Resolver(), check=passes,
                          home=home)
    await tree.run()
    last = treemerge.last(home)
    assert last["outcome"] == "merged" and last["rounds"] == 1 and last["landed"] == ["a", "b"]
    assert [c["id"] for c in last["changes"]] == ["a", "b"] and last["ended"] >= last["at"]
    # the next train starting doesn't take the last one's account away
    treemerge.Tree(repo, leaves[:1], base=git(repo, "rev-parse", "main"), resolve=Resolver(), check=passes,
                   home=home)
    assert treemerge.state(home)["state"] == "merging" and treemerge.last(home)["landed"] == ["a", "b"]


@pytest.mark.asyncio
async def test_a_train_that_breaks_off_is_kept_as_failed(repo, tmp_path):
    leaves = [change(repo, n, {f"eki/{n}.py": "x\n"}) for n in "ab"]

    async def breaks(commit):
        raise treemerge.MergeError("the checkout went away")

    tree = treemerge.Tree(repo, leaves, base=git(repo, "rev-parse", "main"), resolve=Resolver(), check=breaks,
                          home=tmp_path / "self")
    with pytest.raises(treemerge.MergeError):
        await tree.run()
    last = treemerge.last(tmp_path / "self")
    assert last["outcome"] == "failed" and "went away" in last["why"]
    assert treemerge.state(tmp_path / "self")["state"] == "failed"


def test_eki_self_says_no_merge_is_running_and_how_the_last_train_went():
    from eki import cli
    last = {"train": "t", "at": 100, "ended": 200, "rounds": 2, "outcome": "merged",
            "changes": [{"id": "a1", "title": "one"}, {"id": "b2", "title": "two"}, {"id": "c3", "title": "three"}],
            "landed": ["a1", "b2"], "dropped": {"c3": "the batch failed its checks with it"}}
    gone = {"carrying": [{"id": "a1"}, {"id": "b2"}], "text": "live"}
    got = "\n".join(cli._tree({"state": "merged"}, last, gone))
    assert "no merge running — the next go-live merges whatever is ready" in got
    assert "3 changes, 2 rounds — 2 landed, 1 dropped — live" in got
    assert "a1  one" in got and "dropped c3: the batch failed" in got
    failed = {**last, "outcome": "failed", "why": "the checkout went away", "landed": [], "dropped": {}}
    assert "failed: the checkout went away" in "\n".join(cli._tree({}, failed, gone))


def test_eki_self_always_shows_going_in_and_the_tree_merge(monkeypatch, capsys):
    from eki import cli
    v = {"can": True, "goal": None, "autonomy": "propose", "review_max": 3, "working": [], "waiting": [],
         "queue": [], "left": [], "changes": [], "roadmap": {"next": []}, "pipeline": [], "golive": {}, "tree": {}}
    monkeypatch.setattr(cli, "call", lambda *a, **k: v)
    assert cli._self_status("http://x", 5) == 0
    out = capsys.readouterr().out
    assert "going in: nothing on its way right now" in out
    assert "tree merge: no merge running" in out
