"""FastAPI application: live WebSocket feed, REST control, and exports.

The monitor threads push each completed pass into a :class:`Hub`, which fans
the snapshot out to every connected browser over WebSockets. REST endpoints
cover target management, per-window time-series (served from SQLite), the alert
event log, and CSV export of raw samples. PNG export of the current graph is
done client-side from the chart canvas (see web/app.js) so no server-side
plotting dependency is required.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import TIME_WINDOWS, AlertConfig, Config, clamp_interval
from .manager import MonitorManager

WEB_DIR = Path(__file__).resolve().parent / "web"


class Hub:
    """Thread-safe bridge from monitor threads to WebSocket clients."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._queue: "asyncio.Queue[dict]" = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, message: dict) -> None:
        """Called from any thread; hands the message to the event loop."""
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, message)
        except RuntimeError:
            pass  # loop shutting down

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def run(self) -> None:
        """Drain the queue and broadcast to all clients until cancelled."""
        while True:
            message = await self._queue.get()
            dead = []
            for ws in list(self._clients):
                try:
                    await ws.send_json(message)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                self.unregister(ws)


def create_app(config: Optional[Config] = None,
               initial_targets: Optional[list] = None) -> FastAPI:
    config = config or Config()
    hub = Hub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        manager = MonitorManager(
            config,
            on_update=lambda mon, snap: hub.publish(
                {"type": "update", "target": snap["target"], "snapshot": snap}),
        )
        app.state.manager = manager
        app.state.config = config
        for target in (initial_targets or []):
            try:
                manager.add_target(target)
            except ValueError:
                pass
        broadcaster = asyncio.create_task(hub.run())
        try:
            yield
        finally:
            broadcaster.cancel()
            manager.shutdown()

    app = FastAPI(title="PathPulse", version="0.1.0", lifespan=lifespan)

    def manager_of(request: Request) -> MonitorManager:
        return request.app.state.manager

    # -- UI ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = WEB_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h1>PathPulse</h1><p>UI not built.</p>")
        return HTMLResponse(index_file.read_text())

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    # -- config -----------------------------------------------------------

    @app.get("/api/config")
    async def get_config(request: Request):
        cfg = request.app.state.config
        return {"config": cfg.to_dict(), "windows": TIME_WINDOWS}

    # -- targets ----------------------------------------------------------

    @app.get("/api/targets")
    async def list_targets(request: Request):
        return {"targets": manager_of(request).list_targets()}

    @app.get("/api/history")
    async def history(request: Request):
        """Previously-monitored targets from SQLite, so past incidents can be
        resumed after a restart."""
        mgr = manager_of(request)
        active = {t["target"] for t in mgr.list_targets()}
        mons = mgr.storage.list_monitors()
        return {"monitors": [{**m, "active": m["target"] in active} for m in mons]}

    @app.post("/api/targets")
    async def add_target(request: Request):
        body = await request.json()
        target = (body.get("target") or "").strip()
        if not target:
            raise HTTPException(400, "target is required")
        alert = None
        if isinstance(body.get("alert"), dict):
            alert = AlertConfig(**{k: v for k, v in body["alert"].items()
                                   if k in AlertConfig().to_dict()})
        try:
            monitor = manager_of(request).add_target(target, alert)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "target": target, "snapshot": monitor.snapshot()}

    @app.delete("/api/targets/{target}")
    async def remove_target(request: Request, target: str):
        ok = manager_of(request).remove_target(target)
        if not ok:
            raise HTTPException(404, "no such target")
        return {"ok": True}

    @app.get("/api/targets/{target}/snapshot")
    async def target_snapshot(request: Request, target: str):
        monitor = manager_of(request).get(target)
        if not monitor:
            raise HTTPException(404, "no such target")
        return monitor.snapshot()

    @app.post("/api/targets/{target}/control")
    async def control_target(request: Request, target: str):
        body = await request.json()
        action = body.get("action")
        mgr = manager_of(request)
        kwargs = {k: v for k, v in body.items() if k != "action"}
        if action == "set_interval":
            kwargs["seconds"] = clamp_interval(kwargs.get("seconds", 2.5))
        ok = mgr.control(target, action, **kwargs)
        if not ok:
            raise HTTPException(400, "unknown target or action")
        monitor = mgr.get(target)
        return {"ok": True, "snapshot": monitor.snapshot() if monitor else None}

    # -- time series ------------------------------------------------------

    def _window_seconds(window: str) -> float:
        return float(TIME_WINDOWS.get(window, TIME_WINDOWS["1h"]))

    @app.get("/api/targets/{target}/dest_series")
    async def dest_series(request: Request, target: str, window: str = "1h",
                          max_points: int = 600):
        monitor = manager_of(request).get(target)
        if not monitor or monitor.monitor_id is None:
            raise HTTPException(404, "no such target")
        data = manager_of(request).storage.dest_series(
            monitor.monitor_id, _window_seconds(window), max_points)
        return {"target": target, "window": window, "series": data}

    @app.get("/api/targets/{target}/hop_series")
    async def hop_series(request: Request, target: str, ttl: int,
                         window: str = "1h", max_points: int = 600):
        monitor = manager_of(request).get(target)
        if not monitor or monitor.monitor_id is None:
            raise HTTPException(404, "no such target")
        data = manager_of(request).storage.hop_series(
            monitor.monitor_id, ttl, _window_seconds(window), max_points)
        return {"target": target, "ttl": ttl, "window": window, "series": data}

    @app.get("/api/targets/{target}/events")
    async def events(request: Request, target: str, limit: int = 50):
        monitor = manager_of(request).get(target)
        if not monitor or monitor.monitor_id is None:
            raise HTTPException(404, "no such target")
        return {"events": manager_of(request).storage.recent_events(
            monitor.monitor_id, limit)}

    # -- export -----------------------------------------------------------

    @app.get("/api/targets/{target}/export.csv")
    async def export_csv(request: Request, target: str, window: Optional[str] = None):
        monitor = manager_of(request).get(target)
        if not monitor or monitor.monitor_id is None:
            raise HTTPException(404, "no such target")
        window_seconds = _window_seconds(window) if window else None
        rows = manager_of(request).storage.raw_samples(
            monitor.monitor_id, window_seconds)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp_iso", "ts_epoch", "ttl", "address",
                         "is_dest", "sent", "received", "loss_pct",
                         "rtt_best_ms", "rtt_avg_ms"])
        for r in rows:
            iso = datetime.fromtimestamp(r["ts"], timezone.utc).isoformat()
            loss = 0.0 if r["sent"] == 0 else round(
                100.0 * (r["sent"] - r["received"]) / r["sent"], 2)
            writer.writerow([iso, f"{r['ts']:.3f}", r["ttl"], r["address"] or "",
                             r["is_dest"], r["sent"], r["received"], loss,
                             r["rtt_best"] if r["rtt_best"] is not None else "",
                             r["rtt_avg"] if r["rtt_avg"] is not None else ""])
        safe = target.replace("/", "_").replace(":", "_")
        fname = f"pathpulse_{safe}_{int(time.time())}.csv"
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # -- websocket --------------------------------------------------------

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await hub.register(ws)
        try:
            # Send the current full state immediately on connect.
            manager = ws.app.state.manager
            await ws.send_json({"type": "init", "targets": manager.list_targets()})
            while True:
                # We do not expect client messages, but keep the socket alive
                # and detect disconnects.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            hub.unregister(ws)

    return app


app = create_app()
