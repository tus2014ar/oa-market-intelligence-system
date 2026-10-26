"""T3: the decline alert (docs/modelling_plan.md, section 14).

The question is whether a segment's share, `h` months after the origin, is at least 30% below its
share at the origin. A segment-month is scored only if its origin share is above zero and it had at
least 100 category visits in the origin month (both known at the origin). Candidates: three
baselines (recent drop, small segment, always alert) and three classifiers (logistic regression,
gradient boosting, random forest) on the T2 inputs. The primary score is PR-AUC (average precision)
pooled over the test months; precision, recall and F1 are reported at a threshold that is chosen at
each origin from earlier origins only (`alert_threshold`), never from a test month. A trained model
is promoted if the lower end of its paired PR-AUC gain over the best baseline is above zero at the
corrected percentile, resampling whole outcome months.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from oa_market_intelligence.modeling.horizons import (
    apply_scenario,
    assert_pairing,
    horizon_splits,
    month_index,
    pair_rows,
    promotion_percentile,
    serve_simplest,
)
from oa_market_intelligence.modeling.segment_eval import CLIP
from oa_market_intelligence.modeling.segment_models import (
    GBM_CONFIGS,
    LOGISTIC_CONFIGS,
    RF_BASE,
    RF_CONFIGS,
    SEED,
    _boosting_frame,
    _preprocessor,
    design_frame,
)

FALL = 0.30  # a decline is a share at most this fraction below the origin share
MIN_ORIGIN_VISITS = 100
TOLERANCE = 1e-9  # so a fall of exactly 30% counts despite float rounding
INNER_ORIGINS = 12
MIN_INNER_TRAIN_MONTHS = 12
ALERT_MODELS = ("logistic", "gbm", "rf")
ALERT_ORDER = ("logistic", "gbm", "rf")  # simplest first
ALERT_BASELINES = ("recent_drop", "small_segment", "always_alert")
ALERT_SCENARIOS = (
    "main", "covid_removed", "dip_2024_removed", "pediatrics_removed", "fall_20", "fall_50",
)
FALL_SENSITIVITY = {"fall_20": 0.20, "fall_50": 0.50}
CARRIED_ALERT = ("specialty_name", "segment", "seg_log_visits_lag1")


# --------------------------------------------------------------------------- the label


def origin_visits(rows: pd.DataFrame) -> np.ndarray:
    """Category visits in the origin month, recovered from the stored log."""
    return np.expm1(rows["seg_log_visits_lag1"].to_numpy(float)).round()


def add_alert_label(paired: pd.DataFrame, fall: float = FALL) -> pd.DataFrame:
    """The scored rows with the label `y_alert` (1 if the outcome share is at most (1 - fall)
    times the origin share). Eligibility (origin share above zero, at least 100 origin visits)
    uses origin-month values only."""
    origin_share = paired["seg_share_lag1"].to_numpy(float)
    eligible = (origin_share > 0) & (origin_visits(paired) >= MIN_ORIGIN_VISITS)
    out = paired[eligible].copy()
    outcome = out["y_share"].to_numpy(float)
    out["y_alert"] = (
        outcome <= (1 - fall) * out["seg_share_lag1"].to_numpy(float) * (1 + TOLERANCE)
    ).astype(int)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- the metrics


def average_precision(y, score) -> float:
    """PR-AUC as average precision: the sum over thresholds of the step in recall times the
    precision at that threshold, with tied scores treated as one threshold. NaN if no positives."""
    y, score = np.asarray(y, float), np.asarray(score, float)
    n_positive = y.sum()
    if n_positive == 0:
        return float("nan")
    order = np.argsort(-score, kind="stable")
    s, ordered = score[order], y[order]
    last = np.r_[s[1:] != s[:-1], True]
    tp, fp = np.cumsum(ordered)[last], np.cumsum(1 - ordered)[last]
    precision, recall = tp / (tp + fp), tp / n_positive
    return float(np.sum((recall - np.r_[0.0, recall[:-1]]) * precision))


def alert_counts(y, score, threshold) -> dict:
    """Precision, recall, F1 and the number of alerts when a row alerts if its score is at least
    its threshold (an array, one per row, or a single value)."""
    y = np.asarray(y, float)
    alerts = np.asarray(score, float) >= np.asarray(threshold, float)
    tp = float((alerts * y).sum())
    n_alerts, n_positive = float(alerts.sum()), float(y.sum())
    precision = tp / n_alerts if n_alerts else float("nan")
    recall = tp / n_positive if n_positive else float("nan")
    denominator = 2 * tp + (n_alerts - tp) + (n_positive - tp)
    f1 = 2 * tp / denominator if denominator else float("nan")
    return {"precision": precision, "recall": recall, "f1": f1, "n_alerts": int(n_alerts),
            "n_positive": int(n_positive), "n_rows": int(len(y))}


def alert_threshold(score, y) -> float:
    """The score cut-off that maximises F1 over the given predictions (a row alerts if its score
    is at least the cut-off). Ties in F1 go to the higher cut-off. With no positive in the
    predictions there is nothing to learn from, so the cut-off is +inf (no alerts)."""
    score, y = np.asarray(score, float), np.asarray(y, float)
    if len(y) == 0 or y.sum() == 0:
        return float("inf")
    order = np.argsort(-score, kind="stable")
    s, ordered = score[order], y[order]
    last = np.r_[s[1:] != s[:-1], True]
    tp, fp = np.cumsum(ordered)[last], np.cumsum(1 - ordered)[last]
    f1 = 2 * tp / (2 * tp + fp + (y.sum() - tp))
    best = int(np.argmax(f1))  # the first maximum is the highest cut-off
    return float(s[last][best])


# --------------------------------------------------------------------------- baselines


def recent_drop_score(rows: pd.DataFrame) -> np.ndarray:
    """The segment's 3-month mean share over its share at the origin: above 1 when the latest
    month is below the recent average."""
    return rows["seg_share_roll3"].to_numpy(float) / rows["seg_share_lag1"].to_numpy(float)


def small_segment_score(rows: pd.DataFrame) -> np.ndarray:
    """Fewer visits at the origin means a higher score: sampling noise alone."""
    return -rows["seg_log_visits_lag1"].to_numpy(float)


def always_alert_score(rows: pd.DataFrame) -> np.ndarray:
    return np.ones(len(rows))


BASELINE_SCORES = {
    "recent_drop": recent_drop_score,
    "small_segment": small_segment_score,
    "always_alert": always_alert_score,
}


class BaselineAlert:
    """A baseline with the alert protocol: `(scores, threshold)`. The cut-off comes from the last
    `INNER_ORIGINS` outcome months of the training rows (scores need no fitting). `always_alert`
    has the cut-off -inf, so it alerts on every row."""

    def __init__(self, name: str):
        self.name = name
        self.log: list[dict] = []

    def __call__(self, train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, float]:
        fn = BASELINE_SCORES[self.name]
        scores = fn(test)
        if self.name == "always_alert":
            return scores, float("-inf")
        months = sorted(train["month_id"].unique())[-INNER_ORIGINS:]
        inner = train[train["month_id"].isin(months)]
        threshold = alert_threshold(fn(inner), inner["y_alert"])
        self.log.append({"threshold": threshold})
        return scores, threshold


# --------------------------------------------------------------------------- classifiers


def binary_log_loss(y, p) -> float:
    y, p = np.asarray(y, float), np.clip(np.asarray(p, float), CLIP, 1 - CLIP)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def _constant_if_one_class(y: np.ndarray):
    """A predictor of the single class seen, or None when both classes are present."""
    return None if len(np.unique(y)) > 1 else (lambda test, v=float(y[0]): np.full(len(test), v))


def _fitter_logistic(config: dict, extra: tuple[str, ...]) -> Callable:
    def fit(train: pd.DataFrame) -> Callable:
        y = train["y_alert"].to_numpy(int)
        constant = _constant_if_one_class(y)
        if constant:
            return constant
        model = Pipeline(
            [("prep", _preprocessor(scale=True, extra_columns=extra)),
             ("clf", LogisticRegression(max_iter=2000, **config))]
        )
        model.fit(design_frame(train, extra), y)
        return lambda test: model.predict_proba(design_frame(test, extra))[:, 1]

    return fit


def _fitter_gbm(config: dict, extra: tuple[str, ...]) -> Callable:
    def fit(train: pd.DataFrame) -> Callable:
        y = train["y_alert"].to_numpy(int)
        constant = _constant_if_one_class(y)
        if constant:
            return constant
        encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan)
        encoder.fit(train[["specialty_grouped"]].astype(str))
        X = _boosting_frame(train, encoder, extra)
        model = HistGradientBoostingClassifier(
            categorical_features=[X.columns.get_loc("specialty_grouped")],
            max_iter=200, random_state=SEED, early_stopping=False, loss="log_loss", **config,
        )
        model.fit(X, y)
        return lambda test: model.predict_proba(_boosting_frame(test, encoder, extra))[:, 1]

    return fit


def _fitter_rf(config: dict, extra: tuple[str, ...]) -> Callable:
    def fit(train: pd.DataFrame) -> Callable:
        y = train["y_alert"].to_numpy(int)
        constant = _constant_if_one_class(y)
        if constant:
            return constant
        model = Pipeline(
            [("prep", _preprocessor(scale=False, extra_columns=extra)),
             ("clf", RandomForestClassifier(**{**RF_BASE, **config}))]
        )
        model.fit(design_frame(train, extra), y)
        return lambda test: model.predict_proba(design_frame(test, extra))[:, 1]

    return fit


_ALERT_BUILDERS = {"logistic": _fitter_logistic, "gbm": _fitter_gbm, "rf": _fitter_rf}
ALERT_CONFIGS = {"logistic": LOGISTIC_CONFIGS, "gbm": GBM_CONFIGS, "rf": RF_CONFIGS}


def tune_alert_config(
    train: pd.DataFrame, configs: list[dict], factory: Callable[[dict], Callable],
    *, val_months: int = 12,
) -> int:
    """Index of the setting with the lowest validation log-loss. The last `val_months` outcome
    months of `train` are the validation block; each setting is fitted on the months before it.
    A tie keeps the first (simplest) setting."""
    months = sorted(train["month_id"].unique())
    if len(months) <= val_months:
        raise ValueError("not enough training months to hold out a validation block")
    block = months[-val_months:]
    fit_rows = train[train["month_id"] < block[0]]
    val_rows = train[train["month_id"].isin(block)]
    visible = val_rows.drop(columns=[c for c in val_rows.columns if c.startswith("y_")])
    losses = [
        binary_log_loss(val_rows["y_alert"], factory(config)(fit_rows)(visible))
        for config in configs
    ]
    return int(np.argmin(losses))


class TunedAlertModel:
    """A classifier with the alert protocol: `(scores, threshold)`.

    The setting is re-chosen every `retune_every` origins on log-loss inside the training window.
    The alert cut-off is chosen at each origin from earlier origins only: for each of the last
    `INNER_ORIGINS` outcome months of the training rows, a model is fitted only on the rows whose
    outcome month is at or before that month's origin (outcome month minus the horizon), skipping
    inner origins with fewer than `MIN_INNER_TRAIN_MONTHS` training months, and the cut-off is the
    F1-maximising score over those pooled predictions. Inner predictions depend only on the
    setting and the inner month, so they are cached."""

    def __init__(self, model: str, horizon: int, *, extra_columns: tuple[str, ...] = (),
                 configs: list[dict] | None = None, retune_every: int = 6, val_months: int = 12):
        self.model, self.horizon = model, horizon
        self.configs = configs if configs is not None else ALERT_CONFIGS[model]
        self.extra = tuple(extra_columns)
        self.retune_every, self.val_months = retune_every, val_months
        self.log: list[dict] = []
        self._chosen = 0
        self._inner: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    def _factory(self, config: dict) -> Callable:
        return _ALERT_BUILDERS[self.model](config, self.extra)

    def _threshold(self, train: pd.DataFrame, config: dict) -> tuple[float, int]:
        key = tuple(sorted(config.items()))
        outcome_idx = month_index(train["month_id"].to_numpy())
        months = sorted(train["month_id"].unique())[-INNER_ORIGINS:]
        scores, labels = [], []
        for month in months:
            origin = int(month_index(month)) - self.horizon
            fit_mask = outcome_idx <= origin
            if len(np.unique(train.loc[fit_mask, "month_id"])) < MIN_INNER_TRAIN_MONTHS:
                continue
            if (key, int(month)) not in self._inner:
                fit_rows = train[fit_mask]
                target = train[train["month_id"] == month]
                visible = target.drop(columns=[c for c in target.columns if c.startswith("y_")])
                self._inner[(key, int(month))] = (
                    self._factory(config)(fit_rows)(visible), target["y_alert"].to_numpy(int)
                )
            s, y = self._inner[(key, int(month))]
            scores.append(s)
            labels.append(y)
        if not scores:
            return float("inf"), 0
        return alert_threshold(np.concatenate(scores), np.concatenate(labels)), len(scores)

    def __call__(self, train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, float]:
        retune = len(self.log) % self.retune_every == 0
        if retune:
            self._chosen = tune_alert_config(
                train, self.configs, self._factory, val_months=self.val_months
            )
        config = self.configs[self._chosen]
        scores = self._factory(config)(train)(test)
        threshold, n_inner = self._threshold(train, config)
        self.log.append({"config": config, "retuned": retune, "threshold": threshold,
                         "n_inner_origins": n_inner})
        return np.asarray(scores, float), threshold


def alert_predictor(name: str, horizon: int, extra_columns: tuple[str, ...]):
    if name in BASELINE_SCORES:
        return BaselineAlert(name)
    return TunedAlertModel(name, horizon, extra_columns=extra_columns)


# --------------------------------------------------------------------------- the walk


def walk_forward_alerts(paired: pd.DataFrame, predictor, horizon: int) -> pd.DataFrame:
    """The scores, labels and cut-offs of every test row. The predictor sees the training rows
    (outcomes included, all with an outcome month at or before the origin) and the test rows
    with every `y_` column removed."""
    frames = []
    for _, train_idx, test_idx in horizon_splits(horizon)(paired):
        train, test = paired.iloc[train_idx], paired.iloc[test_idx]
        visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
        scores, threshold = predictor(train, visible)
        out = pd.DataFrame(
            {
                "month_id": test["month_id"].to_numpy(),
                "score": np.asarray(scores, float),
                "label": test["y_alert"].to_numpy(int),
                "threshold": threshold,
            }
        )
        for column in CARRIED_ALERT:
            if column in test.columns:
                out[column] = test[column].to_numpy()
        frames.append(out)
    return pd.concat(frames, ignore_index=True)


def walk_alert_method(
    paired: pd.DataFrame, horizon: int, name: str, extra_columns: tuple[str, ...]
) -> pd.DataFrame:
    return walk_forward_alerts(paired, alert_predictor(name, horizon, extra_columns), horizon)


# --------------------------------------------------------------------------- the judgement


def month_groups(preds: pd.DataFrame) -> list[np.ndarray]:
    """Row positions of each outcome month, in month order (the resampling unit)."""
    positions = np.arange(len(preds))
    month = preds["month_id"].to_numpy()
    return [positions[month == m] for m in sorted(preds["month_id"].unique())]


def ap_bootstrap(
    preds_by_method: dict[str, pd.DataFrame], *, n_boot: int = 2000, seed: int = 0
) -> dict[str, np.ndarray]:
    """PR-AUC of every method on the same resampled sets of whole outcome months. Every method
    must have the same rows in the same order."""
    names = list(preds_by_method)
    first = preds_by_method[names[0]]
    for name in names:
        other = preds_by_method[name]
        if not (other["month_id"].to_numpy() == first["month_id"].to_numpy()).all():
            raise ValueError("every method must be scored on the same test rows")
    groups = month_groups(first)
    draws = np.random.default_rng(seed).integers(0, len(groups), (n_boot, len(groups)))
    labels = {name: preds_by_method[name]["label"].to_numpy(int) for name in names}
    scores = {name: preds_by_method[name]["score"].to_numpy(float) for name in names}
    out = {name: np.empty(n_boot) for name in names}
    for b in range(n_boot):
        rows = np.concatenate([groups[g] for g in draws[b]])
        for name in names:
            out[name][b] = average_precision(labels[name][rows], scores[name][rows])
    return out


def method_report(preds: pd.DataFrame) -> dict:
    """PR-AUC and the precision, recall, F1 and alert count at each origin's own cut-off."""
    y, score = preds["label"].to_numpy(int), preds["score"].to_numpy(float)
    return {"pr_auc": average_precision(y, score),
            **alert_counts(y, score, preds["threshold"].to_numpy(float))}


