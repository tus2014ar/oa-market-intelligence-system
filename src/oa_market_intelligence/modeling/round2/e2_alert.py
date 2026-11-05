"""E2: an alert for lasting declines, ranked as a top-10 list (plan s. 5, modelling_round2_plan.md).

**The target (T3b), fixed in the plan before any run.** For an eligible segment at origin t0 and
horizon h, the label is 1 if *both* `share(t0+h) <= 0.70 x share(t0)` *and*
`share(t0+h+1) <= 0.85 x share(t0)`: a fall that is still there a month later, so a one-month spike
that recovers does not count. Eligibility is round 1's (a positive origin share and at least 100
category visits at the origin). The label is only known at month t0+h+1, so the walk-forward
treats it as a horizon of h+1 months: a training row may be used at an origin only if the *later* of
its two outcome months is at most that origin. That leaves one fewer test month than round 1.

**The measure.** Precision at 10: each test month, take the 10 segments the method ranks riskiest
(ties broken by score, then by segment name) and count how many truly had a lasting decline; average
over the months (a month with fewer than 10 eligible segments uses all of them). The base rate (the
share of eligible test segments that truly decline) is reported beside every figure. PR-AUC is
reported as a secondary measure.

**The method and the comparators.** The method is round 1's logistic-regression classifier on the
same inputs, trained on the new label. The comparators are the corrected "just spiked" rule (round
1's post-hoc finding), the small-segment rule, a random order, and round 1's own alert (the
classifier trained on round 1's 30%-fall label) scored on the new label.

**Pass rule (per horizon, main scenario).** Both of: precision at 10 is at least 2 times the base
rate; and it beats the best comparator, the lower end of the paired difference (resampling whole
test months, 2,000 draws, seed 0) being above zero at the round's percentile (0.05 / 7). A pass is
provisional, never adoption (plan, section 9).
"""

from __future__ import annotations

import zlib
from collections.abc import Callable

import numpy as np
import pandas as pd

from oa_market_intelligence.modeling.alerts import (
    _ALERT_BUILDERS,
    ALERT_CONFIGS,
    FALL,
    MIN_ORIGIN_VISITS,
    TOLERANCE,
    average_precision,
    origin_visits,
    recent_drop_score,
    small_segment_score,
    tune_alert_config,
    walk_forward_alerts,
)
from oa_market_intelligence.modeling.horizons import (
    apply_scenario,
    assert_pairing,
    month_id_from_index,
    month_index,
    pair_rows,
)
from oa_market_intelligence.modeling.round2.e1_bias import ROUND_PERCENTILE

SECOND_FALL = 0.15  # the month after: at most this far below the origin share
TOP_K = 10
LIFT_MINIMUM = 2.0
HORIZONS = (1, 3)
COMPARATORS = ("recent_spike", "small_segment", "random", "round1_alert")
SCENARIOS = ("main", "covid_removed", "dip_2024_removed", "pediatrics_removed")


# ------------------------------------------------------------------ the target


