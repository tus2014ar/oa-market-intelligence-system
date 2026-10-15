"""The seven feature groups of the feature plan (DL-75, docs/feature_engineering_plan.md).

Target T2 (segment share, one row per segment and month) takes G1 to G7, target T1 (the national
share) takes G1, G2, G5, G6 and G7. Every column uses information from month t-1 or earlier. The
IQVIA-derived groups are checked by the generic test that rewrites everything from month t onward;
the outside series (G6) come from the as-of table, so a value is used only once it was public.

Missing values follow the fixed rule of the plan: a column that can be missing is zero-filled and
the group has a `*_missing` indicator (1 where a value could not be computed), so no NaN reaches a
model. The groups are built here; whether any of them helps is decided by the pre-registered test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oa_market_intelligence.availability import add_months
from oa_market_intelligence.features.candidate_families import (
    KEYS,
    SHRINK_K,
    _fa1,
    _fa2,
    _fa3,
    _fa4,
    _prepare,
    market_outside_features,
    medicare_adoption_features,
    seasonal_gap,
)

COVID_MONTHS = (202003, 202005)
DIP_MONTHS = (202403, 202407)
PROMO_WINDOW = 6  # months of Open Payments totals summed in G6

G1_T1 = [
    "g1_share_roll6",
    "g1_share_std6",
    "g1_share_chg1",
    "g1_share_chg3",
    "g1_log_visits_chg",
    "g1_missing",
]
G1_T2 = ["g1_share_lag2", "g1_share_lag3", *G1_T1]

GROUP_COLUMNS_T2 = {
    "G1": G1_T2,
    "G2": ["g2_is_dec", "g2_is_mar_apr", "g2_seasonal_gap", "g2_missing"],
    "G3": [
        "g3_cum_share",
        "g3_months_active",
        "g3_months_since_zilretta",
        "g3_log_cum_zilretta",
        *[f"g3_{name}_k{k}" for k in SHRINK_K for name in ("logit_shrunk", "weight")],
        "g3_missing",
    ],
    "G4": ["g4_logit_spec_share_lag1", "g4_spec_change_3m", "g4_missing"],
    "G5": ["g5_tri_share_lag1", "g5_zil_vs_tri_lag1", "g5_steroid_chg3", "g5_missing"],
    "G6": [
        "g6_price_ratio",
        "g6_price_ratio_chg_4q",
        "g6_sales_yoy",
        "g6_months_since_event",
        "g6_promo_6m_log",
        "g6_medicare_adoption",
        "g6_price_chg_missing",
        "g6_sales_missing",
        "g6_promo_missing",
        "g6_medicare_missing",
    ],
    "G7": ["g7_covid", "g7_dip", "g7_pediatrics"],
}
GROUP_COLUMNS_T1 = {
    "G1": G1_T1,
    "G2": GROUP_COLUMNS_T2["G2"],
    "G5": GROUP_COLUMNS_T2["G5"],
    "G6": GROUP_COLUMNS_T2["G6"],
    "G7": ["g7_covid", "g7_dip"],
}
T2_GROUPS = tuple(GROUP_COLUMNS_T2)
T1_GROUPS = tuple(GROUP_COLUMNS_T1)

# the shrinkage columns are chosen inside the training window (one k at a time); see validate.py
SHRINK_COLUMNS = {k: (f"g3_logit_shrunk_k{k}", f"g3_weight_k{k}") for k in SHRINK_K}


def _rename(frame: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    return frame.rename(columns=mapping)


# --------------------------------------------------------------------------------- G1


def _history_columns(share: pd.DataFrame, log_visits: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The G1 values from wide tables (rows = month position, columns = unit). Everything is
    shifted so the value at position i uses positions up to i-1 only."""
    past = share.shift(1)
    return {
        "g1_share_lag2": share.shift(2),
        "g1_share_lag3": share.shift(3),
        "g1_share_roll6": past.rolling(6, min_periods=3).mean(),
        "g1_share_std6": past.rolling(6, min_periods=3).std(),
        "g1_share_chg1": share.shift(1) - share.shift(2),
        "g1_share_chg3": share.shift(1) - share.shift(4),
        "g1_log_visits_chg": log_visits.shift(1) - log_visits.shift(2),
    }


