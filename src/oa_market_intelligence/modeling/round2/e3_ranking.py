"""E3: does the segment model rank winners and losers? (plan s. 6, modelling_round2_plan.md)

**The measure, fixed in the plan before any run (horizon 3 months, the planning case).** At each
test origin, over the eligible segments, the Spearman rank correlation between the predicted
change (forecast share minus the share at the origin) and the actual change (share at origin + 3
minus the share at the origin); the mean over test origins. Also reported: of the segments put in
the top fifth for predicted change, the share that were in the top two fifths for actual change.

**The comparators.** "Same as last month" predicts no change for anyone and cannot rank, so it is
not a comparator. They are: the specialty's own 3-month change continuing; the segment's own
3-month change continuing; and a random order. Each uses only months at or before the origin.

**Pass rule.** The mean Spearman correlation beats the best comparator's, the lower end of the
paired difference (resampling whole test origins, 2,000 draws, seed 0) being above zero at the
round's percentile (0.05 / 7). A pass is provisional, never adoption (plan, section 9). Part of any
predicted change is mean reversion; a pass is reported as "ranks well", not as "predicts what will
happen to a segment".
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from oa_market_intelligence.modeling.alerts import MIN_ORIGIN_VISITS, origin_visits
from oa_market_intelligence.modeling.horizons import (
    apply_scenario,
    assert_pairing,
    horizon_splits,
    month_index,
    pair_rows,
)
from oa_market_intelligence.modeling.round2.e1_bias import ROUND_PERCENTILE, assert_matches_round1
from oa_market_intelligence.modeling.round2.e2_alert import mean_bootstrap, random_score
from oa_market_intelligence.modeling.segment_eval import (
    CARRIED,
    pooled,
    share_month_stats,
    walk_forward_predict_rows,
)
from oa_market_intelligence.modeling.t2_horizon import predictor_for

HORIZON = 3
LOOKBACK = 3  # months back from the origin for the "trend continues" comparators
MIN_ROWS = 5  # a test origin with fewer eligible segments is left out for every method
TOP_PREDICTED = 0.2  # the top fifth by predicted change
TOP_ACTUAL = 0.4  # ... compared with the top two fifths by actual change
COMPARATORS = ("specialty_trend", "segment_trend", "random")
METHODS = ("model", *COMPARATORS)
SCENARIOS = ("main", "covid_removed", "dip_2024_removed", "pediatrics_removed")
CARRY = (*CARRIED, "seg_share_lag1", "origin_id")


# ------------------------------------------------------------------ history known at the origin


def specialty_of(segments: pd.Series) -> pd.Series:
    """The specialty part of the 'specialty|age band|gender' segment key."""
    return segments.astype(str).str.split("|").str[0]


def share_tables(outcomes: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """The share of every segment-month, and of every specialty-month (all its segments pooled),
    each indexed by (name, running month number)."""
    out = outcomes.copy()
    out["z"] = np.rint(out["y_share"].to_numpy(float) * out["y_visits"].to_numpy(float))
    out["specialty"] = specialty_of(out["segment"])
    out["mi"] = month_index(out["month_id"].to_numpy())
    segment = out.set_index(["segment", "mi"])["y_share"]
    by_specialty = out.groupby(["specialty", "mi"])[["z", "y_visits"]].sum()
    specialty = by_specialty["z"] / by_specialty["y_visits"].where(by_specialty["y_visits"] > 0)
    return segment, specialty


def _lookup(table: pd.Series, name: pd.Series, month: np.ndarray) -> np.ndarray:
    index = pd.MultiIndex.from_arrays([name.to_numpy(), month])
    return table.reindex(index).to_numpy(float)


def add_history(
    preds: pd.DataFrame, outcomes: pd.DataFrame, lookback: int = LOOKBACK
) -> pd.DataFrame:
    """The segment's and its specialty's share change over the `lookback` months ending at the
    origin, from the outcome table. Only the origin month and the month `lookback` earlier are read,
    so nothing after the origin can enter."""
    segment, specialty = share_tables(outcomes)
    origin = month_index(preds["origin_id"].to_numpy())
    earlier = origin - lookback
    if (earlier >= origin).any() or lookback < 1:
        raise ValueError("the history must be before the origin")
    out = preds.copy()
    name = specialty_of(out["segment"])
    out["segment_trend"] = _lookup(segment, out["segment"], origin) - _lookup(
        segment, out["segment"], earlier
    )
    out["specialty_trend"] = _lookup(specialty, name, origin) - _lookup(specialty, name, earlier)
    return out


def eligible_frame(preds: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    """One row per eligible segment and test origin, with the predicted change, the actual change
    and the comparators' scores. Eligible: round 1's rule (a positive share and at least 100
    category visits at the origin) and a known share `LOOKBACK` months before the origin, so every
    method is scored on the same rows."""
    frame = add_history(preds, outcomes)
    origin_share = frame["seg_share_lag1"].to_numpy(float)
    keep = (
        (origin_share > 0)
        & (origin_visits(frame) >= MIN_ORIGIN_VISITS)
        & frame["segment_trend"].notna().to_numpy()
        & frame["specialty_trend"].notna().to_numpy()
    )
    frame = frame[keep].copy()
    origin_share = frame["seg_share_lag1"].to_numpy(float)
    frame["actual"] = frame["z"].to_numpy(float) / frame["t"].to_numpy(float) - origin_share
    frame["model"] = frame["p"].to_numpy(float) - origin_share
    frame["random"] = random_score(frame)
    columns = ["month_id", "segment", "actual", *METHODS]
    return frame[columns].reset_index(drop=True)


# ------------------------------------------------------------------ the measures


def spearman(a, b) -> float:
    """Spearman rank correlation with ties given their average rank. NaN if either side is
    constant (a constant has no order)."""
    ra = pd.Series(np.asarray(a, float)).rank(method="average").to_numpy()
    rb = pd.Series(np.asarray(b, float)).rank(method="average").to_numpy()
    if np.ptp(ra) == 0 or np.ptp(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def months_used(frame: pd.DataFrame) -> list[int]:
    counts = frame.groupby("month_id").size()
    return sorted(counts[counts >= MIN_ROWS].index)


def monthly_spearman(frame: pd.DataFrame, method: str) -> pd.Series:
    """Per test origin: the rank correlation of the method's score with the actual change. An origin
    where the method gives everyone the same score counts 0 (it ranks nothing)."""
    out = {}
    for month in months_used(frame):
        part = frame[frame["month_id"] == month]
        value = spearman(part[method], part["actual"])
        out[month] = 0.0 if np.isnan(value) else value
    return pd.Series(out, name=method)


def _top(part: pd.DataFrame, column: str, share: float) -> set:
    k = int(np.ceil(share * len(part)))
    ordered = part.sort_values([column, "segment"], ascending=[False, True])
    return set(ordered["segment"].head(k))


def top_fifth_hit(frame: pd.DataFrame, method: str) -> pd.Series:
    """Per test origin: of the segments the method puts in the top fifth for predicted change, the
    share that were in the top two fifths for actual change (ties by segment name)."""
    out = {}
    for month in months_used(frame):
        part = frame[frame["month_id"] == month]
        picked = _top(part, method, TOP_PREDICTED)
        out[month] = len(picked & _top(part, "actual", TOP_ACTUAL)) / len(picked)
    return pd.Series(out, name=method)


def chance_hit(frame: pd.DataFrame) -> float:
    """The hit rate a random pick would have: the top two fifths' share of the segments."""
    shares = [
        int(np.ceil(TOP_ACTUAL * n)) / n for n in frame.groupby("month_id").size() if n >= MIN_ROWS
    ]
    return float(np.mean(shares))


