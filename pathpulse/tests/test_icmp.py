"""Unit tests for ICMP packet build/parse - no sockets, no privileges."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pathpulse import icmp


def test_checksum_known_value():
    # Checksum of an all-zero header must invert the summed data to 0xFFFF.
    assert icmp.checksum(b"\x00\x00\x00\x00") == 0xFFFF


def test_echo_request_roundtrip_checksum_valid():
    pkt = icmp.build_echo_request(0x1234, 0x5678, b"payload")
    # A valid ICMP message checksums to zero when recomputed over itself.
    assert icmp.checksum(pkt) == 0
    typ, code, _chk, ident, seq = icmp.ICMP_HEADER_STRUCT.unpack(pkt[:8])
    assert typ == icmp.ICMP_ECHO_REQUEST
    assert code == 0
    assert ident == 0x1234
    assert seq == 0x5678


def _ip_wrap(payload: bytes, proto: int = 1) -> bytes:
    """Wrap an ICMP payload in a minimal 20-byte IPv4 header."""
    ver_ihl = (4 << 4) | 5
    return struct.pack("!BBHHHBBH4s4s", ver_ihl, 0, 20 + len(payload),
                       0, 0, 64, proto, 0, b"\x08\x08\x08\x08",
                       b"\x0a\x00\x00\x01") + payload


def test_parse_echo_reply():
    reply = icmp.ICMP_HEADER_STRUCT.pack(icmp.ICMP_ECHO_REPLY, 0, 0, 0xABCD, 42)
    parsed = icmp.parse_response(_ip_wrap(reply))
    assert parsed is not None
    assert parsed.is_reply
    assert parsed.ident == 0xABCD
    assert parsed.seq == 42


def test_parse_time_exceeded_recovers_original_seq():
    # Build the original echo request we "sent"...
    original = icmp.build_echo_request(0x1111, 777, b"pathpulse")
    inner_ip = _ip_wrap(original)  # original IP datagram
    # ...then a Time Exceeded whose body is that datagram (IP hdr + 8 bytes).
    te_header = icmp.ICMP_HEADER_STRUCT.pack(icmp.ICMP_TIME_EXCEEDED, 0, 0, 0, 0)
    te_packet = _ip_wrap(te_header + inner_ip)
    parsed = icmp.parse_response(te_packet)
    assert parsed is not None
    assert parsed.is_time_exceeded
    assert not parsed.is_reply
    # The parser must recover the *original* probe's id/seq for matching.
    assert parsed.ident == 0x1111
    assert parsed.seq == 777


def test_parse_ignores_unrelated_icmp():
    # ICMP redirect (type 5) is not traceroute-relevant.
    other = icmp.ICMP_HEADER_STRUCT.pack(5, 0, 0, 0, 0)
    assert icmp.parse_response(_ip_wrap(other)) is None


def test_parse_rejects_truncated():
    assert icmp.parse_response(b"\x45\x00\x00") is None
