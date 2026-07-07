"""Command-line entry point: ``python -m pathpulse``.

Builds a :class:`Config` from CLI flags, constructs the FastAPI app, and serves
it with uvicorn. Optionally auto-starts one or more targets on launch.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from .config import Config, clamp_interval
from .server import create_app


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pathpulse",
        description="PathPulse - continuous network path monitor (local web UI).",
    )
    p.add_argument("targets", nargs="*", help="optional targets to start monitoring")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8787, help="port (default 8787)")
    p.add_argument("--interval", type=float, default=2.5,
                   help="traceroute interval in seconds, 1-60 (default 2.5)")
    p.add_argument("--max-hops", type=int, default=30)
    p.add_argument("--probes", type=int, default=3, help="probes per hop per pass")
    p.add_argument("--timeout", type=float, default=2.0, help="per-pass timeout (s)")
    p.add_argument("--db", default="data/pathpulse.sqlite", help="SQLite path")
    p.add_argument("--retention-days", type=float, default=7.0,
                   help="prune samples older than this (default 7)")
    p.add_argument("--mode", choices=["auto", "raw", "dgram"], default="auto",
                   help="ICMP socket mode (default auto: raw if privileged)")
    p.add_argument("--no-dns", action="store_true", help="disable reverse DNS")
    p.add_argument("--no-asn", action="store_true", help="disable ASN lookups")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = Config(
        interval_seconds=clamp_interval(args.interval),
        max_hops=args.max_hops,
        probes_per_hop=args.probes,
        pass_timeout=args.timeout,
        db_path=args.db,
        retention_days=args.retention_days,
        resolve_dns=not args.no_dns,
        resolve_asn=not args.no_asn,
        socket_mode=args.mode,
        host=args.host,
        port=args.port,
    )
    app = create_app(config, initial_targets=args.targets)

    print(f"PathPulse -> http://{args.host}:{args.port}  "
          f"(interval {config.interval_seconds}s, mode {args.mode})")
    if args.targets:
        print("  starting targets:", ", ".join(args.targets))
    print("  (run with sudo for full per-hop raw-ICMP detail)")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
