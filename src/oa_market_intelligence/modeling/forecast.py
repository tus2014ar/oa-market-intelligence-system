"""Task B: forecast next month's Zilretta visit share, with 80% and 90% intervals.

One value per month (percentage points), walk-forward: the forecast for month t sees only the
months before it. Two baselines (last month, same month last year), two ETS models (damped trend,
and damped trend with yearly seasonality) and a ridge regression on lags. Intervals come from the
model itself (ETS), from the model's own past one-step errors (ridge) or from the baseline's
(the empirical percentiles of those errors). Forecast errors in one series are serially
dependent, so the comparison with the best baseline uses a moving-block bootstrap.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine
from statsmodels.tsa.exponential_smoothing.ets import ETSModel

DEFAULT_MIN_TRAIN = 24
SEASON = 12
LOWER_PERCENTILE = 1.67  # Bonferroni for three trained models (DL-41)
COMPLEXITY_ORDER = ("ets_damped", "ridge", "ets_seasonal")
_PERCENTILES = {"80": (10, 90), "90": (5, 95)}
_ALPHA = {"80": 0.2, "90": 0.1}


def load_share_series(engine: Engine) -> pd.Series:
    """Monthly Zilretta visit share from Gold, in percentage points."""
    with engine.connect() as conn:
        frame = pd.read_sql(
            "SELECT month_id, visit_share FROM gold_visit_share_monthly ORDER BY month_id", conn
        )
    return pd.Series(frame["visit_share"].to_numpy(float) * 100, index=frame["month_id"].to_numpy())


def empirical_interval(point: float, errors: np.ndarray) -> dict:
    """The forecast plus percentiles of past one-step errors (actual minus forecast)."""
    errors = np.asarray(errors, dtype=float)
    out = {"point": float(point), "fallback": False}
    for level, (low, high) in _PERCENTILES.items():
        out[f"lo{level}"] = float(point + np.percentile(errors, low))
        out[f"hi{level}"] = float(point + np.percentile(errors, high))
    return out


def naive_forecaster(history: pd.Series, target_month: int) -> dict:
    """Last month's value, with an interval from the one-step changes seen so far."""
    values = history.to_numpy(float)
    return empirical_interval(values[-1], np.diff(values))


def seasonal_naive_forecaster(history: pd.Series, target_month: int) -> dict:
    """The value twelve months before the target, with an interval from past year-on-year
    changes."""
    values = history.to_numpy(float)
    return empirical_interval(values[-SEASON], values[SEASON:] - values[:-SEASON])


