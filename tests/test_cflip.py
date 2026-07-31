from scripts.cflip import csv_header, parse_candidates


def test_candidate_parser_keeps_only_requested_port():
    """An incorrect explicit port must not enter the requested endpoint set."""
    text = "1.1.1.1:443#HK\n2.2.2.2:2053#HK\n3.3.3.3\n"

    assert parse_candidates(text, "HK", 443) == ["1.1.1.1", "3.3.3.3"]


def test_candidate_parser_discards_invalid_entries_and_preserves_first_seen_order():
    """Malformed addresses and duplicate candidates must not change output ordering."""
    text = "1.1.1.1:443#HK\ninvalid\n1.1.1.1\n4.4.4.4:443#US\n5.5.5.5:443#HK\n"

    assert parse_candidates(text, "HK", 443) == ["1.1.1.1", "5.5.5.5"]


def test_csv_header_matches_cfopt_contract():
    assert csv_header() == [
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
