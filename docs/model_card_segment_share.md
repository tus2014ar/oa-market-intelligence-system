# Model Card: Segment Share Model (Task A, logistic regression)

**Status: a working estimator with a modest, consistent gain; not a forecast of the overall level.** It estimates a segment's Zilretta share next month better than "same as last month", mostly for small segments. It runs about 7% high in the declining period it was tested on. Decisions: [`decision_log.md`](decision_log.md) DL-48 to DL-51. Protocol: [`phase4_modeling_plan.md`](phase4_modeling_plan.md) Steps 6 to 9. Evidence: [`notebooks/08_segment_share_prediction.ipynb`](../notebooks/08_segment_share_prediction.ipynb).

## Model

| | |
|---|---|
| Task | For a segment (specialty × age band × gender) in month *t*, estimate Zilretta's share of the three-category total, using only information from month *t-1* or earlier |
| Served model | Binomial-style logistic regression: Zilretta and non-Zilretta visits are weighted rows (weights scaled to total the row count), standardized numeric inputs, specialty one-hot |
| Settings | `C` in {0.01, 0.1, 1}, chosen inside each training window by validation log-loss on its last 12 months and re-chosen every 6 test months (it chose `C = 1` at 7 of 8 re-tunings) |
| Alternatives evaluated | Gradient boosting (depth 3 or 5, learning rate 0.05 or 0.1, 200 iterations) and random forest (300 trees); statistically indistinguishable from logistic regression, so the simplest serves (DL-50) |
| Code | `src/oa_market_intelligence/features/segment_task.py`, `modeling/segment_models.py`, `segment_eval.py`, `segment_task_run.py`, `segment_judge.py` |
| Protocol | Walk-forward over months: train on every row before the test month (24-month minimum), predict every row of the test month; 46 test months, Oct 2021 to Jul 2025; a fresh model per month; tuning only inside the training window |
| Serving rule (fixed beforehand) | A model is promoted only if the 1.67th percentile of its paired log-loss improvement over the best baseline is above zero (months resampled, 2,000 draws); among promoted models the simplest serves unless a more complex one is clearly better |

## Intended use

- To get a better estimate of a segment's current share than "same as last month", especially for segments with few visits (it shrinks noisy small segments toward their own history and their specialty).
- To rank segments by estimated share, and (as supporting evidence) to say whether a segment is likely to sit above the market-wide share.

**Not intended for:** forecasting the overall level (it runs about 7% high in a falling market and would run low in a rising one); targeting individual prescribers (the data is aggregated to segments); or any causal statement.

## Data and inputs

- **Source:** the committed warehouse (`gold_segment_adoption`), IQVIA NMTA visit counts aggregated to month × specialty × age band × gender; no patient-level data.
- **Rows:** 8,450 prediction rows over 70 months (a prediction is made only for a segment with at least 20 category visits the month before).
- **Inputs (all from month *t-1* or earlier):** the segment's share last month and its three-month average, its long-run share, the specialty's long-run share, last month's market-wide share and change, last month's visits, age, gender, month of year. The answer (month *t*'s counts and share) sits in separate `y_` columns the model never sees.
- **Leakage controls:** a test that rewrites every month from a cut onward and requires the earlier inputs not to move, which fails when a leak is injected on purpose; the same check on the real table at three cut months (largest change 0); five rows recomputed independently from raw counts. One accepted detail: rare specialties are grouped using which specialties appear (no visit counts or shares).

## Evaluation (46 test months, 5,522 rows)

| | Log-loss per visit | Share error, visit-weighted | Share error, unweighted |
|---|---|---|---|
| Market share last month | 0.11566 | 0.00997 | 0.01996 |
| **Last month's share (best baseline)** | **0.11317** | 0.00555 | 0.01497 |
| Segment's long-run share | 0.11349 | 0.00704 | 0.01348 |
| Specialty's long-run share | 0.11494 | 0.01002 | 0.01644 |
| **Logistic regression (serves)** | **0.11274** | **0.00487** | **0.01120** |
| Gradient boosting | 0.11274 | 0.00484 | 0.01153 |
| Random forest | 0.11274 | 0.00479 | 0.01173 |

- **Improvement over the best baseline:** +0.00044 per visit (+0.39%), 1.67th percentile +0.00037; it beats last month's share in 44 of 46 months on log-loss and in all 46 on unweighted share error. Share error is 12% lower visit-weighted and 25% lower unweighted.
- **Where the gain is:** unweighted share error is about 36% lower for segments with fewer than 50 visits last month and about 9% lower above 300; the log-loss gain over 300 visits is close to zero.
- **Every specialty improves;** the largest gains are in the smallest specialties, the smallest in Orthopedic Surgery.
- **What it uses (permutation importance):** the segment's own recent share, then its long-run share, then the specialty's; the market-wide share, the calendar and last month's volume add nothing. The largest numeric weight is the three-month mean share.
- **High or Low (supporting):** net accuracy gain of +1.7 points over "same side as last month" (0.848), catching about 42% of real flips while wrongly flipping about 5.5% of stable segments; ROC AUC 0.94.
- **Agreement with the specialty inference:** rank agreement 0.98 between its predicted share by specialty and the adjusted shares of the Q3 model (notebook 07).

## Robustness

Four pre-set rules (beats the best baseline, both share errors lower, High/Low beats persistence, calibration slope between 0.8 and 1.2) were applied to the baseline and to three re-runs without PEDIATRICS, the COVID months and the March to July 2024 dip. **All four hold in all four runs;** logistic regression serves in every run. Two identical full runs reproduced every number.

## Limitations

- **Bias in a falling market.** Predicted share averages 2.65% against 2.48% observed (about 7% high); every calibration group is predicted high. The test period is the decline, and a model trained on earlier, higher-share months lags it. Low-share specialties are over-predicted and Physical Medicine & Rehab (which rose) under-predicted: the model shrinks toward the average.
- **Modest size.** Log-loss improves by 0.4%; "better, reliably, by a modest margin" is the honest wording. For segments with more than 300 visits the gain is negligible.
- **The label barely changes,** so High or Low is only supporting evidence (persistence is right about 82% of the time on the rows used).
- **Seven extra feature families were tested and none is used (DL-72).** Specialty momentum and activity history pass the pre-registered rule but lower log-loss by only about 0.06%; price, company sales and Medicare adoption do not help. The inputs above are unchanged ([`feature_results.md`](feature_results.md)).
- **Short history, one drug.** 72 months; no payer, geography, price or prescription-volume inputs.
- **The pre-set rules are small perturbations.** "Robust" means not driven by these three known data problems.
- **The interval-label scheme failed its audit** (2,355 scored rows against a 3,000 gate), which is why the share, not the label, is the primary target (DL-48, DL-49).

## Maintenance

Refit and re-tune each month in the publish step; the serving rule decides whether a trained model or a baseline serves. **Not yet run (exploratory):** a model of each segment's share *relative to the market*, which might remove the lag in a falling market. SHAP and MLflow were not used (not installed); permutation importance and a JSON run log were.
