"""`eki sync`: one line per direction in order, clashes indented under it; `--diff` the drift or 'no drift'."""
import json
import time

from eki.cli import main


def state(home, data):
    d = home / "sync"
    d.mkdir(exist_ok=True)
    (d / "state.json").write_text(json.dumps(data))


def test_five_lines_in_order_never_and_clashes(home, capsys):
    state(home, {"skills-in": {"at": time.time() - 120, "count": 7, "clashes": ["pdf: edited in eki", "docx: learned"],
                               "note": ""},
                 "instructions": {"at": time.time() - 5, "count": 1, "clashes": [], "note": "equal"}})
    assert main(["sync"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [l.split()[0] for l in out if not l.startswith(" ")] == \
        ["skills-in", "skills-out", "memory-in", "memory-out", "instructions"]
    assert out[0].split() == ["skills-in", "2m", "ago", "7", "2", "clashes"]
    assert out[1] == "    pdf: edited in eki" and out[2] == "    docx: learned"
    assert out[3].split() == ["skills-out", "never", "0", "no", "clashes"]
    assert out[4].split()[1] == "never" and out[5].split()[1] == "never"
    assert out[6].split() == ["instructions", "5s", "ago", "1", "no", "clashes"]
    assert len(out) == 7


def test_one_clash_is_singular(home, capsys):
    state(home, {"memory-out": {"at": time.time(), "count": 2, "clashes": ["x"], "note": ""}})
    main(["sync"])
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("memory-out")][0]
    assert line.endswith("1 clash")


def test_no_state_at_all_is_five_nevers(home, capsys):
    assert main(["sync"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 5 and all(l.split()[1:] == ["never", "0", "no", "clashes"] for l in out)


def test_diff_with_and_without_a_diff_file(home, capsys):
    assert main(["sync", "--diff"]) == 0
    assert capsys.readouterr().out == "no drift\n"
    (home / "sync").mkdir(exist_ok=True)
    (home / "sync" / "instructions.diff").write_text("--- a\n+++ b\n-old\n+new\n")
    assert main(["sync", "--diff"]) == 0
    assert capsys.readouterr().out == "--- a\n+++ b\n-old\n+new\n"
