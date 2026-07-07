"""Manage many concurrent monitors - one per target tab.

Owns the shared :class:`Storage` and :class:`Resolver`, spawns a background
pruning thread that enforces the retention window, and exposes add/remove/list
plus per-target control (pause/resume/interval/alerts) to the web layer.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

from .config import AlertConfig, Config
from .monitor import Monitor
from .resolver import Resolver
from .storage import Storage


class MonitorManager:
    """Registry and lifecycle owner for all active monitors."""

    def __init__(self, config: Config,
                 on_update: Optional[Callable[[Monitor, dict], None]] = None):
        self.config = config
        self.on_update = on_update
        self.storage = Storage(config.db_path, retention_days=config.retention_days)
        self.resolver = Resolver(resolve_dns=config.resolve_dns,
                                 resolve_asn=config.resolve_asn)
        self._monitors: Dict[str, Monitor] = {}
        self._lock = threading.Lock()
        self._prune_stop = threading.Event()
        self._prune_thread = threading.Thread(
            target=self._prune_loop, name="pruner", daemon=True)
        self._prune_thread.start()

    # -- monitor lifecycle -----------------------------------------------

    def add_target(self, target: str,
                   alert_config: Optional[AlertConfig] = None) -> Monitor:
        target = target.strip()
        if not target:
            raise ValueError("target must not be empty")
        with self._lock:
            existing = self._monitors.get(target)
            if existing:
                return existing
            monitor = Monitor(
                target=target,
                config=Config(**{**self.config.to_dict()}),  # per-monitor copy
                storage=self.storage,
                resolver=self.resolver,
                alert_config=alert_config or AlertConfig(),
                on_update=self.on_update,
            )
            self._monitors[target] = monitor
        monitor.start()
        return monitor

    def remove_target(self, target: str) -> bool:
        with self._lock:
            monitor = self._monitors.pop(target, None)
        if monitor:
            monitor.stop()
            return True
        return False

    def get(self, target: str) -> Optional[Monitor]:
        with self._lock:
            return self._monitors.get(target)

    def list_targets(self) -> List[dict]:
        with self._lock:
            monitors = list(self._monitors.values())
        return [m.snapshot() for m in monitors]

    def snapshot_all(self) -> Dict[str, dict]:
        with self._lock:
            monitors = dict(self._monitors)
        return {t: m.snapshot() for t, m in monitors.items()}

    # -- control ----------------------------------------------------------

    def control(self, target: str, action: str, **kwargs) -> bool:
        monitor = self.get(target)
        if not monitor:
            return False
        if action == "pause":
            monitor.pause()
        elif action == "resume":
            monitor.resume()
        elif action == "set_interval":
            monitor.set_interval(kwargs["seconds"])
        elif action == "set_alert":
            monitor.set_alert_config(**kwargs)
        else:
            return False
        return True

    # -- retention pruning -----------------------------------------------

    def _prune_loop(self) -> None:
        # Prune shortly after start, then hourly.
        while not self._prune_stop.wait(5):
            try:
                self.storage.prune()
            except Exception:  # noqa: BLE001
                pass
            if self._prune_stop.wait(3600):
                break

    # -- shutdown ---------------------------------------------------------

    def shutdown(self) -> None:
        self._prune_stop.set()
        with self._lock:
            monitors = list(self._monitors.values())
            self._monitors.clear()
        for m in monitors:
            m.stop()
        self.resolver.close()
        self.storage.close()
