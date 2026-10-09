"""The modelling improvement round (DL-73, docs/modelling_improvement_plan.md).

    PYTHONPATH=src python -m oa_market_intelligence.modeling.improvement_round --run

M1  a 12-month logit-offset layer on the serving segment model (bias correction)
M4  conformal intervals: adaptive for the national forecast, standardised for segments
M5  a CUSUM drift monitor for the national series and a top-10 headroom overlap for segments
(M6, the competitor shares, is in analysis/competitor_shares.py)

Every rule and threshold below is the plan's, fixed before any run. Nothing here is tuned to a
result, and the real run happens once (and is repeated once for identical output).
"""

from __future__ import annotations

import argparse
import json
from math import ceil
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from scipy.optimize import brentq
from scipy.special import expit, logit

from oa_market_intelligence.modeling.feature_test import SCENARIOS, logistic_predictor, verdict
from oa_market_intelligence.modeling.forecast import (
    DEFAULT_MIN_TRAIN,
    coverage_gate,
    forecast_scores,
    naive_forecaster,
    walk_forward_forecast,
)
from oa_market_intelligence.modeling.monitoring import (
    HORIZON,
    MIN_MONTHS_AFTER_START,
    perturb_drift,
)
from oa_market_intelligence.modeling.segment_eval import (
    CARRIED,
    CLIP,
    improvement_summary,
    month_bootstrap,
    share_month_stats,
    walk_forward_predict_rows,
)
from oa_market_intelligence.modeling.segment_judge import SIZE_EDGES, SIZE_LABELS

RESULTS_JSON = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "reference"
    / "modelling_improvement_results.json"
)
CARRY = (*CARRIED, "seg_share_lag1")

# M1
OFFSET_WINDOW = 12
OFFSET_MIN_PRIOR = 3
M1_LOWER_PERCENTILE = 5.0
BIAS_TOLERANCE = 0.03

# M4
ACI_GAMMA = 0.02
ACI_ALPHA = 0.10  # 90% intervals
WIDTH_CUT = 0.10
SEGMENT_WARMUP = 3
SEGMENT_COVERAGE_TOLERANCE = 0.03
BUCKET_COVERAGE_TOLERANCE = 0.05

# M5
CUSUM_K = 0.5
H_GRID = (3, 4, 5, 6, 8)
ARL_MIN = 48
ARL_CAP = 480
DRIFTS = (0.05, 0.1, 0.2, 0.3)
PASS_DRIFT = 0.2
PASS_DETECTION = 0.80
DETECTION_HORIZONS = (12, HORIZON)
TOP_K = 10
MIN_VISITS_RANK = 100


# ------------------------------------------------------------------------------- M1


def fit_offset(z, t, logit_p) -> float:
    """The offset delta that maximises the binomial likelihood of logit(p) + delta (the
    gradient is decreasing in delta, so the root is found by bracketing)."""
    z, t, logit_p = (np.asarray(a, dtype=float) for a in (z, t, logit_p))
    if len(z) == 0:
        return 0.0

    def gradient(delta: float) -> float:
        return float((z - t * expit(logit_p + delta)).sum())

    low, high = -10.0, 10.0
    if gradient(low) <= 0:
        return low
    if gradient(high) >= 0:
        return high
    return float(brentq(gradient, low, high))


def _logit_clipped(p) -> np.ndarray:
    return logit(np.clip(np.asarray(p, dtype=float), CLIP, 1 - CLIP))


def apply_offset_layer(
    preds: pd.DataFrame, *, window: int = OFFSET_WINDOW, min_prior: int = OFFSET_MIN_PRIOR
) -> tuple[pd.DataFrame, pd.Series]:
    """Correct each month's predictions with an offset fitted on the raw predictions of the
    months before it (the previous `window` months present, at least `min_prior`, otherwise no
    correction). Returns the frame with `p` corrected and the offset used for each month."""
    months = sorted(preds["month_id"].unique())
    by_month = {m: preds[preds["month_id"] == m] for m in months}
    corrected, deltas = [], {}
    for i, month in enumerate(months):
        prior = months[max(0, i - window) : i]
        part = by_month[month]
        if len(prior) < min_prior:
            delta = 0.0
        else:
            past = pd.concat([by_month[m] for m in prior])
            delta = fit_offset(past["z"], past["t"], _logit_clipped(past["p"]))
        deltas[month] = delta
        corrected.append(part.assign(p=expit(_logit_clipped(part["p"]) + delta)))
    return pd.concat(corrected, ignore_index=True), pd.Series(deltas, name="offset")


