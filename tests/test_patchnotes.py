"""The short digest page's rules: areas, the rules-only page, the check."""
import time

import pytest

from eki import patchnotes as pn
from eki.patchnotes import Change

UNTIL = time.mktime((2026, 9, 28, 9, 0, 0, 0, 0, -1))


def body(page):
    """The lines between the title and the closing line."""
    lines = page.strip().splitlines()
    return "\n".join(lines[1:-1]).strip()


def sections(page):
    """{area: its '- ' lines} of a short page."""
    out, cur = {}, None
    for ln in page.splitlines():
        if ln in pn.AREAS:
            cur = out.setdefault(ln, [])
        elif ln.startswith("- ") and cur is not None:
            cur.append(ln)
    return out


@pytest.mark.parametrize("path,area", [
    ("docs/self-build.md", "Docs"), ("README.md", "Docs"), ("ROADMAP.md", "Docs"),
    ("eki/routing/table.py", "Routing"),
    ("eki/web/station.js", "Web UI"), ("eki/api_station.py", "Web UI"), ("eki/api_settings.py", "Web UI"),
    ("eki/providers/comfyui.py", "Pictures"), ("eki/comfyflow.py", "Pictures"), ("eki/gallery.py", "Pictures"),
    ("eki/providers/codex.py", "Models"), ("eki/models.py", "Models"),
    ("eki/selfwork.py", "Self-build"), ("eki/queue.py", "Self-build"), ("eki/train.py", "Self-build"),
    ("eki/trainwatch.py", "Self-build"), ("eki/builds.py", "Self-build"), ("eki/resolve.py", "Self-build"),
    ("eki/rebase.py", "Self-build"), ("eki/integration.py", "Self-build"), ("eki/review.py", "Self-build"),
    ("eki/faults.py", "Self-build"), ("eki/score.py", "Self-build"), ("eki/digestprose.py", "Self-build"),
    ("eki/patchnotes.py", "Self-build"), ("eki/chores.py", "Self-build"),
    ("eki/engine.py", "Engine"), ("bin/check", "Engine"), ("eki/docs/x.md", "Engine"),
])
def test_area_of_each_rule(path, area):
    assert pn.area_of([path]) == area


def test_area_of_ignores_tests_and_counts_the_most():
    assert pn.area_of(["tests/test_web.py", "tests/test_x.py", "eki/models.py"]) == "Models"
    assert pn.area_of(["eki/web/a.js", "eki/web/b.js", "eki/models.py"]) == "Web UI"


def test_area_of_ties_go_to_the_earlier_rule():
    assert pn.area_of(["eki/models.py", "eki/web/a.js"]) == "Web UI"
    assert pn.area_of(["eki/engine.py", "eki/queue.py"]) == "Self-build"
    assert pn.area_of(["eki/routing/t.py", "docs/a.md"]) == "Docs"


def test_area_of_defaults_to_engine():
    assert pn.area_of([]) == "Engine"
    assert pn.area_of(["tests/test_a.py"]) == "Engine"


def test_kind_of():
    assert pn.kind_of(["tests/test_a.py", "tests/conftest.py"]) == "tests"
    assert pn.kind_of(["docs/a.md", "README.md"]) == "docs"
    assert pn.kind_of(["eki/queue.py", "tests/test_queue.py"]) == "change"
    assert pn.kind_of([]) == "change"


def test_title_and_closing():
    assert pn.title(UNTIL) == "# eki 09-28"
    assert pn.closing(0, "same as yesterday") == "Waits for you: nothing.  Score: same as yesterday."
    assert pn.closing(3, "better than yesterday") == "Waits for you: 3.  Score: better than yesterday."


def test_one_change_is_one_line():
    page = pn.rules_page([Change("a1b2c3d4e5", "Station folds", "Web UI", "change")], UNTIL, 0, "same as yesterday")
    assert page.splitlines()[:2] == ["# eki 09-28", "1 change landed: Web UI."]
    assert sections(page) == {"Web UI": ["- Station folds"]}
    assert page.rstrip().endswith("Waits for you: nothing.  Score: same as yesterday.")
    assert "a1b2c3d4e5" not in page and "## " not in page


def test_many_changes_keep_the_limits():
    long = " ".join(f"word{i}" for i in range(30))
    changes = [Change(f"{i:010x}", f"change {i} {long}", pn.AREAS[i % len(pn.AREAS)], "change")
               for i in range(20)]
    page = pn.rules_page(changes, UNTIL, 2, "same as yesterday")
    secs = sections(page)
    assert all(len(v) <= pn.SECTION_LINES for v in secs.values())
    assert sum(len(v) for v in secs.values()) <= pn.PAGE_LINES
    assert all(len(ln[2:].split()) <= pn.LINE_WORDS for v in secs.values() for ln in v)
    assert len(pn.headline(page).split()) <= pn.HEADLINE_WORDS
    assert pn.headline(page).startswith("20 changes landed: Self-build, Web UI")
    assert list(secs) == list(pn.AREAS)
    assert pn.check(body(page), pn.areas_in(page)) is None


