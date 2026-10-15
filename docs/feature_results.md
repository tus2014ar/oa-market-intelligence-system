# Feature groups: results (DL-76)

The test is the one fixed before any build or run in [`feature_engineering_plan.md`](feature_engineering_plan.md) (DL-75). Nothing was changed after the first result. It was run twice from the same code, data and seed (0; 2,000 bootstrap draws), and the two output files are byte-identical. Raw numbers: [`data/reference/feature_group_results.json`](../data/reference/feature_group_results.json); runner: `features/validate.py` (`python -m oa_market_intelligence.features.validate --run`). Notebook: [14](../notebooks/14_feature_groups_test.ipynb).

## In one table

| Target | Groups tested | Pass the rule | Robust |
|---|---|---|---|
| **T2** segment share (46 test months) | 7 | **G4** | **G4** |
| **T1** national share (48 test months) | 5 | none | none |

## T2: segment share

The reference is the Phase 4 serving logistic regression (log-loss 0.11274 per visit). A group passes if the lower end of its paired gain is above zero at the 0.71th percentile (0.05 divided by 7 groups). Positive is better.

| Group | Gain over B0 | Lower end | Share of log-loss | Months better |
|---|---|---|---|---|
| G1 Recent history, extended | -4.2e-07 | -2.6e-05 | -0.000% | 24 / 46 |
| G2 Calendar and seasonality | +2.8e-05 | -8.2e-06 | +0.025% | 29 / 46 |
| G3 Segment history and pooling | +4.7e-05 | -7.3e-06 | +0.042% | 25 / 46 |
| G4 Market and specialty context | +6.5e-05 | +4.4e-05 | +0.058% | 42 / 46 |
| G5 Competitor volumes | +1.0e-05 | -4.1e-05 | +0.009% | 23 / 46 |
| G6 Outside signals (as-of) | -3.2e-05 | -1.1e-04 | -0.028% | 20 / 46 |
| G7 Data-quality flags | +5.5e-06 | -1.8e-05 | +0.005% | 24 / 46 |

Gain with its lower end in brackets, in the two robustness runs:

| Group | Main | COVID removed | 2024 dip removed | Verdict |
|---|---|---|---|---|
| G1 Recent history, extended | -4.2e-07 (-2.6e-05) | +1.5e-05 (-6.7e-06) | -6.7e-06 (-4.4e-05) | no pass |
| G2 Calendar and seasonality | +2.8e-05 (-8.2e-06) | +3.7e-06 (-2.4e-05) | +3.0e-05 (-7.0e-06) | no pass |
| G3 Segment history and pooling | +4.7e-05 (-7.3e-06) | +5.0e-05 (-1.6e-06) | +4.9e-05 (-3.7e-06) | no pass |
| G4 Market and specialty context | +6.5e-05 (+4.4e-05) | +7.3e-05 (+4.8e-05) | +6.5e-05 (+4.3e-05) | **robust** |
| G5 Competitor volumes | +1.0e-05 (-4.1e-05) | -4.6e-05 (-1.5e-04) | +7.3e-06 (-4.7e-05) | no pass |
| G6 Outside signals (as-of) | -3.2e-05 (-1.1e-04) | -3.9e-05 (-1.2e-04) | -2.7e-05 (-1.2e-04) | no pass |
| G7 Data-quality flags | +5.5e-06 (-1.8e-05) | -3.0e-06 (-2.2e-05) | +4.6e-06 (-1.2e-05) | no pass |

- **G4 is the only group that passes, and it is robust.** It holds in the main run and with the COVID months and the 2024 dip removed. Its gain is +6.5e-05 on 0.1127, about 0.06% of log-loss, and it is better in 42 of 46 test months. That reproduces the archived attempt's specialty-momentum result almost exactly (+6.5e-5), which is some reassurance that it is real, not a fluke of one run.
- **No combined model was tested**, because only one group is robust.
- **G3 is the nearest miss** (pooling and history: +4.7e-5, lower end -7.3e-6). It fails at the stricter 0.71th percentile.
- **The two new ideas did not help in the main run.** Competitor volumes (G5) add +1.0e-5 with a lower end of -4.1e-5, and the data-quality flags (G7) add +5.5e-6. Outside signals (G6) are slightly harmful.
- **The size is tiny.** The best group improves log-loss by about 0.06%. This is the same order of magnitude as the archived attempt.

## T1: national share

The plain ridge and the ridge with each group, against "same as last month" (what serves). A group passes if the lower end of its paired MAE gain over last month is above zero at the 1.0th percentile (0.05 divided by 5 groups).

