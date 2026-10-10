import pytest

from eki import targets
from eki.targets import Target


@pytest.mark.parametrize("s,want", [
    ("claude", Target("claude")),
    ("claude:fast", Target("claude", tier="fast")),
    ("claude:claude-haiku-4-5", Target("claude", model="claude-haiku-4-5")),
    ("codex:top", Target("codex", tier="top")),
])
def test_parse_and_fmt_round_trip(s, want):
    assert targets.parse(s) == want
    assert targets.fmt(targets.parse(s)) == s


@pytest.mark.parametrize("bad", ["", ":fast", "claude:"])
def test_parse_refuses_an_empty_part(bad):
    with pytest.raises(ValueError):
        targets.parse(bad)


def test_provider_of():
    assert targets.provider_of("claude:fast") == "claude"
    assert targets.provider_of("local") == "local"


CFG = {"kind": "claude_code", "model": "claude-sonnet-4-5",
       "models": {"fast": "claude-haiku-4-5", "top": "claude-opus-4-1"}}


def test_resolve_a_tier_from_models():
    assert targets.resolve("claude:fast", CFG, None) == ("claude-haiku-4-5", "")
    assert targets.resolve(Target("claude", tier="top"), CFG, None) == ("claude-opus-4-1", "")


def test_resolve_default_falls_back_to_the_old_model():
    assert targets.resolve("claude:default", CFG, None) == ("claude-sonnet-4-5", "")
    picked = {**CFG, "models": {**CFG["models"], "default": "claude-sonnet-5"}}
    assert targets.resolve("claude:default", picked, None) == ("claude-sonnet-5", "")


def test_resolve_an_unpicked_tier_is_the_default_with_a_note():
    assert targets.resolve("claude:fast", {"kind": "claude_code"}, None) == \
        (None, "claude has no fast model picked; its default")


def test_resolve_a_model_gone_from_the_read_list():
    listed = ["claude-haiku-4-5", "claude-sonnet-4-5"]
    assert targets.resolve("claude:claude-opus-4-1", CFG, listed) == \
        (None, "claude-opus-4-1 isn't in claude's list any more; its default")
    assert targets.resolve("claude:top", CFG, listed)[0] is None
    assert targets.resolve("claude:fast", CFG, listed) == ("claude-haiku-4-5", "")


def test_resolve_an_unread_list_trusts_the_name():
    assert targets.resolve("claude:claude-opus-4-1", CFG, None) == ("claude-opus-4-1", "")


def test_resolve_a_bare_provider_is_its_default():
    assert targets.resolve("claude", CFG, ["x"]) == (None, "")


def test_problems():
    known = {"claude", "local"}
    assert targets.problems("claude:fast", known) == []
    assert targets.problems("claude:claude-haiku-4-5", known) == []
    assert any("unknown provider 'nope'" in p for p in targets.problems("nope:fast", known))
    assert targets.problems("claude:", known)
    assert targets.problems("claude:a b", known)
    assert targets.problems(3, known)


# ---- every written form ---------------------------------------------------------------

@pytest.mark.parametrize("s,want", [
    ("claude:default", Target("claude", tier="default")),
    ("claude:top", Target("claude", tier="top")),
    ("claude:opus", Target("claude", model="opus")),
    ("claude:claude-opus-4-8", Target("claude", model="claude-opus-4-8")),
    ("local:qwen3:32b", Target("local", model="qwen3:32b")),
])
def test_every_old_string_still_parses(s, want):
    assert targets.parse(s) == want
    assert targets.fmt(want) == s
    assert targets.key(s) == s


@pytest.mark.parametrize("obj,want,short", [
    ({"provider": "claude"}, Target("claude"), "claude"),
    ({"provider": "claude", "model": "opus"}, Target("claude", model="opus"), "claude:opus"),
    ({"provider": "claude", "model": "fast"}, Target("claude", tier="fast"), "claude:fast"),
    ({"provider": "claude", "model": "opus", "effort": "high"},
     Target("claude", model="opus", effort="high"), {"provider": "claude", "model": "opus", "effort": "high"}),
    ({"provider": "claude", "effort": "low"}, Target("claude", effort="low"),
     {"provider": "claude", "effort": "low"}),
    ({"provider": "local", "model": "qwen3:32b"}, Target("local", model="qwen3:32b"), "local:qwen3:32b"),
])
def test_objects_parse_and_fmt_is_the_shortest_form(obj, want, short):
    assert targets.parse(obj) == want
    assert targets.fmt(want) == short
    assert targets.parse(short) == want


def test_key_is_stable_and_one_line():
    assert targets.key({"provider": "claude", "model": "opus", "effort": "high"}) == "claude:opus@high"
    assert targets.key(Target("claude", model="opus", effort="high")) == "claude:opus@high"
    assert targets.key({"effort": "high", "model": "opus", "provider": "claude"}) == "claude:opus@high"
    assert targets.key("claude:fast") == "claude:fast"
    assert targets.key({"provider": "claude", "effort": "low"}) == "claude@low"


def test_provider_of_an_object():
    assert targets.provider_of({"provider": "codex", "model": "gpt-5"}) == "codex"
    assert targets.provider_of({"model": "x"}) == ""


