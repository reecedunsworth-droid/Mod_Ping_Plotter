"""Configuration defaults and clamping for PathPulse."""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Interval bounds from the project brief.
MIN_INTERVAL = 1.0
MAX_INTERVAL = 60.0
DEFAULT_INTERVAL = 2.5


def clamp_interval(value: float) -> float:
    return max(MIN_INTERVAL, min(MAX_INTERVAL, float(value)))


# Selectable graph windows (label -> seconds).
TIME_WINDOWS = {
    "10m": 10 * 60,
    "1h": 60 * 60,
    "6h": 6 * 60 * 60,
    "24h": 24 * 60 * 60,
}


@dataclass
class AlertConfig:
    """Threshold-based alerting for a target's destination."""

    enabled: bool = False
    latency_ms: float = 150.0     # fire if dest latency exceeds this...
    loss_pct: float = 5.0         # ...or loss exceeds this...
    consecutive: int = 3          # ...for this many consecutive passes.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Config:
    """Global runtime configuration."""

    interval_seconds: float = DEFAULT_INTERVAL
    max_hops: int = 30
    probes_per_hop: int = 3
    pass_timeout: float = 2.0
    stats_window: int = 200
    db_path: str = "data/pathpulse.sqlite"
    retention_days: float = 7.0
    resolve_dns: bool = True
    resolve_asn: bool = True
    socket_mode: str = "auto"     # auto | raw | dgram
    host: str = "127.0.0.1"
    port: int = 8787

    def __post_init__(self):
        self.interval_seconds = clamp_interval(self.interval_seconds)

    def to_dict(self) -> dict:
        return asdict(self)