| Group | MAE (pp) | Against last month | Lower end | Against plain ridge | Verdict |
|---|---|---|---|---|---|
| Last month (serves) | 0.1246 | - | - | - | - |
| Plain ridge | 0.1466 | -0.0220 | - | - | - |
| G1 Recent history, extended | 0.1564 | -0.0318 | -0.0815 | -0.0098 | no pass |
| G2 Calendar and seasonality | 0.1644 | -0.0398 | -0.0869 | -0.0178 | no pass |
| G5 Competitor volumes | 0.1391 | -0.0145 | -0.0400 | +0.0075 | no pass |
| G6 Outside signals (as-of) | 0.1641 | -0.0396 | -0.0901 | -0.0175 | no pass |
| G7 Data-quality flags | 0.1464 | -0.0219 | -0.0532 | +0.0002 | no pass |

**No group passes, and every ridge is worse than "same as last month".** Competitor volumes (G5) come closest: they improve the plain ridge by +0.0075 pp (interval crossing zero) but are still 0.0145 pp worse than last month. With the 2024-dip months removed from scoring the picture is the same. The national forecast stays at the serving rule.

## Sensitivity: data through t-2 (not part of the pass rule)

IQVIA publishes a month about 40 days after it ends, so a real-time user would only have data through *t-2*. Every IQVIA-derived feature is lagged one more month (rows without a previous-month row are dropped: 8112 of 8450 remain).

| Group | Gain over B0 | Lower end | Share of log-loss | Months better |
|---|---|---|---|---|
| G1 Recent history, extended | +7.7e-06 | -2.0e-05 | +0.007% | 24 / 45 |
| G2 Calendar and seasonality | +3.3e-05 | -7.0e-06 | +0.029% | 28 / 45 |
| G3 Segment history and pooling | +7.0e-05 | -3.9e-06 | +0.062% | 26 / 45 |
| G4 Market and specialty context | +3.5e-05 | +2.1e-05 | +0.031% | 38 / 45 |
| G5 Competitor volumes | +7.9e-05 | +2.6e-05 | +0.070% | 28 / 45 |
| G6 Outside signals (as-of) | -2.3e-05 | -1.0e-04 | -0.021% | 23 / 45 |
| G7 Data-quality flags | +2.7e-05 | -4.5e-06 | +0.024% | 29 / 45 |

- **G4 still passes** (+3.5e-05, about 0.031% of log-loss).
- **G5 (competitor volumes) also passes here**: +7.9e-05, about 0.07%, better in 28 of 45 months. It did not pass in the main run. This is outside the pass rule and was run on one scenario only, so it is a hypothesis for the modelling stage, not a finding. (The runner's printed summary calls both passes "robust" and ran a G4 + G5 combined test, because this run has a single scenario; that label is a by-product and is not a robustness result.)
- **T1 is still all no-pass** (table below).

| Group | MAE (pp) | Against the two-step baseline | Lower end |
|---|---|---|---|
| Two months ago (baseline) | 0.1567 | - | - |
| G1 Recent history, extended | 0.2141 | -0.0574 | -0.1220 |
| G2 Calendar and seasonality | 0.2013 | -0.0446 | -0.1057 |
| G5 Competitor volumes | 0.1692 | -0.0124 | -0.0418 |
| G6 Outside signals (as-of) | 0.1939 | -0.0371 | -0.1060 |
| G7 Data-quality flags | 0.1804 | -0.0237 | -0.0710 |

## What this says

1. **Specialty momentum (G4) is the one input that earns its place for segment share**, at about 0.06% of log-loss. It is the specialty's share at *t-1* and its change over three months.
2. **Everything else is a null in the main run**, including the two untested ideas (competitor volumes and data-quality flags) and the outside signals. This matches the EDA: 72 months, one regime change and no monthly drivers.
3. **The national forecast does not improve with any group**, so "same as last month" remains the rule to beat.
4. **One hypothesis to carry forward:** competitor volumes helped under a realistic *t-2* lag. It is worth one pre-registered look in modelling, not a conclusion.

## Disclosures

- G2, G3 and G6 were EDA-informed (see the plan), so they are not a clean test.
- T1 robustness has two scenarios (main and dip removed); the COVID months precede the first test month, so that scenario does not apply. G7 cannot be judged by removing the months it flags and was judged on the main run only.
- The pass rule is a significance rule; the plan set no minimum effect size, so "robust" is not "worth a change": that is a decision (DL-76).
