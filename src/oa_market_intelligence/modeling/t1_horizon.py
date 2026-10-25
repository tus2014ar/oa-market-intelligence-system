"""T1: the national share at 1, 3 and 6 months ahead (docs/modelling_plan.md, sections 3 to 6).

One value per month, in percentage points. A forecast at origin o (the last month of the history)
is for the month o + h. Baselines: the value at the origin and the value twelve months before the
target month. Trained: ETS with a damped trend, the same with yearly seasonality, and a ridge
regression fitted directly for the horizon on the share's lags at the origin and the target month's
sine and cosine. Intervals are the model's own (ETS) or empirical percentiles of the model's past
h-step errors. The comparison with the best baseline uses the moving-block bootstrap.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.exponential_smoothing.ets import ETSModel

from oa_market_intelligence.features.groups import DIP_MONTHS
from oa_market_intelligence.modeling.forecast import (
    SEASON,
    block_bootstrap_mae,
    coverage_gate,
    empirical_interval,
    forecast_scores,
    improvement_from_draws,
)
from oa_market_intelligence.modeling.horizons import (
    month_index,
    promotion_percentile,
    serve_simplest,
)

T1_MIN_HISTORY = 24
T1_TRAINED = ("ridge", "ets_damped", "ets_seasonal")
T1_ORDER = ("ridge", "ets_damped", "ets_seasonal")  # simplest first
T1_BASELINES = ("last_month", "same_month_last_year")
_ALPHA = {"80": 0.2, "90": 0.1}
RIDGE_MIN_ROWS = 6


def naive_forecaster(history: pd.Series, target_month: int, horizon: int) -> dict:
    """The value at the origin, with an interval from the past h-step changes."""
    values = history.to_numpy(float)
    return empirical_interval(values[-1], values[horizon:] - values[:-horizon])


def seasonal_naive_forecaster(history: pd.Series, target_month: int, horizon: int) -> dict:
    """The value twelve months before the target month (inside the history for h up to 12), with
    an interval from the past year-on-year changes."""
    values = history.to_numpy(float)
    return empirical_interval(
        values[len(values) + horizon - 1 - SEASON], values[SEASON:] - values[:-SEASON]
    )


def ets_forecaster(*, seasonal: bool) -> Callable[[pd.Series, int, int], dict]:
    """ETS, additive error and damped additive trend (and additive yearly seasonality when
    `seasonal`), with its own h-step point forecast and prediction intervals."""

    def forecast(history: pd.Series, target_month: int, horizon: int) -> dict:
        values = history.to_numpy(float)
        model = ETSModel(
            pd.Series(values), error="add", trend="add", damped_trend=True,
            seasonal="add" if seasonal else None, seasonal_periods=SEASON if seasonal else None,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = model.fit(disp=False, maxiter=2000)
            prediction = fit.get_prediction(start=len(values), end=len(values) + horizon - 1)
            frames = {level: prediction.summary_frame(alpha=_ALPHA[level]) for level in _ALPHA}
        out = {"point": float(frames["80"]["mean"].iloc[-1]), "fallback": False}
        for level, frame in frames.items():
            out[f"lo{level}"] = float(frame["pi_lower"].iloc[-1])
            out[f"hi{level}"] = float(frame["pi_upper"].iloc[-1])
        if not all(np.isfinite(v) for k, v in out.items() if k != "fallback"):
            raise ValueError("ETS returned a non-finite forecast")
        return out

    return forecast


class RidgeDirectForecaster:
    """Ridge regression fitted directly for the horizon: the target month's value on the three most
    recent values at the origin, their mean and the target month's sine and cosine. It is fitted
    on the targets already observed at the origin (target index at most the origin). The penalty is
    chosen from {0.1, 1, 10} by h-step rolling-origin error over the last 12 targets and re-chosen
    every `retune_every` forecasts; the interval is the empirical percentiles of the model's own
    rolling-origin h-step errors."""

    def __init__(self, alphas=(0.1, 1.0, 10.0), *, retune_every: int = 6, val_months: int = 12):
        self.alphas = tuple(alphas)
        self.retune_every = retune_every
        self.val_months = val_months
        self.log: list[dict] = []
        self._alpha = self.alphas[-1]

    @staticmethod
    def _row(values: np.ndarray, target_month: int, origin: int) -> list[float]:
        lags = [values[origin], values[origin - 1], values[origin - 2]]
        month = target_month % 100
        return [*lags, float(np.mean(lags)), np.sin(2 * np.pi * month / 12),
                np.cos(2 * np.pi * month / 12)]

    def _table(self, values, month_ids, horizon, last_target):
        targets = range(horizon + 2, last_target + 1)
        rows = [self._row(values, month_ids[j], j - horizon) for j in targets]
        return np.array(rows), np.array([values[j] for j in targets])

    def _predict_for(self, values, month_ids, horizon, target, alpha) -> float:
        """Fit on the targets observed by the origin `target - horizon`, predict `target`."""
        X, y = self._table(values, month_ids, horizon, target - horizon)
        model = make_pipeline(StandardScaler(), Ridge(alpha=alpha)).fit(X, y)
        row = self._row(values, month_ids[target], target - horizon)
        return float(model.predict(np.array([row]))[0])

    def _first_target(self, horizon: int) -> int:
        return 2 * horizon + 1 + RIDGE_MIN_ROWS  # at least RIDGE_MIN_ROWS rows in the first fit

    def _errors(self, values, month_ids, horizon, alpha, targets):
        return np.array(
            [values[s] - self._predict_for(values, month_ids, horizon, s, alpha) for s in targets]
        )

    def _choose(self, values, month_ids, horizon) -> float:
        n = len(values)
        targets = range(max(self._first_target(horizon), n - self.val_months), n)
        scores = {
            alpha: float(np.mean(self._errors(values, month_ids, horizon, alpha, targets) ** 2))
            for alpha in self.alphas
        }
        return min(scores, key=lambda alpha: (scores[alpha], -alpha))  # ties: larger alpha

    def __call__(self, history: pd.Series, target_month: int, horizon: int) -> dict:
        values = history.to_numpy(float)
        origin = len(values) - 1
        month_ids = [*history.index.tolist(), *[0] * horizon]
        month_ids[origin + horizon] = target_month
        retune = len(self.log) % self.retune_every == 0
        if retune:
            self._alpha = self._choose(values, month_ids, horizon)
        X, y = self._table(values, month_ids, horizon, origin)
        model = make_pipeline(StandardScaler(), Ridge(alpha=self._alpha)).fit(X, y)
        point = float(
            model.predict(np.array([self._row(values, target_month, origin)]))[0]
        )
        errors = self._errors(
            values, month_ids, horizon, self._alpha,
            range(self._first_target(horizon), origin + 1),
        )
        self.log.append({"retuned": retune, "alpha": self._alpha})
        if len(errors) == 0:
            raise ValueError("no past errors to build the interval from")
        return empirical_interval(point, errors)


def naive_fallback(history: pd.Series, target_month: int, horizon: int) -> dict:
    return {**naive_forecaster(history, target_month, horizon), "fallback": True}


def walk_forward_forecast_h(
    series: pd.Series,
    forecaster: Callable[[pd.Series, int, int], dict],
    horizon: int,
    *,
    min_history: int = T1_MIN_HISTORY,
) -> pd.DataFrame:
    """Forecast every month that is `horizon` months after an origin with at least `min_history`
    months of history, each from the history through its origin only. A forecaster that raises
    falls back to the value at the origin and the fallback is flagged."""
    rows = []
    for origin in range(min_history - 1, len(series) - horizon):
        history = series.iloc[: origin + 1]
        target = int(series.index[origin + horizon])
        try:
            out = forecaster(history, target, horizon)
        except Exception:  # noqa: BLE001 - a failed fit must not stop the evaluation
            out = naive_fallback(history, target, horizon)
        rows.append(
            {"month_id": target, "origin_id": int(series.index[origin]),
             "actual": float(series.iloc[origin + horizon]), **out,
             "scale": float(np.mean(np.abs(np.diff(history.to_numpy(float)))))}
        )
    return pd.DataFrame(rows)


def check_series(series: pd.Series) -> None:
    """The series index must be consecutive months, since the horizon is counted in positions."""
    if not (np.diff(month_index(series.index.to_numpy())) == 1).all():
        raise ValueError("the share series must have one value for every consecutive month")


def make_forecasters() -> dict[str, Callable]:
    return {
        "last_month": naive_forecaster,
        "same_month_last_year": seasonal_naive_forecaster,
        "ridge": RidgeDirectForecaster(),
        "ets_damped": ets_forecaster(seasonal=False),
        "ets_seasonal": ets_forecaster(seasonal=True),
    }


def _scenario_mask(frame: pd.DataFrame, scenario: str) -> np.ndarray:
    if scenario == "main":
        return np.ones(len(frame), bool)
    if scenario == "dip_2024_removed":
        return ~frame["month_id"].between(*DIP_MONTHS).to_numpy()
    raise ValueError(f"unknown T1 scenario {scenario!r}")


def judge_t1(
    forecasts: dict[str, pd.DataFrame],
    horizon: int,
    scenario: str = "main",
    *,
    n_boot: int = 2000,
    block_length: int = 6,
    seed: int = 0,
) -> dict:
    """Scores, the best baseline, the paired block bootstrap, promotions and the serving model
    for one scenario. A scenario drops test months from scoring (the forecasts are the same);
    the COVID months precede every test month, so they need no scenario here."""
    mask = _scenario_mask(next(iter(forecasts.values())), scenario)
    scoped = {name: frame[mask].reset_index(drop=True) for name, frame in forecasts.items()}
    scores = pd.DataFrame({name: forecast_scores(f) for name, f in scoped.items()}).T
    n_test = len(next(iter(scoped.values())))
    for level, nominal in (("80", 0.80), ("90", 0.90)):
        scores[f"gate{level}"] = [
            coverage_gate(c, nominal=nominal, n=n_test)["ok"] for c in scores[f"cover{level}"]
        ]
    lower = promotion_percentile(horizon)
    best = min(T1_BASELINES, key=lambda name: scores.loc[name, "mae"])
    errors = {
        name: (scoped[name]["actual"] - scoped[name]["point"]).abs().to_numpy()
        for name in (best, *T1_TRAINED)
    }
    draws = block_bootstrap_mae(errors, block_length=block_length, n_boot=n_boot, seed=seed)
    models = {}
    for name in T1_TRAINED:
        summary = improvement_from_draws(
            scores.loc[best, "mae"], scores.loc[name, "mae"], draws[best], draws[name],
            lower_percentile=lower,
        )
        models[name] = {**summary, "mae": float(scores.loc[name, "mae"]),
                        "promoted": bool(summary["low"] > 0)}
    promoted = [name for name in T1_TRAINED if models[name]["promoted"]]
    tie = serve_simplest(
        promoted, scores["mae"].to_dict(), draws, order=T1_ORDER, lower_percentile=lower,
        higher_is_better=False,
    )
    return {
        "horizon": horizon, "scenario": scenario, "lower_percentile": lower,
        "n_test_months": int(n_test), "best_baseline": best,
        "scores": {name: row.to_dict() for name, row in scores.iterrows()},
        "models": models, "promoted": promoted,
        "serving": tie["serving"] or best, "serving_is_baseline": tie["serving"] is None,
        "serving_reason": tie["reason"],
        "fallbacks": {name: int(f["fallback"].sum()) for name, f in scoped.items()},
    }


def run_t1_horizon(
    series: pd.Series,
    horizon: int,
    *,
    scenarios: tuple[str, ...] = ("main", "dip_2024_removed"),
    n_boot: int = 2000,
    block_length: int = 6,
    seed: int = 0,
    min_history: int = T1_MIN_HISTORY,
) -> dict:
    """Every method through the walk-forward at one horizon, then the judgement per scenario."""
    check_series(series)
    forecasts = {
        name: walk_forward_forecast_h(series, fn, horizon, min_history=min_history)
        for name, fn in make_forecasters().items()
    }
    return {
        "horizon": horizon,
        "scenarios": {
            scenario: judge_t1(forecasts, horizon, scenario, n_boot=n_boot,
                               block_length=block_length, seed=seed)
            for scenario in scenarios
        },
        "_forecasts": forecasts,
    }

