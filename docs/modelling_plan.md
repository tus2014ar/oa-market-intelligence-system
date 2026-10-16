# Modelling plan (DL-78)

**Status: fixed before any model is built or run (16 Oct 2026).** Everything below is the rule. A change made after a result has been seen is a deviation: it is written in the decision log with its reason, and both versions are shown. "No model beats the baseline" is a valid result and leaves the baseline serving.

## 1. Purpose and scope

Decide, under rules fixed here, which model (if any) should serve for each prediction task and each forecast horizon, how large its advantage over a simple baseline is, and how wide the uncertainty is. A stakeholder suggested batch prediction ("take some months, predict the next ones"); this plan adds multi-month horizons and a batch demonstration to the original one-month-ahead tests.

- **Starting point:** the base set B0 plus the adopted group G4 (DL-76, DL-77), stored in `feat_segment_month` (8,450 scored rows) and `feat_monthly` (69 months), and the evaluation harness restored from the archive tag with its tests.
- **Tasks:** **T2** segment share (specialty × age band × gender) and **T1** national share, each at horizons of **1, 3 and 6 months ahead**.
- **Out of scope:** the Up/Flat/Down direction label, the inference questions (change point, mix-versus-adoption), monitoring and drift tools, new inputs, and building the monthly batch scoring job (designed here, built in the production phase).
- **What was seen first (disclosure).** The archived attempt (tag `archive/full-project-2026-10-11`) had a modest segment gain, no national gain and a negative direction result. This stage is a **replication on the new feature set plus the new horizons**, not a blind test.

## 2. Targets, horizons and the time rule

Share = Zilretta visits over the visits of Zilretta, generic corticosteroids and NSAID/OTC together.

- **Origin and horizon.** A forecast is made at an *origin* month *t0* using data through *t0* only, for the month *t0 + h*, with h in {1, 3, 6}. For h = 1 this is the convention of DL-75 (data through *t-1* predicts *t*). IQVIA publishes about 40 days after a month ends, so a real user's h = 1 is really h = 2; the 3-month horizon is therefore the realistic planning case, and the DL-76 *t-2* sensitivity already covers h = 2 for the feature groups.
- **Rows.** For T2 a feature row of month *m* (features through *m-1*) is paired with the outcome of month *m + h - 1*; a row is used for training at origin *t0* only if its outcome month is at most *t0*. For T1 the same pairing applies to the national table.
- **Test months.** Walk-forward, expanding window, 24-month minimum, one origin at a time, a fresh model per origin. Test outcome months: T1 about 48, 46 and 43 for h = 1, 3, 6; T2 about 46, 44 and 41 (the exact counts are printed by the runner and reported). Errors at neighbouring origins overlap, so all intervals use whole-month (T2) or moving-block (T1) resampling.
- **Primary result:** h = 1. h = 3 and h = 6 are secondary and carry a stricter threshold (section 5).

## 3. Candidates

| Task | Baselines (no fitting) | Trained models |
|---|---|---|
| **T2** | the segment's share at the origin (persistence), the segment's long-run share, the specialty's long-run share, the market share at the origin | **logistic regression** on B0 + G4 (the serving reference), **gradient boosting**, **random forest** |
| **T1** | the value at the origin (persistence), the value twelve months before the target month | **ETS** (damped trend), **ETS** (damped trend, yearly seasonality), **ridge** on lags 1 to 3 and the month sine and cosine |

- **Tuning only inside the training window** (redone every 6 test origins). Grids are fixed here: logistic C in {0.01, 0.1, 1}; gradient boosting depth {3, 5} × learning rate {0.05, 0.1}; random forest depth {4, 8, 12} × minimum leaf {20, 5}; ridge penalty {0.1, 1, 10} chosen by rolling-origin error over the last 12 months.
- **Direct multi-step.** Each horizon has its own fitted model (the features at the origin predict the outcome at *t0 + h*). ETS uses its own *h*-step forecast.
- **Segment model target.** Counts out of the month's category visits (binomial), so a segment with 200 visits counts more than one with 8.

## 4. Metrics

- **T2:** binomial log-loss per visit (primary), visit-weighted and unweighted share error (secondary).
- **T1:** mean absolute error in percentage points (primary), RMSE and MASE (secondary).
- **Intervals (T1):** empirical 80% and 90% percentiles of the model's past *h*-step errors; coverage and mean width are reported; a 90% interval is acceptable if its coverage is within two binomial standard errors of 90%. Segment-level intervals are not built (the earlier attempt failed this gate); a plain-language typical error by segment size is reported instead.

## 5. Promotion and serving rules

