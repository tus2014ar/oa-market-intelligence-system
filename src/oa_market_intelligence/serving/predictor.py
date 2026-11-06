"""Live predictions from the saved models (DL-117).

The monthly job fits the adopted models and saves each logistic regression as readable numbers
(the imputing medians, the scaling, the specialty categories, the coefficients) in the
`model_artifacts` table, with the inputs of every segment at the last month in `pred_inputs`. This
module applies those numbers: with pandas and NumPy only, so the API needs no scikit-learn, and
with the same `design_frame` the models were trained on. It must reproduce the stored batch
forecasts exactly, and the tests and the monthly job check that it does.

It predicts for the segments and the month in the data: it is not a what-if tool, and a segment it
has no row for is an error, not a guess.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from oa_market_intelligence.modeling.design import _NUMERIC, calendar_columns, design_frame
from oa_market_intelligence.modeling.segment_baselines import BASELINES, last_month_baseline
from oa_market_intelligence.serving import views

SMALL_VISITS = 100  # visits at the last month under which a segment's share is noisy


def _sigmoid(z: np.ndarray) -> np.ndarray:
    """The logistic function, stable for large positive and negative values."""
    z = np.asarray(z, dtype=float)
    positive = z >= 0
    out = np.empty_like(z)
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    e = np.exp(z[~positive])
    out[~positive] = e / (1.0 + e)
    return out


def logistic_predict(parameters: dict, frame: pd.DataFrame) -> np.ndarray:
    """The probability from saved logistic-regression numbers for each row of `frame` (a table of
    segment features): the same steps as the fitted scikit-learn pipeline, in NumPy."""
    columns = list(parameters["numeric_columns"])
    extra = tuple(columns[len(_NUMERIC) :])
    X = design_frame(frame, extra)
    numeric = X[columns].to_numpy(float)
    numeric = np.where(np.isnan(numeric), np.asarray(parameters["impute_median"], float), numeric)
    numeric = (numeric - np.asarray(parameters["scale_mean"], float)) / np.asarray(
        parameters["scale_scale"], float
    )
    categories = np.asarray(parameters["categories"], dtype=object)
    # a specialty group the model never saw has no column (all zeros), as in the fitted encoder
    onehot = (X["specialty_grouped"].astype(str).to_numpy()[:, None] == categories[None, :]).astype(
        float
    )
    z = np.hstack([numeric, onehot]) @ np.asarray(parameters["coef"], float)
    return _sigmoid(z + float(parameters["intercept"]))


def _month_after(month_id: int, months: int) -> int:
    index = (month_id // 100) * 12 + (month_id % 100 - 1) + months
    return (index // 12) * 100 + index % 12 + 1


class Predictor:
    """The saved models and the last month's inputs, ready to predict for a segment."""

    def __init__(self, artifacts: pd.DataFrame, inputs: pd.DataFrame):
        if artifacts.empty or inputs.empty:
            raise ValueError("no saved models or inputs")
        self._artifacts = {
            (str(r.task), int(r.horizon)): {
                **r._asdict(),
                "parameters": json.loads(r.parameters) if r.parameters else {},
            }
            for r in artifacts.itertuples(index=False)
        }
        self._inputs = inputs.reset_index(drop=True)
        self.origin = int(artifacts["run_month_id"].iloc[0])

    # ---- what is available

    def horizons(self) -> list[int]:
        return sorted(h for task, h in self._artifacts if task == "segment")

    def segments(self) -> list[dict]:
        """Every segment there is an input row for, in a fixed order."""
        keys = ["specialty_name", "age_band", "gender"]
        out = self._inputs[keys].sort_values(keys)
        return [
            dict(zip(("specialty", "age_band", "gender"), row, strict=True)) for row in out.values
        ]

    def models(self) -> list[dict]:
        """One line for each saved model: what it is, what it was trained on and how to check it."""
        rows = []
        for (task, horizon), art in sorted(self._artifacts.items()):
            params = art["parameters"]
            count = len(params.get("coef", [])) + 1 if params else 0
            rows.append(
                {
                    "model_id": art["model_id"],
                    "task": task,
                    "horizon": horizon,
                    "model": art["model"],
                    "model_version": art["model_version"],
                    "run_month_id": int(art["run_month_id"]),
                    "trained_on_rows": int(art["trained_on_rows"]),
                    "data_version": art["data_version"],
                    "library": art["library"],
                    "n_parameters": count,
                    "artifact_sha256": art["artifact_sha256"],
                    "known_bias_pct": None
                    if pd.isna(art["known_bias"])
                    else round(100 * float(art["known_bias"]), 2),
                }
            )
        return rows

    # ---- one segment

    def _row(self, specialty: str, age_band: str, gender: str) -> pd.DataFrame:
        want = [str(v).strip().upper() for v in (specialty, age_band, gender)]
        frame = self._inputs
        mask = (
            (frame["specialty_name"].str.upper() == want[0])
            & (frame["age_band"].str.upper() == want[1])
            & (frame["gender"].str.upper() == want[2])
        )
        if not mask.any():
            raise LookupError(
                f"No segment {specialty!r}, {age_band!r}, {gender!r} among the segments with a "
                "model input (see /v1/predict/segments)."
            )
        return frame[mask].iloc[[0]].reset_index(drop=True)

    def _artifact(self, task: str, horizon: int) -> dict:
        try:
            return self._artifacts[(task, int(horizon))]
        except KeyError:
            served = self.horizons() if task == "segment" else [1]
            raise LookupError(
                f"No saved {task} model for horizon {horizon}; served: {served}."
            ) from None

    def segment(self, specialty: str, age_band: str, gender: str, horizon: int) -> dict:
        """The adopted model's forecast of a segment's share `horizon` months on."""
        row = self._row(specialty, age_band, gender)
        art = self._artifact("segment", horizon)
        target = _month_after(self.origin, int(horizon))
        frame = calendar_columns(row, target)
        if art["model"] == "logistic":
            share = float(logistic_predict(art["parameters"], frame)[0])
        elif art["model"] in BASELINES:
            share = float(BASELINES[art["model"]](None, frame)[0])
        else:
            raise LookupError(f"The model {art['model']!r} cannot be run here.")
        last = float(last_month_baseline(None, frame)[0])
        visits = int(round(float(np.expm1(row["seg_log_visits_lag1"].iloc[0]))))
        bias = art["known_bias"]
        return {
            "specialty": str(row["specialty_name"].iloc[0]),
            "age_band": str(row["age_band"].iloc[0]),
            "gender": str(row["gender"].iloc[0]),
            "horizon": int(horizon),
            "target_month_id": target,
            "target_month": views.month_label(target),
            "model": art["model"],
            "model_version": art["model_version"],
            "forecast_pct": round(100 * share, 4),
            "last_month_pct": round(100 * last, 4),
            "change_pp": round(100 * (share - last), 4),
            "origin_visits": visits,
            "small": visits < SMALL_VISITS,
            "known_bias_pct": None if pd.isna(bias) else round(100 * float(bias), 2),
        }

    def alert(self, specialty: str, age_band: str, gender: str) -> dict:
        """The 1-month decline-alert score of a segment, and whether it is over the cut-off."""
        row = self._row(specialty, age_band, gender)
        art = self._artifact("alert", 1)
        params = art["parameters"]
        visits = int(round(float(np.expm1(row["seg_log_visits_lag1"].iloc[0]))))
        base = {
            "specialty": str(row["specialty_name"].iloc[0]),
            "age_band": str(row["age_band"].iloc[0]),
            "gender": str(row["gender"].iloc[0]),
            "model": art["model"],
            "model_version": art["model_version"],
            "origin_visits": visits,
            "threshold": None if params.get("threshold") is None else float(params["threshold"]),
        }
        minimum = int(params.get("min_origin_visits", 100))
        if not float(row["seg_share_lag1"].iloc[0]) > 0 or visits < minimum:
            reason = (
                "Zilretta had no share in this segment last month."
                if not float(row["seg_share_lag1"].iloc[0]) > 0
                else f"Fewer than {minimum} visits last month: too few to score."
            )
            return {
                **base,
                "eligible": False,
                "reason": reason,
                "alert_score": None,
                "flagged": None,
            }
        frame = calendar_columns(row, _month_after(self.origin, 1))
        score = float(logistic_predict(params, frame)[0])
        threshold = base["threshold"]
        return {
            **base,
            "eligible": True,
            "reason": None,
            "alert_score": round(score, 6),
            "flagged": None if threshold is None else bool(score >= threshold),
        }
