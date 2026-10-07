"""Q4: how often do the predictions match, and when should a review flag be raised?

Two monitors, as fixed in the Phase 4 plan (DL-54):

(a) the direction classifier's hit series, its rolling 6-month accuracy and a review threshold
    set at the 5th percentile of that accuracy under stable performance (a moving-block
    bootstrap of the hits), with a stricter line at what random guessing produces;
(b) an alarm on the served forecast when its prediction interval is missed in too many of the
    last months. Two rules are compared on the real backtest and on simulated degradations
    (more volatility, steady drift) of the real series, and the better one is chosen by a
    criterion fixed beforehand.

A forecaster that always predicts last month adapts to a level shift after one month, so an
interval alarm cannot see a level shift; it sees volatility and fast drift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import binom
from sqlalchemy import Engine, text

from oa_market_intelligence.features.monthly import (
    compute_monthly_features,
    load_monthly_gold,
    model_ready_monthly,
)
from oa_market_intelligence.modeling.baselines import (
    MajorityClassBaseline,
    PersistenceBaseline,
    SeasonalBaseline,
)
from oa_market_intelligence.modeling.evaluation import compare_models
from oa_market_intelligence.modeling.forecast import naive_forecaster, walk_forward_forecast
from oa_market_intelligence.modeling.models import make_logistic_regression

RULES = {
    "R90": {"level": "90", "k": 3, "n": 6},
    "R80": {"level": "80", "k": 4, "n": 6},
}
DEFAULT_SCENARIOS = {
    "volatility 0.15": ("volatility", 0.15),
    "volatility 0.30": ("volatility", 0.30),
    "drift 0.1": ("drift", 0.1),
    "drift 0.2": ("drift", 0.2),
    "drift 0.3": ("drift", 0.3),
}
HORIZON = 6
MIN_MONTHS_AFTER_START = 12


def hit_series(y_true, y_pred) -> np.ndarray:
    """1 where the predicted direction equals the actual one, else 0."""
    return (np.asarray(y_true) == np.asarray(y_pred)).astype(int)


def rolling_accuracy(hits: np.ndarray, window: int) -> np.ndarray:
    """Hit rate over every window of `window` consecutive months."""
    sums = np.convolve(np.asarray(hits, dtype=float), np.ones(window), mode="valid")
    return sums / window


def _block_indices(n: int, block_length: int, n_boot: int, rng) -> np.ndarray:
    n_blocks = -(-n // block_length)
    starts = rng.integers(0, n - block_length + 1, (n_boot, n_blocks))
    return (starts[:, :, None] + np.arange(block_length)).reshape(n_boot, -1)[:, :n]


def _rolling_matrix(samples: np.ndarray, window: int) -> np.ndarray:
    cumulative = np.concatenate([np.zeros((len(samples), 1)), np.cumsum(samples, axis=1)], axis=1)
    return (cumulative[:, window:] - cumulative[:, :-window]) / window


def stable_threshold(
    hits: np.ndarray,
    *,
    window: int = 6,
    block_length: int = 3,
    n_boot: int = 2000,
    seed: int = 0,
    percentile: float = 5.0,
) -> dict:
    """The review threshold: the `percentile` point of the rolling accuracy of resampled copies
    of the hit series, i.e. how low the accuracy dips by chance when performance is stable."""
    hits = np.asarray(hits, dtype=float)
    rng = np.random.default_rng(seed)
    samples = hits[_block_indices(len(hits), block_length, n_boot, rng)]
    rolling = _rolling_matrix(samples, window)
    rates = samples.mean(axis=1)
    return {
        "threshold": float(np.percentile(rolling, percentile)),
        "match_rate": float(hits.mean()),
        "rate_low": float(np.percentile(rates, 5)),
        "rate_high": float(np.percentile(rates, 95)),
        "n": int(len(hits)),
    }


def random_guess_line(labels, *, window: int = 6, n_runs: int = 1000, seed: int = 0,
                      percentile: float = 5.0) -> dict:
    """The `percentile` point of the rolling accuracy of guessing classes at random in their
    observed proportions: a flag below this line means worse than what luck produces."""
    labels = np.asarray(labels)
    classes, counts = np.unique(labels, return_counts=True)
    rng = np.random.default_rng(seed)
    guesses = rng.choice(classes, size=(n_runs, len(labels)), p=counts / counts.sum())
    hits = (guesses == labels).astype(float)
    rolling = _rolling_matrix(hits, window)
    return {"line": float(np.percentile(rolling, percentile)),
            "mean_accuracy": float(hits.mean())}


def interval_misses(frame: pd.DataFrame, level: str) -> pd.Series:
    """True for every month whose actual value fell outside the forecast interval."""
    actual = frame["actual"]
    return (actual < frame[f"lo{level}"]) | (actual > frame[f"hi{level}"])


def window_alarm(misses, *, k: int, n: int) -> np.ndarray:
    """For every month from the n-th on: at least `k` misses among the last `n` months."""
    counts = np.convolve(np.asarray(misses, dtype=float), np.ones(n), mode="valid")
    return counts >= k


def false_alarm_probability(p: float, k: int, n: int) -> float:
    """Chance of at least k misses in n months if each month misses with probability p."""
    return float(binom.sf(k - 1, n, p))


def empirical_false_alarm(
    misses, *, k: int, n: int, block_length: int = 3, n_boot: int = 2000, seed: int = 0
) -> float:
    """Share of n-month windows that alarm in block-bootstrap copies of the real miss record."""
    misses = np.asarray(misses, dtype=float)
    rng = np.random.default_rng(seed)
    samples = misses[_block_indices(len(misses), block_length, n_boot, rng)]
    return float((_rolling_matrix(samples, n) * n >= k - 1e-9).mean())


def perturb_drift(series: pd.Series, start: int, per_month: float) -> pd.Series:
    """Subtract a steadily growing amount from `start` onward (position in the series)."""
    out = series.copy()
    steps = np.arange(1, len(series) - start + 1)
    out.iloc[start:] = out.iloc[start:].to_numpy() - per_month * steps
    return out


def perturb_volatility(series: pd.Series, start: int, sd: float, rng) -> pd.Series:
    """Add independent noise with standard deviation `sd` from `start` onward."""
    out = series.copy()
    out.iloc[start:] = out.iloc[start:].to_numpy() + rng.normal(0, sd, len(series) - start)
    return out


def _delays(series, start, rules, min_train, horizon):
    """For each rule, months from `start` to its first alarm within `horizon` months (or None)."""
    forecast = walk_forward_forecast(series, naive_forecaster, min_train=min_train)
    out = {}
    for name in rules:
        rule = RULES[name]
        alarm = window_alarm(interval_misses(forecast, rule["level"]).to_numpy(),
                             k=rule["k"], n=rule["n"])
        positions = min_train + np.flatnonzero(alarm) + rule["n"] - 1
        hit = positions[(positions >= start) & (positions < start + horizon)]
        out[name] = int(hit[0] - start) if len(hit) else None
    return out


def detection_experiment(
    series: pd.Series,
    *,
    scenarios: dict | None = None,
    rules: tuple[str, ...] = ("R90", "R80"),
    min_train: int = 24,
    horizon: int = HORIZON,
    n_seeds: int = 20,
    seed: int = 0,
) -> pd.DataFrame:
    """How quickly each rule alarms when the series degrades from each possible start month.

    Every start with at least 12 months of data after it is used. Volatility scenarios are
    repeated over `n_seeds` noise draws; drift scenarios are deterministic."""
    scenarios = scenarios or DEFAULT_SCENARIOS
    starts = range(min_train, len(series) - MIN_MONTHS_AFTER_START + 1)
    rows = []
    for label, (kind, size) in scenarios.items():
        delays = {name: [] for name in rules}
        runs = 0
        for start in starts:
            repeats = n_seeds if kind == "volatility" else 1
            for repeat in range(repeats):
                if kind == "volatility":
                    rng = np.random.default_rng([seed, start, repeat])
                    perturbed = perturb_volatility(series, start, size, rng)
                else:
                    perturbed = perturb_drift(series, start, size)
                found = _delays(perturbed, start, rules, min_train, horizon)
                runs += 1
                for name in rules:
                    delays[name].append(found[name])
        for name in rules:
            detected = [d for d in delays[name] if d is not None]
            rows.append(
                {"scenario": label, "rule": name, "detection_rate": len(detected) / runs,
                 "median_delay": float(np.median(detected)) if detected else float("nan"),
                 "n_starts": len(starts)}
            )
    return pd.DataFrame(rows)


def choose_rule(results: pd.DataFrame, backtest_alarms: dict[str, int]) -> dict:
    """The rule with the higher average detection rate across scenarios, provided it raised no
    alarm in the real backtest; ties go to R90 (the order of `RULES`)."""
    average = results.groupby("rule")["detection_rate"].mean().to_dict()
    eligible = [name for name in RULES if name in average and backtest_alarms.get(name, 0) == 0]
    if not eligible:
        return {"rule": None, "average_detection": average}
    best = max(eligible, key=lambda name: (average[name], -list(RULES).index(name)))
    return {"rule": best, "average_detection": average}


def direction_backtest(engine: Engine, *, min_train: int = 24) -> pd.DataFrame:
    """Walk-forward direction predictions of the baselines and the logistic regression, one row
    per test month: `y_true` and one column per model."""
    ready = model_ready_monthly(compute_monthly_features(load_monthly_gold(engine)))
    models = {
        "always-majority": MajorityClassBaseline,
        "persistence": PersistenceBaseline,
        "seasonal": SeasonalBaseline,
        "logistic": make_logistic_regression,
    }
    return compare_models(ready, models, min_train=min_train)


def write_backtest_predictions(engine: Engine, frame: pd.DataFrame, model_version: str) -> None:
    """Record predictions in the Gold placeholder columns. `frame` has one row per predicted
    (target) month: `month_id`, `predicted`, `actual` and `probability` (NaN if the model gives
    none). The row of the month before the target holds the prediction made with data through
    that month and the target month's actual direction."""
    with engine.begin() as conn:
        months = [r[0] for r in conn.execute(
            text("SELECT month_id FROM gold_visit_share_monthly ORDER BY month_id")
        )]
        for row in frame.itertuples(index=False):
            earlier = [m for m in months if m < row.month_id]
            if not earlier:
                continue
            probability = None if pd.isna(row.probability) else float(row.probability)
            conn.execute(
                text(
                    "UPDATE gold_visit_share_monthly SET predicted_direction = :predicted, "
                    "prediction_probability = :probability, actual_direction = :actual, "
                    "model_version = :version WHERE month_id = :month"
                ),
                {"predicted": row.predicted, "probability": probability,
                 "actual": row.actual, "version": model_version, "month": earlier[-1]},
            )