def test_problems_with_objects():
    known = {"claude", "local"}
    assert targets.problems({"provider": "claude", "model": "opus", "effort": "high"}, known) == []
    assert targets.problems({"provider": "local", "model": "qwen3:32b"}, known) == []
    assert any("unknown keys: tier" in p for p in targets.problems({"provider": "claude", "tier": "x"}, known))
    assert targets.problems({"provider": ""}, known)
    assert targets.problems({"model": "opus"}, known)
    assert any("unknown provider 'nope'" in p for p in targets.problems({"provider": "nope"}, known))
    assert any("effort 3" in p for p in targets.problems({"provider": "claude", "effort": 3}, known))
    assert targets.problems({"provider": "claude", "model": ["opus"]}, known)
    assert targets.problems({"provider": "claude", "model": "a b"}, known)
    assert targets.problems(["claude"], known)


# ---- choose: against the read list ----------------------------------------------------

LISTED = [
    {"model": "default", "label": "Default (recommended)", "description": "", "resolved": "claude-opus-5-5",
     "efforts": ["low", "medium", "high"], "default_effort": "medium", "is_default": True, "floating": True},
    {"model": "opus", "label": "Opus 5.5", "description": "", "resolved": "claude-opus-5-5",
     "efforts": ["low", "medium", "high", "max"], "default_effort": "high", "is_default": False,
     "floating": True},
    {"model": "haiku", "label": "Haiku 4.5", "description": "", "resolved": "claude-haiku-4-5",
     "efforts": None, "default_effort": None, "is_default": False, "floating": True},
]


def test_choose_what_is_offered():
    assert targets.choose({"provider": "claude", "model": "opus", "effort": "max"}, CFG, LISTED) == \
        ("opus", "max", "")
    assert targets.choose({"provider": "claude", "effort": "high"}, CFG, LISTED) == (None, "high", "")


def test_choose_a_model_no_longer_listed_is_the_default():
    got = targets.choose({"provider": "claude", "model": "sonnet"}, CFG, LISTED)
    assert got == (None, None, "sonnet isn't in claude's list any more; its default")


def test_choose_an_effort_the_model_dropped():
    got = targets.choose({"provider": "claude", "model": "opus", "effort": "ultra"}, CFG, LISTED)
    assert got == ("opus", None, "claude opus doesn't offer effort ultra any more; its default")
    got = targets.choose({"provider": "claude", "effort": "max"}, CFG, LISTED)
    assert got == (None, None, "claude default doesn't offer effort max any more; its default")


def test_choose_an_effort_on_a_model_that_takes_none():
    got = targets.choose({"provider": "claude", "model": "haiku", "effort": "low"}, CFG, LISTED)
    assert got == ("haiku", None, "haiku takes no effort")


def test_choose_trusts_a_never_read_or_id_only_list():
    obj = {"provider": "claude", "model": "whatever", "effort": "ultra"}
    assert targets.choose(obj, CFG, None) == ("whatever", "ultra", "")
    assert targets.choose(obj, CFG, ["whatever"]) == ("whatever", "ultra", "")


def test_choose_a_tier_still_resolves_through_models():
    assert targets.choose("claude:fast", CFG, None) == ("claude-haiku-4-5", None, "")
    assert targets.choose({"provider": "claude", "model": "top", "effort": "high"}, CFG, None) == \
        ("claude-opus-4-1", "high", "")
    assert targets.choose("claude:fast", {}, LISTED) == (None, None, "claude has no fast model picked; its default")


def test_label():
    assert targets.label("claude", "opus", "high", LISTED) == "claude · Opus 5.5 · high"
    assert targets.label("claude", None, None, LISTED) == "claude · Default (recommended)"
    assert targets.label("claude", None, None, None) == "claude · its default"
    assert targets.label("claude", "gone", None, LISTED) == "claude · gone"
    assert targets.label("local", "qwen", None, ["qwen"]) == "local · qwen"


# ---- the columns ------------------------------------------------------------------------

def test_the_new_columns_and_create_run_effort():
    import json
    from eki import db, modellist, store
    conn = db.connect()
    have = {r["name"] for r in conn.execute("PRAGMA table_info(provider_models)")}
    assert {"description", "resolved", "efforts", "default_effort"} <= have
    assert "effort" in {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    tid = store.create_thread(conn, "t", None)
    rid = store.create_run(conn, tid, "hi", model="opus", effort="high")
    assert (store.run(conn, rid)["model"], store.run(conn, rid)["effort"]) == ("opus", "high")
    assert store.run(conn, store.create_run(conn, tid, "hi"))["effort"] is None
    rows = [("default", "Default", None, "claude-opus-5-5", json.dumps(["low", "high"]), "high", 1),
            ("claude-haiku-4-5", None, "small", "claude-haiku-4-5", None, None, 0),
            ("opus", "Opus", None, None, None, None, 0)]
    for r in rows:
        conn.execute("INSERT INTO provider_models(provider, model, label, description, resolved, efforts,"
                     " default_effort, is_default, read_at) VALUES ('claude',?,?,?,?,?,?,?,0)", r)
    got = modellist.listed(conn, "claude")
    assert got[0] == {"model": "default", "label": "Default", "description": None,
                      "resolved": "claude-opus-5-5", "efforts": ["low", "high"], "default_effort": "high",
                      "is_default": True, "floating": True}
    assert (got[1]["label"], got[1]["efforts"], got[1]["floating"]) == ("claude-haiku-4-5", None, False)
    assert got[2]["floating"] is False
