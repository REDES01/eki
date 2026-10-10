"""A project's skills card reaches runs in that project, and only those, and never a bare agent."""
import hashlib
import json
import shutil
from pathlib import Path

from eki import components, mcp, paths, skills, store, worker, workspace
from eki.components import Call, agent, skillcard
from eki.providers.base import Outcome
from eki.providers.claude_code import ClaudeCode
from eki.providers.codex import Codex


class FakeChannel:
    def __init__(self, turn):
        self.turn, self.written, self.state, self.pending = turn, [], {}, {}

    def write(self, obj):
        self.written.append(obj)


def _skill(root, name, desc="does a thing"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    return d


def _card(proj):
    card = proj / "components" / "skills"
    _skill(card, "tidy", "keeps things tidy")
    (card / "broken").mkdir()                         # shown by the card, never handed out
    skillcard._ensure(card)
    return card


def _turn(conn, cwd, row=None):
    tid = store.create_thread(conn, "t", str(cwd))
    rid = store.create_run(conn, tid, "hi", row=row)
    return worker.build_turn(conn, store.run(conn, rid), "fake")


def _plugins(turn):
    argv = ClaudeCode("claude", {"kind": "claude_code"}).argv(turn)
    return [Path(argv[i + 1]) for i, a in enumerate(argv) if a == "--plugin-dir"]


def _project_plugin(turn):
    return [p for p in _plugins(turn) if p.parent == paths.home() / "project-plugins"]


def _snapshot(folder: Path):
    if not folder.exists():
        return None
    out = {}
    for p in sorted(folder.rglob("*")):
        rel = str(p.relative_to(folder))
        out[rel] = ("link", str(p.readlink())) if p.is_symlink() else \
            ("file", hashlib.sha1(p.read_bytes()).hexdigest()) if p.is_file() else ("dir",)
    return out


def test_project_and_worktree_runs_get_the_card_elsewhere_not(conn, proj, tmp_path):
    _card(proj)
    _skill(paths.skills(), "shared")                  # eki's own store comes first
    t = _turn(conn, proj)
    plug = _project_plugin(t)
    assert len(plug) == 1 and (plug[0] / "skills" / "tidy" / "SKILL.md").is_file()
    assert not (plug[0] / "skills" / "broken").exists()
    assert _plugins(t)[0] == Path(skills.claude_plugin())
    wt = tmp_path / "wt"
    workspace.git(proj, "worktree", "add", "-q", "-b", "side", str(wt))
    assert _project_plugin(_turn(conn, wt)) == plug
    other = tmp_path / "other"
    other.mkdir()
    elsewhere = _turn(conn, other)
    assert _project_plugin(elsewhere) == [] and "codex_skills" not in elsewhere.extra
    chore = _turn(conn, proj, row="chore")
    assert _project_plugin(chore) == [] and "codex_skills" not in chore.extra


def test_codex_gets_them_for_its_run_only(conn, proj, home, tmp_path):
    _card(proj)
    _skill(paths.skills(), "shared")
    user_codex = Path(skills.agents_dir())
    skills.sync_codex()                               # the global store link, as the worker does
    codex_home = tmp_path / "user" / ".codex" / "skills"
    before = (_snapshot(user_codex), _snapshot(codex_home))
    t = _turn(conn, proj)
    linked = Path(t.extra["codex_skills"])
    assert linked == paths.home() / "run-skills" / t.run_id
    assert (linked / "tidy" / "SKILL.md").is_file() and not (linked / "broken").exists()
    p = Codex("codex", {"binary": "/bin/echo"})
    ch = FakeChannel(t)
    out = Outcome()
    p.opening(ch, t)
    p.read({"id": 1, "result": {}}, lambda k, d: None, out, ch)
    roots = ch.written[-1]
    assert roots["method"] == "skills/extraRoots/set" and roots["params"] == {"extraRoots": [str(linked)]}
    p.read({"id": roots["id"], "result": {}}, lambda k, d: None, out, ch)
    assert ch.written[-1]["method"] == "thread/start"
    assert "-c" in p.argv(t) and not any("CODEX_HOME" in a for a in p.argv(t))
    assert (_snapshot(user_codex), _snapshot(codex_home)) == before
    worker.drop_run_skills(t.run_id)
    assert not linked.exists()
    worker.drop_run_skills("../nope")                 # never raises, never reaches outside


def test_older_codex_is_told_where_the_skills_are(conn, proj):
    _card(proj)
    t = _turn(conn, proj)
    p = Codex("codex", {})
    ch = FakeChannel(t)
    out = Outcome()
    p.opening(ch, t)
    p.read({"id": 1, "result": {}}, lambda k, d: None, out, ch)
    p.read({"id": ch.written[-1]["id"], "error": {"message": "unknown method"}}, lambda k, d: None, out, ch)
    start = ch.written[-1]
    assert start["method"] == "thread/start" and not out.finished
    note = start["params"]["developerInstructions"]
    assert "tidy — keeps things tidy" in note and t.extra["codex_skills"] in note
    assert "broken" not in note


def test_a_finished_run_drops_its_links(conn, proj):
    from tests.conftest import run_inline
    _card(proj)
    tid = store.create_thread(conn, "t", str(proj))
    rid = store.create_run(conn, tid, "hi", provider="fake")
    run_inline(conn, rid)
    assert not (paths.home() / "run-skills" / rid).exists()


def _call(proj):
    return Call(args=["play"], caller_dir=str(proj), caller_run="r0", run_id="r1",
                emit=lambda k, d: None,
                extra={"claude_plugin": "/x", "codex_skills": "/y", "codex_config": ["a=1"]})


def _with_claude(home):
    f = home / "providers.json"
    cfg = json.loads(f.read_text())
    cfg["claude"] = {"kind": "claude_code", "binary": "claude"}
    f.write_text(json.dumps(cfg))


def test_bare_agent_gets_no_skills(proj, home):
    _with_claude(home)
    _card(proj)
    src = components.templates_dir() / "playtester"
    shutil.copytree(src, proj / "components" / "playtester")
    mcp.save(json.loads((src / "component.json").read_text())["mcp_servers"])
    comp = components.get("playtester", proj)
    s = home / "s"
    s.mkdir()
    _, turn = agent.turn_for(comp, _call(proj), s)
    assert "--plugin-dir" not in ClaudeCode("claude", {"kind": "claude_code"}).argv(turn)
    assert "codex_skills" not in turn.extra and "claude_plugin" not in turn.extra


def test_non_bare_agent_gets_store_then_project_then_its_own(proj, home):
    _with_claude(home)
    _card(proj)
    _skill(paths.skills(), "shared")
    d = proj / "components" / "helper"
    _skill(d / "skills", "notes")
    (d / "component.json").write_text(json.dumps({"kind": "agent", "about": "a persona", "target": "claude"}))
    (d / "prompt.md").write_text("Be kind.")
    s = home / "s"
    s.mkdir()
    _, turn = agent.turn_for(components.get("helper", proj), _call(proj), s)
    plugs = _plugins(turn)
    assert len(plugs) == 3
    assert plugs[0] == Path(skills.claude_plugin())
    assert (plugs[1] / "skills" / "tidy").is_dir()
    assert (plugs[2] / "skills" / "notes").is_dir()
