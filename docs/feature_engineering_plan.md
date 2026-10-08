# Feature engineering plan (DL-71)

**Status: fixed before any feature is built or run (8 Oct 2026).** Everything below is the rule. A change made after a result has been seen is a deviation: it is written in the decision log with its reason, and both versions are shown. "No family passes" is a valid result and leaves the serving models unchanged.

## 1. Purpose and scope

Test, under the Phase 4 harness and its promotion rule, whether any of six candidate feature families improves on the models that serve today.

- **Task A, segment share next month** (the main target: 21,636 segment-months, 46 walk-forward test months). Serving model today: logistic regression ([`model_card_segment_share.md`](model_card_segment_share.md)).
- **Task B, the national share forecast** (48 test months). No trained model is promoted today; "same as last month" serves ([`model_card_share_forecast.md`](model_card_share_forecast.md)).
- **Out of scope:** the direction classifier (Q2 is closed, DL-56), RA, any new outside data, and changes to the models, grids or labels.

## 2. Disclosures (what was seen before this plan was written)

- The Phase 4 results for both tasks and the model cards, and the EDA of notebook 13: the annual hint in the monthly changes (lag-12 autocorrelation +0.27, December, March and April high), the sparsity of the segment panel (60% of segment-months under 20 visits, 65% with no Zilretta visit), the availability of the outside series (price and company sales for all 72 months, promotion from July 2020, Medicare adoption from January 2023), and plots of price, promotion and company sales beside the share (no relationship was tested).
- **FA4 and FB1 are therefore EDA-informed and not a clean test** (the seasonal pattern was seen first). They are reported with that label.
- The price series and company sales were used in earlier pre-registered checks of a different design (H2, E2b, the gap diagnosis); no feature has been built from them and no model has seen them.
- No candidate family has been run.

## 3. The candidate families

Reference models (fixed): **Task A** the logistic regression of the Phase 4 plan (inputs as in `features/segment_task.py`; C in {0.01, 0.1, 1}); **Task B** the ridge regression of the Phase 4 plan (lags 1 to 3, the 3-month mean, month sin and cos; alpha in {0.1, 1, 10}). A family adds its columns to the reference model; nothing else changes.

| Family | Task | Definition (every input uses month *t-1* or earlier, or `mart_signal_asof`) |
|---|---|---|
| **FA1 shrinkage** | A | The segment's cumulative share shrunk toward its specialty's: (z + k x prior) / (n + k), where z and n are the segment's cumulative Zilretta and category visits through *t-1* and the prior is the specialty's cumulative share (the market's when the specialty has no history). k in {5, 20, 80}, chosen inside the training window by validation log-loss. Inputs: the logit of the shrunk share and the weight n / (n + k). |
| **FA2 activity history** | A | Months the segment has appeared before *t*; months since its last month with a Zilretta visit (capped at 24, 24 if never); log1p of its cumulative Zilretta visits. |
| **FA3 specialty momentum** | A | The specialty's volume-weighted share in *t-1* and its change from *t-4* to *t-1* (all of the specialty's segments). |
| **FA4 seasonal gap** | A | The specialty's mean over earlier years of (its share in the same calendar month minus its mean for that year); missing in the first year (missing rule below). EDA-informed. |
| **FA5 market outside features** | A and B | From the as-of table: the J3304-to-J3301 price ratio, its change from four quarters earlier, company net sales growth against the same quarter a year earlier, and months since the last known event (capped at 36). Same values for every segment in a month. |
| **FA6 Medicare adoption** | A only | The Medicare adoption rate of the segment's specialty group (the 11 approved groups; other specialties missing) as known as of *t-1*, plus a missing indicator. Known for only 31 of the 72 months, so it is expected to add little. |
| **FB1 seasonal gap** | B | The same quantity as FA4 for the national share. EDA-informed. |

Task B tests FB1 and FA5 (two families). Task A tests FA1 to FA6 (six families; FA5 is shared with Task B but judged separately in each task).

## 4. The test (the Phase 4 protocol, unchanged)

- **Task A:** walk-forward by month, expanding window, 24-month minimum, one month ahead, a fresh model per fold, tuning only inside the training window (redone every 6 test months). Metric: **binomial log-loss per visit**. Test months resampled whole (2,000 draws, seed 0), paired against the **serving logistic regression** on the same resampled months.
- **Task B:** walk-forward, 48 test months, one step ahead. Metric: **MAE in percentage points**. Moving-block bootstrap (block 6, 2,000 draws, seed 0), paired against **last month's value** (the serving baseline), because a family changes what serves only if it beats that.
- **A family passes** if the lower end of its paired improvement is above zero at the Bonferroni-corrected percentile: **0.83th percentile in Task A** (0.05 divided by the six families tested there) and **2.5th percentile in Task B** (0.05 divided by the two families tested there). *Correction recorded before any run:* the plan was first shown with "1st percentile" for Task A while listing six families; the exact quotient for six is 0.83, which is stricter, and is the rule.
- **Robustness:** a passing family must also pass in two further runs, each with the same reference: the **COVID months** (Mar to May 2020) removed from training and scoring rows, and the **early-2024 dip months** (Mar to Jul 2024) removed likewise (lag features left as they are, as in Phase 4). A family that passes the main run but not both is **fragile** and is not adopted.
- **Combined model:** only if two or more families pass robustly. It contains exactly those families, and it is adopted only if it beats the best single passing family by the same rule (a conditional extra test; the family-wise error rate is not exactly controlled for it and that is stated).
- **Effect sizes are reported for every family**, including those that fail: the mean and the interval of the paired improvement, and the number of test months in which the candidate beats the reference.
- **Missing values (fixed):** tree models keep NaN; logistic and ridge get zero-fill plus a missing indicator for the columns that can be missing (FA4, FB1, FA6, the early months of FA5).
- **Not allowed:** choosing features by importance on test data, adding or dropping a family after a result, changing a grid, a threshold or a test month, using any series computed from IQVIA's own visits (the target would enter the inputs), or a second run chosen because the first looked poor.

## 5. Leakage control

- Every IQVIA-derived feature is tested by the generic test the existing features use (`tests/test_features_monthly.py`, `tests/test_features_segment.py`): every measurement from month *t* onward is rewritten and the month-*t* features must not move. Each new family gets the same test.
- Every outside feature comes from `mart_signal_asof`; the pipeline's `leakage_violations` must return nothing, and a planted future value must be caught (tests exist, R2).
- A leaky model factory must fail the existing guard test.

## 6. Build, run and report

1. Build the families in new modules beside the existing feature code, with unit tests that have hand-computed answers and the leakage tests. No real run yet.
2. Update the feature dictionary.
3. One real run of all tests, repeated once for identical output (seeds fixed).
4. Report in notebook 14, `docs/feature_results.md`, the model cards if anything is adopted, and a decision-log entry, whatever the result. If a family is adopted, the publish step's serving rule applies as before and the site says what serves.

## 7. What is expected, stated now

Small effects at best. FA1 to FA3 have the most room because the segment panel has the power to show a few percent. FA5 and FA6 are limited by availability (price and company sales are known for every month; promotion and Medicare adoption are heavily lagged), and Task B most likely stays at "same as last month". A null result is a finding, and it fits what the data allows: 72 months, one regime change and no monthly drivers.
