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
| "Same High/Low as last month" | **0.77 balanced accuracy** (0.82 on segments with 100+ visits; Step 6 audit) | **The bar to beat is persistence, not majority** |
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
- *Method (fixed; revised 7 Oct 2026, DL-44):* fit up to two breakpoints to each category's share series (piecewise linear, own intercept and slope per segment, at least 12 months per segment). The number of breaks is chosen by **sequential tests calibrated by a block bootstrap** (a break is kept only if the improvement in fit beats what noise produces in 95% of 1,000 simulated series), not by BIC, which in testing reported breaks that were not there. Each break month gets a 90% interval from a second block bootstrap (1,000 draws) of the residuals. Block lengths 3 and 12 are run as sensitivity checks; a conclusion that changes with the block length is reported as fragile.
- *Events, listed before the analysis runs:* COVID shock (Mar to May 2020), the Change Healthcare outage window (Mar to Jul 2024), and any competitor approval dates inside the data window. The approval dates were looked up on 7 Oct 2026 (70 branded products, openFDA; DL-45) and none falls inside the window, so the list is the two events. Break intervals are compared with this list only.
- *Limits stated up front:* Zilretta was approved in Oct 2017, before the data starts in Aug 2019, so its market entry cannot be observed and the FDA part of Q1 is answered by saying so. A break near an event is consistent with an effect, not proof of one.
- *Gate:* break locations are stable across the bootstrap, or the instability is reported as the result.

**Step 3: Q1, why did share fall: mix or adoption? (M).**
- *What it measures:* Zilretta's share of the category total equals the sum, over specialties, of (the specialty's share of category visits) × (Zilretta's share within that specialty). A decomposition splits any change in total share into a **mix effect** (the specialty mix moved, for example Orthopedic Surgery fell from about 46% to 35% of visits) and a **rate effect** (Zilretta's share within specialties moved).
- *Windows (fixed):* the 72 months are exactly six 12-month windows (Aug to Jul). Decompose each consecutive pair and the first window against the last.
- *Segmentation:* specialty (17) is primary; the 139-segment version (specialty × age × gender) is a robustness check.
- *Method:* the standard two-term (Kitagawa) decomposition, so mix + rate equals the total change exactly; uncertainty by resampling months within each window (1,000 draws); per-specialty contributions shown so one specialty cannot hide.
- *Caveat:* a mix effect says the mix moved, not why.
- *Pre-specified vs exploratory:* the consecutive pairs and the first window against the last are fixed here. Splits at the peak year (year 1 to 3, year 3 to 6) were added after the Step 2 break was seen and are labelled **exploratory**. Run 7 Oct 2026: the segment table reconciles to monthly Gold exactly, and in every comparison the rate effect dominates (see the Step 3 results in the notebook).
- *Gate:* a hand-computed example proves mix + rate equals the total change; the totals reconcile to Gold.

**Step 4: Q3, does adoption vary by specialty, and is the pattern stable? (L).**
- *Model (fixed):* a binomial model on the counts, Zilretta visits out of category visits for every segment-month, with month fixed effects (which absorb the market-wide level), plus specialty, age band and gender. It uses the counts as they are, so small segments carry less weight instead of being thrown away.
- *Outputs:* adjusted Zilretta share by specialty (standardized to a common age and gender mix) with intervals; the share of the explained variation due to specialty, age and gender; a test of whether the specialty effect is needed.
- *Uncertainty (revised 7 Oct 2026, DL-46):* two bootstraps of 1,000 refits each, one resampling months and one resampling whole segments; the wider of the two intervals is reported for every specialty, because visits are not independent. (Cluster-robust standard errors were the planned cross-check; a segment bootstrap gives the same protection on the quantity actually reported, the adjusted share.)
- *Specialty grouping and the test (DL-46):* specialties under 0.5% of category visits are grouped as RARE, which keeps Pain Medicine, Sports Medicine, Rheumatology and Orthopedic Surgery (the four the question names) separate. The specialty term is tested with a quasi-likelihood F test scaled by the model's dispersion, because the data varies more than a plain binomial allows.
- *Stability check:* fit on the first 36 months and on the last 36 and compare the specialty effects. A "focus on specialty X" message is only reliable if the ranking holds across both halves.
- *Gate:* the model reproduces the observed totals; the two uncertainty methods are compared and the difference reported.

