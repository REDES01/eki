"""The picture rows join any routing table in memory; the file is never rewritten for them."""
import json

from eki.routing import needs, table

PICTURES = ["image", "image-hq", "image-anime", "image-edit", "image-upscale"]


def test_an_old_table_gets_all_five_and_stays_byte_for_byte(home):
    path = home / "routing.json"
    before = path.read_bytes()
    rows = {r["key"]: r for r in table.rows()}
    assert table.PICTURE_ROWS == PICTURES
    assert set(PICTURES) <= set(rows)
    assert all(rows[k]["targets"] == ["comfyui"] and rows[k]["examples"] for k in PICTURES)
    assert rows["image-edit"]["title"] == "Change the previous or attached picture from an instruction"
    assert path.read_bytes() == before
    assert table.row("image-anime")["title"] == "An anime-style picture"


def test_a_persons_own_image_row_wins(home):
    mine = {"key": "image", "title": "mine", "needs": ["image"], "targets": ["fake"]}
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "general", "title": "all", "targets": ["fake"]}, mine]}))
    rows = table.rows()
    assert [r for r in rows if r["key"] == "image"] == [mine]
    assert {r["key"] for r in rows} == {"general", *PICTURES}


def test_other_missing_defaults_are_not_added(home):
    keys = [r["key"] for r in table.rows()]
    assert keys == ["general", "code", *PICTURES]
    assert "answer" not in keys and "web" not in keys


def test_row_needs_for_each_picture_key():
    assert needs.row_needs({"key": "image"}) == ["image"]
    assert needs.row_needs({"key": "image-hq"}) == ["image"]
    assert needs.row_needs({"key": "image-anime"}) == ["image"]
    assert needs.row_needs({"key": "image-edit"}) == ["image-edit"]
    assert needs.row_needs({"key": "image-upscale"}) == ["image-edit"]


def test_an_edit_source_is_not_something_to_read():
    assert "vision" not in needs.request_needs({"key": "image-edit"}, ["x.png"])
    assert "vision" not in needs.request_needs({"key": "image-upscale"}, ["x.png"])
    assert needs.request_needs({"key": "answer"}, ["x.png"]) == ["text", "vision"]
