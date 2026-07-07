"""Traceroute engine: discover the path to a target and time every hop.

Rather than the classic "one hop at a time" walk, PathPulse fires all probes
for a pass up front (every TTL x every probe), then collects responses within
a single time window. That keeps a full-path sweep fast enough to repeat every
couple of seconds, which is what a continuous monitor needs.
"""

from __future__ import annotations

import socket
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from .probe import Prober, ProbeResult


@dataclass
class HopSample:
    """One hop's result within a single traceroute pass."""

    ttl: int
    address: Optional[str] = None   # representative responder IP (most common)
    addresses: List[str] = field(default_factory=list)  # all responders seen
    rtts: List[Optional[float]] = field(default_factory=list)  # per-probe, None=loss
    reached_dest: bool = False

    @property
    def sent(self) -> int:
        return len(self.rtts)

    @property
    def received(self) -> int:
        return sum(1 for r in self.rtts if r is not None)

    @property
    def loss_pct(self) -> float:
        return 0.0 if not self.rtts else 100.0 * (self.sent - self.received) / self.sent

    @property
    def best_rtt(self) -> Optional[float]:
        vals = [r for r in self.rtts if r is not None]
        return min(vals) if vals else None


@dataclass
class TraceSample:
    """A complete traceroute pass to one target at one instant."""

    target: str
    dest_ip: str
    timestamp: float
    hops: List[HopSample]
    dest_reached: bool

    @property
    def dest_rtt(self) -> Optional[float]:
        """Best RTT of the destination, or None if it was not reached this pass."""
        if not self.dest_reached or not self.hops:
            return None
        return self.hops[-1].best_rtt


def resolve_target(target: str) -> str:
    """Resolve a hostname/IP to an IPv4 address (raises socket.gaierror)."""
    return socket.gethostbyname(target)


class Traceroute:
    """Runs traceroute passes for a single target using one :class:`Prober`."""

    def __init__(
        self,
        prober: Optional[Prober] = None,
        max_hops: int = 30,
        probes_per_hop: int = 3,
        pass_timeout: float = 2.0,
    ):
        self.prober = prober or Prober()
        self.max_hops = max_hops
        self.probes_per_hop = probes_per_hop
        self.pass_timeout = pass_timeout

    @property
    def mode(self) -> str:
        return self.prober.mode

    def close(self) -> None:
        self.prober.close()

    def trace(self, target: str, dest_ip: Optional[str] = None) -> TraceSample:
        """Run one full traceroute pass and return a :class:`TraceSample`."""
        dest_ip = dest_ip or resolve_target(target)
        timestamp = time.time()

        # Fire every probe (ttl x probe index) and remember which seq is which.
        seq_to_ttl: dict[int, int] = {}
        for ttl in range(1, self.max_hops + 1):
            for _ in range(self.probes_per_hop):
                try:
                    seq = self.prober.send(dest_ip, ttl)
                    seq_to_ttl[seq] = ttl
                except OSError:
                    continue

        # Accumulator per ttl.
        acc: dict[int, HopSample] = {
            ttl: HopSample(ttl=ttl) for ttl in range(1, self.max_hops + 1)
        }
        for hop in acc.values():
            hop.rtts = [None] * self.probes_per_hop
        filled: dict[int, int] = {ttl: 0 for ttl in acc}

        deadline = time.monotonic() + self.pass_timeout
        outstanding = len(seq_to_ttl)
        while outstanding > 0:
            result: Optional[ProbeResult] = self.prober.receive(dest_ip, deadline)
            if result is None:
                break
            ttl = seq_to_ttl.get(result.seq)
            if ttl is None:
                continue
            hop = acc[ttl]
            idx = filled[ttl]
            if idx < len(hop.rtts):
                hop.rtts[idx] = result.rtt_ms
                filled[ttl] = idx + 1
            if result.address:
                hop.addresses.append(result.address)
            if result.reached_dest:
                hop.reached_dest = True
            outstanding -= 1

        # Choose a representative address per hop (most frequent responder).
        for hop in acc.values():
            if hop.addresses:
                hop.address = Counter(hop.addresses).most_common(1)[0][0]

        # Determine where the path ends: first TTL that reached the destination.
        dest_ttl = None
        for ttl in sorted(acc):
            if acc[ttl].reached_dest:
                dest_ttl = ttl
                break

        if dest_ttl is not None:
            end_ttl = dest_ttl
            dest_reached = True
        else:
            # Never reached dest this pass: keep up to the last hop that answered.
            responding = [ttl for ttl, h in acc.items() if h.address]
            end_ttl = max(responding) if responding else self.max_hops
            dest_reached = False

        hops = [acc[ttl] for ttl in range(1, end_ttl + 1)]
        return TraceSample(
            target=target,
            dest_ip=dest_ip,
            timestamp=timestamp,
            hops=hops,
            dest_reached=dest_reached,
        )
