# OA & RA Market Intelligence System

A monthly-refreshable analytics foundation that tracks Zilretta's visit share against generic corticosteroid injections and NSAIDs in Osteoarthritis (OA) and Rheumatoid Arthritis (RA), built on real IQVIA National Medical and Treatment Audit (NMTA) patient-visit data and a set of public datasets. Built around one stakeholder, an OA/RA Injectable Brand Manager.

**Status:** data profiling, cleaning and preprocessing, ingestion, the data warehouse and exploratory data analysis (EDA) are complete and documented. **Next: feature engineering**, then modelling, then the deliverables. The earlier full build (features, models, website) is preserved in the git tag `archive/full-project-2026-10-11`.

Built as a capstone project for **DAAN 888 — Design and Implementation of Analytics System**, Penn State University, School of Graduate Professional Studies (Fall 2026).

**Team:** Tushar and Saakshaat Saini

## The problem

A newer, extended-release branded injectable (Zilretta) is trying to take visit share from decades-old generic corticosteroids and NSAIDs in a high-volume OA market (7.19M+ visits across 6 years in our extract). The system is built around a single stakeholder, an **OA/RA Injectable Brand Manager**, who needs an early signal on competitive share movement before it shows up in a standard quarterly business review.

## What is built

| Stage | What exists | Where |
|---|---|---|
| **Data profiling** | Raw IQVIA structure, the cleaned warehouse, RA, and a profile of every public file (structure only) | notebooks 01, 02, 03, 10 |
| **Cleaning and preprocessing** | Pandera validation gate, Silver and Gold builders, a product taxonomy, 11 dataset-level quality checks with a stored baseline | `src/oa_market_intelligence/ingestion/`, `warehouse/`, `quality.py` |
| **Ingestion** | The four IQVIA extracts parsed and audited (SHA-256, rows parsed, month span), openFDA approval dates, and 171 public files (CMS, CDC, SEC, FDA, price) with a committed download manifest | `ingestion/`, `external/` |
| **Data warehouse** | IQVIA star schema (4 dimensions, 2 facts, Gold tables) plus a separate public-data database (27 tables, every load reconciled to the raw files) and a committed 6 MB publishable subset | `data/published/warehouse.db`, `data/published/external_subset.db` |
| **Availability layer** | A recorded rule for when each outside value could have been known, and a point-in-time ("as-of") table built from it | `availability.py`, `mart.py` |
| **EDA** | The share series, seasonality and persistence, the sparse segment panel, the outside series on their availability dates, a specialty triangulation against Medicare, and a diagnosis of the sales-versus-visits gap | notebooks 11, 12, 13 |
| **Safety nets** | A golden regression test of the warehouse, an atomic publish with a run log, restatement checks | `golden.py`, `publish.py` |

### What the EDA found (descriptive; no model)
- Zilretta's share rose until early 2022 and has fallen since in IQVIA's data.
- Two data problems shaped the cleaning: a likely mis-coded `PEDIATRICS` prescriber, and a March to July 2024 volume dip across all three product classes.
- Company net sales kept rising while IQVIA's Zilretta visits fell by about half; age mix, care setting and value per visit do not explain the gap, so the size of the IQVIA decline is not a market fact until IQVIA is asked about panel coverage (notebook 12).
- Medicare billing does not confirm the IQVIA specialty ranking (notebook 11).
- The segment panel is sparse (most segment-months are small), and most public series are too lagged or too coarse for monthly use (notebook 13).

## Architecture in one picture

| Layer | What lives there |
|---|---|
| **Bronze** | The raw IQVIA Excel extracts in `data/raw/`, untouched, with a per-run record of their hashes |
| **Silver** | Star schema: 4 dimension tables + 2 fact tables (Place-of-Service is a separate grain, so it gets its own fact table) |
| **Gold** | `gold_visit_share_monthly` (one row per month) and `gold_segment_adoption` (month x specialty x demographic) |
| **Mart** | `mart_signal` and `mart_signal_asof`: outside series with the date each value could have been known |

The pipeline writes `data/processed/warehouse.db` (git-ignored). The publish step builds `data/published/warehouse.db` (committed) in a staging file, checks it, and swaps it in atomically, so a failed run never damages the last good database.

