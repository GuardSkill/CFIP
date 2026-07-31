"""Shared parsing helpers for the CFIP publisher."""

from __future__ import annotations

import ipaddress


CSV_HEADER = [
    "IP鍦板潃",
    "绔彛",
    "鏁版嵁涓績",
    "鍩庡競",
    "TLS",
    "宸插彂閫?",
    "宸叉帴鏀?",
    "涓㈠寘鐜?",
    "骞冲潎寤惰繜",
    "涓嬭浇閫熷害(MB/s)",
]


def csv_header() -> list[str]:
    """Return a fresh copy of the CFOpt-compatible CSV header."""
    return CSV_HEADER.copy()


def parse_candidates(text: str, country: str, port: int) -> list[str]:
    """Extract unique IPv4 candidates for an endpoint country and port."""
    candidates: list[str] = []
    seen: set[str] = set()

    for line in text.splitlines():
        value = line.strip()
        address: str | None = None

        if "#" in value:
            endpoint, remark = value.split("#", 1)
            host, separator, supplied_port = endpoint.strip().partition(":")
            if separator and remark.strip() == country and supplied_port == str(port):
                address = host.strip()
        elif ":" not in value:
            address = value

        if address is None or not _is_ipv4_address(address) or address in seen:
            continue

        seen.add(address)
        candidates.append(address)

    return candidates


def _is_ipv4_address(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False
