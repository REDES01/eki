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


def test_eki_never_applies_a_protected_change_on_its_own_but_a_person_can(tmp_path):
    root = repo(tmp_path)
    p = propose(tmp_path, root, agent({"eki/agent.py": "# changed\n", "eki/thing.py": "VALUE = 2\n"}))
    home, swaps = tmp_path / "self", []
    with pytest.raises(selfwork.SelfWorkError, match="a person applies it"):
        selfwork.apply(p.id, home=home, check=verdict(True), swap=lambda b, **kw: swaps.append(kw))
    assert not swaps and selfwork.change(p.id, home)["state"] == "proposed"
    got = selfwork.apply(p.id, home=home, check=verdict(True), by_person=True,
                         swap=lambda b, **kw: swaps.append(kw))
    assert got["state"] == "applying" and swaps == [{"self_id": p.id}]
    c = selfwork.change(p.id, home)
    assert c["applied_by"] == "you" and c["asked_at"]                  # who asked, written down
    fields = {k: v for k, v in c.items() if k in selfwork.Proposal.__dataclass_fields__}
    assert "applied because you asked for it" in "\n".join(selfwork.Proposal(**fields).lines())


def test_a_change_that_comes_to_touch_protected_paths_on_top_of_your_checkout_waits(tmp_path):
    root = repo(tmp_path)
    p = propose(tmp_path, root, agent({"eki/thing.py": "VALUE = 2\n"}))
    home = tmp_path / "self"
    assert not p.protected
    # your checkout moved on, and the branch picked up a protected file since it was judged
    (root / "README.md").write_text("moved on\n")
    selfwork.git(root, "add", "-A")
    selfwork.git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "yours")
    (Path(p.worktree) / "eki" / "agent.py").write_text("# sneaked in\n")
    subprocess.run(["git", "-C", p.worktree, "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qam", "more"], check=True)
    p.commit = selfwork.git(Path(p.worktree), "rev-parse", "HEAD")
    selfwork.record(p, home)
    got = selfwork.apply(p.id, home=home, check=verdict(True), swap=lambda b, **kw: None)
    assert got["state"] == "proposed" and "protected" in got["why"]
    assert selfwork.change(p.id, home)["protected"] == ["eki/agent.py"]


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
    assert f"merge {p.branch}" in text and f"eki self discard {p.id}" in text
    assert f"eki self apply {p.id}" in text and f"eki self diff {p.id}" in text


def test_not_a_repo(tmp_path):
    with pytest.raises(selfwork.SelfWorkError, match="isn't a git checkout"):
        selfwork.propose("x", root=tmp_path, ask=agent({}), home=tmp_path / "self")


def test_the_real_base_check_is_the_candidates_test_check(tmp_path):
    """No stand-in for this one: a passing base goes through, with real pytest."""
    p = propose(tmp_path, repo(tmp_path), agent({"eki/thing.py": "VALUE = 2\n"}),
                check_base=True)
    assert p.fit


def test_the_agent_is_asked_for_a_summary_and_it_is_read_back():
    assert "SUMMARY:" in selfwork.brief("x", "python")
    answer = ("Changed the list.\n\n**SUMMARY:**\n- The chat list shows project names.\n"
              "- Click a project to filter.\nITEM: done")
    assert selfwork.summary_of(answer) == "- The chat list shows project names.\n- Click a project to filter."
    assert selfwork.summary_of("no summary here") == ""
    from eki import selfloop
    assert selfloop.reason(answer) == "Changed the list."


def test_a_change_whose_swap_was_superseded_is_applied_with_the_build_that_carries_it(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "self"
    p = propose(tmp_path, root, agent({"eki/thing.py": "VALUE = 2\n"}))
    selfwork.set_state(p.id, "applying", home)
    # a later build, made on top of it
    selfwork.git(root, "checkout", "-q", "-b", "later", p.commit)
    (root / "eki" / "other.py").write_text("X = 1\n")
    selfwork.git(root, "add", "-A")
    selfwork.git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "later")
    later = selfwork.git(root, "rev-parse", "HEAD")
    assert selfwork.carried(root, selfwork.git(root, "rev-parse", "main"), home) == []   # not in that one
    assert selfwork.carried(root, later, home) == [p.id]
    c = selfwork.change(p.id, home)
    assert c["state"] == "applied" and "with a later change" in c["how"]


def test_an_app_change_is_looked_at_and_its_pictures_go_where_eki_shows_them(tmp_path):
    said = selfwork.brief("make the chat calmer", "python", str(tmp_path / "self" / "ab12cd34"))
    assert "eki_screenshot" in said and "EKI_APP_PATH=/tmp/eki-look.app" in said
    assert str(selfwork.SHOTS / "ab12cd34") in said and "before-<view>-<light|dark>.png" in said


def test_the_cli_asks_before_applying_a_protected_change(monkeypatch, capsys):
    from eki import cli
    c = {"id": "abc", "protected": ["eki/agent.py"]}
    assert cli._confirm_protected(c, yes=True)                            # --yes
    assert "eki/agent.py" in capsys.readouterr().err                      # shown either way
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _q: "y")
    assert cli._confirm_protected(c, yes=False)
    monkeypatch.setattr("builtins.input", lambda _q: "")
    assert not cli._confirm_protected(c, yes=False)                       # N is the default
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert not cli._confirm_protected(c, yes=False)                       # nobody to ask
    assert "--yes" in capsys.readouterr().err
