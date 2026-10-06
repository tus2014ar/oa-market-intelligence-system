# OA & RA Market Intelligence System

A monthly-refreshable, production-oriented classification system that tracks and predicts visit-share direction between branded specialty injectables and generic pain therapies in Osteoarthritis (OA) and Rheumatoid Arthritis (RA), built on real IQVIA National Medical and Treatment Audit (NMTA) patient-visit data.

**Status:** data pipeline, exploratory analysis, feature engineering and the evaluation harness with baselines complete; the classifier models are next (see [Status](#status)).

Built as a capstone project for **DAAN 888 — Design and Implementation of Analytics System**, Penn State University, School of Graduate Professional Studies (Fall 2026).

**Team:** Tushar and Saakshaat Saini

## The problem

A newer, extended-release branded injectable (Zilretta) is trying to take visit share from decades-old generic corticosteroids and NSAIDs in a high-volume OA market (7.19M+ visits across 6 years in our extract). The system is built around a single stakeholder — an **OA/RA Injectable Brand Manager** — who needs an early signal on competitive share movement before it shows up in a standard quarterly business review.

## What this system does, and where each piece stands

Each item is tagged **[Built]** (implemented and tested in this repo), **[In progress]**, or **[Planned]** (designed in [`docs/PROPOSAL.md`](docs/PROPOSAL.md), not yet implemented).

- **[Built]** Ingests the four IQVIA NMTA Excel extracts, validates them with a Pandera gate, and builds a Bronze → Silver → Gold SQLite warehouse with a single CLI command
- **[Built]** Computes the monthly target in the Gold table — Zilretta's visit share and its Up / Down / Flat direction label (volatility-scaled threshold) — plus lag and rolling-average features
- **[Built]** Runs lint and tests on every push and pull request (GitHub Actions, `ruff` + `pytest`), and a scheduled, manually triggerable monthly pipeline workflow that uploads the rebuilt warehouse as a build artifact
- **[Built]** Three executed EDA notebooks (raw data, the cleaned warehouse, and RA) whose findings shaped the cleaning rules and the label definition
- **[Built]** Leak-safe, model-ready feature matrices computed from the Gold tables (a monthly matrix for the classifier and a segment matrix with a specialty-level target encoding), each guarded by a test that rewrites every later month and requires the features not to move
- **[Built]** A walk-forward evaluation harness (24-month minimum window, one month ahead, McNemar's test) and two baselines, always-majority and persistence, on the real data; its leakage test fails if the harness trains on the month it predicts
- **[Planned]** Classifies next month's visit-share direction (Up / Down / Flat) for the branded injectable in OA, evaluated against a persistence baseline and a classical time-series (SARIMA/ETS) validation check. This is the next step: the target label, the feature matrices, the evaluation harness and the persistence baseline are built, but no model has been trained yet
- **[Planned]** Segments provider specialties/demographics by adoption level (High vs. Low) — stretch objective. The segment-level Gold table it will read is built; the adoption labels are not
- **[Planned]** Monitors its own predictions against actual results monthly, and flags meaningful misses (the Gold table has empty placeholder columns for predictions and actuals)
- **[Planned]** Serves a public, multi-user website (open signup, access-code gated) with a dashboard and a Q&A / on-demand visualization layer: Claude connected through the Model Context Protocol (MCP) to scoped, read-only tools over the Gold tables, plus a RAG tool for methodology and regulatory questions — never raw SQL
- **[Planned]** Adds a knowledge graph of products, manufacturers and FDA events for competitive context — stretch objective
- **[Planned]** Designed to run on a right-sized MLOps stack: DVC for data and model-registry persistence (`.dvc/` is initialized but no remote is configured), MLflow + Optuna for experimentation, SHAP for explainability, and Evidently AI for drift monitoring — none of which is installed yet. GitHub Actions for CI and the monthly run is already in place (see above). Deliberately *without* Kubernetes, Kafka, Databricks, or live A/B testing, since this is a monthly batch system, not a real-time service

RA is an exploratory, comparison-only track: with ~1,250 visits over 6 years in the warehouse (1,283 on the reference file's different counting basis) and some miscoded oncology products, it is too sparse for a classifier.

## Architecture in one picture

Data moves through three layers, all stored in one SQLite file (`data/processed/warehouse.db`, accessed via SQLAlchemy Core):

| Layer | What lives there |
|---|---|
| **Bronze** | The raw IQVIA Excel extracts in `data/raw/`, untouched |
| **Silver** | Star schema: 4 dimension tables + 2 fact tables (Place-of-Service is a separate grain, so it gets its own fact table) |
| **Gold** | 2 serving tables: `gold_visit_share_monthly` (core classifier, one row per month) and `gold_segment_adoption` (stretch objective, month × specialty × demographic) |

Engineered features (lags, rolling averages, FDA-derived competitive-context features) are stored as columns in the Gold tables, alongside model predictions. The remaining model features (momentum, calendar, event and segment features) are computed on demand from Gold by `src/oa_market_intelligence/features/` and are not stored. Claude and the website read only from Gold. Each monthly run upserts the dimension tables (to keep surrogate keys stable) and full-refresh-overwrites the fact and Gold tables.

This is now a real, running pipeline, not just a design: `src/oa_market_intelligence/pipeline.py` orchestrates ingest → validate → Silver build → Gold build end to end, and produces a verified `warehouse.db` with 72 months, 160 products, 149,141 product-visit rows, and 135,119 total branded-injectable visits — matching every figure documented in `PROPOSAL.md` §18.1. Run it yourself with:

```bash
PYTHONPATH=src python -m oa_market_intelligence.pipeline
```

A scheduled GitHub Actions workflow ([`.github/workflows/monthly_pipeline.yml`](.github/workflows/monthly_pipeline.yml)) runs the same pipeline monthly (and can be triggered manually), uploading the resulting database as a build artifact.

## Data

The primary dataset is a real IQVIA NMTA patient-visit extract, provided for this capstone. The four Excel extracts are committed directly in [`data/raw/`](data/raw/) with the course instructor's approval under Penn State's data license. That approval also covers sending aggregate Gold-table data (never raw rows) to the Claude API for the planned Q&A layer. A maintained product taxonomy mapping (160 products: 145 OA + 15 RA) lives in [`data/reference/product_taxonomy.csv`](data/reference/product_taxonomy.csv) — a product the pipeline hasn't seen before is flagged for human review rather than guessed at.

FDA approval dates come from the free public openFDA Drugs@FDA dataset.

## Documentation

| Document | Covers |
|---|---|
| [`docs/PROPOSAL.md`](docs/PROPOSAL.md) | Full project proposal: objectives, technical architecture (§9), production operations (§17), precise definitions and build decisions (§18), website and AI serving layer (§19) |
| [`docs/data_dictionary.md`](docs/data_dictionary.md) | Field-level structure of every raw source file, verified by direct inspection |
| [`docs/data_analysis_reference.md`](docs/data_analysis_reference.md) | Dataset audit, full product taxonomy, feature priorities, and the strategy for combining NMTA with openFDA |
| [`docs/database_schema.md`](docs/database_schema.md) | SQLite DDL for the Silver and Gold tables, ER diagram, and rebuild rules |
| [`docs/silver_gold_data_dictionary.md`](docs/silver_gold_data_dictionary.md) | What every Silver and Gold column means and where its value comes from |
| [`docs/feature_dictionary.md`](docs/feature_dictionary.md) | Every model feature: its definition, the EDA finding behind it, and the leakage rule that governs it |
| [`docs/evaluation_protocol.md`](docs/evaluation_protocol.md) | How models are tested (walk-forward, metrics, McNemar), the baselines, and their results on the real data |

## Status

**Phase 1 (data foundation and design) is complete**: data dictionary, dataset overview, database choice, star schema, Gold schema, and a reconciled technical architecture are all documented and merged. Real data and the product taxonomy are committed.

**Phase 2 (the pipeline code) is complete**, built and merged step by step with real-data verification at every stage:

- **Ingestion**: parsers for the NMTA pivot extract, the Place-of-Service sheet, and the Branded/Generic reference tables, plus an openFDA client for approval-date lookups
- **Validation**: a Pandera gate that rejects bad data before it can reach the warehouse (value ranges, categorical domains, expected columns)
- **Silver builder**: upserts the 4 dimension tables (stable surrogate keys) and full-refresh-overwrites the 2 fact tables
- **Gold builder**: computes `visit_share`, the Up/Down/Flat direction label, lag/rolling features, and the FDA-derived competitive-context features
- **Pipeline orchestration**: `pipeline.py` ties every stage together behind one CLI command, with a scheduled + manually-triggerable GitHub Actions workflow
- 205 tests (real-data integration tests included, not just mocks), `ruff`-clean, CI green on every PR

**Phase 3's EDA sub-phase is complete** — three notebooks, each executed end to end against the real warehouse with zero errors:

- [`notebooks/01_eda_raw_data.ipynb`](notebooks/01_eda_raw_data.ipynb) — EDA on the raw Excel structure and the reshaped-but-unvalidated data, confirming the Phase 2 pipeline's decisions were correct
- [`notebooks/02_eda_cleaned_data.ipynb`](notebooks/02_eda_cleaned_data.ipynb) — a full audit of the Gold tables, surfacing two real findings not documented anywhere else (a likely mis-coded `PEDIATRICS` prescriber, and a Mar–Jul 2024 volume dip plausibly tied to the Change Healthcare outage — both now in `PROPOSAL.md` §10) — and **closing the long-open Flat-threshold decision** (§18.2): the originally-proposed ±1.0pp fixed threshold was confirmed degenerate (0 Up, 0 Down on all 72 real months); it's replaced with a leakage-safe threshold based on each month's change measured in standard deviations of the trailing 12 months' change, now `build_gold.py`'s default (7 Up / 43 Flat / 9 Down on the real data)
- [`notebooks/03_eda_ra_data.ipynb`](notebooks/03_eda_ra_data.ipynb) — the first dedicated look at RA, which had ridden along in the warehouse since Phase 2 without ever being analyzed on its own. Confirms RA still can't support a classifier (OA's smallest category alone sees ~110x RA's entire monthly volume), but finds RA volume is **growing, not flat** (~5.4 → ~29.4 visits/month, 2019–2020 vs. 2024–2025) and delivers the originator-vs-biosimilar decomposition `PROPOSAL.md` §6.2 had only ever planned: the infliximab family's originator share fell from 100% to 11% over the window — a real, demonstrable biosimilar switch, not a hypothetical

**Feature engineering is built** — [`src/oa_market_intelligence/features/`](src/oa_market_intelligence/features/) computes leak-safe model-ready matrices from the Gold tables, documented in [`docs/feature_dictionary.md`](docs/feature_dictionary.md): 59 monthly rows (7 Up / 43 Flat / 9 Down) with 22 features, and 8,563 modelling segments with a specialty-level target encoding. They reproduce the notebook prototype exactly on the real warehouse, and a generic test that rewrites every later month guards against look-ahead leakage.

**Evaluation harness and baselines are built** — [`src/oa_market_intelligence/modeling/`](src/oa_market_intelligence/modeling/) runs an expanding walk-forward backtest (24-month minimum window, one month ahead) and scores models on recall for the Down class, balanced accuracy and macro-F1; results are in [`notebooks/04_evaluation_and_baselines.ipynb`](notebooks/04_evaluation_and_baselines.ipynb) and [`docs/evaluation_protocol.md`](docs/evaluation_protocol.md). On the 35 test months (25 Flat, 6 Down, 4 Up), always-Flat reaches 71% accuracy but never catches a Down month, and persistence catches 1 of 6, about the base rate, so it has little skill for direction. With only 6 Down months, significance tests have very little power, so results are reported as effect sizes too.

**Remaining in Phase 3** — the classifier itself: logistic regression, random forest and gradient boosting, run through that harness against the two baselines and compared via McNemar's test, with MLflow tracking and SHAP explainability. Later phases: knowledge graph (stretch), monitoring and promotion, MCP/RAG serving layer, website, and deployment.

Development is local-first: cloud infrastructure (the DVC remote, the hosted website) is stood up only once there is something ready to demo publicly. See [`docs/PROPOSAL.md`](docs/PROPOSAL.md) §8 for the course roadmap and §19.6 for the persistence model.

## Tech stack

`pandas` · `SQLite` · `SQLAlchemy Core` · `Pandera` · `scikit-learn` · `statsmodels` · `MLflow` · `Optuna` · `SHAP` · `Evidently AI` · `DVC` · `Docker` · `GitHub Actions` · `FastAPI` · `Claude API` + `MCP` · `Chroma`/`FAISS` (RAG) · `RDFLib`/`NetworkX` (knowledge graph, stretch)

Currently installed in `requirements.txt`: `pandas`, `numpy`, `openpyxl`, `requests`, `pandera`, `sqlalchemy`, `matplotlib`, `seaborn`, `jupyter`, `ipykernel`, `nbformat`, `nbconvert`, `scipy`, `scikit-learn`, `statsmodels`, `ruff`, `pytest`; the rest (`MLflow`, `Optuna`, `SHAP`, `Evidently AI`, `DVC`, `Docker`, `FastAPI`, `Claude API`/`MCP`, `Chroma`/`FAISS`, `RDFLib`/`NetworkX`) are added as each later phase is built.

## Authors

Tushar, Saakshaat Saini
