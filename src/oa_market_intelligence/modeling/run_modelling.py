"""Runs the modelling plan (DL-78, docs/modelling_plan.md) once, and writes the results as JSON.

    PYTHONPATH=src python -m oa_market_intelligence.modeling.run_modelling --counts-only
    PYTHONPATH=src python -m oa_market_intelligence.modeling.run_modelling --run

`--counts-only` prints the pairing, test-month and label counts for every horizon without fitting
anything. `--run` is the real run: T1, T2 and T3 at h = 1, 3 and 6 in every scenario, the labelled
G5 hypothesis (h = 1 and 3), and the batch demonstration of the serving models at the two fixed
cut-offs. It has no timestamp and every random choice is seeded, so a second run must give a
byte-identical file. The settings are the plan's; the code runs the plan once.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine

from oa_market_intelligence.features.groups import columns_for
from oa_market_intelligence.modeling.alerts import (
    ALERT_SCENARIOS,
    add_alert_label,
    run_alerts_horizon,
)
from oa_market_intelligence.modeling.demonstration import (
    CUTOFFS,
    national_batch,
    segment_batch,
)
from oa_market_intelligence.modeling.forecast import load_share_series
from oa_market_intelligence.modeling.horizons import (
    HORIZONS,
    SCENARIOS,
    assert_pairing,
    horizon_splits,
    outcome_table,
    pair_rows,
)
from oa_market_intelligence.modeling.t1_horizon import run_t1_horizon
from oa_market_intelligence.modeling.t2_horizon import run_g5_hypothesis, run_t2_horizon

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "data" / "published" / "warehouse.db"
RESULTS_JSON = ROOT / "data" / "reference" / "modelling_results.json"
G5_HORIZONS = (1, 3)
TASKS = ("T1", "T2", "T3", "G5", "demonstration")


def clean(value):
    """JSON-ready: drop private keys (those starting with `_`), turn numpy values into plain
    ones and NaN or infinity into null."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def write_results(results: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(clean(results), indent=1, sort_keys=True, allow_nan=False) + "\n", "utf-8"
    )


def load_inputs(engine: Engine) -> dict:
    """The feature table, the Gold outcome table and the national share series."""
    from oa_market_intelligence.features.segment import load_segment_gold

    with engine.connect() as conn:
        rows = pd.read_sql("SELECT * FROM feat_segment_month ORDER BY month_id, segment", conn)
    seg = load_segment_gold(engine)
    return {
        "rows": rows,
        "seg": seg,
        "outcomes": outcome_table(seg),
        "series": load_share_series(engine),
    }


def counts(rows: pd.DataFrame, outcomes: pd.DataFrame, horizons=HORIZONS) -> dict:
    """What each horizon will score, with no model fitted: paired rows, test months, scored rows
    and declines (full history, and the test months only)."""
    out = {}
    for h in horizons:
        paired = pair_rows(rows, outcomes, h)
        assert_pairing(paired, h)
        labelled = add_alert_label(paired)
        test_months = {month for month, _, _ in horizon_splits(h)(labelled)}
        in_test = labelled[labelled["month_id"].isin(test_months)]
        out[str(h)] = {
            "paired_rows": int(len(paired)),
            "t2_test_months": int(len({m for m, _, _ in horizon_splits(h)(paired)})),
            "t3_scored_rows": int(len(labelled)),
            "t3_declines": int(labelled["y_alert"].sum()),
            "t3_decline_share": float(labelled["y_alert"].mean()),
            "t3_test_months": int(len(test_months)),
            "t3_test_rows": int(len(in_test)),
            "t3_test_declines": int(in_test["y_alert"].sum()),
        }
    return out


def serving_map(task_result: dict) -> dict[int, str]:
    """The serving model of each horizon from a task's main scenario."""
    return {int(h): r["scenarios"]["main"]["serving"] for h, r in task_result.items()}


