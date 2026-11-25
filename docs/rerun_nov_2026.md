# The re-run on IQVIA's Nov 2026 data

IQVIA sent a new extract in November 2026. This page records what changed in the data, how the pre-registered modelling plan was re-run on it, and what came out. It does not replace the earlier results documents: those describe the earlier extract (Aug 2019 to Jul 2025) and stay as the record of that run. Where the two differ, this page is the current state and the earlier ones are the history.

## What changed in the data

| | Earlier extract | Nov 2026 pull |
|---|---|---|
| Months | 72 (Aug 2019 to Jul 2025) | 72 (Oct 2020 to Sep 2026) |
| Products / specialties | 160 / 50 | 167 / 53 |
| Visit rows (OA, long form) | 240,773 | 255,827 |
| Segments with a stored forecast | 113 | 122 |
| New months | | 14 (Aug 2025 to Sep 2026) |
| Dropped months | | 14 (Aug 2019 to Sep 2020) |
| Months shared with the earlier extract | | 58, and **restated** |

**IQVIA restated history.** In the 58 shared months about 27% of cells differ from the earlier extract (the national share for Jul 2025 moved from 1.884% to 1.818%). The earlier file said nothing about revisions; this pull shows that they happen. Any number that was computed on the earlier extract can move when it is recomputed on this one, with no change in the code.

**Two files are not what they look like.** The two `Branded Generic` workbooks in this set were built from the instructor's CSV files, and their Grand Total row is a stand-in (the largest value per column), not an IQVIA figure. The real workbooks have been requested. The Place-of-Service sheets write months without a year ("Oct..."); the loader takes the years from the same workbook's pivot sheet only when the labels match it exactly (DL-129).

**New products and specialties.** 12 products were added to the taxonomy (7 OA: ADVIL JR STRENGTH, BETAMETHASONE DIPR, CORTISONE ACE, KENALOG SPRAY, MORPHINE SULF PFS, TRILOAN SUIK, BSP 0820; 5 RA: AVSOLA, DUPIXENT, ENBREL, IMFINZI, TOFIDENCE). The data-quality check, which compares with an older baseline, listed 14 products as new to it. Five earlier products are no longer listed (APAP/CAF, BIOFREEZE W/ILEX, EXCEDRIN TEN HDCHE, OPDIVO, YERVOY). Their treatment categories in `product_taxonomy.csv` and groups in `competitor_groups.csv` were assigned by the project owner's rules and are judgment calls for the small ones (for example BSP 0820 is "unclassified"; KENALOG SPRAY falls into the triamcinolone group by the frozen name rule even though a spray may be topical). Three specialties are new (COLON & RECTAL SURGERY, GERIATRIC PSYCHIATRY, OTOLARYNGOLOGY; 4 visits in all).

## How it was re-run

The plan in [`modelling_plan.md`](modelling_plan.md) (DL-78) was run again without change: the same walk-forward design, horizons 1, 3 and 6, the same inputs (13 numeric inputs and a specialty indicator; the feature groups were not re-tested), the same scenarios, 2,000 bootstrap draws and seed 0. About 3.5 hours for T1 to T3, G5 and the demonstration, then about 5 minutes for the explanation, post-hoc and round 2 runs.

Only the months after July 2025 are new to the models. Everything before them is the same months as before, restated, so a result here is a re-run on a different version of the data, not a fresh out-of-sample test. A separate test on the 14 new months alone has not been run.

## National share (T1)

No model beats "same as last month" at any horizon, as before. It serves at 1, 3 and 6 months. Its error is 0.124, 0.145 and 0.205 points (earlier: 0.125, 0.180 and 0.239). The trained models' 90% ranges cover as little as 72% of months (earlier: 47%).

## Segment share (T2)

