# Modelling: results (DL-78)

The test is the one fixed before any model was built in [`modelling_plan.md`](modelling_plan.md) (DL-78, with the amendment and the implementation choices in its sections 14 and 15). Nothing was changed after the first result. It was run twice from the same code, data and seed (0; 2,000 bootstrap draws), and the two output files are byte-identical. Raw numbers: [`data/reference/modelling_results.json`](../data/reference/modelling_results.json); runner: `modeling/run_modelling.py` (`python -m oa_market_intelligence.modeling.run_modelling --run`). Notebook: [15](../notebooks/15_modelling_results.ipynb). One check was added **after** the results were seen and is labelled post hoc: [`data/reference/modelling_posthoc.json`](../data/reference/modelling_posthoc.json), `modeling/posthoc.py`.

## In one table

| Task | At 1 month | At 3 months | At 6 months |
|---|---|---|---|
| **T1** national share | baseline serves | baseline serves | baseline serves |
| **T2** segment share | logistic regression promoted and serves; gain about 0.44% of log-loss | logistic regression promoted and serves; gain about 0.21% | none promoted; baseline serves |
| **T3** decline alert (registered) | all three promoted; logistic serves | all three promoted; logistic serves | all three promoted; logistic serves |
| **T3** against a sign-flipped baseline (post hoc) | gain +0.049 PR-AUC, reliable | +0.019, not reliable | +0.004, not reliable |

"Promoted" means the lower end of the paired gain over the best baseline is above zero at the Bonferroni-corrected percentile (1.67th at 1 month, 0.83th at 3 and 6). It is a significance rule with no minimum effect size, so it does not mean the gain is worth acting on. "The baseline serves" means no trained model qualified.

## T1: the national share

Mean absolute error in percentage points. The best baseline is "same as last month" at every horizon. No model is promoted; every model has a lower end below zero.

| Horizon | Test months | Same as last month | Ridge | ETS damped | ETS seasonal | 90% range covers (ridge / ETS / ETS seasonal) | Serves |
|---|---|---|---|---|---|---|---|
| 1 month | 48 | **0.125** | 0.147 | 0.158 | 0.158 | 96% / 90% / 81% | last month |
| 3 months | 46 | **0.180** | 0.280 | 0.213 | 0.232 | 83% / 76% / 76% | last month |
| 6 months | 43 | **0.239** | 0.402 | 0.263 | 0.279 | 47% / 72% / 77% | last month |

- The national share moves slowly and nothing we observe predicts its changes, so its last value is the best forecast. This matches the feature test (DL-76) and the archived attempt.
- The ranges of the trained models miss more often than they should from 3 months out; the ridge's 90% range covers 47% of months at 6 months. Dropping the 2024 dip months leaves the same result.

## T2: the segment share

Binomial log-loss per visit; the best baseline is "last month's share" at every horizon. The table gives each model's gain as a share of the baseline's log-loss, with the number of test months it was better in brackets.

| Horizon | Test months | Baseline log-loss | Logistic | Gradient boosting | Random forest | Promoted | Serves |
|---|---|---|---|---|---|---|---|
| 1 month | 46 | 0.11317 | +0.44% (46) | +0.44% (46) | +0.40% (46) | all three | logistic |
| 3 months | 44 | 0.11210 | +0.21% (33) | +0.16% (35) | +0.17% (36) | all three | logistic |
| 6 months | 41 | 0.11033 | +0.12% (25) | +0.03% (24) | -0.01% (23) | none | last month |

- **A small, real gain at 1 and 3 months, none at 6.** At 1 month the three models are better in every one of the 46 test months. The lower end of the logistic regression's gain is +4.4e-04 at 1 month, +1.1e-04 at 3 months and -3.9e-05 at 6 months.
- **The extra complexity buys nothing.** The gradient boosting and the forest tie with the logistic regression, so the simplest promoted model serves.
- **Robust to what is removed.** The same pattern holds without the COVID months, without the 2024 dip months and without PEDIATRICS (promoted at 1 and 3, none at 6, whatever is removed). At 3 months the gain is 0.17 to 0.27% of log-loss in every scenario.
- The 1-month gain is in line with the archived attempt's "about 0.4%". G4 was chosen on these same test months (DL-76), so the gain is slightly optimistic.

