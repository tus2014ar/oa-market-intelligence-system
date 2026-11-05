# What the segment forecast relies on (DL-91)

A post-hoc explanation of the adopted segment forecast, the logistic regression that serves at 1 and 3 months ([model card](model_card_segment_share.md)). It answers one question: *which kinds of information does the model move its forecasts on?* It does not choose, tune or rank any model (the choice was made under DL-79 before this was run), and it says nothing about what makes a share move.

SHAP was in the original proposal but was not part of the rebuilt project until this step. It covers the segment forecast only: the national forecast is the simple rule "same as last month" (nothing to open), and the decline alert is not explained here.

## Method
- **Exact SHAP for a linear model.** A logistic regression is linear on the log-odds scale, so each input's SHAP value has a closed form: `coefficient x (value - the average value)`. The contributions add up to the model's own log-odds minus the average segment's. `modeling/explain.py` computes this directly, with no extra package.
- **The model is refitted exactly as the monthly scoring job fits it** at the latest origin (July 2025), and `explain` refuses to report unless the refit reproduces the stored `pred_batch` forecasts to 1e-9 and the contributions add up to each forecast to 1e-9. Both hold. The committed file records that they passed, not the raw rounding errors, which differ between machines at the 1e-16 level.
- **The average ("typical segment")** is taken over the feature rows the model was fitted on, unweighted: about 1.85% at 1 month and 1.9% at 3 months.
- **Grouping.** The 13 numeric inputs, the 3 specialty-and-market context inputs and the specialty indicators are grouped into nine plain-language kinds of information (for example the segment's own recent share is its last month and its 3-month mean). A new input with no group is an error.
- **Cross-check.** Where the `shap` package is installed, a test confirms the closed form equals `shap.LinearExplainer` to 1e-9. The pipeline does not depend on that package.

## Results (113 segments, forecast from July 2025)
Share of the forecasts' movement that comes from each kind of information (mean absolute SHAP value, as a percentage of the total).

| Kind of information | 1 month ahead | 3 months ahead |
|---|---|---|
| The segment's own recent share | 40.4% | 46.6% |
| Specialty and market context | 19.0% | 16.8% |
| The segment's long-run share | 14.9% | 9.0% |
| Age and gender | 8.6% | 8.9% |
| The specialty's long-run share | 7.2% | 8.8% |
| Which specialty it is | 4.1% | 5.0% |
| The whole market's share and its recent change | 3.5% | 1.3% |
| How big the segment is | 2.2% | 2.0% |
| Time of year | 0.1% | 1.6% |

- The model mostly follows each segment's **own recent share** (about 40% at 1 month, 47% at 3 months), which is why it stays close to "same as last month" and why its gain over that rule is small and steady.
- **Specialty and market context** (the specialty's share last month and its 3-month change) is the next largest, about a fifth at 1 month. It is the one input group that earned its place in the feature tests (DL-76).
- **Time of year** is under 1% at 1 month; age and gender together are about 9%.

### One forecast explained
Physician Assistant, ages 65 to 74, female: 6,104 visits last month, share 2.05% last month, forecast 1.96% for next month, from a typical segment at 1.85%. Pushes on the log-odds scale (positive raises the forecast above a typical segment's, negative lowers it):

| Kind of information | Push |
|---|---|
| The segment's long-run share | +0.083 |
| The segment's own recent share | +0.082 |
| Age and gender | +0.030 |
| Time of year | -0.001 |
| How big the segment is | -0.008 |
| Specialty and market context | -0.009 |
| The specialty's long-run share | -0.030 |
| The whole market's share and its recent change | -0.040 |
| Which specialty it is | -0.051 |

## How to read it, and what it does not say
- **Pushes are relative to a typical segment, not to last month.** A positive push means higher than a typical segment's forecast.
- **Related inputs share the credit.** The segment's recent share and its long-run share move together, so how the credit splits between them is partly a matter of the method.
- **It describes the model, not the market.** A large share means the model uses that information, not that it causes the share to change.
- **It is a description of the latest origin.** After a new extract, regenerate it.

## Reproduce
```bash
oa-explain          # or: python -m oa_market_intelligence.modeling.explain
```
Reads the committed warehouse, writes `data/reference/shap_summary.json` (a few seconds). It is not part of the monthly publish. `tests/test_explain.py` fails when the committed file differs from a fresh run, with that command in its message.
