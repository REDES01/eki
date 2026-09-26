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
