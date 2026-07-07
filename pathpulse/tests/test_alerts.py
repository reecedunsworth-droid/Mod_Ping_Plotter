"""Unit tests for the threshold alert evaluator (edge-triggered)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathpulse.alerts import AlertEvaluator
from pathpulse.config import AlertConfig


def _evaluator(**cfg_kwargs):
    events = []
    cfg = AlertConfig(enabled=True, latency_ms=100, loss_pct=5, consecutive=3,
                      **cfg_kwargs)
    ev = AlertEvaluator(target="8.8.8.8", config=cfg,
                        notifier=lambda *a: True,
                        on_event=lambda k, m: events.append((k, m)))
    return ev, events


def test_disabled_never_fires():
    cfg = AlertConfig(enabled=False, latency_ms=1, loss_pct=1, consecutive=1)
    ev = AlertEvaluator("t", cfg, notifier=lambda *a: True)
    assert ev.evaluate(9999, 100, True) is None


def test_fires_after_consecutive_latency_breaches():
    ev, events = _evaluator()
    assert ev.evaluate(200, 0, True) is None   # breach 1
    assert ev.evaluate(200, 0, True) is None   # breach 2
    fired = ev.evaluate(200, 0, True)          # breach 3 -> fire
    assert fired is not None
    assert fired["kind"] == "alert"
    assert "latency" in fired["message"]


def test_does_not_reflood_while_active():
    ev, events = _evaluator()
    for _ in range(3):
        ev.evaluate(200, 0, True)
    # Still breaching, but already active -> no new event.
    assert ev.evaluate(200, 0, True) is None
    assert ev.evaluate(200, 0, True) is None
    # Exactly one 'alert' event was recorded.
    assert [e for e in events if e[0] == "alert"] == events[:1]


def test_recovery_event_then_can_fire_again():
    ev, events = _evaluator()
    for _ in range(3):
        ev.evaluate(200, 0, True)          # fire alert
    rec = ev.evaluate(10, 0, True)         # back to normal -> recovered
    assert rec["kind"] == "recovered"
    # New breach series can fire a fresh alert.
    ev.evaluate(200, 0, True)
    ev.evaluate(200, 0, True)
    fired = ev.evaluate(200, 0, True)
    assert fired["kind"] == "alert"


def test_intermittent_breach_resets_counter():
    ev, _ = _evaluator()
    ev.evaluate(200, 0, True)   # breach
    ev.evaluate(10, 0, True)    # ok -> reset
    ev.evaluate(200, 0, True)   # breach 1 again
    ev.evaluate(200, 0, True)   # breach 2
    assert ev.evaluate(200, 0, True) is not None  # breach 3 -> fire


def test_unreachable_counts_as_loss_breach():
    ev, _ = _evaluator()
    # dest_reached False -> treated as 100% loss.
    ev.evaluate(None, 0, False)
    ev.evaluate(None, 0, False)
    fired = ev.evaluate(None, 0, False)
    assert fired is not None
    assert "loss" in fired["message"]
