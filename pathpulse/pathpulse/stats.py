"""Rolling per-hop statistics computed over a sliding window.

These are the *live summary* numbers shown next to each hop (min / avg / max /
current latency, packet-loss %, jitter). They are kept in memory over a bounded
window so cost stays constant regardless of how long a monitor has been running;
full history for the time-series graphs lives in SQLite (see storage.py).

Loss colour thresholds match the project brief:
    green  < 1%,  yellow 1-5%,  red > 5%.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

LOSS_GREEN = 1.0
LOSS_YELLOW = 5.0


def loss_color(loss_pct: float) -> str:
    """Map a packet-loss percentage to a status colour name."""
    if loss_pct > LOSS_YELLOW:
        return "red"
    if loss_pct >= LOSS_GREEN:
        return "yellow"
    return "green"


@dataclass
class HopStats:
    """Sliding-window statistics for a single hop (one TTL)."""

    ttl: int
    window: int = 200                      # number of probes retained
    address: Optional[str] = None
    _rtts: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    _received_flags: Deque[bool] = field(default_factory=lambda: deque(maxlen=200))
    _addresses: Deque[str] = field(default_factory=lambda: deque(maxlen=200))
    current: Optional[float] = None
    reached_dest: bool = False
    total_sent: int = 0
    total_received: int = 0

    def __post_init__(self):
        # Honour a custom window size on the bounded deques.
        if self.window != 200:
            self._rtts = deque(self._rtts, maxlen=self.window)
            self._received_flags = deque(self._received_flags, maxlen=self.window)
            self._addresses = deque(self._addresses, maxlen=self.window)

    def add_pass(self, rtts: List[Optional[float]], address: Optional[str],
                 reached_dest: bool = False) -> None:
        """Fold one traceroute pass's probes for this hop into the window."""
        last_valid = None
        for r in rtts:
            self.total_sent += 1
            received = r is not None
            self._received_flags.append(received)
            if received:
                self.total_received += 1
                self._rtts.append(r)
                last_valid = r
        if address:
            self.address = address
            self._addresses.append(address)
        if reached_dest:
            self.reached_dest = True
        # 'current' reflects the most recent successful probe this pass;
        # if the whole pass was lost, current becomes None.
        self.current = last_valid if any(r is not None for r in rtts) else None

    @property
    def min(self) -> Optional[float]:
        return min(self._rtts) if self._rtts else None

    @property
    def max(self) -> Optional[float]:
        return max(self._rtts) if self._rtts else None

    @property
    def avg(self) -> Optional[float]:
        return sum(self._rtts) / len(self._rtts) if self._rtts else None

    @property
    def jitter(self) -> Optional[float]:
        """Mean absolute difference between consecutive RTTs in the window."""
        if len(self._rtts) < 2:
            return None
        rtts = list(self._rtts)
        deltas = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
        return sum(deltas) / len(deltas)

    @property
    def loss_pct(self) -> float:
        """Packet loss over the window (0 if nothing sent yet)."""
        n = len(self._received_flags)
        if n == 0:
            return 0.0
        lost = n - sum(self._received_flags)
        return 100.0 * lost / n

    @property
    def color(self) -> str:
        return loss_color(self.loss_pct)

    def snapshot(self) -> dict:
        """Serializable summary for the API/UI."""
        return {
            "ttl": self.ttl,
            "address": self.address,
            "current": _round(self.current),
            "min": _round(self.min),
            "avg": _round(self.avg),
            "max": _round(self.max),
            "jitter": _round(self.jitter),
            "loss_pct": round(self.loss_pct, 2),
            "color": self.color,
            "reached_dest": self.reached_dest,
            "sent": self.total_sent,
            "received": self.total_received,
        }


def _round(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 2)


@dataclass
class TargetStats:
    """Aggregate live stats for one monitored target across all its hops."""

    target: str
    dest_ip: str
    window: int = 200
    hops: dict = field(default_factory=dict)  # ttl -> HopStats
    last_dest_rtt: Optional[float] = None
    last_pass_ts: Optional[float] = None
    dest_reached: bool = False
    hop_count: int = 0

    def update(self, sample) -> None:
        """Fold a :class:`traceroute.TraceSample` into the live stats."""
        self.last_pass_ts = sample.timestamp
        self.dest_reached = sample.dest_reached
        self.hop_count = len(sample.hops)
        for hop in sample.hops:
            hs = self.hops.get(hop.ttl)
            if hs is None:
                hs = HopStats(ttl=hop.ttl, window=self.window)
                self.hops[hop.ttl] = hs
            hs.add_pass(hop.rtts, hop.address, hop.reached_dest)
        self.last_dest_rtt = sample.dest_rtt

    def snapshot(self) -> dict:
        ordered = [self.hops[t].snapshot() for t in sorted(self.hops)]
        return {
            "target": self.target,
            "dest_ip": self.dest_ip,
            "dest_reached": self.dest_reached,
            "last_dest_rtt": _round(self.last_dest_rtt),
            "last_pass_ts": self.last_pass_ts,
            "hop_count": self.hop_count,
            "hops": ordered,
        }
