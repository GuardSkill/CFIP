# Country speed thresholds and Chengdu artifact naming

## Purpose

Publish CFIP measurements from the Chengdu Windows runner without retaining
slow results that are not useful for a given target country.  The published
CSV remains compatible with CFOpt.

## Published artifact identity

The generated CSV is named `CloudflareSpeedTest_CD.csv`.  Its `城市` field
identifies the measurement origin as `🇨🇳 CD [成都测速#NN tcp-precheck]`.
The workflow stages this new artifact and no longer stages the former
`CloudflareSpeedTest_GH.csv` file.

## Country speed policy

Speed values are measured and compared in MB/s.  The default country policy
is:

| Country | Minimum download speed |
| --- | ---: |
| JP | 10 MB/s |
| US | 5 MB/s |
| KR | 3 MB/s |
| HK | 2 MB/s |
| DE | 5 MB/s |
| GB | 3 MB/s |
| SG | 5 MB/s |

Countries not in this table retain the existing default of `0.03 MB/s`.

For each country, valid and de-duplicated results are ranked by download
speed descending, then latency ascending, then original input order.  All
rows at or above that country's speed threshold are eligible.  If fewer than
two rows meet the threshold, the fastest remaining valid rows are added until
two rows have been retained or there are no more valid rows for that country.
Existing validity rules (received packet count, loss, latency limit) still
apply to both threshold-qualified and fallback rows.  The existing
`--per-country` cap remains the maximum retained count.

## Configuration and testing

The CLI exposes the country thresholds through a parseable argument so the
workflow defaults are visible and can be overridden without editing Python.
Tests cover all three cases: normal threshold filtering, fallback to two
sub-threshold results, and no fallback for invalid results.  Tests also
assert the new default output name and Chengdu city marker.

## Migration

The current generated GH CSV is renamed to the CD artifact in the repository
so consumers have one current CSV.  Documentation and ignore/staging rules
are updated consistently.  `proxyip-best.txt` and subscription configuration
assets are unchanged.
