"""Shared parsing helpers for the CFIP publisher."""

from __future__ import annotations

import argparse
import csv
import ipaddress
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from io import BytesIO
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

DEFAULT_COUNTRIES = ("HK", "JP", "KR", "SG", "DE", "GB")
DEFAULT_PORTS = (443, 2053, 2083, 2087, 2096, 8443)
DEFAULT_IP_ZIP_URL = "https://zip.cm.edu.kg/ip.zip"
DEFAULT_CFBESTIP_BASE_URL = "https://zoroaaa.github.io/cf-bestip"
DEFAULT_PROXY_SOURCE = "https://zip.cm.edu.kg/all.txt"
SUCCESS_INTERVAL = timedelta(minutes=150)


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


def should_run(
    state_file: Path, now: datetime, interval: timedelta = SUCCESS_INTERVAL
) -> bool:
    """Return whether the last successful publish is at least ``interval`` old."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone offset")
    try:
        last_success = datetime.fromisoformat(
            state_file.read_text(encoding="utf-8").strip()
        )
    except (FileNotFoundError, OSError, ValueError):
        return True
    if last_success.tzinfo is None or last_success.utcoffset() is None:
        return True
    return now.astimezone(timezone.utc) - last_success.astimezone(timezone.utc) >= interval


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
        speed_mbps = round(speed * 8, 2)
        if received < 1 or loss >= 1 or latency > max_latency or speed_mbps < min_speed:
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


def deterministic_sample(items: list[str], limit: int) -> list[str]:
    """Keep a stable, evenly distributed subset without using randomness."""
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return items.copy()
    return [items[index * len(items) // limit] for index in range(limit)]


def _parse_cfbestip_candidates(text: str, country: str, port: int) -> list[str]:
    sanitized: list[str] = []
    for raw_line in text.splitlines():
        endpoint = raw_line.partition("#")[0].strip()
        host, separator, supplied_port = endpoint.partition(":")
        if separator and supplied_port == str(port):
            sanitized.append(f"{host}:{port}#{country}")
    return parse_candidates("\n".join(sanitized), country, port)


def _resolve_fixture_dir(path: Path) -> Path:
    nested = path / "pipeline"
    return nested if nested.is_dir() else path


def load_fixture_candidates(
    fixture_dir: Path, countries: list[str], ports: list[int]
) -> dict[tuple[str, int], list[str]]:
    """Load deterministic test candidates from the committed fixture tree."""
    root = _resolve_fixture_dir(fixture_dir)
    loaded: dict[tuple[str, int], list[str]] = {}
    cfbestip_text: dict[str, str] = {}
    for country in countries:
        cfbestip_path = root / "cfbestip" / f"ip_{country}.txt"
        cfbestip_text[country] = (
            cfbestip_path.read_text(encoding="utf-8-sig")
            if cfbestip_path.is_file()
            else ""
        )
        for port in ports:
            ipzip_path = root / "ipzip" / str(port) / f"{country}.txt"
            ipzip = (
                parse_candidates(
                    ipzip_path.read_text(encoding="utf-8-sig"), country, port
                )
                if ipzip_path.is_file()
                else []
            )
            cfbestip = _parse_cfbestip_candidates(
                cfbestip_text[country], country, port
            )
            loaded[(country, port)] = list(dict.fromkeys(ipzip + cfbestip))
    return loaded


def _download(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "CFIP/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _zip_country_text(archive: zipfile.ZipFile, country: str, port: int) -> str:
    expected = f"{port}/{country}.txt".lower()
    for name in archive.namelist():
        normalized = name.replace("\\", "/").lstrip("/").lower()
        if normalized == expected or normalized.endswith("/" + expected):
            return archive.read(name).decode("utf-8-sig")
    return ""


def load_runtime_candidates(
    countries: list[str],
    ports: list[int],
    ip_zip_url: str,
    cfbestip_base_url: str,
    timeout: float,
) -> dict[tuple[str, int], list[str]]:
    """Download and merge the configured ip.zip and cf-bestip sources."""
    with zipfile.ZipFile(BytesIO(_download(ip_zip_url, timeout))) as archive:
        ipzip_text = {
            (country, port): _zip_country_text(archive, country, port)
            for country in countries
            for port in ports
        }

    cfbestip_text: dict[str, str] = {}
    for country in countries:
        url = f"{cfbestip_base_url.rstrip('/')}/ip_{country}.txt"
        try:
            cfbestip_text[country] = _download(url, timeout).decode("utf-8-sig")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            print(f"Optional cf-bestip source missing for {country}: {url}")
            cfbestip_text[country] = ""
    loaded: dict[tuple[str, int], list[str]] = {}
    for country in countries:
        for port in ports:
            ipzip = parse_candidates(ipzip_text[(country, port)], country, port)
            cfbestip = _parse_cfbestip_candidates(
                cfbestip_text[country], country, port
            )
            loaded[(country, port)] = list(dict.fromkeys(ipzip + cfbestip))
    return loaded


def _normalize_mapped_cfst_rows(
    path: Path, port: int, countries_by_address: dict[str, str]
) -> list[dict[str, str]]:
    sequences: dict[str, int] = {}
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            address = row["IP 地址"].strip()
            try:
                country = countries_by_address[address]
            except KeyError as error:
                raise ValueError(
                    f"CFST returned an address that was not submitted: {address}"
                ) from error
            sequences[country] = sequences.get(country, 0) + 1
            rows.append(
                {
                    "IP地址": address,
                    "端口": str(port),
                    "数据中心": row["地区码"],
                    "城市": (
                        f"{_country_flag(country)} {country} "
                        f"[GitHub Actions#{sequences[country]:02d} tcp-precheck]"
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


def _cfst_command(
    cfst_path: Path,
    candidates_path: Path,
    result_path: Path,
    port: int,
    max_latency: float,
    min_speed: float,
    download_url: str,
) -> list[str]:
    command = [
        str(cfst_path),
        "-f", str(candidates_path),
        "-o", str(result_path),
        "-n", "80",
        "-t", "6",
        "-dn", "30",
        "-dt", "15",
        "-tl", str(max_latency),
        "-tlr", "0",
        "-p", "0",
    ]
    if port != 443:
        command.extend(["-tp", str(port)])
    if download_url:
        command.extend(["-url", download_url])
    if min_speed > 0:
        command.extend(["-sl", str(min_speed)])
    return command


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def _write_state(path: Path, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(now.isoformat() + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _parse_csv(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _parse_ports(value: str) -> list[int]:
    ports = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not ports or any(port < 1 or port > 65535 for port in ports):
        raise ValueError("ports must be integers from 1 through 65535")
    return ports


def run_pipeline(args: argparse.Namespace, now: datetime) -> None:
    """Run one transactional CFIP publish using runtime sources or fixtures."""
    countries = _parse_csv(args.countries)
    ports = _parse_ports(args.ports)
    if not countries or any(len(country) != 2 for country in countries):
        raise ValueError("countries must be comma-separated two-letter codes")

    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else None
    if fixture_dir:
        fixture_root = _resolve_fixture_dir(fixture_dir)
        candidates = load_fixture_candidates(fixture_root, countries, ports)
    elif args.dry_run:
        raise ValueError("--dry-run requires --fixture-dir")
    else:
        candidates = load_runtime_candidates(
            countries,
            ports,
            args.ip_zip_url,
            args.cfbestip_base_url,
            args.source_timeout,
        )

    with tempfile.TemporaryDirectory(prefix="cflip-") as temporary_name:
        work = Path(temporary_name)
        all_rows: list[dict[str, str]] = []
        for port in ports:
            countries_by_address: dict[str, str] = {}
            selected: list[str] = []
            for country in countries:
                sampled = deterministic_sample(
                    candidates.get((country, port), []), args.candidate_limit
                )
                survivors = (
                    sampled[: args.tcp_limit]
                    if args.dry_run
                    else tcp_precheck(
                        sampled, port, args.tcp_timeout, args.tcp_limit
                    )
                )
                for address in survivors:
                    if address not in countries_by_address:
                        countries_by_address[address] = country
                        selected.append(address)

            if not selected:
                continue
            candidates_path = work / f"candidates-{port}.txt"
            candidates_path.write_text(
                "\n".join(selected) + "\n", encoding="utf-8", newline="\n"
            )
            result_path = work / f"cfst-{port}.csv"
            command = _cfst_command(
                Path(args.cfst_path),
                candidates_path,
                result_path,
                port,
                args.max_latency,
                args.min_speed,
                args.download_url,
            )
            if args.dry_run:
                print("Would run CFST: " + " ".join(command))
                fixture_result = fixture_root / "cfst" / f"{port}.csv"
                if not fixture_result.is_file():
                    raise FileNotFoundError(
                        f"missing CFST fixture for port {port}: {fixture_result}"
                    )
                shutil.copyfile(fixture_result, result_path)
            else:
                subprocess.run(command, check=True)
            all_rows.extend(
                _normalize_mapped_cfst_rows(
                    result_path, port, countries_by_address
                )
            )

        filtered = filter_rows(
            all_rows, args.max_latency, args.min_speed, args.per_country
        )
        if not filtered:
            raise RuntimeError("CFST produced no publishable rows")
        staged_csv = work / "CloudflareSpeedTest_GH.csv"
        write_csv(staged_csv, filtered)

        staged_proxy = work / "proxyip-best.txt"
        if args.dry_run:
            proxy_fixture = fixture_root / "proxyip-best.txt"
            if not proxy_fixture.is_file():
                raise FileNotFoundError(
                    f"missing proxy fixture: {proxy_fixture}"
                )
            shutil.copyfile(proxy_fixture, staged_proxy)
        else:
            proxy_script = Path(__file__).with_name("generate_proxyip_best.py")
            subprocess.run(
                [
                    sys.executable,
                    str(proxy_script),
                    "--source", args.proxy_source,
                    "--output", str(staged_proxy),
                    "--countries", args.proxy_countries,
                    "--limit", str(args.proxy_limit),
                    "--country-limits", args.proxy_country_limits,
                    "--timeout", str(args.proxy_timeout),
                    "--workers", str(args.proxy_workers),
                ],
                check=True,
            )

        _atomic_copy(staged_csv, Path(args.output))
        _atomic_copy(staged_proxy, Path(args.proxy_output))
        _write_state(Path(args.state_file), now)
        print(f"Published {len(filtered)} CSV rows and updated success state.")


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish GitHub Actions CFST results in CFOpt-compatible formats."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fixture-dir")
    parser.add_argument("--state-file", default=".cflip/last-success.txt")
    parser.add_argument("--now", help="Timezone-aware ISO-8601 clock override.")
    parser.add_argument("--output", default="CloudflareSpeedTest_GH.csv")
    parser.add_argument("--proxy-output", default="proxyip-best.txt")
    parser.add_argument("--countries", default=",".join(DEFAULT_COUNTRIES))
    parser.add_argument("--ports", default=",".join(map(str, DEFAULT_PORTS)))
    parser.add_argument("--cfst-path", default=os.environ.get("CFST_PATH", "./cfst"))
    parser.add_argument("--ip-zip-url", default=DEFAULT_IP_ZIP_URL)
    parser.add_argument("--cfbestip-base-url", default=DEFAULT_CFBESTIP_BASE_URL)
    parser.add_argument("--source-timeout", type=float, default=30.0)
    parser.add_argument("--candidate-limit", type=int, default=200)
    parser.add_argument("--tcp-timeout", type=float, default=0.8)
    parser.add_argument("--tcp-limit", type=int, default=80)
    parser.add_argument("--max-latency", type=float, default=420.0)
    parser.add_argument("--min-speed", type=float, default=0.03)
    parser.add_argument("--per-country", type=int, default=20)
    parser.add_argument("--download-url", default="https://cf.xiu2.xyz/url")
    parser.add_argument("--proxy-source", default=DEFAULT_PROXY_SOURCE)
    parser.add_argument(
        "--proxy-countries", default="IE,AT,AU,KR,HK,SG,JP,DE,GB"
    )
    parser.add_argument("--proxy-limit", type=int, default=10)
    parser.add_argument("--proxy-country-limits", default="HK=50")
    parser.add_argument("--proxy-timeout", type=float, default=0.75)
    parser.add_argument("--proxy-workers", type=int, default=64)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)
    try:
        now = (
            datetime.fromisoformat(args.now)
            if args.now
            else datetime.now(timezone.utc)
        )
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("--now must include a timezone offset")
        state_file = Path(args.state_file)
        if not should_run(state_file, now):
            print("Skipping: the last success is younger than the 150-minute gate.")
            return 0
        run_pipeline(args, now)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"CFIP failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
