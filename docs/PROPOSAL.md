# OA & RA Market Intelligence System — Project Proposal

**Course**: DAAN 888 — Design and Implementation of Analytics System
**Program**: School of Graduate Professional Studies, Penn State University
**Term**: Fall 2026
**Team**: Tushar, Saakshaat Saini
**Revision**: Project Proposal, 6 September 2026

---

## Executive Summary

The OA & RA Market Intelligence System is an end-to-end, production-oriented classification platform that predicts monthly visit-share direction (Up / Down / Flat) between branded specialty injectables and generic pain therapies in Osteoarthritis and Rheumatoid Arthritis, built on real IQVIA NMTA patient-visit data (7.19M+ OA visits across 6 years, of which 5.32M carry a specific product record and form the basis of the visit-share calculation, see §6.1). It is designed around one stakeholder — an Injectable Brand Manager — who needs an early, defensible signal on competitive share movement before it appears in a standard quarterly business review. The system covers the complete ML lifecycle: ingestion, a structured data warehouse, a knowledge graph for competitive context, a classifier evaluated against a persistence baseline with statistical rigor, a locally-deployed LLM for confidential natural-language querying and reporting, and a self-monitoring production loop that retrains monthly. It is deliberately right-sized, real MLOps tooling (MLflow, DVC, Docker, Evidently AI) chosen to fit a monthly-batch, two-person-team system rather than enterprise infrastructure the problem doesn't need.

---

## 1. Purpose & Objectives

The OA & RA Market Intelligence System is a monthly-refreshable predictive analytics platform designed to classify and monitor visit-share direction between branded specialty injectables and generic pain therapies across two disease areas — Osteoarthritis (OA) and Rheumatoid Arthritis (RA) — using IQVIA's NMTA patient-visit data. The system supports the kind of commercial decision-making an Injectable Brand Manager performs, enabling early detection of competitive shifts before they surface in standard quarterly business reviews.

### 1.1 Core Project Objectives

- **Objective 1 (Core)**: Quantify how visit share among OA treatment categories (branded injectable, generic corticosteroid, NSAID — see §18.1) has shifted over the 6-year available history and identify inflection points tied to FDA approval events. *(Note: whether a true launch-inflection is observable for the branded injectable depends on its FDA approval date falling within our Aug 2019–Jul 2025 data window — this is verified, not assumed; see §18.5.)* For RA, whose competitive structure is fundamentally different (originator biologics vs. biosimilars, not branded-vs-generic small molecules — see §6.2), the equivalent analysis is a visit-volume and originator-vs-biosimilar share trend, not the same three-category formula or a formal inflection-point test.
- **Objective 2 (Core)**: Build a classifier that predicts next month's visit-share direction (Up / Down / Flat) for the branded injectable in OA, evaluated against a persistence baseline. RA is explicitly excluded from classification (see §6.2, §10).
- **Objective 3 (Stretch)**: Classify each specialty/demographic segment as High- or Low-Adoption of the branded injectable, identifying where share is gaining fastest across both disease areas.
- **Objective 4 (Stretch)**: Build a monitoring layer that compares each month's predicted direction to actual results and automatically flags meaningful misses or accuracy drops.

### 1.2 Business Impact

Every objective is tied directly to one primary stakeholder — the OA & RA Injectable Brand Manager — and the decisions they would make differently because of this system:

- Early detection of competitive visit-share shifts 1–2 months before standard quarterly business review cycles.
- Early Up/Down/Flat signals to inform budget allocation and sales-force planning before the quarter closes.
- Segment-level adoption classification showing which specialties (orthopedic surgery, pain medicine, rheumatology, sports medicine) are gaining fastest, informing field team and marketing spend.
- Automated miss-flagging that prompts review when OA's actual visit-share direction diverges from what the classifier predicted. (RA has no classifier to compare against; its monitoring is trend-based, not prediction-based — see §6.2.)
- Comparative RA vs. OA intelligence: since RA data is sparse (M04 codes, ~1,283 visits across 6 years), the system treats OA as the primary model target and RA as a parallel exploratory/monitoring track, enabling the brand manager to contrast market dynamics across both disease areas.

**Estimated value of early detection**: if the classifier reliably flags a share decline even one quarter earlier than a standard business review would surface it, the brand team gains an extra quarter to respond, adjust field targeting, revisit pricing/contracting conversations, or reallocate marketing spend, before further share is lost. For a product at meaningful commercial scale, even a small reduction in unaddressed decline translates to material avoided revenue loss; the exact figure depends on the specific product's revenue base and is out of scope for this proposal, but the mechanism (an earlier decision point) is the system's core source of value.

---

## 2. Research & Business Questions

| # | Type | Question |
|---|------|----------|
| Q1 | Trend | How has visit share among OA and RA treatment categories shifted over the 6-year available history, and is there a detectable inflection point around branded injectable entry (corroborated by FDA approval dates)? |
| Q2 | Direction | Can a classifier reliably predict next month's visit-share direction (Up / Down / Flat) for the branded injectable in OA, and to what extent can the same framework be applied to RA? By how much does it beat a persistence baseline? |
| Q3 | Segmentation | Can specialty/demographic segments be classified as High- or Low-Adoption of the branded injectable in OA, and does adoption vary by specialty (orthopedic surgery, pain medicine, sports medicine, rheumatology)? |
| Q4 | Monitoring | How often does the OA classifier's predicted direction match actual results month to month, and what accuracy threshold should trigger a review flag? (RA has no classifier; its monitoring is limited to trend/volume review — see §6.2.) |

