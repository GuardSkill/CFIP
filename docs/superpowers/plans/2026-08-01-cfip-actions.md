# CFIP GitHub Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a CFOpt-compatible GitHub Actions CSV and proxy list every 150 minutes.

**Architecture:** A dependency-free Python runner loads country candidates, asynchronously TCP-ranks them, invokes CFST only for the survivors, then filters/writes one compatible CSV. A workflow runs the runner every 30 minutes, while a committed state file enforces a 150-minute successful-run interval and the workflow commits changed artifacts.

**Tech Stack:** Python 3.12 standard library, pytest, Bash, GitHub Actions, XIU2 CloudflareSpeedTest.

## Global Constraints

- Publish `CloudflareSpeedTest_GH.csv` with CFOpt's exact ten-column header and order.
- Publish `proxyip-best.txt` lines only as `host:port#COUNTRY`.
- Identify generated CSV rows as GitHub Actions measurements, not Chengdu/Beijing results.
- Keep the three CFOpt Subconverter files and `rules/` tree in the repository.
- Never commit `.env`, local tokens, or credentials; use workflow `GITHUB_TOKEN` only.
- Use a 30-minute workflow schedule with a 150-minute successful-run gate.

---

### Task 1: Establish project assets and candidate parsing

**Files:**
- Create: `scripts/cflip.py`
- Create: `tests/test_cflip.py`
- Create: `requirements-dev.txt`
- Copy: `CFOpt_Subconverter.ini`, `CFOpt_Subconverter_lite.ini`, `CFOpt_Subconverter_lite_cmliussss.ini`, `rules/`

**Interfaces:**
- Produces `parse_candidates(text: str, country: str, port: int) -> list[str]` and `csv_header() -> list[str]`.
- `parse_candidates` accepts plain IP lines and `IP:port#remark` lines, discards invalid/nonmatching entries, and preserves first-seen order.

- [ ] **Step 1: Write failing parsing and header tests**

```python
from scripts.cflip import csv_header, parse_candidates

def test_candidate_parser_keeps_only_requested_port():
    text = "1.1.1.1:443#HK\n2.2.2.2:2053#HK\n3.3.3.3\n"
    assert parse_candidates(text, "HK", 443) == ["1.1.1.1", "3.3.3.3"]

def test_csv_header_matches_cfopt_contract():
    assert csv_header() == ["IP地址", "端口", "数据中心", "城市", "TLS", "已发送", "已接收", "丢包率", "平均延迟", "下载速度(MB/s)"]
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: FAIL because `scripts.cflip` does not exist.

- [ ] **Step 3: Implement minimal parsing and output constants**

```python
CSV_HEADER = ["IP地址", "端口", "数据中心", "城市", "TLS", "已发送", "已接收", "丢包率", "平均延迟", "下载速度(MB/s)"]

def csv_header() -> list[str]:
    return CSV_HEADER.copy()

def parse_candidates(text: str, country: str, port: int) -> list[str]:
    # Split at '#', accept bare IPv4 or an explicit matching port, then dedupe.
    return candidates
```

- [ ] **Step 4: Run the focused tests and confirm pass**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: PASS.

- [ ] **Step 5: Copy static subscription assets and commit**

Run: `git add scripts/cflip.py tests/test_cflip.py requirements-dev.txt CFOpt_Subconverter*.ini rules && git commit -m "feat: add CFIP parsing and subscription assets"`

### Task 2: Add TCP precheck and CFST result normalization

**Files:**
- Modify: `scripts/cflip.py`
- Modify: `tests/test_cflip.py`

**Interfaces:**
- Produces `tcp_precheck(addresses: list[str], port: int, timeout: float, limit: int) -> list[str]`.
- Produces `normalize_cfst_rows(path: Path, country: str, port: int) -> list[dict[str, str]]`.

- [ ] **Step 1: Write failing tests with a local TCP listener and CFST fixture**

```python
def test_precheck_returns_reachable_address_first(local_listener):
    assert tcp_precheck([local_listener.host, "198.18.0.1"], local_listener.port, 0.1, 1) == [local_listener.host]

def test_normalize_cfst_rows_uses_compatible_city(tmp_path):
    source = tmp_path / "cfst.csv"
    source.write_text("IP 地址,已发送,已接收,丢包率,平均延迟,下载速度(MB/s),地区码\n1.1.1.1,2,2,0,20,1.5,HKG\n", encoding="utf-8")
    assert normalize_cfst_rows(source, "HK", 443)[0]["城市"] == "🇭🇰 HK [GitHub Actions#01 tcp-precheck]"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: FAIL because precheck and normalization functions do not exist.

