"""Socket-level probing: send TTL-limited ICMP echoes, receive responses.

This module owns the *privilege / capability* logic. On macOS a raw ICMP
socket (``SOCK_RAW``) needs root, so PathPulse degrades gracefully:

* ``raw``   - ``SOCK_RAW`` + ``IPPROTO_ICMP``: full traceroute, needs root
              (run with ``sudo``). This is the default when available.
* ``dgram`` - ``SOCK_DGRAM`` + ``IPPROTO_ICMP``: unprivileged ICMP on macOS
              and Linux. Destination latency always works; visibility of
              intermediate Time-Exceeded hops is best-effort (OS dependent).

The active mode is reported so the UI/README can tell the user whether they
are getting full per-hop detail or just end-to-end latency.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from dataclasses import dataclass
from typing import Dict, Optional

from . import icmp

# A monotonically increasing per-process counter so concurrent Probers get
# distinct ICMP identifiers and never confuse each other's replies.
_ident_counter = 0


def _next_ident() -> int:
    global _ident_counter
    _ident_counter = (_ident_counter + 1) & 0xFFFF
    return ((os.getpid() & 0xFF) << 8 | _ident_counter & 0xFF) & 0xFFFF


class ProbeError(RuntimeError):
    """Raised when no usable probing socket can be created."""


@dataclass
class ProbeResult:
    """Outcome of a single probe (one packet at one TTL)."""

    ttl: int
    seq: int
    address: Optional[str]        # responder IP, or None on timeout
    rtt_ms: Optional[float]       # round-trip time, or None on timeout
    reached_dest: bool = False    # responder is the final destination
    time_exceeded: bool = False   # intermediate hop
    unreachable: bool = False     # dest/port/net unreachable
    timed_out: bool = False


def _select_mode(prefer: str = "auto") -> str:
    """Pick a socket mode by probing what the OS actually allows.

    Returns ``"raw"`` or ``"dgram"``. Raises :class:`ProbeError` if neither
    can be opened.
    """
    candidates = {
        "raw": (socket.SOCK_RAW,),
        "dgram": (socket.SOCK_DGRAM,),
    }
    if prefer in candidates:
        order = [prefer] + [m for m in ("raw", "dgram") if m != prefer]
    else:
        order = ["raw", "dgram"]

    last_err: Optional[Exception] = None
    for mode in order:
        try:
            s = socket.socket(socket.AF_INET, candidates[mode][0], socket.IPPROTO_ICMP)
            s.close()
            return mode
        except (PermissionError, OSError) as exc:
            last_err = exc
    raise ProbeError(
        "Could not open an ICMP socket in raw or dgram mode. "
        "On macOS run PathPulse with sudo for raw sockets. "
        f"Last error: {last_err}"
    )


class Prober:
    """Sends ICMP echo probes and correlates responses.

    A single ``Prober`` owns one socket and one ICMP identifier. It is *not*
    thread-safe; create one per monitoring thread.
    """

    def __init__(self, mode: str = "auto", payload_size: int = 24):
        self.mode = _select_mode(mode)
        self.ident = _next_ident()
        self._seq = 0
        self._payload = b"pathpulse" + b"\x00" * max(0, payload_size - 9)
        self._pending: Dict[int, float] = {}  # seq -> send timestamp

        sock_type = socket.SOCK_RAW if self.mode == "raw" else socket.SOCK_DGRAM
        self._sock = socket.socket(socket.AF_INET, sock_type, socket.IPPROTO_ICMP)
        self._sock.setblocking(False)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "Prober":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def send(self, dest_ip: str, ttl: int) -> int:
        """Send one echo request toward ``dest_ip`` with the given ``ttl``.

        Returns the sequence number identifying this probe. Does not wait for
        a reply - use :meth:`receive` to collect responses.
        """
        seq = self._next_seq()
        # In dgram mode macOS overwrites the ICMP id with the socket's port,
        # so we must not rely on ident matching there - seq alone disambiguates.
        ident = self.ident
        packet = icmp.build_echo_request(ident, seq, self._payload)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
        self._pending[seq] = time.monotonic()
        try:
            self._sock.sendto(packet, (dest_ip, 0))
        except OSError:
            # Send failed (e.g. transient buffer) - treat as no probe in flight.
            self._pending.pop(seq, None)
            raise
        return seq

    def receive(self, dest_ip: str, deadline: float) -> Optional[ProbeResult]:
        """Wait until ``deadline`` (monotonic) for one matching response.

        Returns a :class:`ProbeResult` for the first response matching one of
        our pending probes, or ``None`` if the deadline passes with nothing.
        """
        import select

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            rlist, _, _ = select.select([self._sock], [], [], remaining)
            if not rlist:
                return None
            try:
                packet, addr = self._sock.recvfrom(1500)
            except OSError:
                continue

            parsed = icmp.parse_response(packet)
            if parsed is None:
                continue
            if parsed.seq is None or parsed.seq not in self._pending:
                # Not one of our in-flight probes (another process, stale, or
                # - in raw mode - an id mismatch we can safely ignore).
                if self.mode == "raw" and parsed.ident is not None \
                        and parsed.ident != self.ident:
                    continue
                if parsed.seq not in self._pending:
                    continue

            seq = parsed.seq
            sent_at = self._pending.pop(seq)
            rtt_ms = (time.monotonic() - sent_at) * 1000.0
            responder = addr[0] if isinstance(addr, tuple) else str(addr)
            reached = parsed.is_reply or (responder == dest_ip)
            return ProbeResult(
                ttl=-1,  # filled in by caller which knows the ttl for this seq
                seq=seq,
                address=responder,
                rtt_ms=rtt_ms,
                reached_dest=reached,
                time_exceeded=parsed.is_time_exceeded,
                unreachable=parsed.is_unreachable,
            )

    def probe_once(self, dest_ip: str, ttl: int, timeout: float = 1.0) -> ProbeResult:
        """Convenience: send one probe at ``ttl`` and wait up to ``timeout``s."""
        seq = self.send(dest_ip, ttl)
        deadline = time.monotonic() + timeout
        while True:
            result = self.receive(dest_ip, deadline)
            if result is None:
                self._pending.pop(seq, None)
                return ProbeResult(ttl=ttl, seq=seq, address=None, rtt_ms=None,
                                   timed_out=True)
            if result.seq == seq:
                result.ttl = ttl
                return result
            # A stale/other response - keep waiting until our seq or deadline.
