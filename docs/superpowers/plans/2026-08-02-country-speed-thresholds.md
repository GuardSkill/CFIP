# Country Speed Thresholds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a Chengdu-labelled CFOpt-compatible CSV that applies per-country MB/s speed thresholds while retaining at least two valid results per country.

**Architecture:** `scripts/cflip.py` will parse a compact country-to-speed map and apply it after measurement validation and endpoint de-duplication.  Filtering will first choose threshold-qualified rows, then use the fastest valid sub-threshold rows only to meet the two-row country floor.  The workflow and documentation will consistently publish the renamed CD artifact.

**Tech Stack:** Python 3.11+ standard library, `unittest`, PowerShell GitHub Actions workflow, Git.

## Global Constraints

- Speed thresholds are MB/s: JP=10, US=5, KR=3, HK=2, DE=5, GB=3, SG=5.
- Countries absent from the map retain a `0.03 MB/s` default threshold.
- Fallback rows must still pass received-packet, loss, and latency validation.
- Each country retains at most `--per-country` rows and at least two valid rows when two exist.
- The CSV is named `CloudflareSpeedTest_CD.csv`; its city value is `🇨🇳 CD [成都测速#NN tcp-precheck]`.
- `proxyip-best.txt` and subscription configuration assets are unchanged.

---

### Task 1: Test and implement country-aware filtering

**Files:**
- Modify: `tests/test_cflip.py`
- Modify: `scripts/cflip.py:174-216,572-585`

**Interfaces:**
- Produces: `parse_country_speed_thresholds(value: str) -> dict[str, float]`.
- Produces: `filter_rows(rows, max_latency, min_speed, per_country, country_min_speeds, minimum_per_country=2) -> list[dict[str, str]]`.

- [x] **Step 1: Write failing tests for parsing and country fallback**

```python
def test_filter_rows_uses_country_thresholds_and_two_row_fallback():
    rows = [
        make_row("JP", "10.0", "1.1.1.1"),
        make_row("JP", "9.0", "1.1.1.2"),
        make_row("JP", "8.0", "1.1.1.3"),
        make_row("HK", "1.5", "2.2.2.2"),
        make_row("HK", "1.2", "2.2.2.3"),
        make_row("HK", "0.8", "2.2.2.4"),
    ]
    result = cflip.filter_rows(rows, 420, 0.03, 20, {"JP": 10.0, "HK": 2.0})
    assert [row["IP地址"] for row in result] == ["2.2.2.2", "2.2.2.3", "1.1.1.1", "1.1.1.2"]


def test_parse_country_speed_thresholds_rejects_invalid_values():
    with pytest.raises(ValueError):
        cflip.parse_country_speed_thresholds("JP=ten")
```

- [x] **Step 2: Run the targeted tests to verify failure**

Run: `python -m pytest tests/test_cflip.py -k "country_thresholds or two_row_fallback" -v`

Expected: FAIL because the parser does not exist and `filter_rows` has no country threshold parameter.

- [x] **Step 3: Implement compact threshold parsing and deterministic selection**

```python
DEFAULT_COUNTRY_MIN_SPEEDS = {"JP": 10.0, "US": 5.0, "KR": 3.0,
                              "HK": 2.0, "DE": 5.0, "GB": 3.0, "SG": 5.0}

def parse_country_speed_thresholds(value: str) -> dict[str, float]:
    thresholds: dict[str, float] = {}
    for entry in value.split(","):
        country, separator, raw_speed = entry.strip().partition("=")
        if not separator or len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ValueError(f"invalid country speed threshold: {entry!r}")
        speed = float(raw_speed)
        if not math.isfinite(speed) or speed < 0:
            raise ValueError(f"invalid country speed threshold: {entry!r}")
        thresholds[country.upper()] = speed
    return thresholds

# After validation/de-duplication, sort each country by (-speed, latency, index),
# choose rows meeting its map threshold, and append the highest ranked remaining
# valid rows until there are two, before enforcing per_country.
```

- [x] **Step 4: Run filtering tests and the complete suite**

Run: `python -m pytest tests/test_cflip.py -v`

Expected: PASS.

- [x] **Step 5: Commit the filtering change**

```powershell
git add scripts/cflip.py tests/test_cflip.py
git commit -m "feat: filter CFIP rows by country speed"
```

### Task 2: Rename and label the published Chengdu artifact

**Files:**
- Modify: `scripts/cflip.py:145-170,530,572`
- Modify: `tests/test_cflip.py`
- Modify: `.github/workflows/update.yml`
- Modify: `.gitignore`
- Modify: `README.md`
- Rename: `CloudflareSpeedTest_GH.csv` to `CloudflareSpeedTest_CD.csv`

**Interfaces:**
- Consumes: the filtering interface from Task 1.
- Produces: default `CloudflareSpeedTest_CD.csv` output with Chengdu city labels.

- [x] **Step 1: Write failing assertions for the default name and city label**

```python
def test_normalize_cfst_rows_uses_chengdu_measurement_label(tmp_path):
    rows = cflip.normalize_cfst_rows(write_fixture(tmp_path), "HK", 443)
    assert rows[0]["城市"] == "🇨🇳 CD [成都测速#01 tcp-precheck]"


def test_default_output_is_chengdu_csv():
    assert cflip._build_argument_parser().parse_args([]).output == "CloudflareSpeedTest_CD.csv"
```

- [x] **Step 2: Run label/default tests to verify failure**

Run: `python -m pytest tests/test_cflip.py -k "chengdu or default_output" -v`

Expected: FAIL because GH output naming and labels still exist.

- [x] **Step 3: Update production names, workflow staging, and documentation**

```python
CITY_LABEL = "🇨🇳 CD [成都测速#{sequence:02d} tcp-precheck]"
parser.add_argument("--output", default="CloudflareSpeedTest_CD.csv")
```

Use `git mv CloudflareSpeedTest_GH.csv CloudflareSpeedTest_CD.csv`; update all workflow `git add`, ignore, README, and test expectations to the same CD name.

- [x] **Step 4: Run complete tests and a dry-run publication**

Run: `python -m pytest tests/test_cflip.py -v; python scripts/cflip.py --dry-run --fixture-dir tests/fixtures --output $env:TEMP\cfip-cd.csv --proxy-output $env:TEMP\cfip-proxy.txt --state-file $env:TEMP\cfip-state.txt`

Expected: PASS and the temporary CSV header/rows use CFOpt's ten columns and the Chengdu city label.

- [x] **Step 5: Commit and push production-ready artifact changes**

```powershell
git add scripts/cflip.py tests/test_cflip.py .github/workflows/update.yml .gitignore README.md CloudflareSpeedTest_CD.csv
git rm --cached --ignore-unmatch CloudflareSpeedTest_GH.csv
git commit -m "feat: publish Chengdu CFIP artifacts"
git push origin main
```
