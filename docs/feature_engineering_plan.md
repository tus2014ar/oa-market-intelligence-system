# Feature engineering plan (DL-75)

**Status: fixed before any feature is built or run (14 Oct 2026).** Everything below is the rule. A change made after a result has been seen is a deviation: it is written in the decision log with its reason, and both versions are shown. "No group helps" is a valid result.

## 1. Purpose and scope

Turn the warehouse's monthly and segment numbers into leak-free model inputs, document them, and test under rules fixed here which groups of inputs add anything beyond a simple base set. Choosing and tuning the final model is the next stage (modelling) and is out of scope.

- **Starting point:** the validated warehouse (72 months, Aug 2019 to Jul 2025, 160 products, 21,636 segment-month rows), the as-of table of outside series (`mart_signal_asof`), and the EDA findings (notebooks 11 to 13).
- **Out of scope:** model choice and tuning, prediction intervals, the Up/Flat/Down direction label, any new outside data, and any change to the warehouse's existing tables.
- **Lesson carried from the archived attempt** (tag `archive/full-project-2026-10-11`): six families tested there gave gains of about 0.06% of log-loss at best. This plan restarts from the clean EDA state, adds two untested ideas (G5, G7), and expects small effects.

## 2. Targets

Share = Zilretta visits divided by the visits of Zilretta, generic corticosteroids and NSAID/OTC together.

| Target | Grain | Rows | Test months |
|---|---|---|---|
| **T1** national share next month | one row per month | 72 | 48 |
| **T2** segment share next month (specialty × age band × gender) | one row per segment-month | 21,636 | 46 |

## 3. The forecast origin (the time rule)

- **Main run:** the features for target month *t* use data through month *t-1* (one step ahead of the last observation, the standard backtest convention).
- **Honest note:** IQVIA publishes a month about 40 days after it ends, so a real-time user would only have data through *t-2* when month *t* starts. The *t-1* convention is therefore the optimistic one. A **sensitivity run** lags every IQVIA-derived feature by one more month (through *t-2*) and is reported beside the main run. It is not part of the pass rule.
- **Outside series** come only from `mart_signal_asof` (a value is used only once it was public, with the existing one-month as-of lag on top).

## 4. The base set and the seven groups

**Base set B0** (the reference every group is added to; deliberately small):
- T2: the segment's last-month share, its 3-month mean share, log visits last month, the market share last month, the specialty's cumulative share to *t-1* and the month's sine and cosine.
- T1: the national share's lags 1 to 3 and its 3-month mean.

**Groups added one at a time to B0:**

| Group | Features | Applies to | Missing-value rule |
|---|---|---|---|
| **G1 Recent history, extended** | share lags 2 and 3, 6-month mean and std, change over 1 and 3 months, log-visit change | T1, T2 | first months: zero-fill plus `*_missing` flag |
| **G2 Calendar** | `is_dec`, `is_mar_apr`, `seasonal_gap` (the same calendar month's share in earlier years minus those years' mean) | T1, T2 | needs one prior year: zero-fill plus flag |
| **G3 Segment history and pooling** | cumulative share to *t-1*, `shrunk_share` toward the specialty (k in {5, 20, 80} chosen inside the training window), months active, months since last Zilretta visit (cap 24), log cumulative Zilretta visits | T2 | first month has none: flag |
| **G4 Market and specialty context, extended** | specialty share last month, its 3-month change | T2 | none |
| **G5 Competitor volumes (new)** | triamcinolone IR's share of injectable-steroid visits last month, Zilretta-to-triamcinolone ratio, 3-month change in injectable-steroid visits | T1, T2 | none |
| **G6 Outside signals (as-of)** | price ratio and its 4-quarter change, company net sales growth (with its reporting lag), months since the last event (cap 36), promotion in the last 6 months (from July 2020), Medicare adoption (annual, from January 2023) | T1, T2 | zero-fill plus `*_missing` flag |
| **G7 Data-quality flags (new)** | `covid_flag` (Mar to May 2020), `dip_flag` (Mar to Jul 2024), `pediatrics_flag` | T1, T2 | none |

