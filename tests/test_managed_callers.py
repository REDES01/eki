"""The callers of models: capacity, the pinned route, the sidebar and the worker
treat an off ComfyUI with `serve` the way they treat the local model."""
import json
import sys

import pytest

from eki import api, capacity, db, models, routing, store
from eki.providers.base import Outcome
from eki.providers.comfyui import Comfyui

from tests.conftest import run_inline
from tests.test_models import SERVE, free_port, wait_up


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setenv("EKI_MODELS_POLL", "0.02")
    monkeypatch.setenv("EKI_MODELS_STOP_WAIT", "0.5")


@pytest.fixture
def comfy(home):
    port = free_port()
    (home / "providers.json").write_text(json.dumps({
        "fake": {"kind": "fake"},
        "comfyui": {"kind": "comfyui", "base_url": f"http://127.0.0.1:{port}", "idle_stop": 5,
                    "serve": {"command": [sys.executable, "-c", SERVE, str(port)]}}}))
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": [
        {"key": "general", "title": "all", "targets": ["fake"]},
        {"key": "pictures", "title": "pictures", "needs": [], "targets": ["comfyui"]}]}))
    yield "comfyui"
    models.stop("comfyui", by="test")


def new_run(conn, prompt, **kw):
    with db.tx(conn):
        return store.create_run(conn, store.create_thread(conn, "t", None), prompt, **kw)


def test_capacity_says_an_off_comfyui_starts_for_the_run(comfy, conn):
    assert not models.status(comfy)["up"]
    assert capacity.status(conn)[comfy] == (True, "off; starts for the run") == (True, capacity.STARTS)


def test_a_pinned_run_on_an_off_comfyui_is_started(comfy, conn):
    d = routing.decide(conn, store.run(conn, new_run(conn, "draw a fox", provider=comfy)))
    assert (d.provider, d.row, d.why) == (comfy, "picked", f"you picked {comfy} (starting it)")


def test_the_sidebar_gets_a_model_block_for_comfyui(comfy, conn):
    item = next(p for p in api.provider_list(conn) if p["name"] == comfy)
    assert item["model"]["kind"] == "comfyui" and item["model"]["startable"]
    assert not item["model"]["up"]


def test_the_worker_brings_comfyui_up_before_take(comfy, conn, monkeypatch):
    seen = []

    def take(self, turn, emit):
        seen.append(models.status(self.name)["up"])
        emit("text", {"text": "drew it"})
        return Outcome(state="done")

    monkeypatch.setattr(Comfyui, "take", take)
    rid = new_run(conn, "draw a fox", row="pictures")
    r = run_inline(conn, rid)
    assert r["provider"] == comfy and r["state"] == "done"
    assert seen == [True]                                   # up by the time take ran
    assert models.status(comfy)["managed"]                  # and eki started it
    models.stop(comfy, by="test")
    assert wait_up(comfy, up=False)