**Step 5: Sensitivity to known data problems (M).** Re-run Steps 2, 3 and 4 excluding, in turn: the PEDIATRICS specialty (suspected mis-coded prescriber from Oct 2024), the COVID months (Mar to May 2020), and the Mar to Jul 2024 dip. A headline conclusion is reported as **robust** only if its direction and significance survive all three; otherwise it is reported as **fragile**, with the reason. *Verdict rules (fixed 7 Oct 2026, before the run).* Each headline conclusion has one rule, evaluated on a baseline re-run and on each of the three exclusions; it is **robust** only if the rule holds in all four, otherwise **fragile**, and the verdict names which exclusion broke it.
- **T1, Zilretta's share has a trend break in early 2022:** the first break test has p < 0.05 and the selected break month lies inside the baseline run's 90% interval.
- **D1, the decline is within-specialty, not mix:** for year 1 to 6 and for year 3 to 6 (the latter exploratory), the rate effect is negative and at least twice the size of the mix effect in absolute terms.
- **A1, adoption varies by specialty:** the specialty term carries at least 25% of the explainable deviance and the quasi-F test has p < 0.01.
- **A2, the clear positions hold:** every specialty whose 90% interval excludes the overall share in the baseline run stays on the same side with the interval still excluding it. Reported per specialty.
- **A3, the pattern is stable over time:** the split-half rank correlation is at least 0.6.

*How the exclusions are applied:* PEDIATRICS is removed as a specialty from the segment-level data and from the monthly series (rebuilt from segment counts); the COVID and 2024-dip months are removed from the windows and the adoption data, and linearly interpolated in the share series used for the change-point fit. Sensitivity runs use 300 bootstrap draws per interval (100 for the stability interval) instead of 1,000, and the baseline is re-run at the same settings so like is compared with like.

*Gate:* a robust/fragile verdict for every headline conclusion.

### Part 2: Prediction (tested against the strongest simple alternatives)

**Step 6: Task A label audit, dataset and leakage tests (M).**
- *Label (fixed, subject to the audit below):* for a segment-month with a Wilson 95% interval for its Zilretta share, **High** if the whole interval is above the market-wide share that month, **Low** if the whole interval is below, otherwise **undetermined** (not scored, but counted). The simple two-label version and a 90% interval are run as sensitivity checks.
- *Audit gate, run before any model:* the rule is adopted only if at least 3,000 scored rows remain and each class has at least 15% of them. If not, fall back to the two-label version restricted to segments with at least 100 visits, and record why.
- *Unit and selection:* a prediction is made only for segments with at least 20 category visits in month t−1; a row is scored only if its label is determined in month t.
- *Leakage rule:* every feature uses month t−1 or earlier. The current feature set has one same-month column (`log_total_visits`); it is replaced by last month's volume. Tests rewrite every later month and require features not to move, and fail when a leak is deliberately injected.
- *Features (fixed):* specialty (rare ones grouped) and its prior share; the segment's own prior share; age as an ordered number; gender; last month's segment share and the mean over months t−3 to t−1; **last month's segment share minus last month's market share (distance from the line: a segment just above flips easily, one far above does not)**; last month's log volume and label; last month's market share and its change; month sin/cos. A missing lag is a missing value (median fill plus a "was missing" flag inside the pipeline, fit on training data only).
- *Result of the audit (7 Oct 2026, DL-48):* the interval rule **failed** the gate (2,355 scored rows, fewer than 3,000; 72% of prediction rows undetermined), so the plan's fallback applies: two labels (above or below the market-wide share), restricted to segments with at least 100 category visits **last month** (a prediction-time rule; the plan did not say which month, and conditioning on the outcome month's volume would use information we do not have when predicting). That leaves 5,436 prediction rows over 70 months, 47% High, with persistence at 0.82 balanced accuracy.
- *Gate:* leakage tests pass (including the injected-leak check) and row counts reconcile to Gold.

**Task A is now two linked tests (DL-49, 7 Oct 2026).** The Step 6 audit showed the High/Low label barely changes (persistence 0.82, only about 980 changes in 70 months), so a label-only test has little power. The **primary test (A1) predicts each segment's share next month from its visit counts** and is judged on out-of-sample log-loss per visit, which uses every row and the actual counts. The **secondary test (A2) is the High/Low question**, derived from the A1 predictions and judged against persistence on the segments whose label changes.

