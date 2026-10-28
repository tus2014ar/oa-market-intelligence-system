"""The monthly scoring job (DL-82, docs/modelling_plan.md section 8): the adopted models, refitted
on all the data, forecast the next 1, 3 and 6 months and the result is written to `pred_batch`.

Which model serves is not decided here: `data/reference/serving_models.json` records the adoption
of DL-79 (the national baseline; the segment logistic regression at 1 and 3 months and the baseline
at 6; the decline alert at 1 month only). `validate_serving_map` refuses a map that names a model
the registered serving rule did not choose (checked against `model_scores`), a horizon the model
was not tested at, or a model that is not allowed for the task. The job refits the adopted models
only; the full promotion test (`run_modelling --run`, about two hours) is run on demand.

The origin of a run is the last month of data. Features at the origin are built from data through
the origin only, with one placeholder row per segment for the next month (zero counts) that
makes the row builder produce the features of the month to be predicted; those rows never carry
an outcome.
`score_batch(..., origin=past_month)` truncates the data to that month first, which is how the
job is checked against the stored batch demonstration.

    PYTHONPATH=src python -m oa_market_intelligence.modeling.scoring [--db PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine

from oa_market_intelligence.features.build import build_feature_tables
from oa_market_intelligence.features.groups import columns_for
from oa_market_intelligence.features.segment import group_rare_specialties
from oa_market_intelligence.modeling.alerts import (
    ALERT_MODELS,
    MIN_ORIGIN_VISITS,
    TunedAlertModel,
    add_alert_label,
)
from oa_market_intelligence.modeling.horizons import (
    HORIZONS,
    month_id_from_index,
    month_index,
    outcome_table,
    pair_rows,
)
from oa_market_intelligence.modeling.run_modelling import DEFAULT_DB
from oa_market_intelligence.modeling.segment_baselines import BASELINES
from oa_market_intelligence.modeling.segment_models import TunedPredictor
from oa_market_intelligence.modeling.t1_horizon import check_series, make_forecasters
from oa_market_intelligence.modeling.t2_horizon import T2_MODELS

SERVING_JSON = Path(__file__).resolve().parents[3] / "data" / "reference" / "serving_models.json"
PRED_TABLE = "pred_batch"
TASK_FOR_KEY = {"national": "T1", "segment": "T2", "alert": "T3"}
NATIONAL_MODELS = tuple(make_forecasters())
PRED_COLUMNS = [
    "run_month_id", "target_month_id", "horizon", "level", "specialty_name", "age_band", "gender",
    "model", "model_version", "point_share", "lo80_share", "hi80_share", "lo90_share",
    "hi90_share", "origin_visits", "alert_score", "alert_flag", "known_bias",
]


class ServingMapError(ValueError):
    """The adoption map names something the registered results do not support."""


# --------------------------------------------------------------------------- the adoption map


def load_serving_map(path: Path | str = SERVING_JSON) -> dict:
    return json.loads(Path(path).read_text("utf-8"))


def validate_serving_map(serving: dict, scores: pd.DataFrame | None = None) -> None:
    """Raise `ServingMapError` unless every adopted (task, horizon, model) is allowed and, when the
    stored `model_scores` are given, is what the registered serving rule chose."""
    allowed = {
        "national": NATIONAL_MODELS,
        "segment": (*BASELINES, *T2_MODELS),
        "alert": ALERT_MODELS,
    }
    for key in serving:
        if key in ("decision", "decided_on"):
            continue
        if key not in allowed:
            raise ServingMapError(f"unknown task {key!r} in the serving map")
        for horizon, model in serving[key].items():
            if int(horizon) not in HORIZONS:
                raise ServingMapError(f"{key}: horizon {horizon} was not tested")
            if model not in allowed[key]:
                raise ServingMapError(f"{key}: model {model!r} is not allowed for this task")
            if scores is None:
                continue
            rows = scores[
                (scores["task"] == TASK_FOR_KEY[key]) & (scores["horizon"] == int(horizon))
                & (scores["scenario"] == "main") & (scores["method"] == model)
            ]
            if rows.empty or int(rows["serving"].iloc[0]) != 1:
                raise ServingMapError(
                    f"{key} at {horizon} months: {model!r} is not the model the registered "
                    "serving rule chose"
                )


# --------------------------------------------------------------------------- features at the origin


def with_next_month_rows(seg: pd.DataFrame, origin: int) -> tuple[pd.DataFrame, int]:
    """The segment table plus one placeholder row (zero counts) for every segment present in the
    origin month, dated the month after it. The row builder then produces, for that month, the
    features of the month to be predicted from data through the origin."""
    next_month = int(month_id_from_index(month_index(origin) + 1))
    placeholder = seg[seg["month_id"] == origin].copy()
    placeholder["month_id"] = next_month
    placeholder["branded_injectable_visits"] = 0
    placeholder["total_category_visits"] = 0
    if "segment_visit_share" in placeholder.columns:
        placeholder["segment_visit_share"] = np.nan
    return pd.concat([seg, placeholder], ignore_index=True), next_month


def origin_features(
    seg: pd.DataFrame,
    monthly: pd.DataFrame,
    origin: int,
    *,
    specialty_groups: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(training rows, origin rows). Training rows are the usual feature rows (B0 and G4, outcomes
    included) of months up to the origin; origin rows are the features of the next month, from data
    through the origin, with no outcome columns. Rare specialties are grouped from the whole table
    as everywhere else (an accepted detail: it uses which specialties appear, no counts)."""
    seg = seg[seg["month_id"] <= origin]
    monthly = monthly[monthly["month_id"] <= origin]
    groups = specialty_groups or dict(
        zip(seg["specialty_name"], group_rare_specialties(seg["specialty_name"]), strict=True)
    )
    extended, next_month = with_next_month_rows(seg, origin)
    with np.errstate(divide="ignore", invalid="ignore"):
        table, _ = build_feature_tables(extended, monthly, groups=("G4",))
    table["specialty_grouped"] = table["specialty_name"].map(groups)
    train = table[table["month_id"] <= origin].reset_index(drop=True)
    now = table[table["month_id"] == next_month].reset_index(drop=True)
    now = now.drop(columns=[c for c in now.columns if c.startswith("y_")])
    return train, now