### The forecasts run too high

Visit-weighted predicted over observed share, minus one (calibration slope in brackets).

| Horizon | Logistic | Gradient boosting | Random forest |
|---|---|---|---|
| 1 month | +5.5% (1.09) | +4.3% (1.06) | +3.9% (1.07) |
| 3 months | +10.9% (1.09) | +10.7% (1.07) | +9.8% (1.10) |
| 6 months | +18.2% (1.09) | +18.4% (1.06) | +18.5% (1.10) |

Every model over-predicts, and the bias grows with the horizon. This is the bias the archived attempt found (about +7% while the market was falling); the new inputs did not remove it. By segment size at 1 month it is about +6% for segments with 200 or more visits and slightly negative (-4.6%) for those under 50. Calibration is reported, not a pass or fail test, but a user of the 3-month forecast should read it as optimistic.

## T3: the decline alert

The label is 1 when a segment's share is at least 30% below its share at the origin; only segments with a positive origin share and at least 100 category visits in the origin month are scored. PR-AUC is read against the decline share, which is also what "always alert" scores. The best baseline is "small segment" at every horizon.

| Horizon | Test months | Scored rows | Declines | Decline share | Recent drop | Small segment | Logistic | Gradient boosting | Random forest | Logistic precision / recall / F1 (alerts) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 month | 46 | 3,183 | 729 | 22.9% | 0.152 | 0.379 | **0.553** | 0.541 | 0.516 | 0.49 / 0.64 / 0.56 (952) |
| 3 months | 44 | 3,067 | 735 | 24.0% | 0.166 | 0.366 | **0.513** | 0.459 | 0.458 | 0.45 / 0.65 / 0.53 (1,067) |
| 6 months | 41 | 2,893 | 824 | 28.5% | 0.207 | 0.397 | **0.510** | 0.475 | 0.481 | 0.42 / 0.64 / 0.51 (1,250) |

**Registered result.** All three classifiers are promoted at every horizon, with the logistic regression's gain over "small segment" at +0.174, +0.147 and +0.113 PR-AUC and lower ends of +0.139, +0.110 and +0.084. The logistic regression serves. At its threshold it beats "always alert" (F1 0.37, 0.39 and 0.44) and "small segment" (F1 0.48, 0.47 and 0.50). The 20% and 50% fall rules give the same picture (all promoted, logistic highest).

### Post hoc: most of that gain is regression to the mean

*Added after the results were seen; it does not replace any registered result.*

The registered "recent drop" baseline scores a segment higher when its latest share is *below* its 3-month mean. It scored 0.15 to 0.21 PR-AUC, **below** the no-skill level of 0.23 to 0.29, because it ranks segments in the wrong order: a month that spiked above the segment's recent average tends to fall back, which is exactly what the label counts as a decline. I wrote that baseline's sign backwards, so the registered comparison was against a baseline worse than doing nothing. The check changes only that sign.

| Horizon | Registered recent drop | Same score, sign flipped | Logistic | Gain over flipped | Lower end (registered percentile) | Lower end (2.5th) |
|---|---|---|---|---|---|---|
| 1 month | 0.152 | **0.504** | 0.553 | +0.049 | +0.013 | +0.016 |
| 3 months | 0.166 | **0.494** | 0.513 | +0.019 | -0.013 | -0.006 |
| 6 months | 0.207 | **0.506** | 0.510 | +0.004 | -0.030 | -0.025 |

- A one-line rule, "this segment's last month spiked above its recent average", gets 0.49 to 0.51 PR-AUC, almost what the models get.
- Against it the logistic regression still shows a reliable gain at **1 month**; at **3 and 6 months** the gain cannot be told from zero.
- So the alert's apparent skill is mostly a statistical fact that spikes fall back. That is real and predictable, but it is not a business insight and it says nothing about why a segment's share is going down. The plan's rules were followed exactly; the weakness was in the baseline I wrote.

## G5: competitor volumes (the labelled hypothesis)

