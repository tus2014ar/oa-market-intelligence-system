# OA & RA Market Intelligence System

A monthly-refreshable, production-oriented classification system that tracks and predicts visit-share direction between branded specialty injectables and generic pain therapies in Osteoarthritis (OA) and Rheumatoid Arthritis (RA), built on real IQVIA National Medical and Treatment Audit (NMTA) patient-visit data.

Built as a capstone project for **DAAN 888 — Design and Implementation of Analytics System**, Penn State University, School of Graduate Professional Studies (Fall 2026).

## The problem

A newer, extended-release branded injectable (Zilretta) is trying to take visit share from decades-old generic corticosteroids and NSAIDs in a high-volume OA market (7.19M+ visits across 6 years in our extract). The system is built around a single stakeholder — an **OA/RA Injectable Brand Manager** — who needs an early signal on competitive share movement before it shows up in a standard quarterly business review.

## What this system does

- **Classifies** next month's visit-share direction (Up / Down / Flat) for the branded injectable in OA, evaluated against a persistence baseline and a classical time-series (SARIMA/ETS) validation check
- **Segments** provider specialties/demographics by adoption level (High vs. Low) — stretch objective
- **Monitors** its own predictions against actual results monthly, and flags meaningful misses
- **Serves** a public, multi-user website (open signup, access-code gated) with a dashboard and a Q&A / on-demand visualization layer: Claude connected through the Model Context Protocol (MCP) to scoped, read-only tools over the Gold tables, plus a RAG tool for methodology and regulatory questions — never raw SQL
- Runs on a **right-sized MLOps pipeline**: DVC for data and model-registry persistence, MLflow + Optuna for experimentation, SHAP for explainability, Evidently AI for drift monitoring, GitHub Actions for CI and the monthly scheduled run — deliberately *without* Kubernetes, Kafka, Databricks, or live A/B testing, since this is a monthly batch system, not a real-time service

RA is an exploratory, comparison-only track: with ~1,283 visits over 6 years (and some miscoded oncology products), it is too sparse for a classifier.

## Architecture in one picture

Data moves through three layers, all stored in one SQLite file (`data/processed/warehouse.db`, accessed via SQLAlchemy Core):

| Layer | What lives there |
|---|---|
| **Bronze** | The raw IQVIA Excel extracts in `data/raw/`, untouched |
| **Silver** | Star schema: 4 dimension tables + 2 fact tables (Place-of-Service is a separate grain, so it gets its own fact table) |
| **Gold** | 2 serving tables: `gold_visit_share_monthly` (core classifier, one row per month) and `gold_segment_adoption` (stretch objective, month × specialty × demographic) |

Engineered features (lags, rolling averages, FDA-derived competitive-context features) are stored as columns in the Gold tables, alongside model predictions. Claude and the website read only from Gold. Each monthly run upserts the dimension tables (to keep surrogate keys stable) and full-refresh-overwrites the fact and Gold tables.

This is now a real, running pipeline, not just a design: `src/oa_market_intelligence/pipeline.py` orchestrates ingest → validate → Silver build → Gold build end to end, and produces a verified `warehouse.db` with 72 months, 160 products, 149,141 product-visit rows, and 135,119 total branded-injectable visits — matching every figure documented in `PROPOSAL.md` §18.1. Run it yourself with:

```bash
PYTHONPATH=src python -m oa_market_intelligence.pipeline
```

A scheduled GitHub Actions workflow ([`.github/workflows/monthly_pipeline.yml`](.github/workflows/monthly_pipeline.yml)) runs the same pipeline monthly (and can be triggered manually), uploading the resulting database as a build artifact.

## Data

The primary dataset is a real IQVIA NMTA patient-visit extract, provided for this capstone under Penn State's data license. The four Excel extracts are committed directly in [`data/raw/`](data/raw/). A maintained product taxonomy mapping (160 products: 145 OA + 15 RA) lives in [`data/reference/product_taxonomy.csv`](data/reference/product_taxonomy.csv) — a product the pipeline hasn't seen before is flagged for human review rather than guessed at.

FDA approval dates come from the free public openFDA Drugs@FDA dataset.

