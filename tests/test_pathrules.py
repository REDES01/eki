"""Path rules: .gitignore-style matching, the rules of a card's `command` list, and the run-all script."""
import subprocess

import pytest

from eki import pathrules
from eki.pathrules import Rule


@pytest.mark.parametrize("pattern, path, hit", [
    ("design/script/**", "design/script/a/b.yaml", True),
    ("design/script/**", "design/scripts/x", False),
    ("design/script/**", "design/script", False),
    ("*.md", "docs/x/y.md", True),
    ("*.md", "README.md", True),
    ("*.md", "docs/x.mdx", False),
    ("/tools/*", "tools/a.ts", True),
    ("/tools/*", "tools/a/b.ts", False),
    ("/tools/*", "x/tools/a.ts", False),
    ("tools/", "tools/a/b.ts", True),
    ("tools/", "slice/tools/a.ts", True),
    ("tools/", "tools", False),
    ("slice/tools/", "slice/tools/a.ts", True),
    ("slice/tools/", "x/slice/tools/a.ts", False),
    ("**/data/*.json", "data/a.json", True),
    ("**/data/*.json", "slice/data/a.json", True),
    ("a/**/b.txt", "a/b.txt", True),
    ("a/**/b.txt", "a/x/y/b.txt", True),
    ("file?.ts", "src/file1.ts", True),
    ("file?.ts", "src/file/.ts", False),
    ("[ab].ts", "x/a.ts", True),
    ("[!ab].ts", "x/a.ts", False),
    ("Makefile", "sub/Makefile", True),
])
def test_matching_follows_gitignore(pattern, path, hit):
    assert pathrules.matches(pattern, path) is hit


def test_a_bang_pattern_is_refused():
    with pytest.raises(ValueError) as e:
        pathrules.parse([{"paths": ["!x/**"], "run": "true"}])
    assert str(e.value) == "paths: `!` patterns aren't supported"
    assert not pathrules.matches("!x", "x")


@pytest.mark.parametrize("value, words", [
    ("make", "command must be text or a list of rules"),
    ([], "command needs at least one rule"),
    (["x"], "command: rule 1 must be an object"),
    ([{"paths": ["a"]}], "command: rule 1 needs run"),
    ([{"paths": ["a"], "run": "x"}, {"paths": ["b"], "run": "y", "tests": "all"}],
     "command: rule 2 has both run and tests"),
    ([{"paths": ["a"], "tests": []}], "command: rule 1's tests must be"),
    ([{"paths": ["a"], "tests": "some"}], "command: rule 1's tests must be"),
    ([{"paths": ["a"], "tests": [""]}], "command: rule 1's tests must be"),
    ([{"run": "x"}], "paths: rule 1 needs paths"),
    ([{"paths": [], "run": "x"}], "paths: rule 1 needs paths"),
    ([{"paths": [""], "run": "x"}], "paths: rule 1 needs paths"),
    ([{"paths": ["a"], "run": "x", "when": 1}], "command: rule 1 has unknown keys: when"),
    ([{"paths": ["a"], "run": "x", "name": 3}], "command: rule 1's name must be text"),
])
def test_bad_rules_say_why(value, words):
    with pytest.raises(ValueError) as e:
        pathrules.parse(value)
    assert str(e.value).startswith(words)


def test_a_tests_rule_parses_as_patterns_or_the_whole_suite():
    queue, core = pathrules.parse([{"name": "queue", "paths": ["eki/queue*.py"], "tests": ["tests/test_queue*.py"]},
                                   {"name": "core", "paths": ["eki/db.py"], "tests": "all"}])
    assert queue == Rule(paths=("eki/queue*.py",), run="", name="queue", tests=("tests/test_queue*.py",))
    assert core.tests == pathrules.ALL and core.run == ""


def test_the_script_runs_only_run_rules():
    rules = pathrules.parse([{"paths": ["a"], "tests": "all"}, {"paths": ["a"], "run": "true"}])
    assert pathrules.script(rules[:1]) == pathrules.script([])
    assert "== a: true" in pathrules.script(rules)


GAME = [{"name": "story files", "paths": ["design/script/**", "design/quests/**", "slice/data/**"],
         "run": "npm run check:story"},
        {"name": "code", "paths": ["slice/src/**", "slice/tools/**", "tools/**"],
         "run": "npm run typecheck && npm run build"}]


def test_chosen_keeps_card_order_and_takes_each_once():
    story, code = pathrules.parse(GAME)
    assert pathrules.chosen((story, code), ["tools/x.ts", "design/script/a.yaml", "slice/data/b.json"]) == [story, code]
    assert pathrules.chosen((story, code), ["design/script/a.yaml"]) == [story]
    assert pathrules.chosen((story, code), ["README.md"]) == []


def test_label_is_the_name_or_the_paths():
    assert pathrules.label(Rule(paths=("a/**", "b/"), run="x")) == "a/**, b/"
    assert pathrules.label(Rule(paths=("a/**",), run="x", name="story")) == "story"


def sh(text):
    return subprocess.run(["/bin/sh", "-c", text], capture_output=True, text=True, timeout=30)


def test_the_script_runs_every_rule_and_is_red_when_any_was():
    out = sh(pathrules.script([Rule(paths=("a",), run="echo first; false", name="it's one"),
                               Rule(paths=("b",), run="echo second")]))
    assert out.returncode != 0
    assert out.stdout.splitlines() == ["== it's one: echo first; false", "first", "== b: echo second", "second"]


def test_the_script_is_green_when_every_rule_is():
    assert sh(pathrules.script([Rule(paths=("a",), run="true # a comment")])).returncode == 0


def test_no_rules_prints_the_note_and_is_green():
    out = sh(pathrules.script([]))
    assert out.returncode == 0 and out.stdout.strip() == pathrules.NOTHING
