"""The Segment detail page, as tested functions: one group on its own (a specialty, an age band, a
gender, or any combination), set beside the whole market and beside its neighbours (DL-105).

Everything is read from the frames the other pages use and nothing is fitted. A group's share is
Zilretta's visits over the visits of Zilretta, generic steroid injections and NSAID/OTC together
within that group; "the market" is the same share over every group.
"""

from __future__ import annotations

import pandas as pd

from oa_market_intelligence.serving import forecasts, segments, trend, views

COMPARE_MONTHS = 12
MIN_COMPARE_VISITS = 1000  # visits in each 12-month period under which a change is not given


def crumbs(specialty: str | None, age: str | None, gender: str | None) -> list[str]:
    """The path to the group, from everything down to the most specific part chosen."""
    parts = ["All visits"]
    if specialty:
        parts.append(trend.pretty(specialty))
    if age:
        parts.append(f"Ages {trend.pretty(age)}")
    if gender:
        parts.append(trend.pretty(gender))
    return parts


def chosen(specialty: str | None, age: str | None, gender: str | None) -> bool:
    """True when at least one of the three is chosen (otherwise there is no group to detail)."""
    return any(v is not None for v in (specialty, age, gender))


def peer_split(
    specialty: str | None, age: str | None, gender: str | None
) -> tuple[str, tuple[str | None, str | None, str | None]] | None:
    """How to compare the group with its neighbours: the dimension to compare across (the most
    specific one chosen: gender, then age band, then specialty) and the other chosen parts to hold
    fixed. None when nothing is chosen."""
    if gender:
        return "gender", (specialty, age, None)
    if age:
        return "age_band", (specialty, None, None)
    if specialty:
        return "specialty", (None, None, None)
    return None


def peers(
    seg: pd.DataFrame,
    specialty: str | None,
    age: str | None,
    gender: str | None,
    period: str,
    calendar: list[int] | None = None,
) -> pd.DataFrame:
    """The group beside its neighbours: the table of groups along the compared dimension, ranked by
    share, with `current` marking the chosen one and `rank` its place among those listed."""
    split = peer_split(specialty, age, gender)
    if split is None:
        return pd.DataFrame()
    by, parent = split
    frame = trend.select(seg, *parent)
    table = segments.group_table(frame, by, period, calendar=calendar)
    value = {"specialty": specialty, "age_band": age, "gender": gender}[by]
    table["current"] = table["group"] == value
    table["rank"] = range(1, len(table) + 1)
    table.attrs["by"] = by
    return table


def rank_of(table: pd.DataFrame) -> tuple[int, int] | None:
    """(rank, how many) of the chosen group in the peers table, or None if it is not listed (it
    has too few visits to be ranked)."""
    if table.empty or not table["current"].any():
        return None
    return int(table.loc[table["current"], "rank"].iloc[0]), int(len(table))


def compare_series(
    seg: pd.DataFrame,
    specialty: str | None,
    age: str | None,
    gender: str | None,
    market: pd.DataFrame,
    period: str,
) -> pd.DataFrame:
    """The group's monthly share beside the whole market's, for the months of the period, in
    percent: `month`, `label`, `group_pct`, `market_pct`, `category` (the group's visits)."""
    full = trend.monthly(seg, specialty, age, gender)
    window = trend.window(full, period)
    base = market.set_index("month_id")["visit_share"]
    out = window[["month_id", "month", "label", "share_pct", "category"]].rename(
        columns={"share_pct": "group_pct"}
    )
    out["market_pct"] = 100 * out["month_id"].map(base)
    return out.reset_index(drop=True)


def range_compare(series: pd.DataFrame, bounds: tuple[float, float] | None) -> dict | None:
    """The group against the whole market over a range dragged on the chart (milliseconds since
    1970): the average of the monthly shares of each and the gap, in points. None when there is no
    range, it holds fewer than two months, or the group has no share in it."""
    if bounds is None:
        return None
    low, high = (pd.to_datetime(b, unit="ms") for b in bounds)
    rows = series[(series["month"] >= low) & (series["month"] <= high)]
    if len(rows) < 2 or rows["group_pct"].notna().sum() == 0:
        return None
    group, market = float(rows["group_pct"].mean()), float(rows["market_pct"].mean())
    return {
        "from": str(rows["label"].iloc[0]),
        "to": str(rows["label"].iloc[-1]),
        "months": int(len(rows)),
        "group_pct": group,
        "market_pct": market,
        "gap_pp": group - market,
    }


