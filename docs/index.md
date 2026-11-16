# Documentation index

Every document and notebook in the project, with what each covers. Start with [`architecture.md`](architecture.md) for the whole system on one page.

| Document | Covers |
|---|---|
| [`findings.md`](findings.md) | The 17 findings with their evidence and business impact, the data-quality and engineering outcomes, and the 171 public datasets (the website's Findings tab shows the same table) |
| [`decision_log.md`](decision_log.md) | Every significant decision, why it was made, what was rejected (entries up to DL-128; some describe work kept in the archive tag) |
| [`PROPOSAL.md`](PROPOSAL.md) | The original project proposal: objectives, architecture, definitions and build decisions |
| [`data_dictionary.md`](data_dictionary.md), [`database_schema.md`](database_schema.md), [`silver_gold_data_dictionary.md`](silver_gold_data_dictionary.md), [`data_analysis_reference.md`](data_analysis_reference.md) | Raw field structure, the star schema and DDL, every Silver and Gold column, and the dataset audit |
| [`external_data_protocol.md`](external_data_protocol.md) | The protocol for the public datasets: code sets, specialty crosswalk, pass or fail rules, fixed before any analysis |
| [`external_data_lineage.md`](external_data_lineage.md) | Every column of the external-data database with its source file, original column and rule |
| [`external_data_refresh.md`](external_data_refresh.md) | How to restore the raw public files, rebuild and verify the external database |
| [`external_data_results.md`](external_data_results.md) | Results of the public-data analyses: each pre-registered rule, its verdict, the numbers, the limits |
| [`feature_engineering_plan.md`](feature_engineering_plan.md) | The feature plan: targets, the base set and seven groups, the time rule, tests and the validation protocol, fixed before anything was built or run |
| [`feature_dictionary.md`](feature_dictionary.md) | Every model input: definition, source and leakage rule |
| [`feature_results.md`](feature_results.md) | Results of the feature-group test: each rule, its verdict, the numbers and the limits |
| [`database_schema.md`](database_schema.md) sections 13 and 14 | The two feature tables and the two model-result tables the publish step writes into the warehouse |
| [`pipeline.md`](pipeline.md) | The orchestrated pipeline: its nine stages (including the monthly scoring job, the promotion gate and monitoring), the public-data modes, the run record with versions, the monthly schedule and the CI gates |
| [`site.md`](site.md) | The website: its 11 pages, where each number comes from, how to run it locally and how it is tested (live) |
| [`api.md`](api.md) | The API: read-only data endpoints and live predictions from the saved models, API keys and rate limits, errors, and how it is tested |
| [`loadtest.md`](loadtest.md) | The load test (Locust): budgets, results before and after the answer cache, the live function's warm latency and cold start, what was not measured, and what the rate limit and cost limits rest on |
| [`architecture.md`](architecture.md) | The technical architecture on one page: four diagrams (the whole system, the nine-stage monthly build, one request from visitor to forecast, and a change reaching production), the components and why each was chosen, the design decisions and their trade-offs, what happens when something fails, and what was deliberately not built |
| [`orchestration.md`](orchestration.md) | The monthly publish as a Dagster job: one op per stage over the same code `oa-publish` runs, the run history in a web UI, a schedule and a sensor (both off by default), how to run it locally and in Docker Compose, and what was and was not verified |
| [`promotion_gate.md`](promotion_gate.md) | The promotion gate: how a refit is checked against the models in service before it is published (sanity, reproducibility, stability), where each tolerance comes from, and how a written reason overrides it |
| [`tracking.md`](tracking.md) | Experiment tracking and the model registry in MLflow: every scored method as a run, the models in service as registered models with a champion alias, rebuilt from the published warehouse |
| [`docker.md`](docker.md) | The Docker images (pipeline, website, API, and the API for AWS Lambda), the one-command stack and what CI checks |
| [`runbook.md`](runbook.md) | The monthly run: what a normal month looks like, what to do when each stage fails, going back to the last good database, forecast drift and tracing a number to its data and code |
| [`environment.md`](environment.md) | The supported Python, the pinned packages, installing the project and the Docker image, and the environment behind the stored modelling results |
| [`modelling_plan.md`](modelling_plan.md) | The modelling plan: three tasks, horizons, baselines and models, the evaluation and promotion rules, the leakage timeline and tests, fixed before any model ran (with the T3 amendment and the implementation choices) |
| [`modelling_results.md`](modelling_results.md) | Results of the modelling run, including the post-hoc check of the decline alert and the limits |
| [`model_card_segment_share.md`](model_card_segment_share.md), [`model_card_national_share.md`](model_card_national_share.md), [`model_card_decline_alert.md`](model_card_decline_alert.md) | One card for each model that serves: use, inputs, evaluation, calibration, limits |

**Notebooks** (executed end to end; each opens with the question, the answer in one line, and the method in plain words):

| Notebook | Covers |
|---|---|
| [`01_eda_raw_data`](../notebooks/01_eda_raw_data.ipynb), [`02_eda_cleaned_data`](../notebooks/02_eda_cleaned_data.ipynb), [`03_eda_ra_data`](../notebooks/03_eda_ra_data.ipynb) | Raw structure, the cleaned warehouse, RA |
| [`10_external_raw_data_profile`](../notebooks/10_external_raw_data_profile.ipynb) | Profile of the raw public files (structure only) |
| [`11_external_analysis_results`](../notebooks/11_external_analysis_results.ipynb), [`12_gap_diagnosis`](../notebooks/12_gap_diagnosis.ipynb) | The pre-registered public-data analyses, then the sales-versus-visits gap diagnosis |
| [`13_eda_unified_data`](../notebooks/13_eda_unified_data.ipynb) | Descriptive EDA of the unified data before feature engineering. Runs from committed files only |
| [`14_feature_groups_test`](../notebooks/14_feature_groups_test.ipynb) | The pre-registered test of seven feature groups (DL-75): the stored result read back, effect sizes and the COVID and 2024-dip robustness runs. One group passes (about 0.06% of log-loss); the national forecast does not improve (DL-76). |
| [`15_modelling_results`](../notebooks/15_modelling_results.ipynb) | The modelling run read back: national share, segment share and its upward bias, the decline alert, the post-hoc check, the G5 hypothesis and the batch demonstration (DL-78, DL-79). |

Notebooks 10 to 12 need the local external database and run only on the author's machine; notebook 13 runs from committed files.
