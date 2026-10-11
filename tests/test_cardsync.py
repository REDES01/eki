"""Project skills cards: entries that came from the Claude client are refreshed (eki/cardsync.py)."""
import os

import pytest

from eki import cardsync, paths, projects, syncstate
from eki.components import skillcard


def _skill(where, name, body, mtime=None):
    d = where / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(f"---\nname: {name}\ndescription: about {name}\n---\n{body}")
    if mtime is not None:
        os.utime(md, (mtime, mtime))
    return d


@pytest.fixture
def client():
    return paths.user_home() / ".claude" / "skills" / "synced" / "acct-1"


@pytest.fixture
def card(conn, proj):
    c = proj / "components" / "skills"
    skillcard._ensure(c)
    projects.add(conn, proj, name="holo")
    return c


def _clashes():
    return syncstate.status()["skills-in"]["clashes"]


def test_an_older_hand_copy_is_refreshed_with_a_backup(conn, card, client):
    _skill(card, "holo-card-maker", "old\n", mtime=1_000_000)
    _skill(client, "holo-card-maker", "new\n", mtime=2_000_000)
    said = cardsync.tick(conn)
    assert len(said) == 1 and said[0].startswith("holo/holo-card-maker: refreshed")
    assert (card / "holo-card-maker" / "SKILL.md").read_text().endswith("new\n")
    saved = list((syncstate.backups() / "cards" / "holo").iterdir())
    assert len(saved) == 1 and saved[0].name.startswith("holo-card-maker-")
    assert (saved[0] / "SKILL.md").read_text().endswith("old\n")
    base = syncstate.baseline("cards")[f"{card}/holo-card-maker"]
    assert base == {"hash": syncstate.tree_hash(card / "holo-card-maker"), "from": str(client / "holo-card-maker")}
    assert cardsync.tick(conn) == []


def test_an_equal_copy_only_gets_a_baseline(conn, card, client):
    _skill(card, "comfyui-local-api", "same\n")
    _skill(client, "comfyui-local-api", "same\n")
    assert cardsync.tick(conn) == []
    assert f"{card}/comfyui-local-api" in syncstate.baseline("cards")
    assert not (syncstate.backups() / "cards").exists()
    assert _clashes() == []


def test_a_newer_hand_copy_is_kept_and_shown(conn, card, client):
    _skill(card, "game-design-principles", "mine\n", mtime=2_000_000)
    _skill(client, "game-design-principles", "theirs\n", mtime=1_000_000)
    assert cardsync.tick(conn) == []
    assert (card / "game-design-principles" / "SKILL.md").read_text().endswith("mine\n")
    assert f"{card}/game-design-principles" not in syncstate.baseline("cards")
    assert [c for c in _clashes() if c.startswith("holo/game-design-principles: kept")]


def test_after_a_baseline_a_client_change_is_applied(conn, card, client):
    _skill(card, "design", "v1\n")
    _skill(client, "design", "v1\n")
    cardsync.tick(conn)
    _skill(client, "design", "v2\n", mtime=1_000)                 # even with an older time
    said = cardsync.tick(conn)
    assert said == ["holo/design: refreshed from the Claude client"]
    assert (card / "design" / "SKILL.md").read_text().endswith("v2\n")
    assert syncstate.baseline("cards")[f"{card}/design"]["hash"] == syncstate.tree_hash(client / "design")


def test_a_card_edit_after_the_baseline_is_kept_and_shown(conn, card, client):
    syncstate.record("skills-in", count=3, clashes=["eki: kept the store's copy of x"], note="n")
    _skill(card, "design", "v1\n")
    _skill(client, "design", "v1\n")
    cardsync.tick(conn)
    (card / "design" / "SKILL.md").write_text("---\nname: design\ndescription: d\n---\nproject edit\n")
    _skill(client, "design", "v2\n")
    assert cardsync.tick(conn) == []
    assert (card / "design" / "SKILL.md").read_text().endswith("project edit\n")
    state = syncstate.status()["skills-in"]
    assert state["count"] == 3 and state["note"] == "n"
    assert state["clashes"] == ["eki: kept the store's copy of x",
                                "holo/design: kept the project's copy (edited in the project)"]
    (card / "design" / "SKILL.md").write_text((client / "design" / "SKILL.md").read_text())
    cardsync.tick(conn)                                           # the clash goes once they agree again
    assert syncstate.status()["skills-in"]["clashes"] == ["eki: kept the store's copy of x"]


def test_a_client_skill_not_in_the_card_is_not_added(conn, card, client):
    _skill(client, "other", "x\n")
    _skill(card, "local-only", "y\n")
    assert cardsync.tick(conn) == []
    assert not (card / "other").exists()
    assert (card / "local-only" / "SKILL.md").is_file()


def test_a_skill_removed_from_the_client_stays_in_the_card(conn, card, client):
    _skill(card, "design", "v1\n")
    _skill(client, "design", "v1\n")
    cardsync.tick(conn)
    import shutil
    shutil.rmtree(client / "design")
    assert cardsync.tick(conn) == []
    assert (card / "design" / "SKILL.md").is_file()


def test_no_client_folder_is_quiet(conn, card):
    _skill(card, "design", "v1\n")
    assert cardsync.tick(conn) == []
