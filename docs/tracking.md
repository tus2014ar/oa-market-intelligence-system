# Experiment tracking and the model registry (DL-125)

The modelling results already existed as tables: every method at every horizon scored against its baselines (`model_scores`, 222 rows), and the models in service saved as readable numbers (`model_artifacts`, 4 rows). This records them in **MLflow**, the tool most ML teams use for the same job, so they can be browsed, compared and, in the next roadmap step, promoted by a rule.

**Status: built and tested; runs on your machine.** It is not hosted anywhere and nothing on the website, the API or AWS depends on it. MLflow is an optional install.

## What it records

- **Experiments.** One per task: `T1 national share`, `T2 segment share`, `T3 decline alert`, plus `Registry: the models in service`. Every row of `model_scores` is a run (222 in all, including the sensitivity scenarios): its task, horizon, scenario, method and kind as **parameters**; its score, gain over the best baseline with its lower and upper bound, bias, coverage, precision, recall and F1 as **metrics** (a missing value is left out, never recorded as zero); and **tags** for whether it is the model in service (`serving`), whether it was promoted, whether it is the best baseline, the data version and the code commit.
- **Registered models.** The four models in service: `oa-segment-share-1m`, `oa-segment-share-3m`, `oa-segment-share-6m` (the rule "same as last month") and `oa-decline-alert-1m`. Each has one version whose **artifact is the saved numbers** (`model/parameters.json`, exactly the bytes that were hashed), which carries the alias **`champion`**, and whose tags hold the SHA-256 of those numbers, the model's version fingerprint, the library, the rows it was trained on, the data version and its known bias. A tag, `evidence_run_id`, names the run that scored it (the `main` scenario), so the evidence for a model is one click from the model.
- **Integrity.** Before anything is written, each saved model is checked against its recorded SHA-256; one mismatch refuses the whole build. So the registry can never describe a model other than the one the API serves.

## How to use it

Install MLflow beside the project's packages, then build the store from the published warehouse:

```bash
pip install -r requirements-tracking.txt
oa-track build
oa-track summary
mlflow ui --backend-store-uri sqlite:///mlflow_store/mlflow.db
```

`oa-track build` rebuilds the store from `data/published/warehouse.db` into `mlflow_store/` (a SQLite database and an artifact folder; generated, git-ignored, safe to delete). It refuses to replace any folder that is not one of its own stores (they carry a marker file). `oa-track summary` lists each registered model and its champion. `mlflow ui` opens the browser view (usually http://127.0.0.1:5000): **Experiments** for the runs, **Models** for the registry with the `@ champion` alias; open a model version to see the tags and the source run.

## Checked

- 20 tests (`tests/test_tracking.py`): the run and model specifications (12, pandas only, so they run everywhere), and 8 against a real MLflow store (small made-up warehouse, then the published one). They cover: a run per score row in its task's experiment; missing values left out; the score also under its own metric name; the champion alias, tags and checksum equal to the saved numbers; the artifact equal to the hashed bytes and the evidence run linked; a tampered model stops the build before anything is created; a folder that is not a store is never overwritten; running the command again replaces the store (tested through the real command, in a fresh process).
- On the published warehouse the store holds 222 runs in 4 experiments and 4 registered models, and each champion's checksum equals the one in `model_artifacts` (for example `oa-segment-share-6m`, `44136fa355b3…`, is the same fingerprint the live API reported).
- The MLflow UI was started on the real store and showed the four models with `@ champion` and, for `oa-segment-share-1m`, the tags above.
- CI job `tracking` installs MLflow, runs these tests and builds the real store.

## Limits, said plainly

- **A local registry is not a shared one.** It lives on one machine and is rebuilt from the warehouse, so it records results that already exist. It does not train anything, does not change what is served and is not read by the API or the site. A team registry would be a hosted MLflow server; none is planned (it costs money, and the AWS free plan ends on 6 May 2027).
- **One build per process.** MLflow remembers, per process, that it has created the tables of a database path, so the module refuses a second build of the same store in the same process (the command always runs in a fresh one).
- **`_release` reaches into MLflow's client.** After a build it closes the database connections (Windows keeps an open SQLite file locked). It is best effort and may need updating if MLflow changes.
- **Refits are not bit-for-bit reproducible across machines.** The warehouse rebuilt by the monthly job on GitHub's machine holds saved numbers whose checksums differ from the ones the earlier build on another machine produced (the segment model at 1 month: `1e6fb99c8fa5…` then `5dd0ce05b65c…`), although the data, the model version fingerprint and the settings are the same. I have not investigated why. The consequence for the promotion gate (roadmap step 5) is that a new model must be compared with the champion by how it performs, never by its checksum.
