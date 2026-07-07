"""Unit tests for rolling statistics."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathpulse.stats import HopStats, TargetStats, loss_color
from pathpulse.traceroute import HopSample, TraceSample


def test_loss_color_thresholds():
    assert loss_color(0.0) == "green"
    assert loss_color(0.9) == "green"
    assert loss_color(1.0) == "yellow"
    assert loss_color(5.0) == "yellow"
    assert loss_color(5.1) == "red"


def test_hop_min_avg_max_current():
    h = HopStats(ttl=1)
    h.add_pass([10.0, 20.0, 30.0], "1.1.1.1")
    assert h.min == 10.0
    assert h.max == 30.0
    assert h.avg == 20.0
    assert h.current == 30.0  # last successful probe of the pass
    assert h.loss_pct == 0.0
    assert h.color == "green"


def test_hop_loss_and_color():
    h = HopStats(ttl=2)
    # 2 of 10 probes lost -> 20% loss -> red.
    h.add_pass([5.0, None, 5.0, None, 5.0], "2.2.2.2")
    h.add_pass([5.0, 5.0, 5.0, 5.0, 5.0], "2.2.2.2")
    assert round(h.loss_pct, 1) == 20.0
    assert h.color == "red"
    assert h.total_sent == 10
    assert h.total_received == 8


def test_hop_jitter():
    h = HopStats(ttl=3)
    h.add_pass([10.0, 12.0, 8.0], "3.3.3.3")  # deltas |2|,|4| -> mean 3.0
    assert h.jitter == 3.0


def test_hop_all_lost_pass_sets_current_none():
    h = HopStats(ttl=4)
    h.add_pass([10.0], "4.4.4.4")
    assert h.current == 10.0
    h.add_pass([None], "4.4.4.4")
    assert h.current is None
    assert round(h.loss_pct, 1) == 50.0


def test_target_stats_update():
    hops = [
        HopSample(ttl=1, address="10.0.0.1", rtts=[1.0, 1.2], reached_dest=False),
        HopSample(ttl=2, address="8.8.8.8", rtts=[9.0, 9.4], reached_dest=True),
    ]
    sample = TraceSample("8.8.8.8", "8.8.8.8", 1000.0, hops, dest_reached=True)
    ts = TargetStats("8.8.8.8", "8.8.8.8")
    ts.update(sample)
    snap = ts.snapshot()
    assert snap["dest_reached"] is True
    assert snap["hop_count"] == 2
    assert len(snap["hops"]) == 2
    assert snap["last_dest_rtt"] == 9.0  # best rtt of final hop
    assert snap["hops"][-1]["reached_dest"] is True