def checks(low_vs_best: float) -> dict:
    """The one condition of the pass rule, and whether it holds."""
    result = {"beats_best_comparator": bool(low_vs_best > 0)}
    result["passes"] = all(result.values())
    return result


def judge(frame: pd.DataFrame, *, n_boot: int = 2000, seed: int = 0) -> dict:
    """The model against its comparators on the same rows: mean rank correlation, the top-fifth hit
    rate, the paired comparison with the best comparator and with each, and the pass rule."""
    spearman_by_month = {m: monthly_spearman(frame, m) for m in METHODS}
    hits = {m: top_fifth_hit(frame, m) for m in METHODS}
    mean = {m: float(s.mean()) for m, s in spearman_by_month.items()}
    best = max(COMPARATORS, key=lambda name: mean[name])
    draws = mean_bootstrap(spearman_by_month, n_boot=n_boot, seed=seed)
    versus = {}
    for name in COMPARATORS:
        gain = draws["model"] - draws[name]
        low, high = np.percentile(gain, [ROUND_PERCENTILE, 100 - ROUND_PERCENTILE])
        versus[name] = {
            "estimate": mean["model"] - mean[name],
            "low": float(low),
            "high": float(high),
            "share_positive": float((gain > 0).mean()),
        }
    return {
        "horizon": HORIZON,
        "n_test_months": int(len(spearman_by_month["model"])),
        "n_rows": int(frame.groupby("month_id").size().loc[months_used(frame)].sum()),
        "mean_spearman": mean,
        "top_fifth_hit_rate": {m: float(s.mean()) for m, s in hits.items()},
        "chance_hit_rate": chance_hit(frame),
        "best_comparator": best,
        "versus_best_comparator": versus[best],
        "versus_each_comparator": versus,
        **checks(versus[best]["low"]),
    }


# ------------------------------------------------------------------ the runner


def run_e3(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    extra_columns: tuple[str, ...] = (),
    scenarios: tuple[str, ...] = SCENARIOS,
    stored_log_loss: float | None = None,
    n_boot: int = 2000,
    seed: int = 0,
    make_predictor: Callable[[str], Callable] | None = None,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """E3 at 3 months in every scenario. `stored_log_loss` (round 1's pooled logistic log-loss at 3
    months, main scenario) guards that the model walked here is round 1's."""
    make = make_predictor or (lambda name: predictor_for(name, extra_columns))
    paired_all = pair_rows(rows, outcomes, HORIZON)
    assert_pairing(paired_all, HORIZON)
    out: dict = {"horizon": HORIZON, "scenarios": {}}
    for scenario in scenarios:
        paired = apply_scenario(paired_all, scenario)
        progress(f"E3 h={HORIZON} {scenario}: {len(paired)} rows")
        preds = walk_forward_predict_rows(
            paired, make("logistic"), splits=horizon_splits(HORIZON), carry=CARRY
        )
        if scenario == "main" and stored_log_loss is not None:
            assert_matches_round1(share_month_stats(preds), stored_log_loss)
        frame = eligible_frame(preds, outcomes)
        out["scenarios"][scenario] = {
            **judge(frame, n_boot=n_boot, seed=seed),
            "log_loss": pooled(share_month_stats(preds), "log_loss"),
        }
        if scenario == "main":
            out["_frame"] = frame
    return out
