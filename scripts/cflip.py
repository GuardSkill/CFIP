"""Shared parsing helpers for the CFIP publisher."""

from __future__ import annotations

import ipaddress


CSV_HEADER = [
    "IP地址",
    "端口",
    "数据中心",
    "城市",
    "TLS",
    "已发送",
    "已接收",
    "丢包率",
    "平均延迟",
    "下载速度(MB/s)",
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