def bias(preds: pd.DataFrame) -> float:
    """Visit-weighted mean predicted share over the observed share, minus one (the model card's
    measure: predicted 2.65% against observed 2.48% is +7%)."""
    return float((preds["p"] * preds["t"]).sum() / preds["z"].sum() - 1)


def _bucket(preds: pd.DataFrame) -> pd.Series:
    previous = np.expm1(preds["seg_log_visits_lag1"].to_numpy(float))
    return pd.cut(previous, SIZE_EDGES, right=False, labels=SIZE_LABELS)


def judge_m1(
    preds: pd.DataFrame, *, n_boot: int = 2000, seed: int = 0, window: int = OFFSET_WINDOW
) -> dict:
    """The reference predictions against the same predictions with the offset layer."""
    corrected, deltas = apply_offset_layer(preds, window=window)
    stats = {"reference": share_month_stats(preds), "corrected": share_month_stats(corrected)}
    draws = month_bootstrap(stats, "log_loss", n_boot=n_boot, seed=seed)
    summary = improvement_summary(
        stats["reference"],
        stats["corrected"],
        draws["reference"],
        draws["corrected"],
        "log_loss",
        lower_percentile=M1_LOWER_PERCENTILE,
    )
    ref, new = stats["reference"], stats["corrected"]
    better = int(((new["loss_sum"] / new["visits"]) < (ref["loss_sum"] / ref["visits"])).sum())
    bias_before, bias_after = bias(preds), bias(corrected)
    by_size = {}
    bucket_before, bucket_after = _bucket(preds), _bucket(corrected)
    for label in SIZE_LABELS:
        before, after = (
            preds[np.asarray(bucket_before == label)],
            corrected[np.asarray(bucket_after == label)],
        )
        by_size[label] = {"before": bias(before), "after": bias(after), "n": int(len(before))}
    return {
        **summary,
        "n_test_months": int(len(ref)),
        "months_better": better,
        "bias_before": bias_before,
        "bias_after": bias_after,
        "bias_by_size": by_size,
        "offsets": {int(k): float(v) for k, v in deltas.items()},
        "passes": bool(summary["low"] > 0 and abs(bias_after) <= BIAS_TOLERANCE),
    }


def reference_predictions(rows: pd.DataFrame, *, min_train_months: int = 24) -> pd.DataFrame:
    """The serving logistic regression's walk-forward predictions (one pass, reused)."""
    return walk_forward_predict_rows(
        rows, logistic_predictor(()), min_train_months=min_train_months, carry=CARRY
    )


def run_m1(
    preds_by_scenario: dict[str, pd.DataFrame], *, n_boot: int = 2000, seed: int = 0
) -> dict:
    scenarios = {
        name: judge_m1(preds, n_boot=n_boot, seed=seed) for name, preds in preds_by_scenario.items()
    }
    passes = {name: block["passes"] for name, block in scenarios.items()}
    return {
        "lower_percentile": M1_LOWER_PERCENTILE,
        "scenarios": scenarios,
        "verdict": verdict(passes),
    }


# ------------------------------------------------------------------------------- M4


def conformal_quantile(scores, alpha: float) -> float:
    """The ceil((n + 1)(1 - alpha))-th smallest score; the largest if that rank exceeds n; zero
    if the rank is below one (alpha at or above 1)."""
    scores = np.sort(np.asarray(scores, dtype=float))
    n = len(scores)
    rank = ceil((n + 1) * (1 - alpha) - 1e-12)
    if rank > n:
        return float(scores[-1])
    if rank < 1:
        return 0.0
    return float(scores[rank - 1])


