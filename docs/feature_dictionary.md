# Feature Dictionary

The model-ready feature matrices the classifiers train on. They are computed **on demand from the Gold tables** by `src/oa_market_intelligence/features/` (`monthly.py`, `segment.py`); nothing is written back to the warehouse and the Gold schema is unchanged. Each feature was prototyped and traced to an EDA finding in [`notebooks/02_eda_cleaned_data.ipynb`](../notebooks/02_eda_cleaned_data.ipynb) §13, and the pipeline versions reproduce that notebook's output exactly on the real warehouse (all 72 months × 23 features, and all 8,563 segment rows).

```python
from oa_market_intelligence.features.monthly import (
    load_monthly_gold, compute_monthly_features, model_ready_monthly)
from oa_market_intelligence.features.segment import load_segment_gold, compute_segment_features

monthly = compute_monthly_features(load_monthly_gold(engine))   # 72 rows, NaN during warm-up
train_ready = model_ready_monthly(monthly)                      # 59 complete rows
segments = compute_segment_features(load_segment_gold(engine))  # 8,563 modelling rows
```

## The leakage rule

A model predicting month *t* may only use information available at the end of month *t − 1*. Every monthly feature is computed from the previous month's row or from windows that end at *t − 1*. Calendar and event features depend only on month *t*'s own date, which is known in advance.

This is enforced by a generic test, not feature by feature: `tests/test_features_monthly.py` rewrites every measurement from month *t* onward and requires month *t*'s features not to move. The segment target encoding has the same test (`tests/test_features_segment.py`). Reintroducing the original current-month-inclusive rolling mean makes the monthly test fail.

## Monthly features (`gold_visit_share_monthly`)

Target: the Gold table's stored `direction_label` (Up / Down / Flat; the trailing 12-month z-score rule, PROPOSAL §18.2).

| Feature | Definition | Why (EDA finding) |
|---|---|---|
| `share_lag_1`, `_2`, `_3` | `visit_share` 1, 2, 3 months earlier | Share changes slowly; past share is the strongest legitimate signal (§9) |
| `share_mean_prior_3m`, `_6m` | Mean of the 3 / 6 months before this one (equals the Gold `visit_share_roll_*` columns) | Smoothed recent level |
| `share_gap_to_prior_6m` | `share_lag_1` minus the mean of the 6 months before it | Distance from recent level (mean reversion) |
| `share_change_lag_1`, `_2` | Month-over-month change in share, 1 and 2 months earlier | After a rise the next month tends to fall back (§9) |
| `share_change_std_prior_12m` | Std of the previous 12 monthly changes | How volatile share has recently been (the same quantity that sets the label threshold) |
| `share_rolling_z_lag_1` | Last month's share as a z-score against the 12 months before it | Share relative to its recent norm (§12.3) |
| `branded_injectable_growth_lag_1`, `generic_corticosteroid_growth_lag_1`, `nsaid_otc_growth_lag_1` | `log1p` difference of the category's visits, previous month vs the month before | Skewed counts become growth rates (§4) |
| `nsaid_mix_lag_1` | Previous month's NSAID share of the three-category total | Denominator mix (§9) |
| `office_mix_lag_1`, `telehealth_mix_lag_1` | Previous month's office / telehealth share of place-of-service visits | Care-setting mix (COVID shift) |
| `month_sin`, `month_cos` | Cyclical month, so December and January are adjacent | Seasonality (§9, §13) |
| `is_december`, `is_january` | Month flags | The strongest seasonal pattern found in the EDA: December averages +0.16pp, January −0.28pp. This is an in-sample pattern in a few years of data; the trained models did not use it to predict out of sample (permutation importance of the calendar inputs is about zero), so treat it as descriptive |
| `months_since_launch` | Months since the Oct 2017 FDA approval | A clock; kept in place of the redundant `year` and `quarter` |
| `covid_shock` | 1 for Mar–May 2020 | Event flag (§10, §11) |
| `dip_2024` | 1 for Mar–Jul 2024 | Event flag; a likely claims-supply disruption (PROPOSAL §10) |

