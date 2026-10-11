"""Kinds declare the settings whose values come from a fixed set, next to their validate."""
from eki import components
from eki.components import knowledge

ACCESS = ["queue", "exclusive", "shared"]


def test_knowledge_adds_read_and_has_no_read_default():
    assert components.choices("knowledge") == {"access": ACCESS, "read": ["always", "when-relevant"]}
    assert components.choice_defaults("knowledge") == {"access": "shared"}


def test_agent_bare_is_a_switch_defaulting_off():
    assert components.choices("agent") == {"access": ACCESS, "bare": [False, True]}
    assert components.choice_defaults("agent") == {"access": "shared", "bare": False}


def test_a_kind_without_its_own_gets_the_common_ones():
    assert components.choices("assets") == {"access": ACCESS}
    assert components.choice_defaults("assets") == {"access": "shared"}


def test_an_unknown_or_empty_kind_gets_the_common_ones():
    for kind in ("nope", "", None):
        assert components.choices(kind) == {"access": ACCESS}
        assert components.choice_defaults(kind) == {"access": "shared"}


def test_the_returned_lists_are_copies():
    components.choices("knowledge")["read"].append("never")
    components.choices("knowledge")["access"].append("never")
    assert components.choices("knowledge")["read"] == ["always", "when-relevant"]
    assert components.choices("knowledge")["access"] == ACCESS


def test_knowledge_refuses_a_read_outside_its_choices(tmp_path):
    spec = {"kind": "knowledge", "about": "x", "files": ["a.md"]}
    assert knowledge.validate({**spec, "read": "always"}, tmp_path) == []
    errs = knowledge.validate({**spec, "read": "sometimes"}, tmp_path)
    assert errs == ["read must be one of always, when-relevant (got 'sometimes')"]


def test_access_is_checked_against_the_declared_list():
    base = {"kind": "assets", "about": "x"}
    assert not any("access" in e for e in components._check_common(base))
    for a in ACCESS:
        assert not any("access" in e for e in components._check_common({**base, "access": a}))
    assert "access must be one of queue, exclusive, shared" in components._check_common({**base, "access": "x"})
