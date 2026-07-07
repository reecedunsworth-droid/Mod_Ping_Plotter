"""Unit tests for SQLite storage: insert, downsampled query, prune."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathpulse.storage import Storage
from pathpulse.traceroute import HopSample, TraceSample


def _sample(ts, dest_rtt):
    hops = [
        HopSample(ttl=1, address="10.0.0.1", rtts=[1.0, 1.1], reached_dest=False),
        HopSample(ttl=2, address="8.8.8.8", rtts=[dest_rtt, dest_rtt + 0.2],
                  reached_dest=True),
    ]
    return TraceSample("8.8.8.8", "8.8.8.8", ts, hops, dest_reached=True)


def test_insert_and_dest_series(tmp_path):
    st = Storage(str(tmp_path / "t.sqlite"))
    mid = st.get_or_create_monitor("8.8.8.8", "8.8.8.8")
    now = time.time()
    for i in range(10):
        st.insert_pass(mid, _sample(now - (10 - i), 9.0 + i))
    series = st.dest_series(mid, window_seconds=60, max_points=100)
    assert len(series) >= 5
    # Every dest point should carry an rtt around the values we inserted.
    assert all(p["rtt"] is not None for p in series)
    assert min(p["rtt"] for p in series) >= 9.0
    st.close()


def test_hop_series_and_loss(tmp_path):
    st = Storage(str(tmp_path / "t.sqlite"))
    mid = st.get_or_create_monitor("host", "1.2.3.4")
    now = time.time()
    # hop 1 with a lossy pass.
    s = TraceSample("host", "1.2.3.4", now, [
        HopSample(ttl=1, address="10.0.0.1", rtts=[None, None], reached_dest=False),
    ], dest_reached=False)
    st.insert_pass(mid, s)
    series = st.hop_series(mid, ttl=1, window_seconds=60)
    assert len(series) == 1
    assert series[0]["loss"] == 100.0
    st.close()


def test_get_or_create_is_idempotent(tmp_path):
    st = Storage(str(tmp_path / "t.sqlite"))
    a = st.get_or_create_monitor("dup", "1.1.1.1")
    b = st.get_or_create_monitor("dup", "1.1.1.1")
    assert a == b
    assert len(st.list_monitors()) == 1
    st.close()


def test_prune_removes_old(tmp_path):
    st = Storage(str(tmp_path / "t.sqlite"), retention_days=7)
    mid = st.get_or_create_monitor("8.8.8.8", "8.8.8.8")
    old = time.time() - 8 * 86400   # 8 days old
    recent = time.time() - 60
    st.insert_pass(mid, _sample(old, 10))
    st.insert_pass(mid, _sample(recent, 10))
    removed = st.prune()
    assert removed >= 2             # both hop rows of the old pass
    remaining = st.raw_samples(mid)
    assert all(r["ts"] >= recent - 1 for r in remaining)
    st.close()


def test_raw_samples_for_csv(tmp_path):
    st = Storage(str(tmp_path / "t.sqlite"))
    mid = st.get_or_create_monitor("8.8.8.8", "8.8.8.8")
    st.insert_pass(mid, _sample(time.time(), 12))
    rows = st.raw_samples(mid)
    assert len(rows) == 2
    assert {"ts", "ttl", "address", "is_dest", "sent", "received",
            "rtt_best", "rtt_avg"} <= set(rows[0].keys())
    st.close()
