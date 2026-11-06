"""The Market trend tab, as tested functions: the filters, the windows, the headline numbers, the
straight-line trend, the movers and the profile of who the visits come from (DL-97).

Every function takes the segment-month frame from `data.segment_monthly` (or the national monthly
frame from `data.market_monthly`) and fits nothing but one straight line through the share, which
is drawn as a guide and never presented as a forecast. Shares are Zilretta's share of the visits
for Zilretta, generic steroid injections and NSAID/OTC together (`category` in the frames).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oa_market_intelligence.serving import views

PERIOD_MONTHS = {"1Y": 12, "3Y": 36, "5Y": 60, "All": None}
PERIOD_NAMES = {"1Y": "last year", "3Y": "last 3 years", "5Y": "last 5 years", "All": "all months"}
PERIOD_PHRASE = {
    "1Y": "in the last year",
    "3Y": "in the last 3 years",
    "5Y": "in the last 5 years",
    "All": "across all months",
}
MIN_PEAK_VISITS = 100  # a month with fewer visits is too noisy to be called a peak
SMALL_GROUP_VISITS = 300  # median visits a month under which a group's share is mostly noise
MIN_GROUP_VISITS = 1000  # visits in a 12-month period under which a group is left out of a ranking
COMPARE_MONTHS = 12  # the "change" columns compare the last 12 months with the 12 before


def pretty(name: str) -> str:
    """A group's name as a reader would write it (the warehouse stores capitals)."""
    text = str(name).strip()
    if text.upper() == "UNSPECIFIED":
        return "Not specified"
    return text.title().replace(" To ", " to ").replace(" +", "+")


def select(
    seg: pd.DataFrame,
    specialty: str | None = None,
    age_band: str | None = None,
    gender: str | None = None,
) -> pd.DataFrame:
    """The segment-months of the chosen specialty, age band and gender (None means all)."""
    mask = pd.Series(True, index=seg.index)
    for column, value in (("specialty", specialty), ("age_band", age_band), ("gender", gender)):
        if value is not None:
            mask &= seg[column] == value
    return seg[mask]


def monthly(seg: pd.DataFrame, specialty=None, age_band=None, gender=None) -> pd.DataFrame:
    """One row per month of the whole series (a month with no visits in the group stays, with a
    share of NaN): `month_id`, `month`, `zilretta`, `category`, `share`, `share_pct`."""
    months = seg[["month_id", "month"]].drop_duplicates().sort_values("month_id")
    sums = (
        select(seg, specialty, age_band, gender)
        .groupby("month_id")[["zilretta", "category"]]
        .sum()
        .reindex(months["month_id"], fill_value=0)
        .reset_index()
    )
    out = months.merge(sums, on="month_id").reset_index(drop=True)
    out["share"] = out["zilretta"] / out["category"].where(out["category"] > 0)
    out["share_pct"] = 100 * out["share"]
    out["label"] = [views.month_label(m) for m in out["month_id"]]
    return out


