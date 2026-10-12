import json

import pytest

from eki import projectbrief, projects, workspace

OLD = "for the person to merge"


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return r


def _card(proj, name, spec):
    d = proj / "components" / name
    d.mkdir(parents=True)
    (d / "component.json").write_text(json.dumps(spec))


def _knowledge(proj, name, files, read, about):
    (proj / files[0]).write_text("# notes\n")
    _card(proj, name, {"kind": "knowledge", "about": about, "files": files, "read": read})


def _build(project):
    return projectbrief.build(goal="g", title="t", spec="s", files=["app.py"], branch="eki/abc",
                              base="0" * 40, source="/src", others=[], project=project)


def test_plan_lists_the_cards_and_asks_for_context(conn, proj):
    _knowledge(proj, "decisions", ["DECISIONS.md"], "always", "owner-approved rules")
    _knowledge(proj, "architecture", ["ARCH.md", "docs/*.md"], "when-relevant", "how the code hangs together")
    p = projects.get(conn, projects.add(conn, proj))
    text = projectbrief.plan("fix it", "0" * 40, p)
    assert "- decisions (read: always; card decisions): owner-approved rules — files: DECISIONS.md" in text
    assert ("- architecture (read: when-relevant; card architecture): how the code hangs together — files: ARCH.md, docs/*.md"
            in text)
    assert 'Give each item a "context" list' in text
    assert '"context": [{"name": "scene-loader", "why": "touches how scenes load"}]' in text
    assert text.rstrip().endswith("}]")


def test_no_cards_no_section(conn, proj):
    p = projects.get(conn, projects.add(conn, proj))
    text = projectbrief.plan("fix it", "0" * 40, p)
    assert "knowledge documents" not in text and '"context"' not in text
    assert 'ITEMS:\n[{"title": "...",' in text


def test_a_broken_card_never_breaks_the_plan(conn, proj, monkeypatch):
    from eki import components
    monkeypatch.setattr(components, "own", lambda root: 1 / 0)
    p = projects.get(conn, projects.add(conn, proj))
    assert "ITEMS:" in projectbrief.plan("fix it", "0" * 40, p)


def test_build_says_how_the_work_lands(conn, proj):
    pid = projects.add(conn, proj)
    projects.set_autonomy(conn, pid, "apply")
    p = projects.get(conn, pid)
    assert projectbrief.landing(p) == "auto"
    text = _build(p)
    assert "once it passes the checks, lands it on the\nproject's branch trunk itself" in text
    assert OLD not in text

    projects.set_autonomy(conn, pid, "propose")
    p = projects.get(conn, pid)
    assert projectbrief.landing(p) == "person"
    assert "proposes it as the branch eki/abc for the person to merge" in _build(p)

    conn.execute("UPDATE projects SET repo='me/proj' WHERE id=?", (pid,))
    p = projects.get(conn, pid)
    assert projectbrief.landing(p) == "pr"
    text = _build(p)
    assert "opens a pull request from the branch eki/abc\nfor the person to review and merge" in text
    assert OLD not in text


def test_the_workflow_card_says_first(conn, proj):
    pid = projects.add(conn, proj)
    projects.set_autonomy(conn, pid, "apply")
    _card(proj, "workflow", {"kind": "workflow", "about": "how work runs", "merge": "person"})
    assert projectbrief.landing(projects.get(conn, pid)) == "person"


def test_a_playtester_card_adds_the_start_path_line(conn, proj):
    p = projects.get(conn, projects.add(conn, proj))
    assert "?jump=s25" not in projectbrief.plan("fix it", "0" * 40, p)
    _card(proj, "playtester", {"kind": "agent", "about": "plays", "url": "http://127.0.0.1:4200/"})
    (proj / "components" / "playtester" / "prompt.md").write_text("Play {url}.\n")
    text = projectbrief.plan("fix it", "0" * 40, p)
    assert '`eki use playtester "?jump=s25 <focus>"`' in text and "never a URL" in text
