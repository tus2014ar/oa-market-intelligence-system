# Phase 4 Plan: Modeling and Evaluation

**Status: draft v3, awaiting approval (7 Oct 2026).** Not yet locked. Decisions behind it: [`decision_log.md`](decision_log.md) DL-38 to DL-43. Builds on [`evaluation_protocol.md`](evaluation_protocol.md) and replaces the thin first pass (one trained model on one weak task, DL-26). The schedule is deliberately left open: it will be set around the owner's dates, not assumed.

**Direction of v3: inference first, prediction second.** The data is six years of aggregated counts for one drug. That is well suited to careful descriptive and statistical analysis with honest uncertainty, and poorly suited to precise prediction. So the headline results are the ones a brand manager can rely on (what changed, why, where adoption differs, whether the forecast can be trusted), and the machine-learning models are tested rigorously against the strongest simple alternatives, not presented as the main story.

Once approved, everything below that could be tuned to flatter a result (targets, labels, features, grids, metrics, the rule that decides what serves) is fixed **before** the analyses run. Anything tried later that is not in this plan is reported as **exploratory** and is not a finding.

## What "reliable" means here

A number is reliable if (1) it uses only information that existed at prediction time, (2) it is tested on months the model never saw, (3) it is compared with the strongest simple alternative, (4) it carries an honest uncertainty range, (5) it still holds when known data problems are removed, and (6) another person can re-run it and get the same number.

## The four business questions and where each is answered

From the course project document (Assignment 3, section 2).

| Question | Main analysis | Predictive part |
|---|---|---|
| **Q1. Trend and inflection point** | Step 2 (trend and change points) and **Step 3 (how much of the decline is a change in specialty mix, and how much is a change in adoption within specialties)** | none |
| **Q2. Can a classifier predict next month's direction, and by how much does it beat persistence?** | Done (DL-26): it does **not**. **Step 12** closes it with a statement of what the test could and could not have detected | Done |
| **Q3. High/Low adoption by segment, does it vary by specialty?** | **Step 4:** binomial model of specialty, age and gender effects with intervals, and a check that the specialty ranking holds across time | Steps 6 to 9 (Task A) |
| **Q4. Monitoring: how often does the prediction match, and what accuracy should trigger a review flag?** | **Step 11:** the direction model's match rate with an accuracy threshold, and a forecast-interval flag that is the more reliable monitor | Step 10 (Task B) |

RA has about 1,283 visits in six years, so it is handled descriptively (counts and trend) and is not modeled or monitored.

## What the data allows (ad hoc profile, 7 Oct 2026; the harness reproduces these formally in Step 8)

| Fact | Number | Consequence |
|---|---|---|
| Segment-months with at least 20 category visits (rows eligible for Task A) | about 8,300 (139 segments, 17 specialties, 71 months) | Enough for a real test; few specialties and segments |
| Raw "above the market share" label, High rate | 43.9% | Balanced, but see the label problem below |
| "Same High/Low as last month" | **0.77 balanced accuracy** | **The bar to beat is persistence, not majority** |
| Specialty track-record rule | 0.68 balanced accuracy | Weaker than persistence |
| Monthly share, lag-1 autocorrelation | 0.90 (lag 12: 0.39) | "Same as last month" is already a strong forecast |
| Monthly change, lag-1 autocorrelation | −0.19 | Moves partly reverse; small but real |
| Category visits per eligible row | median 170, 10th percentile 41 | Small segments are noisy; their raw label can flip by chance |

**The label problem.** "Above the market share" flips by chance for a segment with 41 visits, so part of persistence's 0.77 is just stickiness of noise, and part of any model's miss is unpredictable chance. Task A therefore uses a three-label scheme (Step 6), tested before it is adopted.

## Step by step (each ends with a gate that must pass before the next begins)

Effort: S small, M medium, L large.

**Step 1: Lock the plan (S).** The owner approves the questions, the analyses, the models, the grids, the metrics and the serving rule here. *Gate:* approval; afterwards any change needs a decision-log entry.

### Part 1: Inference (answers Q1 and Q3 with intervals)

