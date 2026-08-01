import csv
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import cflip
from scripts.cflip import csv_header, parse_candidates


@dataclass(frozen=True)
class LocalTcpListener:
    """A live loopback listener that accepts every probe connection."""

    host: str
    port: int


@pytest.fixture
def local_listener():
    """Provide a loopback TCP endpoint and always close its worker thread."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(0.05)
    stopped = threading.Event()

    def accept_connections() -> None:
        while not stopped.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                pass

    worker = threading.Thread(target=accept_connections, daemon=True)
    worker.start()
    listener = LocalTcpListener(*server.getsockname())

    try:
        yield listener
    finally:
        stopped.set()
        server.close()
        worker.join(timeout=1)


def test_candidate_parser_keeps_only_requested_port():
    """An incorrect explicit port must not enter the requested endpoint set."""
    text = "1.1.1.1:443#HK\n2.2.2.2:2053#HK\n3.3.3.3\n"

    assert parse_candidates(text, "HK", 443) == ["1.1.1.1", "3.3.3.3"]


def test_candidate_parser_discards_invalid_entries_and_preserves_first_seen_order():
    """Malformed addresses and duplicate candidates must not change output ordering."""
    text = "1.1.1.1:443#HK\ninvalid\n1.1.1.1\n4.4.4.4:443#US\n5.5.5.5:443#HK\n"

    assert parse_candidates(text, "HK", 443) == ["1.1.1.1", "5.5.5.5"]


def test_csv_header_matches_cfopt_contract():
    fixture = Path(__file__).parent / "fixtures" / "cfopt-header.csv"
    with fixture.open(encoding="utf-8", newline="") as source:
        expected_header = next(csv.reader(source))

    assert csv_header() == expected_header


def test_precheck_returns_reachable_address_first(local_listener):
    """Unreachable candidates must not occupy the precheck result limit."""
    assert cflip.tcp_precheck(
        [local_listener.host, "198.18.0.1"], local_listener.port, 0.1, 1
    ) == [local_listener.host]


def test_precheck_limits_in_flight_submissions_for_large_input(monkeypatch):
    """Large inputs must not queue more probes than the worker capacity."""
    addresses = [f"192.0.2.{index}" for index in range(1, 41)]
    original_executor = cflip.ThreadPoolExecutor
    release_probes = threading.Event()
    all_workers_started = threading.Event()
    started_lock = threading.Lock()
    started = 0

    class BoundedExecutor(original_executor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.outstanding = set()

        def submit(self, *args, **kwargs):
            if len(self.outstanding) >= 32:
                raise AssertionError("more than 32 probes were submitted in flight")
            future = super().submit(*args, **kwargs)
            self.outstanding.add(future)
            future.add_done_callback(self.outstanding.discard)
            return future

    def blocked_probe(address, port, timeout):
        nonlocal started
        with started_lock:
            started += 1
            if started == 32:
                all_workers_started.set()
        release_probes.wait()
        return 0.1

    monkeypatch.setattr(cflip, "ThreadPoolExecutor", BoundedExecutor)
    monkeypatch.setattr(cflip, "_tcp_connect_time", blocked_probe)
    outcome = []
    errors = []

    def run_precheck():
        try:
            outcome.append(cflip.tcp_precheck(addresses, 443, 0.1, 4))
        except BaseException as error:  # Keep worker assertion failures observable.
            errors.append(error)

    worker = threading.Thread(target=run_precheck)
    worker.start()
    try:
        assert all_workers_started.wait(timeout=1)
    finally:
        release_probes.set()
        worker.join(timeout=1)

    assert not worker.is_alive()
    assert not errors
    assert outcome == [addresses[:4]]


def test_normalize_cfst_rows_uses_cfopt_ten_column_mapping(tmp_path):
    """CFST measurements must become a complete CFOpt row with a GH marker."""
    source = tmp_path / "cfst.csv"
    source.write_text(
        "IP 地址,已发送,已接收,丢包率,平均延迟,下载速度(MB/s),地区码\n"
        "1.1.1.1,2,2,0,20,1.5,HKG\n",
        encoding="utf-8",
    )

    assert cflip.normalize_cfst_rows(source, "HK", 443) == [
        {
            "IP地址": "1.1.1.1",
            "端口": "443",
            "数据中心": "HKG",
            "城市": "🇭🇰 HK [GitHub Actions#01 tcp-precheck]",
            "TLS": "true",
            "已发送": "2",
            "已接收": "2",
            "丢包率": "0",
            "平均延迟": "20",
            "下载速度(MB/s)": "1.5",
        }
    ]


def test_filter_rows_rejects_bad_measurements_and_caps_each_country():
    """A bad CFST measurement or slower country peer must not be published."""
    rows = [
        {
            "IP地址": "1.1.1.1", "端口": "443", "数据中心": "HKG",
            "城市": "🇭🇰 HK [GitHub Actions#01 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "20",
            "下载速度(MB/s)": "0.04",
        },
        {
            "IP地址": "2.2.2.2", "端口": "443", "数据中心": "HKG",
            "城市": "🇭🇰 HK [GitHub Actions#02 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "30",
            "下载速度(MB/s)": "0.08",
        },
        {
            "IP地址": "3.3.3.3", "端口": "443", "数据中心": "NRT",
            "城市": "🇯🇵 JP [GitHub Actions#01 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "40",
            "下载速度(MB/s)": "0.04",
        },
        {
            "IP地址": "4.4.4.4", "端口": "443", "数据中心": "HKG",
            "城市": "🇭🇰 HK [GitHub Actions#03 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "0", "丢包率": "0", "平均延迟": "20",
            "下载速度(MB/s)": "0.04",
        },
        {
            "IP地址": "5.5.5.5", "端口": "443", "数据中心": "HKG",
            "城市": "🇭🇰 HK [GitHub Actions#04 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "1", "平均延迟": "20",
            "下载速度(MB/s)": "0.04",
        },
        {
            "IP地址": "6.6.6.6", "端口": "443", "数据中心": "HKG",
            "城市": "🇭🇰 HK [GitHub Actions#05 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "421",
            "下载速度(MB/s)": "0.04",
        },
        {
            "IP地址": "7.7.7.7", "端口": "443", "数据中心": "HKG",
            "城市": "🇭🇰 HK [GitHub Actions#06 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "20",
            "下载速度(MB/s)": "0.003",
        },
    ]

    assert [row["IP地址"] for row in cflip.filter_rows(rows, 420, 0.03, 1)] == [
        "1.1.1.1",
        "3.3.3.3",
    ]


def test_write_csv_emits_the_exact_cfopt_header_and_ten_columns(tmp_path):
    """A CSV writer regression must not alter CFOpt's byte-level contract."""
    output = tmp_path / "CloudflareSpeedTest_GH.csv"
    rows = [{
        "IP地址": "1.1.1.1", "端口": "443", "数据中心": "HKG",
        "城市": "🇭🇰 HK [GitHub Actions#01 tcp-precheck]", "TLS": "true",
        "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "20",
        "下载速度(MB/s)": "1.5",
    }]

    cflip.write_csv(output, rows)

    assert output.read_bytes() == (
        "IP地址,端口,数据中心,城市,TLS,已发送,已接收,丢包率,平均延迟,下载速度(MB/s)\n"
        "1.1.1.1,443,HKG,🇭🇰 HK [GitHub Actions#01 tcp-precheck],true,2,2,0,20,1.5\n"
    ).encode("utf-8")


def test_proxy_writer_emits_only_address_country_lines(tmp_path, local_listener):
    """The proxy CLI must preserve CFOpt's address#COUNTRY output format."""
    source = tmp_path / "candidates.txt"
    output = tmp_path / "proxyip-best.txt"
    source.write_text(f"{local_listener.host}:{local_listener.port}#hk\n", encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(Path("scripts/generate_proxyip_best.py").resolve()),
            "--source", source.as_uri(), "--output", str(output),
            "--countries", "HK", "--limit", "1", "--timeout", "0.1", "--workers", "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert output.read_text(encoding="utf-8") == f"{local_listener.host}:{local_listener.port}#HK\n"