def lasting_decline_rows(rows: pd.DataFrame, outcomes: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """The feature rows paired with both outcomes: `y_share` at origin + h and `y_share_next` at
    origin + h + 1. `month_id` is the month the label resolves (origin + h + 1), so every
    walk-forward helper treats the label as a horizon of h + 1 months. A row with no outcome at
    either month is dropped."""
    paired = pair_rows(rows, outcomes, horizon)
    later = outcomes[["segment", "month_id", "y_share"]].rename(
        columns={"month_id": "label_month", "y_share": "y_share_next"}
    )
    paired["label_month"] = month_id_from_index(month_index(paired["month_id"].to_numpy()) + 1)
    merged = paired.merge(later, on=["segment", "label_month"], how="inner")
    merged["month_id"] = merged["label_month"]
    out = merged.drop(columns="label_month").sort_values(["month_id", "segment"])
    out = out.reset_index(drop=True)
    assert_pairing(out, horizon + 1)
    return out


def add_lasting_label(paired: pd.DataFrame) -> pd.DataFrame:
    """The eligible rows with `y_alert` (the lasting-decline label) and `y_alert_round1` (round
    1's label: the outcome at origin + h alone is at most 70% of the origin share)."""
    origin = paired["seg_share_lag1"].to_numpy(float)
    eligible = (origin > 0) & (origin_visits(paired) >= MIN_ORIGIN_VISITS)
    out = paired[eligible].copy()
    origin = out["seg_share_lag1"].to_numpy(float)
    first = out["y_share"].to_numpy(float) <= (1 - FALL) * origin * (1 + TOLERANCE)
    second = out["y_share_next"].to_numpy(float) <= (1 - SECOND_FALL) * origin * (1 + TOLERANCE)
    out["y_alert_round1"] = first.astype(int)
    out["y_alert"] = (first & second).astype(int)
    return out.reset_index(drop=True)


# ------------------------------------------------------------------ the methods


def recent_spike_score(rows: pd.DataFrame) -> np.ndarray:
    """Round 1's post-hoc finding: the recent-drop score with its sign flipped, so a segment whose
    latest share is *above* its 3-month mean (a spike) scores higher."""
    return -recent_drop_score(rows)


def random_score(rows: pd.DataFrame) -> np.ndarray:
    """A random order that is a fixed function of each row's month and segment, so the same rows
    always get the same scores whatever else is in the frame."""
    keys = (rows["segment"].astype(str) + "|" + rows["month_id"].astype(str)).to_numpy()
    return np.array([zlib.crc32(k.encode("utf-8")) / 2**32 for k in keys])


COMPARATOR_SCORES = {
    "recent_spike": recent_spike_score,
    "small_segment": small_segment_score,
    "random": random_score,
}


class ScoreOnly:
    """A rule with the alert protocol `(scores, threshold)`; the cut-off is not used here."""

    def __init__(self, fn: Callable[[pd.DataFrame], np.ndarray]):
        self.fn = fn

    def __call__(self, train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, float]:
        return np.asarray(self.fn(test), float), float("nan")


class RankingClassifier:
    """Round 1's tuned logistic-regression classifier, scores only: the setting is re-chosen every
    6 origins on log-loss inside the training window; no alert cut-off is needed to rank."""

    def __init__(self, extra_columns: tuple[str, ...] = (), retune_every: int = 6):
        self.configs = ALERT_CONFIGS["logistic"]
        self.extra = tuple(extra_columns)
        self.retune_every = retune_every
        self.calls = 0
        self._chosen = 0

    def _factory(self, config: dict) -> Callable:
        return _ALERT_BUILDERS["logistic"](config, self.extra)

    def __call__(self, train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, float]:
        if self.calls % self.retune_every == 0:
            self._chosen = tune_alert_config(train, self.configs, self._factory, val_months=12)
        self.calls += 1
        scores = self._factory(self.configs[self._chosen])(train)(test)
        return np.asarray(scores, float), float("nan")


# ------------------------------------------------------------------ the measure


def precision_at_k(preds: pd.DataFrame, k: int = TOP_K) -> pd.Series:
    """Per test month: of the `k` segments ranked riskiest (ties by score, then segment name),
    the share that truly had a lasting decline. A month with fewer than `k` rows uses them all."""
    out = {}
    for month, part in preds.groupby("month_id"):
        top = part.sort_values(["score", "segment"], ascending=[False, True]).head(k)
        out[month] = float(top["label"].mean())
    return pd.Series(out, name="precision_at_k")


def base_rate(preds: pd.DataFrame) -> float:
    """The share of eligible test segments that truly had a lasting decline."""
    return float(preds["label"].mean())


def mean_bootstrap(
    series: dict[str, pd.Series], *, n_boot: int = 2000, seed: int = 0
) -> dict[str, np.ndarray]:
    """The mean of every series on the same resampled sets of whole test months."""
    names = list(series)
    index = series[names[0]].index
    for name in names:
        if not series[name].index.equals(index):
            raise ValueError("every method must be scored on the same test months")
    draws = np.random.default_rng(seed).integers(0, len(index), (n_boot, len(index)))
    return {name: series[name].to_numpy(float)[draws].mean(axis=1) for name in names}


def checks(precision: float, rate: float, low_vs_best: float) -> dict:
    """The two conditions of the pass rule, and whether both hold."""
    result = {
        "lift_at_least_2": bool(precision >= LIFT_MINIMUM * rate),
        "beats_best_comparator": bool(low_vs_best > 0),
    }
    result["passes"] = all(result.values())
    return result


def judge(
    preds: dict[str, pd.DataFrame], horizon: int, *, n_boot: int = 2000, seed: int = 0
) -> dict:
    """The method against its comparators on the same test rows: precision at 10, the base rate,
    PR-AUC, the paired comparison with the best comparator and the pass rule. `preds` holds
    `model` and every comparator."""
    series = {name: precision_at_k(frame) for name, frame in preds.items()}
    mean = {name: float(s.mean()) for name, s in series.items()}
    comparators = [name for name in COMPARATORS if name in preds]
    best = max(comparators, key=lambda name: mean[name])
    draws = mean_bootstrap({n: series[n] for n in ("model", best)}, n_boot=n_boot, seed=seed)
    gain = draws["model"] - draws[best]
    low, high = np.percentile(gain, [ROUND_PERCENTILE, 100 - ROUND_PERCENTILE])
    model_frame = preds["model"]
    rate = base_rate(model_frame)
    return {
        "horizon": horizon,
        "n_test_months": int(len(series["model"])),
        "n_rows": int(len(model_frame)),
        "base_rate": rate,
        "precision_at_10": mean,
        "lift_over_base_rate": {
            name: value / rate if rate else None for name, value in mean.items()
        },
        "pr_auc": {
            name: average_precision(frame["label"].to_numpy(int), frame["score"].to_numpy(float))
            for name, frame in preds.items()
        },
        "best_comparator": best,
        "versus_best_comparator": {
            "estimate": mean["model"] - mean[best],
            "low": float(low),
            "high": float(high),
            "share_positive": float((gain > 0).mean()),
        },
        **checks(mean["model"], rate, float(low)),
    }


# ------------------------------------------------------------------ the runner


def run_e2(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    horizon: int,
    *,
    extra_columns: tuple[str, ...] = (),
    scenarios: tuple[str, ...] = SCENARIOS,
    n_boot: int = 2000,
    seed: int = 0,
    make_classifier: Callable[[], Callable] | None = None,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """E2 at one horizon in every scenario. The classifier is walked twice over the same rows:
    trained on the new label (the method) and on round 1's label (a comparator scored on the new
    label). Scenarios remove rows by the month the label resolves, the later of the two outcomes."""
    if horizon not in HORIZONS:
        raise ValueError(f"E2 is run at horizons {HORIZONS}")
    make = make_classifier or (lambda: RankingClassifier(extra_columns))
    effective = horizon + 1  # the label resolves a month after the first outcome
    base = lasting_decline_rows(rows, outcomes, horizon)
    out: dict = {"horizon": horizon, "scenarios": {}}
    for scenario in scenarios:
        paired = add_lasting_label(apply_scenario(base, scenario))
        progress(f"E2 h={horizon} {scenario}: {len(paired)} eligible rows")
        preds = {
            "model": walk_forward_alerts(paired, make(), effective),
            **{
                name: walk_forward_alerts(paired, ScoreOnly(fn), effective)
                for name, fn in COMPARATOR_SCORES.items()
            },
        }
        round1 = walk_forward_alerts(
            paired.assign(y_alert=paired["y_alert_round1"]), make(), effective
        )
        if not (round1["month_id"].to_numpy() == preds["model"]["month_id"].to_numpy()).all():
            raise ValueError("round 1's alert must be scored on the same rows")
        preds["round1_alert"] = round1.assign(label=preds["model"]["label"].to_numpy())
        out["scenarios"][scenario] = judge(preds, horizon, n_boot=n_boot, seed=seed)
        if scenario == "main":
            out["_preds"] = preds
    return out
