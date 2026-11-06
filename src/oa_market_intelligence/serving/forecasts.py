"""The Forecasts tab, as tested functions: the national forecasts with their ranges, how often the
ranges held, the segment forecasts next to last month's share, the specialty roll-up and the
decline alert (DL-99).

Every function takes the stored predictions (`pred_batch`, from `data.predictions`), the stored
scores and the segment-month frame, and fits nothing. The forecasts are what the monthly job wrote;
this module only arranges them. The segment model runs high in testing (the known bias), so the
tab always shows it beside the forecasts.
"""

from __future__ import annotations

import pandas as pd

from oa_market_intelligence.serving import trend, views

HORIZONS = (1, 3, 6)
MIN_ALERT_VISITS = 100  # visits in the latest month under which a segment is called small
MIN_SPECIALTY_VISITS = 300  # latest-month visits under which a specialty is left out of the chart
KEY = ["specialty", "age_band", "gender"]


def horizon_label(horizon: int) -> str:
    return f"{horizon} month{'s' if horizon != 1 else ''}"


def national(pred: pd.DataFrame) -> pd.DataFrame:
    """The national forecast at each horizon with its 80% and 90% ranges, in percent."""
    rows = pred[pred["level"] == "national"].sort_values("horizon")
    out = pd.DataFrame(
        {
            "horizon": rows["horizon"].to_numpy(),
            "target_month_id": rows["target_month_id"].to_numpy(),
            "month": [views.month_label(m) for m in rows["target_month_id"]],
            "forecast_pct": 100 * rows["point_share"].to_numpy(),
            "lo80_pct": 100 * rows["lo80_share"].to_numpy(),
            "hi80_pct": 100 * rows["hi80_share"].to_numpy(),
            "lo90_pct": 100 * rows["lo90_share"].to_numpy(),
            "hi90_pct": 100 * rows["hi90_share"].to_numpy(),
            "method": [views.method_text(m) for m in rows["model"]],
        }
    )
    out["date"] = pd.to_datetime(out["target_month_id"].astype(str), format="%Y%m")
    return out.reset_index(drop=True)


def coverage(scores: pd.DataFrame) -> dict[int, float]:
    """How often the national 90% range held in testing, by horizon (a fraction)."""
    rows = scores[
        (scores["task"] == "T1") & (scores["scenario"] == "main") & (scores["serving"] == 1)
    ]
    return {
        int(r.horizon): float(r.coverage_90) for r in rows.itertuples() if pd.notna(r.coverage_90)
    }


def fan(
    monthly: pd.DataFrame, pred: pd.DataFrame, months_back: int = 36
) -> dict[str, pd.DataFrame]:
    """The frames of the national chart: the last `months_back` months of share, the forecasts with
    their ranges, and one two-point line per forecast from the latest month to the forecast."""
    history = monthly.tail(months_back)[["month_id", "month", "visit_share"]].copy()
    history["share_pct"] = 100 * history["visit_share"]
    history["label"] = [views.month_label(m) for m in history["month_id"]]
    forecast = national(pred)
    last = history.iloc[-1]
    links = []
    for row in forecast.itertuples():
        links.append(
            {"horizon": row.horizon, "month": last["month"], "share_pct": last["share_pct"]}
        )
        links.append({"horizon": row.horizon, "month": row.date, "share_pct": row.forecast_pct})
    return {"history": history, "forecast": forecast, "links": pd.DataFrame(links)}


