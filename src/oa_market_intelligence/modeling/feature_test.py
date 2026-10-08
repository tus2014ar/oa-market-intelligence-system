"""The pre-registered test of the candidate feature families (DL-71, feature_engineering_plan.md).

    PYTHONPATH=src python -m oa_market_intelligence.modeling.feature_test --run

Task A: each family is added, one at a time, to the serving logistic regression and judged on
log-loss per visit over the 46 walk-forward test months, paired against the unmodified serving
model on the same resampled months, at the Bonferroni-corrected percentile (0.05 / 6 families:
the 0.83th). Task B: each family is added to the ridge forecast and judged on MAE with the
moving-block bootstrap against "last month", the model that serves (0.05 / 2: the 2.5th
percentile). A family passes if the lower end of its paired improvement is above zero; it is
**robust** only if it also passes with the COVID months removed and with the early-2024 dip months
removed, otherwise **fragile**. Effect sizes are reported for every family.

Nothing here is tuned to a result: the settings are the plan's, and the code runs the plan once.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from oa_market_intelligence.features.candidate_families import (
    FAMILY_COLUMNS,
    FB1_COLUMNS,
    KEYS,
    market_outside_features,
    task_a_family_columns,
    task_b_family_columns,
)
from oa_market_intelligence.modeling.forecast import (
    DEFAULT_MIN_TRAIN,
    RidgeForecaster,
    block_bootstrap_mae,
    forecast_scores,
    improvement_from_draws,
    naive_forecaster,
    walk_forward_forecast,
)
from oa_market_intelligence.modeling.segment_eval import (
    improvement_summary,
    month_bootstrap,
    pooled,
    share_month_stats,
    walk_forward_predict_rows,
)
from oa_market_intelligence.modeling.segment_models import (
    LOGISTIC_CONFIGS,
    TunedPredictor,
    fit_predict_for,
)

ALPHA = 0.05
TASK_A_FAMILIES = ("FA1", "FA2", "FA3", "FA4", "FA5", "FA6")
TASK_B_FAMILIES = ("FB1", "FA5")
LOWER_A = 100 * ALPHA / len(TASK_A_FAMILIES)  # 0.8333...
LOWER_B = 100 * ALPHA / len(TASK_B_FAMILIES)  # 2.5
SCENARIOS = {"main": None, "covid_removed": (202003, 202005), "dip_2024_removed": (202403, 202407)}
RESULTS_JSON = (
    Path(__file__).resolve().parents[3] / "data" / "reference" / "feature_test_results.json"
)
REFERENCE = "reference"


def _columns(families: tuple[str, ...]) -> tuple[str, ...]:
    """The extra model columns of a set of families (FA1 is handled through its own k grid)."""
    return tuple(c for f in families if f != "FA1" for c in FAMILY_COLUMNS[f])


def logistic_predictor(families: tuple[str, ...] = ()) -> TunedPredictor:
    """The serving logistic regression with the given families added. FA1 adds the shrinkage k
    to the tuned setting (k in {5, 20, 80}, chosen inside the training window like C)."""
    extra = _columns(families)
    if "FA1" not in families:
        return TunedPredictor("logistic", extra_columns=extra)
    configs = [{"C": c["C"], "k": k} for k in (5, 20, 80) for c in LOGISTIC_CONFIGS]

    def factory(config):
        k = config["k"]
        columns = (*extra, f"fa1_logit_shrunk_k{k}", f"fa1_weight_k{k}")
        return fit_predict_for("logistic", {"C": config["C"]}, extra_columns=columns)

    return TunedPredictor("logistic", configs, factory=factory)


def _walk(rows: pd.DataFrame, families: tuple[str, ...], min_train_months: int) -> pd.DataFrame:
    predictor = logistic_predictor(families)
    preds = walk_forward_predict_rows(rows, predictor, min_train_months=min_train_months)
    return share_month_stats(preds)


def _monthly_wins(stats_ref: pd.DataFrame, stats_new: pd.DataFrame) -> int:
    loss = lambda s: s["loss_sum"] / s["visits"]  # noqa: E731
    return int((loss(stats_new) < loss(stats_ref)).sum())


def judge_task_a_scenario(
    rows: pd.DataFrame,
    candidates: dict[str, tuple[str, ...]],
    *,
    min_train_months: int = 24,
    n_boot: int = 2000,
    seed: int = 0,
    lower_percentile: float = LOWER_A,
    n_jobs: int = 1,
) -> dict:
    """One scenario: the reference and every candidate through the walk-forward, then the paired
    month bootstrap of each candidate against the reference."""
    names = [REFERENCE, *candidates]
    specs = [(), *candidates.values()]
    stats_list = Parallel(n_jobs=n_jobs)(
        delayed(_walk)(rows, families, min_train_months) for families in specs
    )
    stats = dict(zip(names, stats_list, strict=True))
    draws = month_bootstrap(stats, "log_loss", n_boot=n_boot, seed=seed)
    out = {
        "reference_log_loss": pooled(stats[REFERENCE], "log_loss"),
        "n_test_months": int(len(stats[REFERENCE])),
        "n_rows": int(len(rows)),
        "families": {},
    }
    for name in candidates:
        summary = improvement_summary(
            stats[REFERENCE],
            stats[name],
            draws[REFERENCE],
            draws[name],
            "log_loss",
            lower_percentile=lower_percentile,
        )
        out["families"][name] = {
            **summary,
            "log_loss": pooled(stats[name], "log_loss"),
            "months_better": _monthly_wins(stats[REFERENCE], stats[name]),
            "passes": summary["low"] > 0,
        }
    out["_stats"], out["_draws"] = stats, draws
    return out


def verdict(passes_by_scenario: dict[str, bool]) -> str:
    """robust (passes in every scenario), fragile (passes the main run but not both exclusions)
    or no_pass (fails the main run)."""
    if not passes_by_scenario["main"]:
        return "no_pass"
    return "robust" if all(passes_by_scenario.values()) else "fragile"


def run_task_a(
    rows: pd.DataFrame,
    families: tuple[str, ...] = TASK_A_FAMILIES,
    *,
    scenarios: dict | None = None,
    min_train_months: int = 24,
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """Every family and scenario, the verdicts, and the combined model if two are robust."""
    scenarios = SCENARIOS if scenarios is None else scenarios
    lower = 100 * ALPHA / len(families)
    candidates = {name: (name,) for name in families}
    by_scenario = {}
    for scenario, months in scenarios.items():
        scoped = rows if months is None else rows[~rows["month_id"].between(*months)]
        progress(f"Task A, scenario {scenario}: {len(scoped)} rows")
        by_scenario[scenario] = judge_task_a_scenario(
            scoped.reset_index(drop=True),
            candidates,
            min_train_months=min_train_months,
            n_boot=n_boot,
            seed=seed,
            lower_percentile=lower,
            n_jobs=n_jobs,
        )
    verdicts = {
        name: verdict({s: by_scenario[s]["families"][name]["passes"] for s in by_scenario})
        for name in families
    }
    result = {"lower_percentile": lower, "scenarios": {}, "verdicts": verdicts, "combined": None}
    for scenario, data in by_scenario.items():
        result["scenarios"][scenario] = {k: v for k, v in data.items() if not k.startswith("_")}
    robust = [name for name in families if verdicts[name] == "robust"]
    if len(robust) >= 2:
        main = by_scenario["main"]
        best = max(robust, key=lambda name: main["families"][name]["estimate"])
        combined_families = tuple(robust)
        progress(f"Task A, combined model of {robust} against {best}")
        both = judge_task_a_scenario(
            rows.reset_index(drop=True),
            {best: (best,), "combined": combined_families},
            min_train_months=min_train_months,
            n_boot=n_boot,
            seed=seed,
            lower_percentile=lower,
            n_jobs=n_jobs,
        )
        stats, draws = both["_stats"], both["_draws"]
        versus_best = improvement_summary(
            stats[best],
            stats["combined"],
            draws[best],
            draws["combined"],
            "log_loss",
            lower_percentile=lower,
        )
        result["combined"] = {
            "families": robust,
            "best_single": best,
            **versus_best,
            "adopted": versus_best["low"] > 0,
        }
    return result


# ---------------------------------------------------------------- Task B


class ExtraRidgeForecaster(RidgeForecaster):
    """The Phase 4 ridge forecast with extra columns appended to every row (a family's columns for
    the target month, as known the month before)."""

    def __init__(self, extra: pd.DataFrame, **kwargs):
        super().__init__(**kwargs)
        self.extra = extra

    def _row(self, values: np.ndarray, month_id: int, j: int) -> list[float]:  # noqa: D102
        base = RidgeForecaster._row(values, month_id, j)
        return [*base, *self.extra.loc[month_id].to_numpy(float).tolist()]


def run_task_b(
    series: pd.Series,
    extras: dict[str, pd.DataFrame],
    *,
    min_train: int = DEFAULT_MIN_TRAIN,
    n_boot: int = 2000,
    block_length: int = 6,
    seed: int = 0,
    lower_percentile: float = LOWER_B,
    dip: tuple[int, int] = SCENARIOS["dip_2024_removed"],
) -> dict:
    """Last month, the plain ridge and the ridge with each family: MAE with the block bootstrap,
    judged against last month (what serves) at the corrected percentile and against the plain
    ridge for the effect size. The robustness run scores without the early-2024 dip months; the
    COVID months lie before the first test month, so for this task that scenario changes nothing
    in the scoring (reported, not run)."""
    forecasters = {"last_month": naive_forecaster, "ridge": RidgeForecaster()}
    for name, frame in extras.items():
        forecasters[f"ridge+{name}"] = ExtraRidgeForecaster(frame.set_index("month_id"))
    forecasts = {
        name: walk_forward_forecast(series, fn, min_train=min_train)
        for name, fn in forecasters.items()
    }
    result = {"lower_percentile": lower_percentile, "scenarios": {}, "verdicts": {}}
    passes: dict[str, dict[str, bool]] = {name: {} for name in extras}
    for scenario, drop in (("main", None), ("dip_2024_removed", dip)):
        kept = {
            name: f if drop is None else f[~f["month_id"].between(*drop)].reset_index(drop=True)
            for name, f in forecasts.items()
        }
        errors = {name: (f["actual"] - f["point"]).abs().to_numpy() for name, f in kept.items()}
        draws = block_bootstrap_mae(errors, block_length=block_length, n_boot=n_boot, seed=seed)
        scores = {name: forecast_scores(f)["mae"] for name, f in kept.items()}
        block = {"mae": scores, "n_test_months": int(len(kept["last_month"])), "families": {}}
        for name in extras:
            model = f"ridge+{name}"
            vs_last = improvement_from_draws(
                scores["last_month"],
                scores[model],
                draws["last_month"],
                draws[model],
                lower_percentile=lower_percentile,
            )
            vs_ridge = improvement_from_draws(
                scores["ridge"],
                scores[model],
                draws["ridge"],
                draws[model],
                lower_percentile=lower_percentile,
            )
            block["families"][name] = {
                "against_last_month": vs_last,
                "against_plain_ridge": vs_ridge,
                "passes": vs_last["low"] > 0,
            }
            passes[name][scenario] = vs_last["low"] > 0
        result["scenarios"][scenario] = block
    for name in extras:
        result["verdicts"][name] = verdict(passes[name])
    result["fallbacks"] = {name: int(f["fallback"].sum()) for name, f in forecasts.items()}
    return result


# ---------------------------------------------------------------- the real run


def run_all(*, n_boot: int = 2000, seed: int = 0, n_jobs: int = 4, log=print) -> dict:
    """Load the data, build the families, run both tasks. Used by the command line."""
    import json as _json

    from sqlalchemy import create_engine

    from oa_market_intelligence.availability import load_availability
    from oa_market_intelligence.external.export import DEFAULT_SUBSET
    from oa_market_intelligence.features.segment import load_segment_gold
    from oa_market_intelligence.features.segment_task import build_task_a_rows
    from oa_market_intelligence.mart import MANIFEST_JSON, build_signals, load_manifest_rows
    from oa_market_intelligence.modeling.forecast import load_share_series

    root = Path(__file__).resolve().parents[3]
    engine = create_engine(f"sqlite:///{(root / 'data' / 'published' / 'warehouse.db').as_posix()}")
    subset = create_engine(f"sqlite:///{DEFAULT_SUBSET.as_posix()}")
    signals = build_signals(subset, load_availability(), load_manifest_rows(MANIFEST_JSON))

    seg = load_segment_gold(engine)
    rows = build_task_a_rows(seg)
    columns = task_a_family_columns(seg, signals)
    rows = rows.merge(columns, on=KEYS, how="left")
    family_columns = [c for f in TASK_A_FAMILIES for c in FAMILY_COLUMNS[f]]
    if rows[family_columns].isna().any().any():
        raise ValueError("a family column has missing values in the scored rows")
    log(f"Task A rows: {len(rows)}; families: {TASK_A_FAMILIES}")
    task_a = run_task_a(rows, n_boot=n_boot, seed=seed, n_jobs=n_jobs, progress=log)

    series = load_share_series(engine)
    with engine.connect() as conn:
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
    both = task_b_family_columns(monthly, signals).set_index("month_id")
    extras = {
        "FB1": both[FB1_COLUMNS].reset_index(),
        "FA5": market_outside_features(signals, series.index.tolist()),
    }
    log("Task B")
    task_b = run_task_b(series, extras, n_boot=n_boot, seed=seed)
    result = {"task_a": task_a, "task_b": task_b, "seed": seed, "n_boot": n_boot}
    return _json.loads(_json.dumps(result, default=float))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="run the pre-registered test once")
    parser.add_argument("--out", type=Path, default=RESULTS_JSON)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    if not args.run:
        parser.print_help()
        return 0
    result = run_all(n_jobs=args.jobs)
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print("Task A verdicts:", result["task_a"]["verdicts"])
    print("Task B verdicts:", result["task_b"]["verdicts"])
    print("combined:", result["task_a"]["combined"])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
