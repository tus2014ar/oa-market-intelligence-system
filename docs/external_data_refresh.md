# Rebuilding and refreshing the public data

The public data (CMS, CDC, SEC, FDA) is kept **local**: about 24 GB of raw files in `data/raw/New Datasets/` and the 134 MB database `data/processed/external.db`, both git-ignored. What *is* in the repository is everything needed to rebuild them: the manifest of every file (source link, size, SHA-256), the download scripts, the loaders and the checks. This page says how.

## 1. Restore the raw files (same data again)

`data/reference/external_manifest.jsonl` lists the 171 raw files. The restore tool downloads any file that is missing and checks it against the manifest; files already there that match are left alone.

```bash
PYTHONPATH=src python -m oa_market_intelligence.external.fetch --dry-run   # what would happen
PYTHONPATH=src python -m oa_market_intelligence.external.fetch             # do it
```

The 171 files fall into four kinds, and the tool says which are left:

| Kind | Files | How they are restored |
|---|---|---|
| Plain links (CMS, CDC, FDA, NUCC, policy PDFs) | 51 | downloaded by the tool |
| SEC filings | 86 | downloaded by the tool **if** `SEC_CONTACT_EMAIL` is set to your own address (the SEC requires a contact in the request; it is read from the environment and never stored in the repository) |
| Filtered Medicare Part B provider pull, 2019 to 2023 | 5 | `python scripts/external_data/pull_partb_provider.py` (pages through the CMS data API for the approved billing codes) |
| Downloaded by hand (licence pages and large files) | 29 | listed with their source page; download them into the same folder names, then run the tool again to check them |

A file that **differs** from the manifest is kept and reported, never deleted: a source may have revised a file since it was recorded. Investigate before loading (the loaders also refuse a file that does not match, so a changed file cannot be loaded silently). The tool exits with status 0 only when all 171 files are present and match.

## 2. Rebuild the database and check it

```bash
PYTHONPATH=src python -m oa_market_intelligence.external --rebuild --verify   # load everything, then run the 38 checks (about 13 minutes or more)
PYTHONPATH=src python -m oa_market_intelligence.external --check-only          # re-run the checks without loading anything
PYTHONPATH=src python -m oa_market_intelligence.external.analysis              # E1 to E4 and the gap diagnosis
```

Every load is recounted independently from the raw files; the analyses are seeded and give identical results on rerun. Results are in `gold_ext_verdicts`, one row per rule under each run id.

## 3. When a **new release** arrives (not the same data)

A new year or quarter is not a refresh. It changes numbers the protocol fixed in advance, so it is a decision, not a rerun:

1. Add the new files with the scripts in `scripts/external_data/` (extend their year lists) or by hand, then run `scripts/external_data/record_manual_downloads.py` for any hand downloads. Commit the updated manifest.
2. Re-profile the new files (notebook 10 pattern) and update the expected targets in `external/verify.py` and the profile files in `data/reference/external_profile/` on purpose; a changed target is a visible diff.
3. Extend the reference year and quarter ranges in `external/loaders/reference.py` if needed, and rebuild.
4. The analysis windows (E1 2020 to 2024, E2b quarters, the 2021 against 2024 comparison, the 2024 against 2023 months) are fixed in the protocol. Moving them is a **deviation**: write it in the decision log with the reason and show both versions.

## What is not automated

The public data is not part of the monthly publish step, the GitHub workflow or the website. The monthly pipeline covers the IQVIA data only (see [`deployment.md`](deployment.md)). Copying the small public result tables into the published warehouse, and showing them on the site, is a later-phase step.