def _g1_segment(seg: pd.DataFrame) -> pd.DataFrame:
    n_months = int(seg["mi"].max()) + 1
    share = (seg["z"] / seg["t"].replace(0, np.nan)).rename("v")
    frame = seg[["segment", "mi"]].assign(share=share, lv=np.log1p(seg["t"]))
    share_wide = frame.pivot(index="mi", columns="segment", values="share").reindex(range(n_months))
    visits_wide = frame.pivot(index="mi", columns="segment", values="lv").reindex(range(n_months))
    pieces = {}
    for name, wide in _history_columns(share_wide, visits_wide).items():
        stacked = wide.stack(future_stack=True).rename(name)
        pieces[name] = stacked
    table = pd.concat(pieces.values(), axis=1).reset_index()
    table = table.rename(columns={"level_0": "mi", "level_1": "segment"})
    merged = seg[["segment", "mi"]].merge(table, how="left", on=["segment", "mi"])
    values = merged[G1_T2[:-1]]
    missing = values.isna().any(axis=1).astype(float)
    out = values.fillna(0.0)
    out["g1_missing"] = missing
    out.index = seg.index
    return out[G1_T2]


def _g1_national(monthly: pd.DataFrame) -> pd.DataFrame:
    frame = monthly.sort_values("month_id").reset_index(drop=True)
    share = (frame["z"] / frame["t"].replace(0, np.nan)).to_frame("national")
    visits = np.log1p(frame["t"]).to_frame("national")
    values = pd.DataFrame(
        {name: wide["national"] for name, wide in _history_columns(share, visits).items()}
    )[G1_T1[:-1]]
    missing = values.isna().any(axis=1).astype(float)
    out = values.fillna(0.0)
    out["g1_missing"] = missing
    out.index = frame.index
    return out[G1_T1]


# --------------------------------------------------------------------------------- G2


def _calendar(month_ids: pd.Series) -> pd.DataFrame:
    month = month_ids % 100
    return pd.DataFrame(
        {
            "g2_is_dec": (month == 12).astype(float),
            "g2_is_mar_apr": month.isin([3, 4]).astype(float),
        }
    )


def _g2_segment(seg: pd.DataFrame) -> pd.DataFrame:
    gap = _rename(_fa4(seg), {"fa4_seasonal_gap": "g2_seasonal_gap", "fa4_missing": "g2_missing"})
    calendar = _calendar(seg["month_id"]).set_axis(seg.index)
    return pd.concat([calendar, gap], axis=1)[GROUP_COLUMNS_T2["G2"]]


def _g2_national(monthly: pd.DataFrame) -> pd.DataFrame:
    shares = pd.DataFrame(
        {
            "year": monthly["month_id"] // 100,
            "month": monthly["month_id"] % 100,
            "z": monthly["z"],
            "t": monthly["t"],
        },
        index=monthly.index,
    )
    gap = seasonal_gap(shares)
    out = _calendar(monthly["month_id"]).set_axis(monthly.index)
    out["g2_seasonal_gap"] = gap.fillna(0.0)
    out["g2_missing"] = gap.isna().astype(float)
    return out[GROUP_COLUMNS_T2["G2"]]


# --------------------------------------------------------------------------------- G3, G4


def _g3(seg: pd.DataFrame) -> pd.DataFrame:
    own = seg.groupby("segment")[["z", "t"]].cumsum()
    cum_z, cum_t = own["z"] - seg["z"], own["t"] - seg["t"]
    cum_share = (cum_z / cum_t.replace(0, np.nan)).fillna(0.0)
    shrink = _rename(
        _fa1(seg),
        {
            **{f"fa1_logit_shrunk_k{k}": f"g3_logit_shrunk_k{k}" for k in SHRINK_K},
            **{f"fa1_weight_k{k}": f"g3_weight_k{k}" for k in SHRINK_K},
        },
    )
    history = _rename(
        _fa2(seg),
        {
            "fa2_months_active_prior": "g3_months_active",
            "fa2_months_since_branded": "g3_months_since_zilretta",
            "fa2_log_cum_branded": "g3_log_cum_zilretta",
        },
    )
    shrink_columns = [c for c in shrink.columns if "shrunk" in c]
    missing = shrink[shrink_columns].isna().any(axis=1).astype(float).rename("g3_missing")
    shrink[shrink_columns] = shrink[shrink_columns].fillna(0.0)
    out = pd.concat([cum_share.rename("g3_cum_share"), history, shrink, missing], axis=1)
    return out[GROUP_COLUMNS_T2["G3"]]