**Step 2: Q1, trend and change points (M).**
- Analyze the monthly share of each OA treatment category (branded injectable, generic corticosteroid, NSAID).
- *Method (fixed):* fit up to two breakpoints to the Zilretta share series (piecewise linear, at least 12 months per segment), choose the number of breaks (0, 1 or 2) by BIC, and attach a 90% interval to each break month by block bootstrap of the residuals.
- *Events, listed before the analysis runs:* COVID shock (Mar to May 2020), the Change Healthcare outage window (Mar to Jul 2024), and any competitor approval dates inside the data window from the openFDA enrichment. Break intervals are compared with this list only.
- *Limits stated up front:* Zilretta was approved in Oct 2017, before the data starts in Aug 2019, so its market entry cannot be observed and the FDA part of Q1 is answered by saying so. A break near an event is consistent with an effect, not proof of one.
- *Gate:* break locations are stable across the bootstrap, or the instability is reported as the result.

**Step 3: Q1, why did share fall: mix or adoption? (M).**
- *What it measures:* Zilretta's share of the category total equals the sum, over specialties, of (the specialty's share of category visits) × (Zilretta's share within that specialty). A decomposition splits any change in total share into a **mix effect** (the specialty mix moved, for example Orthopedic Surgery fell from about 46% to 35% of visits) and a **rate effect** (Zilretta's share within specialties moved).
- *Windows (fixed):* the 72 months are exactly six 12-month windows (Aug to Jul). Decompose each consecutive pair and the first window against the last.
- *Segmentation:* specialty (17) is primary; the 139-segment version (specialty × age × gender) is a robustness check.
- *Method:* the standard two-term (Kitagawa) decomposition, so mix + rate equals the total change exactly; uncertainty by resampling months within each window (1,000 draws); per-specialty contributions shown so one specialty cannot hide.
- *Caveat:* a mix effect says the mix moved, not why.
- *Gate:* a hand-computed example proves mix + rate equals the total change; the totals reconcile to Gold.

**Step 4: Q3, does adoption vary by specialty, and is the pattern stable? (L).**
- *Model (fixed):* a binomial model on the counts, Zilretta visits out of category visits for every segment-month, with month fixed effects (which absorb the market-wide level), plus specialty, age band and gender. It uses the counts as they are, so small segments carry less weight instead of being thrown away.
- *Outputs:* adjusted Zilretta share by specialty (standardized to a common age and gender mix) with intervals; the share of the explained variation due to specialty, age and gender; a test of whether the specialty effect is needed.
- *Uncertainty:* month-level bootstrap (1,000 refits) as the primary interval, with cluster-robust standard errors by segment as a cross-check; the wider of the two is reported, because visits are not independent.
- *Stability check:* fit on the first 36 months and on the last 36 and compare the specialty effects. A "focus on specialty X" message is only reliable if the ranking holds across both halves.
- *Gate:* the model reproduces the observed totals; the two uncertainty methods are compared and the difference reported.

**Step 5: Sensitivity to known data problems (M).** Re-run Steps 2, 3 and 4 excluding, in turn: the PEDIATRICS specialty (suspected mis-coded prescriber from Oct 2024), the COVID months (Mar to May 2020), and the Mar to Jul 2024 dip. A headline conclusion is reported as **robust** only if its direction and significance survive all three; otherwise it is reported as **fragile**, with the reason. *Gate:* a robust/fragile verdict for every headline conclusion.

### Part 2: Prediction (tested against the strongest simple alternatives)

**Step 6: Task A label audit, dataset and leakage tests (M).**
- *Label (fixed, subject to the audit below):* for a segment-month with a Wilson 95% interval for its Zilretta share, **High** if the whole interval is above the market-wide share that month, **Low** if the whole interval is below, otherwise **undetermined** (not scored, but counted). The simple two-label version and a 90% interval are run as sensitivity checks.
- *Audit gate, run before any model:* the rule is adopted only if at least 3,000 scored rows remain and each class has at least 15% of them. If not, fall back to the two-label version restricted to segments with at least 100 visits, and record why.
- *Unit and selection:* a prediction is made only for segments with at least 20 category visits in month t−1; a row is scored only if its label is determined in month t.
- *Leakage rule:* every feature uses month t−1 or earlier. The current feature set has one same-month column (`log_total_visits`); it is replaced by last month's volume. Tests rewrite every later month and require features not to move, and fail when a leak is deliberately injected.
- *Features (fixed):* specialty (rare ones grouped) and its prior share; the segment's own prior share; age as an ordered number; gender; last month's segment share and the mean over months t−3 to t−1; **last month's segment share minus last month's market share (distance from the line: a segment just above flips easily, one far above does not)**; last month's log volume and label; last month's market share and its change; month sin/cos. A missing lag is a missing value (median fill plus a "was missing" flag inside the pipeline, fit on training data only).
- *Gate:* leakage tests pass (including the injected-leak check) and row counts reconcile to Gold.

