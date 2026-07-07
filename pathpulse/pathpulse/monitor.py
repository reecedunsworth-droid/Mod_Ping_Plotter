"""Per-target continuous monitoring loop.

Each :class:`Monitor` owns a background thread that repeatedly:

    resolve -> traceroute pass -> update live stats -> persist to SQLite
            -> enrich hops (DNS/ASN) -> evaluate alerts -> notify subscribers

The engine's sockets are created *inside* the thread (sockets are not shared
across threads). Everything the UI needs each pass is packaged into a snapshot
dict and handed to an ``on_update`` callback, which the web server uses to push
live updates over WebSockets.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from .alerts import AlertEvaluator
from .config import AlertConfig, Config, clamp_interval
from .probe import ProbeError, Prober
from .stats import TargetStats
from .traceroute import Traceroute, resolve_target


class Monitor:
    """Monitors a single target continuously in its own thread."""

    def __init__(
        self,
        target: str,
        config: Config,
        storage,
        resolver,
        alert_config: Optional[AlertConfig] = None,
        on_update: Optional[Callable[["Monitor", dict], None]] = None,
    ):
        self.target = target
        self.config = config
        self.storage = storage
        self.resolver = resolver
        self.alert_config = alert_config or AlertConfig()
        self.on_update = on_update

        self.dest_ip: Optional[str] = None
        self.mode: Optional[str] = None
        self.status = "starting"          # starting|running|paused|error|stopped
        self.error: Optional[str] = None
        self.monitor_id: Optional[int] = None
        self.started_at = time.time()

        self._stats = TargetStats(target, "", window=config.stats_window)
        self._stats_lock = threading.Lock()
        self._version = 0
        self._ver_lock = threading.Lock()
        self._last_snapshot: Optional[dict] = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._evaluator: Optional[AlertEvaluator] = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"monitor-{self.target}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.config.pass_timeout + 2)
        self.status = "stopped"

    def pause(self) -> None:
        self._paused.set()
        self.status = "paused"
        self._publish()

    def resume(self) -> None:
        self._paused.clear()
        if self.status == "paused":
            self.status = "running"
        self._publish()

    def set_interval(self, seconds: float) -> None:
        self.config.interval_seconds = clamp_interval(seconds)
        self._publish()

    def set_alert_config(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self.alert_config, k):
                setattr(self.alert_config, k, v)
        self._publish()

    # -- the loop ---------------------------------------------------------

    def _run(self) -> None:
        try:
            self.dest_ip = resolve_target(self.target)
        except OSError as exc:
            self.status = "error"
            self.error = f"Could not resolve '{self.target}': {exc}"
            return

        with self._stats_lock:
            self._stats.dest_ip = self.dest_ip

        try:
            engine = Traceroute(
                prober=Prober(mode=self.config.socket_mode),
                max_hops=self.config.max_hops,
                probes_per_hop=self.config.probes_per_hop,
                pass_timeout=self.config.pass_timeout,
            )
        except ProbeError as exc:
            self.status = "error"
            self.error = str(exc)
            return

        self.mode = engine.mode
        self.monitor_id = self.storage.get_or_create_monitor(self.target, self.dest_ip)
        self._evaluator = AlertEvaluator(
            target=self.target,
            config=self.alert_config,
            on_event=lambda kind, msg: self.storage.log_event(
                self.monitor_id, kind, msg),
        )
        self.status = "running"

        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._stop.wait(0.2)
                    continue
                t0 = time.monotonic()
                self._one_pass(engine)
                elapsed = time.monotonic() - t0
                remaining = max(0.0, self.config.interval_seconds - elapsed)
                if self._stop.wait(remaining):
                    break
        finally:
            engine.close()

    def _one_pass(self, engine: Traceroute) -> None:
        try:
            sample = engine.trace(self.target, self.dest_ip)
        except OSError as exc:
            self.error = f"trace error: {exc}"
            return
        self.error = None

        with self._stats_lock:
            self._stats.update(sample)

        try:
            self.storage.insert_pass(self.monitor_id, sample)
        except Exception:  # noqa: BLE001 - persistence must never kill the loop
            pass

        # Destination per-pass metrics for alerting.
        dest_loss = 0.0
        if sample.hops:
            last = sample.hops[-1]
            dest_loss = last.loss_pct
        alert_event = None
        if self._evaluator:
            alert_event = self._evaluator.evaluate(
                sample.dest_rtt, dest_loss, sample.dest_reached)

        self._publish(alert_event)

    # -- snapshots --------------------------------------------------------

    def _publish(self, alert_event: Optional[dict] = None) -> dict:
        """Build a versioned snapshot, cache it, and push to subscribers.

        Called from both the monitor thread (each pass) and the request thread
        (control actions). The version stamp lets clients discard any snapshot
        that arrives out of order, so control changes never regress.
        """
        snapshot = self._build_snapshot(alert_event)
        self._last_snapshot = snapshot
        if self.on_update:
            try:
                self.on_update(self, snapshot)
            except Exception:  # noqa: BLE001
                pass
        return snapshot

    def _next_version(self) -> int:
        with self._ver_lock:
            self._version += 1
            return self._version

    def _build_snapshot(self, alert_event: Optional[dict] = None) -> dict:
        with self._stats_lock:
            base = self._stats.snapshot()
        # Enrich each hop with DNS/ASN (cached, non-blocking).
        for hop in base["hops"]:
            info = self.resolver.enrich(hop.get("address"))
            hop["hostname"] = info["hostname"]
            hop["asn"] = info["asn"]
            hop["as_name"] = info["as_name"]
            hop["private"] = info["private"]
        base.update({
            "version": self._next_version(),
            "monitor_id": self.monitor_id,
            "status": self.status,
            "mode": self.mode,
            "error": self.error,
            "interval": self.config.interval_seconds,
            "alert": self.alert_config.to_dict(),
            "alert_event": alert_event,
            "started_at": self.started_at,
        })
        return base

    def snapshot(self) -> dict:
        if self._last_snapshot is not None:
            snap = dict(self._last_snapshot)
            # Reflect live control state that may have changed since last pass.
            snap["status"] = self.status
            snap["error"] = self.error
            snap["interval"] = self.config.interval_seconds
            snap["alert"] = self.alert_config.to_dict()
            return snap
        # No pass completed yet - return a minimal placeholder.
        return {
            "target": self.target,
            "dest_ip": self.dest_ip or "",
            "version": 0,
            "status": self.status,
            "mode": self.mode,
            "error": self.error,
            "monitor_id": self.monitor_id,
            "interval": self.config.interval_seconds,
            "alert": self.alert_config.to_dict(),
            "hops": [],
            "dest_reached": False,
            "last_dest_rtt": None,
            "hop_count": 0,
        }
