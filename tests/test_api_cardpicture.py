"""A picture card's "picture" key in the cards API: its target and workflow (from the ComfyUI
provider's entry), its absolute out folder, and the last pictures its calls made, each with
the prompt and the task that asked."""
import json

import pytest

from eki import api_cardpicture, api_cards, apiroutes, store


def project(conn, folder, pid="p1"):
    conn.execute("INSERT INTO projects(id, name, path, branch, created_at, folder) VALUES (?,?,?,?,?,?)",
                 (pid, "game", str(folder), "trunk", 0, str(folder)))
    conn.commit()


def card(proj, name, spec):
    (proj / "components" / name).mkdir(parents=True)
    (proj / "components" / name / "component.json").write_text(json.dumps(spec))


def cards(conn):
    fn, m = apiroutes.match("GET", "/api/projects/p1/cards")
    return {c["name"]: api_cards.card_of(conn, "p1", c["name"]) for c in fn(conn, m, {}, {})["cards"]}


def call_run(conn, proj, name, args, caller=None, paths=(), t=None):
    tid = store.create_thread(conn, f"use {name}", None, by="eki")
    prompt = json.dumps({"component": name, "args": args, "caller_dir": str(proj), "caller_run": caller})
    rid = store.create_run(conn, tid, prompt, provider="component", parent=caller)
    for p in paths:
        store.add_event(conn, rid, 1, "tool", {"name": f"eki use {name}", "path": str(p)})
    store.add_event(conn, rid, 1, "tool", {"name": "something else", "path": str(proj / "x.png")})
    conn.execute("UPDATE runs SET state='done', ended_at=? WHERE id=?", (t, rid))
    conn.commit()
    return rid


@pytest.fixture
def pics(conn, proj):
    project(conn, proj)
    card(proj, "portrait", {"kind": "service", "about": "faces", "target": "comfyui:hq",
                            "out": "assets/generated/portrait/"})
    card(proj, "images", {"kind": "service", "about": "pictures", "provider": "comfyui"})
    card(proj, "tts", {"kind": "service", "about": "voice", "run": ["say"]})
    return proj


def test_a_target_card_has_its_workflow_and_out(conn, pics):
    got = cards(conn)["portrait"]["picture"]
    assert got["target"] == "comfyui:hq" and got["provider"] == "comfyui"
    w = got["workflow"]
    assert w["id"] == "hq" and w["label"] == "Qwen-Image 2.1 — high quality"
    assert "prompt" in [i["name"] for i in w["inputs"]] and w["models"]
    assert all(m["found"] is not True for m in w["models"])       # tests have no ComfyUI folders
    assert got["out"] == str((pics / "assets" / "generated" / "portrait").resolve())
    assert got["recent"] == []


def test_the_old_provider_card_reads_as_the_draft_workflow(conn, pics):
    got = cards(conn)["images"]["picture"]
    assert got["target"] == "comfyui:draft" and got["workflow"]["id"] == "draft"
    assert got["out"].endswith("assets/generated/images")


def test_other_services_have_no_picture(conn, pics):
    assert "picture" not in cards(conn)["tts"]


def test_an_unknown_workflow_or_provider_says_so(conn, pics):
    card(pics, "odd", {"kind": "service", "about": "x", "target": "comfyui:nope", "out": "o/"})
    card(pics, "wrong", {"kind": "service", "about": "x", "target": "claude:opus", "out": "o/"})
    got = cards(conn)
    assert "nope" in got["odd"]["picture"]["workflow"]["error"]
    assert "ComfyUI" in got["wrong"]["picture"]["workflow"]["error"]


def test_recent_pictures_with_prompt_and_task(conn, pics):
    out = pics / "assets" / "generated" / "portrait"
    out.mkdir(parents=True)
    a, b, c = out / "a.png", out / "b.png", out / "c.png"
    for f in (a, b, c):
        f.write_bytes(b"png")
    ttid = store.create_thread(conn, "Draw the heroine", None, by=None)
    caller = store.create_run(conn, ttid, "draw her\nmore", provider="claude")
    untitled = store.create_thread(conn, "", None, by=None)
    caller2 = store.create_run(conn, untitled, "paint a fox\nwith detail", provider="claude")
    call_run(conn, pics, "portrait", ["--prompt", "a girl", "--seed", "4"], caller, [a], t=10)
    call_run(conn, pics, "portrait", ["--seed=3", "--prompt=a fox"], caller2, [b, pics / "gone.png"], t=20)
    call_run(conn, pics, "portrait", ["a", "cat", "--size", "512x512"], None, [c], t=30)
    call_run(conn, pics, "images", ["--prompt", "not mine"], None, [a], t=40)
    got = cards(conn)["portrait"]["picture"]["recent"]
    assert [x["path"] for x in got] == [str(c), str(b), str(a)]          # the gone file is left out
    assert [x["prompt"] for x in got] == ["a cat", "a fox", "a girl"]
    assert [x["task"] for x in got] == ["by hand", "paint a fox", "Draw the heroine"]
    assert set(got[0]) == {"path", "prompt", "task", "run", "at"} and got[0]["at"] == 30


def test_recent_keeps_the_last_twelve(conn, pics):
    out = pics / "o"
    out.mkdir()
    for i in range(15):
        f = out / f"{i}.png"
        f.write_bytes(b"png")
        call_run(conn, pics, "portrait", ["--prompt", str(i)], None, [f], t=i)
    got = api_cardpicture.recent(conn, pics, "portrait")
    assert len(got) == api_cardpicture.RECENT == 12
    assert got[0]["prompt"] == "14" and got[-1]["prompt"] == "3"


def test_prompt_from_args():
    p = api_cardpicture._prompt
    assert p(["--prompt", "x y"]) == "x y" and p(["--prompt=z"]) == "z"
    assert p(["a", "--seed", "1", "b", "--w=2"]) == "a b" and p([]) == ""
