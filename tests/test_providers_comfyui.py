"""ComfyUI joins the providers in memory when it's on this Mac, never in providers.json."""
import json

from eki import providers
from eki.providers.comfyui import Comfyui


def _comfy(tmp_path, monkeypatch, main=True):
    d = tmp_path / "ComfyUI"
    d.mkdir()
    if main:
        (d / "main.py").write_text("")
    monkeypatch.setenv("EKI_COMFYUI_DIR", str(d))
    return d


def test_joins_in_memory_when_comfyui_is_here(home, tmp_path, monkeypatch):
    d = _comfy(tmp_path, monkeypatch)
    before = (home / "providers.json").read_bytes()
    cfg = providers.config()
    assert cfg["comfyui"]["kind"] == "comfyui"
    assert cfg["comfyui"]["serve"]["cwd"] == str(d)
    assert providers.DEFAULTS["comfyui"]["serve"]["cwd"] == "~/flux/ComfyUI"   # the default is untouched
    assert (home / "providers.json").read_bytes() == before


def test_absent_without_main_py(tmp_path, monkeypatch):
    _comfy(tmp_path, monkeypatch, main=False)
    assert providers.comfyui_dir() is None
    assert "comfyui" not in providers.config()


def test_absent_by_default_in_tests():
    assert providers.comfyui_dir() is None
    assert "comfyui" not in providers.config()


def test_persons_entry_wins(home, tmp_path, monkeypatch):
    _comfy(tmp_path, monkeypatch)
    (home / "providers.json").write_text(json.dumps({"comfyui": {"off": True}}))
    assert providers.config()["comfyui"] == {"off": True}
    assert "comfyui" not in providers.all_providers()


def test_fresh_home_writes_no_comfyui(home, tmp_path, monkeypatch):
    _comfy(tmp_path, monkeypatch)
    (home / "providers.json").unlink()
    cfg = providers.config()
    written = json.loads((home / "providers.json").read_text())
    assert set(written) == {"claude", "codex", "local"}
    assert "comfyui" in cfg                      # joined in memory all the same


def test_builds_a_comfyui():
    p = providers.build("comfyui", providers.DEFAULTS["comfyui"])
    assert isinstance(p, Comfyui)
    assert providers.KINDS["comfyui"] is Comfyui


def test_capabilities():
    assert providers.capabilities("comfyui", providers.DEFAULTS["comfyui"]) == ["image", "image-edit"]
