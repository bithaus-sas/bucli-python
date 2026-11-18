import socket
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import bucli  # noqa: E402

_real_connect = socket.socket.connect


def _no_network(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError(f"Las pruebas no salen a la red (intento de conexión a {address!r}).")
    return _real_connect(self, address)


def pytest_configure(config):
    """No test reaches the network: ``responses`` answers every HTTP call, and
    a socket that tries to connect anyway fails loudly."""
    socket.socket.connect = _no_network


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(sleeps):
    """A test-mode client whose backoff is recorded instead of slept."""
    instance = bucli.Bucli("sk_test_abc123", max_retries=2)
    instance._sleep = sleeps.append
    return instance