def aci_forecast(
    series: pd.Series,
    *,
    min_train: int = DEFAULT_MIN_TRAIN,
    gamma: float = ACI_GAMMA,
    alpha0: float = ACI_ALPHA,
) -> pd.DataFrame:
    """Last month's value with an adaptive conformal 90% interval. The score is the absolute
    one-step change; alpha moves by gamma x (0.10 - miss) after each month. Month t uses only
    changes up to t - 1."""
    values = series.to_numpy(float)
    alpha, rows = alpha0, []
    for i in range(min_train, len(values)):
        scores = np.abs(np.diff(values[:i]))
        half = conformal_quantile(scores, alpha)
        point = values[i - 1]
        low, high = point - half, point + half
        miss = float(not (low <= values[i] <= high))
        rows.append(
            {
                "month_id": int(series.index[i]),
                "actual": float(values[i]),
                "point": float(point),
                "lo90": float(low),
                "hi90": float(high),
                "alpha": float(alpha),
            }
        )
        alpha += gamma * (ACI_ALPHA - miss)
    return pd.DataFrame(rows)


def _block_index(n: int, block: int, n_boot: int, rng, length: int | None = None) -> np.ndarray:
    """Moving-block resample positions: `n_boot` rows of `length` (default n) positions in
    [0, n), taken in runs of `block` consecutive positions."""
    length = n if length is None else length
    n_blocks = -(-length // block)
    starts = rng.integers(0, n - block + 1, (n_boot, n_blocks))
    return (starts[:, :, None] + np.arange(block)).reshape(n_boot, -1)[:, :length]


def judge_m4_task_b(
    series: pd.Series, *, n_boot: int = 2000, block_length: int = 6, seed: int = 0
) -> dict:
    served = walk_forward_forecast(series, naive_forecaster)
    served_scores = forecast_scores(served)
    aci = aci_forecast(series)
    inside = ((aci["actual"] >= aci["lo90"]) & (aci["actual"] <= aci["hi90"])).to_numpy(float)
    width = (aci["hi90"] - aci["lo90"]).to_numpy(float)
    gate = coverage_gate(float(inside.mean()), nominal=0.90, n=len(aci))
    width_cut = 1 - float(width.mean()) / served_scores["width90"]
    index = _block_index(len(aci), block_length, n_boot, np.random.default_rng(seed))
    cover_draws, width_draws = inside[index].mean(axis=1), width[index].mean(axis=1)
    return {
        "n_test_months": int(len(aci)),
        "served": {"coverage90": served_scores["cover90"], "width90": served_scores["width90"]},
        "aci": {
            "coverage90": float(inside.mean()),
            "width90": float(width.mean()),
            "coverage_interval": [float(x) for x in np.percentile(cover_draws, [5, 95])],
            "width_interval": [float(x) for x in np.percentile(width_draws, [5, 95])],
            "final_alpha": float(aci["alpha"].iloc[-1]),
        },
        "coverage_gate": gate,
        "width_cut": float(width_cut),
        "passes": bool(gate["ok"] and width_cut >= WIDTH_CUT),
    }


def segment_intervals(preds: pd.DataFrame, *, level: float = 0.90, warmup: int = SEGMENT_WARMUP):
    """Conformal intervals for every row of every scored month. The score is the absolute
    share error divided by sqrt(p(1 - p) / previous-month visits); the quantile comes from
    the scores of all earlier test months. The first `warmup` months only fill the pool."""
    p = np.clip(preds["p"].to_numpy(float), CLIP, 1 - CLIP)
    previous = np.maximum(np.expm1(preds["seg_log_visits_lag1"].to_numpy(float)), 1.0)
    scale = np.sqrt(p * (1 - p) / previous)
    share = preds["z"].to_numpy(float) / preds["t"].to_numpy(float)
    score = np.abs(share - p) / scale
    month = preds["month_id"].to_numpy()
    months = sorted(np.unique(month))
    pieces = []
    for i, m in enumerate(months):
        if i < warmup:
            continue
        pool = score[np.isin(month, months[:i])]
        q = conformal_quantile(pool, 1 - level)
        mask = month == m
        low = np.clip(p[mask] - q * scale[mask], 0.0, 1.0)
        high = np.clip(p[mask] + q * scale[mask], 0.0, 1.0)
        pieces.append(
            pd.DataFrame(
                {
                    "month_id": m,
                    "covered": ((share[mask] >= low) & (share[mask] <= high)).astype(float),
                    "width": high - low,
                    "bucket": _bucket(preds.loc[mask]).astype(str),
                }
            )
        )
    return pd.concat(pieces, ignore_index=True)


def judge_m4_task_a(
    preds: pd.DataFrame, *, n_boot: int = 2000, seed: int = 0, level: float = 0.90
) -> dict:
    scored = segment_intervals(preds, level=level)
    months = sorted(scored["month_id"].unique())
    draws = np.random.default_rng(seed).integers(0, len(months), (n_boot, len(months)))

    def coverage_with_interval(frame: pd.DataFrame) -> dict:
        per_month = frame.groupby("month_id")["covered"].agg(["sum", "count"]).reindex(months)
        per_month = per_month.fillna(0.0)
        sums, counts = per_month["sum"].to_numpy(), per_month["count"].to_numpy()
        boot = sums[draws].sum(axis=1) / np.maximum(counts[draws].sum(axis=1), 1)
        return {
            "coverage": float(frame["covered"].mean()) if len(frame) else float("nan"),
            "interval": [float(x) for x in np.percentile(boot, [5, 95])],
            "n": int(len(frame)),
            "mean_width": float(frame["width"].mean()) if len(frame) else float("nan"),
        }

    overall = coverage_with_interval(scored)
    buckets = {
        label: coverage_with_interval(scored[scored["bucket"] == label]) for label in SIZE_LABELS
    }
    overall_ok = abs(overall["coverage"] - level) <= SEGMENT_COVERAGE_TOLERANCE
    buckets_ok = all(
        b["n"] > 0 and abs(b["coverage"] - level) <= BUCKET_COVERAGE_TOLERANCE
        for b in buckets.values()
    )
    return {
        "level": level,
        "n_scored_months": int(len(months)),
        "overall": overall,
        "buckets": buckets,
        "overall_ok": bool(overall_ok),
        "buckets_ok": bool(buckets_ok),
        "passes": bool(overall_ok and buckets_ok),
    }


# ------------------------------------------------------------------------------- M5


def standardized_changes(values, *, min_train: int = DEFAULT_MIN_TRAIN) -> np.ndarray:
    """The one-step change at each position from `min_train` on, divided by the standard
    deviation of all earlier changes (NaN before that). Position i uses values up to i only."""
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    for i in range(min_train, len(values)):
        sd = np.std(np.diff(values[:i]), ddof=1)
        out[i] = (values[i] - values[i - 1]) / sd if sd > 0 else 0.0
    return out


def cusum_alarm_positions(z, *, h: float, k: float = CUSUM_K) -> list[int]:
    """Two-sided CUSUM; the statistic restarts from zero after an alarm. NaN counts as zero."""
    up = down = 0.0
    alarms = []
    for i, value in enumerate(np.asarray(z, dtype=float)):
        if np.isnan(value):
            continue
        up = max(0.0, up + value - k)
        down = max(0.0, down - value - k)
        if up > h or down > h:
            alarms.append(i)
            up = down = 0.0
    return alarms


def simulate_arl(
    changes,
    *,
    h_grid=H_GRID,
    k: float = CUSUM_K,
    min_train: int = DEFAULT_MIN_TRAIN,
    cap: int = ARL_CAP,
    n_runs: int = 2000,
    block_length: int = 6,
    seed: int = 0,
) -> dict:
    """Average run length to the first alarm without drift, from moving-block resamples of the
    real one-step changes; runs with no alarm in `cap` months count as `cap` (so the figure is
    a lower bound). Returns the ARL, and the share of runs without an alarm, per threshold."""
    changes = np.asarray(changes, dtype=float)
    start = min_train - 1  # the first standardised position uses `start` earlier changes
    length = start + cap
    sample = changes[
        _block_index(len(changes), block_length, n_runs, np.random.default_rng(seed), length)
    ]
    s1, s2 = np.cumsum(sample, axis=1), np.cumsum(sample**2, axis=1)
    z = np.zeros((n_runs, cap))
    for j in range(cap):
        n = start + j  # earlier changes available for the change at column start + j
        mean = s1[:, n - 1] / n
        var = np.maximum((s2[:, n - 1] - n * mean**2) / (n - 1), 1e-12)
        z[:, j] = sample[:, n] / np.sqrt(var)
    out = {}
    for h in h_grid:
        up = np.zeros(n_runs)
        down = np.zeros(n_runs)
        first = np.full(n_runs, cap, dtype=float)
        for j in range(cap):
            up = np.maximum(0.0, up + z[:, j] - k)
            down = np.maximum(0.0, down - z[:, j] - k)
            hit = ((up > h) | (down > h)) & (first == cap)
            first[hit] = j + 1
        out[h] = {"arl": float(first.mean()), "no_alarm_share": float((first == cap).mean())}
    return out


def choose_threshold(arl: dict, *, minimum: int = ARL_MIN) -> float | None:
    """The smallest threshold on the grid whose no-drift average run length reaches `minimum`."""
    eligible = [h for h in sorted(arl) if arl[h]["arl"] >= minimum]
    return eligible[0] if eligible else None


def detection_experiment(
    series: pd.Series,
    *,
    h: float,
    drifts=DRIFTS,
    horizons=DETECTION_HORIZONS,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> list[dict]:
    """The DL-54 experiment for the CUSUM: every start month with at least 12 months after it,
    a drift growing by `per_month` points from there, an alarm counted if it falls within the
    horizon after the start."""
    starts = range(min_train, len(series) - MIN_MONTHS_AFTER_START + 1)
    rows = []
    for per_month in drifts:
        delays = []
        for start in starts:
            perturbed = perturb_drift(series, start, per_month)
            alarms = cusum_alarm_positions(
                standardized_changes(perturbed.to_numpy(float), min_train=min_train), h=h
            )
            after = [a for a in alarms if a >= start]
            delays.append(after[0] - start if after else None)
        for horizon in horizons:
            found = [d for d in delays if d is not None and d < horizon]
            rows.append(
                {
                    "drift_pp_per_month": per_month,
                    "horizon_months": horizon,
                    "detection_rate": len(found) / len(delays),
                    "median_delay": float(np.median(found)) if found else None,
                    "n_starts": len(delays),
                }
            )
    return rows


def run_m5_cusum(series: pd.Series, *, seed: int = 0, n_runs: int = 2000) -> dict:
    changes = np.diff(series.to_numpy(float))
    arl = simulate_arl(changes, seed=seed, n_runs=n_runs)
    h = choose_threshold(arl)
    out = {"k": CUSUM_K, "h_grid": list(H_GRID), "arl": {str(k): v for k, v in arl.items()}}
    if h is None:
        return {**out, "h": None, "detection": [], "real_alarms": [], "passes": False}
    detection = detection_experiment(series, h=h)
    z = standardized_changes(series.to_numpy(float))
    alarms = [int(series.index[i]) for i in cusum_alarm_positions(z, h=h)]
    at_pass = next(
        r["detection_rate"]
        for r in detection
        if r["drift_pp_per_month"] == PASS_DRIFT and r["horizon_months"] == DETECTION_HORIZONS[0]
    )
    return {
        **out,
        "h": float(h),
        "detection": detection,
        "real_alarms": alarms,
        "detection_at_pass_point": float(at_pass),
        "passes": bool(at_pass >= PASS_DETECTION),
    }


def top_overlap(
    predicted_share,
    realised_share,
    previous_visits,
    realised_visits,
    market_before,
    market_now,
    *,
    k: int = TOP_K,
    minimum: int = MIN_VISITS_RANK,
) -> float | None:
    """Overlap (0 to 1) between the predicted and realised top-k segments by headroom, among
    segments with at least `minimum` visits last month; None if fewer than k are eligible."""
    previous_visits = np.asarray(previous_visits, dtype=float)
    eligible = np.flatnonzero(previous_visits >= minimum)
    if len(eligible) < k:
        return None
    predicted = previous_visits[eligible] * np.maximum(
        0.0, market_before - np.asarray(predicted_share, dtype=float)[eligible]
    )
    realised = np.asarray(realised_visits, dtype=float)[eligible] * np.maximum(
        0.0, market_now - np.asarray(realised_share, dtype=float)[eligible]
    )
    top_p = set(np.argsort(-predicted, kind="stable")[:k])
    top_r = set(np.argsort(-realised, kind="stable")[:k])
    return len(top_p & top_r) / k


def decision_metric(preds: pd.DataFrame, *, n_boot: int = 2000, seed: int = 0) -> dict:
    """Mean top-10 overlap of the model and of the last-month-share baseline, with a whole-month
    bootstrap of the difference. Reported only; there is no pass or fail."""
    previous = np.expm1(preds["seg_log_visits_lag1"].to_numpy(float))
    share = preds["z"].to_numpy(float) / preds["t"].to_numpy(float)
    months = sorted(preds["month_id"].unique())
    model, baseline, kept = [], [], []
    for month in months:
        mask = (preds["month_id"] == month).to_numpy()
        common = {
            "realised_share": share[mask],
            "previous_visits": previous[mask],
            "realised_visits": preds["t"].to_numpy(float)[mask],
            "market_before": float(preds["market_share_lag1"].to_numpy(float)[mask][0]),
            "market_now": float(preds["y_market"].to_numpy(float)[mask][0]),
        }
        m = top_overlap(preds["p"].to_numpy(float)[mask], **common)
        b = top_overlap(preds["seg_share_lag1"].to_numpy(float)[mask], **common)
        if m is None or b is None:
            continue
        model.append(m)
        baseline.append(b)
        kept.append(month)
    model, baseline = np.array(model), np.array(baseline)
    diff = model - baseline
    draws = np.random.default_rng(seed).integers(0, len(diff), (n_boot, len(diff)))
    boot = diff[draws].mean(axis=1)
    return {
        "k": TOP_K,
        "min_visits_last_month": MIN_VISITS_RANK,
        "n_months": int(len(diff)),
        "model_overlap": float(model.mean()),
        "baseline_overlap": float(baseline.mean()),
        "difference": float(diff.mean()),
        "difference_interval": [float(x) for x in np.percentile(boot, [5, 95])],
    }


# ------------------------------------------------------------------------ the real run


def run_all(*, n_boot: int = 2000, seed: int = 0, n_jobs: int = 3, log=print) -> dict:
    from sqlalchemy import create_engine

    from oa_market_intelligence.analysis.competitor_shares import competitor_tables
    from oa_market_intelligence.features.segment import load_segment_gold
    from oa_market_intelligence.features.segment_task import build_task_a_rows
    from oa_market_intelligence.modeling.forecast import load_share_series

    root = Path(__file__).resolve().parents[3]
    engine = create_engine(f"sqlite:///{(root / 'data' / 'published' / 'warehouse.db').as_posix()}")
    rows = build_task_a_rows(load_segment_gold(engine))
    log(f"Task A rows: {len(rows)}; scenarios: {list(SCENARIOS)}")

    def scoped(months):
        out = rows if months is None else rows[~rows["month_id"].between(*months)]
        return out.reset_index(drop=True)

    walked = Parallel(n_jobs=n_jobs)(
        delayed(reference_predictions)(scoped(months)) for months in SCENARIOS.values()
    )
    preds = dict(zip(SCENARIOS, walked, strict=True))
    log("M1 bias correction")
    m1 = run_m1(preds, n_boot=n_boot, seed=seed)
    main = preds["main"]
    log("M4 intervals")
    series = load_share_series(engine)
    m4 = {
        "task_b": judge_m4_task_b(series, n_boot=n_boot, seed=seed),
        "task_a": judge_m4_task_a(main, n_boot=n_boot, seed=seed),
    }
    log("M5 CUSUM and decision metric")
    m5 = {
        "cusum": run_m5_cusum(series, seed=seed),
        "decision_metric": decision_metric(main, n_boot=n_boot, seed=seed),
    }
    log("M6 competitor shares")
    m6 = competitor_tables(engine)
    result = {"m1": m1, "m4": m4, "m5": m5, "m6": m6, "seed": seed, "n_boot": n_boot}
    return json.loads(json.dumps(result, default=float))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="run the pre-registered round once")
    parser.add_argument("--out", type=Path, default=RESULTS_JSON)
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args(argv)
    if not args.run:
        parser.print_help()
        return 0
    result = run_all(n_jobs=args.jobs)
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print("M1 verdict:", result["m1"]["verdict"])
    print("M4 Task B passes:", result["m4"]["task_b"]["passes"])
    print("M4 Task A passes:", result["m4"]["task_a"]["passes"])
    print("M5 CUSUM passes:", result["m5"]["cusum"]["passes"])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
