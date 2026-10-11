"""What a builder is shown of a failed check (eki/selfwork.py failure_tail)."""
from eki import selfwork


def test_js_stack_frames_never_hide_the_errors_printed_before_them():
    errors = [f"  t-oharu-1.{n:02d}: no tags (add its row to ch1/npc/oharu.tags.tsv)" for n in range(2, 12)]
    out = "\n".join(["build: tools/story/build.mjs · ok", *errors,
                     "file:///w/slice/tools/build.mjs:24",
                     "Error: build: tools/script-check/tag.mjs failed (exit 1)",
                     *[f"    at genStory (file:///w/slice/tools/build.mjs:{n}:99)" for n in range(24, 40)],
                     "    at async node:internal/modules/esm/loader:650:26",
                     "Node.js v26.8.2"])
    tail = selfwork.failure_tail(out)
    assert all(e in tail for e in errors)                     # every error line is still there
    assert "    at " not in tail and "Node.js v" not in tail
    assert tail.endswith("Error: build: tools/script-check/tag.mjs failed (exit 1)")


def test_long_output_keeps_its_end_within_bounds():
    out = "\n".join(f"FAILED tests/test_{n}.py::test_x - assert {n}" for n in range(500))
    tail = selfwork.failure_tail(out)
    assert len(tail) <= selfwork.FAILURE_CHARS
    assert tail.splitlines()[-1].endswith("assert 499")
    assert len(tail.splitlines()) <= selfwork.FAILURE_LINES


def test_python_tracebacks_stay():
    out = 'Traceback (most recent call last):\n  File "x.py", line 3, in <module>\nKeyError: \'a\''
    assert selfwork.failure_tail(out) == out
