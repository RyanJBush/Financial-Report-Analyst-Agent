import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # lets tests import helpers.py

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def pytest_configure(config):
    config.addinivalue_line("markers", "loopback: test may open connections to 127.0.0.1/::1 only (local test server)")


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Tests must never touch the network. Any attempt fails loudly.

    Tests marked `loopback` may talk to 127.0.0.1/::1 (a local test server) and nothing else.
    """
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo
    allow_loopback = request.node.get_closest_marker("loopback") is not None

    def blocked_connect(self, address, *a, **k):
        if allow_loopback and isinstance(address, tuple) and address[0] in LOOPBACK:
            return real_connect(self, address, *a, **k)
        raise AssertionError(f"network access attempted during tests: {address!r}")

    def blocked_getaddrinfo(host, *a, **k):
        if allow_loopback and host in LOOPBACK:
            return real_getaddrinfo(host, *a, **k)
        raise AssertionError(f"name resolution attempted during tests: {host!r}")

    monkeypatch.setattr(socket, "getaddrinfo", blocked_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
