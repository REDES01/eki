# SPDX-License-Identifier: Apache-2.0
"""eki proposing a change to itself — with a repo, an agent and a checker
that are all stand-ins. The real thing is `eki self "…"`."""
import subprocess
from pathlib import Path

import pytest

from eki import candidate, selfwork


def repo(tmp_path: Path, passing: bool = True) -> Path:
    root = tmp_path / "src"
    (root / "eki").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "eki" / "thing.py").write_text("VALUE = 1\n")
    (root / "eki" / "agent.py").write_text("# what launchd runs\n")
    (root / "tests" / "test_thing.py").write_text(f"def test_it():\n    assert {passing}\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    return root


def agent(edits: dict, state: str = "done"):
    """An Ask that writes `edits` into the folder it's given."""
    seen = {}

    def ask(prompt: str, folder: str):
        seen.update(prompt=prompt, folder=folder)
        for name, text in edits.items():
            path = Path(folder) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return {"state": state, "run": "r123", "backend": "claude"}

    ask.seen = seen
    return ask


def verdict(fit: bool):
    def check(where, **kw):
        return candidate.Report(str(where), [candidate.Check("tests", fit, "as told")])
    return check


def propose(tmp_path, root, ask, **kw):
    kw.setdefault("check", verdict(True))
    return selfwork.propose("make VALUE two", root=root, ask=ask,
                            home=tmp_path / "self", **kw)


def test_a_change_becomes_a_commit_on_its_own_branch_and_nothing_else_moves(tmp_path):
    root = repo(tmp_path)
    before = selfwork.git(root, "rev-parse", "HEAD")
    ask = agent({"eki/thing.py": "VALUE = 2\n", "tests/test_two.py": "def test_two():\n    pass\n"})
    p = propose(tmp_path, root, ask)

    assert p.fit and "proposed, not merged" in p.verdict
    assert sorted(p.files) == ["eki/thing.py", "tests/test_two.py"]
    assert p.branch == f"self/{p.id}" and p.base == before
    # the agent worked in the worktree, not in the source
    assert ask.seen["folder"] == p.worktree != str(root)
    assert (root / "eki" / "thing.py").read_text() == "VALUE = 1\n"
    assert selfwork.git(root, "rev-parse", "HEAD") == before
    assert selfwork.git(root, "status", "--porcelain") == ""
    # and the branch carries it
    assert selfwork.git(root, "show", f"{p.branch}:eki/thing.py") == "VALUE = 2"
    assert "written by claude in run r123" in selfwork.git(root, "log", "-1", "--format=%B", p.branch)


def test_the_agent_is_told_where_it_is_and_not_to_ask(tmp_path):
    ask = agent({"eki/thing.py": "VALUE = 2\n"})
    propose(tmp_path, repo(tmp_path), ask, python="/the/python", check_base=False)
    said = ask.seen["prompt"]
    assert "make VALUE two" in said and "eki's own source" in said
    assert "/the/python -m pytest" in said and "Don't ask questions" in said
    assert "eki/candidate.py" in said


def test_a_broken_base_stops_it_before_any_agent_is_asked(tmp_path):
    root = repo(tmp_path, passing=False)
    ask = agent({"eki/thing.py": "VALUE = 2\n"})
    p = propose(tmp_path, root, ask)
    assert not p.fit and "not started" in p.verdict and "doesn't pass its own tests" in p.verdict
    assert ask.seen == {}                                   # nothing was spent
    assert "self/" not in selfwork.git(root, "branch", "--list")
    assert not (tmp_path / "self" / p.id).exists()


def test_nothing_changed_leaves_nothing_behind(tmp_path):
    root = repo(tmp_path)
    p = propose(tmp_path, root, agent({}))
    assert p.verdict == "the agent changed nothing" and not p.commit
    assert "self/" not in selfwork.git(root, "branch", "--list")


def test_a_run_that_didnt_finish_keeps_its_worktree_for_a_look(tmp_path):
    p = propose(tmp_path, repo(tmp_path), agent({"eki/thing.py": "half"}, state="failed"))
    assert "ended failed" in p.verdict and not p.commit
    assert (Path(p.worktree) / "eki" / "thing.py").read_text() == "half"


def test_an_unfit_result_is_kept_but_says_so(tmp_path):
    root = repo(tmp_path)
    p = propose(tmp_path, root, agent({"eki/thing.py": "VALUE = 2\n"}), check=verdict(False))
    assert not p.fit and p.verdict.startswith("not fit to run")
    assert p.commit and "FAIL" in "\n".join(p.lines())


def test_protected_paths_are_for_a_person(tmp_path):
    p = propose(tmp_path, repo(tmp_path), agent({"eki/agent.py": "# changed\n",
                                                 "eki/quota/new.py": "x = 1\n",
                                                 "eki/thing.py": "VALUE = 2\n"}))
    assert sorted(p.protected) == ["eki/agent.py", "eki/quota/new.py"]
    assert "protected" in p.verdict and "!!" in "\n".join(p.lines())


def test_every_proposal_is_written_down(tmp_path):
    root = repo(tmp_path)
    propose(tmp_path, root, agent({"eki/thing.py": "VALUE = 2\n"}))
    propose(tmp_path, root, agent({}))
    log = selfwork.history(tmp_path / "self")
    assert [e["verdict"].split(" —")[0] for e in log] == ["fit to run", "the agent changed nothing"]
    assert log[0]["request"] == "make VALUE two" and log[0]["run"] == "r123"


def test_the_report_says_how_to_read_take_or_drop_it(tmp_path):
    p = propose(tmp_path, repo(tmp_path), agent({"eki/thing.py": "VALUE = 2\n"}))
    text = "\n".join(p.lines())
    assert f"merge {p.branch}" in text and "worktree remove" in text and "diff " in text


def test_not_a_repo(tmp_path):
    with pytest.raises(selfwork.SelfWorkError, match="isn't a git checkout"):
        selfwork.propose("x", root=tmp_path, ask=agent({}), home=tmp_path / "self")


def test_the_real_base_check_is_the_candidates_test_check(tmp_path):
    """No stand-in for this one: a passing base goes through, with real pytest."""
    p = propose(tmp_path, repo(tmp_path), agent({"eki/thing.py": "VALUE = 2\n"}),
                check_base=True)
    assert p.fit