**Step 7: Extend the harness for segment rows (M).**
- *Walk-forward over rows:* for each test month t, train on every row from months before t (24-month minimum), predict every row in t. A runtime guard raises if the training rows contain month t or later, and a test proves it.
- *Scores:* binomial log-loss per visit (primary; predictions are clipped to 1e-6 to 1 - 1e-6), visit-weighted and unweighted MAE of the share, per-month versions of each, and for A2 balanced accuracy and the flip-subset score (rows whose label changed from last month, where persistence is wrong by definition).
- *Inference:* month-block bootstrap (2,000 draws, fixed seed) of the paired difference against the best baseline, resampling whole test months from per-month sums so every draw is fast.
- *Gate:* every function has a test with a hand-computed answer; the guard test fails when a deliberately leaky model factory is used.

**Step 8: Baselines and models (L).**

*A1, predicted share for segment-month t (fixed):*

| Role | Model | Grid (fixed) |
|---|---|---|
| Baseline | **Market:** the market-wide share in t-1 | none |
| Baseline | **Last month:** the segment's share in t-1, smoothed as (z + 0.5) / (n + 1) | none |
| Baseline | **Segment history:** the segment's cumulative share to t-1, same smoothing | none |
| Baseline | **Specialty history:** the specialty's cumulative share to t-1 | none |
| Trained, core | Binomial logistic regression (inputs below; scaled) | C in {0.01, 0.1, 1} |
| Trained, core | Gradient boosting (HistGradientBoosting, binomial loss, 200 iterations) | learning_rate in {0.05, 0.1} x max_depth in {3, 5} |
| Trained, if time | Random forest (300 trees) | max_depth in {4, 8, 12} x min_samples_leaf in {5, 20} |
| Trained, last | XGBoost (first cut) | same grid as gradient boosting |

The **best baseline** is the one with the lowest pooled log-loss on the test rows. Models fit success and failure counts as weighted rows. Logistic inputs: the logit of the smoothed last-month share, 3-month mean, segment history, specialty history and market share; the market change; last month's log volume and its interaction with the logit of last month's share (so the model can weigh a noisy small-segment history less); age; gender; month sin and cos; specialty (one-hot). Tree models take the raw features of `TASK_A_FEATURES`.

*A2, High or Low (secondary, fixed):* predict High if the A1 prediction is above last month's market share; the truth is the two-label label from Step 6 (segments with at least 100 visits last month). Baselines: majority class, persistence (last month's sign), and the specialty rule (High if the specialty's prior share is above last month's market share).

*Protocol:* walk-forward by month, expanding window, 24-month minimum, one month ahead, a fresh model per fold. Tuning only inside the training window (last 12 months as validation; log-loss per visit; ties go to the simpler setting; redone every 6 test months); a test month never influences a choice. Seeds fixed; every run logged to MLflow. *Gate:* the A2 baselines reproduce the Step 6 audit numbers (persistence 0.82 balanced accuracy on the 5,436 rows) within rounding; a second full run gives identical results.

**Step 9: Judge and explain Task A (M).**
- *A1 metrics:* log-loss per visit (primary), visit-weighted and unweighted MAE of the share, per-month log-loss, and calibration (predicted against observed share by decile of the prediction, visit-weighted). *A2 metrics:* balanced accuracy, ROC AUC, F1 for High, and the flip-subset score; unweighted and visit-weighted.
- *Intervals:* the bootstrap resamples whole test months (2,000 draws, fixed seed) because rows in a month share market conditions; paired differences against the best baseline on the same resampled months.
- *Serving rule (DL-41, applied to A1):* a trained model is promoted only if the 1.67th percentile of its paired log-loss improvement over the best baseline is above zero (Bonferroni for three trained models); the best promoted model serves, otherwise the best baseline serves and the site says so. A2 is reported with the same intervals as supporting evidence and does not decide serving.
- *Tie-break (DL-50):* among promoted models the simplest serves (order: logistic, gradient boosting, forest) unless a more complex one is clearly better, meaning the 1.67th percentile of its paired log-loss improvement over the simpler one is above zero.
- *Judging rules (fixed 8 Oct 2026, before the Step 9 runs).* Each is evaluated on the baseline run and on three exclusions (PEDIATRICS removed before the dataset is built, so the market share is recomputed; the COVID months and the Mar to Jul 2024 dip removed from training and scoring rows, with lag features left as they are). A conclusion is **robust** only if its rule holds in all four runs, otherwise **fragile**.
  - **J1, promotion holds:** the serving model's paired log-loss improvement over the best baseline has its 1.67th percentile above zero.
  - **J2, share error is lower:** the serving model's visit-weighted and unweighted MAE are both lower than last month's share's.
  - **J3, High or Low beats persistence:** the serving model's derived balanced accuracy beats persistence with the 1.67th percentile of the paired difference above zero.
  - **J4, calibration:** the serving model's calibration slope (observed against predicted on the logit scale, visit-weighted) lies between 0.8 and 1.2. This one is informational for promotion but is reported with its verdict.
