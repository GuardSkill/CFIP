"""Shared parsing helpers for the CFIP publisher."""

from __future__ import annotations

import csv
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


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


def tcp_precheck(
    addresses: list[str], port: int, timeout: float, limit: int
) -> list[str]:
    """Return the fastest reachable addresses from concurrent TCP probes."""
    if not addresses or limit <= 0:
        return []

    ranked: list[tuple[float, int, str]] = []
    workers = min(32, len(addresses))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_tcp_connect_time, address, port, timeout): (index, address)
            for index, address in enumerate(addresses)
        }
        for future in as_completed(futures):
            index, address = futures[future]
            elapsed = future.result()
            if elapsed is not None:
                ranked.append((elapsed, index, address))

    ranked.sort()
    return [address for _, _, address in ranked[:limit]]


def normalize_cfst_rows(path: Path, country: str, port: int) -> list[dict[str, str]]:
    """Map CloudflareSpeedTest CSV measurements to CFOpt's ten columns."""
    country_code = country.strip().upper()
    rows: list[dict[str, str]] = []

    with path.open(encoding="utf-8-sig", newline="") as source:
        for sequence, row in enumerate(csv.DictReader(source), start=1):
            rows.append(
                {
                    "IP地址": row["IP 地址"],
                    "端口": str(port),
                    "数据中心": row["地区码"],
                    "城市": (
                        f"{_country_flag(country_code)} {country_code} "
                        f"[GitHub Actions#{sequence:02d} tcp-precheck]"
                    ),
                    "TLS": "true",
                    "已发送": row["已发送"],
                    "已接收": row["已接收"],
                    "丢包率": row["丢包率"],
                    "平均延迟": row["平均延迟"],
                    "下载速度(MB/s)": row["下载速度(MB/s)"],
                }
            )

    return rows


def _is_ipv4_address(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False


def _tcp_connect_time(address: str, port: int, timeout: float) -> float | None:
    started = time.perf_counter()
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return time.perf_counter() - started
    except OSError:
        return None


def _country_flag(country: str) -> str:
    if len(country) == 2 and country.isascii() and country.isalpha():
        return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in country)
    return country
