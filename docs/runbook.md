# Runbook: the monthly run (DL-84)

For whoever looks after the monthly publish. The run is `oa-publish` (scheduled by `.github/workflows/monthly_pipeline.yml` on the 10th of each month, or started by hand from the Actions tab). What it does stage by stage is in [`pipeline.md`](pipeline.md).

## A normal month
1. The workflow rebuilds the warehouse, writes the feature, result and prediction tables, checks the published database (`tests/test_published_warehouse.py`) and commits `data/published/` if anything changed.
2. Open the run's **job summary**. It shows the stage table, the data-quality result, the monitoring line, the versions and the SHA-256 of each input extract.
3. Read the monitoring line:
   - **data freshness: fresh** is normal. **stale** means the latest month of data is more than 100 days old: no newer extract has arrived. The publish still succeeds; chase the extract.
   - **forecast drift: ok** or **nothing_to_compare** is normal. **nothing_to_compare** means no new month has arrived since the previous predictions, so there is nothing to check yet. **drift** means that once a month arrived, the earlier forecasts missed by more than the tolerance (a segment bias 5 points away from the tested bias, or a national error more than twice the tested error): see "Forecast drift" below.
4. `monitoring: attention` is a prompt to look, not a failure.

## When a run fails
The published database is not touched by a failed run. The run's line in `data/published/run_log.jsonl` (and the job summary) names the **stage that failed** and its error.

| Failed stage | Usual cause | What to do |
|---|---|---|
| `public_data` | the committed public-data subset, its fingerprint or the download manifest changed or is damaged | `python -m oa_market_intelligence.public_data --verify` names the table. If the change was intended (a refreshed public dataset), run `oa-publish --public-data full` locally, review the diff of `data/published/external_subset.db`, then `python -m oa_market_intelligence.public_data --update-fingerprint` and commit both. Otherwise restore the files with `git checkout` |
| `warehouse` | a new extract failed validation, a data-quality check raised an error (a gap in the months, a missing disease area), or the build broke | read the error in the log; the quality checks name the extract and the rule. Do not loosen a check to get a run through: fix or replace the extract |
| `coverage_check` | the new warehouse has fewer months than the published one | an extract is missing or truncated: restore it |
| `feature_tables` / `model_tables` | a code change, or `modelling_results.json` is missing or empty | run the tests; `oa-model --counts-only` shows what the run expects |
| `scoring` | the adoption map names a model the stored results do not support, or a model could not be fitted | `data/reference/serving_models.json` is validated against `model_scores`; the error names the task and horizon |
| `monitoring` | rare: monitoring reports its own errors without stopping | look at the error in the record |
| `swap` | a file-system problem | re-run |

After fixing the cause, start the workflow again (Actions, Monthly Pipeline, Run workflow) or run `oa-publish` locally.

## Going back to the last good database
Every publish is a commit. `git revert <commit>` of the "Publish warehouse" commit restores the previous `data/published/warehouse.db` and `run_log.jsonl`.

## Forecast drift
It is reported, not acted on automatically. Look at the `drift` block of the run record: which horizon, the realised bias or error and the tested one. If a bias has moved by more than the tolerance for a few months, re-run the modelling test (`oa-model --run`, about two hours, `--n-jobs 6`), read the results, and decide as in DL-79 whether the adopted models still stand. Changing which model serves means editing `data/reference/serving_models.json`, and the file is only accepted if the stored results support it.

## Versions: tracing a number
Every run records `versions` in the run log: `data` (a short hash of the input extracts' SHA-256), `code` (the git commit) and `environment` (a hash of `requirements.txt`). Every row of `pred_batch` carries the same `data_version` and its own `model_version`. To find what produced a forecast, match its `data_version` with the run's record and the extract hashes listed there.

## Commands
```bash
oa-publish                      # the whole run
oa-publish --public-data full   # local only: reload and re-export the public data first
oa-model --counts-only          # what the modelling run will score (fits nothing)
oa-model --run --n-jobs 6       # the full modelling run (about two hours)
pytest tests/test_published_warehouse.py   # check the published database
```

## What this run does not do
It does not retrain the promotion test, deploy anything, or fetch new IQVIA data: a "new month" means new extracts committed to `data/raw/`. While there is one static extract, the monitoring line will say the data are stale and there is nothing to compare, and that is correct.
