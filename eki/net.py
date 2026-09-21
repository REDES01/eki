# SPDX-License-Identifier: Apache-2.0
"""Plain HTTPS fetches for the few public files eki reads.

Python's urllib tries every address a host resolves to in order, waiting
the full timeout on each; on a Mac whose IPv6 route goes nowhere that is a
minute per request to hosts that publish IPv6 addresses. This opener tries
IPv4 first, the way curl and browsers do.
"""
from __future__ import annotations

import http.client
import json
import socket
import urllib.request
from typing import Any, Optional

CONNECT_TIMEOUT = 10.0


def _connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):  # type: ignore[no-untyped-def]
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    infos.sort(key=lambda i: i[0] != socket.AF_INET)   # v4 first, v6 as the fallback
    err: Optional[OSError] = None
    for family, kind, proto, _, sockaddr in infos:
        s = socket.socket(family, kind, proto)
        try:
            s.settimeout(CONNECT_TIMEOUT)
            if source_address:
                s.bind(source_address)
            s.connect(sockaddr)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                s.settimeout(timeout)
            return s
        except OSError as e:
            err = e
            s.close()
    raise err or OSError(f"no address for {host}")


class _Conn(http.client.HTTPSConnection):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._create_connection = _connect


class _Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):                      # type: ignore[no-untyped-def]
        return self.do_open(_Conn, req, context=self._context)


_opener = urllib.request.build_opener(_Handler())


def get_bytes(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "eki"})
    with _opener.open(req, timeout=timeout) as r:   # noqa: S310 (fixed public hosts)
        return r.read()


def get_json(url: str, timeout: float = 60.0) -> Any:
    return json.loads(get_bytes(url, timeout).decode("utf-8"))
