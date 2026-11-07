"""Experiment tracking and a model registry with MLflow (DL-125).

The modelling results already exist: every method at every horizon was scored against its baselines
by a pre-registered run, and the monthly job saved the models in service. This module records them
the way ML teams do, so they can be browsed, compared and promoted with the standard tool:

- **Experiments.** One MLflow experiment per task (national share, segment share, decline alert).
  Every row of the published `model_scores` table is a run: its settings (task, horizon, scenario,
  method, kind) as parameters, its scores, gain over the best baseline with its interval, bias and
  coverage as metrics, and tags for whether it is in service, the data version and the code commit.
- **Registry.** Each model in service (`model_artifacts`: the segment model at 1, 3 and 6 months and
  the 1-month decline alert) is a registered model with a version whose artifact is the saved
  numbers, whose tags carry their SHA-256, the library, the rows it was trained on and its known
  bias, and which carries the alias `champion`. The version also names the run that scored it, so
  the evidence for a model is one click from the model.

Everything is rebuilt from the published warehouse by `oa-track build`, in a local SQLite-backed
store that is generated and not committed. Nothing is trained and nothing that is served changes.
A saved model is checked against its recorded SHA-256 before it is registered, so a registry entry
cannot differ from what the API serves.

The run and model specifications are plain data built with pandas only (tested without MLflow);
only the functions that write or read the store import MLflow, which is an optional install
(`requirements-tracking.txt`).

    oa-track build                    # rebuild the store from data/published/warehouse.db
    oa-track summary                  # what is registered and which version is champion
    mlflow ui --backend-store-uri sqlite:///mlflow_store/mlflow.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "published" / "warehouse.db"
DEFAULT_STORE = REPO_ROOT / "mlflow_store"
MARKER = ".oa-tracking-store"  # a store is only ever replaced if it carries this file
CHAMPION = "champion"
REGISTRY_EXPERIMENT = "Registry: the models in service"

EXPERIMENTS = {
    "T1": "T1 national share",
    "T2": "T2 segment share",
    "T3": "T3 decline alert",
}
SERVED_TASK = {"segment": "segment-share", "alert": "decline-alert"}
EVIDENCE_TASK = {"segment": "T2", "alert": "T3"}  # which scored task backs each served model
METRIC_COLUMNS = (
    "score",
    "gain_over_best_baseline",
    "gain_lower",
    "gain_upper",
    "bias_vs_actual",
    "coverage_90",
    "precision",
    "recall",
    "f1",
    "months_better",
)
PARAM_COLUMNS = ("task", "horizon", "scenario", "method", "kind", "metric", "higher_is_better")


_built_here: set[str] = set()  # stores built by this process (see build_store)


class TrackingError(ValueError):
    """Something the registry must not record. The message is safe to print."""


@dataclass(frozen=True)
class RunSpec:
    """One scored method, as the experiment run it becomes."""

    experiment: str
    name: str
    params: dict[str, str]
    metrics: dict[str, float]
    tags: dict[str, str]


@dataclass(frozen=True)
class ModelSpec:
    """One model in service, as the registered model version it becomes."""

    model_id: str
    name: str
    task: str
    horizon: int
    parameters: str
    tags: dict[str, str]
    description: str


def _number(value) -> float | None:
    """A finite float, or None for a missing or non-finite value."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _flag(value) -> str:
    number = _number(value)
    return "unknown" if number is None else str(int(number))


def run_specs(scores: pd.DataFrame, *, data_version: str, code_version: str) -> list[RunSpec]:
    """One run per row of `model_scores`, in the table's order."""
    specs = []
    for row in scores.to_dict("records"):
        task = str(row["task"])
        if task not in EXPERIMENTS:
            raise TrackingError(f"a score for an unknown task: {task!r}")
        params = {k: str(row[k]) for k in PARAM_COLUMNS if row.get(k) is not None}
        for extra in ("n_test_months", "n_alerts"):
            if _number(row.get(extra)) is not None:
                params[extra] = str(int(_number(row[extra])))
        metrics = {}
        for column in METRIC_COLUMNS:
            value = _number(row.get(column))
            if value is not None:
                metrics[column] = value
        metric_name = str(row.get("metric", ""))
        if metric_name and "score" in metrics:
            metrics[metric_name] = metrics["score"]  # the score under its own name too
        tags = {
            "task": task,
            "scenario": str(row["scenario"]),
            "kind": str(row["kind"]),
            "serving": _flag(row.get("serving")),
            "promoted": _flag(row.get("promoted")),
            "best_baseline": _flag(row.get("is_best_baseline")),
            "data_version": data_version,
            "code_version": code_version,
            "source": "model_scores in the published warehouse",
        }
        name = f"{task} h{int(row['horizon'])} {row['scenario']} {row['method']}"
        specs.append(RunSpec(EXPERIMENTS[task], name, params, metrics, tags))
    return specs


