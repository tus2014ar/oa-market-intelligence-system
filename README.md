# OA & RA Market Intelligence System

A monthly-refreshable, production-oriented analytics system that tracks Zilretta's visit share against generic corticosteroid injections and NSAIDs in Osteoarthritis (OA) and Rheumatoid Arthritis (RA), and tests, with pre-set rules and honest verdicts, what can and cannot be predicted from real IQVIA National Medical and Treatment Audit (NMTA) patient-visit data. Built around one stakeholder, an OA/RA Injectable Brand Manager.

**Status (8 October 2026):** the data pipeline, exploratory analysis, feature engineering, evaluation harness, and the full modeling and evaluation phase (Phase 4) are complete and documented. The public website and Claude question box are built and tested but **not yet deployed**, and the Phase 4 results are not yet wired into them. The next planned improvement is extra data (payer, geography, price, prescription volume). See [Results at a glance](#results-at-a-glance) and [Status](#status).

Built as a capstone project for **DAAN 888 — Design and Implementation of Analytics System**, Penn State University, School of Graduate Professional Studies (Fall 2026).

**Team:** Tushar and Saakshaat Saini

## The problem

A newer, extended-release branded injectable (Zilretta) is trying to take visit share from decades-old generic corticosteroids and NSAIDs in a high-volume OA market (7.19M+ visits across 6 years in our extract). The system is built around a single stakeholder, an **OA/RA Injectable Brand Manager**, who needs an early signal on competitive share movement before it shows up in a standard quarterly business review.

## Results at a glance

The project answers four business questions (Assignment 3, §2). Every method and pass/fail rule was written into [`docs/phase4_modeling_plan.md`](docs/phase4_modeling_plan.md), and committed, before the analysis that tested it. Overview: [`docs/phase4_results.md`](docs/phase4_results.md).

| # | Question | Answer in one line | Strength |
|---|---|---|---|
| **Q1** | How has share shifted; is there an inflection point; can an FDA event explain it? | Share rose until early 2022 and has fallen since; the turn is statistically clear; no FDA brand entry explains it; the fall is within specialties, not a change in the specialty mix | High |
| **Q2** | Can a classifier predict next month's direction (Up / Flat / Down)? | No: logistic regression, random forest and gradient boosting do not beat persistence or chance; 35 test months could only have detected a gain of about 30 percentage points | Negative result, with a stated limit |
| **Q3** | Does adoption vary by specialty; can segments be classified? | Strongly by specialty (eight clearly above or below the market share; stable across time; robust); a simple model estimates a segment's next-month share better than "same as last month", mostly for small segments | High for the pattern; modest for the estimator |
| **Q4** | How often do predictions match; when should a review flag be raised? | The served classifier matches about 66%; flag at a rolling six-month match rate of 33% or below; a forecast-interval alarm has almost no false alarms but catches only large volatility jumps | Moderate, with stated blind spots |

Plain-language write-ups for the brand manager: [`docs/stakeholder_summary_part1.md`](docs/stakeholder_summary_part1.md) (Q1 and Q3) and [`docs/stakeholder_summary_part2.md`](docs/stakeholder_summary_part2.md) (prediction and monitoring).

## What this system does, and where each piece stands

Each item is tagged **[Built]** (implemented and tested in this repo), **[Built, not yet deployed]**, or **[Planned]** (designed in [`docs/PROPOSAL.md`](docs/PROPOSAL.md), not yet implemented).

- **[Built]** Ingests the four IQVIA NMTA Excel extracts, validates them with a Pandera gate, and builds a Bronze → Silver → Gold SQLite warehouse with a single CLI command; computes the monthly target and its Up / Down / Flat direction label (volatility-scaled threshold) plus lag and rolling features
- **[Built]** Runs lint and tests on every push and pull request (GitHub Actions, `ruff` + `pytest`; 455 tests including real-data tests)
- **[Built]** Exploratory analysis in three executed notebooks (raw data, the cleaned warehouse, RA), whose findings shaped the cleaning rules and the label definition
- **[Built]** Leak-safe feature sets (monthly and segment), each guarded by a test that rewrites every later month and requires the earlier features not to move, and that fails when a leak is injected on purpose
- **[Built]** A walk-forward evaluation harness (24-month minimum window, one month ahead, McNemar's test, block-bootstrap intervals, a random-guessing band) with simple baselines, for both the monthly direction task and a segment-level share task
- **[Built]** **Phase 4, inference** (Q1, Q3): a bootstrap-calibrated change-point test, a mix-versus-adoption decomposition of the share change, a binomial specialty model with adjusted shares, intervals and stability, and re-runs without the known data problems (notebooks 06 and 07)
- **[Built]** **Phase 4, prediction** (Q2): a segment-level share model (logistic regression, gradient boosting, random forest) that beats "same as last month" modestly, and a monthly share forecast (ETS, ridge) that does **not**, plus random forest and gradient boosting on direction, which do not beat persistence; a power statement says what 35 test months could detect (notebooks 08 and 09; model cards below)
- **[Built]** **Phase 4, monitoring** (Q4): a data-based review threshold for the direction classifier and an interval-miss alarm for the forecast, tested on simulated degradation, plus a tested function that records backtest predictions in the Gold placeholder columns
- **[Built, not yet deployed]** A safe publish step (`python -m oa_market_intelligence.publish`): builds the warehouse in a staging file, checks it, runs a model evaluation, and swaps it in atomically; a failed run keeps the last good database and writes a run log. The monthly workflow runs it and commits `data/published/warehouse.db`. **Not yet in it:** the Phase 4 models and the monitoring write (Step 14)
- **[Built, not yet deployed]** A Streamlit analytics site (`app/streamlit_app.py`) with the market trend, a specialty/age/gender comparison, a model panel (a trained model serves only if it beats chance and every baseline), and a Claude question box over four read-only aggregate tools, with an access code, rate limits and a daily token budget (tested with a scripted client; no live API call yet). Deployment: [`docs/deployment.md`](docs/deployment.md)
- **[Planned]** Extra data to explain what history cannot: payer or formulary changes, geography, price, prescription volume (public CMS sources exist; see [Status](#status))
- **[Planned]** A knowledge graph, RAG over the methodology documents and drift tooling (stretch)
- **[Planned]** The rest of the MLOps stack (DVC remote, MLflow, Optuna, SHAP, Evidently AI): none is installed; a JSON run log, permutation importance and small fixed tuning grids were used instead. Deliberately *without* Kubernetes, Kafka, Databricks, or live A/B testing, since this is a monthly batch system, not a real-time service

RA is an exploratory, comparison-only track: with ~1,250 visits over 6 years in the warehouse (1,283 on the reference file's different counting basis) and some miscoded oncology products, it is too sparse for modeling or monitoring.

## Architecture in one picture

Data moves through three layers, all stored in one SQLite file accessed via SQLAlchemy Core:

| Layer | What lives there |
|---|---|
| **Bronze** | The raw IQVIA Excel extracts in `data/raw/`, untouched |
| **Silver** | Star schema: 4 dimension tables + 2 fact tables (Place-of-Service is a separate grain, so it gets its own fact table) |
| **Gold** | 2 serving tables: `gold_visit_share_monthly` (one row per month) and `gold_segment_adoption` (month × specialty × demographic) |

The pipeline writes `data/processed/warehouse.db` (git-ignored). The publish step builds `data/published/warehouse.db` (committed), which the website and the notebooks read; it also stores the model panel inside that file so one file is one consistent version of the site. Claude and the website read only aggregate Gold tables, never raw rows. Engineered features (lags, rolling averages, FDA-derived context) are columns in the Gold tables; the model features are computed on demand from Gold by `src/oa_market_intelligence/features/`.

This is a real, running pipeline: `src/oa_market_intelligence/pipeline.py` orchestrates ingest → validate → Silver build → Gold build and produces a verified warehouse with 72 months, 160 products, 149,141 product-visit rows, and 135,119 total branded-injectable visits, matching every figure documented in `PROPOSAL.md` §18.1. Run it yourself with:

```bash
PYTHONPATH=src python -m oa_market_intelligence.pipeline
```

## Data

The primary dataset is a real IQVIA NMTA patient-visit extract, provided for this capstone. The four Excel extracts are committed directly in [`data/raw/`](data/raw/) with the course instructor's approval under Penn State's data license. That approval also covers sending aggregate Gold-table data (never raw rows) to the Claude API for the Q&A layer. A maintained product taxonomy mapping (160 products: 145 OA + 15 RA) lives in [`data/reference/product_taxonomy.csv`](data/reference/product_taxonomy.csv); a product the pipeline hasn't seen before is flagged for human review rather than guessed at.

FDA approval dates come from the free public openFDA Drugs@FDA API. A snapshot of the approval dates of every branded product in the share formula is committed in [`data/reference/`](data/reference/) so the analysis is reproducible without the internet.

## Documentation

**Start here:** [`docs/phase4_results.md`](docs/phase4_results.md) (one-page results overview) and [`docs/decision_log.md`](docs/decision_log.md) (every significant decision, why it was made, what was rejected).

| Document | Covers |
|---|---|
| [`docs/phase4_results.md`](docs/phase4_results.md) | The four business questions, the answers, the evidence, what changed during the work, how to reproduce |
| [`docs/phase4_modeling_plan.md`](docs/phase4_modeling_plan.md) | The Phase 4 plan: methods, models, test protocol and pass/fail rules, fixed before the analyses ran |
| [`docs/stakeholder_summary_part1.md`](docs/stakeholder_summary_part1.md), [`part2`](docs/stakeholder_summary_part2.md) | Plain-language summaries for the brand manager |
| [`docs/model_card_segment_share.md`](docs/model_card_segment_share.md) | Segment share model (served: logistic regression): data, results, robustness, limits |
| [`docs/model_card_share_forecast.md`](docs/model_card_share_forecast.md) | Monthly share forecast (served: "same as last month"), intervals and the monitoring alarm |
| [`docs/model_card_logistic_regression.md`](docs/model_card_logistic_regression.md) | Direction classifier: logistic regression, random forest and gradient boosting, overfitting and the power statement |
| [`docs/evaluation_protocol.md`](docs/evaluation_protocol.md) | How models are tested (walk-forward, metrics, McNemar, bootstrap), the baselines, and the Phase 4 protocols |
| [`docs/feature_dictionary.md`](docs/feature_dictionary.md) | Every model feature: definition, the EDA finding behind it, and the leakage rule |
| [`docs/PROPOSAL.md`](docs/PROPOSAL.md) | Full project proposal: objectives, architecture, production operations, precise definitions and build decisions, website and AI layer |
| [`docs/final_deliverable_plan.md`](docs/final_deliverable_plan.md) | The plan for the deployed data and MLOps pipeline and public site, with current status |
| [`docs/deployment.md`](docs/deployment.md) | How the public site is deployed (hosting, secrets, domain) |
| [`docs/data_dictionary.md`](docs/data_dictionary.md), [`database_schema.md`](docs/database_schema.md), [`silver_gold_data_dictionary.md`](docs/silver_gold_data_dictionary.md), [`data_analysis_reference.md`](docs/data_analysis_reference.md) | Raw field structure, the star schema and DDL, every Silver and Gold column, and the dataset audit |

**Notebooks** (executed end to end against the committed warehouse; each opens with the question, the answer in one line, and the method in plain words):

| Notebook | Covers |
|---|---|
| [`01_eda_raw_data`](notebooks/01_eda_raw_data.ipynb), [`02_eda_cleaned_data`](notebooks/02_eda_cleaned_data.ipynb), [`03_eda_ra_data`](notebooks/03_eda_ra_data.ipynb) | Exploratory analysis: raw structure, the cleaned warehouse, RA |
| [`04_evaluation_and_baselines`](notebooks/04_evaluation_and_baselines.ipynb), [`05_logistic_regression`](notebooks/05_logistic_regression.ipynb) | The evaluation harness, baselines, and the first trained model |
| [`06_q1_trend_and_decomposition`](notebooks/06_q1_trend_and_decomposition.ipynb) | Q1: change points, the FDA lookup, mix versus adoption |
| [`07_q3_specialty_adoption_and_robustness`](notebooks/07_q3_specialty_adoption_and_robustness.ipynb) | Q3: adjusted shares by specialty, stability, and the robustness re-runs |
| [`08_segment_share_prediction`](notebooks/08_segment_share_prediction.ipynb) | Segment share prediction: label audit, baselines, models, judging |
| [`09_forecast_monitoring_and_direction`](notebooks/09_forecast_monitoring_and_direction.ipynb) | Share forecast, Q4 monitoring, Q2 closure and the power statement |

## Status

**Phase 1 (data foundation and design)** and **Phase 2 (the pipeline code)** are complete: data dictionary, star schema, Gold schema; ingestion with parsers and an openFDA client; a Pandera validation gate; Silver and Gold builders; one-command pipeline orchestration with a monthly GitHub Actions workflow.

**Phase 3 (exploratory analysis, features and the first model)** is complete: three EDA notebooks (including two real data findings, a likely mis-coded `PEDIATRICS` prescriber and a Mar to Jul 2024 volume dip, and the replacement of a degenerate fixed Flat threshold by a volatility-scaled one); leak-safe monthly and segment features; the walk-forward harness and baselines; and a first logistic regression that does not beat the baselines or chance and overfits.

**Phase 4 (modeling and evaluation, 7 to 8 October 2026)** is complete: tests first, every protocol and pass/fail rule committed before its real run, decisions DL-38 to DL-57. Highlights, including what the work found about itself:

- The planned change-point selection (BIC) was replaced before it touched real data, because testing showed it reports breaks that are not there; the calibrated test used instead is itself somewhat liberal under dependent noise (about 10% false positives against a 5% target in a demonstration), which is stated.
- The three-label segment scheme failed its pre-set gate, so the pre-specified fallback applied and the primary test became the share itself; a looser rescue was not adopted.
- All pre-set robustness rules held without the known data problems; the segment model's modest gain is real but it runs about 7% high in the falling market; the share forecast and the direction classifiers are negative results; the monitoring alarm is conservative and insensitive to slow drift.

**What remains:**

1. **Deployment** (needs the owner's accounts and secrets): an Anthropic API key with a spend limit, the Streamlit Community Cloud app, and a domain redirect ([`docs/deployment.md`](docs/deployment.md)).
2. **Integration (Step 14):** compute the Phase 4 results and monitoring status inside the publish step, write the backtest predictions to Gold, and show them on the site and in the Claude tool.
3. **Extra data (next improvement, DL-57):** the instructor may still have IQVIA prescription-volume, administered-versus-prescribed or regional data to provide; public CMS sources exist (billing data by specialty and state, quarterly average-sales-price files); a lead about Medicare outpatient pass-through payment status ending in March 2021 is unverified. Each addition gets a protocol fixed in the plan first.

Development is local-first: cloud infrastructure (the hosted website) is stood up when the owner is ready. See [`docs/PROPOSAL.md`](docs/PROPOSAL.md) §8 for the course roadmap.

## Tech stack

**In use (`requirements.txt`):** `pandas`, `numpy`, `openpyxl`, `requests`, `pandera`, `sqlalchemy`, `scipy`, `scikit-learn`, `statsmodels`, `matplotlib`, `seaborn`, `jupyter` and its kernel tools, `ruff`, `pytest`. **For the site (`app/requirements.txt`):** `streamlit`, `anthropic` (plus `pandas`, `numpy`, `scipy`, `scikit-learn`, `sqlalchemy`). **Infrastructure:** SQLite, GitHub Actions.

**Designed but not installed:** `MLflow`, `Optuna`, `SHAP`, `Evidently AI`, `DVC`, `Docker`, `FastAPI`, `MCP`, `Chroma`/`FAISS` (RAG), `RDFLib`/`NetworkX` (knowledge graph, stretch).

## Reproducing the results

```bash
PYTHONPATH=src python -m oa_market_intelligence.publish     # builds data/published/warehouse.db (about 2 minutes)
PYTHONPATH=src python -m pytest                             # 455 tests (about 11 minutes)
streamlit run app/streamlit_app.py                          # the site, locally
```

The notebooks read `data/published/warehouse.db` and run in a Jupyter kernel that has the project's requirements installed; the live runs take about 2, 15, 10 and 10 minutes for notebooks 06 to 09. Every random choice uses a fixed seed, and two full runs of the prediction tasks gave identical results.

## Authors

Tushar, Saakshaat Saini
