# External public data: results (DL-59, step 6)

**Status:** the pre-registered analyses (E1 to E4, with E5 skipped by its own rule) have been run on the loaded public data (8 Oct 2026). This is the one-page overview. Rules: [`external_data_protocol.md`](external_data_protocol.md). Full tables, charts and the stored verdicts: [`notebooks/11_external_analysis_results.ipynb`](../notebooks/11_external_analysis_results.ipynb) and, for the gap diagnosis, [`notebooks/12_gap_diagnosis.ipynb`](../notebooks/12_gap_diagnosis.ipynb). Decisions: DL-59 and DL-60 in the [decision log](decision_log.md).

**What was done.** About 24 GB of public CMS, CDC, SEC and FDA files were downloaded, profiled and loaded into a separate local database (`data/processed/external.db`, 27 tables, 38 reconciliation checks pass). The rules were fixed in the protocol before any analysis ran. Each rule writes one row to `gold_ext_verdicts` under a run id; the final run was done twice with identical results.

## Results

| Test | Question | Result | In numbers |
|---|---|---|---|
| **E1** | Does Medicare rank the specialties like IQVIA? | **Disagrees** | rank correlation 0.35 (95% interval 0.18 to 0.44) against 0.6 needed; 1 of the top 3 shared (2 needed). Same when limited to age 65+ (0.38) or without the weakest crosswalk match (0.42) |
| **E2a** | Did Medicare adoption peak in 2021 or 2022 and ease by 2024? | **Consistent** (weakened test) | 1.08% (2019), 1.51%, **1.78% (2021)**, 1.73%, 1.58%, 1.47% (2024) |
| **E2b** | Does company net sales move in the same direction as IQVIA Zilretta visits? | **Inconsistent** (weakened test) | agree in 9 of 20 quarters (45%) against 70% needed; the gap in size opens in 2023 and is largest in 2024 (see the follow-up below) |
| **E3** | Are the state rankings stable enough to use? | **Usable** | 2022 against 2024 rank correlation 0.92 (interval 0.77 to 0.94, 51 states); 0.97 after removing the Medicare Advantage effect |
| **H1** | Did promotion (Open Payments) fall before the turn? | **Supported**, but no lag link | sharpest 6-month fall -81%; no lag from 0 to 6 months passes the correction (smallest p = 0.11) |
| **H2** | Did Zilretta's price relative to triamcinolone change by 10%? | Supported (**not a clean test**) | ratio +11.7%; driven by the comparator's limit falling 14% (Zilretta's fell 4%) |
| **H3** | Did the end of pass-through status (31 Mar 2021) cause the turn? | **Not supported** | the detected break (March 2022) is 11 months later; decided by the existing result |
| **H4** | Is the Mar to Jul 2024 dip a data-capture artefact? | **Rule met when pooled; fails month by month** | Zilretta -47%, corticosteroids -25%, NSAID/OTC -16%; July 2024 NSAID/OTC only -2% |
| **E5** | Add lagged promotion to the forecast? | **Skipped** | no lag was significant |

## Follow-up: why do sales and visits diverge? (DL-61, DL-62; notebook 12)

Four checks written down before any code ran (protocol section 9), comparing 2021 with 2024; the thresholds were not changed afterwards.

| Check | Question | Result | In numbers |
|---|---|---|---|
| **D1** | Is it the population (age, payer mix)? | **Not supported** | IQVIA 65+ visits -34% while Medicare Zilretta patients per 1,000 Original Medicare beneficiaries are -1.5%: a 32-point gap (15 allowed). The fall is larger under 65 (-58%), but the 65+ gap alone rules the idea out |
| **D2** | Did Zilretta move to a setting IQVIA covers poorly? | **Not supported** | Medicare Zilretta is almost all office-based; the facility share went from 0.05% to 0.09% |
| **D3** | Is each visit worth more? | **Supported on direction only, weakly** | sales per IQVIA visit +94%; Medicare services per patient +15%, payment per service -4%, so value per Medicare patient +10%, about a ninth of the 94%; not supported with 2022 as base |
| **D4** | Is the fall concentrated? | **Concentrated** | orthopedic surgery and physician assistants are 64% of the net fall of 11,256 visits; Medicare providers fall in orthopedic surgery (651 to 460) but rise for physician assistants (240 to 268) |
| **Overall** | Does any of D1 to D3 hold? | **Supported, via D3 only** | no tested explanation accounts for the size of the gap |

**Timing (descriptive, seen after the rules were fixed).** IQVIA Zilretta visits and company sales do not diverge in size through 2022 (visits +7%, sales +3%); the gap opens in 2023 (-14% against +5%) and is largest in 2024 (-36% against +6%). The corticosteroid and NSAID categories also fell in 2024 (-18% and -8%), so a data-capture or coverage problem in 2024 is a live possibility, though Zilretta fell about twice as much as the others. This correction replaces an earlier statement (notebook 11) that the 2024 problem could not explain the gap.

**Not tested:** panel coverage of Zilretta settings or specialties, and company-side causes (inventory, gross-to-net adjustments, channel mix).

## What to take from it

1. **The IQVIA specialty ranking is not confirmed by Medicare billing.** The two sources measure different populations (Original Medicare, mostly 65 and over, against IQVIA's panel), so this says the ranking depends on the population. It does not say IQVIA is wrong.
2. **The most important finding is E2b, and the follow-up narrows it.** Company net sales kept rising while IQVIA Zilretta visits fell by about half from their 2022 peak, and Medicare adoption eased only by about a sixth. By full years the gap opens in 2023 (IQVIA visits -14%, sales +5%) and is largest in 2024 (-36% against +6%), a year that overlaps the early-2024 IQVIA dip. The follow-up ruled out the population and the care setting as explanations, found that value per visit is far too small, and found the fall concentrated in two specialty groups. The size of the IQVIA decline should not be quoted as a market fact until IQVIA is asked about panel coverage.
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
