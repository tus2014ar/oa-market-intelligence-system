# Evaluation Protocol

How every model for the Up / Down / Flat classifier is tested, and the baselines it has to beat. The code is in `src/oa_market_intelligence/modeling/` (`evaluation.py`, `baselines.py`); [`notebooks/04_evaluation_and_baselines.ipynb`](../notebooks/04_evaluation_and_baselines.ipynb) runs it on the real data and shows the results.

```python
from oa_market_intelligence.features.monthly import (
    load_monthly_gold, compute_monthly_features, model_ready_monthly)
from oa_market_intelligence.modeling.baselines import (
    MajorityClassBaseline, PersistenceBaseline, SeasonalBaseline, StratifiedRandomBaseline)
from oa_market_intelligence.modeling.evaluation import (
    bootstrap_difference, bootstrap_scores, compare_models, score_table, simulate_scores)

ready = model_ready_monthly(compute_monthly_features(load_monthly_gold(engine)))   # 59 months
predictions = compare_models(
    ready, {"always-majority": MajorityClassBaseline, "persistence": PersistenceBaseline,
            "seasonal": SeasonalBaseline})
score_table(predictions)                                              # point scores
bootstrap_scores(predictions["y_true"], predictions["persistence"])   # with 95% intervals
```

Any scikit-learn style model (`fit(X, y)` and `predict(X)`) can be passed in the same way.

## The protocol (PROPOSAL §18.3)

- **Walk-forward.** Train on the months up to *t*, predict month *t*, add month *t* to the training data, repeat. A fresh model is trained before every prediction.
- **Expanding window.** Old months are never dropped, because history is short and this matches how the monthly production run will work.
- **24-month minimum** before the first prediction (two full seasonal cycles).
- **One month ahead.** Each test window is a single month, so "the last training label" is exactly last month's actual direction.

**The leakage guarantee.** A prediction for month *t* uses the labels and features of earlier months plus month *t*'s own features (known in advance). It never sees month *t*'s label. `tests/test_modeling_evaluation.py` enforces this by flipping the label of month *t* and every later month, scrambling the features of every later month, and requiring the prediction for *t* to be unchanged. It runs against the baselines, a logistic regression and a 1-nearest-neighbour model (which memorizes any row it is allowed to see), and fails if the harness is changed to train on the month it is predicting.

### What the data allows

Only **59 of the 72 months** are model-ready, because the label needs 12 months of volatility history plus one difference. With a 24-month start that leaves **35 test months: 25 Flat, 6 Down, 4 Up.** The proposal assumed about 48 folds; 35 is the real number.

## Metrics

| Metric | Why |
|---|---|
| **Recall on Down** (priority) | A falling share is what the brand manager most needs to know about (PROPOSAL §17.2). |
| Precision and F1 on Down | How often a Down call is right (§16 results table). |
| Balanced accuracy | Mean recall over the classes that occur, so a model cannot hide behind the majority class. |
| Macro-F1 | F1 averaged over Up, Flat and Down. |
| Accuracy | Reported, but misleading here: the lazy always-Flat guess wins it. |

## Baselines

From the weakest idea to the strongest:

- **Random by class mix:** draws each prediction at random in the training months' class proportions. This is what chance looks like. One run is noisy, so it is judged over 1,000 seeds (`simulate_scores`).
- **Always-majority:** always the most common training label (ties resolve toward Flat, then Down).
- **Persistence:** next month's direction repeats last month's (PROPOSAL §6.1). The mandatory bar.
- **Seasonal rule:** for each calendar month, the label that month most often had in the training months. The strongest simple baseline, since seasonality was the clearest pattern in the EDA.

### Results on the real data (24-month start, 35 test months)

| | Accuracy | Balanced accuracy | Macro-F1 | Recall on Down | Recall on Up |
|---|---|---|---|---|---|
| Always-majority | 71.4% | 0.333 | 0.278 | 0 of 6 | 0 of 4 |
| Persistence | 62.9% | 0.406 | 0.406 | 1 of 6 | 1 of 4 |
| Seasonal rule | 65.7% | 0.419 | 0.429 | 1 of 6 (1 of 3 Down calls right) | 1 of 4 |
| Random, mean of 1,000 runs | 55.0% | 0.325 | 0.318 | about 12% | about 13% |

**Chance band.** Across 1,000 random runs, balanced accuracy has a 5th to 95th percentile range of **0.21 to 0.46**, and recall on Down **0 to 0.33**. Persistence and the seasonal rule both sit inside it, so at this sample size **none of the baselines can be told apart from luck**. The result is the same with an 18- or 30-month start.

**A practical yardstick for the models:** to claim more than luck, balanced accuracy should be above about **0.46** and recall on Down above about **33%**.

## Uncertainty (bootstrap confidence intervals)

Every score comes from only 35 months, and recall on Down from 6. `bootstrap_scores` re-draws the test months with replacement (2,000 times by default), recomputes each score, and keeps the middle 95%. Resampling can be done in blocks of consecutive months (`block_length`) to respect month-to-month dependence; the intervals are similar either way.

- A per-class score that is undefined on a resample (for example recall on Down when the resample contains no Down month) is **left out, not counted as zero**; `n_valid` reports how many resamples each interval uses. A point estimate that is undefined for the real predictions is NaN (for example precision on Down for a model that never says Down).
- `bootstrap_difference` compares two models on the *same* resampled months, so the interval is for the difference and luck in which months were drawn cancels out.

On the real data the intervals are wide (persistence: balanced accuracy 0.41 with a 95% interval of roughly 0.25 to 0.64; recall on Down 1 of 6 with an interval of 0 to 0.5), and every paired difference between baselines has an interval that includes zero.

## Comparing models (PROPOSAL §18.4)

`mcnemar_exact` compares two classifiers on the same months, looking only at the months where exactly one is right. Under no difference those split 50/50, and the exact two-sided binomial gives the p-value. It scores either 3-class correctness or, with `positive_class="Down"`, whether the model rightly said Down or rightly said "not Down".

**Power is very low.** With 6 Down months, a model must be right where the baseline is wrong on at least 6 months, and never wrong where the baseline is right, to reach p < 0.05. So results are reported as effect sizes with confidence intervals (recall on Down, balanced accuracy, macro-F1) alongside the p-value, compared against the chance band, and "no significant difference" is treated as a legitimate, expected finding (§18.4).

## Not yet built

- The classical time-series check (SARIMA or ETS on the share series, with the label derived from its forecast).
- The trained models (logistic regression, random forest, gradient boosting), MLflow tracking and SHAP.
