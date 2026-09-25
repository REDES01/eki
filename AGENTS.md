# Working on eki

- Read README.md and the modules you touch first; match their style — plain
  docstrings that say why, small functions, no new dependencies.
- A new `eki` command is a new file in `eki/cli/` with its own
  `register(sub)` and `ORDER`; don't grow a shared file. `eki/cli/common.py`
  holds only what several commands need. `eki/cli.py` is an empty marker —
  never put code in it. (docs/self-build.md, "one file per command")
- Tests: `.venv/bin/python -m pytest -q`.
