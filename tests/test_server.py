"""The server and a page that hangs up before it is answered."""
import socket
import struct
import threading

import pytest

from eki import api_models, server


@pytest.fixture
def srv(home):
    s = server.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()


def test_a_page_that_hangs_up_is_not_a_fault(srv, monkeypatch):
    asked, answer, done = threading.Event(), threading.Event(), threading.Event()
    faults = []

    def slow_read(conn):                    # the page reloads while the lists are read
        asked.set()
        answer.wait(10)
        return {"providers": {"x": "y" * 4_000_000}}

    monkeypatch.setattr(api_models, "read", slow_read)
    monkeypatch.setattr(server.observe, "fault", lambda *a, **k: faults.append(a))
    finish = server.Handler.finish

    def finished(self):
        try:
            finish(self)
        finally:
            done.set()
    monkeypatch.setattr(server.Handler, "finish", finished)

    c = socket.create_connection(srv.server_address)
    c.sendall(b"GET /api/models HTTP/1.1\r\nHost: x\r\n\r\n")
    assert asked.wait(10)
    c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # hang up hard
    c.close()
    answer.set()
    assert done.wait(10)
    assert faults == []
