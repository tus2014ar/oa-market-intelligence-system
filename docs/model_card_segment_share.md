# Model card: segment share (T2, logistic regression)

**Status: serves at 1 and 3 months ahead under the registered rule; the baseline serves at 6 months. A small gain that runs too high; not a forecast of the overall level.** It estimates a segment's Zilretta share better than "last month's share", by about 0.44% of log-loss at 1 month and 0.21% at 3 months. Adopted in DL-79 (28 Oct 2026): the logistic regression at 1 and 3 months with its bias stated beside every forecast, the baseline at 6 months. Decisions: [`decision_log.md`](decision_log.md) DL-76 to DL-79. Plan: [`modelling_plan.md`](modelling_plan.md). Results: [`modelling_results.md`](modelling_results.md), notebook 15, `data/reference/modelling_results.json`, warehouse tables `model_scores` and `backtest_batch`.

## Model

| | |
|---|---|
| Task | For a segment (specialty x age band x gender) at an origin month *t0*, predict Zilretta's share of the three-category total in month *t0 + h*, for h = 1, 3 and 6, using only data through *t0* |
| Served model | Binomial logistic regression: Zilretta and non-Zilretta visits are weighted rows, standardised numeric inputs, specialty one-hot, one fitted model per horizon (direct multi-step) |
| Settings | `C` in {0.01, 0.1, 1}, chosen inside each training window by validation log-loss on its last 12 outcome months and re-chosen every 6 test origins |
| Alternatives evaluated | Gradient boosting (depth 3 or 5, learning rate 0.05 or 0.1, 200 iterations) and a random forest (300 trees); tied with the logistic regression, so the simplest serves |
| Baselines | last month's share (the best at every horizon), the segment's long-run share, the specialty's long-run share, the market share |
| Code | `features/segment_task.py`, `modeling/segment_models.py`, `horizons.py`, `t2_horizon.py`, `run_modelling.py` |
| Protocol | Walk-forward from the 24th month of data, one origin at a time, a fresh model per origin, tuning only inside the training window; training rows only if their outcome month is at or before the origin; 46, 44 and 41 test months at h = 1, 3, 6 |
| Serving rule (fixed beforehand) | A model is promoted only if the lower end of its paired log-loss gain over the best baseline is above zero at the Bonferroni percentile (1.67th at 1 month, 0.83th at 3 and 6); the simplest promoted model serves, otherwise the baseline |

## Intended use

- A better estimate of a segment's share one and three months ahead than "same as last month", especially for segments with few visits (it shrinks noisy small segments toward their own history and their specialty).
- Ranking segments by expected share, with the upward bias below in mind.

**Not intended for:** forecasting the overall level (it runs high in a falling market); six-month planning (no model beat the baseline there); targeting individual prescribers (the data are aggregated to segments); any causal statement.

## Data and inputs

- **Source:** the warehouse (`gold_segment_adoption`), IQVIA NMTA visit counts aggregated to month x specialty x age band x gender; no patient-level data.
- **Rows:** 8,450 scored segment-months (a segment with at least 20 category visits the month before), paired with the outcome of month *t0 + h* read from Gold: 8,450, 8,218 and 7,879 rows at h = 1, 3, 6.
- **Inputs (all from *t0* or earlier):** the base set B0 (the segment's share at the origin and its 3-month mean, margin over the market, whether it was above the market, log visits, the segment's and its specialty's cumulative history shares, the market share and its change, age band, gender, specialty with rare ones grouped, and the **outcome month's** sine and cosine) plus G4 (the logit of the specialty's share last month, its 3-month change and a missing flag).
- **Leakage controls:** a test that rewrites every value after the origin and requires the prediction at the origin not to move (it fails when a leaky training set is used on purpose); a guard that raises if a training row has an outcome after the origin; a test that a pairing with an outcome at or before the origin is caught; at h = 1 the pairing reproduces the stored feature table exactly. One accepted detail: rare specialties are grouped using which specialties appear (no visit counts or shares).

## Evaluation (log-loss per visit, lower is better)

| Horizon | Test months | Best baseline (last month) | Logistic | Gain | Lower end | Months better |
|---|---|---|---|---|---|---|
| 1 month | 46 | 0.11317 | 0.11267 | +0.44% | +4.4e-04 | 46 / 46 |
| 3 months | 44 | 0.11210 | 0.11187 | +0.21% | +1.1e-04 | 33 / 44 |
| 6 months | 41 | 0.11033 | 0.11020 | +0.12% | -3.9e-05 | 25 / 41 |

- The gradient boosting (0.11268, 0.11191, 0.11029) and the forest (0.11272, 0.11191, 0.11034) tie with the logistic regression; the gain is the same size with more complex models.
- At 6 months no model qualifies, so the baseline serves.

## Robustness

The same comparison without the COVID months, without the 2024 dip months and without PEDIATRICS gives the same pattern: promoted at 1 and 3 months and not at 6. At 3 months the gain is 0.17 to 0.27% of log-loss in every scenario. Two identical full runs reproduced every number byte for byte.

## Calibration: the forecasts run too high

| Horizon | Predicted over observed, minus one | Calibration slope |
|---|---|---|
| 1 month | +5.5% | 1.09 |
| 3 months | +10.9% | 1.09 |
| 6 months | +18.2% | 1.09 |

By size at 1 month the bias is about +6% for segments with 200 or more visits and -4.6% for those under 50. In the December 2023 batch demonstration, which runs into the 2024 dip, the segment forecast was 13 to 41% too high.

## Limitations

- **Upward bias that grows with the horizon.** This is the bias the archived attempt found (about +7% in a falling market); the new inputs did not remove it. A user of the 3-month forecast should read it as optimistic.
- **Small gain.** About 0.4% and 0.2% of log-loss. "Promoted" is a significance rule with no minimum effect size, not a statement that the gain is worth a change.
- **G4 was chosen on the same test months** (DL-76), so the gain is slightly optimistic.
- **72 months, one drug, one change in direction, no monthly drivers we can observe.** Overlapping origins make the 3 and 6 month results weaker than the 1 month one.
- **Real use is two months behind.** IQVIA publishes about 40 days after a month ends, so "1 month ahead" is really about 2; the 3-month horizon is the realistic planning case.

## Maintenance

To be refit and re-tuned each month in the production phase (the monthly batch scoring job, designed in section 8 of the plan and not yet built). The serving rule decides each time whether a trained model or the baseline serves.
