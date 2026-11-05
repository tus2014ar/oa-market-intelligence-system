# Modelling round 2: results (DL-92, DL-96)

The tests are the seven fixed in [`modelling_round2_plan.md`](modelling_round2_plan.md) (DL-92, with the implementation choices in its sections 14 to 16) before any code was run. The code and the plan were not changed after the first result. The run was repeated once from the same code, data and seed (0; 2,000 bootstrap draws) and the two output files are **byte-identical**. Raw numbers: [`data/reference/modelling_round2_results.json`](../data/reference/modelling_round2_results.json); runner: `modeling/round2/run.py` (`oa-model-round2 --run`). One check was added **after** the results were seen and is labelled post hoc: [`data/reference/modelling_round2_posthoc.json`](../data/reference/modelling_round2_posthoc.json), `modeling/round2/posthoc.py`.

**Round 2 is exploratory.** The experiments were chosen after seeing round 1, on the same test months. A pass is **provisional**: it is re-run on months after July 2025 and adopted, under a new decision, only if it holds there. **Nothing served has changed**: no warehouse table, `serving_models.json` or site wording.

## In one table

All seven tests use one strict bar, the 0.71th percentile of the paired resampling (0.05 divided by the 7 tests).

| Test | Result | In plain words |
|---|---|---|
| **E1a** correction factor, 1 month | **passes** | The forecast was 5.5% too high; after the correction it is 1.0% too high. No less accurate. |
| **E1a** correction factor, 3 months | **passes** | The forecast was 10.9% too high; after the correction it is 3.0% too high. No less accurate. |
| **E1b** rolling 36-month window, 1 month | no pass | Slightly more accurate, but still 3.9% too high (limit 2.5%). |
| **E1b** rolling 36-month window, 3 months | no pass | Slightly more accurate, but still 8.1% too high (limit 5%). |
| **E2** lasting-decline top 10, 1 month | no pass | Half the top 10 are right (49% against 16% by chance), but a simple rule does as well. |
| **E2** lasting-decline top 10, 3 months | no pass | 43% right (against 16%), and the simple rule does better (46%). |
| **E3** ranking winners and losers, 3 months | **passes**, with a caveat | Orders segments well (rank correlation 0.49), but most of it is "what rose falls back". |

**3 of 7 tests pass.** The plan treated "none" as a normal outcome; here two ideas hold up and one does not.

## E1: can the upward bias be removed?

Round 1 found the segment forecast runs high: +5.5% at 1 month and +10.9% at 3 months. Two fixes were tried. A pass needs all three: the bias within 2.5% (1 month) or 5% (3 months); no more than 0.1% less accurate than the uncorrected forecast (the paired lower end); and still better than "same as last month" by round 1's own rule.

| | Uncorrected | E1a: correction factor | E1b: 36-month window | "Same as last month" |
|---|---|---|---|---|
| **1 month** bias | +5.5% | **+1.0%** | +3.9% | +4.0% |
| 1 month log-loss | 0.11267 | 0.11264 | 0.11265 | 0.11317 |
| **3 months** bias | +10.9% | **+3.0%** | +8.1% | +6.8% |
| 3 months log-loss | 0.11187 | 0.11175 | 0.11179 | 0.11210 |

- **E1a** multiplies each forecast by (actual divided by predicted over the last 12 resolved months), limited to 0.7 to 1.3. It removes most of the bias and does not cost accuracy: the paired gain over the uncorrected forecast is +0.03% of log-loss at 1 month (lower end −0.004%, inside the 0.1% allowance) and +0.10% at 3 months (lower end +0.02%, reliably positive). It also still beats "same as last month" (by 0.47% and 0.31%). It passes in all four scenarios (without the COVID months, without the 2024 dip, without PEDIATRICS).
- **E1b** (fitting only on the last 36 months) is slightly and reliably more accurate than the uncorrected model (+0.02% and +0.07%), but it leaves most of the bias, so it fails the bias limit at both horizons.
- **The factor is not active for the first 12 months** of the test period, so 34 of 46 (1 month) and 30 of 44 (3 months) test months are corrected. In those months the bias is +0.1% and +0.5%, against +6.9% and +13.9% uncorrected. The factor at the last test month is 0.95 (1 month) and 0.86 (3 months): the corrected forecast is currently 5% and 14% below the model's.
- **What the correction does not do.** It is one number for every segment, so it does not fix the differences between them. After correcting, segments with under 50 visits a month run **low** (−9.9% at 1 month, −7.2% at 3 months; they were −4.6% and +3.9% before), and some specialties stay high at 3 months (INTERNAL MEDICINE +32%, RHEUMATOLOGY +25%, and the catch-all OTHER group, 80 segment-months, +324%). Segments with 1,000 or more visits go from +5.7% to +1.2% at 1 month and from +11.1% to +3.2% at 3 months.

