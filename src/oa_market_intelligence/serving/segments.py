"""The Segments tab, as tested functions: the ranked groups, how concentrated Zilretta's visits are,
the specialty-by-age grid and the groups over time (DL-98).

Every function takes the segment-month frame from `data.segment_monthly` and fits nothing. A
group is a specialty, an age band or a gender; its share is Zilretta's visits over the visits of
Zilretta, generic steroid injections and NSAID/OTC together (`category`).
"""

from __future__ import annotations

import pandas as pd

from oa_market_intelligence.serving import trend, views

GROUPS = {"specialty": "Specialty", "age_band": "Age band", "gender": "Gender"}
MIN_VISITS = 1000  # visits in the period under which a group is left out
SMALL_VISITS = 5000  # visits in the period under which a group is drawn as small (noisy)
MIN_CELL_VISITS = 300  # visits in a specialty-by-age cell under which its share is not shown
SHARE_OF_VISITS = 0.8  # "how many groups make up four fifths of Zilretta's visits"


def months_in(seg: pd.DataFrame, period: str, calendar: list[int] | None = None) -> list[int]:
    """The month ids of the last months of the series named by the period. `calendar` is every
    month of the whole series: pass it when `seg` has been narrowed by a filter, so a month a small
    group happens to lack does not shift the window."""
    ids = sorted(calendar) if calendar is not None else sorted(seg["month_id"].unique())
    months = trend.PERIOD_MONTHS[period]
    return ids if months is None else ids[-months:]


def group_table(
    seg: pd.DataFrame,
    by: str,
    period: str = "All",
    *,
    min_visits: int = MIN_VISITS,
    calendar: list[int] | None = None,
) -> pd.DataFrame:
    """One row per group with at least `min_visits` visits in the period: its share, its visits, its
    share of Zilretta's visits among the groups listed, whether it is small, and its change (the
    last 12 months against the 12 before, in points of share, whatever the period; NaN when the
    group is under the floor in either). Sorted by share, highest first. `attrs["left_out"]` holds
    the number of groups under the floor."""
    if by not in GROUPS:
        raise ValueError(f"by must be one of {sorted(GROUPS)}, got {by!r}")
    part = seg[seg["month_id"].isin(months_in(seg, period, calendar))]
    grouped = part.groupby(by)[["zilretta", "category"]].sum()
    kept = grouped[grouped["category"] >= min_visits]
    out = kept.reset_index().rename(columns={by: "group", "zilretta": "zilretta_visits"})
    out["category_visits"] = out.pop("category")
    out["share_pct"] = 100 * out["zilretta_visits"] / out["category_visits"]
    total = out["zilretta_visits"].sum()
    out["pct_of_zilretta"] = 100 * out["zilretta_visits"] / total if total else 0.0
    out["small"] = out["category_visits"] < SMALL_VISITS
    out["label"] = out["group"].map(trend.pretty)
    change = trend.movers(seg, by, calendar=calendar)[["group", "delta_pp"]]
    out = out.merge(change, on="group", how="left")
    out = out.sort_values(["share_pct", "group"], ascending=[False, True]).reset_index(drop=True)
    out.attrs["left_out"] = int(len(grouped) - len(kept))
    return out


def concentration(table: pd.DataFrame) -> dict:
    """How concentrated Zilretta's visits are among the listed groups: the largest source, the three
    largest together, and how many groups make up four fifths of the visits."""
    if table.empty or table["zilretta_visits"].sum() == 0:
        return {
            "n_groups": len(table),
            "top": None,
            "top_pct": None,
            "top3_pct": None,
            "n_for_80": None,
        }
    by_visits = table.sort_values(["zilretta_visits", "group"], ascending=[False, True])
    pct = 100 * by_visits["zilretta_visits"] / by_visits["zilretta_visits"].sum()
    cumulative = pct.cumsum().to_numpy()
    n_for = int((cumulative < 100 * SHARE_OF_VISITS - 1e-9).sum() + 1)
    return {
        "n_groups": int(len(table)),
        "top": by_visits["label"].iloc[0],
        "top_pct": float(pct.iloc[0]),
        "top3_pct": float(pct.head(3).sum()),
        "n_for_80": min(n_for, int(len(table))),
    }


def _sized(table: pd.DataFrame) -> pd.DataFrame:
    """The groups big enough to name in a headline (not small); all of them if none is."""
    big = table[~table["small"]]
    return big if len(big) else table


def extremes(table: pd.DataFrame) -> dict:
    """The groups with the highest and the lowest share and the biggest rise and fall. Small groups
    (under 5,000 visits) are not named: one prescription moves their share too much."""
    out: dict = {"highest": None, "lowest": None, "rising": None, "falling": None}
    if table.empty:
        return out
    sized = _sized(table)
    top, bottom = sized.iloc[0], sized.iloc[-1]
    out["highest"] = (top["label"], float(top["share_pct"]))
    out["lowest"] = (bottom["label"], float(bottom["share_pct"]))
    moved = sized.dropna(subset=["delta_pp"])
    if len(moved):
        up, down = moved.loc[moved["delta_pp"].idxmax()], moved.loc[moved["delta_pp"].idxmin()]
        if up["delta_pp"] > 0:
            out["rising"] = (up["label"], float(up["delta_pp"]))
        if down["delta_pp"] < 0:
            out["falling"] = (down["label"], float(down["delta_pp"]))
    return out


