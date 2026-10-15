"""Trained models for Task A, and the tuning that picks their settings.

Three models predict a segment's share next month from last-month features: a binomial
logistic regression, gradient boosting (binomial loss) and a random forest. The logistic and
boosting models are fitted to the visit counts directly, as weighted success and failure rows;
the forest regresses the share with visits as the weight. Weights are scaled so they total the
number of rows, which keeps the regularisation strength comparable across folds.

Tuning follows the plan: inside the training window only, the last 12 months are held out as a
validation block, each setting is fitted on the months before it and scored by log-loss per
visit, the best wins (a tie goes to the simpler setting, which comes first in each list), and
the winner is refitted on the whole window. Settings are re-chosen every 6 test months.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from oa_market_intelligence.features.segment_task import TASK_A_FEATURES
from oa_market_intelligence.modeling.segment_baselines import smoothed
from oa_market_intelligence.modeling.segment_eval import log_loss_per_visit, successes

SEED = 0
# simplest first, so that a tie in validation loss keeps the simpler setting
LOGISTIC_CONFIGS = [{"C": c} for c in (0.01, 0.1, 1.0)]
GBM_CONFIGS = [{"max_depth": d, "learning_rate": lr} for d in (3, 5) for lr in (0.05, 0.1)]
RF_CONFIGS = [{"max_depth": d, "min_samples_leaf": leaf} for d in (4, 8, 12) for leaf in (20, 5)]
MODEL_CONFIGS = {"logistic": LOGISTIC_CONFIGS, "gbm": GBM_CONFIGS, "rf": RF_CONFIGS}
RF_BASE = {"n_estimators": 300, "random_state": SEED, "n_jobs": 1}
_NUMERIC = [
    "logit_lag1", "logit_roll3", "logit_seg_history", "logit_spec_history", "logit_market_lag1",
    "market_change_lag1", "seg_log_visits_lag1", "lag1_x_visits", "age_ordinal",
    "gender_FEMALE", "gender_MALE", "month_sin", "month_cos",
]


def _logit(p, eps: float = 1e-3) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def design_frame(df: pd.DataFrame, extra_columns: tuple[str, ...] = ()) -> pd.DataFrame:
    """Inputs for the logistic regression and the forest: the shares on the logit scale, with
    the history shares smoothed, plus the interaction that lets a model trust a large
    segment's last month more than a small one's."""
    n_lag = np.expm1(df["seg_log_visits_lag1"].to_numpy(float)).round()
    logit_lag1 = _logit(smoothed(df["seg_share_lag1"].to_numpy(float) * n_lag, n_lag))
    out = pd.DataFrame(
        {
            "logit_lag1": logit_lag1,
            "logit_roll3": _logit(df["seg_share_roll3"]),
            "logit_seg_history": _logit(smoothed(df["seg_prior_z"], df["seg_prior_t"])),
            "logit_spec_history": _logit(smoothed(df["spec_prior_z"], df["spec_prior_t"])),
            "logit_market_lag1": _logit(df["market_share_lag1"]),
            "market_change_lag1": df["market_change_lag1"].to_numpy(float),
            "seg_log_visits_lag1": df["seg_log_visits_lag1"].to_numpy(float),
            "lag1_x_visits": logit_lag1 * df["seg_log_visits_lag1"].to_numpy(float),
            "age_ordinal": df["age_ordinal"].to_numpy(float),
            "gender_FEMALE": df["gender_FEMALE"].to_numpy(float),
            "gender_MALE": df["gender_MALE"].to_numpy(float),
            "month_sin": df["month_sin"].to_numpy(float),
            "month_cos": df["month_cos"].to_numpy(float),
        },
        index=df.index,
    )
    for column in extra_columns:  # a candidate family's columns (feature plan), none by default
        out[column] = df[column].to_numpy(float)
    out["specialty_grouped"] = df["specialty_grouped"].astype(str).to_numpy()
    return out


def expand_counts(X: pd.DataFrame, z, t) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """One weighted row for the successes and one for the failures of every input row (zero
    weights dropped), scaled so the weights total the number of input rows."""
    z, t = np.asarray(z, dtype=float), np.asarray(t, dtype=float)
    failures = t - z
    scale = len(X) / t.sum()
    has_success, has_failure = z > 0, failures > 0
    X2 = pd.concat([X[has_success], X[has_failure]], ignore_index=True)
    y2 = np.concatenate([np.ones(has_success.sum()), np.zeros(has_failure.sum())])
    w2 = np.concatenate([z[has_success], failures[has_failure]]) * scale
    return X2, y2, w2


def _preprocessor(scale: bool, extra_columns: tuple[str, ...] = ()) -> ColumnTransformer:
    numeric = [("impute", SimpleImputer(strategy="median"))]
    if scale:
        numeric.append(("scale", StandardScaler()))
    return ColumnTransformer(
        [
            ("num", Pipeline(numeric), [*_NUMERIC, *extra_columns]),
            ("cat", OneHotEncoder(handle_unknown="ignore"), ["specialty_grouped"]),
        ]
    )


def _fitter_logistic(config: dict, overrides: dict, extra: tuple[str, ...] = ()) -> Callable:
    def fit(train: pd.DataFrame) -> Callable:
        X2, y2, w2 = expand_counts(
            design_frame(train, extra), successes(train), train["y_visits"]
        )
        model = Pipeline(
            [
                ("prep", _preprocessor(scale=True, extra_columns=extra)),
                ("clf", LogisticRegression(max_iter=2000, **{**config, **overrides})),
            ]
        )
        model.fit(X2, y2, clf__sample_weight=w2)
        fit.model = model
        return lambda test: model.predict_proba(design_frame(test, extra))[:, 1]

    return fit


def _boosting_frame(
    df: pd.DataFrame, encoder: OrdinalEncoder, extra: tuple[str, ...] = ()
) -> pd.DataFrame:
    X = df[[*TASK_A_FEATURES, *extra]].copy()
    X["specialty_grouped"] = encoder.transform(df[["specialty_grouped"]].astype(str)).ravel()
    return X.astype(float)


def _fitter_gbm(config: dict, overrides: dict, extra: tuple[str, ...] = ()) -> Callable:
    def fit(train: pd.DataFrame) -> Callable:
        encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan)
        encoder.fit(train[["specialty_grouped"]].astype(str))
        X = _boosting_frame(train, encoder, extra)
        X2, y2, w2 = expand_counts(X, successes(train), train["y_visits"])
        settings = {"max_iter": 200, "random_state": SEED, "early_stopping": False,
                    "loss": "log_loss", **config, **overrides}
        model = HistGradientBoostingClassifier(
            categorical_features=[X.columns.get_loc("specialty_grouped")], **settings
        )
        model.fit(X2, y2, sample_weight=w2)
        return lambda test: model.predict_proba(_boosting_frame(test, encoder, extra))[:, 1]

    return fit


def _fitter_rf(config: dict, overrides: dict, extra: tuple[str, ...] = ()) -> Callable:
    def fit(train: pd.DataFrame) -> Callable:
        weights = train["y_visits"].to_numpy(float)
        model = Pipeline(
            [
                ("prep", _preprocessor(scale=False, extra_columns=extra)),
                ("clf", RandomForestRegressor(**{**RF_BASE, **config, **overrides})),
            ]
        )
        model.fit(design_frame(train, extra), train["y_share"].to_numpy(float),
                  clf__sample_weight=weights / weights.mean())
        return lambda test: np.clip(model.predict(design_frame(test, extra)), 0.0, 1.0)

    return fit


_BUILDERS = {"logistic": _fitter_logistic, "gbm": _fitter_gbm, "rf": _fitter_rf}


def fitter_for(
    model: str,
    config: dict,
    *,
    overrides: dict | None = None,
    extra_columns: tuple[str, ...] = (),
) -> Callable:
    """`fit(train)` returning a `predict(test)` for one model with one setting. Fitting once
    and predicting several times is what permutation importance needs."""
    return _BUILDERS[model](config, overrides or {}, tuple(extra_columns))


def fit_predict_for(
    model: str,
    config: dict,
    *,
    overrides: dict | None = None,
    extra_columns: tuple[str, ...] = (),
) -> Callable:
    """`fit_predict(train, test)` for one model with one setting."""
    fit = fitter_for(model, config, overrides=overrides, extra_columns=extra_columns)
    return lambda train, test: fit(train)(test)


def tune_config(
    train: pd.DataFrame,
    configs: list[dict],
    factory: Callable[[dict], Callable],
    *,
    val_months: int = 12,
) -> tuple[int, list[float]]:
    """Index of the best setting and every setting's validation loss. The last `val_months`
    months of `train` are the validation block; each setting is fitted on the months before it."""
    months = sorted(train["month_id"].unique())
    if len(months) <= val_months:
        raise ValueError("not enough training months to hold out a validation block")
    block = months[-val_months:]
    fit_rows = train[train["month_id"] < block[0]]
    val_rows = train[train["month_id"].isin(block)]
    visible = val_rows.drop(columns=[c for c in val_rows.columns if c.startswith("y_")])
    losses = [
        log_loss_per_visit(successes(val_rows), val_rows["y_visits"],
                           factory(config)(fit_rows, visible))
        for config in configs
    ]
    return int(np.argmin(losses)), losses  # the first minimum, so ties keep the simpler setting


class TunedPredictor:
    """A `fit_predict` that re-chooses its setting every `retune_every` calls (test months)."""

    def __init__(
        self,
        model: str,
        configs: list[dict] | None = None,
        *,
        factory: Callable[[dict], Callable] | None = None,
        retune_every: int = 6,
        val_months: int = 12,
        overrides: dict | None = None,
        extra_columns: tuple[str, ...] = (),
    ):
        self.configs = configs if configs is not None else MODEL_CONFIGS[model]
        self.factory = factory or (
            lambda config: fit_predict_for(
                model, config, overrides=overrides, extra_columns=extra_columns
            )
        )
        self.retune_every = retune_every
        self.val_months = val_months
        self.log: list[dict] = []
        self._chosen = 0

    def __call__(self, train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
        retune = len(self.log) % self.retune_every == 0
        if retune:
            self._chosen, _ = tune_config(
                train, self.configs, self.factory, val_months=self.val_months
            )
        self.log.append(
            {"test_month": int(test["month_id"].iloc[0]), "config": self.configs[self._chosen],
             "retuned": retune}
        )
        return self.factory(self.configs[self._chosen])(train, test)