def model_name(task: str, horizon: int) -> str:
    """The registered model's name, for example `oa-segment-share-3m`."""
    if task not in SERVED_TASK:
        raise TrackingError(f"a saved model for an unknown task: {task!r}")
    return f"oa-{SERVED_TASK[task]}-{int(horizon)}m"


def model_specs(artifacts: pd.DataFrame) -> list[ModelSpec]:
    """One spec per saved model. The saved numbers must match their recorded SHA-256, or nothing
    is registered: the registry may never describe a model other than the one the API serves."""
    specs = []
    for row in artifacts.to_dict("records"):
        text = str(row["parameters"])
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != row["artifact_sha256"]:
            raise TrackingError(
                f"the saved numbers of {row['model_id']} do not match their recorded SHA-256"
            )
        task, horizon = str(row["task"]), int(row["horizon"])
        bias = _number(row.get("known_bias"))
        tags = {
            "model_id": str(row["model_id"]),
            "model": str(row["model"]),
            "model_version": str(row["model_version"]),
            "artifact_sha256": digest,
            "library": str(row["library"]),
            "trained_on_rows": str(int(row["trained_on_rows"])),
            "run_month_id": str(int(row["run_month_id"])),
            "data_version": str(row["data_version"]),
            "known_bias_pct": "none recorded" if bias is None else f"{100 * bias:.2f}",
        }
        what = "the saved logistic regression" if row["model"] == "logistic" else "a rule"
        description = (
            f"The {task} model in service at {horizon} month(s): {what} ({row['model']}). Its "
            "saved numbers are the artifact; the API serves exactly these (their SHA-256 is a tag)."
        )
        specs.append(
            ModelSpec(
                str(row["model_id"]),
                model_name(task, horizon),
                task,
                horizon,
                text,
                tags,
                description,
            )
        )
    return specs


# ---------------------------------------------------------------------------- the store


def _import_mlflow():
    try:
        import mlflow  # noqa: F401
        from mlflow import MlflowClient
        from mlflow.entities import Metric, Param
    except ImportError:
        raise TrackingError(
            "MLflow is not installed: pip install -r requirements-tracking.txt"
        ) from None
    return MlflowClient, Metric, Param


def _release(client) -> None:
    """Close the store's database connections, so the folder can be replaced or removed (Windows
    keeps an open SQLite file locked). Best effort: it reaches into MLflow's client, which may
    change between versions, and a failure here only means the files stay locked a little longer."""
    try:
        stores = (client._tracking_client.store, client._get_registry_client().store)
        for store in {id(s): s for s in stores}.values():
            store.engine.dispose()
    except Exception:  # noqa: BLE001
        pass


def _tracking_uri(store: Path) -> str:
    return f"sqlite:///{(store / 'mlflow.db').as_posix()}"


