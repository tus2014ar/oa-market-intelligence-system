# OA & RA Market Intelligence System

A monthly-refreshable analytics foundation that tracks Zilretta's visit share against generic corticosteroid injections and NSAIDs in Osteoarthritis (OA) and Rheumatoid Arthritis (RA), built on real IQVIA National Medical and Treatment Audit (NMTA) patient-visit data and a set of public datasets. Built around one stakeholder, an OA/RA Injectable Brand Manager.

**Status:** data profiling, cleaning, ingestion, the data warehouse, exploratory data analysis (EDA), feature engineering and **modelling** are complete and documented. **Next:** the final phase, production hardening and then the serving layer. The headline: price, sales and promotion do not predict monthly share; the national share is best forecast by last month's value; a logistic regression gives a small, optimistic gain for segment share at 1 and 3 months; and the decline alert is mostly regression to the mean (DL-76, DL-79). Nothing is deployed.

Built as a capstone project for **DAAN 888 — Design and Implementation of Analytics System**, Penn State University, School of Graduate Professional Studies (Fall 2026).

**Team:** Tushar and Saakshaat Saini

## The problem

A newer, extended-release branded injectable (Zilretta) is trying to take visit share from decades-old generic corticosteroids and NSAIDs in a high-volume OA market (7.19M+ visits across 6 years in our extract). The system is built around a single stakeholder, an **OA/RA Injectable Brand Manager**, who needs an early signal on competitive share movement before it shows up in a standard quarterly business review.

## Business impact at a glance

- **Do not quote the IQVIA decline as a market fact.** Company net sales rose (+5% in 2023, +6% in 2024) while IQVIA Zilretta visits fell (-14%, -36%), and no tested explanation closes the gap, so a target built on the visit decline could be wrong by a large factor until IQVIA confirms panel coverage.
- **Look at orthopedic surgery and physician assistants first.** They are 64% of the net fall of 11,256 visits; Michigan, Arizona, Florida, Minnesota and Indiana come first for room to grow, on a state ranking that is stable (0.92).
- **Plan the national number on last month's value.** Three trained models were tested at 1, 3 and 6 months and none beat "same as last month" (error 0.125, 0.180, 0.239 points); price, company sales, promotion and competitor volumes add nothing to monthly share.
- **Use the segment forecast as a small, optimistic improvement.** A logistic regression beats the baseline by about 0.4% of log-loss at 1 month and 0.2% at 3 months and not at 6, but runs 5% to 18% too high, so the bias is stated beside every forecast.
- **Treat the decline alert as a 1-month flag, not a prediction.** About half of its alerts are not real declines, and most of its apparent skill is a spiked month falling back, which a post-hoc check added after the results exposed and the project reports in full.
- **The results can be trusted for what they are.** Rules were written before any model ran, leakage tests must catch a deliberately leaky case, a full run is repeated for identical output, and null results are reported as findings.

## The six stages at a glance

**1. Data profiling.** Profiled the IQVIA NMTA extracts (nested pivot sheets; 72 months, 160 products, 50 specialties, 27 demographic groups, 7.19M+ visits) and 171 public CMS, CDC, SEC and FDA files (about 24 GB), and documented every raw field in a data dictionary ([`docs/data_dictionary.md`](docs/data_dictionary.md)). Profiling surfaced a likely mis-coded `PEDIATRICS` prescriber, miscoded oncology products in the RA extract, and a March to July 2024 volume dip across all three product classes.

**2. Data cleaning and preprocessing.** A Pandera validation gate (schema, ranges, allowed values; no rows rejected on the real extracts), a 160-product taxonomy that maps every product to a treatment class, and 11 dataset-level quality checks against a stored baseline (an error stops the run; unmapped products are listed in the run log). The data problems above are documented and kept in the data, with nothing silently dropped; they are handled downstream by explicit sensitivity checks.

