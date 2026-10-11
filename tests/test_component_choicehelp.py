"""Every value of a fixed-choice setting has one line of help, declared by the kind next to its CHOICES."""
import json

from eki import components

ACCESS_HELP = {"queue": "Calls wait in line and are served in order, a few at a time (slots).",
               "exclusive": "One run holds it from first use until that run ends; others wait.",
               "shared": "Any number of runs can use it at once."}


def test_knowledge_explains_read():
    assert components.choice_help("knowledge") == {
        "access": ACCESS_HELP,
        "read": {"always": "Every run in this project reads it first, planned or not: "
                 "keep it for the few documents that apply to all work.",
                 "when-relevant": ("The planner picks it for the items that need it; other runs are "
                                   "told it exists and open it when their work touches it.")}}


def test_agent_explains_bare_with_json_keys():
    assert components.choice_help("agent") == {
        "access": ACCESS_HELP,
        "bare": {"true": "On: the agent gets only its system prompt — no skills, settings, project files "
                         "or other tools.",
                 "false": "Off: the agent gets its skills, settings and project files as usual."}}


def test_a_kind_without_its_own_and_an_unknown_one_get_the_common_ones():
    assert components.choice_help("assets") == {"access": ACCESS_HELP}
    for kind in ("nope", "", None):
        assert components.choice_help(kind) == {"access": ACCESS_HELP}


def test_the_returned_dicts_are_copies():
    components.choice_help("knowledge")["read"]["always"] = "x"
    components.choice_help("knowledge")["access"]["queue"] = "x"
    assert components.choice_help("knowledge")["read"]["always"].startswith("Every run")
    assert components.choice_help("knowledge")["access"] == ACCESS_HELP


def test_it_survives_json():
    got = components.choice_help("agent")
    assert json.loads(json.dumps(got)) == got


def test_every_declared_choice_has_help_and_no_help_is_stray():
    for kind in components.KINDS:
        choices, help_ = components.choices(kind), components.choice_help(kind)
        for key, values in choices.items():
            for v in values:
                text = help_.get(key, {}).get(components._json_key(v), "")
                assert isinstance(text, str) and text.strip(), f"{kind}: {key} = {v!r} has no help text"
        for key, texts in help_.items():
            assert key in choices, f"{kind}: help for {key!r}, which is not a choice"
            wanted = {components._json_key(v) for v in choices[key]}
            for v in texts:
                assert v in wanted, f"{kind}: help for {key} = {v!r}, which is not one of its choices"
