import csv
import io
import socket
import subprocess
import sys
import threading
import urllib.error
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from scripts import cflip
from scripts.cflip import csv_header, parse_candidates


def test_runtime_candidates_continue_when_one_cfbestip_country_is_missing(monkeypatch):
    """A missing optional cf-bestip country file must not discard ip.zip input."""
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("443/HK.txt", "1.1.1.1\n")
        archive.writestr("443/KR.txt", "2.2.2.2\n")

    def download(url, timeout):
        if url == "https://example.test/ip.zip":
            return source.getvalue()
        if url.endswith("ip_HK.txt"):
            return b"3.3.3.3:443#HK-score\n"
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(cflip, "_download", download)

    candidates = cflip.load_runtime_candidates(
        ["HK", "KR"], [443], "https://example.test/ip.zip",
        "https://example.test/cf-bestip", 1,
    )

    assert candidates[("HK", 443)] == ["1.1.1.1", "3.3.3.3"]
    assert candidates[("KR", 443)] == ["2.2.2.2"]


def test_cfst_command_serializes_integral_latency_without_decimal(tmp_path):
    """CFST accepts an integer only for its -tl latency limit."""
    command = cflip._cfst_command(
        tmp_path / "cfst.exe", tmp_path / "candidates.txt", tmp_path / "result.csv",
        443, 420.0, 0.03, "https://example.test/download",
    )

    assert command[command.index("-tl") + 1] == "420"


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
        "2.2.2.2",
        "3.3.3.3",
    ]


def test_filter_rows_uses_min_speed_as_mb_per_second():
    """A 0.0037 MB/s row must not satisfy the 0.03 MB/s default floor."""
    rows = [{
        "IP地址": "1.1.1.1", "端口": "443", "数据中心": "HKG",
        "城市": "🇭🇰 HK [GitHub Actions#01 tcp-precheck]", "TLS": "true",
        "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "20",
        "下载速度(MB/s)": "0.0037",
    }]

    assert cflip.filter_rows(rows, 420, 0.03, 1) == []


def test_filter_rows_keeps_the_best_duplicate_endpoint_key():
    """An IP/port/country duplicate keeps the lower-latency, then faster row."""
    rows = [
        {
            "IP地址": "1.1.1.1", "端口": "443", "数据中心": "SLOW",
            "城市": "🇭🇰 HK [GitHub Actions#01 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "30",
            "下载速度(MB/s)": "0.08",
        },
        {
            "IP地址": "1.1.1.1", "端口": "443", "数据中心": "FAST",
            "城市": "🇭🇰 HK [GitHub Actions#02 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "20",
            "下载速度(MB/s)": "0.04",
        },
        {
            "IP地址": "2.2.2.2", "端口": "443", "数据中心": "LOW-SPEED",
            "城市": "🇭🇰 HK [GitHub Actions#03 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "20",
            "下载速度(MB/s)": "0.04",
        },
        {
            "IP地址": "2.2.2.2", "端口": "443", "数据中心": "HIGH-SPEED",
            "城市": "🇭🇰 HK [GitHub Actions#04 tcp-precheck]", "TLS": "true",
            "已发送": "2", "已接收": "2", "丢包率": "0", "平均延迟": "20",
            "下载速度(MB/s)": "0.08",
        },
    ]

    assert [row["数据中心"] for row in cflip.filter_rows(rows, 420, 0.03, 2)] == [
        "HIGH-SPEED",
        "FAST",
    ]


def test_country_speed_thresholds_keep_qualified_rows_and_two_fastest_fallbacks():
    """Each country filters by its own MB/s floor, but keeps two valid rows."""
    header = cflip.csv_header()

    def row(country, address, speed, latency=20):
        return dict(zip(header, [
            address, "443", country, f"{country} test", "true", "2", "2", "0",
            str(latency), str(speed),
        ]))

    rows = [
        row("JP", "1.1.1.1", 11.0),
        row("JP", "1.1.1.2", 9.0),
        row("JP", "1.1.1.3", 8.0),
        row("HK", "2.2.2.2", 1.5),
        row("HK", "2.2.2.3", 1.2),
        row("HK", "2.2.2.4", 0.8),
    ]

    results = cflip.filter_rows(
        rows, 420, 0.03, 20, {"JP": 10.0, "HK": 2.0}, minimum_per_country=2,
    )

    assert [item[header[0]] for item in results] == [
        "2.2.2.2", "2.2.2.3", "1.1.1.1", "1.1.1.2",
    ]


def test_country_speed_threshold_parser_rejects_non_numeric_value():
    with pytest.raises(ValueError):
        cflip.parse_country_speed_thresholds("JP=fast")


def test_default_country_speed_thresholds_include_us_in_mb_per_second():
    assert cflip.DEFAULT_COUNTRY_MIN_SPEEDS == {
        "JP": 10.0, "US": 5.0, "KR": 3.0, "HK": 2.0,
        "DE": 5.0, "GB": 3.0, "SG": 5.0,
    }
    assert "US" in cflip.DEFAULT_COUNTRIES


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