def cross_table(
    seg: pd.DataFrame,
    period: str = "All",
    *,
    top_rows: int = 12,
    min_cell: int = MIN_CELL_VISITS,
    min_column: int = MIN_VISITS,
    calendar: list[int] | None = None,
) -> pd.DataFrame:
    """Zilretta's share for each specialty (the `top_rows` with the most Zilretta visits) and age
    band (those with at least `min_column` visits), long form: `row`, `column`, `share_pct`,
    `zilretta`, `category`, `shown`. A cell with fewer than `min_cell` visits is kept but not
    shown (its share is NaN), so the grid never presents a few visits as a rate."""
    part = seg[seg["month_id"].isin(months_in(seg, period, calendar))]
    rows = part.groupby("specialty")["zilretta"].sum().sort_values(ascending=False).head(top_rows)
    columns = part.groupby("age_band")["category"].sum()
    columns = sorted(columns[columns >= min_column].index)
    cells = (
        part[part["specialty"].isin(rows.index) & part["age_band"].isin(columns)]
        .groupby(["specialty", "age_band"])[["zilretta", "category"]]
        .sum()
        .reindex(pd.MultiIndex.from_product([rows.index, columns], names=["specialty", "age_band"]))
        .fillna(0)
        .reset_index()
        .rename(columns={"specialty": "row", "age_band": "column"})
    )
    cells["shown"] = cells["category"] >= min_cell
    cells["share_pct"] = 100 * cells["zilretta"] / cells["category"].where(cells["shown"])
    cells["row_label"] = cells["row"].map(trend.pretty)
    cells["column_label"] = cells["column"].map(trend.pretty)
    return cells


def group_trend(
    seg: pd.DataFrame, by: str, groups: list[str], calendar: list[int] | None = None
) -> pd.DataFrame:
    """The chosen groups' share by calendar year; a year under 12 months of data is part."""
    sub = seg[seg[by].isin(groups)].copy()
    sub["year"] = sub["month_id"] // 100
    counted = pd.DataFrame({"month_id": sorted(calendar)}) if calendar is not None else seg
    months = counted.assign(year=counted["month_id"] // 100).groupby("year")["month_id"].nunique()
    out = sub.groupby([by, "year"])[["zilretta", "category"]].sum().reset_index()
    out = out.rename(columns={by: "group"})
    out["share_pct"] = 100 * out["zilretta"] / out["category"].where(out["category"] > 0)
    out["label"] = out["group"].map(trend.pretty)
    out["months"] = out["year"].map(months)
    out["partial_year"] = out["months"] < 12
    return out.sort_values(["label", "year"]).reset_index(drop=True)


SPAN = {
    "1Y": "the last year",
    "3Y": "the last 3 years",
    "5Y": "the last 5 years",
    "All": "all months",
}


def sentence(table: pd.DataFrame, by: str, period: str) -> str:
    """The ranking in one plain sentence: the range among the groups big enough to name, and how
    many were left out or drawn small."""
    name = GROUPS[by].lower()
    if table.empty:
        return f"No {name} has enough visits over {SPAN[period]} to be ranked."
    sized = _sized(table)
    high, low = sized.iloc[0], sized.iloc[-1]
    text = (
        f"Over {SPAN[period]}, among {len(sized)} {name} groups with at least {SMALL_VISITS:,} "
        f"visits, Zilretta's share runs from {views.pct(low['share_pct'] / 100)} ({low['label']}) "
        f"to {views.pct(high['share_pct'] / 100)} ({high['label']})."
    )
    small = int(table["small"].sum()) if len(sized) != len(table) or table["small"].any() else 0
    left = table.attrs.get("left_out", 0)
    notes = []
    if small:
        notes.append(f"{small} smaller (drawn faded)")
    if left:
        notes.append(f"{left} under {MIN_VISITS:,} visits left out")
    return text + (" Also: " + ", ".join(notes) + "." if notes else "")


def csv(table: pd.DataFrame) -> bytes:
    """The ranked groups as a CSV."""
    out = pd.DataFrame(
        {
            "group": table["label"],
            "zilretta_share_pct": table["share_pct"].round(3),
            "zilretta_visits": table["zilretta_visits"],
            "three_class_visits": table["category_visits"],
            "pct_of_zilretta_visits": table["pct_of_zilretta"].round(2),
            "change_last_12_months_pp": table["delta_pp"].round(3),
        }
    )
    return out.to_csv(index=False).encode("utf-8")