---

## 3. Data Sources

### 3.1 Primary Dataset — IQVIA NMTA Patient Visit Extract

The primary dataset is an IQVIA National Medical and Treatment Audit (NMTA) patient visit extract covering August 2019 through July 2025 (6 complete years). This is a proprietary commercial dataset provided for this capstone project and is **not included in this repository**.

| Field | Description |
|-------|-------------|
| Patient Visits | Monthly visit volume — the core metric for all share calculations. |
| Manufacturer & Product | Hundreds of distinct products across the OA and RA markets, including branded and generic injectables. |
| ICD-10 Scope | M15–M19 (Osteoarthritis: hip, knee, and other/unspecified sites) and M04 (Rheumatoid Arthritis: sparse). |
| Specialty × Age × Gender | 600+ column crosstab covering orthopedics, pain medicine, rheumatology, and more. |
| Place of Service | Office, Hospital, Telehealth, Other — tracked monthly. |
| Brand / Generic Tag | Four values: BRAND, GENERIC, BRANDED GENERIC, or OTHER. Reflects patent/ownership status, not therapeutic class — e.g., real NSAIDs like ibuprofen are tagged OTHER, not NSAID (see §18.1). |

### 3.2 Secondary Dataset — FDA Approval Dates

Launch dates for the branded injectable (Zilretta) and competitor products are sourced from **openFDA** (free, public API). This enables real event-based inflection-point validation rather than relying solely on visual inspection of trend charts.

### 3.3 What Is Not in the Extract (Known Gaps)

- NPA-style Rx volume, sales ($), or true market-share metrics.
- Administered vs. Prescribed distinction or Indication Approval Status.
- Geographic region or territory-level breakdown.
- Visit sequencing (times seen, days since last visit).

These gaps are acknowledged explicitly. **Visit share** is used as our proxy for market share throughout, and all reporting makes this distinction clear.

### 3.4 Data Validation Approach

Each incoming monthly extract is validated before it enters the pipeline, not just cleaned after the fact. Using a schema-validation library (Pandera or Great Expectations), the ingestion stage checks: expected column count and naming convention for the wide pivot export, valid ranges for visit counts (non-negative, no implausible spikes), expected categorical values for Brand/Generic tag and Place of Service, and presence of the expected ICD-10 scope (M15–M19, M04). A malformed or out-of-spec extract is flagged and halted before it can silently corrupt a monthly model run, rather than being discovered downstream after a bad prediction ships.

---

## 4. Knowledge Graph & Data Warehouse Architecture

To support enriched querying, lineage tracking, and multi-domain analytics across OA and RA data, the system incorporates both a structured data warehouse layer and a knowledge graph layer.

### 4.1 Data Warehouse

The data warehouse serves as the central structured storage layer for all cleaned, transformed, and model-ready data, organized into:

- **Fact Tables**: Monthly visit volume by product × specialty × age band × gender × place of service × ICD-10 code.
- **Dimension Tables**: Product master (brand/generic tag, manufacturer, therapeutic category), Time dimension (month, quarter, year, FDA event flags), Specialty dimension, Demographics dimension.
- **Aggregate Tables**: Pre-computed monthly visit-share by treatment category (branded injectable / generic corticosteroid / NSAID) for rapid dashboard refresh.

The warehouse is designed for a monthly-batch refresh cycle aligned with IQVIA NMTA extract delivery. DVC (Data Version Control) versions each monthly extract so historical comparisons remain reproducible.

### 4.2 Knowledge Graph

A knowledge graph layer enriches the structured warehouse data with relational context that tabular schemas cannot easily express:

- **Entity nodes**: Products, Manufacturers, Drug Classes, ICD-10 Codes (M04, M15–M19), Specialties, FDA Approval Events.
- **Relationship edges**: `is-a-branded-alternative-to`, `approved-for-indication`, `prescribed-by-specialty`, `competes-with`, `entered-market-on`.
- **Use cases**: Automated treatment-category taxonomy construction; linking FDA approval events to visit-share inflection points; enabling natural-language query interfaces over the data pipeline.

The knowledge graph is stored in a lightweight graph format (RDFLib or NetworkX, appropriate for course scope) and is queried at feature engineering time to enrich the classifier's inputs with competitive context (e.g., "how many competing branded injectables were on market in this month?").

---

## 5. Local LLM Integration

The system incorporates a locally deployed Large Language Model (LLM) to support two specific use cases without requiring cloud API calls or transmitting proprietary IQVIA data externally.

### 5.1 Rationale for Local Deployment

Since the NMTA dataset is proprietary commercial data, sending it to external LLM APIs (e.g., OpenAI, Anthropic) raises data governance and confidentiality concerns. A locally deployed open-source LLM (e.g., LLaMA 3, Mistral, or Phi-3, run via Ollama or llama.cpp) keeps all data on-premises.

### 5.2 Use Case 1 — Natural Language Query Interface