| Horizon | Earlier extract | Nov 2026 pull |
|---|---|---|
| 1 month | logistic serves, +0.44% log-loss, better in 46 of 46 months | logistic serves, +0.53%, 46 of 46 |
| 3 months | logistic serves, +0.21% (33 of 44) | logistic serves, +0.32% (41 of 44) |
| 6 months | no model promoted; "last month" serves | **gradient boosting and random forest promoted** (+0.25% and +0.23%, 31 of 41 months); the rule chooses gradient boosting |
| Bias at 1 / 3 / 6 months (logistic) | +5.5% / +10.9% / +18.2% | +1.6% / +3.5% / +11.5% |

**Serving at 6 months stays "last month" (DL-130).** The registered rule chose gradient boosting, but the gain is about 0.25% of log-loss and the project owner kept the baseline for now. The adoption map records this as a written exception and the scoring job refuses any other departure from the rule. Gradient boosting at 6 months from stored forecasts is a possible later change.

**The smaller bias is not proof that the models improved.** The data changed. In the scratch run of the whole pipeline, the forecasts made on the earlier extract ran 13.9% too high at 1 month on the 14 new months (against 5.5% in testing), 11.6% at 3 months and 17.4% at 6 months. The monitoring stage flagged the 1-month drift.

## Decline alert (T3)

The same models are promoted and logistic regression serves at 1 month. At 1 month its PR-AUC is 0.54 against a no-skill 0.23, precision 0.47 and recall 0.67. The post-hoc comparison with a corrected one-line rule (0.51) no longer shows a reliable gain at any horizon: at 1 month the gain is +0.030 with a lower bound of -0.004 (earlier: +0.049, lower bound above zero). The alert stays a supporting flag.

## The hypothesis G5

Still fails at both horizons: the effect is about zero and the intervals span zero.

## What the segment model relies on (SHAP, 1 month)

| Group of inputs | Earlier | Nov 2026 |
|---|---|---|
| The segment's own recent share | 40.4% | 36.8% |
| The segment's long-run share | 14.9% | 17.8% |
| Specialty and market context | 19.0% | 16.9% |
| Age and gender | 8.6% | 11.1% |
| Which specialty it is | 4.1% | 6.4% |
| The whole market's share and its change | 3.5% | 4.7% |
| How big the segment is | 2.2% | 3.1% |
| Time of year | 0.1% | 1.9% |
| The specialty's long-run share | 7.2% | 1.2% |

The ranking at the top is the same; the order of the second and third groups swapped.

## Round 2 (exploratory)

5 of 7 tests pass (earlier: 3). The two new passes are the second bias fix (E1b), and they pass mainly because there is less bias to correct: at 3 months the "corrected" bias is 3.8% against 3.5% before, so the pass shows no harm, not a gain. The first bias fix (E1a) removes most of the bias (1.6% to 0.3% at 1 month, 3.5% to 0.5% at 3 months). The decline-alert test (E2) still fails because the one-line "just spiked" rule is as good. The rank test (E3) passes. All of it stays provisional and none of it is used by the site.

## Public-data checks that depend on IQVIA

Re-run on the new data with the same rules (steps 6a, 6b and the gap analysis): the verdicts are unchanged. D1 (age mix) and D2 (care setting) are not supported, D3 (value per visit) is supported (+91% against +94% before), D4 (concentration) is supported (65% of the net fall of 11,109 visits), E1 (specialty ranking against Medicare) still disagrees (rank correlation 0.38), E3 (state ranking) is usable (0.92).

**Not re-run:** step 6c (promotion against the share, and the detected break), the Mar to Jul 2024 dip analysis, and the feature-group test. They depend on earlier analyses whose stored inputs are not part of the published warehouse. Findings 7, 8, 9 and 13 are marked as earlier-extract in [`findings.md`](findings.md).

## What this does not say

Nothing here shows why the share moved, and the decline's size is still unverified until IQVIA confirms its panel coverage. Visits were flat in 2025 (-0.7%) and Jan to Sep 2026 is 17% above Jan to Sep 2025 in this extract, which nothing tested explains.
