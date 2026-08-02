# Runner Location Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce independent CD/BJ CSVs based on runner OS and synchronize current CFOpt subscription configurations.

**Architecture:** The Python CLI accepts a location token and derives output defaults plus country/speed city labels from it. The workflow supplies CD for Windows and BJ for Linux and stages only that runner's CSV. Config files are copied byte-for-byte from CFOpt.

**Tech Stack:** Python standard library, pytest, GitHub Actions YAML, PowerShell.

## Global Constraints

- Windows publishes `CloudflareSpeedTest_CD.csv` and Linux publishes `CloudflareSpeedTest_BJ.csv`.
- City format is `COUNTRY [LOCATION#NN S.SMB/s]`.
- Final CSV remains exactly ten CFOpt columns.
- `proxyip-best.txt` remains shared.

### Task 1: Location-aware output and city formatting

**Files:** Modify `scripts/cflip.py`, `tests/test_cflip.py`, `.github/workflows/update.yml`, `.gitignore`, `README.md`.

- [ ] Add failing tests asserting `--location CD` writes `CloudflareSpeedTest_CD.csv` and `HK [CD#01 1.5MB/s]`, while `--location BJ` writes BJ equivalents.
- [ ] Run `python -m pytest tests/test_cflip.py -k location -v` and verify failure.
- [ ] Add `--location` with valid `CD`/`BJ` values, derive filenames when output is not explicit, format cities after filtering by country and rounded one-decimal MB/s, and pass the workflow location via environment variable.
- [ ] Run the full fixture suite plus CD and BJ dry runs; inspect both headers and city fields.

### Task 2: Configuration synchronization and publication

**Files:** Copy `CFOpt_Subconverter.ini`, `CFOpt_Subconverter_lite.ini`, `CFOpt_Subconverter_lite_cmliussss.ini` from `H:\Projects\CFOpt`; modify workflow staging and README.

- [ ] Copy only differing INI files and compare `rules/` hashes before copying any differing rule.
- [ ] Stage both CSV artifact names in the workflow without deleting the other location's CSV.
- [ ] Run `git diff --check`, the full tests, commit, and push `main`.
