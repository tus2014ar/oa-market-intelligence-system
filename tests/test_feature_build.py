"""Tests for the feature tables (features/build.py): shape, keys, no missing values, no leakage
into the targets' month, and a round trip through a SQLite warehouse."""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from oa_market_intelligence.features.build import (
    ADOPTED_GROUPS,
    MONTHLY_TABLE,
    SEGMENT_TABLE,
    build_feature_tables,
    monthly_base_columns,
    write_adopted_feature_tables,
    write_feature_tables,
)
from oa_market_intelligence.features.candidate_families import KEYS
from oa_market_intelligence.features.groups import (
    GROUP_COLUMNS_T1,
    GROUP_COLUMNS_T2,
    T1_GROUPS,
    T2_GROUPS,
)


def _seg(months=20, seed=0):
    rng = np.random.default_rng(seed)
    ids, (year, month) = [], (2019, 8)
    for _ in range(months):
        ids.append(year * 100 + month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    rows = []
    for m in ids:
        for specialty in ("S1", "S2"):
            for age in ("40 TO 59", "65 TO 74"):
                for gender in ("FEMALE", "MALE"):
                    t = int(rng.integers(30, 300))
                    rows.append((m, specialty, age, gender, int(rng.binomial(t, 0.03)), t))
    return pd.DataFrame(
        rows,
        columns=[
            "month_id",
            "specialty_name",
            "age_band",
            "gender",
            "branded_injectable_visits",
            "total_category_visits",
        ],
    )


def _monthly(seg):
    grouped = seg.groupby("month_id")[["branded_injectable_visits", "total_category_visits"]].sum()
    rest = grouped["total_category_visits"] - grouped["branded_injectable_visits"]
    return pd.DataFrame(
        {
            "month_id": grouped.index,
            "branded_injectable_visits": grouped["branded_injectable_visits"].to_numpy(),
            "generic_corticosteroid_visits": (rest * 0.7).round().to_numpy(),
            "nsaid_otc_visits": (rest - (rest * 0.7).round()).to_numpy(),
        }
    )


def _group_visits(months):
    return pd.DataFrame(
        {
            "zilretta": 10.0,
            "triamcinolone_ir": 60.0,
            "other_injectable_corticosteroid": 30.0,
            "other": 5.0,
        },
        index=pd.Index(months, name="month_id"),
    )


def _build(seg=None):
    seg = _seg() if seg is None else seg
    monthly = _monthly(seg)
    return build_feature_tables(seg, monthly, None, _group_visits(sorted(seg["month_id"].unique())))


def test_the_segment_table_has_keys_base_groups_and_targets_without_missing_values():
    segment_table, _ = _build()
    assert not segment_table.duplicated(subset=KEYS).any()
    for group in ("G1", "G2", "G3", "G4", "G5", "G7"):  # G6 needs signals; left out here
        assert set(GROUP_COLUMNS_T2[group]) <= set(segment_table.columns)
    assert {"y_share", "y_visits", "y_market", "seg_share_lag1", "month_sin"} <= set(
        segment_table.columns
    )
    assert "y_label" not in segment_table.columns
    value_columns = [
        c for c in segment_table.columns if c not in KEYS + ["segment", "specialty_grouped"]
    ]
    assert not segment_table[value_columns].isna().any().any()


def test_the_monthly_table_has_the_base_lags_the_groups_and_the_target():
    _, monthly_table = _build()
    assert {"share_lag1", "share_lag2", "share_lag3", "share_roll3", "y_share"} <= set(
        monthly_table.columns
    )
    for group in ("G1", "G2", "G5", "G7"):
        assert set(GROUP_COLUMNS_T1[group]) <= set(monthly_table.columns)
    assert monthly_table["month_id"].is_unique
    assert not monthly_table.isna().any().any()


def test_the_base_lags_are_the_previous_months_shares():
    seg = _seg()
    monthly = _monthly(seg)
    base = monthly_base_columns(monthly)
    total = (
        monthly["branded_injectable_visits"]
        + monthly["generic_corticosteroid_visits"]
        + monthly["nsaid_otc_visits"]
    )
    share = (monthly["branded_injectable_visits"] / total).to_numpy()
    row = base.iloc[10]
    assert row["share_lag1"] == pytest.approx(share[9])
    assert row["share_lag3"] == pytest.approx(share[7])
    assert row["share_roll3"] == pytest.approx(np.mean(share[7:10]))
    assert row["y_share"] == pytest.approx(share[10])


def test_a_month_s_features_do_not_move_when_that_month_and_later_are_rewritten():
    seg = _seg()
    cut = 202003
    base_seg, base_monthly = _build(seg)
    altered = seg.copy()
    later = altered["month_id"] >= cut
    altered.loc[later, "branded_injectable_visits"] = 0
    alt_seg, alt_monthly = _build(altered)
    feature_columns = [c for c in base_seg.columns if not c.startswith("y_") and c not in KEYS]
    a = base_seg[base_seg["month_id"] == cut].set_index(KEYS)[feature_columns]
    b = alt_seg[alt_seg["month_id"] == cut].set_index(KEYS)[feature_columns]
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())
    feature_columns_m = [c for c in base_monthly.columns if not c.startswith("y_")]
    a_m = base_monthly[base_monthly["month_id"] == cut][feature_columns_m].reset_index(drop=True)
    b_m = alt_monthly[alt_monthly["month_id"] == cut][feature_columns_m].reset_index(drop=True)
    pd.testing.assert_frame_equal(a_m, b_m)