**Step 7: Extend the harness (M).** Pooled scoring, per-month scoring, the month bootstrap, paired differences against the best baseline, a flip-subset score (segments whose label changed from last month, where persistence is wrong by definition), visit-weighted versions of every metric, and a check that proves the test month is never used in tuning. *Gate:* every function has a test with a hand-computed answer.

**Step 8: Baselines and models for Task A (L).**

| Role | Model | Grid (fixed) |
|---|---|---|
| Baseline | Majority class | none |
| Baseline | **Persistence:** last month's raw above/below-market sign | none |
| Baseline | **Specialty track record:** High if the specialty's prior share is above last month's market share | none |
| Trained, core | Logistic regression (scaled, balanced weights) | C in {0.01, 0.1, 1} |
| Trained, core | Gradient boosting (HistGradientBoosting, 200 iterations) | learning_rate in {0.05, 0.1} × max_depth in {3, 5} |
| Trained, if time | Random forest (300 trees) | max_depth in {4, 8, 12} × min_samples_leaf in {5, 20} |
| Trained, last | XGBoost (first cut) | same grid as gradient boosting |

Walk-forward by month, expanding window, 24-month minimum, one month ahead, a fresh model per fold. Tuning happens only inside the training window (last 12 months as validation; balanced accuracy; ties go to the simpler setting; redone every 6 test months); a test month never influences a choice. Seeds fixed; every run logged to MLflow. *Gate:* the baselines reproduce the profile numbers within rounding; a second full run gives identical results.

**Step 9: Judge and explain Task A (M).**
- *Metrics:* balanced accuracy (primary), ROC AUC, F1 for High, log loss, Brier score, per-month balanced accuracy, and the flip-subset score; unweighted and visit-weighted.
- *Intervals:* the bootstrap resamples whole test months (2,000 draws, fixed seed) because rows in a month share market conditions; paired differences against the best baseline on the same resampled months.
- *Serving rule (DL-41):* a trained model is promoted only if the 1.67th percentile of its paired improvement over the best baseline is above zero (Bonferroni for three core and optional trained models) and its balanced accuracy is above 0.5; the best promoted model serves, otherwise the best baseline serves and the site says so.
- *Checks:* calibration (when the model says 70%, is it right about 70% of the time), error analysis (which segments flip, where it fails), and the Step 5 sensitivity exclusions.
- *Explanation:* permutation importance, SHAP (tree models) and effects by specialty and age for the best trained model, whether or not it is promoted, labelled **not causal** and compared with the Step 4 effects; disagreements are written down.
- *Gate:* the verdict holds in all views, or the exceptions are reported.

**Step 10: Task B, the share forecast with intervals (M).**
- *Baselines:* same as last month; same month last year. *Trained:* ETS with damped additive trend; ETS with damped trend and additive yearly seasonality; ridge regression on lags 1 to 3, the shifted 3-month mean and month sin/cos (alpha in {0.1, 1, 10}). A fold where an ETS fit fails falls back to the baseline forecast, and fallbacks are counted.
- *Intervals (fixed):* 80% and 90% prediction intervals for every model (ETS from simulation; baselines from the expanding-window empirical residuals).
- *Scoring:* MAE in percentage points (primary), RMSE, MASE, **interval coverage and width**, with the same month-bootstrap comparison against the best baseline and the same serving rule (1.67th percentile).
- *Why this matters:* the intervals are what Step 11 monitors, so a tie with "same as last month" is still a useful result.
- *Gate:* coverage is close to nominal (an 80% interval contains the actual about 80% of the time) or the miscalibration is reported.

### Part 3: Monitoring and closure

**Step 11: Q4, monitoring (M).** Two parts.
- *(a) As the question is worded:* how often the served direction model's prediction matched the actual direction, month by month over the backtest (the served model today is the seasonal baseline), a rolling 6-month accuracy, and a **review threshold** set at the 5th percentile of that rolling accuracy under stable performance (block bootstrap of past months), plus a stricter line at the range random guessing produces. With 35 test months this is wide; that is reported, not hidden.
- *(b) The more reliable monitor:* flag the forecast for review when **at least 3 of the last 6 months** fall outside its 90% prediction interval. If the intervals are calibrated, a good forecast triggers this about once in 60 six-month windows (1.6%), and a miscalibrated one triggers it far more often. The rule is tested against a simulated decline.
- Record the backtest predictions in the Gold placeholder columns (`predicted_direction`, `prediction_probability`, `actual_direction`), labelled as backtest. In the publish step each new month compares the previous prediction with the actual result, updates the monitors, and shows a banner on the site if a flag is crossed.
- RA is not monitored (too sparse); OA only. *Gate:* the flag rules have tests with hand-computed examples, and the simulated decline triggers them while a stable series does not.

