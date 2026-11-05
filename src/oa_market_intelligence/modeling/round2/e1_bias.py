"""E1: can the segment forecast's upward bias be removed? (docs/modelling_round2_plan.md, section 4)

Two variants, both fixed in the plan before any run:

- **E1a, a correction factor.** At the origin, `factor = actual Zilretta visits / predicted
  Zilretta visits` over the forecasts of the 12 most recent target months at that horizon whose
  target month is at most the origin (so every outcome is already known), clipped to [0.7, 1.3];
  each predicted share is multiplied by it and capped at 1. With fewer than 12 resolved target
  months the factor is 1. The raw (uncorrected) forecasts feed the factor, so corrections never
  compound.
- **E1b, a rolling window.** The same logistic regression fitted on the rows whose outcome month
  lies in the 36 months ending at the origin (everything else as in round 1).

Pass rule (per variant and horizon, main scenario), all of:
1. the pooled bias is at most 2.5% (1 month) or 5% (3 months) in absolute value;
2. non-inferiority: the lower end of the paired log-loss improvement over the uncorrected forecast
   is at least -0.1% of the uncorrected log-loss, at the round's percentile (0.05 / 7);
3. the variant still beats the best baseline by round 1's own rule (the paired gain's lower end,
   at that horizon's round 1 percentile, above zero).

A pass is provisional, never adoption (plan, section 9).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from oa_market_intelligence.modeling.horizons import (
    DEFAULT_MIN_ORIGIN_MONTHS,
    SCENARIOS,
    apply_scenario,
    assert_pairing,
    horizon_splits,
    month_index,
    pair_rows,
    promotion_percentile,
)
from oa_market_intelligence.modeling.segment_eval import (
    improvement_summary,
    month_bootstrap,
    pooled,
    share_month_stats,
    walk_forward_predict_rows,
)
from oa_market_intelligence.modeling.t2_horizon import calibration_summary, predictor_for

WINDOW_MONTHS = 12  # resolved target months the correction factor is computed from
FACTOR_LOW, FACTOR_HIGH = 0.7, 1.3
ROLLING_MONTHS = 36
ROUND_TESTS = 7  # the formal tests of the whole round (plan, section 8)
ROUND_PERCENTILE = 100 * 0.05 / ROUND_TESTS
BIAS_LIMIT = {1: 0.025, 3: 0.05}
NON_INFERIORITY = 0.001  # relative to the uncorrected log-loss
HORIZONS = (1, 3)
VARIANTS = ("e1a", "e1b")
TOLERANCE = 1e-9


# ------------------------------------------------------------------ E1a: the correction factor


def correction_factors(
    preds: pd.DataFrame,
    horizon: int,
    *,
    window: int = WINDOW_MONTHS,
    low: float = FACTOR_LOW,
    high: float = FACTOR_HIGH,
) -> pd.Series:
    """The factor for every test month, from forecasts whose target month is at most that month's
    origin (the month `horizon` earlier). `preds` holds the raw walk-forward forecasts: `month_id`
    (the target month), `p`, `z` (Zilretta visits) and `t` (category visits)."""
    months = sorted(preds["month_id"].unique())
    z = preds.groupby("month_id")["z"].sum()
    predicted = (preds["t"] * preds["p"]).groupby(preds["month_id"]).sum()
    index = month_index(np.array(months))
    factors = {}
    for month, position in zip(months, index, strict=True):
        resolved = [m for m, i in zip(months, index, strict=True) if i <= position - horizon]
        resolved = resolved[-window:]
        if len(resolved) < window or predicted[resolved].sum() <= 0:
            factors[month] = 1.0
            continue
        factor = float(z[resolved].sum() / predicted[resolved].sum())
        factors[month] = float(np.clip(factor, low, high))
    return pd.Series(factors, name="factor")


def apply_factors(preds: pd.DataFrame, factors: pd.Series) -> pd.DataFrame:
    """The forecasts multiplied by their month's factor and capped at 1; the raw `p` is kept as
    `p_raw`."""
    out = preds.copy()
    out["p_raw"] = out["p"]
    out["p"] = np.minimum(
        1.0, out["p"].to_numpy(float) * out["month_id"].map(factors).to_numpy(float)
    )
    return out


# ------------------------------------------------------------------ E1b: the rolling window


def rolling_splits(horizon: int, months: int = ROLLING_MONTHS) -> Callable:
    """Round 1's walk-forward splits with each training set cut to the rows whose outcome month
    lies in the `months` months ending at the origin."""
    base = horizon_splits(horizon)

    def splits(rows: pd.DataFrame, *, min_train_months: int = DEFAULT_MIN_ORIGIN_MONTHS):
        outcome = month_index(rows["month_id"].to_numpy())
        for test_month, train, test in base(rows, min_train_months=min_train_months):
            origin = int(month_index(test_month)) - horizon
            kept = train[outcome[train] >= origin - (months - 1)]
            if len(kept) == 0:
                continue
            yield test_month, kept, test

    return splits


# ------------------------------------------------------------------ the pass rule


def bias(preds: pd.DataFrame) -> float:
    """Visit-weighted predicted share over the observed share, minus one (round 1's definition)."""
    z = float(preds["z"].sum())
    return float((preds["t"] * preds["p"]).sum() / z - 1) if z > 0 else float("nan")


def checks(
    pooled_bias: float, low_vs_raw: float, raw_loss: float, low_vs_baseline: float, horizon: int
) -> dict:
    """The three conditions of the pass rule, each true or false, and whether all hold."""
    result = {
        "bias_within_limit": bool(abs(pooled_bias) <= BIAS_LIMIT[horizon]),
        "not_less_accurate": bool(low_vs_raw >= -NON_INFERIORITY * raw_loss),
        "still_beats_baseline": bool(low_vs_baseline > 0),
    }
    result["passes"] = all(result.values())
    return result


def assert_matches_round1(raw_stats: pd.DataFrame, stored_log_loss: float) -> None:
    """The uncorrected walk must be round 1's logistic regression exactly, or the experiment is
    comparing against something else."""
    gap = abs(pooled(raw_stats, "log_loss") - stored_log_loss)
    if gap > TOLERANCE:
        raise ValueError(f"the uncorrected forecasts differ from round 1's by {gap:.2e}")


def _months_active(factors: pd.Series) -> list[int]:
    return [int(m) for m, f in factors.items() if f != 1.0]


def judge(
    preds: dict[str, pd.DataFrame],
    factors: pd.Series,
    horizon: int,
    *,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict:
    """The raw forecasts, the two variants and the best baseline: scores, bias, the paired
    comparisons and the pass rule. `preds` has `base`, `raw`, `e1a` and `e1b`, all on the same
    test months."""
    stats = {name: share_month_stats(frame) for name, frame in preds.items()}
    draws = month_bootstrap(stats, "log_loss", n_boot=n_boot, seed=seed)
    raw_loss = pooled(stats["raw"], "log_loss")
    out = {
        "horizon": horizon,
        "n_test_months": int(len(stats["raw"])),
        "log_loss": {name: pooled(s, "log_loss") for name, s in stats.items()},
        "bias": {name: bias(frame) for name, frame in preds.items()},
        "factor_active_months": len(_months_active(factors)),
        "variants": {},
    }
    active = preds["e1a"]["month_id"].isin(_months_active(factors))
    out["bias_when_corrected_e1a"] = bias(preds["e1a"][active]) if active.any() else None
    out["bias_when_corrected_raw"] = bias(preds["raw"][active]) if active.any() else None
    for name in VARIANTS:
        vs_raw = improvement_summary(
            stats["raw"],
            stats[name],
            draws["raw"],
            draws[name],
            "log_loss",
            lower_percentile=ROUND_PERCENTILE,
        )
        vs_base = improvement_summary(
            stats["base"],
            stats[name],
            draws["base"],
            draws[name],
            "log_loss",
            lower_percentile=promotion_percentile(horizon),
        )
        out["variants"][name] = {
            "versus_uncorrected": vs_raw,
            "versus_best_baseline": {
                **vs_base,
                "relative_gain": vs_base["estimate"] / out["log_loss"]["base"],
            },
            **checks(out["bias"][name], vs_raw["low"], raw_loss, vs_base["low"], horizon),
        }
    return out


# ------------------------------------------------------------------ the runner


def run_e1(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    horizon: int,
    *,
    extra_columns: tuple[str, ...] = (),
    scenarios: tuple[str, ...] = SCENARIOS,
    stored_log_loss: float | None = None,
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
    make_predictor: Callable[[str], Callable] | None = None,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """E1 at one horizon in every scenario. `stored_log_loss` (round 1's pooled logistic
    log-loss at this horizon, main scenario) guards that the uncorrected walk is round 1's."""
    if horizon not in HORIZONS:
        raise ValueError(f"E1 is run at horizons {HORIZONS}")
    make = make_predictor or (lambda name: predictor_for(name, extra_columns))
    paired_all = pair_rows(rows, outcomes, horizon)
    assert_pairing(paired_all, horizon)
    out: dict = {"horizon": horizon, "scenarios": {}}
    for scenario in scenarios:
        paired = apply_scenario(paired_all, scenario)
        progress(f"E1 h={horizon} {scenario}: {len(paired)} rows")

        def walk(name, splits):
            return walk_forward_predict_rows(paired, make(name), splits=splits)

        jobs = {
            "raw": ("logistic", horizon_splits(horizon)),
            "e1b": ("logistic", rolling_splits(horizon)),
            "base": ("last_month", horizon_splits(horizon)),
        }
        results = Parallel(n_jobs=n_jobs)(delayed(walk)(*job) for job in jobs.values())
        preds = dict(zip(jobs, results, strict=True))
        if scenario == "main" and stored_log_loss is not None:
            assert_matches_round1(share_month_stats(preds["raw"]), stored_log_loss)
        factors = correction_factors(preds["raw"], horizon)
        preds["e1a"] = apply_factors(preds["raw"], factors)
        out["scenarios"][scenario] = judge(preds, factors, horizon, n_boot=n_boot, seed=seed)
        if scenario == "main":
            out["factors"] = {int(m): float(f) for m, f in factors.items()}
            out["calibration"] = {
                name: calibration_summary(preds[name]) for name in ("raw", "e1a", "e1b")
            }
            out["_preds"] = preds
    return out
