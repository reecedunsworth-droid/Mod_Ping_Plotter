"""Reverse-DNS and ASN enrichment for hop addresses.

Both lookups are best-effort and fully asynchronous: the monitor loop never
blocks on them. A request for an address returns whatever is already cached
(possibly ``None``) and schedules the lookup in a small background thread pool;
subsequent passes pick up the resolved value.

* Reverse DNS uses ``socket.gethostbyaddr`` (stdlib, works offline on the LAN).
* ASN lookup uses Team Cymru's free whois interface over TCP:43 - no API key.
  If the network blocks it (or the host is offline) the lookup simply fails
  and the hop is shown without ASN, which is fine.

Private / bogon addresses are recognised and skipped for ASN (and labelled),
so a LAN gateway does not generate pointless external queries.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

CYMRU_HOST = "whois.cymru.com"
CYMRU_PORT = 43


def is_private_or_bogon(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


class Resolver:
    """Caching, non-blocking reverse-DNS + ASN resolver."""

    def __init__(self, resolve_dns: bool = True, resolve_asn: bool = True,
                 workers: int = 4, timeout: float = 3.0):
        self.resolve_dns = resolve_dns
        self.resolve_asn = resolve_asn
        self.timeout = timeout
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="resolver")
        self._lock = threading.Lock()
        self._ptr: Dict[str, Optional[str]] = {}
        self._asn: Dict[str, Optional[dict]] = {}
        self._inflight: set = set()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- public API -------------------------------------------------------

    def enrich(self, ip: Optional[str]) -> dict:
        """Return cached {hostname, asn, as_name} for ``ip`` and schedule misses."""
        if not ip:
            return {"hostname": None, "asn": None, "as_name": None, "private": False}

        private = is_private_or_bogon(ip)
        with self._lock:
            hostname = self._ptr.get(ip, "__miss__")
            asn_info = self._asn.get(ip, "__miss__")

        if self.resolve_dns and hostname == "__miss__":
            self._schedule(ip, "ptr")
            hostname = None
        elif hostname == "__miss__":
            hostname = None

        if self.resolve_asn and not private and asn_info == "__miss__":
            self._schedule(ip, "asn")
            asn_info = None
        elif asn_info == "__miss__":
            asn_info = None

        result = {
            "hostname": hostname if hostname != "__miss__" else None,
            "asn": None,
            "as_name": None,
            "private": private,
        }
        if isinstance(asn_info, dict):
            result["asn"] = asn_info.get("asn")
            result["as_name"] = asn_info.get("as_name")
        return result

    # -- internals --------------------------------------------------------

    def _schedule(self, ip: str, kind: str) -> None:
        key = (ip, kind)
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)
        fn = self._do_ptr if kind == "ptr" else self._do_asn
        fut = self._pool.submit(fn, ip)
        fut.add_done_callback(lambda _f, k=key: self._inflight.discard(k))

    def _do_ptr(self, ip: str) -> None:
        hostname: Optional[str] = None
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except (socket.herror, socket.gaierror, OSError):
            hostname = None
        with self._lock:
            self._ptr[ip] = hostname

    def _do_asn(self, ip: str) -> None:
        info = self._query_cymru(ip)
        with self._lock:
            self._asn[ip] = info

    def _query_cymru(self, ip: str) -> Optional[dict]:
        """Query Team Cymru whois for the origin ASN of ``ip``."""
        query = f"begin\nverbose\n{ip}\nend\n".encode()
        try:
            with socket.create_connection((CYMRU_HOST, CYMRU_PORT),
                                          timeout=self.timeout) as s:
                s.sendall(query)
                chunks = []
                s.settimeout(self.timeout)
                while True:
                    data = s.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
            text = b"".join(chunks).decode(errors="replace")
        except OSError:
            return None

        for line in text.splitlines():
            if "|" not in line or line.lower().startswith("as ") \
                    or "AS Name" in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            # Format: AS | IP | BGP Prefix | CC | Registry | Allocated | AS Name
            if len(parts) >= 7 and parts[0].isdigit():
                return {"asn": parts[0], "as_name": parts[6]}
        return None