## Documentation

| Document | Covers |
|---|---|
| [`docs/PROPOSAL.md`](docs/PROPOSAL.md) | Full project proposal: objectives, technical architecture (§9), production operations (§17), precise definitions and build decisions (§18), website and AI serving layer (§19) |
| [`docs/data_dictionary.md`](docs/data_dictionary.md) | Field-level structure of every raw source file, verified by direct inspection |
| [`docs/data_analysis_reference.md`](docs/data_analysis_reference.md) | Dataset audit, full product taxonomy, feature priorities, and the strategy for combining NMTA with openFDA |
| [`docs/database_schema.md`](docs/database_schema.md) | SQLite DDL for the Silver and Gold tables, ER diagram, and rebuild rules |
| [`docs/silver_gold_data_dictionary.md`](docs/silver_gold_data_dictionary.md) | What every Silver and Gold column means and where its value comes from |

## Status

**Phase 1 (data foundation and design) is complete**: data dictionary, dataset overview, database choice, star schema, Gold schema, and a reconciled technical architecture are all documented and merged. Real data and the product taxonomy are committed.

**Phase 2 (the pipeline code) is complete**, built and merged step by step with real-data verification at every stage:

- **Ingestion**: parsers for the NMTA pivot extract, the Place-of-Service sheet, and the Branded/Generic reference tables, plus an openFDA client for approval-date lookups
- **Validation**: a Pandera gate that rejects bad data before it can reach the warehouse (value ranges, categorical domains, expected columns)
- **Silver builder**: upserts the 4 dimension tables (stable surrogate keys) and full-refresh-overwrites the 2 fact tables
- **Gold builder**: computes `visit_share`, the Up/Down/Flat direction label, lag/rolling features, and the FDA-derived competitive-context features
- **Pipeline orchestration**: `pipeline.py` ties every stage together behind one CLI command, with a scheduled + manually-triggerable GitHub Actions workflow
- 152 tests (real-data integration tests included, not just mocks), `ruff`-clean, CI green on every PR

A few decisions were deliberately deferred rather than guessed at: the Up/Down/Flat "Flat" threshold (§18.2 — the ±1.0pp default is documented as degenerate against the real 72-month series and will be set during Phase 3's EDA), and full production concerns that need a modeling stage to exist first (writing predictions back to Gold, DVC persistence of `warehouse.db`, Evidently AI monitoring).

**Next: Phase 3 — EDA & modeling.** Target-variable EDA (settling the Flat threshold, time-series decomposition), the two still-missing engineered features (specialty-mix shift, FDA event flags), then the classifier itself: a persistence baseline, logistic regression, random forest, and gradient boosting, backtested with an expanding walk-forward scheme and compared via McNemar's test, with MLflow tracking and SHAP explainability. Later phases: knowledge graph (stretch), monitoring and promotion, MCP/RAG serving layer, website, and deployment.

Development is local-first: cloud infrastructure (the DVC remote, the hosted website) is stood up only once there is something ready to demo publicly. See [`docs/PROPOSAL.md`](docs/PROPOSAL.md) §8 for the course roadmap and §19.6 for the persistence model.

## Tech stack

`pandas` · `SQLite` · `SQLAlchemy Core` · `Pandera` · `scikit-learn` · `statsmodels` · `MLflow` · `Optuna` · `SHAP` · `Evidently AI` · `DVC` · `Docker` · `GitHub Actions` · `FastAPI` · `Claude API` + `MCP` · `Chroma`/`FAISS` (RAG) · `RDFLib`/`NetworkX` (knowledge graph, stretch)

Currently installed in `requirements.txt`: `pandas`, `numpy`, `openpyxl`, `requests`, `pandera`, `sqlalchemy`, `ruff`, `pytest`; the rest (`scikit-learn`, `statsmodels`, `MLflow`, `Optuna`, `SHAP`, `Evidently AI`, `DVC`, `Docker`, `FastAPI`, `Claude API`/`MCP`, `Chroma`/`FAISS`, `RDFLib`/`NetworkX`) are added as each later phase is built.

## Authors

Tushar, Saakshaat Saini