The logistic regression on B0 + G4 + G5 against B0 + G4, each at the 2.5th percentile, outside promotion.

| Horizon | Gain | Lower end | Log-loss without / with G5 | Passes |
|---|---|---|---|---|
| 1 month | -3.3e-05 | -6.7e-05 | 0.11267 / 0.11271 | no |
| 3 months | +6.6e-06 | -1.3e-04 | 0.11187 / 0.11186 | no |

G5 does not help at either horizon, so it is not stored. The pass under the t-2 lag in DL-76 did not carry over to this test.

## The batch demonstration (descriptive, not a test)

Data stopped at a cut-off and the serving models predicted the six following months as one batch. The national forecast is "same as last month" with its 90% range; the segment forecast uses the logistic regression for months 1 to 4 and the baseline for months 5 to 6 (the serving model of the nearest evaluated horizon, section 15 of the plan).

- **National.** The actual share is inside the 90% range in 11 of 12 months; the exception is the first month after December 2022, when it fell below the range. The December 2023 batch includes the 2024 dip (the share fell from about 2.3% to about 1.9%) and stays inside a range that widens from about 0.6 to 1.1 points.
- **Segments, all together.** The December 2022 batch is mixed: 25% too high in the first month, then between about 1% too low and 12% too high. The December 2023 batch is too high in every month, by 13 to 19% in the first three and 30 to 41% in the last three. This is the T2 bias seen in a concrete case: the forecast cannot see a turn coming.

## What this means

1. **The national number: use the last value.** No model improves on it at any horizon, and the trained models' ranges are unreliable from 3 months out.
2. **The segment forecast: a small gain, with an upward bias.** The logistic regression beats the baseline at 1 and 3 months by about 0.4% and 0.2% of log-loss. It runs about 5% too high at 1 month and 11% at 3 months, and the baseline is as good at 6 months.
3. **The decline alert: handle with care.** The registered promotion is real under the rules but mostly reflects that unusually high months fall back. A reliable gain over a simple rule shows only at 1 month, and 1 month is the horizon a real user cannot act on, because the data arrive about 40 days late.
4. **Null results are findings.** At national level and at 6 months nothing beats the baseline, which fits 72 months of data, one change in direction and no monthly drivers we can observe.

## In the warehouse

Every publish writes two tables from the stored results (`modeling/store.py`; nothing is refitted):

- **`model_scores`** (222 rows): one row per task (T1, T2, T3), horizon, scenario and method. Columns: `kind` (baseline or model), `metric` (`mae_pp`, `log_loss`, `pr_auc`) and `higher_is_better`, `score`, `is_best_baseline`, `gain_over_best_baseline` with `gain_lower` and `gain_upper` (positive is better), `promoted`, `serving`, `n_test_months`, and where they apply `months_better`, `coverage_90`, `precision`, `recall`, `f1`, `n_alerts` and `bias_vs_actual`.
- **`backtest_batch`** (174 rows): the batch demonstration. Columns: `level` (national or specialty), `cutoff_month_id`, `step`, `target_month_id`, `specialty_name`, `model`, `predicted_share`, `actual_share`, `lo90_share`, `hi90_share` (national only) and `visits` (specialty only). Shares are fractions.

Model cards: [segment share](model_card_segment_share.md), [national share](model_card_national_share.md), [decline alert](model_card_decline_alert.md). Adoption into serving is recorded in DL-79.

## Disclosures and limits

- The archived modelling results and the EDA were seen first, so this is a replication on the new feature set and horizons, not a blind test. G4 was chosen on the same test months.
- With 72 months and overlapping origins the 3 and 6 month conclusions are weaker; every test-month count is in the tables above.
- The T3 rule (30% fall, 100-visit floor) was sized after looking at how often declines occur, not after any model result (plan section 12).
- The sign problem of the "recent drop" baseline was found after the results were seen; the post-hoc check is outside the plan and cannot promote or demote any model.
- Outcomes are read from the Gold segment table; a segment-month with no category visits has no share and is dropped.
- The pass rule has no minimum effect size, so "promoted" is not "worth a change". Adopting a model into serving is a decision (DL-79), not made here.