def test_no_changes():
    page = pn.rules_page([], UNTIL, 1, "same as yesterday")
    assert pn.headline(page) == "Nothing new landed."
    assert sections(page) == {} and pn.areas_in(page) == []
    assert page.rstrip().splitlines()[-1] == "Waits for you: 1.  Score: same as yesterday."


def test_docs_one_line_and_tests_left_out():
    changes = [Change("1", "Explain the train", "Docs", "docs"),
               Change("2", "Explain the digest", "Docs", "docs"),
               Change("3", "More tests for the queue", "Engine", "tests")]
    page = pn.rules_page(changes, UNTIL, 0, "same as yesterday")
    assert sections(page) == {"Docs": ["- Explain the train; Explain the digest"]}
    assert "More tests" not in page and "Engine" not in page
    assert pn.headline(page) == "2 changes landed: Docs."
    only_tests = pn.rules_page([changes[2]], UNTIL, 0, "same as yesterday")
    assert pn.headline(only_tests) == "Nothing new landed."


EX_AREAS = ["Self-build", "Web UI", "Models"]


def test_check_accepts_the_example():
    assert pn.check(body(pn.EXAMPLE), EX_AREAS) is None


@pytest.mark.parametrize("bad", [
    "Self-build\n- Pieces merge.",                                        # no headline
    "- A line first.\nSelf-build\n- Pieces merge.",                        # headline is a line
    "Headline.\nSelf-build\n- " + " ".join(["word"] * 15),                 # 15-word line
    " ".join(["word"] * 21) + ".\nSelf-build\n- Pieces merge.",            # 21-word headline
    "Headline.\nSelf-build\n" + "\n".join(f"- line {i}" for i in range(6)),  # 6 in a section
    "Headline.\n" + "\n".join(f"{a}\n" + "\n".join(f"- {a} {i}" for i in range(4))
                              for a in ["Self-build", "Web UI", "Models"])
    + "\nSelf-build\n- one more\n- and one more",                         # 16 on the page
    "Headline.\nSelf-build\n- Fixed a1b2c3d4e5 at last.",                 # an id
    "Headline.\nSelf-build\n- Split digest.py in two.",                   # a file name
    "Headline.\nSelf-build\n- The worktree is kept.",                     # worktree
    "Headline.\nSelf-build\n- Three items landed.",                       # items
    "Headline.\nRouting\n- Codex first.",                                 # area not on the page
])
def test_check_rejects(bad):
    reason = pn.check(bad, EX_AREAS)
    assert isinstance(reason, str) and reason


def test_check_counts_sixteen_lines():
    notes = "Headline.\n" + "\n".join(f"{a}\n" + "\n".join(f"- {a} {i}" for i in range(4))
                                      for a in ["Self-build", "Web UI", "Models", "Routing"])
    assert pn.check(notes, EX_AREAS + ["Routing"]) == "more than 15 lines on the page"


def test_short_words_are_free():
    assert pn.words("Pieces merge and go live on their own; a hand-pushed fix") == 8
    assert pn.check("Headline.\nModels\n- " + " ".join(["word"] * 14) + " on a", ["Models"]) is None


def test_check_docs_has_one_line():
    assert pn.check("Headline.\nDocs\n- one\n- two", ["Docs"])


def test_headline_areas_in_and_with_notes():
    assert pn.headline(pn.EXAMPLE) == pn.EXAMPLE.splitlines()[1]
    assert pn.headline("# eki 09-28\n") == ""
    assert pn.headline("no title") == ""
    assert pn.areas_in(pn.EXAMPLE) == EX_AREAS
    page = pn.rules_page([Change("1", "x", "Web UI", "change")], UNTIL, 2, "better than yesterday")
    new = pn.with_notes(page, "\nStation folds.\n\nWeb UI\n- Folds.\n")
    assert new == "# eki 09-28\nStation folds.\n\nWeb UI\n- Folds.\n\nWaits for you: 2.  Score: better than yesterday.\n"


def test_brief_has_the_rules_the_example_and_no_ids():
    changes = [Change("a1b2c3d4e5", "Station folds", "Web UI", "change"),
               Change("0f0f0f0f0f", "Explain the train", "Docs", "docs")]
    text = pn.brief(changes)
    assert pn.EXAMPLE in text
    assert "[Web UI] (change) Station folds" in text and "[Docs] (docs) Explain the train" in text
    assert "a1b2c3d4e5" not in text and "0f0f0f0f0f" not in text
    assert "NOTES:" in text and "TRIAGE:" in text
    assert text.index("NOTES:") < text.index("TRIAGE:")
    assert "at most 20 words" in text and "at most 14 words" in text
