# The orchestrated pipeline (DL-81)

One command runs every stage in order and records each one (the monthly routine and what to do when a stage fails are in [`runbook.md`](runbook.md)):

```bash
oa-publish                          # the default: check the committed public-data subset
oa-publish --public-data full       # local only: reload and re-export the public data first
```

(`PYTHONPATH=src python -m oa_market_intelligence.publish` is the same thing without the install.)

## The stages

| # | Stage | What it does | If it fails |
|---|---|---|---|
| 1 | `public_data` | `committed` mode checks the committed subset (`data/published/external_subset.db`): all 23 published tables are present, each row count equals the file's own record, each table's content hash equals `data/reference/external_subset_fingerprint.json`, and every file in the download manifest has a SHA-256. `full` mode reloads the 24 GB of raw files with the reconciliation checks, exports the subset and reports whether the fingerprint changed | the run stops before anything is built |
| 2 | `warehouse` | ingest, validate, build Silver and Gold, quality checks and the as-of layer, into a staging file | the run stops |
| 3 | `coverage_check` | the staged database must not have fewer months than the published one | the run stops |
| 4 | `feature_tables` | writes `feat_segment_month` and `feat_monthly` | the run stops |
| 5 | `model_tables` | writes `model_scores` and `backtest_batch` from the stored results | the run stops |
| 6 | `scoring` | the monthly scoring job: the adopted models (`data/reference/serving_models.json`, DL-79) are refitted on all the data and the next 1, 3 and 6 months are written to `pred_batch`; the map is checked against the stored `model_scores` first | the run stops |
| 7 | `monitoring` | data freshness (is the latest month more than 100 days old?) and forecast drift (the previous run's predictions against the months that have arrived since, against the tested bias and error); reported, never stops the run | reports `status: error` and continues |
| 8 | `swap` | replaces the published database with an atomic rename | the old database stays |

Everything from stage 2 on is built in a staging file; the published `data/published/warehouse.db` is replaced only by stage 8. A failure in any stage leaves it exactly as it was and removes the staging area.

## The run record

Each run appends one line to `data/published/run_log.jsonl`, with a `stages` list in the order they ran: `stage`, `status` (`ok`, `failed` or `skipped`), `seconds` and a `detail` (or the `error`). A failed run's line ends at the stage that failed, so the log says where it stopped; the job summary in GitHub Actions shows the same table and names the failed stage. The record also keeps the row counts of the feature, model-result and scoring tables, the public-data summary, the SHA-256 of every input extract, the data-quality result, the `monitoring` report and the `versions` of the run: `data` (a short hash of the extracts' SHA-256), `code` (the git commit) and `environment` (a hash of `requirements.txt`). Every row of `pred_batch` carries the data version and its own model version.

## The public-data modes

- **`committed`** is the default and what CI, the container and the monthly workflow use, because the raw public files (about 24 GB) exist only on the author's machine.
- **`full`** replaces the committed subset file, so a refreshed public dataset appears as a diff to review. It does not fail when the fingerprint changed (that is what a refresh is); accept the new one with `python -m oa_market_intelligence.public_data --update-fingerprint`, and the `committed` check then passes against it.

## Scheduling

`.github/workflows/monthly_pipeline.yml` runs `oa-publish --public-data committed` on the 10th of each month (and on demand), commits the published database and run log, and writes the run's stage table to the job summary.

## The gates (DL-83)

What stops a change, or a monthly run, from putting something wrong in the repository:

| Gate | Where | What it checks |
|---|---|---|
| Lint and the fast tests | CI job `lint-and-test` (`pytest -m "not e2e"`) | `ruff`, the unit tests, the golden regression of the warehouse, the stored-results reproduction (`test_modelling_reproduction.py`) and the published-warehouse consistency (`test_published_warehouse.py`) |
| End to end | CI job `end-to-end` (`pytest -m e2e`, `test_end_to_end.py`) | the real extracts through all eight stages of `oa-publish`, twice: every stage ok and in order, the Gold tables equal the golden snapshot, every downstream table present, the predictions agree with the data, and both runs give exactly the same content in every table |
| Container | CI job `docker-build` | the image builds and its commands run |
| Published database | the monthly workflow, after `oa-publish` and before the commit (`test_published_warehouse.py`) | every expected table is present and not empty, the last logged run passed every stage, the result tables equal the stored results, the predictions are for the month after the last month of data, carry the run's data version and follow the adoption map |

`test_modelling_reproduction.py` is how the two-hour modelling run stays honest without being repeated: it recomputes the counts and every baseline score from the committed warehouse (pure arithmetic, equal to the stored value to the last digit) and two trained national models (loose tolerance), and names the table or score that moved if a change to the harness, the features or the data altered a stored number. The full run (`oa-model --run`) is repeated on demand.

