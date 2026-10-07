"""Tests for the Task A dataset and label audit (src/.../features/segment_task.py).

Task A predicts, for a specialty x age band x gender segment, whether Zilretta's share next
month will be High or Low relative to the market-wide share. Two guarantees matter most:
the features of a row for month t use only data from month t-1 or earlier (checked
generically by rewriting every later month), and the label is only defined when the data can
tell the segment apart from the market.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.segment_task import (
    OUTCOME_COLUMNS,
    TASK_A_FEATURES,
    audit_labels,
    build_task_a_rows,
    interval_label,
    wilson_interval,
)


def _seg(rows):
    """rows: (month_id, specialty, age_band, gender, zilretta, category)."""
    frame = pd.DataFrame(
        rows,
        columns=["month_id", "specialty_name", "age_band", "gender",
                 "branded_injectable_visits", "total_category_visits"],
    )
    frame["segment_visit_share"] = (
        frame["branded_injectable_visits"] / frame["total_category_visits"]
    )
    return frame


def _hand_built():
    """Segment A (S1) rises 2,4,6,8 of 100; segment B (S2) is flat at 10 of 100. The market
    share is therefore 6%, 7%, 8%, 9% in months 1 to 4."""
    rows = []
    for month, z in zip((201901, 201902, 201903, 201904), (2, 4, 6, 8)):
        rows.append((month, "S1", "65 TO 74", "FEMALE", z, 100))
        rows.append((month, "S2", "65 TO 74", "FEMALE", 10, 100))
    return _seg(rows)


def _row(frame, month, specialty):
    hit = frame[(frame["month_id"] == month) & (frame["specialty_name"] == specialty)]
    assert len(hit) == 1
    return hit.iloc[0]


def test_wilson_interval_matches_a_hand_computed_case():
    low, high = wilson_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-3)
    assert high == pytest.approx(0.5962, abs=1e-3)
    assert wilson_interval(0, 100)[0] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    "z, n, market, expected",
    [
        (50, 100, 0.06, "High"),  # interval 0.40 to 0.60 is entirely above 6%
        (0, 400, 0.06, "Low"),  # interval 0 to 0.0095 is entirely below 6%
        (8, 100, 0.09, "Undetermined"),  # interval 0.041 to 0.150 contains 9%
    ],
)
def test_the_label_needs_the_whole_interval_on_one_side_of_the_market(z, n, market, expected):
    assert interval_label(z, n, market) == expected


def test_features_for_a_row_are_computed_from_last_month_and_earlier():
    rows = build_task_a_rows(_hand_built())
    a = _row(rows, 201904, "S1")
    assert a["seg_share_lag1"] == pytest.approx(0.06)
    assert a["seg_share_roll3"] == pytest.approx((0.02 + 0.04 + 0.06) / 3)
    assert a["market_share_lag1"] == pytest.approx(0.08)
    assert a["market_change_lag1"] == pytest.approx(0.08 - 0.07)
    assert a["seg_margin_lag1"] == pytest.approx(0.06 - 0.08)
    assert a["seg_high_lag1"] == 0  # 6% is not above the 8% market
    assert a["seg_log_visits_lag1"] == pytest.approx(np.log1p(100))
    assert a["seg_prior_share"] == pytest.approx(12 / 300)
    assert a["specialty_prior_share"] == pytest.approx(12 / 300)
    b = _row(rows, 201904, "S2")
    assert b["seg_high_lag1"] == 1  # 10% is above the 8% market


def test_the_outcome_columns_hold_month_t_and_are_not_features():
    rows = build_task_a_rows(_hand_built())
    a = _row(rows, 201904, "S1")
    assert a["y_share"] == pytest.approx(0.08)
    assert a["y_market"] == pytest.approx(0.09)
    assert a["y_visits"] == 100
    assert a["y_label"] == "Undetermined"
    assert a["y_raw_above"] == 0
    assert set(OUTCOME_COLUMNS).isdisjoint(TASK_A_FEATURES)
    assert all(column.startswith("y_") for column in OUTCOME_COLUMNS)


def test_the_first_two_months_have_no_rows_because_the_market_change_needs_two_lags():
    rows = build_task_a_rows(_hand_built())
    assert sorted(rows["month_id"].unique()) == [201903, 201904]


def test_a_segment_needs_enough_visits_last_month_to_get_a_prediction():
    frame = _hand_built()
    small = (frame["month_id"] == 201903) & (frame["specialty_name"] == "S1")
    frame.loc[small, "total_category_visits"] = 10  # below the 20-visit history requirement
    rows = build_task_a_rows(frame)
    assert not ((rows["month_id"] == 201904) & (rows["specialty_name"] == "S1")).any()
    assert ((rows["month_id"] == 201904) & (rows["specialty_name"] == "S2")).any()


def test_a_segment_absent_last_month_gets_no_prediction():
    frame = _hand_built()
    gone = (frame["month_id"] == 201903) & (frame["specialty_name"] == "S1")
    rows = build_task_a_rows(frame[~gone])
    assert not ((rows["month_id"] == 201904) & (rows["specialty_name"] == "S1")).any()


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


def _rewrite_from(frame, month, seed=99):
    """Replace the counts of every row from `month` onward, keeping which rows exist."""
    rng = np.random.default_rng(seed)
    out = frame.copy()
    later = out["month_id"] >= month
    t = rng.integers(60, 400, later.sum())
    out.loc[later, "total_category_visits"] = t
    out.loc[later, "branded_injectable_visits"] = rng.binomial(t, 0.4)
    out["segment_visit_share"] = out["branded_injectable_visits"] / out["total_category_visits"]
    return out


def _features_unchanged(build, frame, cut):
    """True if every feature of every row for months up to `cut` is identical after the data
    from `cut` onward is rewritten."""
    before = build(frame)
    after = build(_rewrite_from(frame, cut))
    keys = ["month_id", "specialty_name", "age_band", "gender"]
    before = before[before["month_id"] <= cut].sort_values(keys).reset_index(drop=True)
    after = after[after["month_id"] <= cut].sort_values(keys).reset_index(drop=True)
    shared = before.merge(after, on=keys, suffixes=("_a", "_b"))
    columns = [c for c in TASK_A_FEATURES if c not in ("specialty_grouped",)]
    for column in columns:
        left, right = shared[f"{column}_a"].to_numpy(float), shared[f"{column}_b"].to_numpy(float)
        if not np.allclose(left, right, equal_nan=True):
            return False
    return len(shared) > 0


@pytest.mark.parametrize("cut", [201912, 202006, 202101])
def test_features_at_month_t_ignore_everything_from_t_onward(cut):
    frame = _synthetic()
    assert _features_unchanged(build_task_a_rows, frame, cut)


def test_the_leakage_check_fails_when_a_leak_is_injected():
    """A feature built from month t's own share must be caught by the same check."""

    def leaky(frame):
        rows = build_task_a_rows(frame)
        out = rows.copy()
        out["seg_share_lag1"] = out["y_share"]  # the answer, smuggled in as a feature
        return out

    assert not _features_unchanged(leaky, _synthetic(), 202006)


