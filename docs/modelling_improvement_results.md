# Modelling improvement round: results (DL-74)

The rules are the ones fixed before any build or run in [`modelling_improvement_plan.md`](modelling_improvement_plan.md) (DL-73). Nothing was changed after the first result. The round was run once and repeated from the same code, data and seed (0; 2,000 bootstrap draws); the two output files are byte-identical. Raw numbers: [`data/reference/modelling_improvement_results.json`](../data/reference/modelling_improvement_results.json); runner: `modeling/improvement_round.py` (`python -m oa_market_intelligence.modeling.improvement_round --run`). Notebook: [15](../notebooks/15_modelling_improvement_round.ipynb).

| Item | Verdict by the plan's rule | In one line |
|---|---|---|
| **M1** bias correction | **Passes, robust** | Bias falls from +6.9% to +1.4%; log-loss gain about 0.05%; the correction over-corrects the smallest segments |
| **M4** national intervals (ACI) | **Fails** | Same 95.8% coverage, 10% wider than the served intervals, not narrower |
| **M4** segment intervals | **Fails** | Overall coverage 89.2%, but the over-300-visit bucket is only 83.3% |
| **M5** CUSUM monitor | **Passes** | Detects 0.2 pp a month drifts within 12 months in every start; does not flag the real, slower decline |
| **M5** decision metric | Reported only | The model and the last-month baseline give the same top-10 overlap (0.839) |
| **M6** competitor shares | Descriptive | Zilretta visits -46% from 2022 to the latest 12 months, the category -19.5%; triamcinolone IR gained share |

## M1: bias correction for the segment model

The serving logistic regression plus a logit offset fitted on its own predictions of the previous 12 months, at least 3 (46 test months).

| Run | Log-loss gain (lower end, 5th percentile) | Months better | Bias before | Bias after | Passes |
|---|---|---|---|---|---|
| main | +5.4e-5 (+2.3e-5) | 28 / 46 | +6.9% | +1.4% | yes |
| COVID removed | +5.6e-5 (+2.5e-5) | 26 / 43 | +7.2% | +0.8% | yes |
| 2024 dip removed | +5.6e-5 (+2.6e-5) | 25 / 41 | +6.9% | +1.7% | yes |

- **The pass is genuine and the size is small.** The reference log-loss is 0.1127, so the gain is about 0.05%, in the same range as the feature families of DL-72. The bias, which the card states as a limitation, shrinks to within the 3% line in all three runs.
- **The correction is not even.** Bias by size of the segment's visits last month, main run, before and after: under 50 visits -2.0% to **-8.5%**; 50 to 100 +3.5% to -2.4%; 100 to 300 +2.8% to -2.4%; over 300 +7.5% to +1.9%. A single level offset fixes the large segments, where most visits are, and over-corrects the smallest. The overall bias test the plan set does not see this; it is stated here.
- **Reading.** The offset layer does what the plan said (it removes the average bias) and gains almost nothing in log-loss. Whether it is worth adding to the serving model is a decision, not a test result (see below).

## M4: prediction intervals

**National forecast (48 test months).** Adaptive conformal intervals against the served intervals (empirical percentiles of all past changes):

| | Coverage of the 90% interval | Mean width (pp) |
|---|---|---|
| Served (last month, empirical) | 95.8% | 0.639 |
| Adaptive conformal (gamma 0.02) | 95.8% (block-bootstrap 5th to 95th: 91.7% to 100%) | 0.701 (0.628 to 0.756) |

The coverage gate passes (the band is 81.3% to 98.7%), but the width rule needs at least a 10% cut and the result is a **10% increase**. The adaptive interval does not narrow here. Its miscoverage level drifted up from 0.10 to 0.154 over the 48 months (few misses), yet it stays wider than the served interval, which takes the 5th and 95th percentiles of past signed changes while the adaptive one takes a symmetric quantile of the absolute changes. I did not look further into why; it is a failure of the rule as written, not a diagnosis. **Fails.**

**Segments (43 scored test months, 5,157 rows).** Conformal intervals on standardised residuals:

| Bucket (visits last month) | n | Coverage | Mean width | Within 5 points of 90%? |
|---|---|---|---|---|
| all rows | 5,157 | **89.2%** (88.3% to 89.9%) | 0.039 | yes (limit 3 points) |
| under 50 | 881 | 92.9% | 0.061 | yes |
| 50 to 100 | 991 | 93.1% | 0.054 | yes |
| 100 to 300 | 1,487 | 91.5% | 0.041 | yes |
| over 300 | 1,798 | **83.3%** (81.2% to 85.2%) | 0.019 | **no** |

