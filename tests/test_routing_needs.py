"""decide() honours what a request needs; the why keeps the whole chain; explain as JSON."""
import json

from eki import api, db, routing, store


def _setup(home, provs, rows):
    (home / "providers.json").write_text(json.dumps(provs))
    (home / "routing.json").write_text(json.dumps({"checker": "none", "rows": rows}))


def _run(conn, prompt, tid=None, cwd=None, **kw):
    with db.tx(conn):
        tid = tid or store.create_thread(conn, prompt, cwd)
        rid = store.create_run(conn, tid, prompt, **kw)
    return tid, rid


def test_a_row_needing_images_skips_a_target_that_cant(home, conn):
    _setup(home, {"bare": {"kind": "fake", "harness": False},
                  "fake": {"kind": "fake", "can": ["text", "image"]}},
           [{"key": "general", "title": "all", "targets": ["bare", "fake"]},
            {"key": "image", "title": "pictures", "needs": ["image"], "targets": ["bare", "fake"]}])
    _, rid = _run(conn, "draw a cat")
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider == "fake" and d.row == "image"
    assert d.why == "rule: asks for a picture → image → fake; skipped bare: can't do images"


def test_nothing_can_take_it_waits_with_the_reasons(home, conn):
    _setup(home, {"bare": {"kind": "fake", "harness": False}},
           [{"key": "general", "title": "all", "targets": ["bare"]},
            {"key": "image", "title": "pictures", "needs": ["image"], "targets": ["bare"]}])
    _, rid = _run(conn, "draw a cat")
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider is None and d.why.endswith(": waiting — bare: can't do images")


def test_a_thread_on_a_text_model_leaves_it_for_a_picture(home, conn, tmp_path):
    _setup(home, {"bare": {"kind": "fake", "can": ["text"]}, "fake": {"kind": "fake", "can": ["text", "vision"]}},
           [{"key": "general", "title": "all", "targets": ["bare", "fake"]},
            {"key": "answer", "title": "answer", "targets": ["bare", "fake"]}])
    png = tmp_path / "p.png"
    png.write_bytes(b"\x89PNG")
    tid, _ = _run(conn, "hello")
    conn.execute("UPDATE threads SET provider='bare' WHERE id=?", (tid,))
    conn.commit()
    _, rid = _run(conn, "what is this", tid=tid, attachments=[str(png)])
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider == "fake"
    assert d.why.startswith("bare can't take it (needs vision); rule: a picture attached → answer")
    assert "skipped bare: can't do pictures" in d.why


def test_a_thread_provider_that_can_stays_without_a_model(home, conn):
    _setup(home, {"fake": {"kind": "fake"}, "fake2": {"kind": "fake"}},
           [{"key": "general", "title": "all", "targets": ["fake", "fake2"]},
            {"key": "code", "title": "tools", "targets": ["fake", "fake2"]}])
    tid, _ = _run(conn, "hello")
    conn.execute("UPDATE threads SET provider='fake2' WHERE id=?", (tid,))
    conn.commit()
    _, rid = _run(conn, "fix the build", tid=tid)
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider == "fake2" and d.why == "thread stays with fake2"


def test_a_rule_routed_run_says_so(conn):
    _, rid = _run(conn, "fix the failing test")
    d = routing.decide(conn, store.run(conn, rid))
    assert d.provider == "fake" and d.why == "rule: starts with fix → code → fake"


def test_explain_gives_needs_and_each_target(home, conn):
    _setup(home, {"bare": {"kind": "fake", "harness": False, "about": "A plain model."},
                  "fake": {"kind": "fake"},
                  "gone": {"kind": "fake", "off": True},
                  "mlx": {"kind": "local", "serve": "true", "base_url": "http://127.0.0.1:9"}},
           [{"key": "general", "title": "all", "targets": ["mlx", "fake"]},
            {"key": "code", "title": "tools", "targets": ["bare", "fake", "gone"]}])
    e = routing.explain(conn, "fix the failing test")
    assert e["row"] == "code" and e["why"] == "rule: starts with fix" and e["needs"] == ["tools"]
    t = {x["name"]: x for x in e["targets"]}
    assert t["bare"] == {"name": "bare", "ok": False, "why": "can't do tools", "can": ["text"],
                         "about": "A plain model."}
    assert t["fake"]["ok"] and t["fake"]["can"] == ["text", "tools"]
    assert not t["gone"]["ok"] and t["gone"]["why"] == "switched off"
    e = routing.explain(conn, "tell me something")
    assert e["row"] == "general" and e["needs"] == []
    assert e["targets"][0] == {"name": "mlx", "ok": True, "why": "off; starts for the run",
                               "can": ["text"], "about": ""}
    assert api.route(conn, "fix the failing test")["needs"] == ["tools"]


def test_explain_counts_a_picture(home, conn, tmp_path):
    _setup(home, {"bare": {"kind": "fake", "harness": False}},
           [{"key": "general", "title": "all", "targets": ["bare"]},
            {"key": "answer", "title": "answer", "targets": ["bare"]},
            {"key": "code", "title": "tools", "targets": ["bare"]}])
    e = routing.explain(conn, "what is this", attachments=[str(tmp_path / "a.png")])
    assert e["row"] == "answer" and e["needs"] == ["text", "vision"]
    assert e["targets"][0]["why"] == "can't do pictures"
    e = routing.explain(conn, "what is here", cwd=str(tmp_path))
    assert e["row"] == "code" and e["why"] == "rule: has a folder" and e["needs"] == ["tools"]