def ets_forecaster(*, seasonal: bool) -> Callable[[pd.Series, int], dict]:
    """ETS with additive error and a damped additive trend (and additive yearly seasonality when
    `seasonal`). Intervals are the model's own prediction intervals."""

    def forecast(history: pd.Series, target_month: int) -> dict:
        values = history.to_numpy(float)
        model = ETSModel(
            pd.Series(values), error="add", trend="add", damped_trend=True,
            seasonal="add" if seasonal else None, seasonal_periods=SEASON if seasonal else None,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = model.fit(disp=False, maxiter=2000)
            prediction = fit.get_prediction(start=len(values), end=len(values))
            frames = {level: prediction.summary_frame(alpha=_ALPHA[level]) for level in _ALPHA}
        out = {"point": float(frames["80"]["mean"].iloc[0]), "fallback": False}
        for level, frame in frames.items():
            out[f"lo{level}"] = float(frame["pi_lower"].iloc[0])
            out[f"hi{level}"] = float(frame["pi_upper"].iloc[0])
        if not all(np.isfinite(v) for k, v in out.items() if k != "fallback"):
            raise ValueError("ETS returned a non-finite forecast")
        return out

    return forecast


class RidgeForecaster:
    """Ridge regression on lags 1 to 3, their mean and month sin/cos. The penalty is chosen from
    {0.1, 1, 10} by one-step rolling-origin error over the last 12 months of the history and
    re-chosen every `retune_every` forecasts; the interval is the empirical percentiles of the
    model's own rolling-origin errors."""

    def __init__(self, alphas=(0.1, 1.0, 10.0), *, retune_every: int = 6, val_months: int = 12,
                 first_origin: int = 12):
        self.alphas = tuple(alphas)
        self.retune_every = retune_every
        self.val_months = val_months
        self.first_origin = first_origin
        self.log: list[dict] = []
        self._alpha = self.alphas[-1]

    @staticmethod
    def _row(values: np.ndarray, month_id: int, j: int) -> list[float]:
        lags = [values[j - 1], values[j - 2], values[j - 3]]
        month = month_id % 100
        return [*lags, float(np.mean(lags)), np.sin(2 * np.pi * month / 12),
                np.cos(2 * np.pi * month / 12)]

    def _table(self, values, month_ids, stop):
        rows = [self._row(values, month_ids[j], j) for j in range(3, stop)]
        return np.array(rows), values[3:stop]

    def _predict_at(self, values, month_ids, origin, alpha):
        """Fit on every target before `origin` and predict the one at `origin`."""
        X, y = self._table(values, month_ids, origin)
        model = make_pipeline(StandardScaler(), Ridge(alpha=alpha)).fit(X, y)
        return float(model.predict(np.array([self._row(values, month_ids[origin], origin)]))[0])

    def _errors(self, values, month_ids, alpha, origins):
        return np.array([values[s] - self._predict_at(values, month_ids, s, alpha)
                         for s in origins])

    def _choose(self, values, month_ids) -> float:
        n = len(values)
        origins = range(max(self.first_origin, n - self.val_months), n)
        scores = {
            alpha: float(np.mean(self._errors(values, month_ids, alpha, origins) ** 2))
            for alpha in self.alphas
        }
        return min(scores, key=lambda alpha: (scores[alpha], -alpha))  # ties: larger alpha

    def __call__(self, history: pd.Series, target_month: int) -> dict:
        values = history.to_numpy(float)
        month_ids = [*history.index.tolist(), target_month]
        extended = np.append(values, np.nan)
        retune = len(self.log) % self.retune_every == 0
        if retune:
            self._alpha = self._choose(values, month_ids[:-1])
        X, y = self._table(values, month_ids, len(values))
        model = make_pipeline(StandardScaler(), Ridge(alpha=self._alpha)).fit(X, y)
        point = float(model.predict(np.array([self._row(extended, target_month, len(values))]))[0])
        errors = self._errors(values, month_ids[:-1], self._alpha,
                              range(self.first_origin, len(values)))
        self.log.append({"retuned": retune, "alpha": self._alpha})
        return empirical_interval(point, errors)


def walk_forward_forecast(
    series: pd.Series,
    forecaster: Callable[[pd.Series, int], dict],
    *,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> pd.DataFrame:
    """Forecast every month after the first `min_train`, each from the months before it only.
    A forecaster that raises falls back to last month's forecast and the fallback is flagged."""
    rows = []
    for i in range(min_train, len(series)):
        history, target = series.iloc[:i], int(series.index[i])
        try:
            out = forecaster(history, target)
        except Exception:  # noqa: BLE001 - a failed fit must not stop the evaluation
            out = {**naive_forecaster(history, target), "fallback": True}
        rows.append(
            {"month_id": target, "actual": float(series.iloc[i]), **out,
             "scale": float(np.mean(np.abs(np.diff(history.to_numpy(float)))))}
        )
    return pd.DataFrame(rows)


def forecast_scores(frame: pd.DataFrame) -> dict:
    """MAE, RMSE, MASE, and for each interval level its coverage, mean width and Winkler score."""
    actual, point = frame["actual"].to_numpy(float), frame["point"].to_numpy(float)
    error = np.abs(actual - point)
    out = {
        "mae": float(error.mean()),
        "rmse": float(np.sqrt((error**2).mean())),
        "mase": float((error / frame["scale"].to_numpy(float)).mean()),
    }
    for level, alpha in _ALPHA.items():
        low, high = frame[f"lo{level}"].to_numpy(float), frame[f"hi{level}"].to_numpy(float)
        out[f"cover{level}"] = float(((actual >= low) & (actual <= high)).mean())
        out[f"width{level}"] = float((high - low).mean())
        penalty = (2 / alpha) * (np.maximum(low - actual, 0) + np.maximum(actual - high, 0))
        out[f"score{level}"] = float((high - low + penalty).mean())
    return out


def block_bootstrap_mae(
    abs_errors: dict[str, np.ndarray], *, block_length: int = 6, n_boot: int = 2000, seed: int = 0
) -> dict[str, np.ndarray]:
    """Mean absolute error of every model on the same moving-block resamples of the months."""
    n = len(next(iter(abs_errors.values())))
    rng = np.random.default_rng(seed)
    n_blocks = -(-n // block_length)
    starts = rng.integers(0, n - block_length + 1, (n_boot, n_blocks))
    index = (starts[:, :, None] + np.arange(block_length)).reshape(n_boot, -1)[:, :n]
    return {name: np.asarray(errors)[index].mean(axis=1) for name, errors in abs_errors.items()}


def improvement_from_draws(
    point_baseline: float,
    point_model: float,
    draws_baseline: np.ndarray,
    draws_model: np.ndarray,
    *,
    lower_percentile: float = LOWER_PERCENTILE,
) -> dict:
    """How much lower the model's MAE is than the baseline's (positive means better), with the
    paired bootstrap interval; `low` is the point the serving rule looks at."""
    improvement = draws_baseline - draws_model
    low, high = np.percentile(improvement, [lower_percentile, 100 - lower_percentile])
    return {"estimate": float(point_baseline - point_model), "low": float(low),
            "high": float(high), "share_positive": float((improvement > 0).mean())}


def serve_with_tie_break_b(
    promoted: list[str],
    pooled_mae: dict[str, float],
    draws: dict[str, np.ndarray],
    *,
    order: tuple[str, ...] = COMPLEXITY_ORDER,
    lower_percentile: float = LOWER_PERCENTILE,
) -> dict:
    """Among promoted models the simplest serves unless a more complex one is clearly better."""
    if not promoted:
        return {"serving": None, "reason": "no promoted model", "comparisons": {}}
    best = min(promoted, key=lambda name: pooled_mae[name])
    comparisons = {}
    for name in sorted(promoted, key=order.index):
        if name == best:
            return {"serving": best, "reason": "lowest error", "comparisons": comparisons}
        low = float(np.percentile(draws[name] - draws[best], lower_percentile))
        comparisons[name] = low
        if low <= 0:
            return {"serving": name, "reason": f"{best} is not clearly better than {name}",
                    "comparisons": comparisons}
    return {"serving": best, "reason": "lowest error", "comparisons": comparisons}


def coverage_gate(coverage: float, *, nominal: float, n: int) -> dict:
    """Is the interval's coverage within two binomial standard errors of its nominal level?"""
    margin = 2 * float(np.sqrt(nominal * (1 - nominal) / n))
    low, high = nominal - margin, nominal + margin
    return {"ok": bool(low <= coverage <= high), "low": low, "high": high}


def run_task_b(
    series: pd.Series,
    *,
    min_train: int = DEFAULT_MIN_TRAIN,
    n_boot: int = 2000,
    block_length: int = 6,
    seed: int = 0,
    trained: tuple[str, ...] = COMPLEXITY_ORDER,
) -> dict:
    """Every baseline and trained model through the walk-forward, then the serving rule."""
    baselines = {"last_month": naive_forecaster, "same_month_last_year": seasonal_naive_forecaster}
    makers = {
        "ets_damped": lambda: ets_forecaster(seasonal=False),
        "ets_seasonal": lambda: ets_forecaster(seasonal=True),
        "ridge": RidgeForecaster,
    }
    forecasters = {**baselines, **{name: makers[name]() for name in trained}}
    forecasts = {
        name: walk_forward_forecast(series, fn, min_train=min_train)
        for name, fn in forecasters.items()
    }
    scores = pd.DataFrame({name: forecast_scores(f) for name, f in forecasts.items()}).T
    n_test = len(next(iter(forecasts.values())))
    for level, nominal in (("80", 0.80), ("90", 0.90)):
        scores[f"gate{level}"] = [
            coverage_gate(c, nominal=nominal, n=n_test)["ok"] for c in scores[f"cover{level}"]
        ]
    best_baseline = min(baselines, key=lambda name: scores.loc[name, "mae"])
    errors = {
        name: (f["actual"] - f["point"]).abs().to_numpy()
        for name, f in forecasts.items() if name in (best_baseline, *trained)
    }
    draws = block_bootstrap_mae(errors, block_length=block_length, n_boot=n_boot, seed=seed)
    summaries = {
        name: improvement_from_draws(
            scores.loc[best_baseline, "mae"], scores.loc[name, "mae"],
            draws[best_baseline], draws[name],
        )
        for name in trained
    }
    promoted = [name for name in trained if summaries[name]["low"] > 0]
    tie = serve_with_tie_break_b(promoted, scores["mae"].to_dict(), draws)
    serving = tie["serving"] or best_baseline
    return {
        "forecasts": forecasts, "scores": scores, "best_baseline": best_baseline,
        "summaries": summaries, "draws": draws,
        "decision": {"promoted": promoted, "serving": serving, "reason": tie["reason"],
                     "best_baseline": best_baseline},
        "fallbacks": {name: int(f["fallback"].sum()) for name, f in forecasts.items()},
    }
