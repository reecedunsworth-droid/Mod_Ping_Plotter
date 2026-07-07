"""Threshold alerting with native macOS notifications.

An alert fires when the destination's latency exceeds ``latency_ms`` OR its
packet loss exceeds ``loss_pct`` for ``consecutive`` passes in a row. Alerts
are edge-triggered: once fired they stay "active" and do not re-notify until
the condition clears, so a persistent problem produces one notification, not a
flood.

Notifications use ``osascript`` on macOS; on other platforms the alert is still
recorded (logged / stored) but no desktop popup is shown.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional


def macos_notify(title: str, message: str, subtitle: str = "") -> bool:
    """Show a macOS notification via osascript. Returns True on success."""
    if platform.system() != "Darwin" or shutil.which("osascript") is None:
        return False

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    if subtitle:
        script += f' subtitle "{esc(subtitle)}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False,
                       timeout=5, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass
class AlertEvaluator:
    """Tracks consecutive-breach state for one target and fires notifications."""

    target: str
    config: "object"  # AlertConfig (avoids import cycle in type hints)
    notifier: Callable[[str, str, str], bool] = macos_notify
    on_event: Optional[Callable[[str, str], None]] = None  # (kind, message)

    _consec: int = 0
    _active: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def evaluate(self, dest_rtt: Optional[float], dest_loss_pct: float,
                 dest_reached: bool) -> Optional[dict]:
        """Feed one pass's destination metrics. Returns an event dict if fired."""
        cfg = self.config
        if not getattr(cfg, "enabled", False):
            return None

        breach_reasons = []
        # An unreachable destination counts as a loss breach.
        effective_loss = 100.0 if not dest_reached else dest_loss_pct
        if effective_loss > cfg.loss_pct:
            breach_reasons.append(f"loss {effective_loss:.0f}% > {cfg.loss_pct:.0f}%")
        if dest_reached and dest_rtt is not None and dest_rtt > cfg.latency_ms:
            breach_reasons.append(f"latency {dest_rtt:.0f}ms > {cfg.latency_ms:.0f}ms")

        with self._lock:
            if breach_reasons:
                self._consec += 1
                if self._consec >= cfg.consecutive and not self._active:
                    self._active = True
                    reason = " and ".join(breach_reasons)
                    return self._fire("alert", reason)
            else:
                if self._active:
                    self._active = False
                    self._consec = 0
                    return self._fire("recovered",
                                      "metrics back within thresholds")
                self._consec = 0
        return None

    def _fire(self, kind: str, reason: str) -> dict:
        if kind == "alert":
            title = f"PathPulse: {self.target}"
            message = f"Problem detected - {reason}"
        else:
            title = f"PathPulse: {self.target}"
            message = f"Recovered - {reason}"
        self.notifier(title, message, "")
        if self.on_event:
            self.on_event(kind, message)
        return {"kind": kind, "target": self.target, "message": message}