The local LLM acts as a query interface over the knowledge graph and data warehouse, allowing the brand manager to ask questions in plain English, e.g.:

- "Which specialty segments showed the largest OA branded injectable share gain last month?"
- "How did RA visit share compare to OA visit share in Q3 2024?"
- "When did Zilretta's share cross 5% and what happened to generic corticosteroid share in the same month?"

The LLM translates these queries into structured lookups against the knowledge graph and warehouse, returning grounded answers with citations to the underlying data.

### 5.3 Use Case 2 — Automated Narrative Reporting

After each monthly model run, the local LLM auto-drafts a one-page narrative summary of the classifier's output — describing the predicted direction, the key drivers surfaced by SHAP, and any flagged monitoring alerts. This draft is reviewed by the team before delivery to the brand manager, preserving human oversight while reducing reporting time.

### 5.4 Implementation Approach

- **Model**: LLaMA 3 8B or Mistral 7B (quantized 4-bit via llama.cpp for CPU-only environments).
- **Serving**: Ollama for a local REST API; no GPU required for course scope.
- **Integration**: Python client calls the local Ollama endpoint; responses are post-processed and injected into the dashboard or report template.
- **Data safety**: NMTA data is only passed as aggregated summaries to the LLM prompt, never raw row-level records.

---

## 6. Dual Disease-Area Analysis: OA and RA

While OA (M15–M19) is the primary modeling target due to data volume (7.19 million visits over 6 years), the system is architected to analyze both OA and RA (M04) in parallel, providing the brand manager with comparative intelligence across both disease areas.

### 6.1 OA Analysis (Primary Track)

The OA track is the fully developed production model, including all four objectives: trend analysis, visit-share direction classification, segment adoption classification, and automated monitoring.