def test_interval_gate_skips_before_150_minutes_and_runs_at_boundary(tmp_path):
    """A successful publish must suppress every run younger than 150 minutes."""
    state = tmp_path / "last-success.txt"
    state.write_text("2026-08-01T00:00:00+00:00\n", encoding="utf-8")

    assert cflip.should_run(
        state, datetime.fromisoformat("2026-08-01T02:29:00+00:00")
    ) is False
    assert cflip.should_run(
        state, datetime.fromisoformat("2026-08-01T02:30:00+00:00")
    ) is True


def test_cli_gate_returns_before_pipeline_work(tmp_path, monkeypatch, capsys):
    """A gated invocation must not reach candidate, TCP, CFST, or proxy work."""
    state = tmp_path / "last-success.txt"
    state.write_text("2026-08-01T00:00:00+00:00\n", encoding="utf-8")

    def unexpected_pipeline(_args, _now):
        raise AssertionError("the gated invocation entered the network pipeline")

    monkeypatch.setattr(cflip, "run_pipeline", unexpected_pipeline)

    result = cflip.main(
        [
            "--state-file", str(state),
            "--now", "2026-08-01T02:29:00+00:00",
        ]
    )

    assert result == 0
    assert "150-minute gate" in capsys.readouterr().out


def test_dry_run_uses_fixtures_and_writes_complete_artifacts(tmp_path):
    """Dry-run must create reviewable outputs without public network or CFST."""
    output = tmp_path / "CloudflareSpeedTest_GH.csv"
    proxy_output = tmp_path / "proxyip-best.txt"
    state = tmp_path / ".cflip" / "last-success.txt"
    fixture_dir = Path(__file__).parent / "fixtures" / "pipeline"

    completed = subprocess.run(
        [
            sys.executable,
            str(Path("scripts/cflip.py").resolve()),
            "--dry-run",
            "--fixture-dir", str(fixture_dir),
            "--countries", "HK,JP",
            "--ports", "443",
            "--output", str(output),
            "--proxy-output", str(proxy_output),
            "--state-file", str(state),
            "--now", "2026-08-01T02:30:00+00:00",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Would run CFST:" in completed.stdout
    with output.open(encoding="utf-8", newline="") as source:
        assert list(csv.reader(source)) == [
            csv_header(),
            [
                "1.1.1.1", "443", "HKG", "🇭🇰 HK [GitHub Actions#01 tcp-precheck]",
                "true", "2", "2", "0", "20", "1.5",
            ],
            [
                "2.2.2.2", "443", "NRT", "🇯🇵 JP [GitHub Actions#01 tcp-precheck]",
                "true", "2", "2", "0", "30", "1.0",
            ],
        ]
    assert proxy_output.read_text(encoding="utf-8") == (
        "1.1.1.1:443#HK\n2.2.2.2:443#JP\n"
    )
    assert state.read_text(encoding="utf-8") == "2026-08-01T02:30:00+00:00\n"


def test_fixture_runtime_prefilters_before_stubbed_cfst_and_proxy(
    tmp_path, monkeypatch
):
    """Runtime orchestration must prefilter before invoking external tools."""
    fixture_dir = Path(__file__).parent / "fixtures" / "pipeline"
    output = tmp_path / "CloudflareSpeedTest_GH.csv"
    proxy_output = tmp_path / "proxyip-best.txt"
    state = tmp_path / "last-success.txt"
    events = []

    def fixture_precheck(addresses, port, timeout, limit):
        events.append(("tcp", tuple(addresses), port))
        return addresses[:limit]

    def stubbed_run(command, check):
        assert check is True
        if command[0] == "stub-cfst":
            events.append(("cfst",))
            result = Path(command[command.index("-o") + 1])
            result.write_bytes((fixture_dir / "cfst" / "443.csv").read_bytes())
        else:
            events.append(("proxy",))
            result = Path(command[command.index("--output") + 1])
            result.write_bytes((fixture_dir / "proxyip-best.txt").read_bytes())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cflip, "tcp_precheck", fixture_precheck)
    monkeypatch.setattr(cflip.subprocess, "run", stubbed_run)

    result = cflip.main(
        [
            "--fixture-dir", str(fixture_dir),
            "--countries", "HK,JP",
            "--ports", "443",
            "--cfst-path", "stub-cfst",
            "--output", str(output),
            "--proxy-output", str(proxy_output),
            "--state-file", str(state),
            "--now", "2026-08-01T02:30:00+00:00",
        ]
    )

    assert result == 0
    assert [event[0] for event in events] == ["tcp", "tcp", "cfst", "proxy"]
    assert output.is_file()
    assert proxy_output.read_bytes() == (
        fixture_dir / "proxyip-best.txt"
    ).read_bytes()
