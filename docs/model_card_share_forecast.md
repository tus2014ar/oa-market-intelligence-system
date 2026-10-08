# Model Card: Monthly Share Forecast (Task B, "same as last month")

**Status: the served forecast is the baseline.** Five models were tested for forecasting next month's overall Zilretta visit share; none beat "same as last month", so that is what serves, with prediction intervals. Decisions: [`decision_log.md`](decision_log.md) DL-52, DL-53, DL-55. Protocol: [`phase4_modeling_plan.md`](phase4_modeling_plan.md) Steps 10 and 11. Evidence: [`notebooks/09_forecast_monitoring_and_direction.ipynb`](../notebooks/09_forecast_monitoring_and_direction.ipynb).

## Model

| | |
|---|---|
| Task | Forecast next month's overall Zilretta visit share (percentage points of the three-category total), one month ahead |
| Served forecast | Last month's value; 80% and 90% intervals are the forecast plus the 10th/90th and 5th/95th percentiles of the baseline's own one-step errors over the history |
| Models tested | Same month last year; ETS with a damped additive trend; ETS with a damped trend and additive yearly seasonality (statsmodels `ETSModel`); ridge regression on lags 1 to 3, their mean and month sin/cos (penalty in {0.1, 1, 10}, chosen by one-step rolling-origin error over the last 12 months, re-chosen every 6 months) |
| Code | `src/oa_market_intelligence/modeling/forecast.py`, `monitoring.py` |
| Protocol | Walk-forward over months, 24-month minimum, one step ahead, a fresh model per month: 48 test months (Aug 2021 to Jul 2025); a failed ETS fit falls back to last month and is counted (none occurred) |
| Serving rule (fixed beforehand) | A model is promoted only if the 1.67th percentile of its paired MAE improvement over the best baseline is above zero, using a moving-block bootstrap (block 6; 3 and 12 as sensitivity) because forecast errors in one series are serially dependent |

## Intended use

To give next month's expected share with an honest range, and to supply the intervals the monitor watches. Not intended to forecast beyond one month, or to explain why the share moves.

## Evaluation (48 test months)

| | Error (pp) | RMSE | 80% interval coverage | 90% interval coverage | 90% width (pp) |
|---|---|---|---|---|---|
| **Last month (serves)** | **0.125** | 0.157 | 83% | 96% | 0.64 |
| Same month last year | 0.359 | 0.422 | 69% | 73% | 1.15 |
| ETS (damped) | 0.158 | 0.193 | 75% | 90% | 0.63 |
| Ridge | 0.147 | 0.183 | 77% | 90% | 0.68 |
| ETS (damped, seasonal) | 0.158 | 0.189 | 71% | 81% | 0.49 |

- **No trained model is promoted.** Paired improvement over last month: ETS damped -0.034 pp, ridge -0.022 pp (interval -0.052 to +0.017, inconclusive), ETS seasonal -0.033 pp; the 1.67th percentile is below zero for all three, with bootstrap blocks of 3, 6 and 12 months, and without the Mar to Jul 2024 months.
- **Why:** the share series is smooth (this month's value is a very good guide to the next), so extra parameters add estimation noise, and a damped trend overreacts to the 2022 turn.
- **Calibration gate (within two binomial standard errors of nominal for 48 months):** the served intervals are slightly conservative but inside the band; same-month-last-year (73% at 90%) fails and ETS seasonal (81.25% at 90%) is just under; neither serves.
- **Typical size:** the error is about 5% of the level, and the 90% range is about 0.64 pp wide. The served forecast missed its 90% interval twice in 48 months (January and March 2023).

## Monitoring (Q4)

- **Alarm:** R90, at least 3 of the last 6 months outside the 90% interval, chosen over R80 (4 of 6 outside the 80% interval) by a rule fixed beforehand because R80 raised alarms in the real backtest. About 0.4% false alarms per window.
- **Sensitivity (simulated degradation, within 6 months):** about 54% for noise twice as large as normal, 14% for half that, 24% for a drift of 0.3 pp a month, and **0% for drifts of 0.2 pp a month or less**.
- **Blind spots, stated:** it cannot see level shifts (a last-month forecast adapts after one month) or slow drift; the real decline since 2022 is about 0.03 pp a month. A cumulative-sum monitor would be a natural addition and has not been built.

## Limitations

- 72 months of one drug; no payer, geography, price or prescription-volume inputs, which are the likeliest explanation for moves the history cannot predict.
- Two extra feature sets (a seasonal gap and market outside features: price ratio, company sales growth, months since an event) were tested against "same as last month" and both were worse (DL-72, [`feature_results.md`](feature_results.md)). The served rule is unchanged.
- The intervals are empirical and conservative; with 48 months, coverage figures are themselves uncertain (about ±6 points).
- A negative result is a result for *these* models on *this* series; it does not show that no better forecast exists.
