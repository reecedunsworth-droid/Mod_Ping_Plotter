"""Verify Probers don't steal each other's ICMP replies (raw-mode isolation).

A raw ICMP socket receives every ICMP packet on the host, and Prober sequence
numbers overlap across instances, so correct ident filtering is what keeps two
concurrent monitors from corrupting each other's measurements. This test needs
raw sockets (root) and loopback ICMP, so it skips cleanly when unavailable.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathpulse.probe import ProbeError, Prober


def _raw_prober():
    try:
        p = Prober(mode="raw")
    except (ProbeError, PermissionError, OSError) as exc:
        pytest.skip(f"raw ICMP unavailable: {exc}")
    if p.mode != "raw":
        p.close()
        pytest.skip("raw mode not selected")
    return p


def test_prober_ignores_other_probers_reply():
    a = _raw_prober()
    b = _raw_prober()
    try:
        assert a.ident != b.ident  # unique idents per Prober

        # A sends a probe to loopback; B must NOT consume A's reply even though
        # B's socket also receives it and B may have the same seq value free.
        seq_a = a.send("127.0.0.1", 64)

        # Give B a chance to (wrongly) grab A's reply.
        got_b = b.receive("127.0.0.1", time.monotonic() + 0.3)
        assert got_b is None, "Prober B stole Prober A's reply (ident filter failed)"

        # A still receives its own reply correctly.
        got_a = a.receive("127.0.0.1", time.monotonic() + 0.5)
        assert got_a is not None
        assert got_a.seq == seq_a
        assert got_a.address == "127.0.0.1"
    finally:
        a.close()
        b.close()
