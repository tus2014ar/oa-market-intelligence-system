# Feature-family test: results (DL-72)

The test is the one fixed before any run in [`feature_engineering_plan.md`](feature_engineering_plan.md) (DL-71). Nothing was changed after the first result. It was run twice from the same code, data and seed (0; 2,000 bootstrap draws) and the two output files are byte-identical. Raw numbers: [`data/reference/feature_test_results.json`](../data/reference/feature_test_results.json); runner: `modeling/feature_test.py` (`python -m oa_market_intelligence.modeling.feature_test --run`).

## Task A: segment share (serving logistic regression, 46 test months)

The reference model's log-loss is 0.11274 per visit. An improvement is the reference minus the model with one family added; positive is better. A family passes if the lower end of its paired improvement is above zero at the 0.83rd percentile (Bonferroni for six families). "Robust" means it also passes with the COVID months (Mar to May 2020) removed and with the early-2024 dip (Mar to Jul 2024) removed.

| Family | Main: improvement (lower end) | Months better | COVID removed (lower) | 2024 dip removed (lower) | Verdict |
|---|---|---|---|---|---|
| FA1 shrinkage toward the specialty | -2.3e-6 (-9.1e-6) | 21 / 46 | -1.4e-5 (-2.5e-5) | -6.0e-6 (-1.5e-5) | no pass |
| FA2 activity history | +6.1e-5 (+1.0e-5) | 31 / 46 | +5.8e-5 (+6e-7) | +6.5e-5 (+1.3e-5) | **robust** |
| FA3 specialty momentum | +6.5e-5 (+4.4e-5) | 42 / 46 | +7.3e-5 (+4.8e-5) | +6.5e-5 (+4.3e-5) | **robust** |
| FA4 seasonal gap (EDA-informed) | +3.0e-5 (+1.1e-5) | 33 / 46 | +1.4e-6 (-1.0e-5) | +3.1e-5 (+1.3e-5) | fragile |
| FA5 market outside features | -2.3e-5 (-1.0e-4) | 21 / 46 | -1.7e-5 (-7.6e-5) | -2.1e-5 (-1.1e-4) | no pass |
| FA6 Medicare adoption | +4.8e-6 (-3.7e-5) | 22 / 46 | -2.5e-6 (-4.3e-5) | +8e-7 (-4.2e-5) | no pass |

**Combined model (FA2 + FA3):** compared with FA3 alone, improvement +2.6e-5, lower end -6.2e-6. Not better, not adopted.

## Task B: national share forecast (48 test months, mean absolute error)

Judged against "same as last month" (what serves) at the 2.5th percentile; the comparison with the plain ridge is shown for the size of the effect.

| Family | Against last month: improvement (lower end) | Against plain ridge | Verdict |
|---|---|---|---|
| FB1 seasonal gap (EDA-informed) | -0.028 (-0.066) | -0.006 (-0.020) | no pass |
| FA5 market outside features | -0.039 (-0.081) | -0.017 (-0.057) | no pass |

With the early-2024 dip months removed from scoring (the COVID months precede the first test month, so that scenario does not apply to Task B) the picture is the same. No family improves the national forecast; both are worse than last month.

## What this says

1. **Two families pass the pre-registered rule, and the effect is tiny.** FA3 (the specialty's recent share and its change) and FA2 (how long the segment has been active and when it last had a Zilretta visit) lower log-loss by about 0.06% (6e-5 on 0.1127). FA3 is better in 42 of 46 months, so the direction is consistent, but the size is far below anything a brand manager could see. The plan expected "a few percent" at most; the result is about a hundredth of that.
2. **The outside data adds nothing.** FA5 (price ratio, company sales growth, months since an event) and FA6 (Medicare adoption) do not pass at either level. This matches the availability findings: most public series are too lagged or too coarse to inform a monthly segment.
3. **The national forecast does not improve.** Task B stays at "same as last month".
4. **FA4 is fragile**, as the EDA-informed seasonal gap was expected to be: it holds in the main run and without the 2024 dip, and disappears without the COVID months.

## Disclosures

- FA4 and FB1 were chosen after seeing the EDA; they are labelled not a clean test.
- The combined-model test is conditional and does not exactly control the family-wise error rate (stated in the plan).
- Task B robustness has two scenarios (main and dip removed); the COVID scenario is not applicable. This was an implementation choice and is stated here, not hidden.
- The pass rule is a significance rule; the plan set no minimum effect size, so "passes" and "worth using" are different questions. The second is answered in the decision log entry, not here.