- **Comparison:** each trained model against the **best baseline of the same task and horizon**, paired on identical resampled months or blocks (2,000 draws, seed 0).
- **Promotion:** the lower end of the paired gain must be above zero at the Bonferroni-corrected percentile. **Primary (h = 1):** 0.05 / 3 models = the **1.67th** percentile. **Secondary (h = 3 and h = 6):** 0.05 / (3 models × 2 secondary horizons) = the **0.83th** percentile, so testing more horizons does not buy an easier bar.
- **Serving:** among promoted models the simplest (logistic regression or ridge, then ETS damped, then the more complex) serves unless a more complex one is clearly better by the same rule; if none is promoted the baseline serves. The rule is applied per task and per horizon.
- **Robustness (reported, not part of promotion):** the same comparison with the COVID months removed, with the early-2024 dip months removed, and (T2) with the PEDIATRICS group removed.
- **Effect sizes reported for every model and horizon,** including those that fail.

## 6. Extra pre-registered tests

- **G5 hypothesis (T2 only).** The logistic regression on B0 + G4 + G5 against B0 + G4, at h = 1 and h = 3, each at the 2.5th percentile (0.05 / 2). It passed only under the *t-2* lag in DL-76, outside the rule. It is a labelled hypothesis: it does not enter promotion, and G5 is stored only if it passes here.
- **Calibration (T2):** visit-weighted predicted over observed share minus one (the archived attempt found about +7% in the falling market), and the calibration slope; reported by segment size and by specialty. No pass or fail.

## 7. The batch demonstration for stakeholders (descriptive)

Two cut-offs fixed now: **Dec 2022** (predict Jan to Jun 2023, a calmer period) and **Dec 2023** (predict Jan to Jun 2024, which includes the 2024 dip). For each, the serving model of each task is trained on data through the cut-off and predicts the six following months as one batch: the national share with its 90% range, and the segment predictions summarised by specialty. Shown as predicted against actual. It illustrates what a batch forecast looks like and how fast accuracy decays; it is not a pass or fail test.

## 8. The monthly batch scoring job (design only, built in the production phase)

A scheduled step after the monthly publish: refit the serving models on all data, score the next 1, 3 and 6 months for the national series and every scored segment, and write a table `pred_batch` into the warehouse with the run month, target month, horizon, level (national or segment) and keys, model version, point forecast and, for the national level, the 80% and 90% range. A failure keeps the last good table. No code for this job is written in the modelling stage.

## 9. Leakage and testing (written before any real run)

- **Time guard.** The harness raises an error if a training row's outcome month is later than the origin. A new test pairs features and outcomes for h = 3 and h = 6 by hand on a small table.
- **Rewrite-the-future test at model level.** Rewrite every value after the origin: the prediction made at the origin must not move. A deliberately leaky pairing (an outcome month earlier than the origin) must be caught.
- **Hand-computed answers** for the baselines at each horizon, the promotion rule (a planted better model is promoted, a noise model is not) and the percentiles.
- **Reproducibility:** fixed seeds; the full run is repeated once and the outputs must be byte-identical.

## 10. Build, run and report

1. This plan and DL-78, committed before any code.
2. Build the horizon harness and runners with the tests above; no real run.
3. One real run of everything (all tasks, horizons, scenarios and the demonstration), repeated once.
4. A notebook, a results document, a model card for each model that serves, and a decision-log entry (DL-79), whatever the result. The promoted models' scores and the demonstration predictions are then stored in the warehouse tables `model_scores` and `backtest_batch`, so the explorer can show them.
5. A branch for all of it; a PR only when you say "run CI".

## 11. What is expected, stated now

- **T2 at h = 1:** a modest gain of the logistic regression over the best baseline (the archive found about 0.4% of log-loss), with gradient boosting and the forest tied to it; the simplest model serves.
- **T2 at h = 3 and h = 6:** the persistence baseline gets weaker with distance, so a model may do relatively better; I do not know, and the plan does not assume it.
- **T1:** "same as last month" most likely stays at h = 1; a seasonal or trend model may or may not beat persistence at h = 6. Errors grow with the horizon in every case.
- **A null result is a finding.** It fits what the data allows: 72 months, one regime change and no monthly drivers.

## 12. Disclosures

- The archived modelling results and the EDA were seen first (replication, not a blind test).
- **G4 was chosen on the same test months** (DL-76), so the B0 + G4 logistic regression's advantage over the baselines at h = 1 is slightly optimistic. This is stated beside that result.
- With 72 months and overlapping origins, long-horizon conclusions are weaker; the number of test months is reported with every result.
- The pass rule is a significance rule with no minimum effect size, so "promoted" is not "worth a change"; adoption into serving is a decision (DL-79).
- The two demonstration cut-offs were fixed before any run and are not changed.