Not used, deliberately: the same-month category and setting counts (they are the numerator and denominator of the target), `year` and `quarter` (redundant with `months_since_launch` and `month_number`), and the constant and empty Gold columns (§8).

### On the real warehouse

- 72 monthly rows; the first 13 lack enough history (the label needs a 12-month volatility window plus one difference).
- `model_ready_monthly` keeps **59 months** (7 Up / 43 Flat / 9 Down).
- `covid_shock` falls entirely inside the warm-up months, so it is constant in the 59 rows and is dropped by default (`drop_constant=True`): a zero-variance column breaks standardisation and carries no signal. That leaves **22 features**; pass `drop_constant=False` to keep all 23.
- With 59 months and only 16 Up/Down cases, treat any feature ranking as indicative. Use time-ordered validation and a small regularised feature set.

## Segment features (`gold_segment_adoption`)

Rows are segments (month × specialty × demographic group) with at least 20 category visits and at least one earlier month of specialty history. Target: `segment_visit_share`. `sample_weight` is the segment's total category visits, so large segments count more.

| Feature | Definition | Why |
|---|---|---|
| `specialty_prior_share` | The specialty's volume-weighted Zilretta share over **all earlier months** (a target encoding) | One number replaces 50 specialty dummies; specialty is by far the strongest driver of segment share (correlation ratio η = 0.67 on segments with ≥ 20 visits) |
| `specialty_grouped` | Specialties under 1% of segment rows become `RARE (grouped)`; the real specialty `OTHER` stays separate | 22 of 50 specialties are rare |
| `age_ordinal` | Position of the age band, 0 (`00 TO 02`) to 8 (`85 +`); `UNSPECIFIED` is NaN | Ages are ordered, not nominal |
| `gender_FEMALE`, `gender_MALE` | 0/1 flags (`UNSPECIFIED` is the baseline) | |
| `log_total_visits` | `log1p` of the segment's category visits | Volume is heavily skewed |
| `month_sin`, `month_cos` | Cyclical month | Seasonality |

On the real warehouse this gives **8,563** modelling segments; `specialty_prior_share` correlates 0.61 with the segment's actual share, and its deciles sit almost on the diagonal. The first month of each specialty has no prior history and is dropped.

## Task A features (`features/segment_task.py`, added October 2026)

These are the inputs for predicting a segment's share next month (Phase 4, Task A). One row per segment and month *t*; **every input uses month *t-1* or earlier**, and the month-*t* answer sits in `y_`-prefixed columns that a model must never see. A prediction is made only for segments with at least 20 category visits in month *t-1*. This set replaces `log_total_visits` above (a same-month column) with last month's volume.

| Feature | Definition |
|---|---|
| `specialty_grouped`, `specialty_prior_share` | Specialty (rare ones grouped) and its volume-weighted Zilretta share over all earlier months |
| `seg_prior_share` | The segment's own volume-weighted share over all earlier months |
| `age_ordinal`, `gender_FEMALE`, `gender_MALE` | As above |
| `seg_share_lag1`, `seg_share_roll3` | The segment's share last month, and its mean over months *t-3* to *t-1* |
| `seg_margin_lag1` | Last month's segment share minus last month's market-wide share |
| `seg_log_visits_lag1` | `log1p` of the segment's category visits last month |
| `seg_high_lag1` | 1 if the segment's share last month was above the market-wide share, else 0 (the raw sign, not the interval label) |
| `market_share_lag1`, `market_change_lag1` | The market-wide share last month, and its change from the month before |
| `month_sin`, `month_cos` | Cyclical month |

