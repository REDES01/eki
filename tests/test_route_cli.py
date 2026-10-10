"""`eki route`: the table with tags and about, and a request explained target by target."""
import json

from eki import cli


def _setup(home, provs, rows):
    (home / "providers.json").write_text(json.dumps(provs))
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": rows}))


PROVS = {"bare": {"kind": "fake", "harness": False, "about": "A plain text model, quick and free."},
         "fake": {"kind": "fake", "can": ["text", "image"]},
         "gone": {"kind": "fake", "off": True, "can": ["image"]},
         "lm": {"kind": "local", "serve": "true", "base_url": "http://127.0.0.1:1"}}
ROWS = [{"key": "general", "title": "all", "targets": ["lm", "bare", "fake"]},
        {"key": "image", "title": "pictures", "needs": ["image"], "targets": ["bare", "fake", "gone"]}]


def test_explain_a_rule_fired_prompt(home, capsys):
    _setup(home, PROVS, ROWS)
    assert cli.main(["route", "draw", "a", "cat"]) == 0
    out = capsys.readouterr().out
    assert "why:   rule: asks for a picture" in out
    assert "row:   image — pictures   needs: [image]" in out
    assert "  ✗ bare [text]  (can't do images)" in out
    assert "  ✓ fake [text, image]\n" in out
    assert "  ✗ gone [image]  (switched off)" in out


def test_explain_with_a_picture_skips_a_text_model(home, tmp_path, capsys):
    _setup(home, PROVS, ROWS)
    png = tmp_path / "p.png"
    png.write_bytes(b"\x89PNG")
    assert cli.main(["route", "--image", str(png), "what", "is", "this"]) == 0
    out = capsys.readouterr().out
    assert "vision]" in next(l for l in out.splitlines() if l.startswith("row:"))
    assert "✗ lm [text]  (can't do pictures)" in out


def test_explain_an_on_demand_model_counts_as_ok(home, capsys):
    _setup(home, PROVS, ROWS)
    cli.main(["route", "tell", "me", "a", "joke"])
    out = capsys.readouterr().out
    assert "row:   general — all   needs: []" in out
    assert "  ✓ lm [text]  (off; starts for the run)" in out


def test_explain_with_a_folder_is_code(home, tmp_path, capsys):
    _setup(home, PROVS, ROWS + [{"key": "code", "title": "tools", "targets": ["bare", "fake"]}])
    cli.main(["route", "-C", str(tmp_path), "what", "is", "here"])
    out = capsys.readouterr().out
    assert "row:   code — tools   needs: [tools]" in out
    assert "✗ bare [text]  (can't do tools)" in out


def test_table_shows_needs_tags_about_and_unavailable(home, capsys):
    _setup(home, PROVS, ROWS)
    assert cli.main(["route"]) == 0
    out = capsys.readouterr().out
    assert "image    pictures   needs: [image]" in out
    assert "general  all   needs: []" in out
    assert "✓ bare [text] — A plain text model, quick and free." in out
    assert "✗ gone [image]  (switched off)" in out


def _calls(conn, target, ms, n, model=None):
    from eki import roles
    for _ in range(n):
        roles.record(conn, "router", target, model, ms, True)
    conn.commit()


def test_table_prints_the_roles_with_models_and_router_medians(home, conn, capsys, monkeypatch):
    from eki import providers
    monkeypatch.setattr(providers.KINDS["fake"], "complete", lambda self, *a, **k: {}, raising=False)
    provs = dict(PROVS, fake={"kind": "fake",
                              "models": {"fast": "fake-small", "top": "fake-big"}})
    (home / "providers.json").write_text(json.dumps(provs))
    (home / "routing.json").write_text(json.dumps({
        "rows": ROWS + [{"key": "code", "title": "tools", "targets": ["fake:top", "fake"]}],
        "checker": "lm", "roles": {"router": "fake:fast", "planner": "fake:top"}}))
    _calls(conn, "fake:fast", 2400, 20, "fake-small")
    _calls(conn, "lm", 13100, 20)
    assert cli.main(["route"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    router = next(l for l in lines if l.startswith("router "))
    assert router.startswith("router    fake:fast → fake · fake-small   median 2.4 s over 20")
    assert "(lm: 13.1 s over 20)" in router
    assert any(l.startswith("planner   fake:top → fake · fake-big") for l in lines)
    assert any(l.startswith("reviewer  lm → lm · its default") for l in lines)
    assert any(l.startswith("chores    lm → lm · its default") for l in lines)
    assert "checker:" not in out
    assert "fake · top" in next(l for l in lines if "→ fake · fake-big" in l and l.startswith("    "))


def test_table_role_that_falls_back_says_why(home, capsys):
    (home / "providers.json").write_text(json.dumps(PROVS))
    (home / "routing.json").write_text(json.dumps({"checker": "lm", "rows": ROWS,
                                                       "roles": {"router": "gone:fast"}}))
    assert cli.main(["route"]) == 0
    router = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("router "))
    assert router.startswith("router    gone:fast → lm → lm · its default")
    assert "router gone:fast can't run (gone: switched off) → lm" in router


def test_table_prints_each_targets_model_effort_and_note(home, conn, capsys):
    provs = dict(PROVS, fake={"kind": "fake"})
    (home / "providers.json").write_text(json.dumps(provs))
    rows = [{"key": "general", "title": "all",
             "targets": [{"provider": "fake", "model": "big", "effort": "high"},
                         {"provider": "fake", "model": "big", "effort": "max"}, "fake"]}]
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": rows}))
    conn.execute("INSERT INTO provider_models(provider, model, label, efforts, is_default, read_at)"
                 " VALUES ('fake','big','Big One','[\"low\", \"high\"]',1,1)")
    conn.commit()
    assert cli.main(["route"]) == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("    ")]
    assert lines[0].startswith("    ✓ fake · big · high [text, tools] → fake · Big One · high")
    assert lines[1].endswith("→ fake · Big One (fake big doesn't offer effort max any more; its default)")
    assert lines[2] == "    ✓ fake [text, tools]"
