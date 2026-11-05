# Modelling round 2: plan (exploratory; written before any run)

**Status: nothing in this document has been run.** It fixes the experiments, the metrics and the pass rules before any code is written, in the same way as [`modelling_plan.md`](modelling_plan.md) did for round 1. It is **exploratory**: it never replaces a round 1 result, and nothing it finds changes what the site serves until the adoption rules in section 9 are met.

## 1. Why a second round, and what it is for

Round 1 (DL-78, DL-79) ended with three weaknesses of the adopted models, each of which costs the business something:

| Weakness | Round 1 result | What it costs |
|---|---|---|
| The segment forecast **runs high** | +5.5% at 1 month, +10.9% at 3 months | Forecasts look rosier than reality; a brand team may over-invest |
| The decline alert's lift is **mostly regression to the mean** | About half of its alerts are real declines; a "just spiked" rule scores 0.50 against the alert's 0.55 | False alarms; the alert can only be used as a 1-month flag |
| The scores are **technical** | Log-loss and PR-AUC | A brand manager cannot tell from them whether the models help with the decisions they make |

Round 2 asks whether each can be improved, in the cheapest way that could work. It does **not** try to beat "same as last month" for the national share again: that was tested with three models at three horizons and nothing won, and the series is 72 months long.

## 2. Why this is exploratory, and what that means

- **The test months have been seen.** Round 1 scored every walk-forward month. The ideas below were chosen after seeing those scores, so a pass here is weaker evidence than a round 1 pass (the "garden of forking paths"). It is **not** adoption.
- **Counting the tests.** Section 8 counts every formal test in this round and sets one stricter bar for all of them together, so that trying several things does not buy an easier bar.
- **A null result is a valid result** and is reported with the same prominence as a pass.
- **The confirmation is a new extract.** The only genuinely unseen test is the first months of data after July 2025. Until those arrive, anything adopted is **provisional** and is shown beside the round 1 figure, never in place of it.

## 3. What stays fixed from round 1