def test_the_feature_columns_are_exactly_the_declared_ones():
    rows = build_task_a_rows(_synthetic())
    assert set(TASK_A_FEATURES) <= set(rows.columns)
    assert "log_total_visits" not in rows.columns  # the same-month volume column is gone


def test_a_two_label_scheme_keeps_segments_by_last_months_volume_and_labels_by_the_raw_sign():
    rows = build_task_a_rows(_synthetic(), scheme="two_label", min_visits_two_label=100)
    assert set(rows["y_label"]) <= {"High", "Low"}
    assert (np.expm1(rows["seg_log_visits_lag1"]).round() >= 100).all()  # last month's volume
    assert (rows["y_visits"] > 0).all()
    assert (rows["y_label"].eq("High") == rows["y_raw_above"].eq(1)).all()


def test_the_audit_counts_classes_and_applies_the_two_thresholds():
    labels = ["High"] * 40 + ["Low"] * 40 + ["Undetermined"] * 20
    rows = pd.DataFrame({"y_label": labels, "seg_high_lag1": [1] * 100, "y_raw_above": [1] * 100})
    ok = audit_labels(rows, min_scored=50, min_class_share=0.15)
    assert (ok["n_high"], ok["n_low"], ok["n_undetermined"], ok["n_scored"]) == (40, 40, 20, 80)
    assert ok["passes"] is True
    too_few = audit_labels(rows, min_scored=100, min_class_share=0.15)
    assert too_few["passes"] is False and "scored" in too_few["reason"]
    lopsided = pd.DataFrame(
        {"y_label": ["High"] * 90 + ["Low"] * 10, "seg_high_lag1": [1] * 100,
         "y_raw_above": [1] * 100}
    )
    result = audit_labels(lopsided, min_scored=50, min_class_share=0.15)
    assert result["passes"] is False and "class" in result["reason"]


def test_the_audit_reports_how_well_persistence_does_on_the_scored_rows():
    rows = pd.DataFrame(
        {
            "y_label": ["High", "High", "Low", "Low", "Undetermined"],
            "seg_high_lag1": [1, 0, 0, 1, 1],  # right, wrong, right, wrong, (not scored)
            "y_raw_above": [1, 1, 0, 0, 1],
        }
    )
    result = audit_labels(rows, min_scored=1, min_class_share=0.0)
    # recall of High = 1/2, recall of Low = 1/2
    assert result["persistence_balanced_accuracy"] == pytest.approx(0.5)
