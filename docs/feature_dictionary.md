# Feature dictionary (DL-75)

Every model input of the feature plan ([`feature_engineering_plan.md`](feature_engineering_plan.md)): its definition, its source and the rule that keeps it free of the future. Built by `features/groups.py` and `features/build.py`; written to the warehouse tables `feat_segment_month` (target T2) and `feat_monthly` (target T1). Checked by `tests/test_feature_groups.py`.

**Rules for every column.** For target month *t* it uses data through month *t-1* only (the main run); outside series come from the as-of table, so a value is used only once it was public. A column that can be missing is zero-filled and has a `*_missing` indicator (1 where it could not be computed), so no NaN is stored. The leakage test rewrites every IQVIA value from month *t* onward and requires month-*t* features not to move.

## Targets and keys

| Column | Definition |
|---|---|
| `y_share` | The month's Zilretta share: Zilretta visits over Zilretta, generic corticosteroid and NSAID/OTC visits (segment: over the segment's category visits) |
| `y_visits` | The visits behind the share (the binomial denominator) |
| `y_market` | The market-wide share of the target month (segment table) |
| `month_id`, `specialty_name`, `age_band`, `gender` | The key of a segment-month; `feat_monthly` is keyed by `month_id` |

## Base set B0 (the reference every group is added to)

**T2 (segment):** the Phase 4 reference's inputs: `seg_share_lag1`, `seg_share_roll3` (3-month mean), `seg_margin_lag1` (last month's share minus the market's), `seg_log_visits_lag1`, `seg_high_lag1`, `seg_prior_share` and `specialty_prior_share` (cumulative history shares, smoothed in the model), `market_share_lag1`, `market_change_lag1`, `age_ordinal`, `gender_FEMALE`, `gender_MALE`, `month_sin`, `month_cos`, and the specialty (rare ones grouped).

**T1 (national):** `share_lag1`, `share_lag2`, `share_lag3`, `share_roll3` (their mean), `month_sin`, `month_cos`.

## G1 Recent history, extended (T1 and T2)

| Column | Definition | Source |
|---|---|---|
| `g1_share_lag2` | The segment's share two months before the target month | segment Gold |
| `g1_share_lag3` | The segment's share three months before the target month | segment Gold |
| `g1_share_roll6` | Mean share over the six months up to t-1 (at least 3 present) | Gold |
| `g1_share_std6` | Standard deviation of the share over the six months up to t-1 (at least 3) | Gold |
| `g1_share_chg1` | Share at t-1 minus share at t-2 | Gold |
| `g1_share_chg3` | Share at t-1 minus share at t-4 | Gold |
| `g1_log_visits_chg` | log(1 + category visits) at t-1 minus at t-2 | Gold |
| `g1_missing` | 1 where any G1 value could not be computed (then all G1 values are 0) | - |

T1 uses: `g1_share_roll6`, `g1_share_std6`, `g1_share_chg1`, `g1_share_chg3`, `g1_log_visits_chg`, `g1_missing`.

## G2 Calendar and seasonality (T1 and T2)

| Column | Definition | Source |
|---|---|---|
| `g2_is_dec` | 1 if the target month is December | calendar |
| `g2_is_mar_apr` | 1 if the target month is March or April | calendar |
| `g2_seasonal_gap` | Mean over earlier years of (that calendar month's share minus the year's share); the specialty's for segments, the national for T1 | Gold |
| `g2_missing` | 1 where no earlier year has the calendar month | - |

## G3 Segment history and pooling (T2 only)

| Column | Definition | Source |
|---|---|---|
| `g3_cum_share` | The segment's cumulative Zilretta share up to t-1 (0 when no history) | segment Gold |
| `g3_months_active` | Number of months the segment appeared before the target month | segment Gold |
| `g3_months_since_zilretta` | Months since the segment's last month with a Zilretta visit, capped at 24 | segment Gold |
| `g3_log_cum_zilretta` | log(1 + cumulative Zilretta visits to t-1) | segment Gold |
| `g3_logit_shrunk_k5` | Logit of (cumulative Zilretta + 5 x specialty prior) / (cumulative visits + 5); the prior is the specialty's cumulative share (the market's when the specialty has no history) | segment Gold |
| `g3_weight_k5` | cumulative visits / (cumulative visits + 5) | segment Gold |
| `g3_logit_shrunk_k20` | Logit of (cumulative Zilretta + 20 x specialty prior) / (cumulative visits + 20); the prior is the specialty's cumulative share (the market's when the specialty has no history) | segment Gold |
| `g3_weight_k20` | cumulative visits / (cumulative visits + 20) | segment Gold |
| `g3_logit_shrunk_k80` | Logit of (cumulative Zilretta + 80 x specialty prior) / (cumulative visits + 80); the prior is the specialty's cumulative share (the market's when the specialty has no history) | segment Gold |
| `g3_weight_k80` | cumulative visits / (cumulative visits + 80) | segment Gold |
| `g3_missing` | 1 where a shrunk share could not be computed (the first month of the table) | - |

## G4 Market and specialty context (T2 only)

| Column | Definition | Source |
|---|---|---|
| `g4_logit_spec_share_lag1` | Logit of the specialty's volume-weighted share at t-1 | segment Gold |
| `g4_spec_change_3m` | The specialty's share at t-1 minus at t-4 | segment Gold |
| `g4_missing` | 1 where the specialty's lagged share or its change is unavailable | - |

## G5 Competitor volumes (T1 and T2)

| Column | Definition | Source |
|---|---|---|
| `g5_tri_share_lag1` | Triamcinolone IR's share of Zilretta + all injectable-steroid visits at t-1 | product facts |
| `g5_zil_vs_tri_lag1` | Zilretta visits over Zilretta + triamcinolone IR visits at t-1 | product facts |
| `g5_steroid_chg3` | log of injectable-steroid visits at t-1 minus at t-4 | product facts |
| `g5_missing` | 1 where a G5 value could not be computed | - |

## G6 Outside signals (as-of) (T1 and T2)

| Column | Definition | Source |
|---|---|---|
| `g6_price_ratio` | J3304 to J3301 price ratio, latest value public as of t-1 | ASP (as-of) |
| `g6_price_ratio_chg_4q` | Change of that ratio from four quarters earlier | ASP (as-of) |
| `g6_sales_yoy` | Company net sales against the same quarter a year earlier, as of t-1 | SEC (as-of) |
| `g6_months_since_event` | Months since the last known market event, capped at 36 | events (as-of) |
| `g6_promo_6m_log` | log(1 + Open Payments total of the last six public months) | Open Payments (as-of) |
| `g6_medicare_adoption` | Medicare adoption rate: the specialty group's (T2) or the all-specialty (T1), as of t-1 | Part B (as-of) |
| `g6_price_chg_missing` | 1 where the price change is unknown | - |
| `g6_sales_missing` | 1 where the sales growth is unknown | - |
| `g6_promo_missing` | 1 while fewer than six months of payments are public | - |
| `g6_medicare_missing` | 1 where the adoption rate is unknown or the specialty is not in the 11 groups | - |

## G7 Data-quality flags (T1 and T2)

| Column | Definition | Source |
|---|---|---|
| `g7_covid` | 1 for March to May 2020 | calendar |
| `g7_dip` | 1 for March to July 2024 | calendar |
| `g7_pediatrics` | 1 for the PEDIATRICS specialty (a likely mis-coded group) | segment Gold |

T1 uses: `g7_covid`, `g7_dip`.

