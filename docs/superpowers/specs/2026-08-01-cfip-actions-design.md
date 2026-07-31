# CFIP GitHub Actions Design

## Goal

Build `GuardSkill/CFIP` as an independently scheduled, GitHub Actions-hosted
Cloudflare candidate publisher.  It must publish a single CSV that is import
compatible with CFOpt, generate `proxyip-best.txt` in the existing line format,
and carry the current CFOpt Subconverter configuration files and `rules/` tree.

## Outputs

The repository root will contain:

* `CloudflareSpeedTest_GH.csv` with the exact CFOpt header and field order:
  `IP地址,端口,数据中心,城市,TLS,已发送,已接收,丢包率,平均延迟,下载速度(MB/s)`.
* `proxyip-best.txt`, whose lines remain `host:port#COUNTRY`.
* `CFOpt_Subconverter.ini`, `CFOpt_Subconverter_lite.ini`,
  `CFOpt_Subconverter_lite_cmliussss.ini`, and a copied `rules/` directory.

The `城市` value will identify the GitHub source while retaining CFOpt's
country-oriented notation, for example `🇭🇰 HK [GitHub Actions#01 tcp-precheck]`.

## Candidate and measurement pipeline

1. Download candidates from the same `ip.zip` and `cf-bestip` sources used by
   CFOpt, for the configured country set and supported Cloudflare ports.
2. Apply bounded, deterministic per-country sampling.
3. Run an asynchronous TCP connect precheck against each `IP:port`; rank
   reachable candidates by connect latency and retain the best candidates per
   country/port.
4. Run CloudflareSpeedTest only on that retained set.  It measures latency,
   packet loss, and an HTTP download, enabling authentic CFOpt-compatible CSV
   fields rather than inventing values from a TCP probe.
5. Enforce the same acceptance semantics as CFOpt: received probes >= 1, loss
   below 1%, bounded latency, a nonzero configured speed floor, deduplication,
   and a per-country output cap.
6. Build `proxyip-best.txt` using the CFOpt generator's country list, line
   format, TCP ranking behavior, limits, and source.

GitHub Actions results describe connectivity from the GitHub-hosted runner,
not from Chengdu or Beijing.  The filename and city marker make this explicit.

## Scheduling and publishing

One workflow triggers every 30 minutes and supports manual dispatch.  The
runner reads a committed success-state file and exits successfully until 150
minutes have elapsed since the last completed publish.  This provides the
requested 2.5-hour cadence without relying on an unavailable 150-minute cron
expression.

The workflow runs on `ubuntu-latest`, obtains the CFST binary during the job,
and commits only changed generated artifacts and state using the built-in
`GITHUB_TOKEN` with `contents: write`.  No local token or `.env` value is
committed.  Candidate or tool download failure leaves the previous published
CSV intact and makes the run fail with diagnostic logs.

## Structure

* `scripts/run_cflip.py`: orchestration, source parsing, TCP precheck, CFST
  invocation, CSV merge/filter, and interval state handling.
* `scripts/generate_proxyip_best.py`: CFOpt-compatible proxy IP generator.
* `.github/workflows/update.yml`: schedule, dependency setup, execution, and
  change-only commit.
* `tests/`: parser, formatting, filtering, and interval tests with stub CFST;
  no test needs the public network.

## Verification

Tests will assert the CSV header and row layout byte-for-byte against CFOpt's
contract, verify per-country city labels and deduplication, verify TCP
preselection before CFST invocation, validate proxy output lines, and verify
the 150-minute gate.  A workflow dry-run will execute against fixture inputs
without committing.
