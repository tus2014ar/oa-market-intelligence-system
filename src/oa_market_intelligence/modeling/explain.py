"""What the adopted segment forecast relies on: SHAP values of the logistic regression (DL-91).

A post-hoc explanation. It describes the model chosen under DL-79; it does not choose, tune or rank
anything, and it says nothing about what makes a share move.

The adopted segment model is a logistic regression, so its SHAP values have an exact closed form:
on the log-odds scale each input contributes `coefficient x (value - the average value)`, and the
contributions add up to the model's own log-odds minus the average segment's. This module computes
that directly (no extra package); a test checks it against the `shap` package where installed.

The model is refitted exactly as the monthly scoring job fits it at the latest origin, and the refit
is checked against the stored `pred_batch` forecasts before anything is reported. The average
("typical segment") is taken over the feature rows the model was fitted on (unweighted), and the
inputs are grouped into a few plain-language kinds of information:

    python -m oa_market_intelligence.modeling.explain      # writes data/reference/shap_summary.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from oa_market_intelligence.features.groups import columns_for
from oa_market_intelligence.features.segment import group_rare_specialties, load_segment_gold
from oa_market_intelligence.modeling.horizons import (
    month_id_from_index,
    month_index,
    outcome_table,
    pair_rows,
)
from oa_market_intelligence.modeling.run_modelling import DEFAULT_DB
from oa_market_intelligence.modeling.scoring import (
    SERVING_JSON,
    _calendar,
    load_serving_map,
    origin_features,
)
from oa_market_intelligence.modeling.segment_models import (
    LOGISTIC_CONFIGS,
    design_frame,
    fit_predict_for,
    fitter_for,
    tune_config,
)

SUMMARY_JSON = Path(__file__).resolve().parents[3] / "data" / "reference" / "shap_summary.json"
EXPLAINED_HORIZONS = (1, 3)  # the horizons at which the logistic regression is adopted (DL-79)
KEYS = ["specialty_name", "age_band", "gender"]
TOLERANCE = 1e-9

OWN_RECENT = "The segment's own recent share"
LONG_RUN = "The segment's long-run share"
SPECIALTY_LONG_RUN = "The specialty's long-run share"
MARKET = "The whole market's share and its recent change"
SIZE = "How big the segment is"
AGE_GENDER = "Age and gender"
SEASON = "Time of year"
CONTEXT = "Specialty and market context"
WHICH_SPECIALTY = "Which specialty it is"

GROUP_OF = {
    "logit_lag1": OWN_RECENT,
    "logit_roll3": OWN_RECENT,
    "logit_seg_history": LONG_RUN,
    "logit_spec_history": SPECIALTY_LONG_RUN,
    "logit_market_lag1": MARKET,
    "market_change_lag1": MARKET,
    "seg_log_visits_lag1": SIZE,
    "lag1_x_visits": SIZE,
    "age_ordinal": AGE_GENDER,
    "gender_FEMALE": AGE_GENDER,
    "gender_MALE": AGE_GENDER,
    "month_sin": SEASON,
    "month_cos": SEASON,
}
SPECIALTY_PREFIX = "specialty_grouped_"


def group_of(feature: str, context_columns: tuple[str, ...]) -> str:
    """The plain-language kind of information an input belongs to (unknown inputs are an error)."""
    if feature.startswith(SPECIALTY_PREFIX):
        return WHICH_SPECIALTY
    if feature in GROUP_OF:
        return GROUP_OF[feature]
    if feature in context_columns:
        return CONTEXT
    raise ValueError(f"input {feature!r} has no plain-language group")


def linear_shap(
    matrix: np.ndarray, coefficients: np.ndarray, background_mean: np.ndarray
) -> np.ndarray:
    """Exact SHAP values of a linear model on its output scale: coefficient x (value - average)."""
    return (np.asarray(matrix, float) - np.asarray(background_mean, float)) * np.asarray(
        coefficients, float
    )


def by_group(values: np.ndarray, groups: list[str]) -> pd.DataFrame:
    """The SHAP values summed within each group of inputs, one column per group, in order seen."""
    frame = pd.DataFrame(values)
    return pd.DataFrame(
        {
            name: frame.iloc[:, [i for i, g in enumerate(groups) if g == name]].sum(axis=1)
            for name in dict.fromkeys(groups)
        }
    )


def shares(table: pd.DataFrame) -> pd.Series:
    """Each group's share (in percent) of the total absolute movement, largest first."""
    mean_abs = table.abs().mean()
    return (100 * mean_abs / mean_abs.sum()).sort_values(ascending=False)


def _dense(matrix) -> np.ndarray:
    return matrix.toarray() if hasattr(matrix, "toarray") else np.asarray(matrix)


