"""Low-level ICMP packet construction and parsing.

Everything here is pure byte manipulation over the stdlib ``struct`` module -
no sockets, no I/O - so it can be unit-tested without any privileges or
network access.

Reference: ICMP echo (RFC 792). We build echo requests (type 8) and parse the
three response types relevant to traceroute:

* Echo Reply (type 0)        - the destination answered our probe.
* Time Exceeded (type 11)    - an intermediate router decremented TTL to zero.
* Destination Unreachable(3) - a host/port/net was unreachable (still locates a hop).

For Time Exceeded / Destination Unreachable the ICMP payload embeds the IP
header + first 8 bytes of the *original* datagram we sent, which for an ICMP
echo is exactly ``type,code,checksum,id,seq``. That lets us recover the ``id``
and ``seq`` of the probe that triggered the error and match it to a pending
probe.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

# ICMP message types we care about.
ICMP_ECHO_REPLY = 0
ICMP_DEST_UNREACH = 3
ICMP_ECHO_REQUEST = 8
ICMP_TIME_EXCEEDED = 11

ICMP_HEADER_STRUCT = struct.Struct("!BBHHH")  # type, code, checksum, id, seq


def checksum(data: bytes) -> int:
    """Compute the 16-bit one's-complement Internet checksum (RFC 1071)."""
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def build_echo_request(ident: int, seq: int, payload: bytes = b"pathpulse") -> bytes:
    """Return a complete ICMP echo request datagram with a valid checksum."""
    ident &= 0xFFFF
    seq &= 0xFFFF
    header = ICMP_HEADER_STRUCT.pack(ICMP_ECHO_REQUEST, 0, 0, ident, seq)
    chk = checksum(header + payload)
    header = ICMP_HEADER_STRUCT.pack(ICMP_ECHO_REQUEST, 0, chk, ident, seq)
    return header + payload


def _ip_header_length(ip_packet: bytes) -> int:
    """Length in bytes of an IPv4 header (IHL field * 4)."""
    return (ip_packet[0] & 0x0F) * 4


@dataclass
class ParsedResponse:
    """A decoded ICMP response relevant to traceroute.

    ``ident`` / ``seq`` always refer to the *original probe* that this response
    corresponds to (for errors they are recovered from the embedded datagram),
    so the caller can match a response back to the probe it sent.
    """

    icmp_type: int
    icmp_code: int
    ident: Optional[int]
    seq: Optional[int]
    is_reply: bool          # destination answered (echo reply)
    is_time_exceeded: bool  # intermediate hop (TTL expired)
    is_unreachable: bool    # destination/port/net unreachable


def parse_response(ip_packet: bytes) -> Optional[ParsedResponse]:
    """Parse a raw IPv4 packet received on an ICMP socket.

    Raw ICMP sockets deliver the full IP datagram (IP header + ICMP message).
    Returns ``None`` for anything we do not recognise as a traceroute-relevant
    response.
    """
    if len(ip_packet) < 20:
        return None
    ihl = _ip_header_length(ip_packet)
    if len(ip_packet) < ihl + 8:
        return None

    icmp = ip_packet[ihl:]
    icmp_type, icmp_code, _chk, ident, seq = ICMP_HEADER_STRUCT.unpack(icmp[:8])

    if icmp_type == ICMP_ECHO_REPLY:
        return ParsedResponse(
            icmp_type, icmp_code, ident, seq,
            is_reply=True, is_time_exceeded=False, is_unreachable=False,
        )

    if icmp_type in (ICMP_TIME_EXCEEDED, ICMP_DEST_UNREACH):
        # ICMP error: payload after the 8-byte ICMP header is the original
        # IP header + first 8 bytes of original datagram (our echo header).
        inner = icmp[8:]
        if len(inner) < 20:
            return None
        inner_ihl = _ip_header_length(inner)
        orig = inner[inner_ihl:]
        orig_id = orig_seq = None
        if len(orig) >= 8:
            _t, _c, _k, orig_id, orig_seq = ICMP_HEADER_STRUCT.unpack(orig[:8])
        return ParsedResponse(
            icmp_type, icmp_code, orig_id, orig_seq,
            is_reply=False,
            is_time_exceeded=(icmp_type == ICMP_TIME_EXCEEDED),
            is_unreachable=(icmp_type == ICMP_DEST_UNREACH),
        )

    return None
