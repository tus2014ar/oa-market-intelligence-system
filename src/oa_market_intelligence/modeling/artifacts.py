"""Saving the fitted models as readable numbers (DL-117).

The monthly scoring job fits the adopted models and writes their forecasts to `pred_batch`. This
module also saves each fitted logistic regression, as the numbers that define it (the imputing
medians, the scaling, the specialty categories, the coefficients and the intercept), in the
`model_artifacts` table, and the inputs of every segment at the last month in `pred_inputs`. Both
go into the same warehouse file as the forecasts, so a forecast, the model that made it and its
inputs can never be from different months.

Numbers, not a pickled object: they can be read and diffed, need no scikit-learn to apply (the API
does it with NumPy, in `serving.predictor`), and cannot break when a library version changes.

Nothing is saved on trust: before a model is written, the saved numbers are applied to the very
rows that were scored and must give the same probabilities as the fitted model (to 1e-9), or the
run stops.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import sklearn

from oa_market_intelligence.modeling.design import _NUMERIC
from oa_market_intelligence.modeling.segment_models import fitter_for
from oa_market_intelligence.serving.predictor import logistic_predict

ARTIFACT_TABLE = "model_artifacts"
INPUT_TABLE = "pred_inputs"
INPUT_KEYS = ["specialty_name", "age_band", "gender", "specialty_grouped"]
INPUT_FEATURES = [
    "seg_log_visits_lag1",
    "seg_share_lag1",
    "seg_share_roll3",
    "seg_prior_z",
    "seg_prior_t",
    "spec_prior_z",
    "spec_prior_t",
    "market_share_lag1",
    "market_change_lag1",
    "age_ordinal",
    "gender_FEMALE",
    "gender_MALE",
]
TOLERANCE = 1e-9
ARTIFACT_COLUMNS = [
    "model_id", "task", "horizon", "model", "model_version", "run_month_id", "trained_on_rows",
    "data_version", "library", "known_bias", "artifact_sha256", "parameters",
]  # fmt: skip


def export_pipeline(model, extra_columns: tuple[str, ...]) -> dict:
    """The numbers that define a fitted `prep -> logistic regression` pipeline. Stops if it is not
    the shape this module understands (for example, an all-empty column was dropped)."""
    prep, clf = model.named_steps["prep"], model.named_steps["clf"]
    numeric = prep.named_transformers_["num"]
    scaler = numeric.named_steps["scale"]
    columns = [*_NUMERIC, *extra_columns]
    median = numeric.named_steps["impute"].statistics_
    categories = [str(c) for c in prep.named_transformers_["cat"].categories_[0]]
    coef = clf.coef_[0]
    if not (len(median) == len(scaler.mean_) == len(scaler.scale_) == len(columns)):
        raise ValueError("the fitted pipeline does not match its numeric columns")
    if len(coef) != len(columns) + len(categories):
        raise ValueError("the coefficients do not match the columns and the specialty groups")
    return {
        "kind": "logistic",
        "numeric_columns": columns,
        "impute_median": [float(v) for v in median],
        "scale_mean": [float(v) for v in scaler.mean_],
        "scale_scale": [float(v) for v in scaler.scale_],
        "categories": categories,
        "coef": [float(v) for v in coef],
        "intercept": float(clf.intercept_[0]),
        "regularisation_C": float(clf.C),
    }


def verify(parameters: dict, rows: pd.DataFrame, expected, what: str) -> None:
    """The saved numbers, applied to the scored rows, must reproduce the fitted model's output."""
    got = logistic_predict(parameters, rows)
    worst = float(np.max(np.abs(got - np.asarray(expected, float)))) if len(rows) else 0.0
    if worst > TOLERANCE:
        raise ValueError(f"the saved {what} model differs from the fitted one by {worst:.2e}")


def segment_parameters(tuned, name: str, paired, test, extra, predictions) -> dict:
    """Refit the setting the tuner chose on the same rows and save it, after checking that it gives
    the forecasts the job just made."""
    config = tuned.configs[tuned._chosen]
    fit = fitter_for(name, config, extra_columns=extra)
    fit(paired)
    parameters = export_pipeline(fit.model, extra)
    parameters["config"] = config
    verify(parameters, test, predictions, "segment")
    return parameters


def alert_parameters(alert_model, labelled, test, extra, scores, threshold, minimum: int):
    """The same for the decline alert: the saved numbers, the cut-off and the visits floor. None
    when the training rows held one class only (the alert is then a constant, with no model)."""
    config = alert_model.configs[alert_model._chosen]
    fit = alert_model._factory(config)
    fit(labelled)
    if not hasattr(fit, "model"):
        return None
    parameters = export_pipeline(fit.model, extra)
    parameters.update(
        {"config": config, "threshold": float(threshold), "min_origin_visits": int(minimum)}
    )
    verify(parameters, test, scores, "alert")
    return parameters


def _canonical(parameters: dict) -> str:
    return json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)


def build_tables(collect: dict, data_version: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The rows of `model_artifacts` and `pred_inputs` from what the scoring job collected."""
    library = f"scikit-learn {sklearn.__version__}"
    origin = int(collect["origin"])
    rows = []
    for horizon, info in sorted(collect.get("segment", {}).items()):
        parameters = info["parameters"]
        rows.append(_row("segment", horizon, info, parameters, library, origin, data_version))
    alert = collect.get("alert")
    if alert and alert["parameters"]:
        rows.append(_row("alert", 1, alert, alert["parameters"], library, origin, data_version))
    artifacts = pd.DataFrame(rows, columns=ARTIFACT_COLUMNS)
    extra = list(collect.get("extra", ()))
    inputs = collect["inputs"][[*INPUT_KEYS, *INPUT_FEATURES, *extra]].reset_index(drop=True)
    inputs.insert(0, "run_month_id", origin)
    return artifacts, inputs


def _row(task, horizon, info, parameters, library, origin, data_version) -> dict:
    text = _canonical(parameters) if parameters else "{}"
    return {
        "model_id": f"{task}:{horizon}",
        "task": task,
        "horizon": int(horizon),
        "model": info["model"],
        "model_version": info["model_version"],
        "run_month_id": origin,
        "trained_on_rows": int(info["trained_on_rows"]),
        "data_version": data_version,
        "library": library if parameters else "none (a rule)",
        "known_bias": info.get("known_bias"),
        "artifact_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "parameters": text,
    }
