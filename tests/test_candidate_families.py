"""Tests for the candidate feature families (FA1 to FA6, FB1) of the feature plan.

Hand-computed answers on tiny tables, the generic leakage test (rewrite everything from month t
onward, the features for month t must not move) for every IQVIA-derived family, and the as-of
semantics of the outside families. No family is run against a model here.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.candidate_families import (
    EVENT_CAP,
    FAMILY_COLUMNS,
    FB1_COLUMNS,
    KEYS,
    MONTHS_SINCE_CAP,
    market_outside_features,
    seasonal_gap,
    task_a_family_columns,
    task_b_family_columns,
)
from oa_market_intelligence.features.segment_task import build_task_a_rows

SIGNAL_COLUMNS = [
    "signal_id",
    "source_id",
    "specialty_group",
    "grain",
    "period_start_month",
    "period_end_month",
    "value",
    "available_from_month",
]


def _seg(rows):
    """rows: (month_id, specialty, age_band, gender, zilretta, category)."""
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


def _row(frame, month, specialty, age="65 TO 74", gender="FEMALE"):
    hit = frame[
        (frame["month_id"] == month)
        & (frame["specialty_name"] == specialty)
        & (frame["age_band"] == age)
        & (frame["gender"] == gender)
    ]
    assert len(hit) == 1
    return hit.iloc[0]


def _logit(p):
    return np.log(p / (1 - p))


def _signals(rows):
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


# ---------- FA1 shrinkage ----------


def test_fa1_shrinks_the_segments_history_toward_its_specialtys_by_k():
    rows = [
        (201901, "S1", "65 TO 74", "FEMALE", 2, 100),
        (201901, "S1", "40 TO 59", "FEMALE", 10, 100),
        (201902, "S1", "65 TO 74", "FEMALE", 4, 100),
        (201902, "S1", "40 TO 59", "FEMALE", 10, 100),
    ]
    out = task_a_family_columns(_seg(rows), families=("FA1",))
    row = _row(out, 201902, "S1")
    # segment history 2 of 100; the specialty's history to January is 12 of 200 = 0.06
    assert row["fa1_logit_shrunk_k20"] == pytest.approx(_logit((2 + 20 * 0.06) / (100 + 20)))
    assert row["fa1_weight_k20"] == pytest.approx(100 / 120)
    assert row["fa1_weight_k5"] == pytest.approx(100 / 105)
    assert row["fa1_weight_k80"] == pytest.approx(100 / 180)


def test_fa1_with_no_own_history_is_the_specialty_prior_and_a_new_specialty_uses_the_market():
    rows = [
        (201901, "S1", "65 TO 74", "FEMALE", 6, 100),  # month 1
        (201902, "S1", "65 TO 74", "FEMALE", 6, 100),
        (201902, "S2", "65 TO 74", "FEMALE", 1, 100),  # S2 appears for the first time
    ]
    out = task_a_family_columns(_seg(rows), families=("FA1",))
    new_specialty = _row(out, 201902, "S2")
    assert new_specialty["fa1_weight_k20"] == 0.0  # no history of its own
    assert new_specialty["fa1_logit_shrunk_k20"] == pytest.approx(_logit(0.06))  # the market prior


def test_fa1_agrees_with_the_cumulative_counts_of_the_existing_task_a_rows():
    frame = _synthetic()
    rows = build_task_a_rows(frame)
    out = task_a_family_columns(frame, families=("FA1",))
    merged = rows.merge(out, on=KEYS)
    expected = (merged["seg_prior_z"] + 20 * merged["spec_prior_z"] / merged["spec_prior_t"]) / (
        merged["seg_prior_t"] + 20
    )
    assert np.allclose(merged["fa1_logit_shrunk_k20"], _logit(np.clip(expected, 1e-3, 1 - 1e-3)))


# ---------- FA2 activity history ----------


def test_fa2_counts_months_active_months_since_a_zilretta_visit_and_cumulative_visits():
    rows = [
        (m, "S1", "65 TO 74", "FEMALE", z, 100) for m, z in zip(range(201901, 201905), (0, 3, 0, 5))
    ]
    out = task_a_family_columns(_seg(rows), families=("FA2",))
    first, last = _row(out, 201901, "S1"), _row(out, 201904, "S1")
    assert first["fa2_months_active_prior"] == 0
    assert first["fa2_months_since_branded"] == MONTHS_SINCE_CAP  # never used before
    assert first["fa2_log_cum_branded"] == 0.0
    assert last["fa2_months_active_prior"] == 3
    assert last["fa2_months_since_branded"] == 2  # last used in February, now April
    assert last["fa2_log_cum_branded"] == pytest.approx(np.log1p(3))  # the April 5 not counted


def test_fa2_months_since_is_capped():
    rows = [(201901, "S1", "65 TO 74", "FEMALE", 5, 100)]
    rows += [(m, "S1", "65 TO 74", "FEMALE", 0, 100) for m in range(201902, 201913)]
    rows += [(m, "S1", "65 TO 74", "FEMALE", 0, 100) for m in range(202001, 202015 - 2)]
    out = task_a_family_columns(_seg(rows), families=("FA2",))
    assert out["fa2_months_since_branded"].max() == MONTHS_SINCE_CAP


# ---------- FA3 specialty momentum ----------


def test_fa3_is_the_specialtys_last_share_and_its_change_over_three_months():
    rows = [
        (m, "S1", "65 TO 74", "FEMALE", z, 100)
        for m, z in zip(range(201901, 201906), (2, 4, 6, 8, 10))
    ]
    out = task_a_family_columns(_seg(rows), families=("FA3",))
    last = _row(out, 201905, "S1")
    assert last["fa3_logit_spec_share_lag1"] == pytest.approx(_logit(0.08))
    assert last["fa3_spec_change_3m"] == pytest.approx(0.08 - 0.02)
    assert last["fa3_missing"] == 0.0
    early = _row(out, 201903, "S1")  # no month four back
    assert early["fa3_missing"] == 1.0 and early["fa3_spec_change_3m"] == 0.0


# ---------- FA4 and FB1 seasonal gap ----------


def test_the_seasonal_gap_averages_earlier_years_only():
    frame = pd.DataFrame(
        {
            "year": [2019, 2019, 2020, 2020, 2021, 2021],
            "month": [1, 2, 1, 2, 1, 2],
            "z": [2, 4, 5, 7, 1, 1],
            "t": [100] * 6,
        }
    )
    gap = seasonal_gap(frame)
    assert gap.iloc[:2].isna().all()  # the first year has no earlier year
    # 2019: shares .02 .04, year .03, gaps -.01 +.01; the 2020 gaps are the same, so 2021 sees both
    assert gap.iloc[2] == pytest.approx(-0.01) and gap.iloc[3] == pytest.approx(0.01)
    assert gap.iloc[4] == pytest.approx(-0.01) and gap.iloc[5] == pytest.approx(0.01)


def test_fa4_is_zero_filled_and_flagged_in_the_first_year():
    frame = _synthetic()
    out = task_a_family_columns(frame, families=("FA4",))
    first_year = out[out["month_id"] < 202001]
    assert (first_year["fa4_missing"] == 1.0).all() and (
        first_year["fa4_seasonal_gap"] == 0.0
    ).all()
    assert (out[out["month_id"] >= 202101]["fa4_missing"] == 0.0).all()
    assert out["fa4_seasonal_gap"].notna().all()


def test_fb1_gives_the_national_seasonal_gap_and_a_missing_flag():
    months = [201901, 201902, 202001, 202002, 202101]
    monthly = pd.DataFrame(
        {
            "month_id": months,
            "branded_injectable_visits": [2, 4, 5, 7, 1],
            "generic_corticosteroid_visits": [98, 96, 95, 93, 99],
            "nsaid_otc_visits": [0, 0, 0, 0, 0],
        }
    )
    out = task_b_family_columns(monthly)
    assert list(out.columns) == ["month_id", *FB1_COLUMNS]
    assert out.loc[0, "fb1_missing"] == 1.0 and out.loc[2, "fb1_missing"] == 0.0
    assert out.loc[2, "fb1_seasonal_gap"] == pytest.approx(-0.01)  # Jan 2020 vs Jan 2019


# ---------- FA5 market outside features (as-of semantics) ----------


def _market_signals():
    return _signals(
        [
            ("asp.price_ratio", "asp_price", "", "quarter", 202001, 202003, 100.0, 202001),
            ("asp.price_ratio", "asp_price", "", "quarter", 202004, 202006, 104.0, 202004),
            ("asp.price_ratio", "asp_price", "", "quarter", 202101, 202103, 110.0, 202101),
            ("asp.price_ratio", "asp_price", "", "quarter", 202104, 202106, 130.0, 202104),
            ("company.net_sales_usd", "sec_filings", "", "quarter", 202001, 202003, 20.0, 202005),
            ("company.net_sales_usd", "sec_filings", "", "quarter", 202101, 202103, 25.0, 202105),
            ("event.count_in_month", "events", "", "event", 202007, 202007, 1.0, 202007),
        ]
    )


def test_fa5_uses_what_was_known_as_of_the_previous_month_and_nothing_later():
    out = market_outside_features(_market_signals(), [202104, 202106]).set_index("month_id")
    april = out.loc[
        202104
    ]  # as of March 2021: the Q2 2021 price (known from April) is not yet known
    assert april["fa5_price_ratio"] == 110.0
    assert april["fa5_price_ratio_chg_4q"] == pytest.approx(10.0)  # against March 2020
    assert april["fa5_price_chg_missing"] == 0.0
    assert april["fa5_sales_missing"] == 1.0  # Q1 2021 sales are filed in May
    assert april["fa5_months_since_event"] == 8  # the July 2020 event, as of March 2021
    june = out.loc[202106]  # as of May 2021
    assert june["fa5_price_ratio"] == 130.0
    assert june["fa5_sales_yoy"] == pytest.approx(25 / 20 - 1)
    assert june["fa5_sales_missing"] == 0.0


def test_fa5_without_history_is_zero_filled_and_flagged_and_never_means_the_event_cap():
    out = market_outside_features(_market_signals(), [202001]).iloc[0]  # as of December 2019
    assert out["fa5_price_ratio"] == 0.0 and out["fa5_price_chg_missing"] == 1.0
    assert out["fa5_sales_missing"] == 1.0
    assert out["fa5_months_since_event"] == EVENT_CAP


def test_fa5_ignores_a_value_that_becomes_known_only_later():
    signals = _market_signals()
    late = signals.copy()
    is_price = late["signal_id"] == "asp.price_ratio"
    late.loc[is_price, "available_from_month"] += 300  # three years later
    now = market_outside_features(signals, [202104]).iloc[0]
    delayed = market_outside_features(late, [202104]).iloc[0]
    assert now["fa5_price_ratio"] == 110.0 and delayed["fa5_price_ratio"] == 0.0


# ---------- FA6 Medicare adoption ----------


def test_fa6_is_the_specialty_groups_latest_known_rate_and_missing_otherwise():
    signals = _signals(
        [
            (
                "medicare.adoption_rate",
                "partb_provider",
                "S1",
                "year",
                202001,
                202012,
                0.04,
                202212,
            ),
            (
                "medicare.adoption_rate",
                "partb_provider",
                "S1",
                "year",
                202101,
                202112,
                0.05,
                202312,
            ),
        ]
    )
    rows = [
        (202301, "S1", "65 TO 74", "FEMALE", 1, 100),
        (202401, "S1", "65 TO 74", "FEMALE", 1, 100),
        (202301, "OUTSIDE", "65 TO 74", "FEMALE", 1, 100),
        (202101, "S1", "65 TO 74", "FEMALE", 1, 100),
    ]
    out = task_a_family_columns(_seg(rows), signals, families=("FA6",))
    assert _row(out, 202301, "S1")["fa6_medicare_adoption"] == 0.04  # 2020 known from Dec 2022
    assert _row(out, 202401, "S1")["fa6_medicare_adoption"] == 0.05  # 2021 known from Dec 2023
    assert _row(out, 202301, "OUTSIDE")["fa6_medicare_missing"] == 1.0
    assert _row(out, 202101, "S1")["fa6_medicare_missing"] == 1.0  # nothing known yet


# ---------- the shape of the output ----------


def _synthetic(months=30, seed=0):
    rng = np.random.default_rng(seed)
    month_ids = [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(months)]
    rows = []
    for month in month_ids:
        for specialty in ("S1", "S2", "S3"):
            for age in ("40 TO 59", "65 TO 74"):
                for gender in ("FEMALE", "MALE"):
                    t = int(rng.integers(60, 400))
                    rows.append((month, specialty, age, gender, int(rng.binomial(t, 0.03)), t))
    return _seg(rows)


def test_every_family_has_its_declared_columns_and_no_nan_after_the_first_month():
    signals = _market_signals()
    out = task_a_family_columns(_synthetic(), signals)
    declared = [c for cols in FAMILY_COLUMNS.values() for c in cols]
    assert set(declared) <= set(out.columns)
    after_first = out[out["month_id"] > out["month_id"].min()]
    assert after_first[declared].notna().all().all()
    assert len(out) == len(_synthetic())  # one row per segment-month


def test_families_can_be_asked_for_one_at_a_time():
    out = task_a_family_columns(_synthetic(), families=("FA3",))
    assert set(out.columns) == set(KEYS) | set(FAMILY_COLUMNS["FA3"])


# ---------- the generic leakage test ----------


def _rewrite_from(frame, month, seed=99):
    """Replace the counts of every row from `month` onward, keeping which rows exist."""
    rng = np.random.default_rng(seed)
    out = frame.copy()
    later = out["month_id"] >= month
    t = rng.integers(60, 400, later.sum())
    out.loc[later, "total_category_visits"] = t
    out.loc[later, "branded_injectable_visits"] = rng.binomial(t, 0.4)
    return out


def _unchanged(build, frame, cut, columns):
    before = build(frame)
    after = build(_rewrite_from(frame, cut))
    before = before[before["month_id"] <= cut].sort_values(KEYS).reset_index(drop=True)
    after = after[after["month_id"] <= cut].sort_values(KEYS).reset_index(drop=True)
    shared = before.merge(after, on=KEYS, suffixes=("_a", "_b"))
    for column in columns:
        if not np.allclose(
            shared[f"{column}_a"].to_numpy(float),
            shared[f"{column}_b"].to_numpy(float),
            equal_nan=True,
        ):
            return False
    return len(shared) > 0


@pytest.mark.parametrize("family", ["FA1", "FA2", "FA3", "FA4"])
@pytest.mark.parametrize("cut", [201912, 202006, 202101])
def test_a_months_features_ignore_everything_from_that_month_onward(family, cut):
    columns = FAMILY_COLUMNS[family]
    build = lambda f: task_a_family_columns(f, families=(family,))  # noqa: E731
    assert _unchanged(build, _synthetic(), cut, columns)


def test_the_leakage_check_catches_a_family_built_from_the_months_own_counts():
    def leaky(frame):
        out = task_a_family_columns(frame, families=("FA2",))
        keyed = frame.assign(
            share=frame["branded_injectable_visits"] / frame["total_category_visits"]
        )
        merged = out.merge(keyed[[*KEYS, "share"]], on=KEYS)
        merged["fa2_log_cum_branded"] = merged["share"]  # the month's own answer as a feature
        return merged

    assert not _unchanged(leaky, _synthetic(), 202006, ["fa2_log_cum_branded"])


def test_fb1_ignores_everything_from_the_month_onward():
    months = [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(40)]
    rng = np.random.default_rng(3)
    monthly = pd.DataFrame(
        {
            "month_id": months,
            "branded_injectable_visits": rng.integers(900, 3000, 40),
            "generic_corticosteroid_visits": rng.integers(60000, 90000, 40),
            "nsaid_otc_visits": rng.integers(4000, 7000, 40),
        }
    )
    cut = months[25]
    changed = monthly.copy()
    later = changed["month_id"] >= cut
    changed.loc[later, "branded_injectable_visits"] = 5
    a = task_b_family_columns(monthly).set_index("month_id").loc[:cut]
    b = task_b_family_columns(changed).set_index("month_id").loc[:cut]
    assert np.allclose(a.to_numpy(), b.to_numpy())


# ---------- the model hooks: a family's columns reach the models, defaults are untouched ----------


def _rows_with_planted_column():
    rows = build_task_a_rows(_synthetic(months=36), scheme="two_label", min_visits_two_label=1)
    # a column that carries the month's own answer: only there to prove the hook wires it in
    rows["planted"] = rows["y_share"].to_numpy(float)
    return rows


def test_design_frame_adds_extra_columns_and_nothing_by_default():
    from oa_market_intelligence.modeling.segment_models import design_frame

    rows = _rows_with_planted_column()
    plain = design_frame(rows)
    extra = design_frame(rows, ("planted",))
    assert "planted" not in plain.columns and "planted" in extra.columns
    assert list(extra.drop(columns="planted").columns) == list(plain.columns)


@pytest.mark.parametrize("model", ["logistic", "gbm", "rf"])
def test_every_model_uses_the_extra_columns_and_ignores_them_when_not_asked(model):
    from oa_market_intelligence.modeling.segment_eval import log_loss_per_visit, successes
    from oa_market_intelligence.modeling.segment_models import MODEL_CONFIGS, fit_predict_for

    rows = _rows_with_planted_column()
    train = rows[rows["month_id"] < 202101]
    test = rows[rows["month_id"] >= 202101]
    config = MODEL_CONFIGS[model][0]
    plain = fit_predict_for(model, config)(train, test)
    with_column = fit_predict_for(model, config, extra_columns=("planted",))(train, test)
    again = fit_predict_for(model, config, extra_columns=())(train, test)
    assert np.array_equal(plain, again)  # an empty family changes nothing
    loss = lambda p: log_loss_per_visit(successes(test), test["y_visits"], p)  # noqa: E731
    assert loss(with_column) < loss(plain)  # the planted answer is picked up


def test_a_missing_extra_column_is_an_error_not_a_silent_skip():
    from oa_market_intelligence.modeling.segment_models import MODEL_CONFIGS, fit_predict_for

    rows = _rows_with_planted_column()
    train, test = rows[rows["month_id"] < 202101], rows[rows["month_id"] >= 202101]
    with pytest.raises(KeyError):
        fit_predict_for("logistic", MODEL_CONFIGS["logistic"][0], extra_columns=("nope",))(
            train, test
        )
