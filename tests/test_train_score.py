"""The train writes down the score before a build once it's healthy (gate 4);
`eki builds` shows the verdict."""
import json

import pytest

from test_train import conn, land, src  # noqa: F401  (fixtures)

from eki import builds, cli, db, score, train  # noqa: E402


def healthy_swap():
    rec = builds.root() / "swap.json"
    data = json.loads(rec.read_text())
    data.update(state="healthy", healthy_at=1234.5)
    rec.write_text(json.dumps(data))


def rows(conn):
    return conn.execute("SELECT * FROM build_scores").fetchall()


def test_a_build_that_goes_healthy_gets_its_before_score_once(conn, src):  # noqa: F811
    land(conn, "a")
    train.release(conn)
    healthy_swap()
    train.settle(conn)
    got = rows(conn)
    assert len(got) == 1 and got[0]["build"] == builds.current().name
    assert got[0]["healthy_at"] == 1234.5 and "runs" in json.loads(got[0]["before"])
    assert got[0]["verdict"] is None
    train.settle(conn)
    assert len(rows(conn)) == 1


def test_a_score_failure_never_holds_the_items(conn, src, monkeypatch):  # noqa: F811
    iid = land(conn, "a")
    train.release(conn)
    healthy_swap()

    def boom(*a, **k):
        raise RuntimeError("no score")
    monkeypatch.setattr(score, "record_build", boom)
    train.settle(conn)
    assert conn.execute("SELECT state FROM items WHERE id=?", (iid,)).fetchone()["state"] == "live"


def fake_build(bid):
    d = builds.root() / bid
    d.mkdir(parents=True)
    (d / ".eki-build.json").write_text(json.dumps({"id": bid, "commit": "c" * 40, "made_at": 1.0}))


@pytest.mark.parametrize("verdict,shown", [("worse", "worse"), (None, "measuring"), ("better", "better")])
def test_eki_builds_shows_the_verdict(conn, capsys, verdict, shown):
    fake_build("aaaaaaaaaaaa")
    fake_build("bbbbbbbbbbbb")
    conn.execute("INSERT INTO build_scores(build, healthy_at, before, verdict) VALUES (?,?,?,?)",
                 ("aaaaaaaaaaaa", 10.0, "{}", verdict))
    conn.commit()
    assert cli.main(["builds"]) == 0
    out = capsys.readouterr().out
    line_a = next(ln for ln in out.splitlines() if "aaaaaaaaaaaa" in ln and "/" in ln)
    line_b = next(ln for ln in out.splitlines() if "bbbbbbbbbbbb" in ln and "/" in ln)
    assert f" {shown} " in line_a and " - " in line_b
    hint = "build aaaaaaaaaaaa made things worse — eki self undo aaaaaaaaaaaa"
    assert (hint in out) == (verdict == "worse")
