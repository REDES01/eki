"""The draft brief and reading a drafter's GOAL: block (eki/selfbrief.py)."""
from eki import selfbrief

BASE = "0123456789abcdef0123"


def test_brief_has_wish_first_base_and_shape():
    wish = "ask about the colours\nand make the window nicer"
    b = selfbrief.draft(wish, BASE)
    first = b.splitlines()[0]
    assert "ask about the colours" in first
    assert "and make the window nicer" in b
    assert BASE[:12] in b and BASE not in b
    for h in ("What must be true when done", "Read first", "Item shape and shared files",
              "Tests (bin/check green)"):
        assert h in b
    assert "AskUserQuestion" in b
    assert "GOAL:" in b and "DRAFT: done" in b and "DRAFT: person" in b
    assert "eki observe --since 7d --kind fault,correction" in b
    assert "Do not" in b and "change any file" in b
    assert "no digest yet" in b
    assert "old eki" not in b


def test_brief_names_digest_and_old_only_when_given():
    b = selfbrief.draft("wish", BASE, digest="/h/self/digests/2026-09-25.md",
                        old="/Users/x/eki-2026-09-26")
    assert "/h/self/digests/2026-09-25.md" in b
    assert "/Users/x/eki-2026-09-26" in b and "reference" in b
    assert "no digest yet" not in b


def test_goal_in_done():
    ans = "thinking...\nGOAL:\nWhat must be true when done\n- x\nDRAFT: done\n"
    assert selfbrief.goal_in(ans) == ("What must be true when done\n- x", "")


def test_goal_in_same_line_and_missing_draft_line():
    assert selfbrief.goal_in("GOAL: make it fast\nmore") == ("make it fast\nmore", "")


def test_goal_in_person():
    ans = "GOAL:\nsomething\nDRAFT: person the wish contradicts the design"
    assert selfbrief.goal_in(ans) == (None, "the wish contradicts the design")
    assert selfbrief.goal_in("DRAFT: person") == (None, "the drafter left it for you")


def test_goal_in_missing_or_empty_block():
    assert selfbrief.goal_in("no goal here\nDRAFT: done") == (None, "the draft has no GOAL: block")
    assert selfbrief.goal_in("GOAL:\n\nDRAFT: done") == (None, "the draft has no GOAL: block")
    assert selfbrief.goal_in("") == (None, "the draft has no GOAL: block")


def test_goal_in_fenced_multiline_and_last_block():
    ans = ("I'll end with GOAL: later.\nGOAL: old\nDRAFT: done\n"
           "Revised:\nGOAL:\n```\nWhat must be true when done\n1. a\n\nRead first\n- docs/design.md\n```\n"
           "DRAFT: done")
    goal, why = selfbrief.goal_in(ans)
    assert why == ""
    assert goal == "What must be true when done\n1. a\n\nRead first\n- docs/design.md"