**3. Data ingestion and warehouse modelling.** A re-runnable Bronze → Silver → Gold pipeline. The IQVIA warehouse is a **star schema** (4 dimensions: month, product, specialty, demographics; 2 fact tables; 149K+ fact rows; 2 Gold serving tables) with documented DDL ([`docs/database_schema.md`](docs/database_schema.md)) and Silver and Gold data dictionaries ([`docs/silver_gold_data_dictionary.md`](docs/silver_gold_data_dictionary.md)). The public data is a separate fact constellation (5 dimensions, 10 facts, column-level lineage in [`docs/external_data_lineage.md`](docs/external_data_lineage.md)) with 38 reconciliation checks passing. Every extract is SHA-256 audited, a golden regression test guards the warehouse, an as-of layer records when each outside value could have been known, and an atomic publish never replaces the last good database.

**4. EDA and business findings.** Company net sales rose (+5% in 2023, +6% in 2024) while IQVIA Zilretta visits fell (-14%, -36%), so the size of the decline cannot be quoted as a market fact; orthopedic surgery and physician assistants are 64% of the net fall of 11,256 visits; Medicare billing does not confirm IQVIA's specialty ranking (rank correlation 0.35 against 0.6 needed); and a stable state ranking (0.92) puts Michigan, Arizona, Florida, Minnesota and Indiana first for room to grow. All twelve findings are in the table below.

**5. Feature engineering.** Turned the warehouse into model inputs that cannot see the future, and tested which of them help. Seven candidate groups (recent history, calendar, segment history and pooling, market and specialty context, competitor volumes, outside signals, data-quality flags) were built with leakage tests (rewrite everything from month *t* onward and the features for month *t* must not move; a deliberately leaky feature must be caught), as-of dates for outside data and a documented feature dictionary ([`docs/feature_dictionary.md`](docs/feature_dictionary.md)). The rules, thresholds and groups were written before anything ran ([`docs/feature_engineering_plan.md`](docs/feature_engineering_plan.md)), and the full test, repeated once for byte-identical output, used walk-forward validation (46 test months for segments, 48 national), a paired bootstrap and a stricter pass bar for testing several groups at once. **Result:** only specialty and market context improves segment share, by about 0.06% of log-loss, better in 42 of 46 months and robust to removing COVID and the 2024 dip; none of the seven improves the national forecast, which stays at "same as last month". **Business impact:** price, company sales and promotion do not predict monthly share, competitor volumes are a hypothesis worth one more pre-registered look at longer horizons, and the honest planning baseline is hard to beat, so expectations for the models are set before they are built. The adopted base set and that one group are stored in the warehouse as `feat_segment_month` (8,450 scored segment-months) and `feat_monthly` (69 months), rebuilt by every publish ([`docs/feature_results.md`](docs/feature_results.md), notebook 14).

**6. Modelling.** Asked whether any model beats a simple rule at 1, 3 and 6 months ahead for three tasks: the national share, each segment's share, and an alert for segments about to lose 30% of their share. The rules were written before any model ran ([`docs/modelling_plan.md`](docs/modelling_plan.md)): walk-forward testing (about 46 test months), a paired bootstrap, a stricter pass bar for the longer horizons, leakage tests at model level (rewrite the future and the prediction must not move; a deliberately leaky pairing must be caught), and a full run repeated once for byte-identical output ([`docs/modelling_results.md`](docs/modelling_results.md)). **Result:** nothing beats "same as last month" for the national share at any horizon; a logistic regression beats the segment baseline by about 0.4% of log-loss at 1 month and 0.2% at 3 months and not at 6, but its forecasts run too high (+5% at 1 month, +11% at 3, +18% at 6); the decline alert passes the registered test, but a check added afterwards shows that most of its gain is that a month which spiked falls back, with a reliable gain only at 1 month. **Business impact:** use the last value for the national number, treat the segment forecast as a small and optimistic improvement, and use the alert only as a 1-month supporting flag. The results are stored in the warehouse as `model_scores` (222 rows) and `backtest_batch` (174 rows) by every publish, and each model that serves has a model card ([segment share](docs/model_card_segment_share.md), [national share](docs/model_card_national_share.md), [decline alert](docs/model_card_decline_alert.md); notebook 15).

## What is built

