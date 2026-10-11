"""Picture cards: a ComfyUI workflow as a card — inputs, out folder, --help, migration."""
import json

import pytest

from eki import components, providers
from eki.cli import main
from eki.components import Call, picture, service
from eki.providers.base import Outcome


def card(root, spec, name="portrait"):
    d = root / "components" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "component.json").write_text(json.dumps({"kind": "service", "about": "portraits", **spec}))
    return components.load(d, "project")


def a_call(caller, args, run_id="r1"):
    return Call(args=list(args), caller_dir=str(caller), caller_run=None, run_id=run_id,
                emit=lambda k, d: None, extra={})


@pytest.fixture
def drawer(monkeypatch, tmp_path):
    """The comfyui provider, faked: records each turn and 'draws' one picture."""
    seen = []
    made = tmp_path / "made"
    made.mkdir()

    class Drawer:
        def take(self, turn, emit):
            seen.append(turn)
            pic = made / f"ComfyUI_{len(seen):05}_.png"
            pic.write_bytes(b"png")
            emit("tool", {"name": "image", "path": str(pic)})
            emit("text", {"text": f"Drew 1 picture:\n{pic}"})
            return Outcome("done", finished=True)

    real = providers.get
    monkeypatch.setattr(providers, "get", lambda name: Drawer() if name == "comfyui" else real(name))
    return seen


def test_only_declared_inputs_are_filled_and_pictures_land_in_out(tmp_path, drawer):
    c = card(tmp_path, {"target": "comfyui:draft", "out": "assets/generated/portrait/"})
    got = service.call(c, a_call(tmp_path, ["--prompt", "a fox", "--seed", "7", "--size", "512x768"]))
    assert got.error == ""
    [turn] = drawer
    assert turn.extra["model"] == "draft" and turn.images == []
    assert json.loads(turn.prompt) == {"inputs": {"prompt": "a fox", "seed": 7, "width": 512, "height": 768}}
    out = tmp_path / "assets" / "generated" / "portrait"
    assert got.paths == [str((out / "ComfyUI_00001_.png").resolve())]
    assert (out / "ComfyUI_00001_.png").read_bytes() == b"png"


def test_bare_words_are_the_prompt(tmp_path, drawer):
    c = card(tmp_path, {"target": "comfyui:draft", "out": "pics"})
    assert service.call(c, a_call(tmp_path, ["a", "red", "cat"])).error == ""
    assert json.loads(drawer[0].prompt) == {"inputs": {"prompt": "a red cat"}}


def test_unknown_inputs_are_refused_before_comfyui(tmp_path, drawer):
    c = card(tmp_path, {"target": "comfyui:draft", "out": "pics"})
    got = service.call(c, a_call(tmp_path, ["--negative", "blur", "cat"]))
    assert got.error == "unknown input --negative; portrait takes: prompt, width, height, seed"
    assert "unknown input --source" in service.call(c, a_call(tmp_path, ["--source", "x.png"])).error
    assert "whole number" in service.call(c, a_call(tmp_path, ["--seed", "seven"])).error
    assert drawer == []


def test_source_is_the_callers_picture(tmp_path, drawer):
    caller = tmp_path / "game"
    caller.mkdir()
    (caller / "hero.png").write_bytes(b"src")
    c = card(tmp_path, {"target": "comfyui:edit", "out": "pics"}, "touchup")
    assert "give --source" in service.call(c, a_call(caller, ["brighter"])).error
    assert "no picture at" in service.call(c, a_call(caller, ["--source", "nope.png", "x"])).error
    assert service.call(c, a_call(caller, ["--source", "hero.png", "make", "it", "night"])).error == ""
    [turn] = drawer
    assert turn.images == [str((caller / "hero.png").resolve())] and turn.extra["model"] == "edit"
    assert json.loads(turn.prompt) == {"inputs": {"prompt": "make it night"}}


def test_a_pool_card_saves_under_eki_images(home, drawer, tmp_path):
    d = components.pool_dir() / "draft"
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps({"kind": "service", "about": "drafts",
                                                  "target": "comfyui:draft", "out": "x"}))
    c = components.load(d, "pool")
    got = service.call(c, a_call(tmp_path, ["a cat"]))
    assert got.paths and got.paths[0].startswith(str((home / "images" / "draft").resolve()))


def test_use_prints_the_copies(proj, conn, monkeypatch, drawer, capsys):
    from eki.cli import use
    from conftest import run_inline
    card(proj, {"target": "comfyui:draft", "out": "assets/generated/portrait/"})
    monkeypatch.chdir(proj)

    def start(quiet=False):
        r = conn.execute("SELECT id FROM runs WHERE provider='component' AND state='queued'").fetchone()
        run_inline(conn, r["id"])
    monkeypatch.setattr(use.common, "ensure_engine", start)
    assert main(["use", "portrait", "--prompt", "a knight"]) == 0
    out = capsys.readouterr().out.splitlines()
    pic = proj.resolve() / "assets" / "generated" / "portrait" / "ComfyUI_00001_.png"
    assert out[-1] == str(pic) and pic.is_file()


def test_help_lists_the_workflow_without_a_run(proj, conn, monkeypatch, capsys):
    card(proj, {"target": "comfyui:draft", "out": "assets/generated/portrait/"})
    monkeypatch.chdir(proj)
    assert main(["use", "portrait", "--help"]) == 0
    text = capsys.readouterr().out
    assert "portrait — portraits" in text and "comfyui:draft (FLUX.2 klein — draft)" in text
    for line in ("--prompt", "--seed", "--width", "--size WxH", "flux-2-klein-4b.safetensors"):
        assert line in text
    assert f"saves into: {proj.resolve() / 'assets/generated/portrait/'}" in text
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_the_provider_comfyui_card_is_read_as_a_picture_card(tmp_path):
    d = tmp_path / "components" / "images"
    d.mkdir(parents=True)
    old = {"kind": "service", "about": "pictures", "access": "queue", "provider": "comfyui"}
    (d / "component.json").write_text(json.dumps(old))
    c = components.load(d, "project")
    assert c.spec["target"] == "comfyui:draft" and c.spec["out"] == "assets/generated/images/"
    assert "provider" not in c.spec and c.spec["access"] == "queue"
    assert json.loads((d / "component.json").read_text()) == old          # rewritten only when saved
    other = components.migrate({"kind": "service", "about": "x", "provider": "fake"}, "x")
    assert other["provider"] == "fake" and "target" not in other           # not a ComfyUI provider


def test_images_template_is_a_picture_card():
    spec = components.load(components.templates_dir() / "images", "pool").spec
    assert (spec["target"], spec["access"], spec["slots"], spec["memory_gb"]) == ("comfyui:draft", "queue", 1, 20)
    assert picture.workflow(spec) == ("comfyui", "draft")