Unless an experiment says otherwise: the data (`gold_segment_adoption`, `gold_visit_share_monthly`, the same 72 months); walk-forward testing with an expanding window, a 24-month minimum and one origin at a time; a fresh model per origin with settings chosen inside the training window only (the last 12 months held back, re-chosen every 6 origins); the base model, the logistic regression on B0 + G4 with the same settings grid; the horizons **1 and 3 months** (6 months is not adopted and is not tested); paired resampling of whole months, 2,000 draws, seed 0; the leakage guards (a training row's outcome month may not be later than the origin) and the rewrite-the-future test; and a full run repeated once with byte-identical output.

## 4. Experiment E1: remove the upward bias

**Hypothesis.** The segment forecast's bias can be reduced using only past errors, without making it less accurate.

**Two variants, declared now** (neither may be added to later):

- **E1a: a correction factor.** At origin *t0*, compute `c = (sum of actual Zilretta visits) / (sum of predicted Zilretta visits)` over the last **12** forecasts at the same horizon whose target month is at most *t0* (so every outcome is already known). Multiply each segment's predicted share by `c`, with `c` clipped to **[0.7, 1.3]**, and cap the result at 1. If fewer than 12 resolved forecasts exist the factor is 1.
- **E1b: a rolling window.** Fit the logistic regression on the last **36** months of rows instead of all rows (everything else as in round 1), on the idea that a falling market makes old, rising months misleading.

**Measures.** Per horizon: the bias (visit-weighted predicted over actual, minus one) and the log-loss per visit.

**Pass rule (all must hold, per variant and horizon).**
1. The bias falls to **at most 2.5% at 1 month and at most 5% at 3 months** in absolute value.
2. **Non-inferiority:** the corrected log-loss is no more than **0.1% worse** than the uncorrected, tested as the lower end of the paired difference at the round's percentile (section 8).
3. The corrected forecast still beats the best baseline by round 1's own rule.

**If it passes:** the site shows the corrected forecast with its smaller bias, marked provisional, beside the round 1 forecast.

## 5. Experiment E2: an alert for lasting declines, ranked

**Hypothesis.** An alert for declines that last, ranked so the team gets a short list, gives a higher hit rate than the threshold alert, because a one-month spike no longer counts.

**New target (T3b), declared now.** For an eligible segment at origin *t0* and horizon *h*, the label is 1 if **both** `share(t0+h) <= 0.70 x share(t0)` **and** `share(t0+h+1) <= 0.85 x share(t0)`; otherwise 0. Eligibility is as in round 1 (a positive share at the origin and at least 100 category visits). The outcome months run one month further than round 1's, so there is one fewer test month.

**Candidates.** The same logistic-regression classifier on the same inputs; no new model. **Comparators:** the corrected "just spiked" rule (round 1's post-hoc finding), the small-segment rule, a random order, and round 1's alert scored on the new label.

**Decision-shaped measure.** **Precision at 10:** each test month, take the 10 segments the model ranks riskiest and count how many truly had a lasting decline; average over the months. Ties are broken by the alert score, then by segment name, so the result is repeatable. PR-AUC is reported as a secondary measure. The **base rate** (the share of eligible segments that truly decline) is reported beside every figure.

**Pass rule (per horizon).**
1. Precision at 10 is at least **2 times the base rate**.
2. It beats the best comparator, with the lower end of the paired difference above zero at the round's percentile.

**If it passes:** the site's alert list becomes "the 10 segments most at risk of a lasting fall", with its measured hit rate stated, marked provisional.

## 6. Experiment E3: do the models rank winners and losers?

**Hypothesis.** Even where accuracy differences are tiny, the segment model orders segments by their expected change in share better than simple alternatives, which is what a brand manager decides on.

**Measure, declared now (horizon 3 months, the planning case).** At each origin, over eligible segments, the **Spearman rank correlation** between the predicted change (`forecast share - share at the origin`) and the actual change; averaged over test origins. Also reported: of the segments the model puts in the top fifth for predicted change, the share that were in the top two fifths for actual change.

**Comparators.** "Same as last month" predicts no change for anyone and so cannot rank: it is reported as a tie, not as a comparator. The comparators are: the specialty's own 3-month change continuing; the segment's own 3-month change continuing; and a random order.

**Pass rule.** The mean Spearman correlation exceeds the best comparator's, with the lower end of the paired difference (resampling whole origins) above zero at the round's percentile.

**Caution, stated now.** Part of any predicted change is mean reversion, as round 1 showed for the alert. That is a real signal for ranking, but the result is reported as "ranks well" and not as "predicts what will happen to a segment". E3 changes how results are reported; it does not change what is served.

## 7. Reported, not decided

For every experiment: the effect sizes at every horizon, including failures; the same comparison without the COVID months, without the March to July 2024 dip and without PEDIATRICS; the bias by segment size and by specialty (E1); and the round 1 numbers beside every new one. The 6-month horizon is not tested.

## 8. The tests counted, and the one bar

| Test | Count |
|---|---|
| E1a at 1 and 3 months | 2 |
| E1b at 1 and 3 months | 2 |
| E2 at 1 and 3 months | 2 |
| E3 at 3 months | 1 |
| **Total formal tests in this round** | **7** |

All seven use the **0.71th percentile** (0.05 / 7) of the paired resampling distribution. A test passes only if the lower end of its paired difference is on the right side of its bar at that percentile.

## 9. Adoption, and what a pass does not mean

- **A pass here makes a result provisional, never adopted.** The site shows it beside the round 1 figure with a plain note that it was found after seeing round 1.
- **Adoption needs confirmation on new data.** When an extract covering months after July 2025 arrives, the passing experiment is re-run on those months only (the rules above, unchanged). It is adopted, under a new decision, only if it holds there.
- **Nothing changes until then** in `model_scores`, `pred_batch`, `serving_models.json` or the site's wording, apart from the provisional note above.
- If an experiment fails, the round 1 choice stands and the failure is recorded in the results document.

## 10. Leakage and testing (written before any code)

- **Time guard (E1a).** The correction factor may use only forecasts whose target month is at most the origin. A test changes every value after the origin and requires the corrected forecast at the origin not to move.
- **Labels by hand (E2).** A small table with a fall exactly at the 0.70 and 0.85 thresholds (counted), just inside them (not counted), a one-month dip that recovers (not counted), a zero origin share and 99 against 100 origin visits (eligibility).
- **Precision at 10 by hand.** A small example with ties, checked on paper, and the base-rate and lift calculation.
- **Spearman by hand (E3).** A six-segment example, and the top-fifth hit rate.
- **Clipping and caps (E1a).** The factor never leaves [0.7, 1.3] and a corrected share never exceeds 1.
- **Reproducibility.** The full run is repeated once and the two outputs must be byte-identical.

## 11. Build, run and report

New code lives apart from the adopted pipeline so it cannot change it: `modeling/round2/` (the correction, the lasting-decline label and ranking, the rank measure), a runner `oa-model-round2`, results in `data/reference/modelling_round2_results.json` and a document `docs/modelling_round2_results.md`. Round 2 writes **no** table to the warehouse, is **not** part of `oa-publish`, and does not touch `serving_models.json`.

## 12. What is expected, stated now

- **E1:** a reduction in bias is likely at 1 month and less likely at 3, where the round 1 bias was largest; a full removal is unlikely. A failure to improve accuracy is a live possibility.
- **E2:** a modest gain at best. A lasting-decline label has a lower base rate, which makes any ranking harder, so a failure of the 2-times-base-rate rule is a real possibility.
- **E3:** uncertain. The simple comparators already carry some of the ranking signal.
- A result in which **none** of the seven tests passes is a valid result, and is what the plan treats as a normal outcome.

## 13. Disclosures

- **No new data** has been tested since round 1; the test months are the same ones.
- **Forking paths.** The three experiments, the thresholds (2.5%, 5%, 0.70, 0.85, 10, 2 times) and the 12-month and 36-month windows were chosen by judgement after seeing round 1, not derived.
- **The business-shaped measures (precision at 10, rank correlation) are our own choice** of what a brand manager would use; they have not been validated with one.
- **IQVIA's panel coverage is still unconfirmed.** If it changed in 2023 or 2024, every model here learned from a series with a break in it, and no experiment in this round can fix that.
- Nothing here claims a model will predict what happens to a segment; round 1's limits all stand.

## 14. Implementation choices fixed while building E1 (before any run)

Fixed on 5 Nov 2026 while the code for E1 was written; nothing had been run on the real data. None of them changes a rule or a threshold above; each settles a point the plan left open.

- **"The last 12 forecasts"** (section 4, E1a) means the forecasts of the **12 most recent target months** at that horizon, pooled over every segment, whose target month is at most the origin. The factor is computed from the **uncorrected** forecasts, so corrections never compound. With fewer than 12 such months the factor is 1, which makes the first 12 + h - 1 test months uncorrected.
- **Rule 1 of the pass rule (the bias limit) is measured over all test months of the horizon**, including the first months where the factor is 1. That is the stricter reading. The bias over only the months where a correction was applied is reported as well, beside the uncorrected bias for the same months.
- **The rolling window** (E1b) keeps the training rows whose **outcome month** lies in the 36 months ending at the origin. Until the history is 36 months long it equals round 1's full history.
- **Non-inferiority** (rule 2) is relative: the lower end of the paired improvement over the uncorrected forecast must be at least minus 0.1% of the uncorrected pooled log-loss, at the round's percentile (0.05 / 7).
- **Rule 3** ("still beats the best baseline") uses round 1's own rule at that horizon: the paired gain's lower end above zero at round 1's percentile (the 1.67th at 1 month, the 0.83th at 3 months).
- **A reproduction guard.** The uncorrected walk is round 1's logistic regression. The run stops unless its pooled log-loss equals the stored round 1 value to 1e-9, so E1 cannot be comparing against something else.
- **Robustness scenarios** (COVID months, the 2024 dip, PEDIATRICS) are run for every variant and reported, as in section 7; only the main scenario decides.
- **Code and tests.** `modeling/round2/e1_bias.py`; `tests/test_round2_e1.py` (the factor by hand and its time guard, the clipping and the cap, the window, the pass rule at each threshold, a small run that must reproduce itself).

## 15. Implementation choices fixed while building E2 (before any run)

Fixed on 5 Nov 2026 while the code for E2 was written; nothing had been run on the real data. None of them changes a rule or a threshold in sections 5 to 8; each settles a point the plan left open.

- **The label resolves at origin + h + 1.** The walk-forward treats the lasting-decline label as a horizon of h + 1 months: a training row is used at an origin only if the *later* of its two outcome months is at most that origin. The first origin is round 1's (after 24 months of data), so the last test month is one earlier: one fewer test month, as section 5 says.
- **The base rate is the pooled share** of eligible test segments (across all test months) that truly had a lasting decline. Precision at 10 is the plain mean of the monthly values, each month counting once. The lift is that mean divided by the pooled base rate, and the first condition of the pass rule is a lift of at least 2.
- **A month with fewer than 10 eligible segments** uses all of them for its precision at 10.
- **The best comparator** is chosen on the whole test set by mean precision at 10, among the four comparators: the corrected "just spiked" rule, the small-segment rule, a random order and round 1's alert. The paired comparison resamples whole test months (2,000 draws, seed 0) at the round's percentile, 0.05 / 7.
- **The corrected "just spiked" rule** is round 1's recent-drop score with its sign flipped, exactly as the post-hoc check defined it.
- **A random order** is a fixed function of each row's month and segment (a hash), so the same rows always get the same scores and the result repeats exactly.
- **Round 1's alert as a comparator** is the same logistic-regression classifier trained on round 1's label (a fall to 70% of the origin share at the horizon) on the same rows, and scored on the new label. Its training rows are those whose *both* outcomes were known at the origin, which is stricter than round 1's own training set. It ranks segments; no alert cut-off is used anywhere in E2, since the measure needs only the ranking.
- **The method's settings** are round 1's alert grid for the logistic regression (C in 0.01, 0.1, 1), chosen on log-loss inside the training window and re-chosen every 6 origins.
- **Robustness scenarios** (COVID months, the 2024 dip, PEDIATRICS) are run and reported as in section 7, removing rows by the month the label resolves (the later of the two outcomes); only the main scenario decides.
- **Code and tests.** `modeling/round2/e2_alert.py`; `tests/test_round2_e2.py` (the label at every threshold and the eligibility edge, the pairing, the one-fewer-test-month check, the training-row time guard, the rewrite-the-future test with a planted leak that it must catch, precision at 10 by hand with ties, the pass rule at each threshold, planted good and random rankings, and a small run that reproduces itself).