```bash
PYTHONPATH=src python -m oa_market_intelligence.pipeline   # build the warehouse (about 2.5 minutes)
PYTHONPATH=src python -m oa_market_intelligence.publish    # build, check and publish it atomically
PYTHONPATH=src python -m pytest                            # the test suite (CI runs it in parallel)
```

## Data

The primary dataset is a real IQVIA NMTA patient-visit extract, provided for this capstone. The Excel extracts are committed directly in [`data/raw/`](data/raw/) with the course instructor's approval under Penn State's data license. A maintained product taxonomy (160 products: 145 OA + 15 RA) is in [`data/reference/`](data/reference/).

FDA approval dates come from the free public openFDA Drugs@FDA API; a snapshot is committed so the work is reproducible without the internet. The public datasets (Medicare Part B and D, geographic variation, CDC, ASP price, SEC filings, OpenPayments) are restorable from the committed manifest ([`docs/external_data_refresh.md`](docs/external_data_refresh.md)); the full 134 MB database is local only.

## Documentation

| Document | Covers |
|---|---|
| [`docs/decision_log.md`](docs/decision_log.md) | Every significant decision, why it was made, what was rejected (entries up to DL-70; some describe work kept in the archive tag) |
| [`docs/PROPOSAL.md`](docs/PROPOSAL.md) | The original project proposal: objectives, architecture, definitions and build decisions |
| [`docs/data_dictionary.md`](docs/data_dictionary.md), [`database_schema.md`](docs/database_schema.md), [`silver_gold_data_dictionary.md`](docs/silver_gold_data_dictionary.md), [`data_analysis_reference.md`](docs/data_analysis_reference.md) | Raw field structure, the star schema and DDL, every Silver and Gold column, and the dataset audit |
| [`docs/external_data_protocol.md`](docs/external_data_protocol.md) | The protocol for the public datasets: code sets, specialty crosswalk, pass or fail rules, fixed before any analysis |
| [`docs/external_data_lineage.md`](docs/external_data_lineage.md) | Every column of the external-data database with its source file, original column and rule |
| [`docs/external_data_refresh.md`](docs/external_data_refresh.md) | How to restore the raw public files, rebuild and verify the external database |
| [`docs/external_data_results.md`](docs/external_data_results.md) | Results of the public-data analyses: each pre-registered rule, its verdict, the numbers, the limits |

**Notebooks** (executed end to end; each opens with the question, the answer in one line, and the method in plain words):

| Notebook | Covers |
|---|---|
| [`01_eda_raw_data`](notebooks/01_eda_raw_data.ipynb), [`02_eda_cleaned_data`](notebooks/02_eda_cleaned_data.ipynb), [`03_eda_ra_data`](notebooks/03_eda_ra_data.ipynb) | Raw structure, the cleaned warehouse, RA |
| [`10_external_raw_data_profile`](notebooks/10_external_raw_data_profile.ipynb) | Profile of the raw public files (structure only) |
| [`11_external_analysis_results`](notebooks/11_external_analysis_results.ipynb), [`12_gap_diagnosis`](notebooks/12_gap_diagnosis.ipynb) | The pre-registered public-data analyses, then the sales-versus-visits gap diagnosis |
| [`13_eda_unified_data`](notebooks/13_eda_unified_data.ipynb) | Descriptive EDA of the unified data before feature engineering. Runs from committed files only |

Notebooks 10 to 12 need the local external database and run only on the author's machine; notebook 13 runs from committed files.

## Status

**Phase 1 and 2 (data foundation, design and the pipeline code):** complete. **Profiling, cleaning, ingestion, warehouse and EDA:** complete, including the data layer (availability rules, ingest audit, quality stage, as-of mart, golden regression, public subset). **Next:** feature engineering, with a written plan and rules fixed before anything is built or run.

The earlier full build (leak-safe features, evaluation harness, models, a Streamlit site with a Claude question box, and a feature and modelling improvement round, DL-71 to DL-74) is kept in the tag `archive/full-project-2026-10-11` and is not part of this branch of the project.

## Tech stack

**In use (`requirements.txt`):** `pandas`, `numpy`, `openpyxl`, `requests`, `pandera`, `sqlalchemy`, `scipy`, `scikit-learn`, `statsmodels`, `matplotlib`, `seaborn`, `jupyter` and its kernel tools, `ruff`, `pytest`. **Infrastructure:** SQLite, GitHub Actions (lint and tests on every pull request; a monthly publish workflow).

## Authors

Tushar, Saakshaat Saini