def judge_alerts(
    preds: dict[str, pd.DataFrame], horizon: int, *, n_boot: int = 2000, seed: int = 0
) -> dict:
    """The best baseline (highest PR-AUC on the full test set), each trained model's paired gain
    over it, the promotions and the serving model."""
    lower = promotion_percentile(horizon)
    reports = {name: method_report(frame) for name, frame in preds.items()}
    baselines = [n for n in ALERT_BASELINES if n in preds]
    trained = [n for n in ALERT_MODELS if n in preds]
    best = max(baselines, key=lambda n: reports[n]["pr_auc"])
    draws = ap_bootstrap({n: preds[n] for n in (best, *trained)}, n_boot=n_boot, seed=seed)
    models = {}
    for name in trained:
        gain = draws[name] - draws[best]
        valid = gain[~np.isnan(gain)]
        low, high = np.percentile(valid, [lower, 100 - lower])
        estimate = reports[name]["pr_auc"] - reports[best]["pr_auc"]
        models[name] = {
            "estimate": float(estimate), "low": float(low), "high": float(high),
            "share_positive": float((valid > 0).mean()), "n_valid": int(len(valid)),
            "promoted": bool(low > 0),
        }
    promoted = [n for n in trained if models[n]["promoted"]]
    pooled_ap = {n: reports[n]["pr_auc"] for n in (best, *trained)}
    tie = serve_simplest(
        promoted, pooled_ap, draws, order=ALERT_ORDER, lower_percentile=lower,
        higher_is_better=True,
    )
    first = next(iter(preds.values()))
    return {
        "horizon": horizon, "lower_percentile": lower,
        "n_test_months": int(first["month_id"].nunique()),
        "n_rows": int(len(first)), "n_positive": int(first["label"].sum()),
        "decline_share": float(first["label"].mean()),
        "best_baseline": best, "methods": reports, "models": models, "promoted": promoted,
        "serving": tie["serving"] or best, "serving_is_baseline": tie["serving"] is None,
        "serving_reason": tie["reason"],
    }