- **Volume**: ~7.19M total OA visits, Aug 2019 – Jul 2025, of which ~5.32M carry a specific product record and form the basis of the visit-share calculation (§18.1); the remaining ~1.87M are diagnosis-only encounters with no product recorded, not a data-loss error.
- **Market dynamics**: The branded injectable (Zilretta, 135,133 visits, the only product in its category with no generic equivalent) competing against generic-equivalent corticosteroids (Kenalog, Depo-Medrol, and 50 other products, 4.91M visits combined) and NSAIDs (54 products, 398,722 visits combined).
- **Classification target**: Monthly Up/Down/Flat direction for branded injectable share.
- **Baseline**: Persistence model (last month's direction repeated).

### 6.2 RA Analysis (Secondary/Exploratory Track)

The RA track is an exploratory parallel analysis. With only ~1,283 visits over 6 years, RA data is too sparse for a reliable monthly classifier, confirmed further by two data-level findings, not just the low volume: the RA product reference table contains **zero generic-tagged products** (all 18 entries are BRAND), and three of those products (Opdivo, Yervoy, Keytruda) are cancer immunotherapy drugs, not RA treatments at all, almost certainly miscoded under the M04 diagnosis after a checkpoint-inhibitor-related inflammatory joint side effect. RA's data isn't just small, some of it isn't RA at all. Given this, the following analyses are feasible and valuable instead of a classifier:

- **Trend analysis**: 6-year visit volume trend for RA biologic injectables by product.
- **Originator-vs-biosimilar decomposition**: unlike OA's branded-vs-generic-small-molecule dynamic, RA's real competitive structure is originator biologic vs. biosimilar (e.g., Remicade vs. its biosimilar Inflectra), reflecting a biologic-drug market rather than small-molecule generics.
- **Comparative intelligence**: Side-by-side OA vs. RA branded injectable adoption curves — useful context for a brand manager whose portfolio may span both indications.
- **Knowledge graph linkage**: RA products and approved indications are mapped in the knowledge graph, enabling the LLM to answer cross-indication queries.

RA will not have a direction classifier in the core scope due to data sparsity and the data-quality issues above, but is flagged as a future-extension case once additional months of NMTA data are collected.

### 6.3 Comparative OA vs. RA Dashboard

A dedicated dashboard panel displays OA and RA metrics side by side, including visit share trends, branded injectable share over time, top 5 gaining specialties, and monitoring flags — for a brand manager who needs to quickly assess whether branded injectable performance is consistent across disease areas or diverging.

---

## 7. Project Tasks

| # | Task Category | Description | Deliverables |
|---|---------------|-------------|---------------|
| 1 | Data Collection | Receive and validate the IQVIA NMTA extract (OA + RA ICD-10 scope). Confirm with the instructor whether Rx-volume, Administered-vs-Prescribed, Approval Status, or Region data is still incoming. Retrieve FDA approval dates for all branded injectables via openFDA API. | Raw NMTA extract, openFDA records, data inventory document. |
| 2 | Data Analysis (EDA) | Explore monthly visit volume, place of service distribution, and product mix. Surface the branded-vs-generic competitive pattern in OA. Characterize RA visit data separately to confirm sparsity and set scope expectations. Build preliminary OA vs. RA comparison charts. | EDA notebook, summary statistics, preliminary visualizations. |
| 3 | Data Cleaning | Reshape the wide pivot export (600+ columns) to tidy long format. Standardize product and manufacturer names. Resolve unspecified categorical values. Separate OA (M15–M19) and RA (M04) records into distinct analytic tables. Load cleaned data into the data warehouse schema. | Cleaned long-format dataset, data warehouse tables (OA + RA fact/dim), DVC-versioned data snapshot. |
| 4 | Variable Selection & Transformation | Build the OA treatment-category taxonomy (branded injectable / generic corticosteroid / NSAID) from the Brand/Generic tag and product names (§18.1, §10). Build RA's separate originator-vs-biosimilar product mapping (§6.2), a different taxonomy reflecting RA's biologic-drug market. Engineer monthly lag features, rolling averages, seasonal indicators, and FDA event flags via the knowledge graph for OA. Document all variable definitions. | Feature engineering pipeline, variable dictionary, knowledge graph with product/FDA nodes. |
| 5 | Modelling | Build and evaluate the monthly visit-share direction classifier (Up/Down/Flat) for OA against a persistence baseline. Evaluate candidate models: logistic regression, random forest, gradient boosting (MLflow + Optuna for hyperparameter tuning). Apply SHAP for explainability. Run RA exploratory trend analysis in parallel. Integrate local LLM for narrative generation. | Trained OA classifier, MLflow experiment log, SHAP plots, RA trend analysis, LLM narrative draft module. |
| 6 | Data Visualization | Build the OA trend/prediction dashboard (visit-share over time, predicted direction, SHAP drivers). Add RA panel for side-by-side comparison. Build final demo visuals. Implement automated monitoring alerts and LLM-generated summary narrative. | Interactive dashboard (OA + RA panels), monitoring alert module, final presentation visuals. |

---

## 8. Project Roadmap

| Week | Milestone | Key Activities |
|------|-----------|-----------------|
| Week 2 | Proposal Submission | Finalize proposal; confirm data receipt and scope with instructor. |
| Week 4 | Data Collection & Variables | Data inventory, EDA kickoff, variable documentation, openFDA retrieval. |
| Week 6 | Storage Plan | Data warehouse schema finalized; data cleaning pipeline complete; DVC versioning; knowledge graph entity/relationship map. |
| Week 8 | Data Cleaning Complete | Long-format reshape done; OA and RA records separated; cleaned tables loaded to warehouse. |
| Week 10 | Variable Selection & Transformation | Feature engineering pipeline complete; treatment-category taxonomy built; lag/seasonal features engineered. |
| Week 12 | Modeling & Evaluation | OA classifier trained and evaluated (MLflow); SHAP explainability; RA trend analysis; local LLM integrated. |
| Week 13 | Report & Visualization | Dashboard built (OA + RA panels); monitoring alerts implemented; LLM narrative module tested. |
| Week 14 | Live Demo | End-to-end system demo; final report submitted; production cycle documented. |

---

## 9. Technical Architecture

The system is designed as a right-sized MLOps pipeline appropriate for a 2-person capstone team. Each layer is chosen for fit rather than enterprise scale.

| Layer | Stage | Tools / Approach |
|-------|-------|-------------------|
| Strategy | Problem Definition | Business KPI → ML task mapping; feasibility analysis; SLA definition. |
| Data Layer | Data Ingestion | Monthly NMTA extract + DVC versioning; openFDA API for FDA events; Pandera/Great Expectations schema validation before entry. |
| Data Layer | Data Warehouse | Star-schema structured storage (OA + RA fact/dim tables); monthly-batch refresh. |
| Data Layer | Knowledge Graph | RDFLib / NetworkX; product-indication-approval entity-relationship map. |
| Data Layer | Exploratory Analysis | Trend, distribution, and correlation checks; OA vs. RA comparative EDA. |
| Modeling | Feature Engineering | Lag/seasonal features via sklearn Pipelines; KG-derived competitive context features. |
| Modeling | Model Development | Logistic regression → random forest → gradient boosting; MLflow + Optuna. |
| Modeling | Evaluation | Time-based train/test split (expanding-window backtest, §18.3); precision/recall/F1; SHAP explainability; backtesting across historical months in place of live A/B testing; McNemar's test on the paired baseline-vs-model "Down"-class correctness indicator (§18.4), not a point-estimate comparison alone. |
| MLOps / Prod | Local LLM | LLaMA 3 / Mistral via Ollama; natural language query + automated narrative. |
| MLOps / Prod | Monitoring | Drift + accuracy tracking via Evidently AI; monthly direction-vs-actual check. |
| MLOps / Prod | Productionization | joblib + Docker; scheduled monthly run (not Kubernetes-scale). |
| MLOps / Prod | Iteration | Monthly retrain on real-world ground truth as new IQVIA data lands. |
| MLOps / Prod | CI/CD | GitHub Actions: lint and run unit tests on every push, so a broken pipeline change is caught before it merges, not discovered at the next monthly run. |

Deliberately **not** used: Kubernetes, Kafka, live A/B testing, and Grafana-style real-time dashboards — none of these fit a monthly-batch, 2-person-team system, and choosing not to over-engineer is itself a deliberate design decision.

---

## 10. Key Data Considerations

The following analytical maturity points are explicitly acknowledged before modeling begins:

- **Wide-to-long reshape required**: Data arrives in pivot-table shape (600+ demographic combination columns). Reshaping to tidy long format is a mandatory first step, not optional.
- **Visit share as market share proxy**: We have no NPA-style Rx volume or sales ($) data — only patient visit counts. This distinction is made explicit in all reporting.
- **Treatment-category taxonomy**: Branded injectable / generic corticosteroid / NSAID groupings are not pre-labeled, and the raw Brand/Generic tag alone is actively misleading here: Kenalog and Depo-Medrol are tagged BRANDED GENERIC/BRAND, the same tags used for Zilretta, yet both have real generic equivalents elsewhere in the data (e.g., Amneal's, Teva's, and Northstar Rx's triamcinolone acetonide) and so belong in the generic-corticosteroid bucket, not as branded peers to Zilretta. The taxonomy is built from the Brand/Generic tag plus manual product-name review and cross-checking for generic equivalents, not the tag alone.
- **Manufacturer-of-record changes mid-window**: Zilretta's manufacturer changed during the 6-year window (Flexion Therapeutics, later acquired by Pacira BioSciences), splitting its visits across two manufacturer labels in the raw data (11,235 + 123,898 = 135,133 combined). Grouping by (Manufacturer, Product) instead of Product name alone would understate its true share by roughly 8%; Product-name-only grouping is used throughout.
- **Internal total discrepancy in the OA reference file**: the OA Brand/Generic reference file's row-level product total (5,561,131) does not exactly match its own printed Grand Total (5,323,282), a ~4.3% internal inconsistency. This is flagged as an open item to raise with the instructor/IQVIA contact rather than treating either figure as authoritative.
- **RA data sparsity**: Only ~1,283 total visits over 6 years for M04 (RA). Confirmed as too sparse for monthly direction classification, and partly not RA-specific at all (§6.2). RA is included as an exploratory/monitoring parallel track, not a primary classification target.
- **OA as primary model target**: All core classifier development focuses on OA (M15–M19) data, with RA analysis run in parallel for comparative intelligence.
- **Local LLM data governance**: Only aggregated summaries (not raw IQVIA rows) are passed to the local LLM prompt, protecting data confidentiality.

---

## 11. Repository Structure

The repository is organized so each folder maps directly to a stage in the Technical Architecture (§9). Only `README.md`, `.gitignore`, and `docs/PROPOSAL.md` exist as of this proposal; the rest is the target structure the team will build into over the course of the semester.

```
oa-market-intelligence-system/
├── .github/workflows/            # CI/CD — lint + test on every push
├── data/
│   ├── raw/                     # gitignored — real NMTA extracts
│   ├── synthetic/                 # public-safe stand-in dataset (once built)
│   ├── interim/                    # gitignored — reshape outputs
│   └── processed/                  # DVC-tracked — model-ready tables
├── src/oa_market_intelligence/
│   ├── ingestion/                  # NMTA extract loader, openFDA client
│   ├── warehouse/                  # star-schema fact/dimension table builders
│   ├── knowledge_graph/            # entity/relationship graph builder (RDFLib/NetworkX)
│   ├── features/                   # taxonomy, lag/season features, KG-derived context
│   ├── models/                     # baseline, classifier training, evaluation, SHAP
│   ├── llm/                        # Ollama client, NL query interface, narrative generation
│   ├── monitoring/                 # Evidently AI drift checks, alerting
│   └── pipeline.py                 # orchestrates the monthly end-to-end run
├── notebooks/                       # EDA, reshape validation, model exploration
├── tests/                           # unit tests per module
├── dashboard/                       # Streamlit/Dash app (OA + RA panels)
├── docker/Dockerfile
├── docs/
│   ├── PROPOSAL.md                 # this document
│   ├── ARCHITECTURE.md
│   ├── data_dictionary.md
│   └── MODEL_CARD.md               # intended use, training data, limitations, performance by segment
├── .dvc/                            # data versioning config
├── mlruns/                          # gitignored — MLflow local tracking store
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 12. Full ML Pipeline Coverage

This section maps the project explicitly onto the standard machine learning lifecycle, to make clear the system covers the complete pipeline, not just model training.

| Standard ML Lifecycle Stage | Coverage |
|---|---|
| Business understanding | Purpose, Objectives, and named stakeholder (§1) |
| Data collection | IQVIA NMTA extract + openFDA API (§3) |
| Data storage / warehousing | Star-schema fact/dimension tables (§4.1) |
| Exploratory data analysis | Notebooks; monthly volume, place-of-service, product-mix analysis |
| Data cleaning | Wide-to-long reshape, categorical standardization (§10) |
| Feature engineering | Lag/seasonal features, treatment-category taxonomy, KG-derived competitive context (§4.2, §7) |
| Model training with baseline | Persistence baseline → logistic regression → random forest → gradient boosting (§9) |
| Evaluation | Time-based train/test split, precision/recall/F1 with class-specific focus, SHAP explainability (§9) |
| Deployment | joblib packaging + Docker containerization, scheduled monthly run (§9) |
| Monitoring | Evidently AI drift and accuracy tracking (§9) |
| Iteration / retraining | Monthly retrain cycle on real-world ground truth (§9) |
| Reproducibility / versioning | DVC (data) + MLflow (experiments/models) |
| Serving / interface | Local LLM natural-language query interface + interactive dashboard (§5, §6.3) |

Every stage of a complete pipeline is represented, including two stages most student (and many early-stage industry) projects skip entirely: a self-monitoring feedback loop and a serving/query interface.

---

## 13. Data Science & Machine Learning Concepts Applied

**Core statistics and machine learning**
- Supervised learning; multi-class classification (Up/Down/Flat) and binary classification (High/Low Adoption)
- Time-based train/test splitting (not random) — required for time-series data to avoid future-information leakage
- Baseline modeling — persistence baseline as the mandatory bar any model must clear
- Ensemble methods / gradient-boosted trees
- Hyperparameter tuning (Optuna) and experiment tracking (MLflow)
- Class-specific evaluation — precision/recall prioritized on the business-critical "Down" class, not overall accuracy alone
- Model explainability (SHAP)
- Class imbalance awareness (High/Low Adoption segmentation)
- Statistical significance testing on model-vs-baseline comparisons, not point estimates alone
- Backtesting across historical periods, as the substitute for live A/B testing

**Data engineering**
- Wide-to-long (tidy data) reshaping
- Star-schema data modeling — fact tables vs. dimension tables
- Data versioning (DVC) for reproducibility
- Schema/data validation (Pandera or Great Expectations) at ingestion, before a bad extract can enter the pipeline

**Knowledge representation**
- Entity-relationship / graph data modeling — nodes (products, manufacturers, FDA events) and typed edges (`competes-with`, `approved-for-indication`)
- Graph-derived feature engineering — deriving model features from relationships a flat table can't easily express

**Applied LLM engineering**
- Retrieval-grounded querying (a RAG-style pattern) — the LLM answers are grounded in structured lookups against the knowledge graph/warehouse with citations, not free generation
- Local model quantization and serving (4-bit via llama.cpp/Ollama) — running an 8B-parameter model on CPU rather than a cloud API
- Data governance by design — deciding what the LLM prompt is allowed to see (aggregated summaries only, never raw rows) as an engineering constraint, not just policy

**MLOps / production engineering**
- Containerization (Docker) for reproducible execution
- Batch inference architecture — a deliberate choice over real-time serving, matched to the monthly data cadence
- Data/concept drift monitoring (Evidently AI)
- Scoped infrastructure decisions — explicitly not using Kubernetes, Kafka, or live A/B testing, and being able to justify why each doesn't fit this system's scale
- Champion/challenger deployment pattern — a candidate model must beat the currently-deployed model, not just a static baseline, before promotion
- SLA-driven operations — concrete, stated commitments for data freshness, processing turnaround, and alerting latency
- Rollback and incident-response design — what the system does when a new model underperforms or a pipeline run fails, defined in advance, not improvised
- Access control by design — deciding who can see the data, dashboard, and model outputs as part of the architecture, not an afterthought

**Business/analytical judgment**
- Translating a business KPI into a specific ML task formulation
- Proxy-metric reasoning — using visit share as a stated, explicit substitute for market share given known data gaps
- Feasibility assessment — using data volume (7.19M vs. ~1,283 visits) to make a real scope decision between OA and RA rather than treating both identically

---

## 14. Responsible AI & Model Risk

This system informs commercial decisions, not clinical ones, and its outputs are advisory, not autonomous. Three specific controls follow from that:

- **Human-in-the-loop for LLM output**: every auto-drafted narrative summary (§5.3) is reviewed by the team before it reaches the Brand Manager. The LLM drafts; it does not publish unsupervised.
- **Grounded, citable answers only**: the natural-language query interface (§5.2) is restricted to structured lookups against the knowledge graph and warehouse, not free generation, specifically to reduce the risk of a confidently-wrong, ungrounded answer. Every answer traces back to the underlying data it was computed from.
- **Explicit uncertainty communication**: the classifier's output is a prediction with a known baseline-relative accuracy, not a guarantee. Dashboard and narrative outputs state the model's current accuracy alongside the prediction, so the Brand Manager can calibrate how much weight to give it.

This system does not process or expose patient-identifiable information; the NMTA extract is aggregate visit-count data, not patient-level records.

---

## 15. Model Card (Planned)

A model card (`docs/MODEL_CARD.md`) will be published alongside the trained classifier, following the standard practice established by Google and Hugging Face for documenting model behavior and limitations. It will include:

- **Intended use**: early-warning classification of OA branded-injectable visit-share direction for commercial decision support; not intended for clinical, regulatory, or patient-level use.
- **Training data**: IQVIA NMTA patient-visit extract, Aug 2019–Jul 2025, OA (M15–M19) scope; RA (M04) explicitly excluded from the trained classifier due to data sparsity.
- **Evaluation results**: accuracy/precision/recall against the persistence baseline, backtested across historical periods (populated once modeling is complete — see §16).
- **Known limitations**: visit counts as a proxy for market share; no patient-level, regional, or Rx-volume data; model reflects patterns in a single commercial dataset and may not generalize beyond it.
- **Performance by segment**: whether accuracy holds consistently across specialties and demographic segments, or degrades for any particular one.

---

## 16. Preliminary Results (To Be Completed)

Model training has not yet begun as of this proposal (Week 2). This section is structured now so results can be filled in directly as they become available, rather than assembled from scratch at the end of the project.

| Metric | Persistence Baseline | Trained Classifier | Statistically Significant? |
|---|---|---|---|
| Overall accuracy | TBD | TBD | TBD |
| Precision ("Down" class) | TBD | TBD | TBD |
| Recall ("Down" class) | TBD | TBD | TBD |
| F1 ("Down" class) | TBD | TBD | TBD |

Backtesting results across historical periods, and the High/Low Adoption segment classifier's performance, will be reported here in the same format once available (target: Week 12, per the roadmap in §8).

---

## 17. Production Operations

This section defines how the system actually operates once running, not just how it's built, the part of "production" that's easy to skip and most reveals whether a system was designed to be operated, not just demoed once.

### 17.1 Service Level Agreements (SLAs)

- **Refresh cadence**: monthly, aligned to NMTA's own ~40-day data-lag cycle.
- **Processing SLA**: the pipeline completes, end to end, within 24 hours of a new extract landing.
- **Availability definition**: for a batch system, "available" means the monthly prediction and dashboard are refreshed and reviewable before the next monthly cycle begins, not 24/7 uptime, which doesn't apply to a system with no live traffic.
- **Alerting SLA**: a divergence or accuracy-drop flag fires within the same pipeline run that detects it, never held for a manual review to discover later.

### 17.2 Deployment & Model Promotion (Champion/Challenger)

On every push to `main`, CI runs linting and unit tests (§9). When a retrained model ("challenger") is produced, it is evaluated against both the persistence baseline **and** the currently-deployed model ("champion") on the backtest set. Only if the challenger beats the champion on the primary metric (recall on the "Down" class) is it promoted to the MLflow Model Registry's `Production` stage; the next scheduled pipeline run then uses it automatically. A challenger that doesn't beat the champion is logged and discarded, the system never silently swaps in a worse model.

### 17.3 Rollback Procedure

If a newly promoted model's live monthly accuracy falls below its backtested expectation for two consecutive months, or a monitoring alert flags a significant miss, the pipeline automatically reverts to the previous `Production`-stage model in the MLflow registry while the team investigates, rather than continuing to serve a degraded model.

### 17.4 Monitoring & Alerting Channels

Drift and accuracy alerts (Evidently AI, §9) post to a dedicated Slack channel (or email distribution list for course scope) naming the specific flagged metric and its threshold, not a dashboard indicator someone has to remember to check.

### 17.5 Incident Response

If a monthly pipeline run fails (a malformed extract that fails validation, an ingestion error, etc.), the last successful prediction stays live and visible, the dashboard never goes silently blank, and the team is alerted to investigate before the next cycle runs.

### 17.6 Access Control

The dashboard, LLM query interface, and underlying warehouse are restricted to the project team and the named stakeholder (Brand Manager); no public exposure of the data or model internals.

### 17.7 Cost & Resource Considerations

The system deliberately runs on standard team hardware, CPU-only, no GPU required (§5.4), so the monthly retrain and local LLM inference carry no recurring cloud compute cost. This is itself a scoping decision: the system is designed to be operable by a small team with no infrastructure budget, not just technically functional.

### 17.8 Cloud Infrastructure Footprint

The system's design intentionally keeps sensitive data local (§5.1), but that doesn't mean zero cloud usage, it means cloud is used only where nothing sensitive is exposed:

- **DVC remote storage (S3)**: only processed, aggregate, de-identified tables (§18.7) are pushed to a DVC-managed S3 remote for versioned, reproducible access across the team. The raw IQVIA extract is never uploaded anywhere, cloud or otherwise (§3.1), it stays `.gitignore`d and local.
- **CI/CD (GitHub Actions)**: linting and unit tests (§9) run on GitHub-hosted, cloud-based runners on every push. This is genuine cloud compute already in the architecture, simply not previously labeled as such.
- **Dashboard hosting**: the OA/RA comparison dashboard (§6.3) displays only aggregate visit-share and prediction outputs, never raw rows, and is deployable to a small cloud instance (e.g., Streamlit Community Cloud, or a minimal AWS/GCP instance) so the Brand Manager can access it without local infrastructure.

This is a deliberate split, not an oversight: cloud where the artifact is safe to expose, strictly local where it isn't (§5.1, §10).

---

## 18. Precise Definitions & Core-Scope Build Decisions

The objectives, questions, and evaluation approach described above are directionally correct but were not yet precise enough to implement without ambiguity. This section resolves that before any modeling code is written, each decision below is a real design choice with stated reasoning, not a placeholder.

### 18.1 Visit-Share Target Definition (Objective 2)

The classifier's label is computed as:

```
visit_share(branded_injectable, month t) =
    patient_visits(branded_injectable, t) /
    patient_visits(branded_injectable, t) + patient_visits(generic_corticosteroid, t) + patient_visits(NSAID, t)
```

The denominator spans **all three treatment categories** (branded injectable, generic corticosteroid, NSAID) rather than injectables alone. This is deliberate: §6.1 and the Executive Summary already frame the competitive dynamic as including NSAIDs, narrowing the denominator to injectables-only would silently contradict the business framing stated elsewhere in this document.

Concretely, using the full OA product categorization (145 products, data-derived from the reference file, not hand-typed):

- `branded_injectable` = Zilretta only (135,133 visits, the only product in this market with no generic equivalent)
- `generic_corticosteroid` = 52 products including Kenalog and Depo-Medrol (4,914,533 visits combined)
- `NSAID` = 54 products including aspirin, acetaminophen, and ibuprofen variants (398,722 visits combined)

A fourth category present in the data, opioid/other injectable analgesics (37 products, e.g., Ketorolac, Fentanyl, 112,742 visits combined), is **deliberately excluded** from this formula: these are pain-control agents used around procedures, not part of the branded-injectable-vs-generic-corticosteroid competitive story the classifier is built around.

### 18.2 Direction Labeling Threshold ("Flat")

Month-over-month change in `visit_share` is labeled:

- **Up**: change > +1.0 percentage point
- **Down**: change < −1.0 percentage point
- **Flat**: change within ±1.0 percentage point

The ±1.0pp threshold is a stated **default**, not a verified fact. It will be checked against the real distribution of month-over-month share changes during Week 4 EDA and adjusted if needed so the three classes are reasonably balanced (a threshold that leaves "Flat" nearly empty, or nearly all months, would make the classification task degenerate). Any change to this threshold will be logged, not silently altered.

### 18.3 Backtesting / Walk-Forward Validation Scheme

- **Initial training window**: 24 months minimum (two full seasonal cycles) before the first out-of-sample prediction.
- **Window type**: expanding, not rolling — each new month is added to the training set rather than dropping old months, since total history is limited (72 months) and this matches how the real monthly production cycle will actually operate (§17.2).
- **Backtest folds**: walking forward one month at a time across the remaining ~48 months, retraining before each prediction, yielding ~48 paired baseline-vs-model predictions for evaluation.

### 18.4 Statistical Significance Test

**McNemar's test**, applied to the paired binary indicator of "did the baseline correctly identify a Down month" vs. "did the model correctly identify a Down month," across the ~48 backtest folds from §18.3. This is chosen specifically because it matches the proposal's own stated priority metric (recall on the "Down" class, §17.2) and is the correct test for paired predictions on the same test instances, not independent samples. Overall accuracy is reported as a secondary comparison using the same paired approach.

**Honest expectation**: with only ~48 backtest folds for a 3-class problem, and given that monthly visit-share is highly autocorrelated (making the persistence baseline a genuinely strong competitor, not a token comparison), it is entirely possible the trained model does not beat the baseline with statistical significance. That outcome is a legitimate, anticipated finding to report honestly, not a failure of the project design.

### 18.5 FDA Approval-Date Verification (Objective 1)

Before the inflection-point narrative in Objective 1 is finalized, Week 4 work must verify — via openFDA, not assumption — whether the branded injectable's actual FDA approval date falls **within** the Aug 2019–Jul 2025 data window:

- **If within the window**: proceed with the original "launch inflection" framing — we can observe before/after.
- **If it predates the window**: reframe Objective 1's language to "post-launch adoption trend" for that product, since a true launch inflection isn't observable in our data. Any *other* branded competitor product whose approval date does fall inside the window becomes the stronger inflection-point example instead.

This decision will be recorded with its actual finding once verified, not left as an assumption.

### 18.6 Competitor Product Scope for openFDA Retrieval

FDA approval-date retrieval is bounded to **branded (non-generic) products only** — generics and NSAIDs don't have a single meaningful "launch" event driving inflection the way a new branded product does, and most NSAIDs in this market are multi-manufacturer generics with no clean approval-event narrative. The specific list of branded products to query is drawn from what's **actually present in our own data's Brand/Generic tag field**, enumerated during Week 4 EDA, not an externally guessed list. This keeps the scope grounded in what the data can actually support rather than open-ended.

### 18.7 Core-Scope Technical Simplifications

For Objectives 1–2 specifically (Core scope only):

- **Data warehouse (§4.1)**: deferred. A simple tidy long-format table (one row per month × product × specialty × age × gender × visit count) is sufficient for trend analysis and classification. The full star-schema (separate fact/dimension/aggregate tables) is not required to hit Core objectives and is reserved for when the dashboard (§6.3) or Stretch work actually needs it.
- **Knowledge graph (§4.2)**: deferred. Objective 1's FDA-event linkage only needs a simple lookup table (product → approval date), not the full RDFLib/NetworkX graph with typed relationship edges. The full graph is reserved for when Objective 3 (Stretch) needs graph-derived competitive-context features (e.g., "how many competing branded injectables were on market this month").

Both remain fully in-scope for the project overall (§4), just not on the critical path for Core Objectives 1–2, and will be built when the work that actually depends on them begins.

### 18.8 Future Extension: Experimentation Design at Scale

Live A/B testing does not apply to this system as scoped: there is exactly one real-world outcome per month for a single market, and the model itself doesn't causally affect that outcome, it predicts, it doesn't intervene. Backtesting (§18.3) is the statistically appropriate substitute, not a workaround.

If this system were extended to monitor multiple products or therapeutic areas simultaneously, each product's monthly prediction would become an independent unit, enabling a genuine between-product randomized comparison of a challenger model against the current champion (§17.2), a real, causally valid experiment at that scale. This is documented here as a stated future direction. It is **not** built, tested, or claimed as part of the current system, and should never be described as an existing capability.
