# External public data: results (DL-59, step 6)

**Status:** the pre-registered analyses (E1 to E4, with E5 skipped by its own rule) have been run on the loaded public data (8 Oct 2026). This is the one-page overview. Rules: [`external_data_protocol.md`](external_data_protocol.md). Full tables, charts and the stored verdicts: [`notebooks/11_external_analysis_results.ipynb`](../notebooks/11_external_analysis_results.ipynb). Decisions: DL-59 and DL-60 in the [decision log](decision_log.md).

**What was done.** About 24 GB of public CMS, CDC, SEC and FDA files were downloaded, profiled and loaded into a separate local database (`data/processed/external.db`, 27 tables, 38 reconciliation checks pass). The rules were fixed in the protocol before any analysis ran. Each rule writes one row to `gold_ext_verdicts` under a run id; the final run was done twice with identical results.

## Results

| Test | Question | Result | In numbers |
|---|---|---|---|
| **E1** | Does Medicare rank the specialties like IQVIA? | **Disagrees** | rank correlation 0.35 (95% interval 0.18 to 0.44) against 0.6 needed; 1 of the top 3 shared (2 needed). Same when limited to age 65+ (0.38) or without the weakest crosswalk match (0.42) |
| **E2a** | Did Medicare adoption peak in 2021 or 2022 and ease by 2024? | **Consistent** (weakened test) | 1.08% (2019), 1.51%, **1.78% (2021)**, 1.73%, 1.58%, 1.47% (2024) |
| **E2b** | Does company net sales move in the same direction as IQVIA Zilretta visits? | **Inconsistent** (weakened test) | agree in 9 of 20 quarters (45%) against 70% needed; sales rose after 2022 while IQVIA visits fell by about half |
| **E3** | Are the state rankings stable enough to use? | **Usable** | 2022 against 2024 rank correlation 0.92 (interval 0.77 to 0.94, 51 states); 0.97 after removing the Medicare Advantage effect |
| **H1** | Did promotion (Open Payments) fall before the turn? | **Supported**, but no lag link | sharpest 6-month fall -81%; no lag from 0 to 6 months passes the correction (smallest p = 0.11) |
| **H2** | Did Zilretta's price relative to triamcinolone change by 10%? | Supported (**not a clean test**) | ratio +11.7%; driven by the comparator's limit falling 14% (Zilretta's fell 4%) |
| **H3** | Did the end of pass-through status (31 Mar 2021) cause the turn? | **Not supported** | the detected break (March 2022) is 11 months later; decided by the existing result |
| **H4** | Is the Mar to Jul 2024 dip a data-capture artefact? | **Rule met when pooled; fails month by month** | Zilretta -47%, corticosteroids -25%, NSAID/OTC -16%; July 2024 NSAID/OTC only -2% |
| **E5** | Add lagged promotion to the forecast? | **Skipped** | no lag was significant |

## What to take from it

1. **The IQVIA specialty ranking is not confirmed by Medicare billing.** The two sources measure different populations (Original Medicare, mostly 65 and over, against IQVIA's panel), so this says the ranking depends on the population. It does not say IQVIA is wrong.
2. **The most important finding is E2b.** Company net sales rose from 2022 while IQVIA Zilretta visits fell by about half, and Medicare adoption eased only by about a sixth. The size of the IQVIA decline should not be quoted as a market fact until the gap is understood (panel coverage, value per visit or payer mix are all open and untested).
3. **A stable state list exists** (E3), led by Michigan, Arizona, Florida, Minnesota and Indiana by room to grow. Stable is not the same as explained, and the reason for the gaps is untested.
4. **Promotion fell before the turn, but nothing links it month by month** (H1), and the fall coincides with the acquisition of Flexion by Pacira (19 Nov 2021).
5. **None of this is causal.** Several tests are weakened or not clean, as the protocol states; the choices made while running the analyses are in protocol section 8.

## Limits

- Medicare data is Original Medicare only, has no diagnosis, and hides cells of ten or fewer patients; it counts claims, not visits.
- Company sales are national and all-payer; the fourth quarter of each year is derived from the annual figure, and Q4 2021 spans the acquisition.
- The Open Payments files are the 2026 republication of every year.
- Kentucky and Pennsylvania have no arthritis-prevalence value (the CDC file has no rows for them).
- The external database is local and git-ignored; only the code, the protocol, the reference data and the documents are in the repository. Copying the small result tables into the published warehouse is not done (the serving layer is a later phase).

## Reproducing

```bash
PYTHONPATH=src python -m oa_market_intelligence.external
PYTHONPATH=src python -m oa_market_intelligence.external.analysis
```

The first loads the raw files from `data/raw/New Datasets` (about 13 minutes for a full rebuild; local only); the second runs all analyses and prints the verdicts. Tests: `PYTHONPATH=src python -m pytest tests/test_external_*.py` (synthetic cases with planted effects; they do not need the raw data).
