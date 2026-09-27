import json
import time

from eki import asks, db, digest

DAY = 86400.0


def local(y, mo, d, h, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


NOW = local(2026, 9, 26, 10)


def add_item(conn, iid, state, updated_at, title=None, **more):
    cols = {"id": iid, "goal_id": "g1", "title": title or f"title {iid}", "state": state,
            "created_at": updated_at - 100, "updated_at": updated_at, **more}
    conn.execute(f"INSERT INTO items({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 list(cols.values()))


def add_journal(conn, t, kind, data=None, build=None):
    conn.execute("INSERT INTO journal(t, kind, build, data) VALUES (?,?,?,?)",
                 (t, kind, build, json.dumps(data or {})))


def set_self(home, **self):
    p = home / "routing.json"
    cfg = json.loads(p.read_text())
    cfg["self"] = self
    p.write_text(json.dumps(cfg))


def long_page(conn, now):
    return digest.long_of(digest.write(conn, now)).read_text()


def lines_with(text, iid):
    return [ln for ln in text.splitlines() if ln.startswith(f"- {iid} ")]


def section(text, name):
    part = text.split(f"## {name}\n", 1)[1]
    return part.split("\n## ", 1)[0]


def test_every_changed_item_once_in_its_group(conn):
    t = NOW - 3600
    add_item(conn, "live1", "live", t, build="b7")
    add_item(conn, "land1", "landed", t, build="b8")
    add_item(conn, "app1", "applied", t)
    add_item(conn, "unfit1", "unfit", t, error="gate 2 failed", verdict="FAILED tests/test_x.py\nmore")
    add_item(conn, "train1", "unfit", t, error="the train's check failed", verdict="boom in check\n...")
    add_item(conn, "back1", "rolled back", t, error="rolled back from build b6: exit 1 after 3s")
    add_item(conn, "left1", "left", t, error="the rebase onto abc failed: conflict")
    add_item(conn, "lock1", "locked", t, locked=json.dumps(["eki/engine.py"]))
    add_item(conn, "prop1", "proposed", t)
    add_item(conn, "build1", "building", t)
    add_item(conn, "old1", "live", NOW - 3 * DAY, build="b1")     # unchanged: not on the page
    add_item(conn, "late1", "live", NOW + 60, build="b9")          # after the page: not on it
    text = long_page(conn, NOW)

    assert lines_with(text, "old1") == [] and lines_with(text, "late1") == []
    want = {"Landed and live": ["live1", "land1", "app1"],
            "Went wrong": ["unfit1", "train1", "back1", "left1"],
            "Waits for you": ["lock1", "prop1"],
            "Still moving": ["build1"]}
    for group, ids in want.items():
        body = section(text, group)
        for iid in ids:
            assert len(lines_with(text, iid)) == 1, iid
            assert lines_with(body, iid), (group, iid)
    assert "live in build b7" in lines_with(text, "live1")[0]
    assert "b8" in lines_with(text, "land1")[0]
    assert "gate 2 failed" in lines_with(text, "unfit1")[0]
    assert "reverted by the train" in lines_with(text, "train1")[0]
    assert "boom in check" in lines_with(text, "train1")[0]
    assert "exit 1" in lines_with(text, "back1")[0]
    assert "waits for you" in lines_with(text, "left1")[0] and "conflict" in lines_with(text, "left1")[0]
    assert "eki/engine.py" in lines_with(text, "lock1")[0]
    assert "proposed; `eki self apply prop1`" in lines_with(text, "prop1")[0]
    assert "title live1" in lines_with(text, "live1")[0]


def test_empty_groups_say_so(conn):
    text = long_page(conn, NOW)
    assert "## Landed and live\n\nNothing." in text
    assert "## Still moving" not in text
    assert "## Worse builds\n\nNone." in text


def test_score_section_and_worse_build(conn):
    for i in range(4):
        add_journal(conn, NOW - 3600 - i, "run", {"state": "done", "local": True, "first_text": 2.0})
    add_journal(conn, NOW - DAY - 3600, "run", {"state": "done", "local": False})
    add_journal(conn, NOW - 100, "fault", {"frame": "eki/x.py:1"})
    add_journal(conn, NOW - 200, "correction", {})
    add_journal(conn, NOW - 300, "correction", {})
    conn.execute("INSERT INTO build_scores(build, healthy_at, before, after, measured_at, verdict)"
                 " VALUES ('bad1', ?, '{}', '{}', ?, 'worse')", (NOW - 2 * DAY, NOW - 600))
    conn.execute("INSERT INTO build_scores(build, healthy_at, before, after, measured_at, verdict)"
                 " VALUES ('ok1', ?, '{}', '{}', ?, 'same')", (NOW - 2 * DAY, NOW - 600))
    text = long_page(conn, NOW)
    score = section(text, "Score")
    assert "| runs | 4 | 1 |" in score
    assert "| local share | 100% | 0% |" in score
    assert "1 fault(s), 2 correction(s)" in score
    worse = section(text, "Worse builds")
    assert "`eki self undo bad1`" in worse and "ok1" not in worse


def test_due_before_after_and_once_a_day(conn, home):
    set_self(home, digest_at="09:30")
    assert not digest.due(local(2026, 9, 26, 9, 29))
    assert digest.due(local(2026, 9, 26, 9, 30))
    assert digest.tick(conn, local(2026, 9, 26, 8)) is None
    first = digest.tick(conn, local(2026, 9, 26, 9, 45))
    assert first is not None and first.name == "2026-09-26.md"
    assert not digest.due(local(2026, 9, 26, 12))
    assert digest.tick(conn, local(2026, 9, 26, 12)) is None
    assert digest.tick(conn, local(2026, 9, 27, 9, 31)).name == "2026-09-27.md"
    assert digest.latest().name == "2026-09-27.md"


def test_default_time_is_nine(conn):
    assert not digest.due(local(2026, 9, 26, 8, 59))
    assert digest.due(local(2026, 9, 26, 9, 0))


def test_next_page_starts_where_the_last_ended(conn):
    first_at = NOW
    digest.write(conn, first_at)
    assert digest.last() == first_at
    add_item(conn, "a", "live", first_at - 10, build="b1")      # on the first page's window only
    add_item(conn, "b", "live", first_at + 10, build="b2")
    text = long_page(conn, first_at + DAY)
    assert lines_with(text, "a") == [] and len(lines_with(text, "b")) == 1
    assert digest.last() == first_at + DAY


def test_first_page_covers_the_last_day(conn):
    add_item(conn, "in", "live", NOW - DAY + 60)
    add_item(conn, "out", "live", NOW - DAY - 60)
    text = long_page(conn, NOW)
    assert lines_with(text, "in") and not lines_with(text, "out")


def test_rewrite_today_and_restart_change_nothing(conn):
    add_item(conn, "x", "live", NOW - 60, build="b1")
    path = digest.write(conn, NOW)
    text = path.read_text()
    fresh = db.connect()                   # a restart: nothing held in memory
    assert not digest.due(NOW + 60)
    assert digest.tick(fresh, NOW + 60) is None
    assert path.read_text() == text and digest.last() == NOW
    assert digest.latest() == path
    assert not list(digest.folder().glob(".*.tmp"))


def add_goal(conn, gid, state, wish=None, draft_run=None, text="the goal"):
    conn.execute("INSERT INTO goals(id, text, state, wish, draft_run, created_at) VALUES (?,?,?,?,?,?)",
                 (gid, text, state, wish, draft_run, NOW - 5 * DAY))


def ask_colour(conn, run_id):
    return asks.create(conn, run_id, "t1", "question",
                       {"questions": [{"question": "Which colour?\nmore", "options": [
                           {"label": "red"}, {"label": "blue"}]}]})


def test_drafting_goal_with_open_question_waits_for_you(conn):
    add_goal(conn, "gd1", "drafting", wish="make the window blue\nand more", draft_run="r1")
    add_goal(conn, "gp1", "planning", wish="plan me", draft_run="r2")
    add_goal(conn, "gq1", "drafting", wish="nothing asked", draft_run="r3")
    aid = ask_colour(conn, "r1")
    ask_colour(conn, "r2")
    text = long_page(conn, NOW)
    body = section(text, "Waits for you")
    assert f"- goal gd1 make the window blue — asked you: Which colour? (eki answer {aid})" in body
    assert "Nothing." not in body
    assert "gp1" not in text and "gq1" not in text
    assert text.count("- goal gd1 ") == 1

    asks.answer(conn, aid, {"answers": {"Which colour?": "blue"}})
    text = long_page(conn, NOW + 60)
    assert "gd1" not in text
    assert "## Waits for you\n\nNothing." in text


def add_pick(conn, gid, created_at, pick_key, why, text="fix the fault\nmore", state="planning"):
    conn.execute("INSERT INTO goals(id, text, state, created_at, pick_key, why) VALUES (?,?,?,?,?,?)",
                 (gid, text, state, created_at, pick_key, why))


def test_picked_by_eki_lists_goals_in_the_window_with_why(conn):
    add_pick(conn, "gk1", NOW - 7200, "fault:eki/x.py:12", "fault eki/x.py:12 KeyError, seen 3 times in 7 days")
    add_pick(conn, "gk2", NOW - 3600, "roadmap:abc", "the most impact\nmore", text="do the roadmap thing",
             state="left")
    add_pick(conn, "gk3", NOW - 3 * DAY, "journal:handoff:answer", "old pick")     # before the window
    add_pick(conn, "gk4", NOW + 60, "journal:handoff:answer", "late pick")         # after the window
    add_pick(conn, "gk5", NOW - 600, None, None, text="the person's goal")          # not picked by eki
    text = long_page(conn, NOW)
    body = section(text, "Picked by eki")
    assert body.strip().splitlines() == [
        "- gk2 do the roadmap thing — the most impact (left)",
        "- gk1 fix the fault — fault eki/x.py:12 KeyError, seen 3 times in 7 days (planning)"]
    assert "gk3" not in text and "gk4" not in text and "gk5" not in text
    assert text.index("## Picked by eki") < text.index("## Score")
    assert text.index("## Waits for you") < text.index("## Picked by eki")


def test_picked_by_eki_empty_says_nothing(conn):
    text = long_page(conn, NOW)
    assert "## Picked by eki\n\nNothing." in text


def _project(conn, tmp_path):
    from eki import projects, workspace
    r = tmp_path / "garden"
    r.mkdir()
    (r / "app.py").write_text("print('hi')\n")
    workspace.git(r, "init", "-q", "-b", "trunk")
    workspace.git(r, "add", "-A")
    workspace.git(r, "commit", "-q", "-m", "first")
    return projects.add(conn, r), r.resolve()


def test_a_proposed_project_item_names_its_project_and_the_merge_hint(conn, tmp_path):
    pid, path = _project(conn, tmp_path)
    conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project) VALUES (?,?,?,?,?,?,?)",
                 ("gp", "tidy the garden", "standing", "eki", "planned", NOW - DAY, pid))
    add_item(conn, "pp1", "proposed", NOW - 3600, goal_id="gp", branch="eki/pp1")
    add_item(conn, "pa1", "applied", NOW - 3600, goal_id="gp", branch="eki/pa1")
    add_item(conn, "self1", "proposed", NOW - 3600)
    text = long_page(conn, NOW)
    line = lines_with(section(text, "Waits for you"), "pp1")[0]
    assert "(garden)" in line
    assert f"`git -C {path} merge eki/pp1`" in line and "eki self apply" not in line
    assert "merged into trunk" in lines_with(section(text, "Landed and live"), "pa1")[0]
    assert "proposed; `eki self apply self1`" in lines_with(text, "self1")[0]
    assert "(garden)" not in lines_with(text, "self1")[0]


