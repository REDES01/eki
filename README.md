# eki

The station on your Mac: one queue for your local models and the agents you
rent (Claude Code, Codex). Ask once; eki routes it, runs it, and keeps it
running through restarts.

This is the from-zero rebuild (September 2026). The eki before it is kept,
dated, in `~/eki-2026-09-26`.

```sh
bin/eki ask "write a haiku about snow"          # local model if it's up
bin/eki ask -C ~/games/rpg "fix the failing test"
bin/eki ask -c "make it about rain"              # same thread
bin/eki ask --to codex "…"                       # pick the provider
bin/eki ask --bg --background "…"                # runs when the Mac has room
bin/eki runs | follow | show <run> | cancel <run>
bin/eki route "fix the failing test"             # where it would go, and why
bin/eki providers | skills | mcp
bin/eki models [start|stop] local               # local models; kept up while there's room
bin/eki open                                     # the window (http://127.0.0.1:7788)
bin/eki engine install                           # login agent: always on (the launcher)
bin/eki swap HEAD | --back | --dev               # a new version live, checked first; go back
bin/eki builds                                   # which build runs, which it would go back to
bin/build-mac                                         # build "Eki.app", the thin native shell
bin/eki drill                                    # prove a restart loses nothing
```

Design: [docs/design.md](docs/design.md) · plan: [ROADMAP.md](ROADMAP.md) · how eki changes itself: [docs/self-build.md](docs/self-build.md). Stdlib only; Python 3.11+.
Apache 2.0.
