# Findings, engineering outcomes and public datasets

The detailed results behind the [README](../README.md). Moved here unchanged except for refreshed counts.

## Key findings and business impact

Everything below is descriptive or from pre-registered tests (rules written before the analysis or the models ran; none of it is causal). IQVIA covers 72 months (Aug 2019 to Jul 2025), 160 products, 50 specialties and 7.19M+ visits; Zilretta's share of the three-class visit total rose from about 2.0% (2019) to a peak near 3.4% (Dec 2021) and has fallen since.

| # | Finding | Evidence (quantified) | Business impact |
|---|---|---|---|
| 1 | **Company sales kept rising while IQVIA's Zilretta visits fell** | Visits -14% (2023) and -36% (2024) against net sales +5% and +6%; net sales per IQVIA visit +94% from 2021 to 2024 | The size of the IQVIA decline must not be quoted as a market fact: a target or forecast built on it could be wrong by a large factor. Ask IQVIA whether panel coverage changed (2024 in particular) before using it |
| 2 | **No tested explanation accounts for the gap** | Age and payer mix ruled out (IQVIA 65+ visits -34% against Medicare Zilretta patients -1.5%, a 32-point gap against 15 allowed); care setting ruled out (facility share 0.05% to 0.09%); value per visit +10% in Medicare, about a ninth of the +94% | Narrows the search: the remaining causes (panel coverage, company-side adjustments) are untested and need data we do not hold |
| 3 | **The fall is concentrated** | Orthopedic surgery and physician assistants are 64% of the net fall of 11,256 visits | Where a field or marketing review should look first |
| 4 | **The IQVIA specialty ranking is not confirmed by Medicare billing** | Rank correlation 0.35 (95% interval 0.18 to 0.44) against 0.6 required; 1 of the top 3 specialties shared | Which specialties look "best" depends on the population (Original Medicare against IQVIA's panel); use both views, do not rely on one |
| 5 | **Medicare adoption rose then eased, like the visits** | Share of billing injectors using Zilretta 1.08% (2019), 1.78% (2021 peak), 1.47% (2024): about a sixth below peak | Independent, provider-level support that the turn is real in direction, though smaller than IQVIA's fall |
| 6 | **A stable state list exists** | 2022 vs 2024 state rank correlation 0.92 (51 states); 0.97 without the Medicare Advantage effect; most room to grow in Michigan, Arizona, Florida, Minnesota and Indiana | A defensible shortlist for territory prioritisation |
| 7 | **Promotion fell before the turn, but nothing links it month by month** | Sharpest 6-month fall in payments -81%, coinciding with the Flexion acquisition (19 Nov 2021); no lag from 0 to 6 months passes the correction (smallest p = 0.11) | Do not attribute the share decline to promotion; the timing is suggestive only |
| 8 | **The turn is not the end of pass-through status** | The detected break (March 2022) is 11 months after the 31 Mar 2021 end of pass-through | Removes a tempting but unsupported explanation |
| 9 | **A data-capture dip in Mar to Jul 2024** | Zilretta -47%, corticosteroids -25%, NSAID/OTC -16% over the five months; Zilretta fell about twice as much as the others | Flagged months in every later analysis; results are re-checked without them |
| 10 | **Price relative to the comparator rose, but not cleanly** | Ratio +11.7%, driven by the comparator's limit falling 14% while Zilretta's fell 4% | Not evidence that Zilretta's price moved the share |
| 11 | **Zilretta did not dip in 2020 when most hyaluronic injectables did** | Zilretta Medicare patients 36,500 (2019), 53,400 (2021), 47,200 (2024, 12% below peak); Durolane patients 19,700 to 57,100 (almost threefold) | The competitor set is not moving as one; Durolane is the one competitor that stands out |
| 12 | **The data is thin where it matters for modelling** | 60% of segment-months have fewer than 20 visits and 65% have no Zilretta visit; month-to-month changes have lag-12 autocorrelation +0.27 and lag-1 -0.19; only price and company sales are known for all 72 months | Sets realistic expectations for feature engineering: small effects at best, and "same as last month" will be hard to beat |
| 13 | **Only one input group improves the segment forecast, and none improves the national one** | Of 7 groups tested, specialty and market context gains +0.06% of log-loss (better in 42 of 46 months, robust to COVID and the 2024 dip); no group beats "same as last month" nationally | Price, company sales and promotion do not predict monthly share, so they should not drive a monthly forecast |
| 14 | **The national share is best forecast by last month's value** | Ridge and two ETS models at 1, 3 and 6 months: none beats it (error 0.125, 0.180, 0.239 points); the trained models' 90% ranges cover as little as 47% of months | Plan on the last value with its range; a model would add cost and no accuracy |
| 15 | **Segment forecasts gain a little at 1 and 3 months and run too high** | Logistic regression +0.44% of log-loss (better in 46 of 46 months) and +0.21% (33 of 44), none at 6 months; forecasts +5.5%, +10.9%, +18.2% above actual | A ranking aid and an optimistic estimate: quote the bias beside it and do not plan six months ahead on it |
| 16 | **The decline alert is mostly regression to the mean** | PR-AUC 0.55 against 0.23 no-skill under the registered baselines, but 0.50 once a baseline with a reversed sign is corrected (post hoc); a reliable gain only at 1 month (+0.049); precision 0.45 to 0.49 | Use it only as a 1-month supporting flag; never act on an alert alone |
| 17 | **The forecasts are only as fresh as the last extract** | The data end in July 2025; the monitoring stage marks a latest month older than 100 days as stale and reported 454 days at the last run; the next forecasts are for August 2025, October 2025 and January 2026; no month has arrived to check earlier forecasts against | A new IQVIA extract is what refreshes the forecasts and tests how well the earlier ones held; until then treat the forecasts as a method and a baseline, not as a current view |

### Data-quality findings that shaped the cleaning
- A likely mis-coded `PEDIATRICS` prescriber and miscoded oncology products in the RA extract (RA has about 1,250 visits over 6 years, too sparse to model).
- The Mar to Jul 2024 volume dip across all three product classes (finding 9).
- 11 dataset-level quality checks run before every build against a stored baseline; an error stops the run, and a failed run never replaces the last good database.

### Engineering outcomes
- A re-runnable pipeline from raw Excel to a validated warehouse in about 2.5 minutes; every extract is hashed (SHA-256) per run, and a golden regression test rebuilds the warehouse from the real extracts and compares it with committed values.
- 171 public files (about 24 GB) from CMS, CDC, SEC and FDA were profiled and loaded into a 27-table database with 38 reconciliation checks passing, restorable from a committed manifest.
- A recorded "when could this value have been known" rule for 14 sources, so later features cannot use information from the future.
- About 1,800 automated tests, run in parallel in CI on every pull request, plus an end-to-end job that runs the real extracts through every stage of the publish run twice and requires identical content, and a check of the published database before the monthly workflow commits it.
- Modelling that cannot see the future: a horizon harness that raises if a training row's outcome is after the forecast date, model-level "rewrite the future" tests with a deliberately leaky case that must be caught, and a full run of every model repeated once for byte-identical output.
- Monitoring in every publish run: data freshness and the drift of earlier forecasts against what happened, and a recorded version of the data, code and environment (every prediction row carries its data version), with a runbook for the monthly run.
- Model results stored in the warehouse (`model_scores`, `backtest_batch`) by every publish, a model card for each model that serves, and the adoption of models into serving recorded as a decision (DL-79).

### Public datasets added to the analysis

171 public files (about 24 GB) were downloaded, profiled and loaded into a separate 27-table database (every load reconciled to the raw files; restorable from the committed manifest, see [`docs/external_data_refresh.md`](external_data_refresh.md); the full 134 MB database is local only). Each dataset was added for a stated reason, in a protocol written before any analysis ran ([`docs/external_data_protocol.md`](external_data_protocol.md)).

| Dataset | Source | Rows loaded | What it added |
|---|---|---|---|
| Medicare Part B by provider and service | CMS | about 997K | The specialty ranking test against IQVIA (E1) and provider-level Zilretta adoption by year: 1.08% (2019), 1.78% (2021), 1.47% (2024) |
| Medicare Part B and Part D by geography | CMS | 8.4K and 12.5K | State adoption and room to grow (stable state ranking, 0.92) and the Zilretta against hyaluronic-injectable comparison (E6) |
| Medicare Geographic Variation, and Advantage variation | CMS | 1.8K | Original Medicare beneficiary denominators for the 65+ check in the gap diagnosis, and Medicare Advantage share as a state adjustment |
| NPPES provider registry and NUCC taxonomy | CMS | about 32.5K counts | Provider counts by specialty and state (a snapshot denominator) and the specialty crosswalk |
| ASP drug prices | CMS | 478 | The Zilretta to triamcinolone price ratio (+11.7%, not a clean test) |
| Open Payments (promotion) | CMS | 1.9K by month, 2.8K by type | Promotion timing: the sharpest 6-month fall was -81%, before the turn |
| Company filings | SEC | 45 | Quarterly net sales, which exposed the sales-versus-visits gap (E2b) |
| Arthritis prevalence (PLACES) | CDC | 3.0K | State arthritis prevalence as context for room to grow |
| Dated events (FDA, CMS and company documents) and openFDA approval dates | FDA, CMS, SEC | 14 events | The turn checks (end of pass-through status 31 Mar 2021, the Flexion acquisition 19 Nov 2021) and the FDA-event check on the competitive set |

Two more files, Part D by provider and Medicaid drug utilisation, were downloaded and profiled but are not used in any rule or finding.
