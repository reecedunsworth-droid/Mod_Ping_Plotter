"""SQLite persistence for monitoring history.

Design notes:

* One row per hop per traceroute pass (the "raw sample" - also what CSV export
  dumps). Summary stats (min/avg/max/jitter/loss) are derived, not stored.
* WAL mode + a single shared connection guarded by a lock keeps things safe
  across the monitor threads and the async web server without a per-request
  connection pool.
* Time-series queries downsample by averaging into N time buckets so a 24h
  window does not ship tens of thousands of points to the browser.
* Automatic pruning removes samples older than the retention window
  (default 7 days, configurable).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    target     TEXT NOT NULL,
    dest_ip    TEXT,
    created_at REAL NOT NULL,
    UNIQUE(target)
);

CREATE TABLE IF NOT EXISTS samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    ts         REAL NOT NULL,
    ttl        INTEGER NOT NULL,
    address    TEXT,
    is_dest    INTEGER NOT NULL DEFAULT 0,
    sent       INTEGER NOT NULL,
    received   INTEGER NOT NULL,
    rtt_best   REAL,
    rtt_avg    REAL
);

CREATE INDEX IF NOT EXISTS idx_samples_monitor_ts
    ON samples(monitor_id, ts);
CREATE INDEX IF NOT EXISTS idx_samples_monitor_ttl_ts
    ON samples(monitor_id, ttl, ts);
CREATE INDEX IF NOT EXISTS idx_samples_dest
    ON samples(monitor_id, is_dest, ts);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    ts         REAL NOT NULL,
    kind       TEXT NOT NULL,
    message    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_monitor_ts
    ON events(monitor_id, ts);
"""


class Storage:
    """Thread-safe SQLite store for PathPulse monitoring history."""

    def __init__(self, path: str = "pathpulse.sqlite", retention_days: float = 7.0):
        self.path = str(path)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- monitors ---------------------------------------------------------

    def get_or_create_monitor(self, target: str, dest_ip: Optional[str]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM monitors WHERE target = ?", (target,)
            )
            row = cur.fetchone()
            if row:
                if dest_ip:
                    self._conn.execute(
                        "UPDATE monitors SET dest_ip = ? WHERE id = ?",
                        (dest_ip, row["id"]),
                    )
                    self._conn.commit()
                return row["id"]
            cur = self._conn.execute(
                "INSERT INTO monitors(target, dest_ip, created_at) VALUES (?,?,?)",
                (target, dest_ip, time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_monitors(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, target, dest_ip, created_at FROM monitors ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_monitor(self, monitor_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM samples WHERE monitor_id = ?", (monitor_id,))
            self._conn.execute("DELETE FROM events WHERE monitor_id = ?", (monitor_id,))
            self._conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
            self._conn.commit()

    # -- samples ----------------------------------------------------------

    def insert_pass(self, monitor_id: int, sample) -> None:
        """Persist every hop of one :class:`traceroute.TraceSample`."""
        rows = []
        n = len(sample.hops)
        for i, hop in enumerate(sample.hops):
            received = hop.received
            valid = [r for r in hop.rtts if r is not None]
            rtt_avg = sum(valid) / len(valid) if valid else None
            is_dest = 1 if (hop.reached_dest or i == n - 1 and sample.dest_reached) else 0
            rows.append((
                monitor_id, sample.timestamp, hop.ttl, hop.address, is_dest,
                hop.sent, received, hop.best_rtt, rtt_avg,
            ))
        with self._lock:
            self._conn.executemany(
                "INSERT INTO samples(monitor_id, ts, ttl, address, is_dest, "
                "sent, received, rtt_best, rtt_avg) VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()

    def _series(self, where: str, params: tuple, since: float,
                until: float, max_points: int) -> List[dict]:
        """Downsample matching rows into <= max_points averaged buckets."""
        bucket = max((until - since) / max(1, max_points), 0.001)
        sql = (
            "SELECT CAST((ts - ?) / ? AS INTEGER) AS b, "
            "       AVG(ts) AS ts, "
            "       AVG(rtt_best) AS rtt, "
            "       100.0 * (SUM(sent) - SUM(received)) / SUM(sent) AS loss, "
            "       MIN(rtt_best) AS rtt_min, MAX(rtt_best) AS rtt_max "
            "FROM samples "
            f"WHERE {where} AND ts >= ? AND ts <= ? "
            "GROUP BY b ORDER BY b"
        )
        with self._lock:
            rows = self._conn.execute(
                sql, (since, bucket, *params, since, until)
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "ts": r["ts"],
                "rtt": None if r["rtt"] is None else round(r["rtt"], 2),
                "loss": round(r["loss"], 2) if r["loss"] is not None else 0.0,
                "rtt_min": None if r["rtt_min"] is None else round(r["rtt_min"], 2),
                "rtt_max": None if r["rtt_max"] is None else round(r["rtt_max"], 2),
            })
        return out

    def dest_series(self, monitor_id: int, window_seconds: float,
                    max_points: int = 600) -> List[dict]:
        until = time.time()
        since = until - window_seconds
        return self._series(
            "monitor_id = ? AND is_dest = 1", (monitor_id,), since, until, max_points
        )

    def hop_series(self, monitor_id: int, ttl: int, window_seconds: float,
                   max_points: int = 600) -> List[dict]:
        until = time.time()
        since = until - window_seconds
        return self._series(
            "monitor_id = ? AND ttl = ?", (monitor_id, ttl), since, until, max_points
        )

    def raw_samples(self, monitor_id: int, window_seconds: Optional[float] = None):
        """Yield raw rows (for CSV export), newest window first, ordered by time."""
        with self._lock:
            if window_seconds is None:
                rows = self._conn.execute(
                    "SELECT ts, ttl, address, is_dest, sent, received, rtt_best, "
                    "rtt_avg FROM samples WHERE monitor_id = ? ORDER BY ts, ttl",
                    (monitor_id,),
                ).fetchall()
            else:
                since = time.time() - window_seconds
                rows = self._conn.execute(
                    "SELECT ts, ttl, address, is_dest, sent, received, rtt_best, "
                    "rtt_avg FROM samples WHERE monitor_id = ? AND ts >= ? "
                    "ORDER BY ts, ttl",
                    (monitor_id, since),
                ).fetchall()
        return [dict(r) for r in rows]

    # -- events (alert log) ----------------------------------------------

    def log_event(self, monitor_id: int, kind: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(monitor_id, ts, kind, message) VALUES (?,?,?,?)",
                (monitor_id, time.time(), kind, message),
            )
            self._conn.commit()

    def recent_events(self, monitor_id: int, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, message FROM events WHERE monitor_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (monitor_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- maintenance ------------------------------------------------------

    def prune(self, retention_days: Optional[float] = None) -> int:
        """Delete samples/events older than the retention window. Returns rows removed."""
        days = self.retention_days if retention_days is None else retention_days
        cutoff = time.time() - days * 86400
        with self._lock:
            c1 = self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,)).rowcount
            c2 = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
            self._conn.commit()
        return c1 + c2