| Stage | What exists | Where |
|---|---|---|
| **Data profiling** | Raw IQVIA structure, the cleaned warehouse, RA, and a profile of every public file (structure only) | notebooks 01, 02, 03, 10 |
| **Cleaning and preprocessing** | Pandera validation gate, Silver and Gold builders, a product taxonomy, 11 dataset-level quality checks with a stored baseline | `src/oa_market_intelligence/ingestion/`, `warehouse/`, `quality.py` |
| **Ingestion** | The four IQVIA extracts parsed and audited (SHA-256, rows parsed, month span), openFDA approval dates, and 171 public files (CMS, CDC, SEC, FDA, price) with a committed download manifest | `ingestion/`, `external/` |
| **Data warehouse** | IQVIA star schema (4 dimensions, 2 facts, Gold tables) plus a separate public-data database (27 tables, every load reconciled to the raw files) and a committed 6 MB publishable subset | `data/published/warehouse.db`, `data/published/external_subset.db` |
| **Availability layer** | A recorded rule for when each outside value could have been known, and a point-in-time ("as-of") table built from it | `availability.py`, `mart.py` |
| **EDA** | The share series, seasonality and persistence, the sparse segment panel, the outside series on their availability dates, a specialty triangulation against Medicare, and a diagnosis of the sales-versus-visits gap | notebooks 11, 12, 13 |
| **Feature engineering** | Seven candidate groups with leakage and as-of tests, a feature dictionary, a pre-registered test (one group passes at about 0.06%; none helps the national forecast) and two feature tables in the warehouse | `features/`, notebook 14, DL-75 to DL-77 |
| **Modelling** | A horizon harness with leakage guards, three tasks at 1, 3 and 6 months (national share, segment share, decline alert), a pre-registered run repeated for identical output, a post-hoc check, three model cards and two result tables in the warehouse | `modeling/`, notebook 15, DL-78, DL-79 |
| **Safety nets** | A golden regression test of the warehouse, an atomic publish with a run log, restatement checks | `golden.py`, `publish.py` |

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

### Data-quality findings that shaped the cleaning
- A likely mis-coded `PEDIATRICS` prescriber and miscoded oncology products in the RA extract (RA has about 1,250 visits over 6 years, too sparse to model).
- The Mar to Jul 2024 volume dip across all three product classes (finding 9).
- 11 dataset-level quality checks run before every build against a stored baseline; an error stops the run, and a failed run never replaces the last good database.

### Engineering outcomes
- A re-runnable pipeline from raw Excel to a validated warehouse in about 2.5 minutes; every extract is hashed (SHA-256) per run, and a golden regression test rebuilds the warehouse from the real extracts and compares it with committed values.
- 171 public files (about 24 GB) from CMS, CDC, SEC and FDA were profiled and loaded into a 27-table database with 38 reconciliation checks passing, restorable from a committed manifest.
- A recorded "when could this value have been known" rule for 14 sources, so later features cannot use information from the future.
- About 830 automated tests, run in parallel in CI on every pull request.
- Modelling that cannot see the future: a horizon harness that raises if a training row's outcome is after the forecast date, model-level "rewrite the future" tests with a deliberately leaky case that must be caught, and a full run of every model repeated once for byte-identical output.
- Model results stored in the warehouse (`model_scores`, `backtest_batch`) by every publish, a model card for each model that serves, and the adoption of models into serving recorded as a decision (DL-79).

## Architecture in one picture

| Layer | What lives there |
|---|---|
| **Bronze** | The raw IQVIA Excel extracts in `data/raw/`, untouched, with a per-run record of their hashes |
| **Silver** | Star schema: 4 dimension tables + 2 fact tables (Place-of-Service is a separate grain, so it gets its own fact table) |
| **Gold** | `gold_visit_share_monthly` (one row per month) and `gold_segment_adoption` (month x specialty x demographic) |
| **Mart** | `mart_signal` and `mart_signal_asof`: outside series with the date each value could have been known |

The pipeline writes `data/processed/warehouse.db` (git-ignored). The publish step builds `data/published/warehouse.db` (committed) in a staging file, checks it, and swaps it in atomically, so a failed run never damages the last good database.

