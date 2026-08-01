# CFIP

CFIP publishes CloudflareSpeedTest results measured from GitHub Actions in the
same ten-column UTF-8 CSV format used by CFOpt. It also publishes the existing
`proxyip-best.txt` format used by the bundled Subconverter configurations.

Generated artifacts:

- `CloudflareSpeedTest_GH.csv` — CFOpt-compatible measurements whose city label
  explicitly identifies GitHub Actions, for example
  `🇭🇰 HK [GitHub Actions#01 tcp-precheck]`.
- `proxyip-best.txt` — one `host:port#COUNTRY` entry per line.
- `.cflip/last-success.txt` — the timezone-aware timestamp of the last complete
  publish, committed by the workflow to enforce the interval gate.

## Pipeline

The dependency-free Python runner downloads candidates from
`https://zip.cm.edu.kg/ip.zip` and the country files at
`https://zoroaaa.github.io/cf-bestip`. It deterministically caps each pool,
keeps the fastest TCP-reachable candidates, and submits only those survivors to
XIU2 CloudflareSpeedTest. Measurements must have at least one received probe,
less than 1% loss, no more than 420 ms average latency, and at least 0.03 Mb/s
after CFOpt-compatible rounding. Results are deduplicated and capped per
country before publication.

Downloads and TCP connections occur only during a real publisher run. Tests
and dry-runs use committed fixtures; they do not access public services or run
CFST. A failed source download, CFST invocation, proxy generation, or empty
filtered result exits nonzero before replacing the existing published files.

## Schedule and state gate

`.github/workflows/update.yml` is triggered every 30 minutes, but
`scripts/cflip.py` exits successfully before doing network work when the
committed success timestamp is younger than 150 minutes. The state advances
only after the CSV and proxy list have both been produced. The workflow grants
only `contents: write`, uses the built-in `GITHUB_TOKEN`, and commits only the
two generated artifacts and the success-state file.

## Runner selection

Scheduled runs and manual runs default to GitHub's `ubuntu-latest` runner.
Manual dispatch exposes a constrained `runner_label` choice:

- `ubuntu-latest` uses a GitHub-hosted runner.
- `self-hosted` uses a trusted self-hosted runner carrying GitHub's standard
  `self-hosted`, `linux`, and `x64` labels. Every other input value falls back
  to `ubuntu-latest`; no input is interpolated directly into `runs-on`.

The self-hosted runner must be Linux x64, have outbound HTTPS/TCP access, and
provide Bash, `curl`, `tar`, Git, and Python setup support. Register it with the
repository or an allowed organization runner group, then select `self-hosted`
from **Actions → Update CFIP artifacts → Run workflow**. The choice input is an
allowlist rather than a free-form expression, so a dispatch cannot route the
job to an arbitrary label. To use a custom label, add it deliberately to both
the workflow choice list and the reviewed `runs-on` mapping.

Do not place personal tokens in `.env` or repository files. A self-hosted
runner should be dedicated to trusted repositories and kept patched because it
executes network-downloaded CFST code.

## Local verification

Install the development dependency and run the fixture suite:

```console
python -m pip install -r requirements-dev.txt
python -m pytest -v
```

Exercise the full orchestration without network access:

```console
python scripts/cflip.py --dry-run --fixture-dir tests/fixtures
```

The dry-run prints each intended CFST command and writes fixture-derived output
files. Use temporary `--output`, `--proxy-output`, and `--state-file` paths if
you do not want to update files in the working tree. `--now` accepts a
timezone-aware ISO-8601 value for reproducible gate checks.

Generated outputs and `.cflip/` runtime state are ignored for local work. The
workflow force-adds only `CloudflareSpeedTest_GH.csv`, `proxyip-best.txt`, and
`.cflip/last-success.txt` when publishing, so downloaded binaries and temporary
runtime files cannot be included in its artifact commit.

For a real local run, install a Linux x64 `cfst` binary and either export
`CFST_PATH` or pass `--cfst-path`. Run `python scripts/cflip.py --help` for
source, country, port, threshold, and output overrides.
