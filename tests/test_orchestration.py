"""Tests for the Dagster job (src/.../orchestration.py, DL-128).

Dagster is an optional install, so these skip where it is absent (the main CI job); the
`orchestration` job installs it. The stages are fakes (as in the publish tests), so what is tested
is the orchestration: the graph, the order, what a failure does, the run configuration (including
the override reason), the resource's record-keeping, the schedule and the sensor.
"""

import json
from pathlib import Path

import pytest

dg = pytest.importorskip("dagster")

from oa_market_intelligence import orchestration as orch  # noqa: E402
from oa_market_intelligence.promotion import PromotionHeld  # noqa: E402
from oa_market_intelligence.publish import STAGE_ORDER, PublishRun, publish  # noqa: E402
from test_publish import MONTHS_2, _boom, _fake_pipeline  # noqa: E402


def make_job(tmp_path, **fns):
    """The job over a run with fake stages and a published folder from the run config."""

    def factory(config):
        base = {
            "raw_dir": tmp_path / "raw", "reference_dir": tmp_path / "ref",
            "run_pipeline_fn": _fake_pipeline(MONTHS_2), "public_data_fn": lambda: {},
            "features_fn": lambda engine: {}, "model_tables_fn": lambda engine: {},
            "scoring_fn": None, "monitoring_fn": None,
        }  # fmt: skip
        return PublishRun(
            published_dir=Path(config["published_dir"]),
            accept_challenger=(config.get("accept_challenger") or "").strip() or None,
            **{**base, **fns},
        )

    return orch.make_publish_job(factory)


def config(tmp_path, **extra):
    return {
        "resources": {
            "publish_run": {"config": {"published_dir": str(tmp_path / "published"), **extra}}
        }
    }


def run(tmp_path, job=None, **extra):
    return (job or make_job(tmp_path)).execute_in_process(
        run_config=config(tmp_path, **extra), raise_on_error=False
    )


def log(tmp_path):
    path = tmp_path / "published" / "run_log.jsonl"
    return [json.loads(line) for line in path.read_text().strip().splitlines()]


def steps_started(result):
    return [e.step_key for e in result.all_events if e.is_step_start]


# ------------------------------------------------------------------ the graph


def test_the_job_is_one_op_per_stage_in_the_publish_order_each_after_the_one_before():
    job = orch.publish_job
    names = [node.name for node in job.nodes_in_topological_order]
    assert names == list(STAGE_ORDER) and job.name == "publish_job"
    graph = job.graph
    for before, after in zip(STAGE_ORDER, STAGE_ORDER[1:], strict=False):
        deps = graph.dependency_structure.input_to_upstream_outputs_for_node(after)
        assert [out.node_name for outs in deps.values() for out in outs] == [before]
    assert not graph.dependency_structure.input_to_upstream_outputs_for_node(STAGE_ORDER[0])


def test_the_job_runs_in_one_process_because_the_stages_share_one_staged_database():
    """Found by the first real run: with a process per op (Dagster's default for a launched run)
    every op got a fresh run object and the second stage could not find the staged database.
    `execute_in_process` hides it, so the executor is pinned here."""
    assert orch.publish_job.executor_def.name == "in_process"


def test_the_definitions_hold_the_job_the_schedule_and_the_sensor():
    defs = orch.defs
    assert defs.get_job_def("publish_job").name == "publish_job"
    assert [s.name for s in defs.schedules] == ["monthly_publish"]
    assert [s.name for s in defs.sensors] == ["new_extract_sensor"]


# ------------------------------------------------------------------ a good run


def test_a_good_run_runs_every_stage_in_order_publishes_and_writes_the_record(tmp_path):
    result = run(tmp_path)
    assert result.success
    assert steps_started(result) == list(STAGE_ORDER)
    (record,) = log(tmp_path)
    assert record["status"] == "ok" and record["error"] is None
    assert [s["stage"] for s in record["stages"]] == list(STAGE_ORDER)
    assert (tmp_path / "published" / "warehouse.db").exists()
    assert not list((tmp_path / "published").glob(".staging-*"))


def test_the_orchestrated_run_leaves_the_same_record_as_oa_publish(tmp_path):
    result = run(tmp_path / "dagster")
    assert result.success
    direct = publish(
        raw_dir=tmp_path / "raw", reference_dir=tmp_path / "ref",
        published_dir=tmp_path / "direct" / "published", run_pipeline_fn=_fake_pipeline(MONTHS_2),
        public_data_fn=lambda: {}, features_fn=lambda e: {}, model_tables_fn=lambda e: {},
        scoring_fn=None, monitoring_fn=None,
    )  # fmt: skip
    (via_dagster,) = log(tmp_path / "dagster")
    assert set(via_dagster) == set(direct)
    assert [(s["stage"], s["status"]) for s in via_dagster["stages"]] == [
        (s["stage"], s["status"]) for s in direct["stages"]
    ]
    for key in ("status", "n_months", "last_month_id", "error"):
        assert via_dagster[key] == direct[key]


# ------------------------------------------------------------------ a failing run