- *Further checks, reported and not gating:* in how many of the test months the serving model beats the best baseline; the improvement by size of the segment's visits last month (to test the claim that small segments gain most); the decile calibration table; how many stable segments the High or Low prediction wrongly flips alongside how many real flips it catches; ROC AUC and F1 for High; error analysis by specialty (where the model fails).
- *Explanation (SHAP is not installed and stays on the cut list):* permutation importance by feature group on the test folds, the logistic regression's standardized coefficients, and observed against predicted share by specialty and age band, compared with the Step 4 adjusted shares by rank agreement. Labelled **not causal**; disagreements with Step 4 are written down.
- *Gate:* the verdict holds in all views, or the exceptions are reported.

**Step 10: Task B, the share forecast with intervals (M).** Details fixed 8 Oct 2026, before any Task B run (DL-52).
- *Series and protocol:* the monthly Zilretta visit share from Gold, in percentage points (share x 100), 72 months. Walk-forward, expanding window, 24-month minimum, one month ahead, a fresh model per month: 48 test months (Aug 2021 to Jul 2025).
- *Baselines:* **last month** (forecast = last month's value) and **same month last year** (needs 12 months). Their 80% and 90% intervals are the forecast plus the empirical 10th/90th and 5th/95th percentiles of the baseline's own one-step errors over the training window.
- *Trained models:* **ETS damped** (statsmodels `ETSModel`, additive error, damped additive trend, no seasonality); **ETS damped seasonal** (the same with additive seasonality, period 12); **ridge regression** on lags 1 to 3, the shifted 3-month mean (identical information to the three lags, kept as the plan lists it) and month sin/cos, inputs standardized. A month where an ETS fit fails or returns non-finite values falls back to last month's forecast and interval, and fallbacks are counted and reported.
- *Ridge tuning:* alpha in {0.1, 1, 10}, chosen inside the training window by one-step rolling-origin squared error over its last 12 months (ties go to the larger alpha, the simpler model); re-chosen every 6 test months. With so few rows a single validation split would be too thin, so each of the last 12 months is predicted from a model fitted on the months before it.
- *Intervals:* ETS from the model's own analytic prediction intervals; ridge from the empirical one-step errors of its rolling-origin forecasts over the training window (from the first origin with 12 months of history); baselines as above.
- *Scoring:* MAE in percentage points (primary), RMSE, MASE (each month's absolute error scaled by the training window's in-sample one-step "last month" MAE, then averaged), empirical coverage and mean width of the 80% and 90% intervals, and the interval (Winkler) score.
- *Inference:* a **moving-block bootstrap** of the 48 monthly absolute errors (block length 6, 2,000 draws, seed 0; lengths 3 and 12 as sensitivity checks), paired against the best baseline, because forecast errors in one series are serially dependent and resampling single months would overstate certainty. The best baseline is the one with the lowest pooled MAE.
- *Serving rule:* a trained model is promoted only if the 1.67th percentile of its paired MAE improvement over the best baseline is above zero (Bonferroni for three models); among promoted models the simplest serves unless a more complex one is clearly better (order: ETS damped, ridge, ETS damped seasonal; DL-50); otherwise the best baseline serves and the site says so.
- *Why this matters:* the intervals are what Step 11 monitors, so a tie with "last month" is still a useful result.
- *Gate:* coverage is near nominal, defined as within two binomial standard errors for 48 months (80% interval: 68% to 92%; 90% interval: 81% to 99%), or the miscalibration is reported.

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
- **Q3:** which specialties sit above or below the market after adjusting for age, gender and month, whether that ranking is stable over time, and whether a segment's share next month can be predicted better than "same as last month" or its own history (A1), and High/Low beyond "same as last month", mainly on segments that flip (A2).
- **Q4:** how often the direction predictions matched, the accuracy at which to raise a flag, and a forecast-interval alarm whose false-alarm rate is known.

## Models in one view

| Purpose | Models |
|---|---|
| Inference (Q1, Q3) | Two-term decomposition; piecewise-linear change points; binomial model with month fixed effects |
| Segment prediction (Task A) | A1, next month's share: baselines market, last month, segment history, specialty history; trained binomial logistic regression and gradient boosting (random forest if time, XGBoost last). A2, High/Low: derived from A1, against majority, persistence and the specialty rule |
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