def _read(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        scores = pd.read_sql("SELECT * FROM model_scores", con)
        artifacts = pd.read_sql("SELECT * FROM model_artifacts", con)
        versions = [r[0] for r in con.execute("SELECT DISTINCT data_version FROM pred_batch")]
    except (sqlite3.Error, pd.errors.DatabaseError):
        raise TrackingError(
            "the warehouse lacks model_scores, model_artifacts or pred_batch"
        ) from None
    finally:
        con.close()
    if len(versions) != 1:
        raise TrackingError("the forecasts do not carry exactly one data version")
    log = Path(db_path).parent / "run_log.jsonl"
    code = "unknown"
    if log.exists():
        lines = [ln for ln in log.read_text("utf-8").splitlines() if ln.strip()]
        if lines:
            code = str(json.loads(lines[-1]).get("versions", {}).get("code", "unknown"))
    return scores, artifacts, str(versions[0]), code


def _prepare_store(store: Path) -> None:
    """An empty store directory. An existing one is replaced only if it is ours (it carries the
    marker file), so a mistaken path can never delete anything else."""
    if store.exists():
        if not (store / MARKER).exists():
            raise TrackingError("that folder exists and is not a tracking store: not touching it")
        shutil.rmtree(store)
    store.mkdir(parents=True)
    (store / MARKER).write_text("generated by oa-track; safe to delete\n", encoding="utf-8")


def build_store(db_path: Path | str = DEFAULT_DB, store: Path | str = DEFAULT_STORE) -> dict:
    """Rebuild the tracking store from a published warehouse and return what it holds."""
    MlflowClient, Metric, Param = _import_mlflow()
    store = Path(store)
    # MLflow remembers, per process, that it has created the tables of a database path, so replacing
    # a store's files inside the same process leaves it believing tables exist that do not. The
    # command always runs in a fresh process; a second build in this one is refused, not attempted.
    key = str(store.resolve())
    if key in _built_here:
        raise TrackingError("this store was already built in this process: start a new process")
    scores, artifacts, data_version, code_version = _read(Path(db_path))
    runs = run_specs(scores, data_version=data_version, code_version=code_version)
    models = model_specs(artifacts)  # checks every SHA-256 before anything is written
    _prepare_store(store)
    _built_here.add(key)
    uri = _tracking_uri(store)
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    try:
        return _populate(client, store, runs, models, data_version, code_version, Metric, Param)
    finally:
        _release(client)


def _populate(client, store, runs, models, data_version, code_version, Metric, Param) -> dict:
    roots = store / "artifacts"

    def experiment(name: str) -> str:
        slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
        return client.create_experiment(name, artifact_location=(roots / slug).as_uri())

    ids = {name: experiment(name) for name in [*EXPERIMENTS.values(), REGISTRY_EXPERIMENT]}
    evidence: dict[tuple[str, int, str, str], str] = {}
    for spec in runs:
        run = client.create_run(
            ids[spec.experiment],
            run_name=spec.name,
            tags={k: v for k, v in spec.tags.items()},
        )
        stamp = run.info.start_time
        client.log_batch(
            run.info.run_id,
            metrics=[Metric(k, v, stamp, 0) for k, v in spec.metrics.items()],
            params=[Param(k, v) for k, v in spec.params.items()],
        )
        client.set_terminated(run.info.run_id)
        key = (
            spec.params["task"],
            int(spec.params["horizon"]),
            spec.params["method"],
            spec.params["scenario"],
        )
        evidence[key] = run.info.run_id

    for model in models:
        _register(client, ids[REGISTRY_EXPERIMENT], model, evidence, data_version, code_version)
    return {
        "experiments": len(ids),
        "runs": len(runs),
        "models": [m.name for m in models],
        "data_version": data_version,
        "code_version": code_version,
        "store": str(store),
    }


def _register(
    client, experiment_id, model: ModelSpec, evidence, data_version, code_version
) -> None:
    run = client.create_run(
        experiment_id,
        run_name=f"saved numbers of {model.model_id}",
        tags={
            "model_id": model.model_id,
            "data_version": data_version,
            "code_version": code_version,
        },
    )
    run_id = run.info.run_id
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "parameters.json"
        path.write_text(model.parameters, encoding="utf-8")  # exactly the bytes that were hashed
        client.log_artifact(run_id, str(path), "model")
    client.set_terminated(run_id)
    client.create_registered_model(model.name, description=model.description)
    task_key = EVIDENCE_TASK[model.task]
    method = model.tags["model"]
    evidence_run = evidence.get((task_key, model.horizon, method, "main"))
    tags = dict(model.tags)
    if evidence_run:
        tags["evidence_run_id"] = evidence_run  # the run that scored this model
    version = client.create_model_version(
        model.name,
        source=f"{run.info.artifact_uri}/model",
        run_id=run_id,
        tags=tags,
    )
    client.set_registered_model_alias(model.name, CHAMPION, version.version)


def summary(store: Path | str = DEFAULT_STORE) -> list[dict]:
    """The registered models, each with its champion version's tags."""
    store = Path(store)
    if not (store / MARKER).exists():
        raise TrackingError("there is no tracking store here: run oa-track build first")
    MlflowClient, *_ = _import_mlflow()
    uri = _tracking_uri(store)
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    out = []
    try:
        for registered in sorted(client.search_registered_models(), key=lambda m: m.name):
            version = client.get_model_version_by_alias(registered.name, CHAMPION)
            out.append(
                {
                    "name": registered.name,
                    "champion_version": int(version.version),
                    "run_id": version.run_id,
                    "tags": dict(version.tags),
                }
            )
    finally:
        _release(client)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="rebuild the store from the published warehouse")
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--store", type=Path, default=DEFAULT_STORE)
    show = sub.add_parser("summary", help="list the registered models and their champions")
    show.add_argument("--store", type=Path, default=DEFAULT_STORE)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_store(args.db, args.store)
            print(
                f"recorded {result['runs']} runs in {result['experiments']} experiments and "
                f"{len(result['models'])} models ({', '.join(result['models'])}); "
                f"data version {result['data_version']}"
            )
            print(
                f"browse: mlflow ui --backend-store-uri sqlite:///{Path(args.store).as_posix()}/mlflow.db"
            )
        else:
            for item in summary(args.store):
                tags = item["tags"]
                print(
                    f"{item['name']}: champion v{item['champion_version']} "
                    f"({tags.get('model')} {tags.get('model_version')}, "
                    f"sha256 {tags.get('artifact_sha256', '')[:12]}...)"
                )
    except TrackingError as error:
        print(f"tracking refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