def _g4(seg: pd.DataFrame) -> pd.DataFrame:
    return _rename(
        _fa3(seg),
        {
            "fa3_logit_spec_share_lag1": "g4_logit_spec_share_lag1",
            "fa3_spec_change_3m": "g4_spec_change_3m",
            "fa3_missing": "g4_missing",
        },
    )[GROUP_COLUMNS_T2["G4"]]


# --------------------------------------------------------------------------------- G5


def competitor_features(group_visits: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """G5, one row per month. `group_visits` is `competitor_shares.monthly_group_visits`: one row
    per month with the columns zilretta, triamcinolone_ir and other_injectable_corticosteroid."""
    wide = group_visits.reindex(sorted(group_visits.index))
    zil = wide["zilretta"].astype(float)
    tri = wide["triamcinolone_ir"].astype(float)
    other = wide["other_injectable_corticosteroid"].astype(float)
    steroids = (zil + tri + other).replace(0, np.nan)
    tri_share = (tri / steroids).shift(1)
    zil_vs_tri = (zil / (zil + tri).replace(0, np.nan)).shift(1)
    steroid_chg = np.log(steroids.shift(1)) - np.log(steroids.shift(4))
    table = pd.DataFrame(
        {
            "g5_tri_share_lag1": tri_share,
            "g5_zil_vs_tri_lag1": zil_vs_tri,
            "g5_steroid_chg3": steroid_chg,
        }
    ).reindex(months)
    missing = table.isna().any(axis=1).astype(float)
    out = table.fillna(0.0)
    out["g5_missing"] = missing
    out.index.name = "month_id"
    return out.reset_index()[["month_id", *GROUP_COLUMNS_T2["G5"]]]


# --------------------------------------------------------------------------------- G6


def promotion_features(signals: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """The last six months of Open Payments totals known as of the previous month (log1p of the
    sum); missing (0 and an indicator) while fewer than six months are known."""
    rows = []
    payments = signals[signals["signal_id"] == "openpay.total_amount_usd"]
    for month in months:
        as_of = add_months(month, -1)
        known = payments[payments["available_from_month"] <= as_of]
        known = known.sort_values("period_end_month")
        last = known.tail(PROMO_WINDOW)
        ok = len(last) == PROMO_WINDOW
        rows.append(
            {
                "month_id": month,
                "g6_promo_6m_log": float(np.log1p(last["value"].sum())) if ok else 0.0,
                "g6_promo_missing": 0.0 if ok else 1.0,
            }
        )
    return pd.DataFrame(rows)


def national_medicare_adoption(signals: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """The all-specialty Medicare adoption rate known as of the previous month."""
    rate = signals[
        (signals["signal_id"] == "medicare.adoption_rate") & (signals["specialty_group"] == "")
    ]
    rows = []
    for month in months:
        known = rate[rate["available_from_month"] <= add_months(month, -1)].sort_values(
            "period_end_month"
        )
        value = float(known["value"].iloc[-1]) if len(known) else 0.0
        rows.append(
            {
                "month_id": month,
                "g6_medicare_adoption": value,
                "g6_medicare_missing": 0.0 if len(known) else 1.0,
            }
        )
    return pd.DataFrame(rows)


def outside_features(signals: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """G6 at month level (everything except the specialty-group Medicare rate)."""
    base = market_outside_features(signals, months).rename(
        columns={
            "fa5_price_ratio": "g6_price_ratio",
            "fa5_price_ratio_chg_4q": "g6_price_ratio_chg_4q",
            "fa5_sales_yoy": "g6_sales_yoy",
            "fa5_months_since_event": "g6_months_since_event",
            "fa5_price_chg_missing": "g6_price_chg_missing",
            "fa5_sales_missing": "g6_sales_missing",
        }
    )
    return base.merge(promotion_features(signals, months), on="month_id")


# --------------------------------------------------------------------------------- G7


def flag_columns(month_ids: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "g7_covid": month_ids.between(*COVID_MONTHS).astype(float),
            "g7_dip": month_ids.between(*DIP_MONTHS).astype(float),
        }
    )


# --------------------------------------------------------------------------------- entry points


def task_a_group_columns(
    seg: pd.DataFrame,
    signals: pd.DataFrame | None = None,
    group_visits: pd.DataFrame | None = None,
    groups: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """The group columns for every segment-month of a segment table (`load_segment_gold` output),
    keyed by month, specialty, age band and gender. G5 needs `group_visits`; G6 needs `signals`.
    A group whose input is not given is left out."""
    wanted = groups or T2_GROUPS
    prepared = _prepare(seg)
    months = sorted(prepared["month_id"].unique())
    out = prepared[KEYS].copy()
    if "G1" in wanted:
        out = pd.concat([out, _g1_segment(prepared)], axis=1)
    if "G2" in wanted:
        out = pd.concat([out, _g2_segment(prepared)], axis=1)
    if "G3" in wanted:
        out = pd.concat([out, _g3(prepared)], axis=1)
    if "G4" in wanted:
        out = pd.concat([out, _g4(prepared)], axis=1)
    if "G5" in wanted and group_visits is not None:
        out = out.merge(competitor_features(group_visits, months), on="month_id", how="left")
    if "G6" in wanted and signals is not None:
        out = out.merge(outside_features(signals, months), on="month_id", how="left")
        medicare = medicare_adoption_features(signals, prepared).rename(
            columns={
                "fa6_medicare_adoption": "g6_medicare_adoption",
                "fa6_medicare_missing": "g6_medicare_missing",
            }
        )
        out = pd.concat([out, medicare], axis=1)
    if "G7" in wanted:
        flags = flag_columns(prepared["month_id"]).set_axis(prepared.index)
        flags["g7_pediatrics"] = (prepared["specialty_name"] == "PEDIATRICS").astype(float)
        out = pd.concat([out, flags], axis=1)
    return out


def task_b_group_columns(
    monthly: pd.DataFrame,
    signals: pd.DataFrame | None = None,
    group_visits: pd.DataFrame | None = None,
    groups: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """The group columns for the national series. `monthly` has `month_id`,
    `branded_injectable_visits` and a total (`total_category_visits`, or the three category columns
    of `gold_visit_share_monthly`)."""
    wanted = groups or T1_GROUPS
    frame = monthly.copy()
    if "total_category_visits" not in frame.columns:
        frame["total_category_visits"] = (
            frame["branded_injectable_visits"]
            + frame["generic_corticosteroid_visits"]
            + frame["nsaid_otc_visits"]
        )
    frame = frame.sort_values("month_id").reset_index(drop=True)
    frame["z"] = frame["branded_injectable_visits"].astype(float)
    frame["t"] = frame["total_category_visits"].astype(float)
    months = frame["month_id"].tolist()
    out = frame[["month_id"]].copy()
    if "G1" in wanted:
        out = pd.concat([out, _g1_national(frame)], axis=1)
    if "G2" in wanted:
        out = pd.concat([out, _g2_national(frame)], axis=1)
    if "G5" in wanted and group_visits is not None:
        out = out.merge(competitor_features(group_visits, months), on="month_id", how="left")
    if "G6" in wanted and signals is not None:
        out = out.merge(outside_features(signals, months), on="month_id", how="left")
        out = out.merge(national_medicare_adoption(signals, months), on="month_id", how="left")
    if "G7" in wanted:
        out = pd.concat([out, flag_columns(frame["month_id"])], axis=1)
    return out


def columns_for(groups: tuple[str, ...], *, target: str) -> tuple[str, ...]:
    """The model columns of a set of groups. G3's shrinkage columns are handled separately (one
    k at a time, chosen inside the training window), so they are not listed here."""
    table = GROUP_COLUMNS_T2 if target == "T2" else GROUP_COLUMNS_T1
    shrink = {c for pair in SHRINK_COLUMNS.values() for c in pair}
    return tuple(c for g in groups for c in table[g] if c not in shrink)
