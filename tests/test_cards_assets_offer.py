"""The Cards area offers what a project's first commit left out (gitstart.left_out) as a media pool card."""
import threading
import urllib.request

import pytest

from eki import api_cards, gitstart, server

INSTR = {"template": "instructions",
         "why": "No AGENTS.md yet: add an instructions card to start one (and a CLAUDE.md pointing at it)"}


def ignore(root, *entries):
    (root / ".gitignore").write_text("\n".join([gitstart.MEDIA_BLOCK, *entries, "", gitstart.OTHER_BLOCK,
                                                "build/", ""]))


def test_a_left_out_folder_is_offered_as_the_pool(tmp_path):
    (tmp_path / "audio").mkdir()
    ignore(tmp_path, "audio/")
    got = api_cards.suggest(tmp_path, [])
    assert got["template"] == "assets" and got["path"] == "audio"
    assert "audio/" in got["why"] and "media pool" in got["why"] and "move them" not in got["why"]


def test_files_in_one_folder_give_that_folder(tmp_path):
    ignore(tmp_path, "audio/a.wav", "audio/b.wav")
    got = api_cards.suggest(tmp_path, [])
    assert got["path"] == "audio" and "and 1 more are" in got["why"]


def test_a_loose_big_wav_gives_assets(tmp_path):
    (tmp_path / "big.wav").write_bytes(b"x")
    ignore(tmp_path, "big.wav")
    got = api_cards.suggest(tmp_path, [])
    assert got["template"] == "assets" and got["path"] == "assets"
    assert got["why"].startswith("big.wav is kept out of the history") and "move them into assets/" in got["why"]


def test_entries_in_several_places_give_assets(tmp_path):
    ignore(tmp_path, "big.wav", "audio/", "film/c.mov")
    got = api_cards.suggest(tmp_path, [])
    assert got["path"] == "assets" and got["why"].startswith("big.wav and 2 more are kept out")


def test_with_an_assets_card_the_instructions_offer_comes_back(tmp_path):
    ignore(tmp_path, "audio/")
    assert api_cards.suggest(tmp_path, [{"kind": "assets"}]) == INSTR


def test_with_no_media_block_the_instructions_offer_comes_back(tmp_path):
    assert api_cards.suggest(tmp_path, []) == INSTR
    (tmp_path / ".gitignore").write_text("build/\n")
    assert api_cards.suggest(tmp_path, []) == INSTR
    (tmp_path / "AGENTS.md").write_text("x")
    assert api_cards.suggest(tmp_path, []) == {
        "template": "instructions", "why": "This project has AGENTS.md: add it as a card to see and edit it"}
    assert api_cards.suggest(tmp_path, [{"kind": "assets"}, {"kind": "instructions"}]) is None


@pytest.fixture
def web(home):
    srv = server.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_the_button_names_the_pool_and_sends_the_path(web):
    with urllib.request.urlopen(web + "/ui/projectcards.js", timeout=10) as r:
        js = r.read().decode()
    for name in ("Add media pool card", "Add instructions card", 'sg.template === "assets"', "data-path=",
                 "path: sg.dataset.path", "template: sg.dataset.suggest"):
        assert name in js, name
