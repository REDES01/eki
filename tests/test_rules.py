"""Rules first: the obvious requests are sorted without a model."""
import json

import pytest

from eki import paths
import importlib

checking = importlib.import_module("eki.routing.check")
from eki.routing import rules

ALL = {"general", "code", "answer", "image", "image-edit", "vision"}


def match(prompt, keys=ALL, **kw):
    return rules.match(prompt, keys=keys, **kw)


def test_a_folder_is_code(tmp_path):
    assert match("what is this", cwd=str(tmp_path)) == ("code", "rule: has a folder")


def test_the_scratch_folder_is_not_a_folder():
    assert match("what is this", cwd=str(paths.scratch())) is None


def test_a_path_that_exists_is_code(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    assert match(f"what's in {tmp_path}/notes.txt?") == ("code", "rule: names a path")
    assert match("what's in /no/such/place/at/all") is None


def test_a_relative_path_resolves_against_scratch():
    (paths.scratch() / "sub").mkdir()
    (paths.scratch() / "sub" / "a.py").write_text("")
    assert match("look at sub/a.py", cwd=str(paths.scratch())) == ("code", "rule: names a path")
    assert match("look at ./sub/a.py.") == ("code", "rule: names a path")


def test_a_path_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    assert match("how big is ~/Downloads") == ("code", "rule: names a path")


def test_edit_the_picture():
    assert match("make it brighter", attachments=["a.png"]) == ("image-edit", "rule: edit the picture")
    assert match("Please remove the cat", attachments=["a.JPG"]) == ("image-edit", "rule: edit the picture")


def test_a_picture_attached():
    assert match("what is this", attachments=["a.png"]) == ("vision", "rule: a picture attached")


def test_a_picture_without_a_vision_row_is_answer():
    assert match("what is this", keys=ALL - {"vision"}, attachments=["a.png"]) == \
        ("answer", "rule: a picture attached")


def test_a_file_that_isnt_a_picture_is_no_picture():
    assert match("what is this", attachments=["a.pdf"]) is None


def test_asks_for_a_picture():
    assert match("Draw a fox in the snow") == ("image", "rule: asks for a picture")
    assert match("  ...generate an image of a lighthouse") == ("image", "rule: asks for a picture")


@pytest.mark.parametrize("prompt,verb", [("fix the failing test", "fix"), ("Add a --json flag", "add a"),
                                         ("run the tests", "run"), ("DEPLOY it", "deploy"),
                                         ("- refactor store.py", "refactor")])
def test_a_verb_of_doing_is_code(prompt, verb):
    assert match(prompt) == ("code", f"rule: starts with {verb}")


def test_a_verb_needs_a_word_boundary():
    assert match("running shoes for flat feet") is None
    assert match("fixtures in pytest, explained") is None


def test_a_rewrite_of_the_last_answer():
    for p in ("make it about rain", "shorter", "in Japanese", "Rewrite it as a limerick"):
        assert match(p, previous="a haiku about snow") == ("answer", "rule: rewrites the last answer"), p


def test_a_follow_up_without_a_previous_answer_doesnt_fire():
    assert match("make it about rain") is None


def test_a_long_follow_up_isnt_a_rewrite():
    assert match("make it about rain and also the history of weather forecasting in Europe",
                 previous="a haiku") is None


def test_a_rule_whose_row_is_missing_doesnt_fire():
    assert match("draw a fox", keys={"general", "code"}) is None
    assert match("what is this", keys={"general"}, attachments=["a.png"]) is None
    assert match("fix it", keys={"general"}) is None


def test_the_first_rule_wins(tmp_path):
    assert match("draw a fox", cwd=str(tmp_path)) == ("code", "rule: has a folder")


def _rows(home, *extra):
    got = json.loads((home / "routing.json").read_text())
    got["rows"] += [{"key": k, "title": k, "needs": [k if k != "answer" else "text"],
                     "targets": ["fake"]} for k in extra]
    (home / "routing.json").write_text(json.dumps(got))


def test_check_uses_the_rules_first(home, monkeypatch):
    _rows(home, "image")
    monkeypatch.setattr(checking.providers, "get", lambda n: pytest.fail("asked a model"))
    assert checking.check("draw a fox", checker="none") == ("image", "rule: asks for a picture")
    assert checking.check("fix the tests", checker="none") == ("code", "rule: starts with fix")


def test_check_passes_attachments(home):
    _rows(home, "answer")
    assert checking.check("what is this", checker="none", attachments=["x.png"]) == \
        ("answer", "rule: a picture attached")


def test_no_rule_is_general_with_the_checker_reason():
    assert checking.check("what's a good name for a cat", checker="none") == \
        ("general", "no prompt checker configured")


def test_an_off_checker_doesnt_wake_when_told_not_to(home, monkeypatch):
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"}, "tiny": {"kind": "local", "base_url": "http://127.0.0.1:9", "serve": "x"}}))
    got = json.loads((home / "routing.json").read_text())
    got["checker_wakes"] = False
    (home / "routing.json").write_text(json.dumps(got))
    from eki import models
    monkeypatch.setattr(models, "ensure", lambda name: pytest.fail("woke the checker"))
    assert checking.check("what's a good name for a cat", checker="tiny") == \
        ("general", "prompt check skipped: tiny is off (checker_wakes is false)")


def test_the_menu_has_needs_and_tags(home):
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake", "about": "A pretend harness."}, "bare": {"kind": "fake", "harness": False}}))
    _rows(home, "image")
    table = json.loads((home / "routing.json").read_text())["rows"]
    table[1]["targets"] = ["fake", "bare"]
    menu = checking._menu(table)
    assert "- code: tools" in menu and "needs tools" in menu and "needs image" in menu
    assert "fake [text, tools] — A pretend harness." in menu and "bare [text]" in menu
    assert "- general" not in menu
