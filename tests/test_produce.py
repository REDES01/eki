"""`eki image` / `eki write`: an agent asks for a thing, gets a path and an
exit code — against a stand-in engine speaking the three endpoints used."""
import json

import httpx
import pytest

from eki import cli, migrate, produce


def engine(monkeypatch, *, state="done", answer="", error="", backend="comfyui", never_ends=False):
    """A fake engine; returns the list of /api/ask bodies it was sent."""
    asked = []

    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/ask":
            asked.append(json.loads(req.content))
            return httpx.Response(200, json={"run": "r1", "conversation": "c1"})
        if req.url.path == "/api/runs/r1":
            return httpx.Response(200, json={"id": "r1", "state": "running" if never_ends else state,
                                             "backend": backend, "error": error, "output": answer})
        if req.url.path == "/api/conversations/c1":
            return httpx.Response(200, json={"turns": [{"role": "user", "content": "x"},
                                                       {"role": "assistant", "content": answer}]})
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(produce.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(migrate, "run", lambda root: None)
    monkeypatch.setattr(cli, "ensure_engine", lambda s: None)
    monkeypatch.setattr(produce.time, "sleep", lambda s: None)
    return asked


def test_image_lands_where_asked_and_the_path_is_printed(monkeypatch, tmp_path, capsys):
    drawn = tmp_path / "eki out" / "ComfyUI_0001.png"
    drawn.parent.mkdir()
    drawn.write_bytes(b"\x89PNG fake")
    asked = engine(monkeypatch, answer=f"flux · 1024×1024\n\n![a fox]({str(drawn).replace(' ', '%20')})\n")
    art = tmp_path / "art"
    code = cli.main(["image", "A watercolor fox, at dusk", "-o", str(art) + "/", "--width", "768"])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out == str(art / "a-watercolor-fox-at-dusk.png")
    assert (art / "a-watercolor-fox-at-dusk.png").read_bytes() == b"\x89PNG fake"
    assert asked[0]["images"] is True and asked[0]["width"] == 768 and "height" not in asked[0]
    # asked again: a new file beside it, never over it
    assert cli.main(["image", "A watercolor fox, at dusk", "-o", str(art), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["ok"] and got["paths"] == [str(art / "a-watercolor-fox-at-dusk-2.png")]
    assert got["backend"] == "comfyui" and got["run"] == "r1"


def test_several_images_are_numbered(monkeypatch, tmp_path, capsys):
    shots = []
    for i in range(2):
        p = tmp_path / f"src{i}.png"
        p.write_bytes(b"png")
        shots.append(f"![x]({p})")
    engine(monkeypatch, answer="\n".join(shots))
    assert cli.main(["image", "icons", "-n", "2", "-o", str(tmp_path / "icon.png")]) == 0
    assert capsys.readouterr().out.split() == [str(tmp_path / "icon-1.png"), str(tmp_path / "icon-2.png")]


def test_an_image_run_without_a_picture_exits_4(monkeypatch, tmp_path, capsys):
    engine(monkeypatch, answer="I can't draw, but here is a description.")
    assert cli.main(["image", "a fox", "-o", str(tmp_path), "--json"]) == produce.NOTHING
    got = json.loads(capsys.readouterr().out)
    assert got["ok"] is False and "without a picture" in got["error"] and not list(tmp_path.iterdir())


def test_write_saves_the_text_from_the_named_model(monkeypatch, tmp_path, capsys):
    asked = engine(monkeypatch, answer="Oh the wind blew hard…", backend="qwen")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["write", "a sea shanty", "--model", "qwen"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "a-sea-shanty.md")
    assert (tmp_path / "a-sea-shanty.md").read_text() == "Oh the wind blew hard…\n"
    assert asked[0]["backend"] == "qwen" and not asked[0].get("images")
    assert cli.main(["write", "a sea shanty", "-o", "-"]) == 0
    assert capsys.readouterr().out == "Oh the wind blew hard…\n"
    assert cli.main(["write", "a sea shanty", "-o", "-", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "Oh the wind blew hard…"


def test_a_failed_run_exits_1_and_says_why(monkeypatch, tmp_path, capsys):
    engine(monkeypatch, state="failed", error="qwen is not running")
    assert cli.main(["write", "hi", "-o", str(tmp_path / "x.md")]) == produce.FAILED
    assert "qwen is not running" in capsys.readouterr().err
    assert not (tmp_path / "x.md").exists()


def test_a_slow_run_exits_5_and_keeps_going(monkeypatch, capsys):
    engine(monkeypatch, never_ends=True)
    assert cli.main(["write", "hi", "--timeout", "0", "--json"]) == produce.TIMEOUT
    got = json.loads(capsys.readouterr().out)
    assert "eki watch r1" in got["error"] and got["state"] == "running"


def test_no_engine_exits_3(monkeypatch, capsys):
    engine(monkeypatch)

    def down(service):
        raise SystemExit(1)
    monkeypatch.setattr(cli, "ensure_engine", down)
    assert cli.main(["image", "a fox", "--json"]) == produce.UNREACHABLE
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_asked_from_inside_an_agent_it_says_so(monkeypatch, tmp_path):
    asked = engine(monkeypatch, answer="ok")
    monkeypatch.setenv("EKI_INSIDE", "1")
    cli.main(["write", "hi", "-o", str(tmp_path)])
    assert asked[0]["via"] == "agent"


@pytest.mark.parametrize("prompt,name", [("", "text"), ("Ünïcode!! ok", "n-code-ok")])
def test_slug(prompt, name):
    assert produce.slug(prompt, "text") == name


BACKENDS = [
    {"key": "qwen", "label": "Qwen", "kind": "mlx", "ok": True, "tier": 0, "detail": "",
     "capabilities": {"text": True, "context_tokens": 128000, "produces": ["prose"]}},
    {"key": "comfyui", "label": "Flux", "kind": "comfyui", "ok": True, "tier": 0, "detail": "",
     "capabilities": {"text": False, "images_out": True, "produces": ["image"]}},
    {"key": "claude_code", "label": "Claude Code", "kind": "claude_code", "ok": True, "tier": 3,
     "detail": "", "capabilities": {"text": True, "repo": True, "tools": True, "web": True}},
    {"key": "gpt", "label": "GPT", "kind": "openai", "ok": False, "tier": 2,
     "detail": "no key", "capabilities": {"text": True}},
]


def backends_engine(monkeypatch, status=200):
    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/backends":
            return httpx.Response(status, json=BACKENDS if status == 200 else {"detail": "no"})
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(produce.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(migrate, "run", lambda root: None)
    monkeypatch.setattr(cli, "ensure_engine", lambda s: None)


def test_capabilities_says_what_is_up_and_the_command_for_each(monkeypatch, capsys):
    backends_engine(monkeypatch)
    monkeypatch.delenv("EKI_DEPTH", raising=False)
    assert cli.main(["capabilities"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("eki on this Mac: 3 of 4 backends up.")
    assert 'eki image "…" -m comfyui -o <folder>/' in out
    assert 'eki write "…" -m qwen -o <file>' in out
    assert 'eki ask "…" --backend claude_code -r <folder>' in out
    assert "eki write \"…\" -m comfyui" not in out          # it only draws
    down = out.split("down:")[1]
    assert "gpt: GPT [openai]" in down and "no key" in down and "eki " not in down
    assert "depth" not in out                               # a person's shell: nothing to say


def test_capabilities_json_carries_the_depth(monkeypatch, capsys):
    backends_engine(monkeypatch)
    monkeypatch.setenv("EKI_DEPTH", str(produce.nesting.MAX_DEPTH))
    assert cli.main(["capabilities", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["ok"] and got["can_ask"] is False and got["depth"] == produce.nesting.MAX_DEPTH
    by = {b["key"]: b for b in got["backends"]}
    assert by["claude_code"]["does"] == "repo, tools, text, web"
    assert by["gpt"]["up"] is False and by["gpt"]["use"] == []
    monkeypatch.setenv("EKI_DEPTH", str(produce.nesting.MAX_DEPTH))
    assert cli.main(["capabilities"]) == 0
    assert "eki will refuse" in capsys.readouterr().out


def test_capabilities_without_an_engine_exits_3(monkeypatch, capsys):
    backends_engine(monkeypatch, status=500)
    assert cli.main(["capabilities", "--json"]) == produce.UNREACHABLE
    got = json.loads(capsys.readouterr().out)
    assert got["ok"] is False and "refused" in got["error"]

    def down(service):
        raise SystemExit(1)
    monkeypatch.setattr(cli, "ensure_engine", down)
    assert cli.main(["capabilities"]) == produce.UNREACHABLE
    assert "isn't running" in capsys.readouterr().err