def window(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """The last months of the series named by the period ("1Y", "3Y", "5Y" or "All")."""
    months = PERIOD_MONTHS[period]
    return frame if months is None else frame.tail(months)


def quarterly(frame: pd.DataFrame) -> pd.DataFrame:
    """Complete quarters only (a part quarter would look like a fall), with the share of each."""
    quarter = frame["month"].dt.to_period("Q")
    grouped = frame.groupby(quarter).agg(
        zilretta=("zilretta", "sum"), category=("category", "sum"), months=("month_id", "count")
    )
    grouped = grouped[grouped["months"] == 3].reset_index(names="quarter")
    grouped["month"] = grouped["quarter"].dt.start_time
    grouped["month_id"] = grouped["month"].dt.year * 100 + grouped["month"].dt.month
    grouped["share"] = grouped["zilretta"] / grouped["category"].where(grouped["category"] > 0)
    grouped["share_pct"] = 100 * grouped["share"]
    grouped["label"] = [
        f"Q{(m - 1) // 3 + 1} {y}"
        for y, m in zip(grouped["month"].dt.year, grouped["month"].dt.month, strict=True)
    ]
    return grouped.drop(columns="quarter")


def trend_line(frame: pd.DataFrame) -> tuple[pd.Series, float | None]:
    """A straight line through the share (percent) and its slope in points a year. It is a guide to
    the direction over the period; it says nothing about what comes next. None when fewer than
    three months have a share."""
    x = (frame["month"].dt.year * 12 + frame["month"].dt.month).to_numpy(float)
    y = frame["share_pct"].to_numpy(float)
    ok = ~np.isnan(y)
    if ok.sum() < 3:
        return pd.Series(np.nan, index=frame.index), None
    slope, intercept = np.polyfit(x[ok], y[ok], 1)
    return pd.Series(intercept + slope * x, index=frame.index), float(12 * slope)


def _share_at(frame: pd.DataFrame, month_id: int) -> float | None:
    row = frame[frame["month_id"] == month_id]
    if row.empty or pd.isna(row["share"].iloc[0]):
        return None
    return float(row["share"].iloc[0])


def _months_before(month_id: int, months: int) -> int:
    index = (month_id // 100) * 12 + (month_id % 100 - 1) - months
    return (index // 12) * 100 + index % 12 + 1


def kpis(full: pd.DataFrame, win: pd.DataFrame) -> dict:
    """The headline numbers: the latest month against a year earlier, the peak (over the whole
    series, among months with enough visits), the visits in the period and the slope of the line."""
    latest = full.iloc[-1]
    latest_id = int(latest["month_id"])
    peak_pool = full[(full["category"] >= MIN_PEAK_VISITS) & full["share"].notna()]
    peak = peak_pool.loc[peak_pool["share"].idxmax()] if len(peak_pool) else None
    year_ago = _share_at(full, _months_before(latest_id, 12))
    latest_share = None if pd.isna(latest["share"]) else float(latest["share"])
    z, c = int(win["zilretta"].sum()), int(win["category"].sum())
    _, slope = trend_line(win)
    return {
        "latest_month_id": latest_id,
        "latest_share": latest_share,
        "latest_visits": int(latest["category"]),
        "year_ago_share": year_ago,
        "change_vs_year_ago_pp": (
            None if latest_share is None or year_ago is None else 100 * (latest_share - year_ago)
        ),
        "peak_share": None if peak is None else float(peak["share"]),
        "peak_month_id": None if peak is None else int(peak["month_id"]),
        "change_since_peak": (
            None
            if peak is None or latest_share is None
            else float(latest_share / peak["share"] - 1)
        ),
        "period_zilretta_visits": z,
        "period_category_visits": c,
        "period_share": z / c if c else None,
        "slope_pp_per_year": slope,
        "n_months": int(len(win)),
        "median_monthly_visits": float(win["category"].median()) if len(win) else 0.0,
    }


def is_small(k: dict) -> bool:
    """True when the chosen group is so small that month-to-month moves are mostly noise."""
    return k["median_monthly_visits"] < SMALL_GROUP_VISITS


def movers(
    seg: pd.DataFrame,
    by: str,
    *,
    min_visits: int = MIN_GROUP_VISITS,
    compare: int = COMPARE_MONTHS,
    calendar: list[int] | None = None,
) -> pd.DataFrame:
    """Each group's share in the last `compare` months against the `compare` months before, for
    groups with at least `min_visits` visits in both. `by` is "specialty", "age_band" or "gender".
    Sorted by Zilretta's visits in the latest period, largest first."""
    ids = sorted(calendar) if calendar is not None else sorted(seg["month_id"].unique())
    if len(ids) < 2 * compare:
        return pd.DataFrame(columns=["group", "label", "share_pct", "prior_pct", "delta_pp"])
    last, prior = ids[-compare:], ids[-2 * compare : -compare]

    def period(months):
        part = seg[seg["month_id"].isin(months)]
        return part.groupby(by)[["zilretta", "category"]].sum()

    now, before = period(last), period(prior)
    both = now.join(before, lsuffix="_now", rsuffix="_before", how="inner")
    both = both[
        (both["category_now"] >= min_visits) & (both["category_before"] >= min_visits)
    ].copy()
    both["share_pct"] = 100 * both["zilretta_now"] / both["category_now"]
    both["prior_pct"] = 100 * both["zilretta_before"] / both["category_before"]
    both["delta_pp"] = both["share_pct"] - both["prior_pct"]
    out = both.reset_index().rename(columns={by: "group", "zilretta_now": "zilretta_visits"})
    out["label"] = out["group"].map(pretty)
    out["category_visits"] = out["category_now"]
    columns = ["group", "label", "share_pct", "prior_pct", "delta_pp", "zilretta_visits"]
    return (
        out[[*columns, "category_visits"]]
        .sort_values(["zilretta_visits", "group"], ascending=[False, True])
        .reset_index(drop=True)
    )


def callouts(table: pd.DataFrame) -> dict[str, str | None]:
    """The biggest rise and the biggest fall among the ranked groups, in plain words."""
    out: dict[str, str | None] = {"rising": None, "falling": None}
    if table.empty:
        return out
    up, down = table.loc[table["delta_pp"].idxmax()], table.loc[table["delta_pp"].idxmin()]
    if up["delta_pp"] > 0:
        out["rising"] = f"Biggest rise: {up['label']} {up['delta_pp']:+.2f} pp"
    if down["delta_pp"] < 0:
        out["falling"] = f"Biggest fall: {down['label']} {down['delta_pp']:+.2f} pp"
    return out


def composition(sel: pd.DataFrame, by: str, top: int = 5) -> pd.DataFrame:
    """Where Zilretta's visits come from: each group's share of Zilretta's visits in the rows
    given, the largest `top` groups and the rest together as "Other"."""
    z = sel.groupby(by)["zilretta"].sum()
    z = z[z > 0].sort_values(ascending=False)
    total = float(z.sum())
    if total == 0:
        return pd.DataFrame(columns=["label", "visits", "group", "pct"])
    head, rest = z.head(top), z.iloc[top:]
    rows = [(pretty(name), float(v), name) for name, v in head.items()]
    if len(rest):
        rows.append(("Other", float(rest.sum()), None))
    out = pd.DataFrame(rows, columns=["label", "visits", "group"])
    out["pct"] = 100 * out["visits"] / total
    return out


def class_mix(national: pd.DataFrame, compare: int = COMPARE_MONTHS) -> pd.DataFrame:
    """The three classes' share of the three-class visits in the last `compare` months and the
    change from the `compare` months before (whole market only: the split is not in the segment
    data)."""
    names = [
        ("zilretta", "Zilretta"),
        ("corticosteroid", "Generic steroid injections"),
        ("nsaid_otc", "NSAID and OTC"),
    ]
    if len(national) < 2 * compare:
        return pd.DataFrame(columns=["label", "visits", "pct", "prior_pct", "delta_pp"])
    last, prior = national.tail(compare), national.iloc[-2 * compare : -compare]
    total_now = float(last[[c for c, _ in names]].sum().sum())
    total_before = float(prior[[c for c, _ in names]].sum().sum())
    rows = []
    for column, label in names:
        now, before = float(last[column].sum()), float(prior[column].sum())
        rows.append(
            {
                "label": label,
                "visits": now,
                "pct": 100 * now / total_now,
                "prior_pct": 100 * before / total_before,
            }
        )
    out = pd.DataFrame(rows)
    out["delta_pp"] = out["pct"] - out["prior_pct"]
    return out


def events(frame: pd.DataFrame) -> pd.DataFrame:
    """The dated markers inside the months shown: the break detected in March 2022, the start of
    the March to July 2024 data-capture dip, and the peak of the series shown. Context only."""
    shown = set(frame["month_id"])
    rows = [(pd.to_datetime(str(m), format="%Y%m"), text) for m, text in views.EVENTS if m in shown]
    pool = frame[(frame["category"] >= MIN_PEAK_VISITS) & frame["share"].notna()]
    if len(pool):
        peak = pool.loc[pool["share"].idxmax()]
        rows.append((peak["month"], f"Peak: {views.pct(peak['share'])} in {peak['label']}"))
    out = pd.DataFrame(rows, columns=["month", "label"])
    return out.sort_values("month").reset_index(drop=True)


def dip_range(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """The data-capture dip as a start and end date, if any of it is in the months shown."""
    low, high = views.DIP_MONTHS
    inside = frame[frame["month_id"].between(low, high)]
    return None if inside.empty else (inside["month"].min(), inside["month"].max())


def sentence(k: dict, who: str) -> str:
    """The headline in one plain sentence for the chosen group."""
    if k["latest_share"] is None:
        return f"{who} had no visits in {views.month_label(k['latest_month_id'])}."
    parts = [
        f"{who}: Zilretta's share of visits was {views.pct(k['latest_share'])} in "
        f"{views.month_label(k['latest_month_id'])}"
    ]
    if k["change_vs_year_ago_pp"] is not None:
        gap = k["change_vs_year_ago_pp"]
        direction = "above" if gap > 0 else "below"
        parts.append(f", {abs(gap):.2f} points {direction} a year earlier")
    if k["change_since_peak"] is not None:
        change = k["change_since_peak"]
        peak = f"{views.pct(k['peak_share'])} ({views.month_label(k['peak_month_id'])})"
        if round(100 * abs(change)) == 0:
            parts.append(f" and level with its peak of {peak}")
        else:
            side = "below" if change < 0 else "above"
            parts.append(f" and {abs(100 * change):.0f}% {side} its peak of {peak}")
    return "".join(parts) + "."


def yearly(frame: pd.DataFrame) -> pd.DataFrame:
    """The share and visits by calendar year; a year with fewer than 12 months is marked part."""
    year = frame["month_id"] // 100
    out = frame.groupby(year).agg(
        zilretta=("zilretta", "sum"), category=("category", "sum"), months=("month_id", "count")
    )
    out["share_pct"] = 100 * out["zilretta"] / out["category"].where(out["category"] > 0)
    out = out.reset_index().rename(columns={"month_id": "year"})
    out["partial_year"] = out["months"] < 12
    return out


def csv(frame: pd.DataFrame) -> bytes:
    """The chosen series as a CSV: the month, Zilretta's visits, the three-class visits and the
    share in percent."""
    out = pd.DataFrame(
        {
            "month": frame["month"].dt.strftime("%Y-%m"),
            "zilretta_visits": frame["zilretta"],
            "three_class_visits": frame["category"],
            "zilretta_share_pct": frame["share_pct"].round(3),
        }
    )
    return out.to_csv(index=False).encode("utf-8")
