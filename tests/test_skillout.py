"""Skills, eki -> client: learned and edited skills land in the outbox, nothing else does."""
import json
import shutil

from eki import paths, skillout, syncstate


def store_skill(name, body="# s\n", source=None, extra=None):
    d = paths.skills() / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body)
    for rel, text in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    if source:
        f = paths.config("skill-sources")
        got = json.loads(f.read_text()) if f.exists() else {}
        got[name] = source
        f.write_text(json.dumps(got))
    return d


def outbox(name):
    return syncstate.outbox() / "skills" / name


def seed_baseline(**folders):
    syncstate.set_baseline("skills", {n: {"hash": syncstate.tree_hash(d), "from": "/client/" + n}
                                      for n, d in folders.items()})


def test_only_eki_s_own_skills_are_written(home, conn):
    store_skill("learnt", "# learnt\n", "learned", {"scripts/run.sh": "echo hi\n"})
    store_skill("tuned", "# tuned\n", "edited")
    edited = store_skill("game-design-principles", "# client\n", "claude")
    plain = store_skill("comfyui-local-api", "# client\n", "claude")
    seed_baseline(**{"game-design-principles": edited, "comfyui-local-api": plain})
    (edited / "SKILL.md").write_text("# client, edited in eki\n")
    store_skill("from-folder", "# f\n", "/some/folder")
    store_skill("eki", "# eki\n")

    said = skillout.tick(conn)

    assert sorted(p.name for p in (syncstate.outbox() / "skills").iterdir()) == \
        ["game-design-principles", "learnt", "tuned"]
    assert (outbox("learnt") / "scripts" / "run.sh").read_text() == "echo hi\n"
    assert (outbox("game-design-principles") / "SKILL.md").read_text() == "# client, edited in eki\n"
    assert len(said) == 3 and all("outbox" in line for line in said)
    assert syncstate.status()["skills-out"]["count"] == 3


def test_a_claude_skill_with_no_baseline_counts_as_eki_s_own(home, conn):
    store_skill("orphan", "# o\n", "claude")
    skillout.tick(conn)
    assert outbox("orphan").is_dir()


def test_a_second_pass_rewrites_nothing_and_a_later_edit_is_written(home, conn):
    d = store_skill("learnt", "# v1\n", "learned")
    skillout.tick(conn)
    md = outbox("learnt") / "SKILL.md"
    before = md.stat().st_mtime_ns
    assert skillout.tick(conn) == []
    assert md.stat().st_mtime_ns == before

    (d / "SKILL.md").write_text("# v2\n")
    (d / "notes.md").write_text("more\n")
    assert len(skillout.tick(conn)) == 1
    assert md.read_text() == "# v2\n" and (outbox("learnt") / "notes.md").read_text() == "more\n"
    assert syncstate.status()["skills-out"]["count"] == 1


def test_outbox_entries_are_never_deleted(home, conn):
    d = store_skill("learnt", "# l\n", "learned")
    skillout.tick(conn)
    (outbox("other")).mkdir()
    (outbox("other") / "SKILL.md").write_text("# left by an earlier pass\n")
    shutil.rmtree(d)
    assert skillout.tick(conn) == []
    assert outbox("learnt").is_dir() and outbox("other").is_dir()
    assert syncstate.status()["skills-out"]["count"] == 2


def test_an_empty_store_records_zero(home, conn):
    assert skillout.tick(conn) == []
    assert syncstate.status()["skills-out"]["count"] == 0
    assert syncstate.status()["skills-out"]["at"] is not None