# ---- the short page -----------------------------------------------------------------------

def test_write_makes_the_short_page_and_the_long_one_beside_it(conn):
    add_item(conn, "sh1", "live", NOW - 3600, title="Station folds each section to one line",
             touched=json.dumps(["eki/web/station.js", "tests/test_web_station.py"]))
    add_item(conn, "sh2", "landed", NOW - 1800, title="The train ships a hand-pushed fix",
             files=json.dumps(["eki/train.py"]))
    add_item(conn, "sh3", "live", NOW - 1700, title="only tests", touched=json.dumps(["tests/test_x.py"]))
    path = digest.write(conn, NOW)
    long = digest.long_of(path)
    assert path.name == "2026-09-26.md" and long.name == "2026-09-26.long.md"
    assert long.parent == path.parent and digest.latest() == path

    short = path.read_text()
    assert short.startswith("# eki 09-26\n")
    assert "sh1" not in short and "sh2" not in short and "## " not in short
    assert short.rstrip("\n").splitlines()[-1] == "Waits for you: nothing.  Score: same as yesterday."
    assert "Self-build\n- The train ships a hand-pushed fix" in short
    assert "Web UI\n- Station folds each section to one line" in short
    assert "only tests" not in short
    assert short.index("Self-build") < short.index("Web UI")

    text = long.read_text()
    assert text.startswith("# eki digest — 2026-09-26 (long)\n")
    for part in ("## Landed and live", "## Picked by eki", "## Score", "## Worse builds"):
        assert part in text
    assert lines_with(text, "sh1") and lines_with(text, "sh3")


