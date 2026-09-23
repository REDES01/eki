# SPDX-License-Identifier: Apache-2.0
"""Goals are made, changed and removed without anyone hand-editing YAML — and
an edit touches only its own entry: your comments and the rest stay put."""
import pytest

from eki import goaledit, goals
from eki.goals import GoalError

YAML = """# Ashfall content — hand-written notes stay here
bible: [world.md]   # the world

goals:
  # the townsfolk
  - name: npcs
    count: 2
    id: "npc-{n:02}"
    dir: "npcs/{id}"
    parts:
      bio: {kind: text, file: bio.md, prompt: "Invent one. {others}"}
      portrait: {kind: image, file: portrait.png, from: [bio], prompt: "Portrait. {bio:300}"}

  - name: quests          # side quests
    count: 3
    parts:
      summary: {kind: text, file: summary.md, prompt: "A quest."}

# end of goals
"""

ITEMS = {"name": "items", "count": 4, "id": "item-{n}", "dir": "items/{id}",
         "parts": {"desc": {"kind": "text", "file": "desc.md",
                            "prompt": "Invent one item for the world.\nKeep it short. {others}"}}}


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "rpg"
    root.mkdir()
    (root / "goals.yaml").write_text(YAML)
    return root


def test_adding_a_goal_leaves_the_rest_as_written(proj):
    spec = goaledit.put(str(proj), ITEMS)
    assert [g.name for g in spec.goals] == ["npcs", "quests", "items"]
    text = (proj / "goals.yaml").read_text()
    assert text.startswith(YAML.split("# end of goals")[0].rstrip("\n"))   # untouched, comments and all
    assert "# end of goals" in text and "# side quests" in text
    assert "prompt: |" in text                                             # a readable block, not a quoted blob


def test_changing_one_goal_rewrites_only_that_entry(proj):
    q = goaledit.clean(goaledit.entry(str(proj), "quests"))
    q["count"] = 5
    goaledit.put(str(proj), q, "quests")
    text = (proj / "goals.yaml").read_text()
    assert "# the townsfolk" in text and '"Portrait. {bio:300}"' in text   # the other goal as it was
    assert "# end of goals" in text
    assert len(goals.load(str(proj)).goals[1].items) == 5


def test_rename_and_the_name_clash(proj):
    q = goaledit.clean(goaledit.entry(str(proj), "quests"))
    q["name"] = "npcs"
    with pytest.raises(GoalError, match="already a goal called 'npcs'"):
        goaledit.put(str(proj), q, "quests")
    q["name"] = "side-quests"
    assert [g.name for g in goaledit.put(str(proj), q, "quests").goals] == ["npcs", "side-quests"]


def test_a_bad_goal_is_refused_before_anything_is_written(proj):
    bad = dict(ITEMS, parts={"desc": {"kind": "video", "file": "x.mp4", "prompt": "p"}})
    with pytest.raises(GoalError, match="kind is one of"):
        goaledit.put(str(proj), bad)
    same_id = dict(ITEMS, id="item")
    with pytest.raises(GoalError, match="use \\{n\\}"):
        goaledit.put(str(proj), same_id)
    assert (proj / "goals.yaml").read_text() == YAML


def test_removing_keeps_its_files_unless_you_say_trash(proj):
    d = proj / "quests/quests-01"
    d.mkdir(parents=True)
    (d / "summary.md").write_text("# A quest")
    goaledit.remove(str(proj), "quests")
    assert [g.name for g in goals.load(str(proj)).goals] == ["npcs"]
    assert (d / "summary.md").exists() and "# end of goals" in (proj / "goals.yaml").read_text()
    goaledit.put(str(proj), dict(ITEMS, name="quests", parts={"summary": {"kind": "text",
                 "file": "summary.md", "prompt": "A quest."}}, id="quests-{n:02}", dir="quests/{id}"))
    done = goaledit.remove(str(proj), "quests", trash=True)
    assert done["moved"] == 1 and not (d / "summary.md").exists()
    assert list((proj / ".eki" / "trash").rglob("summary.md"))


def test_a_new_project_gets_a_goals_file(tmp_path):
    spec = goaledit.put(str(tmp_path / "new"), ITEMS)
    assert spec.goals[0].name == "items" and (tmp_path / "new" / "goals.yaml").exists()


def test_what_a_change_does_to_what_was_made(proj):
    for n in (1, 2):
        d = proj / f"npcs/npc-0{n}"
        d.mkdir(parents=True)
        (d / "bio.md").write_text("# X")
        (d / "portrait.png").write_bytes(b"p")
    npcs = goaledit.clean(goaledit.entry(str(proj), "npcs"))
    npcs["parts"]["bio"]["prompt"] = "Invent an older one. {others}"
    npcs["count"] = 3
    got = goaledit.impact(str(proj), "npcs", npcs)
    assert got["changed"] == [{"part": "bio", "made": 2, "with": ["portrait"]}]
    assert got["new_items"] == 1
    npcs["parts"]["bio"]["file"] = "about.md"
    assert goaledit.impact(str(proj), "npcs", npcs)["orphaned"] == 2
    assert goaledit.redo_parts(str(proj), "npcs", ["bio"]) == 4           # bios and the portraits from them


@pytest.mark.parametrize("wrapped", [
    "```yaml\n{y}```",
    "<think>let me see</think>\n{y}",
    "goals:\n{y2}",
])
def test_a_draft_is_read_however_the_model_wrapped_it(wrapped):
    y = goaledit.dump(ITEMS)
    y2 = "".join("  " + ln for ln in y.splitlines(keepends=True))
    entry = goaledit.parse_draft(wrapped.format(y=y, y2=y2))
    assert entry["name"] == "items" and entry["parts"]["desc"]["kind"] == "text"


def test_a_draft_that_isnt_a_goal_is_refused():
    with pytest.raises(GoalError):
        goaledit.parse_draft("Sure! Here are some ideas for items.")
    with pytest.raises(GoalError, match="no parts"):
        goaledit.parse_draft("name: x\ncount: 2\n")
