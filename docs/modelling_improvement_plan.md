# Modelling improvement plan (DL-73)

**Status: fixed before anything is built or run (8 Oct 2026).** Everything below is the rule. A change made after a result has been seen is a deviation: it is written in the decision log with its reason, and both versions are shown. "Nothing improves" is a valid result and leaves the serving models unchanged.

## 1. Purpose and scope

Feature engineering found nothing worth changing (DL-72). This round does not add inputs; it goes after weaknesses that Phase 4 already stated, and adds one descriptive view the brand manager can use:

| Item | Target | Weakness or need it addresses |
|---|---|---|
| **M1** bias correction | Task A, serving logistic regression | Predicted share runs about 7% high in the falling market (2.65% predicted, 2.48% observed; [`model_card_segment_share.md`](model_card_segment_share.md)) |
| **M4** prediction intervals | Task B and Task A | The served national intervals are empirical percentiles of all past one-step changes; they cover 96% of months at a nominal 90% (width 0.64 pp). Segments have no intervals |
| **M5** drift monitor and decision metric | Task B errors; Task A segments | The forecast alarm detects 0% of drifts of 0.2 pp a month or less (DL-55). A brand manager asks "where is the headroom", not "what is the log-loss" |
| **M6** competitor shares | IQVIA product-level visits | Zilretta against the individual injectable steroids; who gained after the 2022 turn |

- **Task A** (segment share next month; 21,636 segment-months; 46 walk-forward test months) and **Task B** (national share; 48 test months) are as in the Phase 4 and feature plans ([`feature_engineering_plan.md`](feature_engineering_plan.md) section 4). The harness, the metrics, the bootstrap and the serving rules are unchanged.
- **Out of scope:** the direction classifier (Q2 closed, DL-56), new inputs or outside data, a change to any grid, label or test month, the 2024-dip treatment and forecast combinations (judged likely null and dropped from this round), the knowledge graph (final phase, optional), RA.
- **Nothing here changes what serves** unless the owner decides so after the results, as in DL-72.

## 2. M1: bias correction for the segment model

- **Candidate (one, no grid):** the serving tuned logistic regression plus a **logit-offset layer**. At test month *t*, the offset delta is fitted by one-parameter binomial maximum likelihood on the model's own earlier out-of-sample predictions for months *t-12* to *t-1* (rows weighted by their visits): the delta that minimises log-loss of `logit(p) + delta`. Those months' actuals are all known by *t* (the target of month *t-1* is known at the start of month *t*), so nothing from month *t* or later is used.
- **Fixed details:** window 12 months; at least 3 prior test months are needed, otherwise the prediction is left uncorrected; the correction is applied to every segment in the month; predicted shares are clipped to [1e-12, 1-1e-12].
- **Test:** paired log-loss against the serving logistic regression on whole resampled test months (2,000 draws, seed 0). **Pass:** the lower end of the paired improvement is above zero at the **5th percentile** (one candidate, no correction for multiplicity) **and** the bias shrinks, where bias = mean predicted share divided by mean observed share minus 1 over the scored rows, from about +7% to within **+-3%**.
- **Robustness:** the same two further runs as the feature plan (COVID months removed; early-2024 dip months removed from training and scoring rows). The layer is re-estimated inside each run. A pass in the main run only is **fragile** and is not adopted.
- **Effect size reported either way:** mean and interval of the paired improvement, months better, bias before and after, and the bias by specialty size.
- **Expectation, stated now:** the bias shrinks; the log-loss gain is tiny, because a level offset is a small part of the error. It may also fail in the dip run, where the recent window is itself distorted.

## 3. M4: prediction intervals

**Task B (national forecast).** Candidate (one): **adaptive conformal inference** on the served rule (last month's value). The score is the absolute one-step change; the 90% interval is last month's value +- the empirical quantile of past scores at level 1 - alpha(t), where alpha(t) is updated each month by alpha(t+1) = alpha(t) + 0.02 x (0.10 - miss(t)) (gamma 0.02, fixed). It starts from the empirical percentile used today and uses only months before the one being forecast.

- **Pass:** coverage within **two binomial standard errors of 90%** over the 48 test months (the coverage gate used in Phase 4, `coverage_gate`), **and** mean width at least **10% below** the served 0.64 pp. Both are reported with a block-bootstrap interval (block 6).
- **Expectation:** narrower intervals at close to nominal coverage; the current ones are slightly conservative.

**Task A (segments).** There are no segment intervals today, so this is an addition, not a comparison. Candidate (one): split-conformal on **standardised residuals**. For each prediction, the scale is s = sqrt(p(1-p) / n_prev), where n_prev is the segment's category visits in the previous month (the month's own volume is not known in advance). The interval is p +- q x s, clipped to [0, 1], where q is the 90th percentile of the pooled absolute standardised residuals of all earlier scored test months. The first 3 test months only build the calibration pool and are not scored.

- **Pass:** overall coverage within **3 points** of 90% and coverage in each of the four size buckets (previous-month visits under 50, 50 to 100, 100 to 300, over 300) within **5 points** of 90%. A whole-month bootstrap interval for each coverage is reported.
- **Expectation:** the overall coverage is near 90%; the small-segment bucket is the hard one because shares of a few visits are lumpy.