def test_project_and_unfit_items_are_only_on_the_long_page(conn, tmp_path):
    pid, _ = _project(conn, tmp_path)
    conn.execute("INSERT INTO goals(id, text, source, owner, state, created_at, project) VALUES (?,?,?,?,?,?,?)",
                 ("gp", "tidy the garden", "standing", "eki", "planned", NOW - DAY, pid))
    add_item(conn, "pa1", "applied", NOW - 3600, goal_id="gp", title="garden tidied")
    add_item(conn, "bad1", "unfit", NOW - 3600, title="broken thing", error="gate 2 failed")
    add_item(conn, "ok1", "live", NOW - 3600, title="engine restarts faster", touched=json.dumps(["eki/engine.py"]))
    path = digest.write(conn, NOW)
    short = path.read_text()
    assert "garden tidied" not in short and "broken thing" not in short
    assert "Engine\n- engine restarts faster" in short
    assert [c.id for c in digest.changes(conn, NOW - DAY, NOW)] == ["ok1"]
    long = digest.long_of(path).read_text()
    assert lines_with(long, "pa1") and lines_with(long, "bad1")


def test_changes_take_touched_then_files(conn):
    add_item(conn, "c1", "live", NOW - 60, touched=json.dumps(["docs/self-build.md"]),
             files=json.dumps(["eki/engine.py"]))
    add_item(conn, "c2", "live", NOW - 50, files=json.dumps(["eki/routing/table.py"]))
    add_item(conn, "c3", "live", NOW - 40)
    got = {c.id: (c.area, c.kind) for c in digest.changes(conn, NOW - DAY, NOW)}
    assert got["c1"] == ("Docs", "docs")
    assert got["c2"] == ("Routing", "change")
    assert got["c3"][0] == "Engine"


