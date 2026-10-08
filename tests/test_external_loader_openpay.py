"""Tests for the 4d Open Payments loader (DL-59).

Tiny files shaped like the real general-payments files exercise every rule from profiling:
product names in any of five slots, spelling variants, dates inside the program year, flagged
zero-dollar and zero-count records, recipient types, and distinct-recipient counting.
"""

import json
import zipfile

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.codes import normalise_openpay_product
from oa_market_intelligence.external.common import replace_partition
from oa_market_intelligence.external.loaders.openpay import (
    OP_USECOLS,
    aggregate_openpay_chunk,
    load_openpay,
)
from oa_market_intelligence.external.profile import sha256_of
from oa_market_intelligence.external.schema import create_external_schema


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    create_external_schema(engine)
    return engine


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


# ---------- product names ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Zilretta", "Zilretta"),
        ("ZILRETTA", "Zilretta"),
        ("Durolane", "Durolane"),
        ("DUROLANE", "Durolane"),
        ("GELSYN 3", "Gelsyn-3"),
        ("Gelsyn-3", "Gelsyn-3"),
        ("GenVisc 850", "GenVisc 850"),
        ("GENVISC 850 SODIUM HYALURONATE", "GenVisc 850"),
        ("Gel-One Cross-linked Hyaluronate", "Gel-One"),
        ("SYNVISC-ONE", "Synvisc"),
        ("SYNVISC", "Synvisc"),
        ("Supartz Fx Sodium  Hyaluronate", "Supartz FX"),
        ("TriVisc sodium hyaluronate", "Trivisc"),
        ("VISCO-3 sodium hyaluronate", "Visco-3"),
        ("Hyalgan Injection", "Hyalgan"),
        ("MONOVISC", "Monovisc"),
        ("ORTHOVISC", "Orthovisc"),
        ("EUFLEXXA", "Euflexxa"),
        ("HYMOVIS", "Hymovis"),
        ("TRILURON", "Triluron"),
        ("BIOLOGICS CONSUMABLES HYALURONIC ACID OTHER", None),
        ("Triamcinolone-Moxifloxacin PF", None),
        ("METHYLPREDNISOLONE SODIUM SUCCINATE", None),
        ("Kenalog-40", None),
        ("", None),
    ],
)
def test_product_spellings_normalise_to_one_name_and_false_matches_are_excluded(raw, expected):
    assert normalise_openpay_product(raw) == expected


# ---------- one chunk ----------

COLUMNS = OP_USECOLS


def _row(**kw):
    base = {c: "" for c in COLUMNS}
    base.update(
        Covered_Recipient_Type="Covered Recipient Physician",
        Covered_Recipient_NPI="111",
        Date_of_Payment="03/15/2021",
        Total_Amount_of_Payment_USDollars="25.50",
        Number_of_Payments_Included_in_Total_Amount="1",
        Nature_of_Payment_or_Transfer_of_Value="Food and Beverage",
        Program_Year="2021",
        Record_ID="r1",
    )
    base.update(kw)
    return base


def _frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS, dtype=str)


