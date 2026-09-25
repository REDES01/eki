"""The web-interface plan (docs/web-ui.md) and its stage in ROADMAP.md agree,
and the loop will take the stage's items one after another, in order."""
from pathlib import Path

from eki import roadmap

ROOT = Path(__file__).resolve().parent.parent
SECTION = "Stage 9 — Web interface in a native shell"


def _items():
    return [i for i in roadmap.parse(roadmap.read(ROOT)) if i.section == SECTION]


def test_the_stage_is_there_and_the_loop_may_take_it():
    items = _items()
    assert len(items) == 10
    assert roadmap.workable(items) == items          # none a person's, none waiting


def test_each_item_waits_for_the_one_above():
    items = _items()
    assert roadmap.after(items, items[0]) is None
    for above, item in zip(items, items[1:]):
        assert roadmap.after(items, item) == above


def test_the_plan_names_what_the_stage_names():
    plan = (ROOT / "docs" / "web-ui.md").read_text()
    assert "docs/web-ui.md" in roadmap.intro(roadmap.read(ROOT), SECTION)
    for page in ("/ui/usage", "/ui/settings", "/ui/models", "/ui/gallery",
                 "/ui/panel/", "/ui/chat/", "/ui/welcome", "/ui/menu", "webshot"):
        assert page in plan
