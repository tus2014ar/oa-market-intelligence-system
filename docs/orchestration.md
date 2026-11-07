# Orchestration: the monthly publish as a Dagster job (DL-128)

The monthly publish ([`pipeline.md`](pipeline.md)) is nine recorded stages with an atomic swap at the
end. This page is about running those same nine stages under an orchestrator, Dagster, and what that
adds and does not add.

## One pipeline, two ways to drive it

`publish.py` holds the pipeline as `PublishRun`: one method per stage, in `STAGE_ORDER`. There are
two drivers:

| Driver | How | Used for |
|---|---|---|
| `oa-publish` (`publish()`) | a plain loop over the stages in one process | the GitHub Actions Monthly Pipeline, local runs, the tests |
| the Dagster job `publish_job` | one op per stage, same order, each calling the same method | local or container runs with a graph, history, schedule and sensor |

Because both call the same methods, the stage order, the names in the run log and the failure behaviour
cannot drift apart (a test checks the order against the published run log, and another checks that
an orchestrated run leaves the same record as `oa-publish`).

## What Dagster adds

- the graph of the nine stages and the history of every run in a web UI, with per-step logs and the
  seconds and detail of each stage as step metadata;
- a **schedule** (`monthly_publish`: 06:00 UTC on the 10th, the same moment as the GitHub schedule);
- a **sensor** (`new_extract_sensor`) that asks for a run when the extracts in the raw folder are new
  or changed; the run key is a fingerprint of the files (name, size, modification time), so one set of
  extracts gives one run request and never a repeat;
- run settings in the launchpad: the three folders and the written reason that overrides the
  promotion gate (`accept_challenger`; blank does nothing, as with `--accept-challenger`).

The schedule and the sensor are **off by default**. Nothing runs by itself until someone turns them on
in the UI.

## What it does not change

- **Failure behaviour.** A failing stage fails its op, the later ops do not start, the staged
  database is removed and the last good database stays byte-for-byte. A refusal by the promotion gate
  stops the job before the swap. (Tests: `tests/test_orchestration.py`.)
- **The run record.** The resource's teardown calls `PublishRun.finish()`, which appends one line to
  `run_log.jsonl` exactly as `oa-publish` does. `finish()` writes nothing for a run object in which no
  stage ran and no error was reported (Dagster sets the resource up again after the steps; that empty
  object must not add a "failed" line).
- **The scheduled run of record.** The GitHub Actions Monthly Pipeline stays the scheduled run: it is
  free, it publishes the release to S3 and it triggers the deploy. The Dagster job is the local and
  container orchestrator. Turning on both schedules would run the publish twice a month.

## Running it

Dagster is an optional install (Python 3.12 is the project baseline; Dagster 1.13 also installs on 3.13):

```bash
pip install -r requirements.txt -r requirements-orchestration.txt
```

The web UI (the module is set in `pyproject.toml`, `[tool.dagster]`):

```bash
dagster dev
```

then open http://127.0.0.1:3000, Jobs, `publish_job`, Launchpad, Launch run. From the command line, one
run without the UI:

```bash
dagster job execute -m oa_market_intelligence.orchestration -j publish_job
```

To change a setting, pass a config file with `-c config.yaml`:

```yaml
resources:
  publish_run:
    config:
      published_dir: "some/other/folder"   # defaults to data/published
      accept_challenger: null              # or a written reason, to publish a refused refit
```

In a container (Docker Compose; the data folder and Dagster's run history are volumes):

```bash
docker compose --profile orchestration up --build orchestrator
```

## Why the job is pinned to one process

The stages share one staged database and one run record through the resource. Dagster's default for
a launched run is a process per op, which gave every op a fresh run object, so the second stage could
not find the staged database. The job therefore uses `in_process_executor`, and a test pins it.
`execute_in_process` (what unit tests use) hides this, which is why it was found only by running the
real job from the command line.

## Verified (7 Nov 2026)

- The real pipeline run through `dagster job execute` on a copy of the published folder: 9 stages, all
  ok, 73 s, **one** record added to the run log, the promotion gate decided promote (11 checks, 0
  failed), and the repository's own published files were untouched.
- The same job launched through the web server's GraphQL API (what the UI's Launch button calls): success
  in 72 s, one record, 9 stages, promote.
- The orchestrator image builds (Dagster resolves together with the pinned packages in one install), and
  in the container (`docker run`, port 3000, a copy of `data/` mounted) the web server lists the job,
  the schedule and the sensor with both off, and a run launched through its API succeeded in 115 s with
  one record, 9 stages and a promote decision. The compose file validates; `docker compose up` itself
  was not run (the test used `docker run` on a scratch copy so the repository's published files stayed
  untouched).
- The UI lists the code location, the job and the runs.
- 13 orchestration tests (Dagster) and 12 publish-run tests (no Dagster) pass; the existing publish,
  promotion and end-to-end tests pass after the refactor.

## Not verified, and not claimed

- The schedule and the sensor have never fired (they are off; the tests check their definitions and the
  sensor's requests, not Dagster's daemon).
- No retries or alerts are configured: a failed run is a failed run, visible in the UI and in the run log.
- Dagster runs in dev mode, one process with local SQLite storage. It is not a production Dagster
  deployment (separate web server, daemon and run workers, a database for run history).
- Dagster Cloud, Prefect and Airflow were not used. Dagster was chosen because its graph and run
  history map directly onto the nine stages, and it runs from a pip install with no server.
- The CI job `orchestration` has not yet run on GitHub.