- [ ] **Step 3: Implement concurrent TCP ranking and CSV mapping**

```python
def tcp_precheck(addresses, port, timeout, limit):
    # ThreadPoolExecutor probes socket.create_connection and returns the sorted top limit.
    return ranked[:limit]

def normalize_cfst_rows(path, country, port):
    # Read CFST CSV, construct all ten CSV fields, and assign city sequence by country.
    return normalized_rows
```

- [ ] **Step 4: Run tests and confirm pass**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add scripts/cflip.py tests/test_cflip.py && git commit -m "feat: prefilter TCP candidates before CFST"`

### Task 3: Filter, write outputs, and generate proxy list

**Files:**
- Modify: `scripts/cflip.py`
- Create: `scripts/generate_proxyip_best.py`
- Modify: `tests/test_cflip.py`

**Interfaces:**
- Produces `filter_rows(rows: list[dict[str, str]], max_latency: float, min_speed: float, per_country: int) -> list[dict[str, str]]`.
- CLI `python scripts/generate_proxyip_best.py --source URL --output proxyip-best.txt` writes compatible lines.

- [ ] **Step 1: Write failing filtering and proxy-format tests**

```python
def test_filter_removes_loss_and_retains_fastest_per_country():
    assert [row["IP地址"] for row in filter_rows(rows, 420, 0.03, 1)] == ["1.1.1.1"]

def test_proxy_writer_emits_only_address_country_lines(tmp_path):
    assert output.read_text(encoding="utf-8") == "1.1.1.1:443#HK\n"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: FAIL because filtering/proxy writer is unavailable.

- [ ] **Step 3: Implement CFOpt-equivalent acceptance checks and copied proxy generator behavior**

```python
def filter_rows(rows, max_latency, min_speed, per_country):
    # Require received >= 1, loss < 1, latency <= max_latency and speed*8 >= min_speed.
    return capped_rows
```

- [ ] **Step 4: Run tests and confirm pass**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add scripts tests && git commit -m "feat: write compatible CFIP artifacts"`

### Task 4: Add runner CLI, state gate, workflow, and documentation

**Files:**
- Modify: `scripts/cflip.py`
- Create: `.github/workflows/update.yml`
- Create: `README.md`
- Modify: `tests/test_cflip.py`

**Interfaces:**
- CLI supports `--dry-run`, `--state-file`, `--now`, and returns zero without network work when the state is younger than 150 minutes.

- [ ] **Step 1: Write failing interval and dry-run tests**

```python
def test_interval_gate_skips_before_150_minutes(tmp_path):
    state = tmp_path / "last-success.txt"; state.write_text("2026-08-01T00:00:00+00:00")
    assert should_run(state, datetime.fromisoformat("2026-08-01T02:29:00+00:00")) is False
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: FAIL because `should_run` does not exist.

- [ ] **Step 3: Implement CLI and workflow**

```yaml
on:
  schedule: [{cron: '*/30 * * * *'}]
  workflow_dispatch:
permissions: {contents: write}
```

The workflow installs CFST, runs the Python CLI, and commits only changed `CloudflareSpeedTest_GH.csv`, `proxyip-best.txt`, and `.cflip/last-success.txt`.

- [ ] **Step 4: Run all tests and static workflow checks**

Run: `python -m pytest -v; python scripts/cflip.py --dry-run --fixture-dir tests/fixtures`

Expected: PASS; dry-run prints intended CFST commands without network calls.

- [ ] **Step 5: Commit**

Run: `git add .github README.md scripts tests && git commit -m "feat: automate CFIP publishing with GitHub Actions"`

### Task 5: Verify a clean repository and publish the initial project

**Files:**
- Verify: all tracked repository files.

- [ ] **Step 1: Inspect generated formats from fixture run**

Run: `python scripts/cflip.py --dry-run --fixture-dir tests/fixtures; Get-Content CloudflareSpeedTest_GH.csv -TotalCount 2; Get-Content proxyip-best.txt -TotalCount 3`

Expected: exact CSV header, ten columns per row, and `host:port#COUNTRY` proxy lines.

- [ ] **Step 2: Run full verification and review diff**

Run: `python -m pytest -v; git status --short; git diff --check HEAD`

Expected: tests pass, no whitespace errors, only intended uncommitted generated files if any.

- [ ] **Step 3: Commit final artifacts and push**

Run: `git add -A && git commit -m "chore: initialize CFIP publisher"; git push origin main`

Expected: `GuardSkill/CFIP` receives the complete initial project.
