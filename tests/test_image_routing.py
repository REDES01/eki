"""Routing and turns know the last picture: the picture rows route by the
person's words, a follow-up in a picture thread edits the last picture, and
the worker hands that picture to ComfyUI as the source."""
import json
import os

import pytest

from eki import db, routing, store, worker
from tests.conftest import run_inline
from tests.test_comfyui import PNG, comfy  # noqa: F401 — the stub ComfyUI fixture


@pytest.fixture
def station(comfy, home):  # noqa: F811
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"}, "fake2": {"kind": "fake"},
        "comfyui": {"kind": "comfyui", "base_url": comfy.provider.cfg["base_url"], "poll": 0.05}}))
    return comfy


def ask(conn, prompt, tid=None, **kw):
    with db.tx(conn):
        tid = tid or store.create_thread(conn, "t", None)
        return tid, store.create_run(conn, tid, prompt, **kw)


def decide(conn, rid):
    return routing.decide(conn, store.run(conn, rid))


def drawn(conn, station):
    """A thread whose one run drew a picture through the stub."""
    tid, rid = ask(conn, "draw a fox")
    r = run_inline(conn, rid)
    assert r["state"] == "done" and r["provider"] == "comfyui" and r["row"] == "image"
    pic = store.last_picture(conn, tid, r["seq"] + 1)
    assert pic and os.path.isfile(pic)
    return tid, pic


def test_an_anime_request_routes_to_image_anime(station, conn):
    d = decide(conn, ask(conn, "draw an anime fox")[1])
    assert (d.provider, d.row) == ("comfyui", "image-anime")
    assert d.why.startswith("rule: asks for an anime picture → image-anime → comfyui")
    e = routing.explain(conn, "draw an anime fox")
    assert e["row"] == "image-anime" and e["why"] == "rule: asks for an anime picture"


@pytest.mark.parametrize("prompt", ["make it bluer", "remove the hat"])
def test_a_follow_up_edits_the_last_picture(station, conn, prompt):
    tid, pic = drawn(conn, station)
    assert routing.explain(conn, prompt, thread_id=tid)["row"] == "image-edit"
    assert routing.explain(conn, prompt)["row"] != "image-edit"      # no thread, no last picture
    _, rid = ask(conn, prompt, tid)
    d = decide(conn, rid)
    assert (d.provider, d.row) == ("comfyui", "image-edit")
    assert d.why.startswith("rule: follow-up to a picture: edit it → image-edit → comfyui")
    store.update_run(conn, rid, provider=d.provider, row=d.row)
    turn = worker.build_turn(conn, store.run(conn, rid), "comfyui")
    assert turn.images == [pic] and turn.extra["row"] == "image-edit"


def test_upscale_it(station, conn):
    tid, pic = drawn(conn, station)
    _, rid = ask(conn, "upscale it", tid)
    d = decide(conn, rid)
    assert (d.provider, d.row) == ("comfyui", "image-upscale")
    assert d.why.startswith("rule: upscale the picture → image-upscale → comfyui")


def test_a_text_question_leaves_the_picture_thread(station, conn):
    tid, _ = drawn(conn, station)
    d = decide(conn, ask(conn, "what is the capital of France?", tid)[1])
    assert d.provider in ("fake", "fake2") and d.row == "general"
    assert d.why.startswith("comfyui draws pictures, this isn't one; ")


def test_only_the_previous_run_s_picture_makes_a_follow_up(station, conn):
    tid, _ = drawn(conn, station)
    with db.tx(conn):
        store.create_run(conn, tid, "thanks")                      # a run in between, no picture
    rid = ask(conn, "make it bluer", tid)[1]
    assert routing.previous_picture(conn, tid, store.run(conn, rid)["seq"]) is None
    assert decide(conn, rid).row != "image-edit"


def test_routing_json_is_left_as_written(station, conn, home):
    before = (home / "routing.json").read_bytes()
    tid, _ = drawn(conn, station)
    for p in ("draw a detailed photorealistic lighthouse", "make it bluer", "upscale it"):
        decide(conn, ask(conn, p, tid)[1])
    assert (home / "routing.json").read_bytes() == before


def test_last_picture_takes_the_later_of_attachment_and_drawing(conn, tmp_path):
    a, b, c = (tmp_path / n for n in ("given.png", "drawn.png", "later.jpg"))
    for f in (a, b, c):
        f.write_bytes(PNG)
    tid, r1 = ask(conn, "look", attachments=[str(a)])
    with db.tx(conn):
        store.add_event(conn, r1, 1, "tool", {"name": "image", "path": str(b)})
        store.add_event(conn, r1, 1, "tool", {"name": "file", "path": str(a)})
    assert store.last_picture(conn, tid, 2) == str(b)         # a run's drawings come after its attachments
    _, r2 = ask(conn, "and this", tid, attachments=[str(c), str(tmp_path / "notes.txt")])
    assert store.last_picture(conn, tid, 3) == str(c)         # a later run's attachment wins
    assert store.last_picture(conn, tid, 2) == str(b)         # only runs before the one asking
    c.unlink()
    assert store.last_picture(conn, tid, 3) == str(b)         # a file gone isn't a picture
    assert store.last_picture(conn, tid, 1) is None


def test_an_attachment_on_an_edit_is_the_source(station, conn, tmp_path):
    tid, _ = drawn(conn, station)
    own = tmp_path / "mine.png"
    own.write_bytes(PNG)
    _, rid = ask(conn, "make it bluer", tid, attachments=[str(own)], row="image-edit")
    turn = worker.build_turn(conn, store.run(conn, rid), "comfyui")
    assert turn.images == [str(own)]


def test_draw_then_edit_end_to_end(station, conn):
    tid, pic = drawn(conn, station)
    assert station.uploads == []
    _, rid = ask(conn, "make it bluer", tid)
    r = run_inline(conn, rid)
    assert r["state"] == "done" and r["provider"] == "comfyui" and r["row"] == "image-edit"
    assert len(station.uploads) == 1
    name, data, _ = station.uploads[0]
    assert name == f"eki_src_{rid}_{os.path.basename(pic)}" and data == PNG
    graph = station.posted[-1]["prompt"]
    assert [n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"] == [name]
    assert store.last_picture(conn, tid, r["seq"] + 1) != pic      # the edit is now the last picture
