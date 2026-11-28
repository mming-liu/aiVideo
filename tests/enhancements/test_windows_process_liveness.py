import os
import pytest
from shared import enhancement_io as eio

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows API")

def test_liveness_probe_does_not_send_console_signals(monkeypatch):
    def forbidden(*args):
        raise AssertionError("a read-only liveness probe must not signal a process")
    monkeypatch.setattr(eio.os, "kill", forbidden)
    assert eio._process_alive(os.getpid()) is True
    assert eio._process_alive(999999999) is False
    assert eio._process_alive(0) is False
