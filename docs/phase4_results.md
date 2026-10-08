# Phase 4 Results: Modeling and Evaluation

**Status:** Phase 4 modeling is complete (7 to 8 Oct 2026); the site and pipeline integration (Step 14, DL-58) is done and merged. This is the one-page overview. Plan: [`phase4_modeling_plan.md`](phase4_modeling_plan.md). Every choice below was written into the plan, and committed, before the analysis that tested it. Decisions: [`decision_log.md`](decision_log.md) DL-38 to DL-57.

## The four business questions

| # | Question | Answer in one line | Strength | Evidence |
|---|---|---|---|---|
| **Q1** | How has share shifted; is there an inflection point; can an FDA event explain it? | Share rose until early 2022 and has fallen since; the turn is statistically clear; no FDA brand entry explains it; the fall is within specialties, not a change in the specialty mix | **High** (robust to method settings and to removing the known data problems) | [Notebook 06](../notebooks/06_q1_trend_and_decomposition.ipynb), [summary part 1](stakeholder_summary_part1.md), DL-44, DL-45 |
| **Q2** | Can a classifier predict next month's direction, and by how much does it beat persistence? | No: logistic regression, random forest and gradient boosting do not beat persistence or chance, and 35 test months could only have detected a gain of about 30 percentage points | A **negative result with a stated limit** | [Notebook 09](../notebooks/09_forecast_monitoring_and_direction.ipynb), [direction card](model_card_logistic_regression.md), DL-54, DL-56 |
| **Q3** | Can segments be classified high or low adoption; does adoption vary by specialty? | Adoption varies strongly by specialty (eight specialties clearly above or below the market-wide share, stable across time and robust); a simple model estimates a segment's next-month share better than "same as last month", mostly for small segments | **High** for the specialty pattern; **modest** for the estimator | [Notebook 07](../notebooks/07_q3_specialty_adoption_and_robustness.ipynb), [notebook 08](../notebooks/08_segment_share_prediction.ipynb), [segment card](model_card_segment_share.md), DL-46 to DL-51 |
| **Q4** | How often do predictions match, and what accuracy should trigger a review flag? | The served classifier matches about 66% (range 49% to 77%); flag at a rolling six-month match rate of 33% or below; a forecast-interval alarm exists but only catches large volatility jumps | **Moderate**, with stated blind spots (slow drift, level shifts) | [Notebook 09](../notebooks/09_forecast_monitoring_and_direction.ipynb), [forecast card](model_card_share_forecast.md), DL-55 |

## What was built and tested

| Purpose | Methods | Verdict |
|---|---|---|
| Q1 inference | Bootstrap-calibrated change-point test; two-term mix-versus-rate decomposition (six 12-month windows); openFDA approval lookup | Findings hold |
| Q3 inference | Binomial model on visit counts with month effects, adjusted shares, two bootstraps, split-half stability | Findings hold |
| Robustness | Baseline plus three exclusions (PEDIATRICS, COVID months, 2024 dip) against pre-set pass/fail rules | All five inference rules and all four prediction rules robust |
| Segment share (Task A) | Four baselines; logistic regression, gradient boosting, random forest | All beat the best baseline modestly; **logistic regression serves** (simplest, indistinguishable) |
| Share forecast (Task B) | Last month and same-month-last-year baselines; two ETS models; ridge | **None beats last month**; the baseline serves |
| Direction (Task C) | Four baselines; logistic regression, random forest, gradient boosting | None promoted; the seasonal rule serves |
| Monitoring | Data-based review threshold; interval-miss alarm (R90 chosen over R80) | Limited sensitivity, stated |

## What changed during the work, and why it matters

- **The planned change-point selection (BIC) was replaced before it ever touched real data,** because writing the tests first showed it reports breaks that are not there (DL-44).
- **The three-label segment scheme failed its pre-set gate** (2,355 scored rows against 3,000), so the pre-specified fallback applied and the primary test became the share itself (DL-48, DL-49). A looser rescue was not adopted.
- **Two limits were found only after the pre-set rules passed** and are reported: the segment estimator runs about 7% high in the falling market, and the change-point test is somewhat liberal under dependent noise (about 10% false positives against a 5% target in a demonstration).
- **The model's gains are modest and its negative results are real;** both are stated at the strength the evidence supports.

## What would make it more useful

Extra data. The public part is done (DL-57, DL-59, DL-60; [`external_data_results.md`](external_data_results.md)): it adds geography, price, promotion and company sales, and its most important result is a caution, that company sales rose after 2022 while IQVIA Zilretta visits fell by about half. Still missing: payer or formulary changes (not public), IQVIA prescription volume or regional data (awaiting the instructor), and a longer history.

## Reproducing the results

- Warehouse and stored results: `PYTHONPATH=src python -m oa_market_intelligence.publish` (builds `data/published/warehouse.db`; about 40 minutes at full precision, `--precision fast` for a quick check). The full-precision stored results equal the notebook numbers (break March 2022, Physical Medicine & Rehab adjusted share 5.34%, logistic regression serving the segment task, "same as last month" the forecast, the seasonal rule direction).
- Tests: `PYTHONPATH=src python -m pytest` (497 tests; about 10 minutes in CI).
- Notebooks 06 to 09 read `data/published/warehouse.db` and run with the registered kernel (see the repository README); the live runs take about 2, 15, 10 and 10 minutes.
- Every random choice uses a fixed seed; two full runs of Tasks A, B and C gave identical results.
