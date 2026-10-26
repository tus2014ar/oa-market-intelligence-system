# Model card: national share (T1, "same as last month")

**Status: the baseline serves at 1, 3 and 6 months ahead. No trained model beat it.** The national Zilretta visit share is best forecast by its last value. Decisions: [`decision_log.md`](decision_log.md) DL-76 to DL-79. Plan: [`modelling_plan.md`](modelling_plan.md). Results: [`modelling_results.md`](modelling_results.md), notebook 15, `data/reference/modelling_results.json`, warehouse tables `model_scores` and `backtest_batch`.

## Model

| | |
|---|---|
| Task | Predict the national Zilretta visit share (percentage points) for month *t0 + h*, h = 1, 3, 6, from the series through *t0* |
| Served model | **Same as last month:** the forecast for every horizon is the value at the origin. Its 80% and 90% ranges are the empirical percentiles of the past h-step changes seen so far |
| Alternatives evaluated | Ridge (direct for the horizon, on the three latest values, their mean and the target month's sine and cosine), ETS with a damped trend, ETS with a damped trend and yearly seasonality; the other baseline was "the value twelve months before the target month" |
| Code | `modeling/forecast.py`, `modeling/t1_horizon.py`, `run_modelling.py` |
| Protocol | Walk-forward from the 24th month of history, one origin at a time; 48, 46 and 43 test months at h = 1, 3, 6; paired moving-block bootstrap (block 6, 2,000 draws) |
| Serving rule (fixed beforehand) | A model is promoted only if the lower end of its paired MAE gain over the best baseline is above zero at the Bonferroni percentile (1.67th at 1 month, 0.83th at 3 and 6); otherwise the baseline serves |

## Intended use

- A simple, honest forecast of where the national share will be, with a likely range, for planning conversations.
- A yardstick: any future model of the national share has to beat it, reliably.

**Not intended for:** explaining why the share moves; predicting a turn in the trend (it cannot see one coming); any statement about causes.

## Data and inputs

- **Source:** `gold_visit_share_monthly`, the monthly Zilretta share of Zilretta, generic corticosteroid and NSAID/OTC visits; 72 months, August 2019 to July 2025.
- **Inputs:** the series itself, up to the origin. Nothing else is used; outside series (price, company sales, promotion, competitor volumes) did not help in the feature test (DL-76).
- **Leakage controls:** forecasts are cut from the history through the origin only; a test rewrites the series after an origin and requires the forecasts made earlier not to move.

## Evaluation (mean absolute error in percentage points, lower is better)

| Horizon | Test months | Same as last month | Ridge | ETS damped | ETS seasonal | Same month last year |
|---|---|---|---|---|---|---|
| 1 month | 48 | **0.125** | 0.147 | 0.158 | 0.158 | 0.359 |
| 3 months | 46 | **0.180** | 0.280 | 0.213 | 0.232 | 0.356 |
| 6 months | 43 | **0.239** | 0.402 | 0.263 | 0.279 | 0.359 |

Every trained model's lower end is below zero at every horizon. Without the 2024 dip months the result is the same.

## The ranges

| Horizon | 90% range width (points) | Months inside the 90% range |
|---|---|---|
| 1 month | 0.64 | 96% |
| 3 months | 0.85 | 91% |
| 6 months | 1.11 | 77% |

The ranges are close to honest at 1 and 3 months and too narrow at 6 months. The trained models' ranges are worse (the ridge's covers 47% of months at 6 months).

## Limitations

- **It cannot anticipate a turn.** In the December 2023 batch demonstration the share fell from about 2.3% to 1.9% in 2024; the flat forecast stayed at 2.37% but the actual values stayed inside a widening range in all six months.
- **Only 72 months and one change in direction.** The comparison with trained models rests on about 45 overlapping test months.
- **Real use is two months behind.** IQVIA publishes about 40 days after a month ends, so a user's "next month" is really about two months ahead.
- **This is a null result about models, not a claim that nothing is knowable.** It means that with these data nothing we observe improves on the last value.

## Maintenance

Recomputed each month from the published series; no fitting is involved. Refit-based alternatives would only replace it if one beat it under the same rule.