def run_alerts_horizon(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    horizon: int,
    *,
    extra_columns: tuple[str, ...] = (),
    scenarios: tuple[str, ...] = ALERT_SCENARIOS,
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """One horizon: every method through the walk-forward in every scenario, then the judgement.
    The 20% and 50% scenarios change only the label rule (reported, outside promotion)."""
    paired_all = pair_rows(rows, outcomes, horizon)
    assert_pairing(paired_all, horizon)
    methods = [*ALERT_BASELINES, *ALERT_MODELS]
    out: dict = {"horizon": horizon, "scenarios": {}}
    for scenario in scenarios:
        fall = FALL_SENSITIVITY.get(scenario, FALL)
        base = paired_all if scenario in FALL_SENSITIVITY else apply_scenario(paired_all, scenario)
        paired = add_alert_label(base, fall)
        progress(f"T3 h={horizon} {scenario}: {len(paired)} scored rows")
        results = Parallel(n_jobs=n_jobs)(
            delayed(walk_alert_method)(paired, horizon, name, extra_columns) for name in methods
        )
        preds = dict(zip(methods, results, strict=True))
        out["scenarios"][scenario] = judge_alerts(preds, horizon, n_boot=n_boot, seed=seed)
        if scenario == "main":
            out["_preds"] = preds
    return out