def test_nothing_landed(conn):
    short = digest.write(conn, NOW).read_text()
    assert short.splitlines()[1] == "Nothing new landed."


def test_waits_counts_locked_proposed_and_asking(conn):
    assert digest.waits(conn) == 0
    add_item(conn, "w1", "locked", NOW - 3 * DAY)        # old: still waits
    add_item(conn, "w2", "proposed", NOW - 60)
    add_item(conn, "w3", "live", NOW - 60)
    add_goal(conn, "gd1", "drafting", wish="blue", draft_run="r1")
    ask_colour(conn, "r1")
    assert digest.waits(conn) == 3
    short = digest.write(conn, NOW).read_text()
    assert short.rstrip("\n").splitlines()[-1].startswith("Waits for you: 3.  Score: ")


def test_verdict_same_with_no_runs_and_worse_with_a_worse_build(conn):
    assert digest.verdict(conn, NOW - DAY, NOW) == "same as yesterday"
    conn.execute("INSERT INTO build_scores(build, healthy_at, before, after, measured_at, verdict)"
                 " VALUES ('abcdef0123', ?, '{}', '{}', ?, 'worse')", (NOW - 2 * DAY, NOW - 600))
    assert digest.verdict(conn, NOW - DAY, NOW) == "worse: build abcdef0 judged worse, see --long"
    short = digest.write(conn, NOW).read_text()
    assert short.rstrip("\n").endswith("Score: worse: build abcdef0 judged worse, see --long.")


def test_verdict_names_why_it_is_worse(conn):
    for i in range(4):
        add_journal(conn, NOW - DAY - 3600 - i, "run", {"state": "done", "local": True})
        add_journal(conn, NOW - 3600 - i, "run", {"state": "done", "local": False})
    assert digest.verdict(conn, NOW - DAY, NOW) == "worse: less done locally"
    assert digest.verdict(conn, NOW, NOW + DAY) == "same as yesterday"      # nothing after: unknown
