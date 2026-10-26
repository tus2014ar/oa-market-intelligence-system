# Model card: decline alert (T3, logistic regression)

**Status: promoted at 1, 3 and 6 months under the registered rule, but read the caveat: most of the gain is regression to the mean, and only the 1-month alert shows a reliable gain over a simple "it just spiked" rule.** The alert says a segment's share is likely to be at least 30% lower in a few months. It does not say why. Decisions: [`decision_log.md`](decision_log.md) DL-78 (and its T3 amendment) and DL-79. Plan: [`modelling_plan.md`](modelling_plan.md) section 14. Results: [`modelling_results.md`](modelling_results.md), notebook 15, `data/reference/modelling_results.json` and the post-hoc file `modelling_posthoc.json`.

## Model

| | |
|---|---|
| Task | For a segment at an origin month *t0*, will its share in month *t0 + h* be at most 70% of its share in *t0*? h = 1, 3, 6. Only segments with a positive origin share and at least 100 category visits in *t0* are scored |
| Served model | Logistic regression classifier on the T2 inputs (B0 + G4), one model per horizon, `C` in {0.01, 0.1, 1} chosen inside each training window, no class weights |
| Alert threshold | The score cut-off that maximises F1 over the last 12 inner origins of the training window (each predicted by a model trained only on rows whose outcome month is at or before that origin); never chosen on a test month |
| Alternatives evaluated | Gradient boosting and a random forest on the same inputs; baselines: recent drop, small segment, always alert |
| Code | `modeling/alerts.py`, `horizons.py`, `run_modelling.py`; post hoc: `modeling/posthoc.py` |
| Protocol | Walk-forward from the 24th month of data; 46, 44 and 41 test months; PR-AUC primary, paired bootstrap on whole outcome months (2,000 draws), the 1.67th (h = 1) and 0.83th (h = 3, 6) percentiles |

## Intended use

- As supporting evidence of which segments are likely to fall back, **one month ahead**, in a review where a person looks at the flagged segments.
- As a way to size how many declines are statistical noise in small segments.

**Not intended for:** an automatic trigger of action; an explanation of why a segment's share is falling; the 3 and 6 month horizons, where it adds nothing reliable over a one-line rule.

## Data and inputs

- **Source:** the warehouse (`gold_segment_adoption` and the T2 feature table); no patient-level data.
- **Scored rows:** 3,183, 3,067 and 2,893 test segment-months at h = 1, 3, 6, of which 729, 735 and 824 are declines (22.9%, 24.0%, 28.5%).
- **Inputs:** the T2 inputs, all from the origin or earlier. Eligibility and the label's reference share use the origin month only.
- **Leakage controls:** a test that rewrites every value after the origin and requires the scores and the threshold at the origin not to move; the threshold's inner fits are tested to stop at their own inner origin; the label rule (exactly 30% counts, a zero origin share and fewer than 100 visits are not scored) is tested by hand.

## Evaluation (PR-AUC, higher is better; the decline share is the no-skill level)

| Horizon | Decline share | Recent drop (registered) | Small segment (best baseline) | Logistic | Lower end of gain over best baseline |
|---|---|---|---|---|---|
| 1 month | 0.229 | 0.152 | 0.379 | **0.553** | +0.139 |
| 3 months | 0.240 | 0.166 | 0.366 | **0.513** | +0.110 |
| 6 months | 0.285 | 0.207 | 0.397 | **0.510** | +0.084 |

At the chosen threshold the logistic regression has precision / recall / F1 of 0.49 / 0.64 / 0.56 (952 alerts) at 1 month, 0.45 / 0.65 / 0.53 (1,067) at 3 months and 0.42 / 0.64 / 0.51 (1,250) at 6 months. "Always alert" has F1 0.37, 0.39 and 0.44.

## The caveat: regression to the mean (post hoc)

The registered "recent drop" baseline was written with its sign backwards, so it scored below no skill. A segment's monthly share is noisy and a month that spiked above its recent average tends to fall back, which is what the label counts as a decline. With only that sign flipped the baseline scores 0.504, 0.494 and 0.506 PR-AUC. Against it the logistic regression's gain is:

| Horizon | Gain over the flipped baseline | Lower end (registered percentile) |
|---|---|---|
| 1 month | +0.049 | +0.013 |
| 3 months | +0.019 | -0.013 |
| 6 months | +0.004 | -0.030 |

So the alert's skill beyond "this segment just spiked" is reliable only at 1 month. This check was added after the results were seen and does not change the registered result.

## Robustness

The ordering of methods is the same without the COVID months, without the 2024 dip months, without PEDIATRICS, and with a 20% or 50% fall rule instead of 30%. The logistic regression is the highest-scoring model in every scenario.

## Limitations

- **Mostly noise-driven.** Declines in small segments are largely sampling noise; the alert is good at saying which segments just spiked, not at predicting real change.
- **The 1-month horizon is the one a real user cannot act on:** IQVIA publishes about 40 days after a month ends, so "1 month ahead" is really about 2.
- **A flag, not a probability of loss.** Precision is about 0.45 to 0.49: roughly half of the alerts are not declines.
- **The 30% rule and the 100-visit floor were sized after looking at how often declines occur,** not after any model result (plan section 12).
- **72 months and overlapping origins** make the 3 and 6 month results weaker.

## Maintenance

To be refit each month in the production phase together with the segment forecast (not yet built). Under the post-hoc reading only the 1-month alert would be kept; that adoption choice is recorded in DL-79.
