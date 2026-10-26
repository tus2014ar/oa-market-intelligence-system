"""Writes the modelling results into the warehouse (DL-79, docs/modelling_plan.md section 10).

    PYTHONPATH=src python -m oa_market_intelligence.modeling.store [--db PATH]

Two tables are built from the stored results of the real run
(`data/reference/modelling_results.json`), so the publish step does not refit anything:

`model_scores` has one row per task (T1 national share, T2 segment share, T3 decline alert),
horizon, scenario and method, baselines and trained models alike (effect sizes are kept for
every model, including those that fail): the score, its metric and direction, the paired gain
over the best baseline with its interval, whether the model was promoted and whether it is the
one that serves.

`backtest_batch` has the batch demonstration: for each cut-off and each of the six following
months, the prediction and what happened, nationally (with the 90% range) and by specialty. Shares
are fractions (the national series is converted from percentage points).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine

from oa_market_intelligence.modeling.run_modelling import DEFAULT_DB, RESULTS_JSON

SCORES_TABLE = "model_scores"
BACKTEST_TABLE = "backtest_batch"
TASK_METRIC = {"T1": ("mae_pp", 0), "T2": ("log_loss", 0), "T3": ("pr_auc", 1)}
SCORE_COLUMNS = [
    "task", "horizon", "scenario", "method", "kind", "metric", "higher_is_better", "score",
    "is_best_baseline", "gain_over_best_baseline", "gain_lower", "gain_upper", "promoted",
    "serving",
    "n_test_months", "months_better", "coverage_90", "precision", "recall", "f1", "n_alerts",
    "bias_vs_actual",
]
BACKTEST_COLUMNS = [
    "level", "cutoff_month_id", "step", "target_month_id", "specialty_name", "model",
    "predicted_share", "actual_share", "lo90_share", "hi90_share", "visits",
]


def _row(task: str, h: int, scenario: str, method: str, kind: str, block: dict, **extra) -> dict:
    metric, higher = TASK_METRIC[task]
    row = dict.fromkeys(SCORE_COLUMNS)
    row.update(
        task=task, horizon=h, scenario=scenario, method=method, kind=kind, metric=metric,
        higher_is_better=higher, is_best_baseline=int(method == block["best_baseline"]),
        serving=int(method == block["serving"]), n_test_months=block["n_test_months"],
    )
    row.update(extra)
    return row


def _gain(model: dict) -> dict:
    return {"gain_over_best_baseline": model["estimate"], "gain_lower": model["low"],
            "gain_upper": model["high"], "promoted": int(model["promoted"])}


def model_scores_frame(results: dict) -> pd.DataFrame:
    """One row per task, horizon, scenario and method from the run's results."""
    rows: list[dict] = []
    for h, task_block in results.get("T1", {}).items():
        for scenario, d in task_block["scenarios"].items():
            for method, s in d["scores"].items():
                model = d["models"].get(method)
                rows.append(_row(
                    "T1", int(h), scenario, method, "model" if model else "baseline", d,
                    score=s["mae"], coverage_90=s["cover90"], **(_gain(model) if model else {}),
                ))
    for h, task_block in results.get("T2", {}).items():
        calibration = task_block.get("calibration", {})
        for scenario, d in task_block["scenarios"].items():
            for method, loss in d["baseline_log_loss"].items():
                rows.append(_row("T2", int(h), scenario, method, "baseline", d, score=loss))
            for method, m in d["models"].items():
                bias = None
                if scenario == "main":
                    bias = calibration.get(method, {}).get("ratio_minus_one")
                rows.append(_row(
                    "T2", int(h), scenario, method, "model", d, score=m["log_loss"],
                    months_better=m["months_better"], bias_vs_actual=bias, **_gain(m),
                ))
    for h, task_block in results.get("T3", {}).items():
        for scenario, d in task_block["scenarios"].items():
            for method, m in d["methods"].items():
                model = d["models"].get(method)
                rows.append(_row(
                    "T3", int(h), scenario, method, "model" if model else "baseline", d,
                    score=m["pr_auc"], precision=m["precision"], recall=m["recall"], f1=m["f1"],
                    n_alerts=m["n_alerts"], **(_gain(model) if model else {}),
                ))
    return pd.DataFrame(rows, columns=SCORE_COLUMNS)


def backtest_batch_frame(results: dict) -> pd.DataFrame:
    """The batch demonstration predictions beside what happened (shares as fractions)."""
    demo = results.get("demonstration")
    if not demo:
        return pd.DataFrame(columns=BACKTEST_COLUMNS)
    rows = []
    for r in demo["national"]:
        rows.append({
            "level": "national", "cutoff_month_id": r["cutoff"], "step": r["step"],
            "target_month_id": r["month_id"], "specialty_name": None, "model": r["model"],
            "predicted_share": r["point"] / 100, "actual_share": r["actual"] / 100,
            "lo90_share": r["lo90"] / 100, "hi90_share": r["hi90"] / 100, "visits": None,
        })
    for r in demo["segment"]:
        rows.append({
            "level": "specialty", "cutoff_month_id": r["cutoff"], "step": r["step"],
            "target_month_id": r["month_id"], "specialty_name": r["specialty_name"],
            "model": r["model"], "predicted_share": r["predicted_share"],
            "actual_share": r["actual_share"], "lo90_share": None, "hi90_share": None,
            "visits": r["visits"],
        })
    return pd.DataFrame(rows, columns=BACKTEST_COLUMNS)


def load_results(path: Path = RESULTS_JSON) -> dict:
    return json.loads(Path(path).read_text("utf-8"))


def write_model_tables(engine: Engine, results_path: Path = RESULTS_JSON) -> dict:
    """Replace `model_scores` and `backtest_batch` in a warehouse (the rest is untouched).
    Returns the row counts. A missing or empty results file is an error, not an empty table."""
    results = load_results(results_path)
    scores, backtest = model_scores_frame(results), backtest_batch_frame(results)
    if scores.empty or backtest.empty:
        raise ValueError(f"{results_path} has no model scores or no demonstration")
    with engine.begin() as conn:
        scores.to_sql(SCORES_TABLE, conn, if_exists="replace", index=False)
        backtest.to_sql(BACKTEST_TABLE, conn, if_exists="replace", index=False)
    return {SCORES_TABLE: len(scores), BACKTEST_TABLE: len(backtest)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--results", type=Path, default=RESULTS_JSON)
    args = parser.parse_args(argv)
    engine = create_engine(f"sqlite:///{args.db.as_posix()}")
    print(write_model_tables(engine, args.results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
