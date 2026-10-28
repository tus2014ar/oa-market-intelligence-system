# Modelling plan (DL-78)

**Status: fixed before any model is built or run (16 Oct 2026).** Everything below is the rule. A change made after a result has been seen is a deviation: it is written in the decision log with its reason, and both versions are shown. "No model beats the baseline" is a valid result and leaves the baseline serving.

## 1. Purpose and scope

Decide, under rules fixed here, which model (if any) should serve for each prediction task and each forecast horizon, how large its advantage over a simple baseline is, and how wide the uncertainty is. A stakeholder suggested batch prediction ("take some months, predict the next ones"); this plan adds multi-month horizons and a batch demonstration to the original one-month-ahead tests.

- **Starting point:** the base set B0 plus the adopted group G4 (DL-76, DL-77), stored in `feat_segment_month` (8,450 scored rows) and `feat_monthly` (69 months), and the evaluation harness restored from the archive tag with its tests.
- **Tasks:** **T2** segment share (specialty × age band × gender) and **T1** national share, each at horizons of **1, 3 and 6 months ahead**; and, added before any build (section 14), **T3** a yes/no decline alert on a segment's share, scored with precision, recall, F1 and PR-AUC at the same horizons.
- **Out of scope:** the Up/Flat/Down direction label (T3 is a different target: a yes/no event on a segment's share, section 14), the inference questions (change point, mix-versus-adoption), monitoring and drift tools, new inputs, and building the monthly batch scoring job (designed here, built in the production phase).
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

A scheduled step after the monthly publish: refit the serving models on all data, score the next 1, 3 and 6 months for the national series and every scored segment, and write a table `pred_batch` into the warehouse with the run month, target month, horizon, level (national or segment) and keys, model version, point forecast and, for the national level, the 80% and 90% range. A failure keeps the last good table. No code for this job is written in the modelling stage. (It was built afterwards, in hardening step 3: `modeling/scoring.py`, DL-82; it refits only the adopted models of DL-79 and the full promotion test stays on demand.)

## 9. Leakage and testing (written before any real run)

- **Time guard.** The harness raises an error if a training row's outcome month is later than the origin. A new test pairs features and outcomes for h = 3 and h = 6 by hand on a small table.
- **Rewrite-the-future test at model level.** Rewrite every value after the origin: the prediction made at the origin must not move. A deliberately leaky pairing (an outcome month earlier than the origin) must be caught.
- **T3 tests (section 14.6)** are added to this list: the label, the eligibility rule, the operating threshold and the metrics are each tested by hand on small tables.
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
- **T3 (decline alert):** I expect little skill beyond the baselines. Most 30% falls in small segments are sampling noise, which no feature predicts, so a volume-only baseline may be hard to beat; PR-AUC is read against the share of declines (about 23 to 24% in the full history), not against zero.
- **A null result is a finding.** It fits what the data allows: 72 months, one regime change and no monthly drivers.

## 12. Disclosures

- The archived modelling results and the EDA were seen first (replication, not a blind test).
- **G4 was chosen on the same test months** (DL-76), so the B0 + G4 logistic regression's advantage over the baselines at h = 1 is slightly optimistic. This is stated beside that result.
- With 72 months and overlapping origins, long-horizon conclusions are weaker; the number of test months is reported with every result.
- The pass rule is a significance rule with no minimum effect size, so "promoted" is not "worth a change"; adoption into serving is a decision (DL-79).
- The two demonstration cut-offs were fixed before any run and are not changed.
- **T3's 30% rule and 100-visit floor were sized after looking at how often declines occur in the full history** (counts in section 14.1), not after any model result. Nothing had been modelled when they were fixed.

## 13. Amendments made before any build (22 Oct 2026)

- **Calendar columns follow the outcome month.** For horizons of 3 and 6 months a feature row of month *m* is paired with the outcome of month *m + h - 1*, so `month_sin` and `month_cos` are recomputed for the **outcome month** (which is known in advance), not taken from the row's own month. For h = 1 they are unchanged.
- **Inputs used by the models, written out.** T2: B0 (the segment's last-month share, 3-month mean, margin over the market, whether it was above the market, log visits last month, the segment's and its specialty's cumulative history shares, the market share last month and its change, age band, gender, specialty with rare ones grouped, and the outcome month's sine and cosine) plus G4 (`g4_logit_spec_share_lag1`, `g4_spec_change_3m`, `g4_missing`). T1: the national share's lags 1 to 3, their mean and the outcome month's sine and cosine for the ridge; the ETS models use the share series itself. No other group is a model input, except in the labelled G5 hypothesis test.

## 14. Amendment before any build (25 Oct 2026): T3, the decline alert, and the prediction timeline

A request asked for the target to be defined more precisely, the candidate models and an evaluation with precision, recall, F1 and PR-AUC to be specified, and the moment of prediction to be stated so that leakage cannot be suspected. Those metrics need a yes/no target, which T1 and T2 do not have, so a third task is added. T1 and T2 are unchanged.

### 14.1 Target (T3)

- **Question:** will this segment's share, a few months from now, be much lower than it is today?
- **Label.** For a segment at origin *t0* and horizon *h* in {1, 3, 6}: label = 1 if share(*t0 + h*) <= 0.70 × share(*t0*), otherwise 0. Share is the T1 and T2 definition (Zilretta over Zilretta, generic corticosteroids and NSAID/OTC visits). Positive class = a fall of at least 30%.
- **Which segments are scored (eligibility).** A segment-month is scored only if its share at the origin is above zero (a zero share cannot fall) and its category visits at the origin are at least 100 (so two or three prescriptions cannot create an "event"). Eligibility uses origin-month information only.
- **Sizing, full history, h = 1 / 3 / 6** (printed again by the runner for the real test months): about 4,900 / 4,800 / 4,650 eligible segment-months across 95 segments; about 1,100 declines at each horizon, a share of about 22.6% / 22.5% / 24.4%. A national-level version was rejected: a fall of 0.5 pp or more occurs in about 1%, 3% and 6% of months (one to four events), too few for any of these metrics.
- **Reported only (outside promotion):** the same scoring with the fall rule at 20% and at 50%.

### 14.2 When the prediction is made (the leakage timeline)

An origin *t0* is a month whose data have been published. IQVIA publishes about 40 days after a month ends, so a forecast "at December 2022" is made around 9 February 2023. Example, origin December 2022:

| Item | What is allowed |
|---|---|
| Features | data through December 2022 only (the segment's share, history, market and G4 context, all built on months up to December 2022) |
| Reference for the label | the segment's share in December 2022 |
| Outcome (label) | January 2023 (h = 1), March 2023 (h = 3), June 2023 (h = 6); never seen before scoring |
| Rows the model may train on | rows whose own outcome month is December 2022 or earlier |
| Eligibility (floor, zero share) | December 2022 values |
| Alert threshold | chosen from earlier origins only (14.4) |
| Calendar inputs | the outcome month's sine and cosine (known in advance, section 13) |

Because publication takes about 40 days, a real user's h = 1 is really about h = 2; h = 3 is the realistic planning case, as in section 2.

### 14.3 Candidates

| Task | Baselines (no fitting) | Trained models |
|---|---|---|
| **T3** | **recent drop** (the segment's share at the origin below its own 3-month mean, as a score), **small segment** (fewer visits at the origin means a higher score; this captures pure sampling noise), **always alert** (no skill: precision equals the decline share, recall 1) | **logistic regression**, **gradient boosting**, **random forest** (classifiers) on B0 + G4, the T2 inputs, with the T2 grids (logistic C in {0.01, 0.1, 1}; gradient boosting depth {3, 5} × learning rate {0.05, 0.1}; forest depth {4, 8, 12} × minimum leaf {20, 5}) |

Direct multi-step, a fresh model per origin, tuning only inside the training window and redone every 6 test origins, on log-loss. No class weights, so the predicted scores stay probabilities.

### 14.4 Evaluation framework

- **Primary: PR-AUC** (average precision), computed over all test segment-months pooled across origins. It needs no threshold and suits a minority class. Its reference is the decline share (what "always alert" scores), reported beside every PR-AUC.
- **Secondary: precision, recall, F1 and the number of alerts** at an operating threshold. The threshold is chosen at each test origin from predictions made on earlier origins only: the last 12 origins inside the training window, each predicted by a model trained only on rows whose outcome month is at or before that origin; the threshold is the score that maximises F1 over those pooled predictions (ties go to the higher score). It is never chosen on a test month. Baselines get a threshold the same way.
- **Promotion:** the lower end of the paired PR-AUC gain of a trained model over the **best baseline** (the baseline with the highest PR-AUC on the full test set) must be above zero, resampling whole months, 2,000 draws, seed 0. Percentile: 0.05 / 3 models = the **1.67th** at h = 1 (primary); 0.05 / (3 × 2) = the **0.83th** at h = 3 and 6 (secondary). Serving: the simplest promoted model (logistic regression, then boosting or forest), otherwise a baseline; per horizon. As in section 5, T3 has its own family of three models; the correction is not widened across T1 and T2.
- **Robustness (reported, not part of promotion):** without the COVID months, without the 2024 dip, and without PEDIATRICS.
- **Effect sizes** for every model and horizon, including those that fail, with the test-month count and the number of positives.

### 14.5 What this does not claim

The alert says a segment is likely to fall by 30% or more; it does not say why, and a fall in a small segment is often noise. No model is promoted unless it beats both the recent-drop and the small-segment baselines by the rule above. "No model beats the baselines" is a valid result.

### 14.6 Tests written before any real run

- **Label by hand:** a small table with a 30% fall exactly at the threshold (counted as a decline), just inside it (not), a zero origin share (not scored) and 99 versus 100 origin visits (floor).
- **Timeline by hand:** for h = 3 and 6, features, label and outcome month are paired as in 14.2; a training row whose outcome month is later than the origin raises an error; a deliberately leaky pairing is caught.
- **Rewrite-the-future:** every value after the origin changed; the origin's alert scores must not move.
- **Threshold:** the operating threshold is computed only from earlier origins (a planted future month with extreme scores must not change it); the F1-maximising choice and the tie rule match a hand calculation.
- **Metrics by hand:** precision, recall, F1 and PR-AUC on a six-row example, and the "always alert" values (precision = decline share, recall = 1).
- **Promotion:** a planted better scorer is promoted and a noise scorer is not.
- **Reproducibility:** fixed seeds; the full run is repeated once and the outputs must be byte-identical.

## 15. Implementation choices fixed while building (25 Oct 2026, before any real run)

These settle details the plan left open. None was chosen after seeing a model result; the runner's `--counts-only` mode prints the resulting counts without fitting anything.

- **First origin.** The first origin is the 24th month of data, which reproduces the printed test-month counts (T2 and T3 46, 44, 41; T1 48, 46, 43 at h = 1, 3, 6). For h = 3 and 6 the first training window therefore holds fewer than 24 outcome months (rows with an outcome at or before the origin).
- **Outcomes come from Gold.** The outcome of month *t0 + h* is read from the segment Gold table, not from the feature table, so whether a later month happens to be a scored row cannot decide whether an earlier row has an outcome. A segment-month with no category visits has no share and is dropped. At h = 1 the pairing reproduces the stored feature table exactly (tested).
- **Scenarios.** For T2 and T3 the COVID months, the 2024 dip months (by outcome month) or the PEDIATRICS rows are removed from training and scoring and the walk is run again (the convention of DL-76). For T1 the 2024 dip months are dropped from scoring (the forecasts are the same); there is no COVID scenario because those months precede every test month. The 20% and 50% fall rules of T3 change only the label.
- **Best baseline.** The baseline with the best pooled score on the full test set of the scenario (lowest log-loss for T2, lowest MAE for T1, highest PR-AUC for T3). "Always alert" counts as a T3 baseline; its PR-AUC equals the decline share.
- **T3 alert threshold.** The inner origins are the last 12 outcome months of the training rows. An inner origin is skipped if the rows with an outcome at or before its origin span fewer than 12 months, so early windows at h = 3 and 6 use fewer than 12. If no inner origin remains or the pooled inner predictions hold no decline, the cut-off is +infinity (no alerts) and that is reported. "Always alert" has the cut-off minus infinity.
- **T1.** The ridge is fitted directly for the horizon on the target month's value against the three most recent values at the origin, their mean and the target month's sine and cosine, using only targets already observed at the origin; it needs at least 6 training rows, so its interval at h = 6 rests on few past errors at the first origins (coverage is reported with the number of test months). ETS uses its own h-step interval. "Same month last year" is the value twelve months before the target month. Serving order, simplest first: ridge, ETS damped, ETS seasonal.
- **Calibration (T2).** Predicted over observed visits minus one, reported overall, by segment size (category visits at the origin: under 50, 50 to 199, 200 to 999, 1000 and over) and by specialty, with the calibration slope from a logistic fit of the counts on the logit of the prediction.
- **Demonstration.** For the month *k* steps after a cut-off the model type is the serving model of the nearest evaluated horizon (k = 1, 2 use h = 1; 3, 4 use h = 3; 5, 6 use h = 6; ties go to the shorter horizon) and is fitted directly for horizon *k* on rows with an outcome at or before the cut-off. The G5 hypothesis uses the same rows with the G5 columns added.
- **Output.** `python -m oa_market_intelligence.modeling.run_modelling --run` writes `data/reference/modelling_results.json` with sorted keys and no timestamp; the second run must be byte-identical.

