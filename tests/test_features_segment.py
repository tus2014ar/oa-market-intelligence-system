"""Tests for the segment-level features (src/.../features/segment.py).

`specialty_prior_share` is a target encoding (a specialty's own historical Zilretta
share), so its leakage guarantee is the important one: month t's value may use only
months before t, and `test_prior_share_ignores_the_current_and_later_months` enforces
that by rewriting every later month and requiring month t's encoding not to move.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.segment import (
    AGE_BANDS_ORDERED,
    SEGMENT_FEATURES,
    compute_segment_features,
    group_rare_specialties,
)


def _seg(rows):
    frame = pd.DataFrame(
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
    frame["segment_visit_share"] = (
        frame["branded_injectable_visits"] / frame["total_category_visits"]
    )
    return frame


def test_prior_share_is_the_cumulative_share_over_earlier_months_only():
    seg = _seg(
        [
            (201908, "ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE", 1, 20),
            (201908, "ORTHOPEDIC SURGERY", "75 TO 84", "FEMALE", 1, 20),
            (201909, "ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE", 6, 50),
            (201910, "ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE", 4, 60),
        ]
    )
    result = compute_segment_features(seg)

    assert 201908 not in result["month_id"].tolist()  # no earlier month, so no encoding
    by_month = result.set_index("month_id")["specialty_prior_share"]
    assert by_month[201909] == pytest.approx((1 + 1) / (20 + 20))
    assert by_month[201910] == pytest.approx((1 + 1 + 6) / (20 + 20 + 50))


def _multi_specialty_segments():
    rng = np.random.RandomState(3)
    rows = []
    for month_id in range(201908, 201914):
        for specialty in ("A", "B", "C"):
            for age in ("40 TO 59", "65 TO 74"):
                total = int(rng.randint(30, 200))
                rows.append((month_id, specialty, age, "FEMALE", int(rng.randint(0, total)), total))
    return _seg(rows)


@pytest.mark.parametrize("t", [201910, 201912, 201913])
def test_prior_share_ignores_the_current_and_later_months(t):
    seg = _multi_specialty_segments()
    base = compute_segment_features(seg, min_visits=1)

    altered = seg.copy()
    later = altered["month_id"] >= t
    altered.loc[later, "total_category_visits"] = (
        altered.loc[later, "total_category_visits"] * 5 + 7
    )
    altered.loc[later, "branded_injectable_visits"] = (
        altered.loc[later, "branded_injectable_visits"] * 3 + 1
    )
    altered["segment_visit_share"] = (
        altered["branded_injectable_visits"] / altered["total_category_visits"]
    )
    changed = compute_segment_features(altered, min_visits=1)

    keys = ["month_id", "specialty_name", "age_band"]
    left = base[base["month_id"] == t].set_index(keys)["specialty_prior_share"].sort_index()
    right = changed[changed["month_id"] == t].set_index(keys)["specialty_prior_share"].sort_index()
    assert len(left) > 0
    pd.testing.assert_series_equal(left, right)


def test_small_segments_are_dropped_and_volume_becomes_the_sample_weight():
    seg = _seg(
        [
            (201908, "A", "65 TO 74", "FEMALE", 1, 30),
            (201909, "A", "65 TO 74", "FEMALE", 2, 19),  # under the 20-visit floor
            (201909, "A", "75 TO 84", "FEMALE", 3, 25),
        ]
    )
    result = compute_segment_features(seg)
    assert result["total_category_visits"].tolist() == [25]
    assert result["sample_weight"].tolist() == [25]


def test_rare_specialties_are_grouped_but_the_real_other_specialty_is_kept():
    specialties = pd.Series(["A"] * 600 + ["OTHER"] * 300 + ["TINY"] * 3)
    grouped = group_rare_specialties(specialties, threshold=0.01)
    assert grouped.tolist().count("RARE (grouped)") == 3
    assert grouped.tolist().count("OTHER") == 300
    assert grouped.tolist().count("A") == 600


def test_age_gender_volume_and_calendar_encodings():
    seg = _seg(
        [
            (201908, "A", "65 TO 74", "FEMALE", 1, 30),
            (201912, "A", "UNSPECIFIED", "MALE", 2, 40),
            (201912, "A", "00 TO 02", "UNSPECIFIED", 3, 50),
        ]
    )
    result = compute_segment_features(seg).set_index("age_band")

    assert AGE_BANDS_ORDERED.index("65 TO 74") == 6
    assert pd.isna(result.loc["UNSPECIFIED", "age_ordinal"])
    assert result.loc["00 TO 02", "age_ordinal"] == 0
    assert result.loc["UNSPECIFIED", ["gender_FEMALE", "gender_MALE"]].tolist() == [0, 1]
    assert result.loc["00 TO 02", ["gender_FEMALE", "gender_MALE"]].tolist() == [0, 0]
    assert result.loc["00 TO 02", "log_total_visits"] == pytest.approx(np.log1p(50))
    assert result.loc["00 TO 02", "month_sin"] == pytest.approx(0.0, abs=1e-12)
    assert result.loc["00 TO 02", "month_cos"] == pytest.approx(1.0)


def test_output_has_the_declared_columns():
    result = compute_segment_features(_multi_specialty_segments(), min_visits=1)
    for column in [*SEGMENT_FEATURES, "segment_visit_share", "sample_weight", "month_id"]:
        assert column in result.columns
