# SPDX-License-Identifier: Apache-2.0
"""One skill store, a view per backend, and the loader for models with none."""
import asyncio
from pathlib import Path

import pytest

from eki import skills
from eki.adapters.base import Message

#: the real check, before conftest makes every program count as installed
REAL_PRESENT = skills.present


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "STORE", tmp_path / "eki" / "skills")
    monkeypatch.setattr(skills, "SIDECAR", tmp_path / "eki" / "skills.json")
    views = {"claude": tmp_path / "claude" / "skills", "codex": tmp_path / "agents" / "skills",
             "gemini": tmp_path / "gemini" / "skills"}
    monkeypatch.setattr(skills, "VIEWS", views)
    monkeypatch.setattr(skills, "LEGACY", [tmp_path / "codex" / "skills"])
    return tmp_path


def _write(folder: Path, name: str, desc: str, body: str = "Do the thing.") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n")


def test_new_skill_is_linked_into_every_cli(home):
    skills.put("haiku", description="Write haikus when asked for a poem", body="5-7-5.")
    for root in skills.VIEWS.values():
        link = root / "haiku"
        assert link.is_symlink()
        assert (link / "SKILL.md").read_text().startswith("---\nname: haiku")
    s = skills.get("haiku")
    assert s["views"] == {"claude": "linked", "codex": "linked", "gemini": "linked"}
    assert s["backends"] == ["claude", "codex", "gemini", "local"]


def test_turning_off_removes_the_link_not_the_skill(home):
    skills.put("haiku", description="poems", body="x")
    skills.set_enabled("haiku", False, "codex")
    assert not (skills.VIEWS["codex"] / "haiku").exists()
    assert (skills.VIEWS["claude"] / "haiku").is_symlink()
    assert (skills.STORE / "haiku" / "SKILL.md").is_file()
    skills.set_enabled("haiku", False)
    assert not (skills.VIEWS["claude"] / "haiku").exists()
    skills.set_enabled("haiku", True)
    skills.set_enabled("haiku", True, "codex")
    assert (skills.VIEWS["codex"] / "haiku").is_symlink()


def test_someone_elses_folder_is_never_touched(home):
    _write(skills.VIEWS["claude"] / "haiku", "haiku", "theirs")
    skills.put("haiku", description="ours", body="x")
    theirs = skills.VIEWS["claude"] / "haiku"
    assert not theirs.is_symlink() and "theirs" in (theirs / "SKILL.md").read_text()
    assert skills.get("haiku")["views"]["claude"] == "conflict"
    skills.remove("haiku")
    assert theirs.is_dir()


def test_import_moves_in_and_links_back(home):
    _write(skills.VIEWS["claude"] / "review", "review", "Review a diff")
    _write(skills.LEGACY[0] / "deploy", "deploy", "Deploy the site")
    found = {u["folder"] for u in skills.unmanaged()}
    assert found == {"review", "deploy"}
    report = skills.import_existing()
    assert set(report["imported"]) == {"review", "deploy"}
    assert (skills.VIEWS["claude"] / "review").is_symlink()
    assert (skills.VIEWS["codex"] / "review").is_symlink()
    assert (skills.VIEWS["codex"] / "deploy").is_symlink()
    assert skills.unmanaged() == []
    assert skills.get("review")["origin"].startswith("claude:")


def test_skills_from_before_gemini_reach_it_until_turned_off(home):
    import json
    skills.put("haiku", description="poems", body="x")
    skills.put("draft", description="drafts", body="x")
    # the sidecar as an eki that knew only Claude Code and Codex wrote it:
    # one skill on everywhere, one kept off Codex
    skills._save_meta({"haiku": {"backends": ["claude", "codex", "local"], "enabled": True},
                       "draft": {"backends": ["claude", "local"], "enabled": True}})
    skills.sync()
    assert skills.get("haiku")["backends"] == ["claude", "codex", "gemini", "local"]
    assert skills.get("draft")["backends"] == ["claude", "gemini", "local"]
    assert (skills.VIEWS["gemini"] / "haiku").is_symlink()
    skills.set_enabled("haiku", False, "gemini")
    assert not (skills.VIEWS["gemini"] / "haiku").exists()
    assert skills.get("haiku")["backends"] == ["claude", "codex", "local"]
    assert json.loads(skills.SIDECAR.read_text())["skills"]["haiku"]["offered"] == list(skills.BACKENDS)
    skills.put("haiku", description="poems", body="y")      # an edit doesn't turn it back on
    assert "gemini" not in skills.get("haiku")["backends"]


def test_a_skill_gemini_wrote_itself_is_taken_in(home):
    from eki import learn
    _write(skills.VIEWS["gemini"] / "notes", "notes", "Keep notes")
    assert learn.adopt_new_folders(0, "gemini", "gemini", "r1") == ["notes"]
    assert (skills.VIEWS["gemini"] / "notes").is_symlink()
    assert skills.get("notes")["origin"] == "agent:gemini"