Set up (Python 3.12 is the tested baseline; the packages are pinned, see [`docs/environment.md`](docs/environment.md)):

```bash
pip install -r requirements.txt && pip install --no-deps -e .   # then oa-pipeline, oa-publish, oa-model are on the path
docker build -t oa-market-intelligence .                          # the same environment as a container
```

Run:

```bash
PYTHONPATH=src python -m oa_market_intelligence.pipeline   # build the warehouse (about 2.5 minutes)
PYTHONPATH=src python -m oa_market_intelligence.publish    # build, check and publish it atomically
PYTHONPATH=src python -m pytest                            # the test suite (CI runs it in parallel)
PYTHONPATH=src python -m oa_market_intelligence.modeling.run_modelling --counts-only   # what the modelling run will score (fits nothing)
PYTHONPATH=src python -m oa_market_intelligence.modeling.run_modelling --run --n-jobs 6   # the full modelling run (about two hours)
```

## Data

The primary dataset is a real IQVIA NMTA patient-visit extract, provided for this capstone. The Excel extracts are committed directly in [`data/raw/`](data/raw/) with the course instructor's approval under Penn State's data license. A maintained product taxonomy (160 products: 145 OA + 15 RA) is in [`data/reference/`](data/reference/).

FDA approval dates come from the free public openFDA Drugs@FDA API; a snapshot is committed so the work is reproducible without the internet.

### Public datasets added to the analysis

171 public files (about 24 GB) were downloaded, profiled and loaded into a separate 27-table database (every load reconciled to the raw files; restorable from the committed manifest, see [`docs/external_data_refresh.md`](docs/external_data_refresh.md); the full 134 MB database is local only). Each dataset was added for a stated reason, in a protocol written before any analysis ran ([`docs/external_data_protocol.md`](docs/external_data_protocol.md)).

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

## Documentation

| Document | Covers |
|---|---|
| [`docs/decision_log.md`](docs/decision_log.md) | Every significant decision, why it was made, what was rejected (entries up to DL-79; some describe work kept in the archive tag) |
| [`docs/PROPOSAL.md`](docs/PROPOSAL.md) | The original project proposal: objectives, architecture, definitions and build decisions |
| [`docs/data_dictionary.md`](docs/data_dictionary.md), [`database_schema.md`](docs/database_schema.md), [`silver_gold_data_dictionary.md`](docs/silver_gold_data_dictionary.md), [`data_analysis_reference.md`](docs/data_analysis_reference.md) | Raw field structure, the star schema and DDL, every Silver and Gold column, and the dataset audit |
| [`docs/external_data_protocol.md`](docs/external_data_protocol.md) | The protocol for the public datasets: code sets, specialty crosswalk, pass or fail rules, fixed before any analysis |
| [`docs/external_data_lineage.md`](docs/external_data_lineage.md) | Every column of the external-data database with its source file, original column and rule |
| [`docs/external_data_refresh.md`](docs/external_data_refresh.md) | How to restore the raw public files, rebuild and verify the external database |
| [`docs/external_data_results.md`](docs/external_data_results.md) | Results of the public-data analyses: each pre-registered rule, its verdict, the numbers, the limits |
| [`docs/feature_engineering_plan.md`](docs/feature_engineering_plan.md) | The feature plan: targets, the base set and seven groups, the time rule, tests and the validation protocol, fixed before anything was built or run |
| [`docs/feature_dictionary.md`](docs/feature_dictionary.md) | Every model input: definition, source and leakage rule |
| [`docs/feature_results.md`](docs/feature_results.md) | Results of the feature-group test: each rule, its verdict, the numbers and the limits |
| [`docs/database_schema.md`](docs/database_schema.md) sections 13 and 14 | The two feature tables and the two model-result tables the publish step writes into the warehouse |
| [`docs/environment.md`](docs/environment.md) | The supported Python, the pinned packages, installing the project and the Docker image, and the environment behind the stored modelling results |
| [`docs/modelling_plan.md`](docs/modelling_plan.md) | The modelling plan: three tasks, horizons, baselines and models, the evaluation and promotion rules, the leakage timeline and tests, fixed before any model ran (with the T3 amendment and the implementation choices) |
| [`docs/modelling_results.md`](docs/modelling_results.md) | Results of the modelling run, including the post-hoc check of the decline alert and the limits |
| [`docs/model_card_segment_share.md`](docs/model_card_segment_share.md), [`model_card_national_share.md`](docs/model_card_national_share.md), [`model_card_decline_alert.md`](docs/model_card_decline_alert.md) | One card for each model that serves: use, inputs, evaluation, calibration, limits |

