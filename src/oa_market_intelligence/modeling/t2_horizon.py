"""T2: the segment's share at 1, 3 and 6 months ahead (docs/modelling_plan.md, sections 3 to 6).

Every baseline and trained model goes through the horizon walk-forward (`horizons.py`); the score
is binomial log-loss per visit, the comparison is each trained model against the best baseline of
the same horizon on identical resampled whole months, and a model is promoted if the lower end of
its paired gain is above zero at the corrected percentile. Nothing is tuned to a result.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.linear_model import LogisticRegression

from oa_market_intelligence.modeling.horizons import (
    SCENARIOS,
    apply_scenario,
    assert_pairing,
    horizon_splits,
    pair_rows,
    promotion_percentile,
    serve_simplest,
)
from oa_market_intelligence.modeling.segment_baselines import BASELINES
from oa_market_intelligence.modeling.segment_eval import (
    best_model,
    improvement_summary,
    month_bootstrap,
    pooled,
    share_month_stats,
    walk_forward_predict_rows,
)
from oa_market_intelligence.modeling.segment_models import TunedPredictor

T2_MODELS = ("logistic", "gbm", "rf")
T2_ORDER = ("logistic", "gbm", "rf")  # simplest first
SIZE_EDGES = (0, 50, 200, 1000, np.inf)  # category visits at the origin
SIZE_LABELS = ("under 50", "50 to 199", "200 to 999", "1000 and over")


def predictor_for(name: str, extra_columns: tuple[str, ...]) -> Callable:
    """A baseline (no fitting) or a tuned trained model, by name."""
    if name in BASELINES:
        return BASELINES[name]
    return TunedPredictor(name, extra_columns=extra_columns)


def walk_method(
    paired: pd.DataFrame, horizon: int, name: str, extra_columns: tuple[str, ...]
) -> pd.DataFrame:
    return walk_forward_predict_rows(
        paired, predictor_for(name, extra_columns), splits=horizon_splits(horizon)
    )


def _slope(preds: pd.DataFrame) -> float:
    """The calibration slope: the coefficient of the logit of the prediction in a logistic
    regression of the visit counts. 1 is perfect; below 1 means predictions are too spread out."""
    p = np.clip(preds["p"].to_numpy(float), 1e-6, 1 - 1e-6)
    z, t = preds["z"].to_numpy(float), preds["t"].to_numpy(float)
    if z.sum() == 0 or (t - z).sum() == 0:
        return float("nan")
    x = np.log(p / (1 - p)).reshape(-1, 1)
    model = LogisticRegression(C=1e6, max_iter=1000)
    model.fit(
        np.vstack([x[z > 0], x[t - z > 0]]),
        np.concatenate([np.ones((z > 0).sum()), np.zeros((t - z > 0).sum())]),
        sample_weight=np.concatenate([z[z > 0], (t - z)[t - z > 0]]),
    )
    return float(model.coef_[0][0])


def _ratio(preds: pd.DataFrame) -> float:
    """Visit-weighted predicted share over the observed share, minus one."""
    z = preds["z"].sum()
    return float((preds["t"] * preds["p"]).sum() / z - 1) if z > 0 else float("nan")


def calibration_summary(preds: pd.DataFrame) -> dict:
    """Is the forecast too high or too low? Overall, by segment size (category visits at the
    origin) and by specialty. Reported, not a pass or fail."""
    size = np.expm1(preds["seg_log_visits_lag1"].to_numpy(float))
    band = pd.cut(size, bins=SIZE_EDGES, labels=SIZE_LABELS, right=False)
    by_size = {
        label: {"ratio_minus_one": _ratio(group), "n_rows": int(len(group))}
        for label, group in preds.groupby(band, observed=True)
    }
    by_specialty = {
        name: {"ratio_minus_one": _ratio(group), "n_rows": int(len(group))}
        for name, group in preds.groupby("specialty_name")
    }
    return {
        "ratio_minus_one": _ratio(preds),
        "slope": _slope(preds),
        "by_size": by_size,
        "by_specialty": by_specialty,
    }


def months_better(stats_base: pd.DataFrame, stats_model: pd.DataFrame) -> int:
    loss = lambda s: s["loss_sum"] / s["visits"]  # noqa: E731
    return int((loss(stats_model) < loss(stats_base)).sum())


def judge(
    stats: dict[str, pd.DataFrame], horizon: int, *, n_boot: int = 2000, seed: int = 0
) -> dict:
    """The best baseline, each trained model against it, the promotions and the serving model."""
    lower = promotion_percentile(horizon)
    baselines = {name: s for name, s in stats.items() if name in BASELINES}
    trained = [name for name in T2_MODELS if name in stats]
    best = best_model(baselines, "log_loss")
    draws = month_bootstrap(
        {name: stats[name] for name in (best, *trained)}, "log_loss", n_boot=n_boot, seed=seed
    )
    pooled_loss = {name: pooled(s, "log_loss") for name, s in stats.items()}
    models = {}
    for name in trained:
        summary = improvement_summary(
            stats[best], stats[name], draws[best], draws[name], "log_loss",
            lower_percentile=lower,
        )
        models[name] = {
            **summary,
            "log_loss": pooled_loss[name],
            "relative_gain": summary["estimate"] / pooled_loss[best],
            "months_better": months_better(stats[best], stats[name]),
            "promoted": bool(summary["low"] > 0),
        }
    promoted = [name for name in trained if models[name]["promoted"]]
    tie = serve_simplest(
        promoted, pooled_loss, draws, order=T2_ORDER, lower_percentile=lower,
        higher_is_better=False,
    )
    return {
        "horizon": horizon,
        "lower_percentile": lower,
        "n_test_months": int(len(stats[best])),
        "best_baseline": best,
        "baseline_log_loss": {name: pooled_loss[name] for name in baselines},
        "models": models,
        "promoted": promoted,
        "serving": tie["serving"] or best,
        "serving_is_baseline": tie["serving"] is None,
        "serving_reason": tie["reason"],
        "_draws": draws,
    }


def run_t2_horizon(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    horizon: int,
    *,
    extra_columns: tuple[str, ...] = (),
    scenarios: tuple[str, ...] = SCENARIOS,
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """One horizon: every method through the walk-forward in every scenario, then the judgement.

    `rows` is the feature table (`feat_segment_month`), `outcomes` the Gold outcome table. The
    main scenario also returns the predictions of every method (`_preds`) and the calibration of
    the trained models."""
    paired_all = pair_rows(rows, outcomes, horizon)
    assert_pairing(paired_all, horizon)
    methods = [*BASELINES, *T2_MODELS]
    out: dict = {"horizon": horizon, "scenarios": {}, "calibration": {},
                 "n_paired_rows": len(paired_all)}
    for scenario in scenarios:
        paired = apply_scenario(paired_all, scenario)
        progress(f"T2 h={horizon} {scenario}: {len(paired)} rows")
        results = Parallel(n_jobs=n_jobs)(
            delayed(walk_method)(paired, horizon, name, extra_columns) for name in methods
        )
        preds = dict(zip(methods, results, strict=True))
        stats = {name: share_month_stats(frame) for name, frame in preds.items()}
        out["scenarios"][scenario] = judge(stats, horizon, n_boot=n_boot, seed=seed)
        if scenario == "main":
            out["_preds"] = preds
            out["calibration"] = {name: calibration_summary(preds[name]) for name in T2_MODELS}
    return out


G5_PERCENTILE = 100 * 0.05 / 2  # two horizons, h = 1 and h = 3 (plan, section 6)


def run_g5_hypothesis(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    horizon: int,
    *,
    base_columns: tuple[str, ...],
    g5_columns: tuple[str, ...],
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
) -> dict:
    """The labelled G5 hypothesis: the logistic regression on B0 + G4 + G5 against B0 + G4 at one
    horizon, judged at the 2.5th percentile. Outside promotion; G5 is stored only if it passes."""
    paired = pair_rows(rows, outcomes, horizon)
    assert_pairing(paired, horizon)
    specs = {"base": base_columns, "with_g5": (*base_columns, *g5_columns)}

    def walk(columns):
        return share_month_stats(
            walk_forward_predict_rows(
                paired, TunedPredictor("logistic", extra_columns=columns),
                splits=horizon_splits(horizon),
            )
        )

    results = Parallel(n_jobs=n_jobs)(delayed(walk)(columns) for columns in specs.values())
    stats = dict(zip(specs, results, strict=True))
    draws = month_bootstrap(stats, "log_loss", n_boot=n_boot, seed=seed)
    summary = improvement_summary(
        stats["base"], stats["with_g5"], draws["base"], draws["with_g5"], "log_loss",
        lower_percentile=G5_PERCENTILE,
    )
    return {
        "horizon": horizon, "lower_percentile": G5_PERCENTILE,
        "n_test_months": int(len(stats["base"])), **summary,
        "log_loss_base": pooled(stats["base"], "log_loss"),
        "log_loss_with_g5": pooled(stats["with_g5"], "log_loss"),
        "passes": bool(summary["low"] > 0),
    }