**G5 product groups** are frozen before any group-level number is looked at, in `data/reference/competitor_groups.csv`: Zilretta; triamcinolone IR (Kenalog, generic triamcinolone and rarer triamcinolone products); other injectable corticosteroids (the rest of the corticosteroid category); other. The file is restored from the archive tag unchanged, and a test checks that it covers all 160 products and the visit totals add up.

**Rules for every feature:** uses only months before the origin; no statistic is computed over the whole series; a feature that can be missing has a stated rule and an indicator; tree models may keep NaN, linear models get the zero-fill and indicator.

## 5. Build

- One re-runnable module writes two warehouse tables, `feat_monthly` and `feat_segment_month`, from the published warehouse and the as-of table.
- A feature dictionary (`docs/feature_dictionary.md`) lists, for every feature: definition, source, availability rule, missing-value rule, leakage rule.
- The evaluation harness (walk-forward, whole-month bootstrap, moving-block bootstrap) is ported from the archive tag with its tests; it is not changed.

## 6. Tests (written before any real run)

- Hand-computed answers on small series for every feature.
- **Generic leakage test:** every measurement from month *t* onward is rewritten; the features of month *t* must not move. A deliberately leaky variant must fail it.
- **As-of test (G6):** a planted future value must not enter a feature before its public date; the pipeline's leakage check returns nothing.
- **Coverage test:** the table's rows, feature names and missing counts match the dictionary.
- **Reproducibility:** two builds give identical tables.

## 7. Validation (the Phase 4 harness, unchanged)

- **T2:** the serving logistic regression (C in {0.01, 0.1, 1} tuned inside each training window every 6 test months), walk-forward by month, expanding window, 24-month minimum, metric binomial log-loss per visit. Test months are resampled whole (2,000 draws, seed 0), paired against B0 on the same draws.
- **T1:** a ridge on lags (plain) against "same as last month" (what serves), walk-forward 48 test months, metric MAE in percentage points, moving-block bootstrap (block 6, 2,000 draws, seed 0). A group is judged against last month, and against the plain ridge for the effect size.
- **A group passes** if the lower end of its paired improvement is above zero at the Bonferroni-corrected percentile: **0.05 divided by the number of groups tested in that target** (T2: 7 groups, the 0.71th percentile; T1: G1, G2, G5, G6, G7 = 5 groups, the 1.0th percentile).
- **Robust** if it also passes in two further runs with the same reference: the COVID months and the 2024 dip months removed from training and scoring rows (lag features left as they are). **Fragile** if it passes the main run but not both. G7 cannot be judged by removal (its flags have no variation once the months are removed), so it is judged on the main run only and labelled "not robustness-testable".
- **Combined model:** only if two or more groups are robust; it contains exactly those groups and is adopted only if it beats the best single group by the same rule.
- **Effect sizes** are reported for every group, including failures: mean and interval of the paired gain, months better, and correlation clusters between features.
- **Not allowed:** choosing features by importance on test data, adding or dropping a group after a result, changing a grid, a threshold or a test month, or a second run chosen because the first looked poor.

## 8. Report

1. This plan and DL-75, committed before any code.
2. Build and tests; no real run.
3. One real run of all tests, repeated once for identical output.
4. A notebook, the feature dictionary, a results document and a decision-log entry, whatever the result. The feature tables are adopted into the warehouse only for groups that pass robustly (or as documented candidates if none does).

## 9. What is expected, stated now

Small effects at best. G1 and G3 have the most room (the panel can show a few percent for small segments); G5 is new and may largely restate the share itself; G6 is limited by availability (only price and company sales cover all 72 months, promotion starts July 2020 and Medicare adoption January 2023, both heavily lagged); G7 helps only if it removes a distortion the model would otherwise learn. T1 most likely stays at "same as last month". A null result is a finding, and it fits what the data allows: 72 months, one regime change and no monthly drivers.

## 10. Disclosures

- The EDA (notebook 13) was seen before this plan: the annual hint, the sparse segment panel and the availability of outside series motivated G2, G3 and G6, so those groups are EDA-informed and not a clean test.
- The archived attempt was seen before this plan; the thresholds here are the same harness's, not tuned to its results.
- Choices made at the planning stage: B0 is small on purpose so that groups can show a gain; T2 tests 7 groups and T1 tests 5, so their percentiles differ.