## 4. M5: a drift monitor and a decision metric

**CUSUM monitor.** Two-sided CUSUM on the served forecast's standardised one-step errors (the change from last month, divided by the standard deviation of all earlier changes, with at least 24 months of history). Reference value k = 0.5. The threshold h is the **smallest on the grid {3, 4, 5, 6, 8}** whose simulated **average run length without drift is at least 48 months** (simulation: moving-block resamples of the real changes, block 6, 2,000 runs, seed 0).

- **Detection experiment:** the existing DL-54 machinery (`perturb_drift`, the same start months), with drifts of **0.05, 0.1, 0.2 and 0.3 pp a month**, detection within **12 months** (6 months is also reported, the horizon the alarm was judged on).
- **Pass:** detection of at least **80% within 12 months at 0.2 pp a month** (the current alarm detects 0% there), with the no-drift average run length at least 48 months.
- **Real series (reported, not a pass condition):** when and whether it flags the 2022 to 2025 decline.
- **Expectation, stated now:** it catches drifts of 0.2 pp a month or more. The real decline is about 0.03 pp a month; I do not expect it to flag that early, and a late or no flag is reported as it is.

**Decision metric (reported, no pass or fail).** Each test month, among segments with at least 100 category visits in the previous month, rank segments by headroom = visits x max(0, market share - segment share). Predicted headroom uses the model's predicted share and the previous month's visits; realised headroom uses the actual share and visits. The metric is the overlap of the predicted and realised top 10 (0 to 1), averaged over the test months, for the serving logistic regression against the last-month-share baseline, with a whole-month bootstrap interval of the difference. No claim beyond the numbers is made.

## 5. M6: competitor shares (descriptive)

- **Product groups, frozen before any group-level result is looked at** in `data/reference/competitor_groups.csv` (product name to group): **Zilretta**; **triamcinolone IR** (Kenalog, generic triamcinolone and other triamcinolone products); **other injectable corticosteroids** (the remaining products with `treatment_category = generic_corticosteroid`: Depo-Medrol, betamethasone, methylprednisolone, dexamethasone, Celestone and the rest); **other**. Products are assigned by the name rules in the file; every product appears once; a test checks the file covers all 160 products and the visit totals add up to the warehouse.
- **Outputs:** by year and by quarter, Zilretta's share of the visits of Zilretta plus all injectable corticosteroids; Zilretta against triamcinolone IR alone; and each group's share of injectable-corticosteroid visits in 2022 (the peak year) against the latest 12 months (Aug 2024 to Jul 2025), with the total volume of the group so that a fall in the whole category (the 2024 dip) can be told apart from a fall in Zilretta alone.
- **Rules:** no significance test and no verdict beyond the tables; no cause is claimed. These are shares of product-visits inside IQVIA's panel (a patient visit can count under more than one product), so the unresolved panel-coverage caveat of DL-62 applies in full and is stated beside every number.

## 6. Leakage control and tests

- M1's offset uses only predictions and actuals of months before the one being predicted; a test rewrites everything from month *t* onward and the corrected prediction for *t* must not move. A deliberately leaky variant (window including month *t*) must be caught.
- M4's interval for month *t* uses only scores from earlier months; the same rewrite test applies to both tasks.
- M5's CUSUM statistic at month *t* uses only changes up to *t*; planted drift of known size must be detected, and a no-drift series must not alarm at the stated rate.
- Hand-computed answers on small series for the offset, the ACI update, the standardised residuals, the CUSUM recursion, the top-10 overlap and the group shares.
- The run uses the same harness code paths as the feature test; the full run is repeated once and the outputs must be byte-identical (seeds fixed).

## 7. Build, run and report

1. Write this plan and DL-73 (this commit), before any code.
2. Build the pieces in new modules beside the existing modelling code, with tests; no real run yet.
3. One real run of M1, M4 and M5, and the M6 tables, repeated once for identical output. Results to `data/reference/modelling_improvement_results.json`.
4. Report in a notebook, `docs/modelling_improvement_results.md` and a decision-log entry (DL-74), whatever the result. Model cards change only if something is adopted, and adoption is the owner's decision.

## 8. What is expected, stated now

M1 reduces the bias and gains almost nothing in log-loss; M4 narrows the national interval and gives segments an interval that is roughly calibrated; M5's CUSUM detects drifts of 0.2 pp a month or more and does not flag the real, slower decline early; M6 shows who gained, with every number carrying the panel caveat. Anything less is reported as it is.

## 9. Disclosures

- The 7% bias, the interval coverage and width, and the alarm's blind spot were seen before this plan (they are in the Phase 4 results): M1, M4 and M5 are designed with that knowledge, so they are targeted fixes, not blind tests.
- The thresholds (+-3%, the 10% width cut, 80% detection at 0.2 pp, the CUSUM grid) were chosen before any run and are not changed after results.
- M1 is a single pre-set candidate on the 5th percentile; M4 and M5 are judged by coverage and simulation rules, not by hypothesis tests, so no multiplicity correction applies; the decision metric and M6 are descriptive.
- The competitor groups are assigned from product names and manufacturers in IQVIA's own file; the category label `generic_corticosteroid` comes from the project's treatment-category map.