The overall figure is on target and the three smaller buckets are slightly conservative, but the largest segments are under-covered. The scale is the binomial standard error of last month's visits, which ignores the month-to-month movement of the true share in large segments; for them the real error is larger than binomial noise. **Fails** on the bucket rule. The intervals as built should not be shown to a stakeholder as 90% intervals for large segments.

## M5: drift monitor and decision metric

**CUSUM (k = 0.5).** The threshold is the smallest on the grid {3, 4, 5, 6, 8} with a no-drift average run length of at least 48 months: **h = 3** (run length 52.7 months; h = 4 gives 188, h = 5 gives 360, h = 6 gives 439, h = 8 gives at least 474, of which 98% of runs never alarm, so the larger figures are lower bounds). Detection in the DL-54 experiment (37 start months):

| Drift (pp a month) | Detected within 12 months | Within 6 months | Median delay (months) |
|---|---|---|---|
| 0.05 | 0% | 0% | none |
| 0.1 | 13.5% | 0% | 9 |
| **0.2** | **100%** | 73% | 4 |
| 0.3 | 100% | 97% | 2 |

The pass line (at least 80% within 12 months at 0.2 pp a month, where the current alarm detects 0%) is met comfortably. Two limits, stated: the no-drift run length of 52.7 is only just above the 48 the rule asked for (about one false alarm in four to five years on average), and on the **real series the CUSUM raised no alarm at all**: it did not flag the 2022 to 2025 decline (about 0.03 pp a month) or any earlier month. That is what the plan expected; the monitor is for faster drifts than the one that actually happened.

**Decision metric (no pass or fail).** Among segments with at least 100 visits last month, the overlap of the predicted and realised top 10 by headroom (visits times the gap to the market share): model 0.839, last-month baseline 0.839, difference 0.000 (interval -0.017 to +0.017, 46 months). The two rankings pick the same large segments, because headroom is dominated by visit volume; the model adds nothing to this particular ranking.

## M6: competitor shares (descriptive; shares of product-visits inside IQVIA's panel)

Groups frozen in [`competitor_groups.csv`](../data/reference/competitor_groups.csv): Zilretta, triamcinolone IR (Kenalog, generic triamcinolone and four rarer products), other injectable corticosteroids (the rest of the corticosteroid category, which includes a few tiny oral products under 100 visits in all), other. A patient visit can count under more than one product, and the panel-coverage caveat of DL-62 applies: the size of the fall after 2022 is not confirmed by company sales.

| Group | Visits 2022 | Visits, latest 12 months | Change | Share of injectable-steroid visits, 2022 | Latest 12 |
|---|---|---|---|---|---|
| Zilretta | 29,449 | 15,906 | -46.0% | 3.07% | 2.06% |
| Triamcinolone IR | 529,434 | 477,341 | -9.8% | 55.1% | 61.8% |
| Other injectable corticosteroids | 401,646 | 279,617 | -30.4% | 41.8% | 36.2% |
| **All three** | 960,529 | 772,864 | **-19.5%** | | |

Zilretta's visits fell about 2.4 times as much as the category's. Triamcinolone IR (Kenalog and its generics) holds the largest share and gained about 6.6 points, mostly relative to the other steroids (-5.6 points) and a little relative to Zilretta (-1.0 point). Against triamcinolone IR alone Zilretta's share rose from 4.9% (2019) to 6.5% (2021), then fell to 3.2% (2025 to date). The tables describe; they do not show that any competitor took Zilretta's visits, and part of the category's fall is the early-2024 dip. 2019 (five months) and 2025 (seven months) are partial years, shown with their month counts in the results file.

## What this says

1. **One real, small improvement:** the bias of the segment model can be removed (+6.9% to +1.4%), at a cost in the smallest segments and a log-loss gain of about 0.05%.
2. **The intervals did not improve.** The national intervals are not narrowed by the adaptive method, and the segment intervals are well calibrated overall but not for the largest segments, so none is ready to show.
3. **The monitoring gap can be closed for faster drifts.** The CUSUM catches 0.2 pp a month drifts reliably where the current alarm catches none; it will not flag a decline as slow as the real one early.
4. **The competitor view gives the brand manager a sharper picture:** Zilretta lost proportionally far more than the steroid category, and the biggest competitor group (triamcinolone IR) lost the least.

## Disclosures

- M1, M4 and M5 were designed after seeing the Phase 4 results they target (the 7% bias, the interval coverage and width, the alarm's blind spot); they are fixes, not blind tests.
- M1's per-size bias and the ARL simulation's 480-month cap (a lower bound) are reported above, not hidden.
- The decision metric is dominated by volume and says little about model quality.
- The pass rules are the plan's; the plan set no minimum effect size for M1, so "passes" is not "worth adding": that is the owner's decision (DL-74).