def run_all(
    engine: Engine,
    *,
    horizons: tuple[int, ...] = HORIZONS,
    tasks: tuple[str, ...] = TASKS,
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
    scenarios: tuple[str, ...] | None = None,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """Every task at every horizon. `scenarios` limits the robustness scenarios of T2 and T3 (the
    plan runs all of them, which is the default)."""
    data = load_inputs(engine)
    rows, outcomes, series = data["rows"], data["outcomes"], data["series"]
    g4 = columns_for(("G4",), target="T2")
    results: dict = {
        "meta": {"horizons": list(horizons), "n_boot": n_boot, "seed": seed,
                 "plan": "docs/modelling_plan.md (DL-78)", "g4_columns": list(g4)},
        "counts": counts(rows, outcomes, horizons),
    }
    if "T1" in tasks:
        results["T1"] = {}
        for h in horizons:
            progress(f"T1 h={h}")
            r = run_t1_horizon(series, h, n_boot=n_boot, seed=seed)
            results["T1"][str(h)] = r
    if "T2" in tasks:
        results["T2"] = {}
        for h in horizons:
            r = run_t2_horizon(rows, outcomes, h, extra_columns=g4, n_boot=n_boot, seed=seed,
                               n_jobs=n_jobs, progress=progress, **_only(scenarios, SCENARIOS))
            r.pop("_preds", None)
            results["T2"][str(h)] = r
    if "T3" in tasks:
        results["T3"] = {}
        for h in horizons:
            r = run_alerts_horizon(rows, outcomes, h, extra_columns=g4, n_boot=n_boot,
                                   seed=seed, n_jobs=n_jobs, progress=progress,
                                   **_only(scenarios, ALERT_SCENARIOS))
            r.pop("_preds", None)
            results["T3"][str(h)] = r
    if "G5" in tasks:
        results["G5"] = {}
        rows_g5 = _rows_with_g5(engine, data["seg"])
        g5 = columns_for(("G5",), target="T2")
        for h in G5_HORIZONS:
            if h not in horizons:
                continue
            progress(f"G5 hypothesis h={h}")
            results["G5"][str(h)] = run_g5_hypothesis(
                rows_g5, outcomes, h, base_columns=g4, g5_columns=g5, n_boot=n_boot, seed=seed,
                n_jobs=n_jobs,
            )
    if (
        "demonstration" in tasks
        and "T1" in results
        and "T2" in results
        and set(HORIZONS) <= {int(h) for h in results["T2"]} & {int(h) for h in results["T1"]}
    ):
        results["demonstration"] = _demonstration(results, series, rows, outcomes, g4, progress)
    return results


def _only(scenarios, allowed) -> dict:
    """The `scenarios` keyword for a runner: the requested ones among those it supports."""
    if scenarios is None:
        return {}
    return {"scenarios": tuple(s for s in scenarios if s in allowed)}


def _rows_with_g5(engine: Engine, seg: pd.DataFrame) -> pd.DataFrame:
    from oa_market_intelligence.analysis.competitor_shares import monthly_group_visits
    from oa_market_intelligence.features.build import build_feature_tables

    with engine.connect() as conn:
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
    table, _ = build_feature_tables(
        seg, monthly, None, monthly_group_visits(engine), groups=("G4", "G5")
    )
    return table


def _demonstration(results, series, rows, outcomes, g4, progress) -> dict:
    t1 = serving_map(results["T1"])
    t2 = serving_map(results["T2"])
    out = {"serving_t1": t1, "serving_t2": t2, "national": [], "segment": []}
    for cutoff in CUTOFFS:
        progress(f"demonstration, cut-off {cutoff}")
        out["national"] += national_batch(series, cutoff, t1).to_dict("records")
        out["segment"] += segment_batch(rows, outcomes, cutoff, t2, extra_columns=g4).to_dict(
            "records"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=RESULTS_JSON)
    parser.add_argument("--counts-only", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--n-jobs", type=int, default=1)
    parser.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS))
    parser.add_argument("--tasks", nargs="+", default=list(TASKS), choices=TASKS)
    args = parser.parse_args(argv)
    if not (args.counts_only or args.run):
        parser.error("choose --counts-only or --run")

    engine = create_engine(f"sqlite:///{args.db.as_posix()}")
    if args.counts_only:
        data = load_inputs(engine)
        print(json.dumps(clean(counts(data["rows"], data["outcomes"], tuple(args.horizons))),
                         indent=1, sort_keys=True))
        return 0
    results = run_all(
        engine, horizons=tuple(args.horizons), tasks=tuple(args.tasks), n_boot=args.n_boot,
        n_jobs=args.n_jobs, progress=lambda message: print(message, flush=True),
    )
    write_results(results, args.out)
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
