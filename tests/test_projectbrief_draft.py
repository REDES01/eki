"""The draft brief for a project goal (eki/projectbrief.py draft)."""
from eki import projectbrief, selfbrief

BASE = "0123456789abcdef0123"
PROJECT = {"name": "garden", "path": "/Users/x/garden", "branch": "trunk"}


def test_project_draft_brief_names_the_project_and_ends_like_the_eki_one():
    b = projectbrief.draft("grow tomatoes\nin the shade", BASE, PROJECT)
    assert "grow tomatoes" in b.splitlines()[0] and "in the shade" in b
    assert "garden" in b and "/Users/x/garden" in b and "trunk" in b
    assert BASE[:12] in b and BASE not in b
    for h in ("What must be true when done", "Read first", "Item shape and shared files",
              "Tests (bin/check green)"):
        assert h in b
    assert "DRAFT: done" in b and b.endswith(selfbrief.DRAFT_ASK)
    assert "README" in b and "AGENTS.md" in b and "Do not change any file" in b.replace("\n", " ")
    for eki_only in ("eki observe", "docs/design.md", "docs/self-build.md", "ROADMAP.md", "digest",
                     "old eki"):
        assert eki_only not in b
    assert "knowledge documents" not in b                     # no cards: no section


def test_an_empty_wish_and_its_answer_is_read_by_goal_in():
    assert "(empty wish)" in projectbrief.draft("", BASE, PROJECT)
    goal, _ = selfbrief.goal_in("GOAL:\nWhat must be true when done: x.\nDRAFT: done\n")
    assert goal == "What must be true when done: x."


def test_project_draft_asks_for_the_checks():
    b = projectbrief.draft("grow tomatoes", BASE, PROJECT)
    assert selfbrief.CHECKS_ASK in b and "C<n>:" in b
    assert b.index("Tests (bin/check green)") < b.index("a `Checks` heading")