**Notebooks** (executed end to end; each opens with the question, the answer in one line, and the method in plain words):

| Notebook | Covers |
|---|---|
| [`01_eda_raw_data`](notebooks/01_eda_raw_data.ipynb), [`02_eda_cleaned_data`](notebooks/02_eda_cleaned_data.ipynb), [`03_eda_ra_data`](notebooks/03_eda_ra_data.ipynb) | Raw structure, the cleaned warehouse, RA |
| [`10_external_raw_data_profile`](notebooks/10_external_raw_data_profile.ipynb) | Profile of the raw public files (structure only) |
| [`11_external_analysis_results`](notebooks/11_external_analysis_results.ipynb), [`12_gap_diagnosis`](notebooks/12_gap_diagnosis.ipynb) | The pre-registered public-data analyses, then the sales-versus-visits gap diagnosis |
| [`13_eda_unified_data`](notebooks/13_eda_unified_data.ipynb) | Descriptive EDA of the unified data before feature engineering. Runs from committed files only |
| [`14_feature_groups_test`](notebooks/14_feature_groups_test.ipynb) | The pre-registered test of seven feature groups (DL-75): the stored result read back, effect sizes and the COVID and 2024-dip robustness runs. One group passes (about 0.06% of log-loss); the national forecast does not improve (DL-76). |
| [`15_modelling_results`](notebooks/15_modelling_results.ipynb) | The modelling run read back: national share, segment share and its upward bias, the decline alert, the post-hoc check, the G5 hypothesis and the batch demonstration (DL-78, DL-79). |

Notebooks 10 to 12 need the local external database and run only on the author's machine; notebook 13 runs from committed files.

## Status

**Phase 1 and 2 (data foundation, design and the pipeline code):** complete. **Profiling, cleaning, ingestion, warehouse and EDA:** complete, including the data layer (availability rules, ingest audit, quality stage, as-of mart, golden regression, public subset). **Feature engineering (DL-75, DL-76):** complete: a written plan with rules fixed first, seven groups built with leakage and as-of tests, one pre-registered run repeated for identical output. Result: specialty and market context improves segment share by about 0.06% of log-loss and is robust to COVID and the 2024 dip; competitor volumes, calendar, history, outside signals and flags add nothing in the main run, and no group improves the national forecast. **Modelling (DL-78, DL-79):** complete: a written plan with rules fixed first (and a decline-alert amendment), a horizon harness with leakage guards, one pre-registered run repeated for identical output, a post-hoc check of the alert, three model cards and two result tables in the warehouse. Result: the national baseline holds; the logistic regression gives a small, upward-biased gain for segment share at 1 and 3 months; the decline alert's gain is mostly regression to the mean. The adoption is decided in DL-79: the national baseline, the segment logistic regression at 1 and 3 months (baseline at 6), and the decline alert as a 1-month flag only. **Next:** the final phase (serving layer and production hardening).

The earlier full build (leak-safe features, evaluation harness, models, a Streamlit site with a Claude question box, and a feature and modelling improvement round, DL-71 to DL-74) is kept in the tag `archive/full-project-2026-10-11` and is not part of this branch of the project.

## Tech stack

**In use (`requirements.txt`):** `pandas`, `numpy`, `openpyxl`, `requests`, `pandera`, `sqlalchemy`, `scipy`, `scikit-learn`, `statsmodels`, `matplotlib`, `seaborn`, `jupyter` and its kernel tools, `ruff`, `pytest`. **Infrastructure:** SQLite, GitHub Actions (lint and tests on every pull request; a monthly publish workflow).

## Authors

Tushar, Saakshaat Saini