def test_a_program_that_is_not_here_gets_no_folder(home, monkeypatch):
    from eki.adapters import claude_code
    monkeypatch.setattr(skills, "present", REAL_PRESENT)
    monkeypatch.setattr(skills, "HOMES", {"claude": home / "claude", "codex": home / "codex",
                                          "gemini": home / "gemini"})
    monkeypatch.setattr(claude_code, "_find_binary", lambda name: "/bin/codex" if name == "codex" else None)
    (home / "claude").mkdir()                   # Claude Code has run here; Codex is installed
    skills.put("haiku", description="poems", body="x")
    assert (skills.VIEWS["claude"] / "haiku").is_symlink()
    assert (skills.VIEWS["codex"] / "haiku").is_symlink()
    assert not (home / "gemini").exists()       # never made for a program that isn't here
    # a view made before the program went away loses eki's links, and only those
    _write(skills.VIEWS["claude"] / "theirs", "theirs", "someone else's")
    monkeypatch.setattr(skills, "HOMES", {**skills.HOMES, "claude": home / "nowhere"})
    report = skills.sync()
    assert "claude:haiku" in report["unlinked"]
    assert not (skills.VIEWS["claude"] / "haiku").exists()
    assert (skills.VIEWS["claude"] / "theirs" / "SKILL.md").is_file()
    assert (skills.VIEWS["codex"] / "haiku").is_symlink()


def test_every_change_is_a_commit(home):
    skills.put("haiku", description="poems", body="one")
    skills.put("haiku", description="poems", body="two")
    skills.remove("haiku")
    messages = [h["message"] for h in skills.history()]
    assert messages[:3] == ["remove haiku", "edit haiku", "add haiku"]


def test_whole_skill_md_keeps_extra_frontmatter(home):
    text = "---\nname: other\ndescription: d\nallowed-tools: Bash\n---\nbody\n"
    skills.put("mine", text=text)
    src = skills.source("mine")
    assert "name: mine" in src and "allowed-tools: Bash" in src and "body" in src


def test_bad_names_and_missing_descriptions(home):
    with pytest.raises(ValueError):
        skills.put("../x", description="d")
    with pytest.raises(ValueError):
        skills.put("x", description="  ")


def test_builtin_eki_skill_is_kept_until_edited(home):
    skills.boot()
    assert (skills.VIEWS["claude"] / "eki").is_symlink()
    assert "local" not in skills.get("eki")["backends"]
    skills.put("eki", text="---\nname: eki\ndescription: mine now\n---\nx\n")
    skills.boot()
    assert "mine now" in skills.source("eki")


# ---- the loader for models without one --------------------------------------

def test_invocation_and_pick_parsing(home):
    skills.put("haiku", description="poems", body="5-7-5.")
    s, rest = skills.invoked("/haiku about rain")
    assert s["name"] == "haiku" and rest == "about rain"
    assert skills.invoked("$haiku")[0]["name"] == "haiku"
    assert skills.invoked("/nope hi")[0] is None
    assert skills.picked("[[skill:haiku]]")["name"] == "haiku"
    assert skills.could_be_pick("[[sk") and skills.could_be_pick(" ")
    assert not skills.could_be_pick("Hello")
    skills.set_enabled("haiku", False, "local")
    assert skills.invoked("/haiku x")[0] is None
    assert skills.catalog_prompt() == ""


class FakeBackend:
    def __init__(self, answers):
        self.answers = list(answers)
        self.seen = []

    async def stream(self, messages, **kw):
        self.seen.append(messages)
        for part in self.answers.pop(0):
            yield part


def _run(backend, history):
    from eki.engine import Engine
    meta = {}

    async def go():
        return [c async for c in Engine._skilled(None, backend, history, {}, meta)]
    return asyncio.run(go()), meta


def test_model_asks_for_a_skill_and_gets_it(home):
    skills.put("haiku", description="poems", body="Always 5-7-5.")
    b = FakeBackend([["[[ski", "ll:haiku]]"], ["Rain ", "falls"]])
    out, meta = _run(b, [Message("user", "a poem about rain")])
    assert [c for c in out if isinstance(c, str)] == ["Rain ", "falls"]
    assert meta["skill"] == "haiku"
    assert "haiku" in b.seen[0][0].content                # the catalog
    assert "Always 5-7-5." not in b.seen[0][0].content    # not the body
    assert "Always 5-7-5." in b.seen[1][-2].content       # the body, when asked


def test_ordinary_answer_passes_through(home):
    skills.put("haiku", description="poems", body="x")
    b = FakeBackend([["He", "llo", " there"]])
    out, meta = _run(b, [Message("user", "hi")])
    assert "".join(c for c in out if isinstance(c, str)) == "Hello there"
    assert "skill" not in meta


def test_thinking_goes_by_and_pick_still_works(home):
    skills.put("haiku", description="poems", body="x")
    b = FakeBackend([["<think>hm", "m</think>", "[[skill:haiku]]"], ["ok"]])
    out, meta = _run(b, [Message("user", "poem")])
    text = "".join(c for c in out if isinstance(c, str))
    assert text == "<think>hmm</think>ok" and meta["skill"] == "haiku"


def test_slash_invocation_hands_the_body_over(home):
    skills.put("haiku", description="poems", body="Always 5-7-5.")
    b = FakeBackend([["done"]])
    out, meta = _run(b, [Message("user", "/haiku about snow")])
    assert meta["skill"] == "haiku"
    assert b.seen[0][-1].content == "about snow"
    assert "Always 5-7-5." in b.seen[0][-2].content


def test_no_skills_no_change(home):
    b = FakeBackend([["plain"]])
    out, _ = _run(b, [Message("user", "hi")])
    assert out == ["plain"] and len(b.seen[0]) == 1
