# Working on eki

Read `docs/design.md` first. The invariants there are not negotiable:

1. A restart at any moment loses nothing. The engine holds no state; workers
   are detached and write to SQLite. `eki-next drill` must pass.
2. eki integrates; it doesn't build tools or harnesses.
3. Subscriptions only through the official CLIs. Never read a token.
4. Everything is a run, and every routing decision says why.
5. No module over 400 lines — split by responsibility before it gets there.
6. Tests never touch the real home (the fixture in tests/conftest.py).

Before committing: `bin/check` (all tests, including the drill). Commit to
main only when it's green. Small commits, one change each.

Layout: `eki/` core modules, `eki/providers/` one file per program,
`eki/routing/` prompt check + table, `eki/cli/` one file per command.
