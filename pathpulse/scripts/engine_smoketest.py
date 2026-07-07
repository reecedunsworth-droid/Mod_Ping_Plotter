#!/usr/bin/env python3
"""Standalone smoke test for the PathPulse traceroute engine.

Run the engine directly, with no web UI, and print a traceroute-style table.
This is how we validate the ICMP engine in isolation (the project brief asks
for the engine to be tested against 8.8.8.8 before any UI is built).

Usage:
    sudo python3 scripts/engine_smoketest.py 8.8.8.8
    sudo python3 scripts/engine_smoketest.py --passes 3 --interval 1 example.com
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathpulse.probe import ProbeError, Prober  # noqa: E402
from pathpulse.traceroute import Traceroute, resolve_target  # noqa: E402


def fmt(v):
    return "   *  " if v is None else f"{v:6.1f}"


def print_pass(sample) -> None:
    print(
        f"\ntraceroute to {sample.target} ({sample.dest_ip})  "
        f"[{'reached' if sample.dest_reached else 'INCOMPLETE'}]  "
        f"dest_rtt={fmt(sample.dest_rtt)} ms"
    )
    print(f"{'hop':>3}  {'address':<18} {'loss%':>6}  probes (ms)")
    for hop in sample.hops:
        probes = " ".join(fmt(r) for r in hop.rtts)
        addr = hop.address or "*"
        flag = "  <=dest" if hop.reached_dest else ""
        print(f"{hop.ttl:>3}  {addr:<18} {hop.loss_pct:6.0f}  {probes}{flag}")


def main() -> int:
    ap = argparse.ArgumentParser(description="PathPulse engine smoke test")
    ap.add_argument("target", help="hostname or IP to trace")
    ap.add_argument("--passes", type=int, default=1, help="number of passes")
    ap.add_argument("--interval", type=float, default=2.5, help="seconds between passes")
    ap.add_argument("--max-hops", type=int, default=30)
    ap.add_argument("--probes", type=int, default=3, help="probes per hop")
    ap.add_argument("--timeout", type=float, default=2.0, help="pass timeout (s)")
    ap.add_argument("--mode", choices=["auto", "raw", "dgram"], default="auto")
    args = ap.parse_args()

    try:
        dest_ip = resolve_target(args.target)
    except OSError as exc:
        print(f"Could not resolve {args.target}: {exc}", file=sys.stderr)
        return 2

    try:
        prober = Prober(mode=args.mode)
    except ProbeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Socket mode: {prober.mode}  (raw = full per-hop detail; "
          f"dgram = unprivileged, hops best-effort)")

    engine = Traceroute(
        prober=prober,
        max_hops=args.max_hops,
        probes_per_hop=args.probes,
        pass_timeout=args.timeout,
    )
    try:
        for i in range(args.passes):
            t0 = time.time()
            sample = engine.trace(args.target, dest_ip)
            print_pass(sample)
            if i < args.passes - 1:
                time.sleep(max(0, args.interval - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
