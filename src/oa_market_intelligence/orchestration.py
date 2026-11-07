"""The monthly publish as a Dagster job (DL-128).

`publish.py` already runs the pipeline as nine recorded stages with an atomic swap. This module puts
the same stages in an orchestrator: one Dagster op per stage, in the same order, each calling the
very method `publish()` calls (`PublishRun.<stage>`), so there is one implementation of the
pipeline and two ways to drive it. What Dagster adds: the graph and each run's history in a web UI,
a schedule, a sensor that asks for a run when new extracts arrive, run configuration (including the
written reason that overrides the promotion gate), and per-step logs and metadata.

What it does not change: the failure behaviour. A failing stage fails its op, the later ops do not
run, the staged database is removed and the last good database stays; the run record is appended
to `run_log.jsonl` exactly as for `oa-publish` (the resource's teardown writes it). The scheduled
run of record remains the GitHub Actions workflow (free, and it publishes to S3); this is the
orchestrator for running it locally or in a container (`docs/orchestration.md`).

    dagster dev            # the web UI at http://127.0.0.1:3000 (module set in pyproject.toml)
    dagster job execute -m oa_market_intelligence.orchestration -j publish_job

Dagster is an optional install (`requirements-orchestration.txt`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import dagster as dg

from oa_market_intelligence.pipeline import DEFAULT_RAW_DIR, DEFAULT_REFERENCE_DIR
from oa_market_intelligence.publish import (
    DEFAULT_PUBLISHED_DIR,
    STAGE_ORDER,
    PublishRun,
    extracts_fingerprint,
)

CRON = "0 6 10 * *"  # 06:00 UTC on the 10th, as in .github/workflows/monthly_pipeline.yml


def default_factory(config: dict) -> PublishRun:
    """The real pipeline, with the folders and the optional override reason from the run config."""
    return PublishRun(
        raw_dir=Path(config["raw_dir"]),
        reference_dir=Path(config["reference_dir"]),
        published_dir=Path(config["published_dir"]),
        accept_challenger=(config.get("accept_challenger") or "").strip() or None,
    )


def _stage_op(name: str):
    @dg.op(
        name=name,
        required_resource_keys={"publish_run"},
        ins={"start_after": dg.In(dg.Nothing)},
        out=dg.Out(dg.Nothing),
        description=f"The `{name}` stage of the publish run (publish.PublishRun.{name}).",
    )
    def op(context):
        run: PublishRun = context.resources.publish_run
        getattr(
            run, name
        )()  # raises on a failed stage: this op fails and the later ones are skipped
        entry = next(s for s in run.stages if s["stage"] == name)
        context.log.info(f"{name}: {entry['status']} in {entry['seconds']} s")
        yield dg.Output(
            None,
            metadata={
                "status": entry["status"],
                "seconds": entry["seconds"],
                "detail": dg.MetadataValue.json(entry.get("detail") or {}),
            },
        )

    return op


OPS = {name: _stage_op(name) for name in STAGE_ORDER}


def make_publish_job(factory: Callable[[dict], PublishRun] = default_factory) -> dg.JobDefinition:
    """The job for a way of building the run (tests pass one with fake stages)."""

    @dg.resource(
        config_schema={
            "raw_dir": dg.Field(str, default_value=str(DEFAULT_RAW_DIR)),
            "reference_dir": dg.Field(str, default_value=str(DEFAULT_REFERENCE_DIR)),
            "published_dir": dg.Field(str, default_value=str(DEFAULT_PUBLISHED_DIR)),
            "accept_challenger": dg.Field(
                dg.Noneable(str),
                default_value=None,
                description="Publish even if the promotion gate refused the refit: the written "
                "reason, recorded in the run record. Leave empty for a normal run.",
            ),
        },
        description="One publish run: set up before the first stage, the run record written (and "
        "the staging folder removed) after the last step, whatever happened.",
    )
    def publish_run(init_context):
        run = factory(init_context.resource_config)
        run.start()
        try:
            yield run
        finally:
            run.finish()  # the record, ok or failed, exactly as `oa-publish` writes it

    # In one process, on purpose: the stages share one staged database and one run record through
    # the resource, which a process per op (Dagster's default for a launched run) would split.
    @dg.job(
        name="publish_job",
        resource_defs={"publish_run": publish_run},
        executor_def=dg.in_process_executor,
    )
    def publish_job():
        previous = None
        for name in STAGE_ORDER:
            previous = OPS[name](start_after=previous) if previous is not None else OPS[name]()

    return publish_job


publish_job = make_publish_job()

monthly_publish = dg.ScheduleDefinition(
    name="monthly_publish",
    job=publish_job,
    cron_schedule=CRON,
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
    description="The monthly publish, on the 10th at 06:00 UTC (as the GitHub Actions schedule).",
)


@dg.sensor(
    name="new_extract_sensor",
    job=publish_job,
    minimum_interval_seconds=3600,
    default_status=dg.DefaultSensorStatus.STOPPED,
    description="Asks for a publish when the extracts in the raw folder are new or changed.",
)
def new_extract_sensor(context):
    fingerprint = extracts_fingerprint(DEFAULT_RAW_DIR)
    if fingerprint:  # the run key makes one run per distinct set of extracts, never a repeat
        yield dg.RunRequest(run_key=fingerprint)


defs = dg.Definitions(jobs=[publish_job], schedules=[monthly_publish], sensors=[new_extract_sensor])
