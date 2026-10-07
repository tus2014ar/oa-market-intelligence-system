# Model Card: Direction Classifier (logistic regression, with random forest and gradient boosting)

**Status: research result, not for use.** The logistic regression, and the random forest and gradient boosting tried afterwards (see "Other model families" below), do not beat the simple baselines or chance on the data available, and all overfit. They are documented so the result, the method and the limits are on record. The data is 72 months; the direction label needs 12 months of earlier changes, so 59 months are labelled and 35 are test months.

## Model

| | |
|---|---|
| Task | Predict next month's direction of Zilretta's visit share (Up / Flat / Down) in OA |
| Algorithm | Standardized features, then L2-regularized multinomial logistic regression (scikit-learn) |
| Settings (fixed before any result was seen) | `C = 0.1` (strong shrinkage), `class_weight = "balanced"`, all 22 features |
| Code | `src/oa_market_intelligence/modeling/models.py` (`make_logistic_regression`) |
| Training protocol | Expanding walk-forward, 24-month minimum, one month ahead, a fresh model per month; scaling is learned inside each training window only |

## Intended use

To test whether the monthly features carry predictive signal for the direction label, and to set the standard any later model has to meet. It is **not** intended to inform a brand decision.

## Data

- **Source:** IQVIA NMTA patient-visit extract, August 2019 to July 2025, aggregated to one row per month (no patient-level data).
- **Target:** the stored `direction_label`, a month's change in visit share measured against the standard deviation of the previous 12 months' changes (PROPOSAL §18.2).
- **Usable rows:** 59 months (the first 13 have no label yet). 24 are used only for initial training, leaving **35 test months: 25 Flat, 6 Down, 4 Up.**
- **Features:** the 22 leak-free monthly features in [`feature_dictionary.md`](feature_dictionary.md).

## Evaluation (35 test months)

| | Accuracy | Balanced accuracy | Recall on Down | Recall on Up |
|---|---|---|---|---|
| Always-majority | 71.4% | 0.333 | 0 of 6 | 0 of 4 |
| Persistence | 62.9% | 0.406 | 1 of 6 | 1 of 4 |
| Seasonal rule | 65.7% | 0.419 | 1 of 6 | 1 of 4 |
| **Logistic regression (primary)** | **40.0%** | **0.369** | **1 of 6** | **2 of 4** |

- **Chance band:** over 1,000 random runs, balanced accuracy has a 95th percentile of 0.46 and recall on Down of 0.33. The model's balanced accuracy of 0.369 is inside that range; about a quarter of random runs score at least as well.
- **Intervals:** balanced accuracy 0.37 with a 95% bootstrap interval of roughly 0.15 to 0.60. Paired differences against persistence and always-majority include zero or only just touch it. Against the seasonal rule, overall correctness is worse for the model (right where the seasonal rule was wrong on 1 month, wrong where it was right on 10; McNemar p = 0.012, one of several comparisons run).
- **Behaviour:** it over-calls moves (10 Up and 9 Down calls against 4 and 6 real ones) and calls only 44% of genuinely Flat months Flat. This is the cost of balanced class weights.

### Overfitting

On its own training months the model scores about **0.86 balanced accuracy and 92% recall on Down**. On the next unseen month: **0.37 and 17%**. That gap persists with strong regularization.

### Exploratory variants (not findings)

Other values of `C`, no class weights, and feature subsets were run and reported in full in [`notebooks/05_logistic_regression.ipynb`](../notebooks/05_logistic_regression.ipynb). None was pre-specified. The best (`C = 0.01`, balanced, balanced accuracy 0.47) clears the chance ceiling by a hair and is one of about a dozen tries, so it is consistent with luck.

## Other model families (added 8 Oct 2026, Step 12, DL-54 and DL-56)

Q2 asks whether a *classifier* can predict direction, and the assignment names logistic regression, random forest and gradient boosting, so the other two were added under the same walk-forward, features and serving rule (fixed in the plan before the run). Each picks its settings inside every training window by balanced accuracy on its last 12 months (ties to the simpler): random forest (300 trees, balanced class weights; depth 2 or 4, minimum leaf 3 or 6) and gradient boosting (100 estimators, balanced sample weights; depth 1 or 2, learning rate 0.05 or 0.1). Code: `src/oa_market_intelligence/modeling/direction_models.py`.

| | Accuracy | Balanced accuracy | McNemar p against persistence |
|---|---|---|---|
| Seasonal rule (serves) | 65.7% | 0.419 | 1.000 |
| Persistence | 62.9% | 0.406 | n/a |
| Gradient boosting | 62.9% | 0.406 | 1.000 (5 months better, 5 worse) |
| Logistic regression | 40.0% | 0.369 | 0.096 |
| Random forest | 45.7% | 0.326 | 0.109 |

- **Chance band:** 95th percentile of balanced accuracy 0.461 over 1,000 random runs; no trained model is above it, so none is promoted (DL-32).
- **Overfitting:** balanced accuracy on the months each model trained on versus the months it was tested on: logistic 0.857 versus 0.369, random forest 0.922 versus 0.326, gradient boosting 0.996 versus 0.406.
- **Reproducible:** two identical runs.

### What a test this small could detect (power statement)

Persistence is right in 22 of 35 months (63%) and differs in correctness from the seasonal rule in 37% of months. Simulating a hypothetical better classifier with that discordance and testing with exact McNemar at the 5% level (5,000 simulations), the smallest accuracy gain over persistence detected with 80% power is **30 percentage points** (power about 10% at +10 points, 40% at +20, 76% at +28; 26 to 30 points for discordance between 0.3 and 0.4). So three model families found no reliable direction signal, and the data cannot tell "no signal" from "a modest signal"; it does not show that direction is impossible to predict.

## Limitations and caveats

- **Very small sample.** 59 months, 16 of them Up or Down. A score from 35 test months with 6 Down months is imprecise (wide intervals), and significance tests have very little power.
- **The label is a surprise measure.** It marks a move larger than recent normal wobble, which makes it hard to predict from the past.
- **Single branded product.** There is no variation in competitor count and no second product to test generalization.
- **Known data issues** that may affect any model: an unconfirmed Mar to Jul 2024 volume dip, a mis-coded Pediatrics prescriber from October 2024 (PROPOSAL §10), and visit counts that are distinct counts rather than additive.
- **No causal claims.** Coefficients are descriptive. In the model trained on all 59 months, the weights agree in sign with the EDA seasonality (December toward Up, January toward Down), but they do not hold up out of sample.

## What it means

The pipeline, the features and the evaluation harness are sound. The finding is that this label is not predictable from these features at this sample size. Options that have not been run: reframe the question (for example "Down versus not Down", or the size of the change), a much simpler model on a small feature set chosen in advance, more history, or reporting the null result as the project's finding. Random forest and gradient boosting have now been run (above) and did not help, as expected at this sample size; the power statement says why a null here is weak evidence about modest effects. Longer history or extra inputs (payer, geography, price, prescription volume) are the realistic routes.
