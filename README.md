# OA & RA Market Intelligence System

A monthly-refreshable, production-oriented classification system that tracks and predicts visit-share direction between branded specialty injectables and generic pain therapies in Osteoarthritis (OA) and Rheumatoid Arthritis (RA), built on real IQVIA National Medical and Treatment Audit (NMTA) patient-visit data.

Built as a capstone project for **DAAN 888 — Design and Implementation of Analytics System**, Penn State University, School of Graduate Professional Studies (Fall 2026).

## The problem

A newer, extended-release branded injectable (e.g., Zilretta) is trying to take visit share from decades-old generic corticosteroids and NSAIDs in a high-volume OA market (7.19M+ visits across 6 years in our extract). The system is built around a single stakeholder — an **OA/RA Injectable Brand Manager** — who needs an early signal on competitive share movement before it shows up in a standard quarterly business review.

## What this system does

- **Classifies** next month's visit-share direction (Up / Down / Flat) for the branded injectable, evaluated against a persistence baseline
- **Segments** provider specialties/demographics by adoption level (High vs. Low)
- **Monitors** its own predictions against actual results monthly, and flags meaningful misses
- Runs on a **right-sized MLOps pipeline**: DVC for data versioning, MLflow + Optuna for experimentation, SHAP for explainability, Docker for packaging, Evidently AI for drift monitoring — deliberately *without* Kubernetes, Kafka, or live A/B testing, since this is a monthly batch system, not a real-time service

See [`docs/PROPOSAL.md`](docs/PROPOSAL.md) for the full project proposal, including the data warehouse, knowledge graph, and local LLM components.

## Data

Primary data is a real, licensed IQVIA NMTA patient-visit extract — **not included in this repository** (see `.gitignore`). Anyone cloning this repo can review the full pipeline code and methodology, but will need their own data (or the synthetic stand-in dataset, once published) to run it end-to-end.

## Status

🚧 Early stage — proposal finalized, pipeline implementation in progress. See [`docs/PROPOSAL.md`](docs/PROPOSAL.md) §8 for the week-by-week roadmap.

## Tech stack

`pandas` · `scikit-learn` · `MLflow` · `Optuna` · `SHAP` · `Docker` · `Evidently AI` · `DVC` · `RDFLib`/`NetworkX` (knowledge graph) · `Ollama` (local LLM, LLaMA 3 / Mistral)

## Authors

Tushar, Saakshaat Saini
