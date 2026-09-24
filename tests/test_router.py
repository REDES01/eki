# SPDX-License-Identifier: Apache-2.0
"""Routing decisions, including the ones that only happen when quota runs out."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eki.adapters.base import Backend, BackendInfo, Capabilities, Cost, Health
from eki.router import Need, QuotaSource, Router


class Fake(Backend):
    async def health(self):
        return Health(True)

    async def stream(self, messages, **kw):
        yield "x"


def make(key, tier, *, repo=False, tools=False, context=8000, quota=None):
    return Fake(BackendInfo(
        key=key, kind="fake", label=key,
        capabilities=Capabilities(context_tokens=context, repo=repo, tools=tools),
        cost=Cost(tier=tier), quota_source=quota), {})


LOCAL = make("qwen", 0, context=32000)
CLAUDE = make("claude", 50, repo=True, tools=True, context=200000, quota="claude")
API = make("api", 100, repo=True, tools=True, context=200000)


class NoQuota(QuotaSource):
    def __init__(self, spent=None):
        super().__init__()
        self._spent = spent or {}

    def exhausted(self):
        return self._spent


def test_plain_question_goes_to_the_cheapest():
    r = Router([CLAUDE, LOCAL, API], NoQuota())
    choice = r.choose(Need())
    assert choice.backend is LOCAL
    assert "tier 0" in choice.reason


def test_repo_work_skips_a_backend_that_cannot_edit():
    r = Router([LOCAL, CLAUDE, API], NoQuota())
    choice = r.choose(Need(repo=True))
    assert choice.backend is CLAUDE
    assert any("qwen" in line and "repo" in line for line in choice.rejected)


def test_spent_quota_is_skipped_not_attempted():
    r = Router([LOCAL, CLAUDE, API], NoQuota({"claude": "5H at 100%"}))
    choice = r.choose(Need(repo=True))
    assert choice.backend is API, "should fall through to metered API"
    assert any("5H at 100%" in line for line in choice.rejected)


def test_context_requirement_filters():
    r = Router([LOCAL, CLAUDE], NoQuota())
    choice = r.choose(Need(context_tokens=100_000))
    assert choice.backend is CLAUDE


def test_explicit_backend_wins_over_everything():
    r = Router([LOCAL, CLAUDE], NoQuota({"claude": "5H at 100%"}))
    choice = r.choose(Need(backend="claude"))
    assert choice.backend is CLAUDE
    assert "asked for by name" in choice.reason


def test_nothing_fits_says_why():
    r = Router([LOCAL], NoQuota())
    choice = r.choose(Need(images_out=True))
    assert choice.backend is None
    assert choice.rejected and "images" in choice.rejected[0]


def test_unknown_quota_is_never_held_against_a_backend():
    assert QuotaSource().exhausted() == {}


# ---- what a backend makes, and what it needs ---------------------------------

class Draws(Fake):
    PRODUCES = ("image",)


class Sculpts(Fake):
    """An image-to-3D backend: makes meshes, and needs a picture to start from."""
    PRODUCES = ("mesh",)
    NEEDS = ("image",)


def made(cls, key, tier=0, **caps):
    return cls(BackendInfo(key=key, kind="fake", label=key,
                           capabilities=Capabilities(**caps), cost=Cost(tier=tier)), {})


def test_adapters_declare_what_they_make():
    from eki import adapters
    assert adapters.produces(LOCAL) == {"code", "prose"}
    assert adapters.produces(made(adapters.base._REGISTRY["comfyui"], "flux",
                                  text=False, images_out=True)) == {"image"}
    for kind in ("claude_code", "codex", "mlx", "openai_compat", "anthropic_api", "gemini_cli"):
        assert set(adapters.base._REGISTRY[kind].PRODUCES) == {"code", "prose"}
    assert set(adapters.PRODUCTS) >= {"code", "prose", "image", "mesh", "audio"}


def test_a_provider_row_overrides_its_adapter():
    # a ComfyUI workflow that makes meshes says so in its row
    b = made(Draws, "trellis", text=False, produces=["mesh"])
    assert b.info.capabilities.produces == ("mesh",)
    from eki.adapters import produces
    assert produces(b) == {"mesh"}


def test_capability_comes_before_cost():
    # the free image model can't make a mesh; the dearer mesh one can
    flux = made(Draws, "flux", 0, text=False)
    mesh = made(Sculpts, "mesh", 100, text=False)
    c = Router([flux, mesh]).choose(Need(produces="mesh", inputs=["image"]))
    assert c.backend is mesh
    assert "flux: lacks meshes" in c.rejected


def test_a_backend_needs_what_it_starts_from():
    mesh = made(Sculpts, "mesh", 0, text=False)
    c = Router([mesh]).choose(Need(produces="mesh"))
    assert c.backend is None
    assert "mesh: needs image to start from" in c.rejected


def test_words_are_not_asked_of_a_mesh_model():
    mesh = made(Sculpts, "mesh", 0, text=False)
    c = Router([mesh, CLAUDE]).choose(Need(inputs=["image"]))
    assert c.backend is CLAUDE
    assert "mesh: makes meshes, doesn't answer in words" in c.rejected


def test_images_out_still_means_a_picture():
    flux = made(Draws, "flux", 0, text=False)
    c = Router([LOCAL, flux]).choose(Need(images_out=True))
    assert c.backend is flux
    assert "qwen: lacks images" in c.rejected


def test_what_a_run_brings(tmp_path):
    import json
    from eki.engine import _inputs_of, _product_of
    assert _product_of("image") == "image" and _product_of("code") == ""
    run = {"cwd": str(tmp_path), "payload": json.dumps({"attachments": ["/x/a.PNG", "/x/notes.txt"]})}
    assert _inputs_of(run) == ["folder", "image"]
    assert _inputs_of({"cwd": "", "payload": ""}) == []
