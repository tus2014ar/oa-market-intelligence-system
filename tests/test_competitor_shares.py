"""Tests for M6, the competitor shares (DL-73): the frozen product groups and the tables."""

import sqlite3

import pandas as pd
import pytest
from sqlalchemy import create_engine

from oa_market_intelligence.analysis import competitor_shares as cs

ROOT = cs.GROUPS_CSV.parents[2]
WAREHOUSE = ROOT / "data" / "published" / "warehouse.db"


def test_the_rules_place_known_products_in_their_groups():
    assert cs.assign_group("ZILRETTA", "branded_injectable")[0] == "zilretta"
    assert cs.assign_group("KENALOG", "generic_corticosteroid")[0] == "triamcinolone_ir"
    assert cs.assign_group("TRIAMCINOLONE ACTN", "generic_corticosteroid")[0] == "triamcinolone_ir"
    assert (
        cs.assign_group("DEPO-MEDROL", "generic_corticosteroid")[0]
        == "other_injectable_corticosteroid"
    )
    assert cs.assign_group("ASPIRIN", "nsaid_otc")[0] == "other"
    assert cs.assign_group("TRIAMCINOLONE NASAL", "nsaid_otc")[0] == "other"  # category decides


def test_the_frozen_group_file_agrees_with_the_rules_and_covers_every_product():
    groups = cs.load_groups()
    assert groups["product_name"].is_unique and len(groups) == 160
    for row in groups.itertuples():
        assert cs.assign_group(row.product_name, row.treatment_category) == (row.group, row.rule)
    assert (groups["group"] == "zilretta").sum() == 1
    assert set(groups["group"]) == set(cs.GROUPS)


@pytest.mark.skipif(not WAREHOUSE.exists(), reason="published warehouse not present")
def test_the_group_file_covers_the_warehouse_products_exactly_and_the_totals_add_up():
    engine = create_engine(f"sqlite:///{WAREHOUSE.as_posix()}")
    conn = sqlite3.connect(WAREHOUSE)
    names = {r[0] for r in conn.execute("select product_name from dim_product")}
    total = conn.execute("select sum(patient_visits) from fact_product_visits").fetchone()[0]
    assert names == set(cs.load_groups()["product_name"])
    monthly = cs.monthly_group_visits(engine)
    assert monthly.to_numpy().sum() == pytest.approx(total)
    assert list(monthly.columns) == list(cs.GROUPS) and len(monthly) == 72


def _tiny_engine():
    engine = create_engine("sqlite://")
    pd.DataFrame(
        {
            "product_id": [1, 2, 3, 4],
            "product_name": ["ZILRETTA", "KENALOG", "DEPO-MEDROL", "ASPIRIN"],
        }
    ).to_sql("dim_product", engine, index=False)
    pd.DataFrame(
        {
            "month_id": [202201, 202201, 202201, 202201, 202202, 202202],
            "product_id": [1, 2, 3, 4, 1, 2],
            "patient_visits": [10, 60, 30, 100, 20, 80],
        }
    ).to_sql("fact_product_visits", engine, index=False)
    return engine


def _tiny_groups():
    return pd.DataFrame(
        {
            "product_name": ["ZILRETTA", "KENALOG", "DEPO-MEDROL", "ASPIRIN"],
            "group": ["zilretta", "triamcinolone_ir", "other_injectable_corticosteroid", "other"],
        }
    )


def test_monthly_group_visits_sums_each_group_and_flags_a_missing_product():
    monthly = cs.monthly_group_visits(_tiny_engine(), _tiny_groups())
    assert monthly.loc[202201].to_dict() == {
        "zilretta": 10.0,
        "triamcinolone_ir": 60.0,
        "other_injectable_corticosteroid": 30.0,
        "other": 100.0,
    }
    assert monthly.loc[202202, "other"] == 0.0
    with pytest.raises(ValueError, match="missing from the group file"):
        cs.monthly_group_visits(_tiny_engine(), _tiny_groups().iloc[:3])


def test_period_table_shares_match_a_hand_computation():
    monthly = cs.monthly_group_visits(_tiny_engine(), _tiny_groups())
    year = pd.Series(monthly.index // 100, index=monthly.index)
    table = cs.period_table(monthly, year)
    row = table.loc[2022]
    steroids = 10 + 60 + 30 + 20 + 80  # Zilretta, triamcinolone and other steroids, two months
    assert row["injectable_steroid_visits"] == steroids
    assert row["zilretta_share_of_injectable_steroids_pct"] == pytest.approx(100 * 30 / steroids)
    assert row["zilretta_share_against_triamcinolone_ir_pct"] == pytest.approx(
        100 * 30 / (30 + 140)
    )
    assert row["months"] == 2


def test_who_gained_compares_the_peak_year_with_the_latest_twelve_months():
    months = [*range(202201, 202213), *range(202408, 202413), *range(202501, 202508)]
    monthly = pd.DataFrame(
        {
            "zilretta": [10.0] * 12 + [5.0] * 12,
            "triamcinolone_ir": [60.0] * 12 + [60.0] * 12,
            "other_injectable_corticosteroid": [30.0] * 12 + [35.0] * 12,
            "other": [1.0] * 24,
        },
        index=months,
    )
    out = cs.who_gained(monthly)
    assert out["groups"]["zilretta"]["share_peak_year_pct"] == pytest.approx(10.0)
    assert out["groups"]["zilretta"]["share_latest_12_pct"] == pytest.approx(5.0)
    assert out["groups"]["zilretta"]["visits_change_pct"] == pytest.approx(-50.0)
    assert out["groups"]["triamcinolone_ir"]["share_latest_12_pct"] == pytest.approx(60.0)
    assert out["category_visits_peak_year"] == 12 * 100
    assert out["category_visits_change_pct"] == pytest.approx(0.0)
    assert sum(g["share_latest_12_pct"] for g in out["groups"].values()) == pytest.approx(100.0)


def test_competitor_tables_are_json_safe_and_carry_the_caveat():
    import json

    out = cs.competitor_tables(_tiny_engine(), _tiny_groups().assign())
    assert {"by_year", "by_quarter", "who_gained", "caveat"} <= set(out)
    assert "DL-62" in out["caveat"]
    json.dumps(out, default=float)
