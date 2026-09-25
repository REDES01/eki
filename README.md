# eki (rebuild)

The station on your Mac: one queue for your local models and the agents you
rent (Claude Code, Codex). Ask once; eki routes it, runs it, and keeps it
running through restarts.

This is the from-zero rebuild. It lives beside the old eki until it can do
what the old one is used for daily; then it takes over the name.

```sh
bin/eki-next ask "write a haiku about snow"          # local model if it's up
bin/eki-next ask -C ~/games/rpg "fix the failing test"
bin/eki-next ask -c "make it about rain"              # same thread
bin/eki-next ask --to codex "…"                       # pick the provider
bin/eki-next ask --bg --background "…"                # runs when the Mac has room
bin/eki-next runs | follow | show <run> | cancel <run>
bin/eki-next route "fix the failing test"             # where it would go, and why
bin/eki-next providers | skills | mcp
bin/eki-next models [start|stop] local               # local models; kept up while there's room
bin/eki-next open                                     # the window (http://127.0.0.1:7788)
bin/eki-next engine install                           # login agent: always on
bin/build-mac                                         # build "Eki Next.app", the thin native shell
bin/eki-next drill                                    # prove a restart loses nothing
```

Design: [docs/design.md](docs/design.md). Stdlib only; Python 3.11+.
Apache 2.0.
