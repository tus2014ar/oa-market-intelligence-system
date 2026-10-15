"""The pre-registered test of the feature groups (DL-75, docs/feature_engineering_plan.md).

    PYTHONPATH=src python -m oa_market_intelligence.features.validate --run

T2 (segment share): each group is added, one at a time, to the reference logistic regression (the
base set B0) and judged on log-loss per visit over the walk-forward test months, paired against the
unmodified reference on the same resampled whole months, at the Bonferroni-corrected percentile
(0.05 divided by the 7 groups). T1 (national share): each group is added to the plain ridge and
judged on MAE with the moving-block bootstrap against "last month", the rule that serves (0.05
divided by the 5 groups). A group passes if the lower end of its paired improvement is above zero;
it is robust only if it also passes with the COVID months removed and with the 2024 dip removed,
otherwise fragile. G7 (flags) cannot be judged by removing the months it flags, so it is judged on
the main run only. A sensitivity run lags every feature by one more month (data through t-2) and is
reported beside the main run; it is not part of the pass rule.

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

from oa_market_intelligence.features.candidate_families import KEYS, SHRINK_K
from oa_market_intelligence.features.groups import (
    SHRINK_COLUMNS,
    T1_GROUPS,
    T2_GROUPS,
    columns_for,
    task_a_group_columns,
    task_b_group_columns,
)
from oa_market_intelligence.modeling.forecast import (
    DEFAULT_MIN_TRAIN,
    RidgeForecaster,
    block_bootstrap_mae,
    empirical_interval,
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
LOWER_T2 = 100 * ALPHA / len(T2_GROUPS)  # 0.714...
LOWER_T1 = 100 * ALPHA / len(T1_GROUPS)  # 1.0
SCENARIOS = {"main": None, "covid_removed": (202003, 202005), "dip_2024_removed": (202403, 202407)}
NOT_ROBUSTNESS_TESTABLE = ("G7",)
RESULTS_JSON = (
    Path(__file__).resolve().parents[3] / "data" / "reference" / "feature_group_results.json"
)
REFERENCE = "reference"
# known for the target month whatever the publication lag (calendar and static columns), so the
# t-2 sensitivity run leaves them as they are
NO_LAG = {
    "month_sin",
    "month_cos",
    "age_ordinal",
    "gender_FEMALE",
    "gender_MALE",
    "g2_is_dec",
    "g2_is_mar_apr",
    "g7_covid",
    "g7_dip",
    "g7_pediatrics",
}


# --------------------------------------------------------------------------- the verdict


def verdict(passes_by_scenario: dict[str, bool], *, robustness_testable: bool = True) -> str:
    """robust (passes in every scenario), fragile (passes the main run but not both exclusions),
    no_pass (fails the main run) or, for a group that cannot be judged by removing months,
    pass_main_only."""
    if not passes_by_scenario["main"]:
        return "no_pass"
    if not robustness_testable:
        return "pass_main_only"
    return "robust" if all(passes_by_scenario.values()) else "fragile"


# --------------------------------------------------------------------------- T2


def logistic_predictor(groups: tuple[str, ...] = ()) -> TunedPredictor:
    """The reference logistic regression with the given groups added. G3 adds the shrinkage k to
    the tuned setting (k in {5, 20, 80}, chosen inside the training window like C)."""
    extra = columns_for(groups, target="T2")
    if "G3" not in groups:
        return TunedPredictor("logistic", extra_columns=extra)
    configs = [{"C": c["C"], "k": k} for k in SHRINK_K for c in LOGISTIC_CONFIGS]

    def factory(config):
        columns = (*extra, *SHRINK_COLUMNS[config["k"]])
        return fit_predict_for("logistic", {"C": config["C"]}, extra_columns=columns)

    return TunedPredictor("logistic", configs, factory=factory)


def _walk(rows: pd.DataFrame, groups: tuple[str, ...], min_train_months: int) -> pd.DataFrame:
    preds = walk_forward_predict_rows(
        rows, logistic_predictor(groups), min_train_months=min_train_months
    )
    return share_month_stats(preds)


def _monthly_wins(stats_ref: pd.DataFrame, stats_new: pd.DataFrame) -> int:
    loss = lambda s: s["loss_sum"] / s["visits"]  # noqa: E731
    return int((loss(stats_new) < loss(stats_ref)).sum())


def judge_t2_scenario(
    rows: pd.DataFrame,
    candidates: dict[str, tuple[str, ...]],
    *,
    min_train_months: int = 24,
    n_boot: int = 2000,
    seed: int = 0,
    lower_percentile: float = LOWER_T2,
    n_jobs: int = 1,
) -> dict:
    """One scenario: the reference and every candidate through the walk-forward, then the paired
    month bootstrap of each candidate against the reference."""
    names = [REFERENCE, *candidates]
    specs = [(), *candidates.values()]
    stats_list = Parallel(n_jobs=n_jobs)(
        delayed(_walk)(rows, groups, min_train_months) for groups in specs
    )
    stats = dict(zip(names, stats_list, strict=True))
    draws = month_bootstrap(stats, "log_loss", n_boot=n_boot, seed=seed)
    out = {
        "reference_log_loss": pooled(stats[REFERENCE], "log_loss"),
        "n_test_months": int(len(stats[REFERENCE])),
        "n_rows": int(len(rows)),
        "groups": {},
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
        out["groups"][name] = {
            **summary,
            "log_loss": pooled(stats[name], "log_loss"),
            "months_better": _monthly_wins(stats[REFERENCE], stats[name]),
            "passes": summary["low"] > 0,
        }
    out["_stats"], out["_draws"] = stats, draws
    return out


def run_t2(
    rows: pd.DataFrame,
    groups: tuple[str, ...] = T2_GROUPS,
    *,
    scenarios: dict | None = None,
    min_train_months: int = 24,
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """Every group and scenario, the verdicts, and the combined model if two are robust."""
    scenarios = SCENARIOS if scenarios is None else scenarios
    lower = 100 * ALPHA / len(groups)
    candidates = {name: (name,) for name in groups}
    by_scenario = {}
    for scenario, months in scenarios.items():
        scoped = rows if months is None else rows[~rows["month_id"].between(*months)]
        progress(f"T2, scenario {scenario}: {len(scoped)} rows")
        by_scenario[scenario] = judge_t2_scenario(
            scoped.reset_index(drop=True),
            candidates,
            min_train_months=min_train_months,
            n_boot=n_boot,
            seed=seed,
            lower_percentile=lower,
            n_jobs=n_jobs,
        )
    verdicts = {}
    for name in groups:
        testable = name not in NOT_ROBUSTNESS_TESTABLE
        passes = {s: by_scenario[s]["groups"][name]["passes"] for s in by_scenario}
        if not testable:
            passes = {"main": passes["main"]}
        verdicts[name] = verdict(passes, robustness_testable=testable)
    result = {"lower_percentile": lower, "scenarios": {}, "verdicts": verdicts, "combined": None}
    for scenario, data in by_scenario.items():
        result["scenarios"][scenario] = {k: v for k, v in data.items() if not k.startswith("_")}
    robust = [name for name in groups if verdicts[name] == "robust"]
    if len(robust) >= 2 and "main" in by_scenario:
        main = by_scenario["main"]
        best = max(robust, key=lambda name: main["groups"][name]["estimate"])
        progress(f"T2, combined model of {robust} against {best}")
        both = judge_t2_scenario(
            rows.reset_index(drop=True),
            {best: (best,), "combined": tuple(robust)},
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
            "groups": robust,
            "best_single": best,
            **versus_best,
            "adopted": versus_best["low"] > 0,
        }
    return result


def lag_rows_one_month(rows: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """The sensitivity table: every feature column of a segment-month is replaced by the value of
    the segment's previous month (data through t-2). Rows without a previous-month row are
    dropped; the targets and keys are untouched."""
    months = sorted(rows["month_id"].unique())
    previous = dict(zip(months[1:], months[:-1], strict=True))
    base = rows.copy()
    base["_prev"] = base["month_id"].map(previous)
    source = rows[["segment", "month_id", *columns]].rename(columns={"month_id": "_prev"})
    merged = base.drop(columns=columns).merge(source, on=["segment", "_prev"], how="inner")
    return merged.drop(columns="_prev").sort_values(["month_id", "segment"]).reset_index(drop=True)


# --------------------------------------------------------------------------- T1


class ExtraRidgeForecaster(RidgeForecaster):
    """The Phase 4 ridge forecast with extra columns appended to every row (a group's columns for
    the target month, as known the month before)."""

    def __init__(self, extra: pd.DataFrame | None = None, **kwargs):
        super().__init__(**kwargs)
        self.extra = extra

    def _row(self, values: np.ndarray, month_id: int, j: int) -> list[float]:  # noqa: D102
        base = RidgeForecaster._row(values, month_id, j)
        if self.extra is None:
            return base
        return [*base, *self.extra.loc[month_id].to_numpy(float).tolist()]


class TwoStepRidgeForecaster(ExtraRidgeForecaster):
    """The same ridge using only data through t-2 (lags 2 to 4), for the sensitivity run."""

    def _row(self, values: np.ndarray, month_id: int, j: int) -> list[float]:  # noqa: D102
        lags = [values[j - 2], values[j - 3], values[j - 4]]
        month = month_id % 100
        base = [
            *lags,
            float(np.mean(lags)),
            np.sin(2 * np.pi * month / 12),
            np.cos(2 * np.pi * month / 12),
        ]
        if self.extra is None:
            return base
        return [*base, *self.extra.loc[month_id].to_numpy(float).tolist()]

    def _table(self, values, month_ids, stop):  # noqa: D102
        rows = [self._row(values, month_ids[j], j) for j in range(4, stop)]
        return np.array(rows), values[4:stop]


def naive_two_step(history: pd.Series, target_month: int) -> dict:
    """The value two months before the target, with an interval from past two-step changes."""
    values = history.to_numpy(float)
    return empirical_interval(values[-2], values[2:] - values[:-2])


def run_t1(
    series: pd.Series,
    extras: dict[str, pd.DataFrame],
    *,
    min_train: int = DEFAULT_MIN_TRAIN,
    n_boot: int = 2000,
    block_length: int = 6,
    seed: int = 0,
    lower_percentile: float | None = None,
    dip: tuple[int, int] = SCENARIOS["dip_2024_removed"],
    lag_two: bool = False,
) -> dict:
    """The plain ridge and the ridge with each group, judged against last month (what serves) at
    the corrected percentile and against the plain ridge for the effect size. The robustness run
    scores without the early-2024 dip months; the COVID months lie before the first test month, so
    for this target that scenario changes nothing in the scoring (reported, not run). With
    `lag_two` the same comparison is made using data through t-2 only."""
    lower_percentile = LOWER_T1 if lower_percentile is None else lower_percentile
    ridge_cls = TwoStepRidgeForecaster if lag_two else ExtraRidgeForecaster
    reference_fn = naive_two_step if lag_two else naive_forecaster
    forecasters = {"last_month": reference_fn, "ridge": ridge_cls()}
    for name, frame in extras.items():
        table = frame.set_index("month_id")
        if lag_two:  # features known through t-2: the value the previous month's row had
            moving = [c for c in table.columns if c not in NO_LAG]
            table[moving] = table[moving].shift(1).fillna(0.0)
        forecasters[f"ridge+{name}"] = ridge_cls(table)
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
        block = {"mae": scores, "n_test_months": int(len(kept["last_month"])), "groups": {}}
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
            block["groups"][name] = {
                "against_last_month": vs_last,
                "against_plain_ridge": vs_ridge,
                "passes": vs_last["low"] > 0,
            }
            passes[name][scenario] = vs_last["low"] > 0
        result["scenarios"][scenario] = block
    for name in extras:
        testable = name not in NOT_ROBUSTNESS_TESTABLE
        scenario_passes = passes[name] if testable else {"main": passes[name]["main"]}
        result["verdicts"][name] = verdict(scenario_passes, robustness_testable=testable)
    result["fallbacks"] = {name: int(f["fallback"].sum()) for name, f in forecasts.items()}
    return result


# --------------------------------------------------------------------------- the real run


def load_inputs(root: Path | None = None):
    """The segment rows with every group column, the national series and its group columns."""
    from sqlalchemy import create_engine

    from oa_market_intelligence.analysis.competitor_shares import monthly_group_visits
    from oa_market_intelligence.availability import load_availability
    from oa_market_intelligence.external.export import DEFAULT_SUBSET
    from oa_market_intelligence.features.segment import load_segment_gold
    from oa_market_intelligence.features.segment_task import build_task_a_rows
    from oa_market_intelligence.mart import MANIFEST_JSON, build_signals, load_manifest_rows
    from oa_market_intelligence.modeling.forecast import load_share_series

    root = root or Path(__file__).resolve().parents[3]
    engine = create_engine(f"sqlite:///{(root / 'data' / 'published' / 'warehouse.db').as_posix()}")
    subset = create_engine(f"sqlite:///{DEFAULT_SUBSET.as_posix()}")
    signals = build_signals(subset, load_availability(), load_manifest_rows(MANIFEST_JSON))
    group_visits = monthly_group_visits(engine)

    seg = load_segment_gold(engine)
    columns = task_a_group_columns(seg, signals, group_visits)
    rows = build_task_a_rows(seg).merge(columns, on=KEYS, how="left")
    wanted = [c for g in T2_GROUPS for c in columns_for((g,), target="T2")]
    wanted += [c for pair in SHRINK_COLUMNS.values() for c in pair]
    if rows[wanted].isna().any().any():
        raise ValueError("a group column has missing values in the scored rows")

    series = load_share_series(engine)
    with engine.connect() as conn:
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
    both = task_b_group_columns(monthly, signals, group_visits).set_index("month_id")
    extras = {g: both[list(columns_for((g,), target="T1"))].reset_index() for g in T1_GROUPS}
    return rows, series, extras


def run_all(*, n_boot: int = 2000, seed: int = 0, n_jobs: int = 4, log=print) -> dict:
    rows, series, extras = load_inputs()
    log(f"T2 rows: {len(rows)}; groups: {T2_GROUPS}")
    t2 = run_t2(rows, n_boot=n_boot, seed=seed, n_jobs=n_jobs, progress=log)
    log("T1")
    t1 = run_t1(series, extras, n_boot=n_boot, seed=seed)

    log("Sensitivity: data through t-2")
    feature_columns = [c for g in T2_GROUPS for c in columns_for((g,), target="T2")]
    feature_columns += [c for pair in SHRINK_COLUMNS.values() for c in pair]
    feature_columns = [c for c in feature_columns if c not in NO_LAG]
    base_columns = [
        c
        for c in rows.columns
        if c not in (*KEYS, "segment", "specialty_grouped")
        and not c.startswith("y_")
        and c not in feature_columns
        and c not in NO_LAG
    ]
    lagged = lag_rows_one_month(rows, [*base_columns, *feature_columns])
    t2_lag = run_t2(
        lagged, scenarios={"main": None}, n_boot=n_boot, seed=seed, n_jobs=n_jobs, progress=log
    )
    t1_lag = run_t1(series, extras, n_boot=n_boot, seed=seed, lag_two=True)
    result = {
        "t2": t2,
        "t1": t1,
        "sensitivity_t_minus_2": {"t2": t2_lag, "t1": t1_lag},
        "seed": seed,
        "n_boot": n_boot,
    }
    return json.loads(json.dumps(result, default=float))


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
    print("T2 verdicts:", result["t2"]["verdicts"])
    print("T1 verdicts:", result["t1"]["verdicts"])
    print("combined:", result["t2"]["combined"])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
