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
| `is_december`, `is_january` | Month flags | The clearest signal found: December averages +0.16pp, January −0.28pp |
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