# --------------------------------------------------------------------------- the forecasts


def _version(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def _national(monthly: pd.DataFrame, serving: dict, origin: int) -> list[dict]:
    series = pd.Series(
        monthly["visit_share"].to_numpy(float) * 100, index=monthly["month_id"].to_numpy()
    ).sort_index()
    series = series[series.index <= origin]
    check_series(series)
    forecasters = make_forecasters()
    rows = []
    for horizon in HORIZONS:
        name = serving.get("national", {}).get(str(horizon))
        if name is None:
            continue
        target = int(month_id_from_index(month_index(origin) + horizon))
        out = forecasters[name](series, target, horizon)
        rows.append({
            "run_month_id": origin, "target_month_id": target, "horizon": horizon,
            "level": "national", "model": name,
            "model_version": _version(name, horizon, origin, len(series), series.sum().round(8)),
            "point_share": out["point"] / 100, "lo80_share": out["lo80"] / 100,
            "hi80_share": out["hi80"] / 100, "lo90_share": out["lo90"] / 100,
            "hi90_share": out["hi90"] / 100,
        })
    return rows


def _calendar(frame: pd.DataFrame, target: int) -> pd.DataFrame:
    """The calendar columns follow the month being predicted (known in advance)."""
    out = frame.copy()
    month = target % 100
    out["month_sin"] = np.sin(2 * np.pi * month / 12)
    out["month_cos"] = np.cos(2 * np.pi * month / 12)
    return out


def _segment(train, now, outcomes, serving, origin, extra, bias) -> pd.DataFrame:
    frames = []
    for horizon in HORIZONS:
        name = serving.get("segment", {}).get(str(horizon))
        if name is None:
            continue
        target = int(month_id_from_index(month_index(origin) + horizon))
        paired = pair_rows(train, outcomes, horizon)
        test = _calendar(now, target)
        if name in BASELINES:
            p = BASELINES[name](paired, test)
        else:
            p = TunedPredictor(name, extra_columns=extra)(paired, test)
        frames.append(pd.DataFrame({
            "run_month_id": origin, "target_month_id": target, "horizon": horizon,
            "level": "segment", "specialty_name": test["specialty_name"].to_numpy(),
            "age_band": test["age_band"].to_numpy(), "gender": test["gender"].to_numpy(),
            "model": name,
            "model_version": _version(name, horizon, origin, len(paired),
                                      round(float(paired["y_share"].sum()), 8)),
            "point_share": np.asarray(p, float),
            "origin_visits": np.expm1(test["seg_log_visits_lag1"].to_numpy(float)).round(),
            "known_bias": bias.get(("T2", horizon, name)),
        }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _alerts(train, now, outcomes, serving, origin, extra) -> pd.DataFrame:
    """The 1-month decline alert for every segment that qualifies (a positive share and enough
    visits at the origin); the cut-off is chosen from earlier origins only."""
    name = serving.get("alert", {}).get("1")
    if name is None:
        return pd.DataFrame()
    labelled = add_alert_label(pair_rows(train, outcomes, 1))
    visits = np.expm1(now["seg_log_visits_lag1"].to_numpy(float)).round()
    eligible = (now["seg_share_lag1"].to_numpy(float) > 0) & (visits >= MIN_ORIGIN_VISITS)
    test = _calendar(now[eligible], int(month_id_from_index(month_index(origin) + 1)))
    scores, threshold = TunedAlertModel(name, 1, extra_columns=extra)(labelled, test)
    return pd.DataFrame({
        "specialty_name": test["specialty_name"].to_numpy(),
        "age_band": test["age_band"].to_numpy(),
        "gender": test["gender"].to_numpy(),
        "alert_score": scores,
        "alert_flag": (scores >= threshold).astype(int),
    })


def score_batch(
    seg: pd.DataFrame,
    monthly: pd.DataFrame,
    serving: dict,
    *,
    origin: int | None = None,
    scores: pd.DataFrame | None = None,
    include_alert: bool = True,
) -> pd.DataFrame:
    """The predictions of the adopted models from data through `origin` (default: the last month).

    `seg` is the segment Gold table (`load_segment_gold` output), `monthly` the monthly Gold table,
    `scores` the stored `model_scores` (to validate the map and to look up the known bias)."""
    validate_serving_map(serving, scores)
    last = int(seg["month_id"].max())
    origin = last if origin is None else int(origin)
    if origin not in set(seg["month_id"]):
        raise ValueError(f"the origin {origin} is not a month of the data")
    groups = dict(zip(seg["specialty_name"], group_rare_specialties(seg["specialty_name"]),
                      strict=True))
    seg_cut = seg[seg["month_id"] <= origin]
    outcomes = outcome_table(seg_cut)
    train, now = origin_features(seg, monthly, origin, specialty_groups=groups)
    extra = columns_for(("G4",), target="T2")
    bias = {}
    if scores is not None:
        main = scores[(scores["scenario"] == "main") & scores["bias_vs_actual"].notna()]
        bias = {
            (r.task, int(r.horizon), r.method): float(r.bias_vs_actual) for r in main.itertuples()
        }

    parts = [pd.DataFrame(_national(monthly, serving, origin))]
    segment = _segment(train, now, outcomes, serving, origin, extra, bias)
    if include_alert and not segment.empty:
        alerts = _alerts(train, now, outcomes, serving, origin, extra)
        if not alerts.empty:
            keys = ["specialty_name", "age_band", "gender"]
            one = segment["horizon"] == 1
            merged = segment[one].merge(alerts, on=keys, how="left")
            segment = pd.concat([merged, segment[~one]], ignore_index=True)
    parts.append(segment)
    out = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    for column in PRED_COLUMNS:
        if column not in out.columns:
            out[column] = np.nan
    return out[PRED_COLUMNS].sort_values(
        ["level", "horizon", "specialty_name", "age_band", "gender"], na_position="first"
    ).reset_index(drop=True)


# --------------------------------------------------------------------------- the pipeline stage


def write_predictions(engine: Engine, serving_path: Path | str = SERVING_JSON) -> dict:
    """The `scoring` stage of the publish run: read the Gold tables and the stored scores from the
    staged warehouse, score the adopted models and replace `pred_batch`. Returns the row counts."""
    from oa_market_intelligence.features.segment import load_segment_gold

    serving = load_serving_map(serving_path)
    with engine.connect() as conn:
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
        scores = pd.read_sql("SELECT * FROM model_scores", conn)
    predictions = score_batch(load_segment_gold(engine), monthly, serving, scores=scores)
    with engine.begin() as conn:
        predictions.to_sql(PRED_TABLE, conn, if_exists="replace", index=False)
    return {
        PRED_TABLE: len(predictions),
        "national": int((predictions["level"] == "national").sum()),
        "segment": int((predictions["level"] == "segment").sum()),
        "alert_flags": int(predictions["alert_flag"].fillna(0).sum()),
        "run_month_id": int(predictions["run_month_id"].iloc[0]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--serving", type=Path, default=SERVING_JSON)
    args = parser.parse_args(argv)
    engine = create_engine(f"sqlite:///{args.db.as_posix()}")
    print(write_predictions(engine, args.serving))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