## E2: an alert for lasting declines, as a top-10 list

The label: a fall to 70% of the origin share at the horizon that is still at 85% of it a month later, among segments with a positive share and at least 100 visits at the origin. The measure: of the 10 segments the method ranks riskiest each month, how many truly had a lasting decline, averaged over the test months. A pass needs a precision at least twice the base rate **and** a win over the best comparator (paired lower end above zero).

| | Base rate | Method | Lift | Corrected "just spiked" rule | Round 1's alert | Small-segment rule | Random |
|---|---|---|---|---|---|---|---|
| **1 month** (45 test months) | 15.6% | **48.7%** | 3.1× | **49.6%** | 48.0% | 30.2% | 16.9% |
| **3 months** (43 test months) | 16.3% | **42.6%** | 2.6× | **46.3%** | 44.0% | 27.0% | 18.6% |

- The first condition holds: the top 10 are 3.1 and 2.6 times better than chance. The second fails: the method does **not** beat the best comparator, the corrected "just spiked" rule from round 1's post-hoc check. At 1 month it is level (difference −0.9 points, lower end −5.3 points); at 3 months it is worse (−3.7 points, lower end −8.4 points). Round 1's own alert, scored on the new label, does about as well as the method too.
- So a brand manager can be told "about half of the 10 segments on the list will show a lasting decline" and that is much better than chance, but the same list comes from a one-line rule (flag the segments whose latest share is above their 3-month average). The trained model adds nothing to it. This is the round 1 finding again: most of the alert's value is regression to the mean.
- The result is the same in all four scenarios.

## E3: does the model rank winners and losers?

At 3 months, over 44 test origins and 3,067 segment-months: the rank correlation between the predicted and the actual change in a segment's share, and the share of the model's top fifth that land in the top two fifths of actual change.

| | Rank correlation | Top-fifth hit rate (random pick: 41%) |
|---|---|---|
| **Segment model** | **+0.49** | **70%** |
| Random order | +0.01 | 41% |
| Specialty's 3-month change continuing | −0.14 | 35% |
| Segment's own 3-month change continuing | −0.38 | 21% |

- The registered rule is passed: the model beats the best comparator, which is the **random order**, with a paired lower end of +0.42 (at the 0.71th percentile; the difference is positive in all 2,000 draws) and in all four scenarios.
- **How to read it.** The two "trend continues" comparators score *below* random, because trends reverse: a segment that rose in the last 3 months tends to fall back, and the other way round. So the registered best comparator is a weak one. The plan said in advance (section 6) that part of any predicted change is mean reversion and that a pass is reported as "ranks well" and not as "predicts what will happen to a segment".
- **Post hoc check, outside the plan's rules.** Flipping the segment-trend score to "the trend reverses" gives a rank correlation of **+0.38** (top-fifth hit rate 61%), and flipping the specialty score gives +0.14 (46%). The model's +0.49 is still above both, by 0.11 (lower end +0.07) over the first, and 0.35 over the second. About three quarters of the model's ranking is therefore reproduced by "reverse each segment's last 3-month move", and the model adds a reliable but smaller amount on top. This check has no bar of its own and is not one of the seven tests.

## What this means

- **E1a is a real candidate.** Removing the upward bias works, costs no accuracy, and survives the robustness checks. It is provisional and has a known limit: it corrects the average, not each segment, and over-corrects the smallest segments.
- **E2 is a null result.** The top-10 list works (3 times better than chance) but a simple rule gives the same list. The alert is not improved, and the site's current alert is unchanged.
- **E3 is a pass with a caveat.** The model orders segments well, mostly because it captures that recent movers fall back. That is a legitimate thing for a brand manager to use ("which segments are likely to give some back and which to gain"), and it must not be read as a forecast of what will happen to a specific segment.
- **Against what the plan said to expect** (section 12): E1 improved more than expected at 3 months, where the plan thought it less likely; E2 failed, which the plan called a real possibility; E3 was labelled uncertain.

## What was not changed, and what stays open

- No model, table or wording served by the site has changed. The correction (E1a) is not applied anywhere; it would need the confirmation described next.
- **Confirmation needs new months.** The test months are the same ones round 1 used, and the experiments were chosen after seeing round 1. E1a and E3 are re-run on months after July 2025 (rules unchanged) when an extract covering them arrives, and adopted only if they hold.
- **The forking paths are disclosed** in section 13 of the plan (the thresholds and windows were chosen by judgement), and the business-shaped measures (precision at 10, rank correlation) have not been validated with a brand manager.
- **IQVIA's panel coverage is still unconfirmed.** If it changed in 2023 or 2024, every model here learned from a series with a break in it, and nothing in this round can fix that.