Four count columns (`seg_prior_z`, `seg_prior_t`, `spec_prior_z`, `spec_prior_t`: cumulative Zilretta and category visits to last month) are kept so the history shares can be smoothed; they use only earlier months too. The outcome columns are `y_share`, `y_visits`, `y_market`, `y_raw_above` and `y_label` (High, Low or Undetermined from a 95% Wilson interval, or High or Low in the two-label fallback). **Leakage control:** a test rewrites every month from a cut onward and requires all earlier inputs not to move; it fails when a leak is injected on purpose, and the same check was run on the real table (largest change 0). One accepted detail: rare specialties are grouped from which specialties appear, never from visit counts or shares. Decisions: DL-48, DL-49.

## Candidate families (built 8 Oct 2026, not yet run; DL-71)

Built in `features/candidate_families.py` for the pre-registered test in [`feature_engineering_plan.md`](feature_engineering_plan.md). They are **candidates**: nothing here is a model input until a family passes the plan's test. Every column uses information from month *t-1* or earlier (the IQVIA-derived families are covered by the generic leakage test; the outside families read the as-of table of `mart.py`, so a value is used only once it was public). A column that can be missing is zero-filled with a `*_missing` indicator (1 where it could not be computed), so no NaN reaches a model. A family is added to the reference model through `extra_columns` (`segment_models.design_frame`, `fitter_for`, `fit_predict_for`, `TunedPredictor`); the default, with no extra columns, is unchanged.

| Family | Task | Columns | Definition |
|---|---|---|---|
| **FA1 shrinkage** | A | `fa1_logit_shrunk_k{5,20,80}`, `fa1_weight_k{5,20,80}` | The logit of the segment's cumulative share to *t-1* shrunk toward its specialty's: (z + k x prior) / (n + k); the prior is the specialty's cumulative share, or the market's when the specialty has no history. The weight is n / (n + k). k is chosen inside the training window. The first month has no history (NaN there; those rows are never scored). |
| **FA2 activity history** | A | `fa2_months_active_prior`, `fa2_months_since_branded`, `fa2_log_cum_branded` | Months the segment appeared before *t*; months since its last month with a Zilretta visit (24 if never or longer); log1p of its cumulative Zilretta visits to *t-1*. |
| **FA3 specialty momentum** | A | `fa3_logit_spec_share_lag1`, `fa3_spec_change_3m`, `fa3_missing` | The specialty's volume-weighted share in *t-1* (logit) and its change from *t-4* to *t-1*. |
| **FA4 seasonal gap** | A | `fa4_seasonal_gap`, `fa4_missing` | The mean over earlier years of the specialty's share in the same calendar month minus its share for that year. EDA-informed (not a clean test). |
| **FA5 market outside features** | A and B | `fa5_price_ratio`, `fa5_price_ratio_chg_4q`, `fa5_sales_yoy`, `fa5_months_since_event`, `fa5_price_chg_missing`, `fa5_sales_missing` | As of *t-1*: the latest known J3304-to-J3301 price ratio and its change from four quarters earlier; company net sales growth against the same quarter a year earlier; months since the last known event (36 if none). Same values for every segment in a month. |
| **FA6 Medicare adoption** | A | `fa6_medicare_adoption`, `fa6_medicare_missing` | The Medicare adoption rate of the segment's specialty group as known as of *t-1* (the 11 approved groups); missing otherwise. Known for 31 of the 72 months. |
| **FB1 seasonal gap** | B | `fb1_seasonal_gap`, `fb1_missing` | The same quantity as FA4 for the national share. EDA-informed. |

Tests (`tests/test_candidate_families.py`): hand-computed answers for every family, the generic leakage test for FA1 to FA4 and FB1 (rewrite everything from month *t* onward, the features for *t* must not move; a deliberately leaky variant is caught), the as-of behaviour of FA5 and FA6, agreement of FA1 with the cumulative counts of the existing Task A rows, and the model hooks (a planted column is used by logistic, gradient boosting and forest; an empty family changes nothing; a missing column is an error).

