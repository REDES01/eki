"""eki's own threads stay out of the chat list unless one needs you."""
from eki import api, store


def test_own_threads_are_hidden_but_yours_stay(conn):
    mine = store.create_thread(conn, "write a haiku about snow", None)
    for title in ("chore: review abc", "self: Shell zoom", "gate 2: x", "train check: build y",
                  "plan: z", "draft: w"):
        store.create_thread(conn, title, None)
    assert [t["id"] for t in api.threads(conn)] == [mine]