def segment_view(pred: pd.DataFrame, seg: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Each scored segment's forecast at the horizon next to its share in the latest month: the
    forecast and last month's share in percent, the visits in the latest month, whether it is small
    and, at 1 month only, the decline alert's score and flag."""
    part = pred[(pred["level"] == "segment") & (pred["horizon"] == horizon)]
    run = int(part["run_month_id"].iloc[0]) if len(part) else None
    last = seg[seg["month_id"] == run].groupby(KEY)[["zilretta", "category"]].sum().reset_index()
    out = part.rename(columns={"specialty_name": "specialty"})[
        [*KEY, "point_share", "origin_visits", "alert_score", "alert_flag", "model", "known_bias"]
    ].merge(last, on=KEY, how="left")
    out["last_pct"] = 100 * out["zilretta"] / out["category"].where(out["category"] > 0)
    out["forecast_pct"] = 100 * out["point_share"]
    out["change_pp"] = out["forecast_pct"] - out["last_pct"]
    out["small"] = out["origin_visits"] < MIN_ALERT_VISITS
    out["specialty_label"] = out["specialty"].map(trend.pretty)
    out["age_label"] = out["age_band"].map(trend.pretty)
    out["gender_label"] = out["gender"].map(trend.pretty)
    columns = [
        *KEY,
        "specialty_label",
        "age_label",
        "gender_label",
        "last_pct",
        "forecast_pct",
        "change_pp",
        "origin_visits",
        "zilretta",
        "category",
        "small",
        "alert_score",
        "alert_flag",
        "model",
        "known_bias",
    ]
    return out[columns].sort_values("origin_visits", ascending=False).reset_index(drop=True)


def select(
    view: pd.DataFrame,
    specialty: str | None = None,
    age: str | None = None,
    gender: str | None = None,
) -> pd.DataFrame:
    """The segment view narrowed to a specialty, an age band and a gender (None means all)."""
    mask = pd.Series(True, index=view.index)
    for column, value in (("specialty", specialty), ("age_band", age), ("gender", gender)):
        if value is not None:
            mask &= view[column] == value
    return view[mask].reset_index(drop=True)


def specialty_view(view: pd.DataFrame, min_visits: int = MIN_SPECIALTY_VISITS) -> pd.DataFrame:
    """The segment view rolled up to specialties: the forecast and last month's share, both
    weighted by the segments' visits in the latest month, and the number of segments. A specialty
    with fewer than `min_visits` visits in the latest month is left out (its share is too noisy to
    chart); `attrs["left_out"]` holds how many."""
    rows = []
    for name, part in view.groupby("specialty"):
        weight = part["origin_visits"]
        total = float(weight.sum())
        rows.append(
            {
                "specialty": name,
                "label": trend.pretty(name),
                "forecast_pct": float((part["forecast_pct"] * weight).sum() / total),
                "last_pct": float(100 * part["zilretta"].sum() / part["category"].sum()),
                "visits": total,
                "segments": int(len(part)),
            }
        )
    columns = ["specialty", "label", "forecast_pct", "last_pct", "visits", "segments"]
    out = pd.DataFrame(rows, columns=columns)
    kept = out[out["visits"] >= min_visits].sort_values("visits", ascending=False)
    kept = kept.reset_index(drop=True)
    kept.attrs["left_out"] = int(len(out) - len(kept))
    return kept


def alerts(view: pd.DataFrame) -> pd.DataFrame:
    """The segments flagged by the 1-month decline alert, highest score first."""
    flagged = view[view["alert_flag"] == 1].sort_values("alert_score", ascending=False)
    return flagged.reset_index(drop=True)


def trust(adoption: dict, scores: pd.DataFrame, horizon: int) -> dict:
    """What the tab says about how far to trust the forecasts at a horizon: the method served for
    the national and the segment forecasts, how often the national range held and whether the
    segment forecast has a tested bias."""
    return {
        "national_method": views.method_text(adoption["national"][str(horizon)]),
        "segment_method": views.method_text(adoption["segment"][str(horizon)]),
        "range_held": coverage(scores).get(horizon),
        "segment_is_model": adoption["segment"][str(horizon)] != "last_month",
    }


def bias_text(pred: pd.DataFrame, horizon: int) -> str:
    """The segment forecast's tested bias at a horizon, in words."""
    value = views.known_bias(pred).get(horizon)
    if value is None:
        return (
            f"At {horizon_label(horizon)} the segment forecast is the simple rule (the same as "
            "last month), which was not found to run high or low."
        )
    return (
        f"At {horizon_label(horizon)} the segment forecast ran {100 * value:.1f}% too high in "
        "testing: read it as optimistic."
    )


def csv(view: pd.DataFrame) -> bytes:
    """The segment forecasts at a horizon as a CSV."""
    out = pd.DataFrame(
        {
            "specialty": view["specialty_label"],
            "age_band": view["age_label"],
            "gender": view["gender_label"],
            "last_month_share_pct": view["last_pct"].round(3),
            "forecast_share_pct": view["forecast_pct"].round(3),
            "visits_in_latest_month": view["origin_visits"].astype(int),
            "small_segment": view["small"],
        }
    )
    return out.to_csv(index=False).encode("utf-8")