def explain_horizon(train, now, outcomes, horizon: int, origin: int, extra, stored=None) -> dict:
    """Refit the adopted logistic regression for one horizon and explain its forecasts."""
    target = int(month_id_from_index(month_index(origin) + horizon))
    paired = pair_rows(train, outcomes, horizon)
    test = _calendar(now, target)
    index, _ = tune_config(
        paired,
        LOGISTIC_CONFIGS,
        lambda config: fit_predict_for("logistic", config, extra_columns=extra),
        val_months=12,
    )
    config = LOGISTIC_CONFIGS[index]
    fitter = fitter_for("logistic", config, extra_columns=extra)
    predict = fitter(paired)
    p = np.asarray(predict(test), float)

    pipeline = fitter.model
    prep, clf = pipeline.named_steps["prep"], pipeline.named_steps["clf"]
    names = [n.split("__", 1)[1] for n in prep.get_feature_names_out()]
    x_test = _dense(prep.transform(design_frame(test, extra)))
    mean = _dense(prep.transform(design_frame(paired, extra))).mean(axis=0)
    coefficients = clf.coef_[0]
    values = linear_shap(x_test, coefficients, mean)
    base = float(clf.intercept_[0] + mean @ coefficients)
    additivity = float(np.abs(base + values.sum(axis=1) - np.log(p / (1 - p))).max())

    gap = None
    if stored is not None:
        saved = stored[stored["horizon"] == horizon][[*KEYS, "point_share"]]
        merged = test[KEYS].assign(p=p).merge(saved, on=KEYS)
        if len(merged) != len(test):
            raise ValueError("the stored forecasts do not cover every segment that was explained")
        gap = float((merged["p"] - merged["point_share"]).abs().max())
        if gap > TOLERANCE:
            raise ValueError(f"the refit differs from the stored forecasts by {gap:.2e}")
    if additivity > TOLERANCE:
        raise ValueError(
            f"the contributions do not add up to the forecast (error {additivity:.2e})"
        )

    groups = [group_of(n, extra) for n in names]
    table = by_group(values, groups)
    movement = shares(table)
    features = pd.Series(np.abs(values).mean(axis=0), index=names)
    top = (100 * features / features.sum()).sort_values(ascending=False).head(8)
    visits = np.expm1(test["seg_log_visits_lag1"].to_numpy(float)).round()
    biggest = int(np.argmax(visits))
    pushes = table.iloc[biggest].sort_values(key=np.abs, ascending=False)
    return {
        "C": config["C"],
        "typical_segment_pct": round(100 / (1 + float(np.exp(-base))), 2),
        "segments": int(len(test)),
        "max_gap_to_stored_forecast": gap,
        "additivity_max_error": additivity,
        "group_share_of_movement_pct": {k: round(float(v), 1) for k, v in movement.items()},
        "top_features_pct": {k: round(float(v), 1) for k, v in top.items()},
        "example": {
            "segment": {k: str(test.iloc[biggest][k]) for k in KEYS},
            "visits_last_month": float(visits[biggest]),
            "forecast_pct": round(100 * float(p[biggest]), 2),
            "last_month_pct": round(100 * float(test.iloc[biggest]["seg_share_lag1"]), 2),
            "pushes": {k: round(float(v), 3) for k, v in pushes.items()},
        },
    }


def explain(seg: pd.DataFrame, monthly: pd.DataFrame, serving: dict, stored=None) -> dict:
    """The explanation of the adopted segment forecast at the latest origin."""
    for horizon in EXPLAINED_HORIZONS:
        model = serving.get("segment", {}).get(str(horizon))
        if model != "logistic":
            raise ValueError(
                f"segment forecast at {horizon} months uses {model!r}: only the logistic "
                "regression has an exact closed-form explanation here"
            )
    origin = int(seg["month_id"].max())
    groups = dict(
        zip(seg["specialty_name"], group_rare_specialties(seg["specialty_name"]), strict=True)
    )
    outcomes = outcome_table(seg[seg["month_id"] <= origin])
    train, now = origin_features(seg, monthly, origin, specialty_groups=groups)
    extra = columns_for(("G4",), target="T2")
    return {
        "description": (
            "SHAP values of the adopted segment logistic regression (DL-91): how much each kind "
            "of information moves the forecasts. A post-hoc explanation of the model, not a cause "
            "of share changes; it did not influence which model was chosen."
        ),
        "method": (
            "Exact SHAP for a linear model: coefficient x (value - average of the fitted rows)."
        ),
        "origin_month_id": origin,
        "horizons": {
            str(h): explain_horizon(train, now, outcomes, h, origin, extra, stored)
            for h in EXPLAINED_HORIZONS
        },
    }


def explain_from_warehouse(db: Path = DEFAULT_DB, serving_path: Path = SERVING_JSON) -> dict:
    engine = create_engine(f"sqlite:///file:{Path(db).as_posix()}?mode=ro&uri=true")
    try:
        seg = load_segment_gold(engine)
        with engine.connect() as conn:
            monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
            stored = pd.read_sql("SELECT * FROM pred_batch WHERE level = 'segment'", conn)
    finally:
        engine.dispose()
    return explain(seg, monthly, load_serving_map(serving_path), stored)


def to_json(summary: dict) -> str:
    return json.dumps(summary, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=SUMMARY_JSON)
    args = parser.parse_args(argv)
    summary = explain_from_warehouse(args.db)
    args.out.write_text(to_json(summary), "utf-8")
    top = next(iter(summary["horizons"]["1"]["group_share_of_movement_pct"].items()))
    print(f"wrote {args.out}: at 1 month the largest group is {top[0]!r} ({top[1]}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
