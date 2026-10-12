"""testpick: which tests gate 1 runs for a change, from a card's tests rules or the tests importing a file."""
import subprocess

import pytest

from eki import pathrules, testpick, workflow, workflowgates
from eki.testpick import Picked

RULES = pathrules.parse([
    {"name": "core", "paths": ["eki/db.py"], "tests": "all"},
    {"name": "queue", "paths": ["eki/queue*.py"], "tests": ["tests/test_queue*.py"]},
    {"name": "lint", "paths": ["eki/web/**"], "run": "true"},
])

TESTS = {
    "test_queue.py": "from eki import queue\n",
    "test_queue_stack.py": "from eki import queuestack\n",
    "test_db.py": "from eki import db\n",
    "test_from_list.py": "from eki import (\n    db,\n    x as ex,  # the one\n)\n",
    "test_dotted.py": "import eki.x\n",
    "test_other.py": "import eki.xyz\nfrom eki import xyz\n",
    "test_deep.py": "from eki.a import b\n",
    "test_web.py": "PAGE = 'eki/web/station.js'\n",
    "conftest.py": "import eki.x\n",
    "helpers.txt": "eki.x\n",
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests" / "__pycache__").mkdir(parents=True)
    for name, text in TESTS.items():
        (root / "tests" / name).write_text(text)
    (root / "tests" / "__pycache__" / "test_x.py").write_text("import eki.x\n")
    return root


def test_a_queue_change_picks_only_the_queue_tests(repo):
    got = testpick.pick(RULES, ["eki/queue.py"], repo)
    assert got == Picked(False, ("tests/test_queue.py", "tests/test_queue_stack.py"), ("eki/queue.py",))


def test_a_core_file_runs_the_whole_suite(repo):
    got = testpick.pick(RULES, ["eki/queue.py", "eki/db.py"], repo)
    assert got.whole and got.because == ("eki/db.py",)
    assert testpick.said(got) == "gate 1: whole fast suite (eki/db.py)"


def test_many_files_run_the_whole_suite(repo):
    got = testpick.pick(RULES, [f"docs/{n}.md" for n in range(26)], repo)
    assert got.whole and testpick.said(got) == "gate 1: whole fast suite (26 files changed)"
    assert not testpick.pick(RULES, [f"eki/queue{n}.py" for n in range(25)], repo).whole
    assert testpick.pick(RULES, ["eki/queue.py", "eki/queue2.py"], repo, whole_over=1).whole


def test_a_module_without_a_rule_draws_the_tests_importing_it(repo):
    assert testpick.importers(repo, "eki/x.py") == ["tests/test_dotted.py", "tests/test_from_list.py"]
    assert testpick.importers(repo, "eki/xyz.py") == ["tests/test_other.py"]
    assert testpick.importers(repo, "eki/a/b.py") == ["tests/test_deep.py"]
    got = testpick.pick(RULES, ["eki/x.py", "eki/queue.py"], repo)
    assert got.files == ("tests/test_dotted.py", "tests/test_from_list.py",
                         "tests/test_queue.py", "tests/test_queue_stack.py")
    assert testpick.said(got) == "gate 1: 4 test files for eki/x.py, eki/queue.py"


def test_a_module_with_no_tests_runs_the_whole_suite(repo):
    got = testpick.pick(RULES, ["eki/queue.py", "eki/newmod.py"], repo)
    assert got.whole and testpick.said(got) == "gate 1: whole fast suite (eki/newmod.py has no tests)"


def test_other_files_draw_tests_by_name_or_nothing(repo):
    got = testpick.pick(RULES, ["eki/web/station.js", "eki/web/board.css", "docs/x.md"], repo)
    assert got == Picked(False, ("tests/test_web.py",), ("eki/web/station.js",))
    none = testpick.pick(RULES, ["docs/x.md"], repo)
    assert none == Picked(False, (), ())
    assert testpick.said(none) == "gate 1: no tests for these files"


def test_a_touched_test_picks_itself(repo):
    assert testpick.pick(RULES, ["tests/test_db.py"], repo).files == ("tests/test_db.py",)


def test_line_quotes_the_files_and_echoes_nothing_when_empty():
    cmd = "EKI_CHECK_FAST=1 bin/check -q"
    assert testpick.line(Picked(True, (), ("eki/db.py",)), cmd) == cmd
    assert testpick.line(Picked(False, ("tests/a b.py", "tests/c.py"), ("x",)), cmd) == \
        f"{cmd} 'tests/a b.py' tests/c.py"
    empty = testpick.line(Picked(False, (), ()), cmd)
    assert subprocess.run(["/bin/sh", "-c", empty], capture_output=True, text=True).stdout.strip() == pathrules.NOTHING


def test_said_names_at_most_three_files():
    files = tuple(f"tests/t{n}.py" for n in range(6))
    assert testpick.said(Picked(False, files[:4], ("eki/queue.py",))) == "gate 1: 4 test files for eki/queue.py"
    assert testpick.said(Picked(False, files, ("eki/queue.py", "eki/queuestack.py", "eki/a.py", "eki/b.py",
                                              "eki/c.py"))) == \
        "gate 1: 6 test files for eki/queue.py, eki/queuestack.py, eki/a.py +2 more"
    assert testpick.said(Picked(True, (), ("31 files changed",))) == "gate 1: whole fast suite (31 files changed)"


def _card(rules, **kw):
    return workflow.Workflow(router="on", target="", plan="split", parallel=1, checks=("self", "command"),
                             command="", merge="person", playtester="", card="x", rules=rules,
                             test_command="run-tests", **kw)


def test_the_gate_runs_the_picked_tests_in_the_worktree(repo):
    wf = _card(RULES)
    argv, says = workflowgates.gate(wf, "t", ["eki/queue.py"], root=repo)
    assert "run-tests tests/test_queue.py tests/test_queue_stack.py" in argv
    assert says == "gate 1: 2 test files for eki/queue.py"
    assert workflow.check_line(wf, ["eki/db.py"], repo) == "run-tests"


def test_run_rules_follow_the_tests_and_both_must_pass(repo):
    wf = _card(pathrules.parse([{"paths": ["eki/web/**"], "tests": ["tests/test_web.py"]},
                                {"name": "lint", "paths": ["eki/web/**"], "run": "false"}]))
    wf = workflow.Workflow(**{**wf.__dict__, "test_command": "true"})
    line = workflow.check_line(wf, ["eki/web/station.js"], repo)
    assert subprocess.run(["/bin/sh", "-c", line], capture_output=True).returncode != 0
    _, says = workflowgates.gate(wf, "t", ["eki/web/station.js"], root=repo)
    assert says == "gate 1: 1 test file for eki/web/station.js · lint: false"
    assert workflow.each_said(_card(RULES)) == \
        "Each change: core → whole suite · queue → tests/test_queue*.py · lint → true"


def test_a_folder_wide_rule_adds_its_tests_on_top_of_the_scan(repo):
    rules = pathrules.parse([{"name": "size", "paths": ["eki/**"], "tests": ["tests/test_db.py"]},
                             {"name": "queue", "paths": ["eki/queue*.py"], "tests": ["tests/test_queue.py"]}])
    assert testpick.pick(rules, ["eki/x.py"], repo).files == \
        ("tests/test_db.py", "tests/test_dotted.py", "tests/test_from_list.py")
    assert testpick.pick(rules, ["eki/queue.py"], repo).files == ("tests/test_db.py", "tests/test_queue.py")
    assert testpick.pick(rules, ["eki/newmod.py"], repo).whole