def test_a_failing_stage_fails_the_job_skips_the_rest_and_keeps_the_last_good_database(tmp_path):
    assert run(tmp_path).success
    database = tmp_path / "published" / "warehouse.db"
    before = database.read_bytes()
    result = run(tmp_path, make_job(tmp_path, features_fn=_boom))
    assert not result.success
    assert [e.step_key for e in result.get_step_failure_events()] == ["feature_tables"]
    started = steps_started(result)
    assert (
        started[-1] == "feature_tables" and "model_tables" not in started and "swap" not in started
    )
    assert database.read_bytes() == before
    failed = log(tmp_path)[-1]
    assert failed["status"] == "failed" and failed["error"] == "RuntimeError: stage broke"
    assert [s["stage"] for s in failed["stages"]][-1] == "feature_tables"
    assert not list((tmp_path / "published").glob(".staging-*"))


def test_the_first_stage_failing_still_leaves_a_record_and_no_staging(tmp_path):
    result = run(tmp_path, make_job(tmp_path, public_data_fn=_boom))
    assert not result.success and steps_started(result) == ["public_data"]
    (record,) = log(tmp_path)
    assert record["status"] == "failed" and [s["stage"] for s in record["stages"]] == [
        "public_data"
    ]
    assert not (tmp_path / "published" / "warehouse.db").exists()


# ------------------------------------------------ the promotion gate through Dagster


def test_a_refusal_by_the_promotion_gate_stops_the_job_before_the_swap(tmp_path):
    def refuse(engine, previous, accept):
        raise PromotionHeld("the refitted models were not promoted: stability:x")

    job = make_job(tmp_path, scoring_fn=lambda e: {"n": 1}, promotion_fn=refuse)
    assert run(tmp_path, job).success is False
    record = log(tmp_path)[-1]
    assert record["status"] == "failed" and "were not promoted" in record["error"]
    assert "swap" not in [s["stage"] for s in record["stages"]]
    assert not (tmp_path / "published" / "warehouse.db").exists()


def test_the_override_reason_is_a_run_setting_and_reaches_the_gate_and_blank_does_nothing(tmp_path):
    seen = []

    def gate(engine, previous, accept):
        seen.append(accept)
        if not accept:
            raise PromotionHeld("refused")
        return {"decision": "override", "override_reason": accept}

    job = make_job(tmp_path, scoring_fn=lambda e: {}, promotion_fn=gate)
    assert not run(tmp_path, job).success
    assert not run(tmp_path, job, accept_challenger="   ").success  # blank: still refused
    ok = run(tmp_path, job, accept_challenger="  reviewed: library upgrade  ")
    assert ok.success and seen == [None, None, "reviewed: library upgrade"]
    assert log(tmp_path)[-1]["promotion"]["override_reason"] == "reviewed: library upgrade"


# ------------------------------------------------------------------ the run settings


def test_the_run_settings_have_the_real_folders_as_defaults_and_an_optional_reason():
    resource = orch.publish_job.resource_defs["publish_run"]
    fields = resource.config_schema.config_type.fields
    assert set(fields) == {"raw_dir", "reference_dir", "published_dir", "accept_challenger"}
    assert fields["published_dir"].default_value == str(orch.DEFAULT_PUBLISHED_DIR)
    assert fields["accept_challenger"].default_value is None


def test_the_default_factory_builds_the_real_pipeline_with_the_settings_it_is_given():
    run_ = orch.default_factory(
        {"raw_dir": "r", "reference_dir": "f", "published_dir": "p", "accept_challenger": " why "}
    )
    assert run_.published_dir == Path("p") and run_.accept_challenger == "why"
    assert run_.run_pipeline_fn.__name__ == "run_pipeline"
    none = orch.default_factory(
        {"raw_dir": "r", "reference_dir": "f", "published_dir": "p", "accept_challenger": None}
    )
    assert none.accept_challenger is None


# ------------------------------------------------------------------ the schedule and the sensor


def test_the_schedule_is_the_monthly_one_utc_and_off_until_someone_turns_it_on():
    schedule = orch.monthly_publish
    assert schedule.cron_schedule == orch.CRON == "0 6 10 * *"
    assert schedule.execution_timezone == "UTC"
    assert schedule.default_status == dg.DefaultScheduleStatus.STOPPED
    assert schedule.job_name == "publish_job"
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/monthly_pipeline.yml"
    ).read_text()
    assert f'cron: "{orch.CRON}"' in workflow  # the same moment as the GitHub schedule


def test_the_sensor_asks_for_one_run_per_distinct_set_of_extracts(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "DEFAULT_RAW_DIR", tmp_path)
    assert orch.new_extract_sensor.default_status == dg.DefaultSensorStatus.STOPPED

    def requests():
        return list(orch.new_extract_sensor(dg.build_sensor_context()))

    assert requests() == []  # no extracts, no run
    (tmp_path / "a.xlsx").write_bytes(b"one")
    (first,) = requests()
    assert first.run_key and requests()[0].run_key == first.run_key  # the same extracts: same key
    (tmp_path / "a.xlsx").write_bytes(b"one, changed")
    (second,) = requests()
    assert second.run_key != first.run_key  # a changed extract is a new run
