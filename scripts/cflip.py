"""Shared parsing helpers for the CFIP publisher."""

from __future__ import annotations

import csv
import ipaddress
import math
import socket
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write filtered CFST rows using CFOpt's exact header and field order."""
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=CSV_HEADER,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


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
    pending = iter(enumerate(addresses))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for _ in range(workers):
            index, address = next(pending)
            futures[executor.submit(_tcp_connect_time, address, port, timeout)] = (
                index,
                address,
            )

        while futures:
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                index, address = futures.pop(future)
                elapsed = future.result()
                if elapsed is not None:
                    ranked.append((elapsed, index, address))

                try:
                    next_index, next_address = next(pending)
                except StopIteration:
                    continue
                futures[
                    executor.submit(_tcp_connect_time, next_address, port, timeout)
                ] = (next_index, next_address)

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


def filter_rows(
    rows: list[dict[str, str]],
    max_latency: float,
    min_speed: float,
    per_country: int,
) -> list[dict[str, str]]:
    """Keep valid CFST rows, deduplicated and capped by country."""
    if per_country <= 0:
        return []

    eligible: list[tuple[str, float, float, int, dict[str, str]]] = []
    for index, row in enumerate(rows):
        try:
            received = float(row["已接收"])
            loss = float(row["丢包率"])
            latency = float(row["平均延迟"])
            speed = float(row["下载速度(MB/s)"])
        except (KeyError, TypeError, ValueError):
            continue

        if not all(math.isfinite(value) for value in (received, loss, latency, speed)):
            continue
        if received < 1 or loss >= 1 or latency > max_latency or speed * 8 < min_speed:
            continue

        eligible.append((_row_country(row), latency, speed, index, row))

    best_by_endpoint: dict[tuple[str, str, str], tuple[str, float, float, int, dict[str, str]]] = {}
    for candidate in eligible:
        country, latency, speed, index, row = candidate
        key = (row.get("IP地址", ""), row.get("端口", ""), country)
        existing = best_by_endpoint.get(key)
        if existing is None or (latency, -speed, index) < (
            existing[1],
            -existing[2],
            existing[3],
        ):
            best_by_endpoint[key] = candidate

    grouped: dict[str, list[tuple[str, float, float, int, dict[str, str]]]] = {}
    for candidate in best_by_endpoint.values():
        grouped.setdefault(candidate[0], []).append(candidate)

    kept: list[dict[str, str]] = []
    for country in sorted(grouped):
        country_rows = sorted(grouped[country], key=lambda item: (item[1], -item[2], item[3]))
        kept.extend(row for _, _, _, _, row in country_rows[:per_country])
    return kept


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


def _row_country(row: dict[str, str]) -> str:
    for token in row.get("城市", "").split():
        if len(token) == 2 and token.isascii() and token.isalpha():
            return token.upper()
    return "UNK"