def test_a_zilretta_record_in_any_product_slot_is_counted_by_month_and_recipient_type():
    rows = [
        _row(Record_ID="a", Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_3="Zilretta"),
        _row(
            Record_ID="b",
            Covered_Recipient_NPI="222",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="ZILRETTA",
            Total_Amount_of_Payment_USDollars="10",
        ),
        _row(Record_ID="c", Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Tylenol"),
    ]
    month, nature, stats = aggregate_openpay_chunk(_frame(rows), 2021)
    assert len(month) == 1
    row = month.iloc[0]
    assert (row["month_id"], row["product"], row["recipient_type"]) == (
        202103,
        "Zilretta",
        "physician",
    )
    assert row["n_records"] == 2 and row["total_amount_usd"] == pytest.approx(35.5)
    assert row["n_payments_counted"] == 2
    assert stats["rows_read"] == 3 and stats["matched_records"] == 2


def test_distinct_recipients_are_counted_once_per_month_and_product():
    rows = [
        _row(
            Record_ID="a",
            Covered_Recipient_NPI="111",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(
            Record_ID="b",
            Covered_Recipient_NPI="111",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(
            Record_ID="c",
            Covered_Recipient_NPI="222",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(
            Record_ID="d",
            Covered_Recipient_NPI="111",
            Date_of_Payment="04/01/2021",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
    ]
    month, _, _ = aggregate_openpay_chunk(_frame(rows), 2021)
    by_month = month.set_index("month_id")
    assert by_month.loc[202103, "n_records"] == 3 and by_month.loc[202103, "recipients"] == {
        "111",
        "222",
    }
    assert by_month.loc[202104, "recipients"] == {"111"}


def test_a_record_naming_two_products_counts_once_for_each_but_not_twice_for_one():
    rows = [
        _row(
            Record_ID="a",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_2="DUROLANE",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_3="Durolane",
        ),
    ]
    month, _, _ = aggregate_openpay_chunk(_frame(rows), 2021)
    assert sorted(zip(month["product"], month["n_records"], strict=True)) == [
        ("Durolane", 1),
        ("Zilretta", 1),
    ]


def test_payments_dated_outside_their_program_year_or_unreadable_are_dropped_and_tallied():
    rows = [
        _row(
            Record_ID="a",
            Date_of_Payment="11/30/0002",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(
            Record_ID="b",
            Date_of_Payment="01/05/2020",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(
            Record_ID="c",
            Date_of_Payment="not a date",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(Record_ID="d", Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta"),
    ]
    month, _, stats = aggregate_openpay_chunk(_frame(rows), 2021)
    assert month["n_records"].sum() == 1
    assert stats["matched_records"] == 4 and stats["dropped_date"] == 3


def test_zero_dollar_and_zero_count_records_are_kept_but_flagged():
    rows = [
        _row(
            Record_ID="a",
            Total_Amount_of_Payment_USDollars="0",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(
            Record_ID="b",
            Number_of_Payments_Included_in_Total_Amount="0",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(Record_ID="c", Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta"),
    ]
    month, _, _ = aggregate_openpay_chunk(_frame(rows), 2021)
    row = month.iloc[0]
    assert row["n_records"] == 3 and row["n_flagged_records"] == 2


def test_recipient_types_are_separated_and_teaching_hospitals_use_their_own_id():
    rows = [
        _row(
            Record_ID="a",
            Covered_Recipient_Type="Covered Recipient Non-Physician Practitioner",
            Covered_Recipient_NPI="333",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
        _row(
            Record_ID="b",
            Covered_Recipient_Type="Covered Recipient Teaching Hospital",
            Covered_Recipient_NPI="",
            Teaching_Hospital_ID="H9",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
    ]
    month, _, _ = aggregate_openpay_chunk(_frame(rows), 2021)
    by_type = month.set_index("recipient_type")
    assert set(by_type.index) == {"non_physician_practitioner", "teaching_hospital"}
    assert by_type.loc["teaching_hospital", "recipients"] == {"H9"}
    assert by_type.loc["non_physician_practitioner", "recipients"] == {"333"}


def test_an_unknown_recipient_type_is_an_error_not_a_guess():
    rows = [
        _row(
            Covered_Recipient_Type="Somebody Else",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        )
    ]
    with pytest.raises(ValueError, match="recipient type"):
        aggregate_openpay_chunk(_frame(rows), 2021)


def test_the_nature_table_splits_by_kind_of_payment():
    rows = [
        _row(Record_ID="a", Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta"),
        _row(
            Record_ID="b",
            Nature_of_Payment_or_Transfer_of_Value="Consulting Fee",
            Total_Amount_of_Payment_USDollars="1000",
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Zilretta",
        ),
    ]
    _, nature, _ = aggregate_openpay_chunk(_frame(rows), 2021)
    got = dict(zip(nature["nature_of_payment"], nature["total_amount_usd"], strict=True))
    assert got == {"Food and Beverage": pytest.approx(25.5), "Consulting Fee": pytest.approx(1000)}


def test_hyaluronic_names_that_match_no_approved_product_are_tallied_not_lost():
    rows = [
        _row(
            Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1=(
                "BIOLOGICS CONSUMABLES HYALURONIC ACID OTHER"
            )
        )
    ]
    month, _, stats = aggregate_openpay_chunk(_frame(rows), 2021)
    assert month.empty and stats["unmapped_names"] == {
        "BIOLOGICS CONSUMABLES HYALURONIC ACID OTHER": 1
    }


# ---------- whole files ----------


def _register(raw, path):
    entry = {
        "dataset": "Open Payments",
        "file": str(path.relative_to(raw)).replace("/", "\\"),
        "bytes": path.stat().st_size,
        "sha256": sha256_of(path),
        "status": "downloaded",
    }
    with open(raw / "_download_manifest.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _csv_text(rows):
    return _frame(rows).to_csv(index=False)


Z = "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1"


def _tree(tmp_path):
    folder = tmp_path / "Open Payments" / "2021"
    folder.mkdir(parents=True)
    zpath = folder / "PGYR2021_P06302026_06032026.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(
            "OP_DTL_GNRL_PGYR2021_P06302026_06032026.csv",
            _csv_text(
                [
                    _row(Record_ID="a", **{Z: "Zilretta"}),
                    _row(Record_ID="b", Date_of_Payment="11/30/0002", **{Z: "Zilretta"}),
                    _row(Record_ID="c", **{Z: "Tylenol"}),
                ]
            ),
        )
        z.writestr("OP_DTL_RSRCH_PGYR2021_P06302026_06032026.csv", "x\n1\n")
    _register(tmp_path, zpath)
    csv25 = tmp_path / "OP_DTL_GNRL_PGYR2025_P06302026_06032026.csv"
    csv25.write_text(
        _csv_text(
            [
                _row(
                    Record_ID="d",
                    Date_of_Payment="02/02/2025",
                    Program_Year="2025",
                    **{Z: "Orthovisc"},
                )
            ]
        ),
        encoding="utf-8",
    )
    _register(tmp_path, csv25)
    return tmp_path


def test_files_load_by_year_idempotently_and_the_run_log_tells_where_every_record_went(
    tmp_path, engine
):
    raw = _tree(tmp_path)
    load_openpay(engine, raw, workers=1)
    load_openpay(engine, raw, workers=1)
    assert (
        _count(engine, "fact_ext_openpay_month") == 2
        and _count(engine, "fact_ext_openpay_nature") == 2
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT month_id, product, n_records, n_distinct_recipients "
                "FROM fact_ext_openpay_month ORDER BY month_id"
            )
        ).all()
        note = json.loads(
            conn.execute(
                text(
                    "SELECT note FROM external_load_runs "
                    "WHERE source = 'openpay' AND data_year = 2021 ORDER BY run_id DESC"
                )
            ).scalar()
        )
    assert [tuple(r) for r in rows] == [(202103, "Zilretta", 1, 1), (202502, "Orthovisc", 1, 1)]
    assert note["rows_read"] == 3 and note["matched_records"] == 2 and note["dropped_date"] == 1


def test_reloading_one_year_leaves_the_other_years_alone(tmp_path, engine):
    raw = _tree(tmp_path)
    load_openpay(engine, raw, workers=1)
    replace_partition(
        engine, "fact_ext_openpay_month", pd.DataFrame(), {"month_id": (202101, 202112)}
    )
    with engine.connect() as conn:
        left = conn.execute(text("SELECT month_id FROM fact_ext_openpay_month")).scalars().all()
    assert left == [202502]
