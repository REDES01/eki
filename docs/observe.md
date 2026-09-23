# eki watching itself

eki keeps a journal of what it notices while it works
(`~/.eki/observe/journal.jsonl`, `eki/observe.py`). Writing it costs
nothing — no model reads it as it's written.

| Kind | What |
|---|---|
| fault | an error whose traceback ends inside eki's own code |
| failed | a provider said no — quota, login, the CLI refused. Not eki's bug |
| friction | you corrected an answer, asked again after a failure, stopped a run |
| gap | a request nothing could take, or a bare model stood in for a harness |
| history | a swap that was rolled back, a learned skill you removed |

`eki observe` shows the last week; `eki observe --all` the latest entries too.

## Fixes and features are different

**Faults get fix proposals, written by eki.** A fault has a right answer and
a test that shows it. When one has happened twice (once, if it broke one of
the engine's own loops), it becomes a piece of self-work: the traceback, the
run, the CLI versions at the time, recent commits to that file. When eki is
working on itself (`eki self on`) the loop takes it first, when there's room;
otherwise it starts at once. The agent works in its own worktree of the code
that's running, adds a regression test, and the candidate check judges it.
The result is a `self/…` branch and a verdict — applied only at the `apply`
autonomy setting (docs/self-build.md). At most once a week per fault, three
a day, one at a time. A fault inside the self-work machinery
(`selfwork`, `candidate`, `builds`, the supervisor, this module) or a
protected path is left for a person: it would be asked to fix itself.

**Features are suggested, never built on eki's own.** What to build is taste
and direction — yours. Friction, gaps and history are the evidence for the
weekly note the loop writes (Goals → Self, `eki self note`): what eki noticed,
the hours the local models worked, and two or three suggestions, leaning
toward adding a provider or server rather than code in eki. What you pick
becomes a ROADMAP item or an `eki self` request — and ROADMAP items are what
the loop builds.

Settings: `self_fix` — `propose` (default) or `off`; `self_fix_daily` (3).