def range_sentence(who: str, r: dict) -> str:
    """The chosen range in one plain sentence: the group's average share against the market's."""
    side = "above" if r["gap_pp"] > 0 else "below"
    return (
        f"{who}, {r['from']} to {r['to']} ({r['months']} months): Zilretta's share averaged "
        f"{r['group_pct']:.2f}% against {r['market_pct']:.2f}% across all visits, "
        f"{abs(r['gap_pp']):.2f} points {side}."
    )


def _share(frame: pd.DataFrame) -> float | None:
    c = float(frame["category"].sum())
    return None if c <= 0 else float(frame["zilretta"].sum()) / c


def change_12m(full: pd.DataFrame, minimum: int = MIN_COMPARE_VISITS) -> float | None:
    """The change in the group's share, in points, from the 12 months before to the last 12 months
    of the series. None if the series is shorter than 24 months or either period has fewer than
    `minimum` visits (the change would be noise)."""
    if len(full) < 2 * COMPARE_MONTHS:
        return None
    last, prior = full.tail(COMPARE_MONTHS), full.iloc[-2 * COMPARE_MONTHS : -COMPARE_MONTHS]
    if last["category"].sum() < minimum or prior["category"].sum() < minimum:
        return None
    return 100 * (_share(last) - _share(prior))


def summary(
    seg: pd.DataFrame,
    specialty: str | None,
    age: str | None,
    gender: str | None,
    period: str,
    market: pd.DataFrame,
) -> dict:
    """The headline numbers of the group: the latest share and how far it sits from the market's,
    the visits in the period, the change over the last 12 months and whether the group is small."""
    full = trend.monthly(seg, specialty, age, gender)
    window = trend.window(full, period)
    k = trend.kpis(full, window)
    market_latest = float(market["visit_share"].iloc[-1])
    latest = k["latest_share"]
    return {
        "latest_month_id": k["latest_month_id"],
        "latest_share": latest,
        "market_share": market_latest,
        "gap_to_market_pp": None if latest is None else 100 * (latest - market_latest),
        "period_zilretta_visits": k["period_zilretta_visits"],
        "period_category_visits": k["period_category_visits"],
        "change_12m_pp": change_12m(full),
        "small": trend.is_small(k),
        "median_monthly_visits": k["median_monthly_visits"],
    }


def forecast_block(
    pred: pd.DataFrame,
    seg: pd.DataFrame,
    specialty: str | None,
    age: str | None,
    gender: str | None,
) -> dict:
    """The stored forecasts for the group at each horizon, visit-weighted over its scored segments,
    beside its latest share, with the 1-month alert count. `rows` is empty when the group has no
    scored segment."""
    rows = []
    flagged = scored = 0
    for horizon in forecasts.HORIZONS:
        view = forecasts.select(forecasts.segment_view(pred, seg, horizon), specialty, age, gender)
        if view.empty:
            continue
        weight = view["origin_visits"]
        total = float(weight.sum())
        rows.append(
            {
                "horizon": horizon,
                "forecast_pct": float((view["forecast_pct"] * weight).sum() / total),
                "last_pct": float(100 * view["zilretta"].sum() / view["category"].sum()),
                "segments": int(len(view)),
                "method": views.method_text(view["model"].iloc[0]),
                "bias": views.known_bias(pred).get(horizon),
            }
        )
        if horizon == 1:
            scored = int(view["alert_flag"].notna().sum())
            flagged = int((view["alert_flag"] == 1).sum())
    return {"rows": rows, "alerts_flagged": flagged, "alerts_scored": scored}


def sentence(who: str, s: dict) -> str:
    """The group in one plain sentence: its share against the market's and the 12-month change."""
    if s["latest_share"] is None:
        return f"{who} had no visits in {views.month_label(s['latest_month_id'])}."
    gap = s["gap_to_market_pp"]
    side = "above" if gap > 0 else "below"
    text = (
        f"{who}: Zilretta's share of visits was {views.pct(s['latest_share'])} in "
        f"{views.month_label(s['latest_month_id'])}, {abs(gap):.2f} points {side} the whole "
        f"market's {views.pct(s['market_share'])}"
    )
    change = s["change_12m_pp"]
    if change is not None:
        text += f", and {abs(change):.2f} points {'up' if change > 0 else 'down'} on a year earlier"
    return text + "."