**Step 12: Q2 closure, what the direction test could and could not detect (S).** The direction classifier does not beat persistence (62.9% accuracy; the logistic regression 40%). With 35 test months, 6 Down and 4 Up, a simulation shows the smallest improvement over persistence the test would have detected 80% of the time. The statement for stakeholders is "no reliable signal was found, and the test could only have detected an improvement of at least X", so that "no signal" is not confused with "too little data to see one". *Gate:* the simulation reproduces with a fixed seed and its logic is tested on a known case.

**Step 13: Stakeholder outputs (M).** Notebooks 06 (segments and inference), 07 (forecast and monitoring) and 08 (the Q1 analyses), a one-page model card per predictive task, and a plain-language summary for the brand manager: for each question, what we can say, what we cannot, how uncertain it is, and whether it survived the sensitivity checks. *Gate:* every number traces to a notebook cell; the owner reviews the wording for overclaiming.

**Step 14: Pipeline and site (M).** The publish step computes the analyses, panels and monitor status and stores them in the database file; the Model results tab and the Claude `get_model_results` tool show them. Tests cover the new stages, including a model-stage failure keeping the last good database. *Gate:* full suite passes and CI is green; merge only on the owner's word.

## What a stakeholder gets

- **Q1:** what the trend is, where it bent, and how much of the fall comes from the specialty mix changing versus Zilretta's own share within specialties.
- **Q2:** no reliable direction signal, by how much the classifier falls short of persistence, and what the test could have detected.
- **Q3:** which specialties sit above or below the market after adjusting for age, gender and month, whether that ranking is stable over time, and whether High/Low can be predicted beyond "same as last month", mainly on segments that flip.
- **Q4:** how often the direction predictions matched, the accuracy at which to raise a flag, and a forecast-interval alarm whose false-alarm rate is known.

## Models in one view

| Purpose | Models |
|---|---|
| Inference (Q1, Q3) | Two-term decomposition; piecewise-linear change points; binomial model with month fixed effects |
| Segment prediction (Task A) | Baselines: majority, persistence, specialty rule. Trained: logistic regression, gradient boosting; random forest if time; XGBoost last |
| Share forecast (Task B, feeds Q4) | Baselines: last month, same month last year. Trained: ETS (damped), ETS (damped, seasonal), ridge on lags |
| Direction (Q2, done) | Logistic regression and four baselines, closed with a power statement |

**Not used, and why:** deep learning (too little data); a separate model per specialty (17 specialties, 139 segments); SARIMA (72 months; ETS is enough); large hyperparameter searches (small fixed grids keep choices pre-specified; Optuna may be used inside the same training window with a fixed trial budget if the course wording needs it); place-of-service features (286 rows).

## Cut list, in order, if time runs short

XGBoost, then the random forest, then SHAP, then the seasonal ETS and the ridge model. **Never cut:** Steps 2 to 5 (inference and sensitivity), the core of Task A (baselines, logistic regression, gradient boosting, intervals, serving rule), Task B with the two baselines and damped ETS (it feeds Q4), and Step 11.

## Risks, stated now

- **Prediction may add little.** Persistence is strong (0.77 on the raw label); the three-label scheme and the flip subset are where a real gain would show. If none shows, segment shifts are mostly noise, and that is the finding. The inference results (Steps 2 to 5) do not depend on it.
- **The three-label scheme is untested.** It is adopted only if it passes the Step 6 audit, with a stated fallback.
- **Segment-months are not independent,** which is why intervals resample months. They still understate uncertainty somewhat, because neighbouring months are related.
- **A mix effect says the specialty mix moved, not why.** The decomposition is descriptive.
- **Known data anomalies** (PEDIATRICS from Oct 2024, COVID, the 2024 dip) can move results; Step 5 labels each conclusion robust or fragile.
- **Q1's FDA question cannot be tested as worded** because the approval predates the data; the plan says so instead of forcing a result.
- **The direction-model monitoring threshold rests on few months,** so it is wide and should be re-derived as months accumulate; the interval-based forecast monitor is the one to rely on.
- **A positive result is evidence for this data and period,** not a general claim, and a negative one is reported as plainly.