def test_the_tables_round_trip_through_a_sqlite_warehouse_and_the_build_is_deterministic():
    first = _build()
    again = _build()
    pd.testing.assert_frame_equal(first[0], again[0])
    pd.testing.assert_frame_equal(first[1], again[1])
    engine = create_engine("sqlite://")
    write_feature_tables(engine, *first)
    with engine.connect() as conn:
        segment = pd.read_sql(f"SELECT * FROM {SEGMENT_TABLE}", conn)
        monthly = pd.read_sql(f"SELECT * FROM {MONTHLY_TABLE}", conn)
    assert len(segment) == len(first[0]) and len(monthly) == len(first[1])
    assert set(segment.columns) == set(first[0].columns)
    assert T2_GROUPS and T1_GROUPS


def test_only_the_adopted_group_is_built_with_the_base_set_when_asked():
    assert ADOPTED_GROUPS == ("G4",)
    seg = _seg()
    segment_table, monthly_table = build_feature_tables(seg, _monthly(seg), groups=ADOPTED_GROUPS)
    assert set(GROUP_COLUMNS_T2["G4"]) <= set(segment_table.columns)
    other = {c for g in T2_GROUPS if g != "G4" for c in GROUP_COLUMNS_T2[g]}
    assert not other & set(segment_table.columns)
    assert not {c for c in monthly_table.columns if c.startswith("g")}  # no T1 group adopted
    assert {"share_lag1", "y_share"} <= set(monthly_table.columns)


def test_the_adopted_tables_are_written_from_a_warehouse_without_outside_data():
    seg = _seg()
    monthly = _monthly(seg)
    engine = create_engine("sqlite://")
    specialties = pd.DataFrame({"specialty_id": [1, 2], "specialty_name": ["S1", "S2"]})
    demographics = pd.DataFrame(
        {
            "demographic_id": [1, 2, 3, 4],
            "age_band": ["40 TO 59", "40 TO 59", "65 TO 74", "65 TO 74"],
            "gender": ["FEMALE", "MALE", "FEMALE", "MALE"],
        }
    )
    merged = seg.merge(specialties.rename(columns={"specialty_name": "specialty_name"})).merge(
        demographics, on=["age_band", "gender"]
    )
    gold = merged[
        [
            "month_id",
            "specialty_id",
            "demographic_id",
            "branded_injectable_visits",
            "total_category_visits",
        ]
    ].assign(
        segment_visit_share=lambda f: f["branded_injectable_visits"] / f["total_category_visits"]
    )
    with engine.begin() as conn:
        specialties.to_sql("dim_specialty", conn, index=False)
        demographics.to_sql("dim_demographics", conn, index=False)
        gold.to_sql("gold_segment_adoption", conn, index=False)
        monthly.to_sql("gold_visit_share_monthly", conn, index=False)
    counts = write_adopted_feature_tables(engine)
    assert counts[SEGMENT_TABLE] > 0 and counts[MONTHLY_TABLE] > 0
    with engine.connect() as conn:
        stored = pd.read_sql(f"SELECT * FROM {SEGMENT_TABLE}", conn)
    assert len(stored) == counts[SEGMENT_TABLE]
    assert set(GROUP_COLUMNS_T2["G4"]) <= set(stored.columns)
