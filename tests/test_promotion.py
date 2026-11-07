"""Tests for the promotion gate (src/.../promotion.py, DL-126).

Small made-up worlds where every number is known, so each tolerance is tested on both sides of its
edge: a challenger that is the same models passes, one that moves a little passes, one that moves
too much, breaks the output or changes behaviour without a change in the data is refused, and a
written reason is the only thing that lets a refused refit through (and it is recorded).
"""

import copy
import hashlib
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from oa_market_intelligence import promotion as pr
from oa_market_intelligence.promotion import PromotionHeld, Side
from test_publish import MONTHS_2, _db, _log, _publish

EXPECTED = {"segment:1", "segment:3", "segment:6"}
SEGMENTS = [(f"SPEC{i}", "65 TO 74", g) for i in range(6) for g in ("FEMALE", "MALE")]  # 12


def canonical(parameters: dict) -> str:
    return json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)


def make_side(origin=202507, version="28b9a7bd3234", visits=200, coef_shift=0.0) -> Side:
    rows = []
    for h in (1, 3, 6):
        for i, (spec, age, gender) in enumerate(SEGMENTS):
            rows.append(
                {
                    "level": "segment", "horizon": h, "specialty_name": spec, "age_band": age,
                    "gender": gender, "point_share": 0.02 + 0.001 * i, "origin_visits": visits,
                    "alert_flag": float(i % 2) if h == 1 else np.nan,
                    "alert_score": 0.3 + 0.02 * i if h == 1 else np.nan,
                    "run_month_id": origin, "data_version": version,
                }
            )  # fmt: skip
    arts = []
    for model_id, task, h, model in [
        ("segment:1", "segment", 1, "logistic"),
        ("segment:3", "segment", 3, "logistic"),
        ("segment:6", "segment", 6, "last_month"),
        ("alert:1", "alert", 1, "logistic"),
    ]:
        text = (
            "{}"
            if model != "logistic"
            else canonical({"coef": [0.5 + coef_shift, -0.25, 1.0], "intercept": 0.1 + coef_shift})
        )
        arts.append(
            {
                "model_id": model_id, "task": task, "horizon": h, "model": model,
                "model_version": f"v-{model_id}", "run_month_id": origin, "parameters": text,
                "artifact_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )  # fmt: skip
    return Side(pd.DataFrame(rows), pd.DataFrame(arts), origin, version)


def moved(side: Side, horizon: int, pp: float, indexes=None, origin=None) -> Side:
    """A copy whose forecasts at a horizon have moved by `pp` percentage points."""
    out = copy.deepcopy(side)
    mask = out.pred["horizon"] == horizon
    if indexes is not None:
        keep = {SEGMENTS[i][0] + SEGMENTS[i][2] for i in indexes}
        mask &= (out.pred["specialty_name"] + out.pred["gender"]).isin(keep)
    out.pred.loc[mask, "point_share"] += pp / 100
    if origin:
        out.origin = origin
        out.pred["run_month_id"] = origin
    return out


def judge(champion, challenger, reason=None):
    return pr.evaluate(champion, challenger, expected_ids=EXPECTED, override_reason=reason)


def failed_names(decision):
    return {f"{c.check}@{c.horizon}" if c.horizon else c.check for c in decision.failed}


# ------------------------------------------------------------------ sanity


def test_a_well_formed_refit_passes_every_sanity_check():
    decision = judge(None, make_side())
    assert decision.status == "no_champion" and not decision.failed
    assert {c.scope for c in decision.checks} == {"sanity"} and len(decision.checks) == 6


@pytest.mark.parametrize(
    ("column", "bad_value", "check"),
    [
        ("point_share", np.nan, "forecasts_finite_and_between_0_and_1"),
        ("point_share", np.inf, "forecasts_finite_and_between_0_and_1"),
        ("point_share", 1.2, "forecasts_finite_and_between_0_and_1"),
        ("point_share", -0.01, "forecasts_finite_and_between_0_and_1"),
        ("alert_score", 1.5, "alert_scores_between_0_and_1"),
    ],
)
def test_broken_forecasts_or_alert_scores_are_refused(column, bad_value, check):
    challenger = make_side()
    challenger.pred.loc[2, column] = bad_value  # one cell of the horizon-1 rows
    decision = judge(None, challenger)
    assert decision.status == "hold" and check in failed_names(decision)


def test_duplicate_segment_keys_are_refused():
    challenger = make_side()
    challenger.pred = pd.concat([challenger.pred, challenger.pred.iloc[[0]]], ignore_index=True)
    assert "no_duplicate_segment_keys" in failed_names(judge(None, challenger))


def test_a_missing_model_of_the_adoption_map_is_refused_and_named():
    challenger = make_side()
    challenger.artifacts = challenger.artifacts[challenger.artifacts["model_id"] != "segment:3"]
    decision = judge(None, challenger)
    missing = next(c for c in decision.failed if c.check == "models_of_the_adoption_map_present")
    assert decision.status == "hold" and "segment:3" in missing.detail


def test_saved_numbers_that_do_not_match_their_checksum_or_are_not_finite_are_refused():
    tampered = make_side()
    tampered.artifacts.loc[0, "parameters"] = tampered.artifacts.loc[0, "parameters"].replace(
        "0.5", "0.6"
    )
    assert "saved_numbers_match_their_checksum" in failed_names(judge(None, tampered))
    infinite = make_side()
    text = '{"coef":[Infinity,1.0],"intercept":0.0}'  # not valid JSON for the API, and not finite
    infinite.artifacts.loc[0, "parameters"] = text
    infinite.artifacts.loc[0, "artifact_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    assert "coefficients_finite" in failed_names(judge(None, infinite))


# ------------------------------------------------------------------ reproducibility: same month


def test_the_same_models_refit_on_the_same_data_are_promoted():
    decision = judge(make_side(), make_side())
    assert decision.status == "promote" and not decision.failed
    assert {c.scope for c in decision.checks} == {"sanity", "reproducibility"}


def test_round_off_noise_far_below_the_limit_is_accepted_and_a_visible_change_is_not():
    champion = make_side()
    noise = moved(champion, 1, 3e-11)  # what two real builds on different machines differed by
    assert judge(champion, noise).status == "promote"
    under = moved(champion, 1, 5e-7)  # half the limit
    assert judge(champion, under).status == "promote"
    over = judge(champion, moved(champion, 1, 1e-5))
    assert over.status == "hold" and failed_names(over) == {"forecasts_identical"}


def test_a_change_in_the_rule_horizon_is_also_a_reproducibility_failure():
    champion = make_side()
    assert "forecasts_identical" in failed_names(judge(champion, moved(champion, 6, 0.01)))


def test_coefficients_must_match_to_a_billionth():
    champion = make_side()
    assert judge(champion, make_side(coef_shift=1e-12)).status == "promote"
    refused = judge(champion, make_side(coef_shift=1e-8))
    assert refused.status == "hold" and failed_names(refused) == {"coefficients_identical"}


def test_an_alert_flag_that_flips_a_changed_fingerprint_or_a_changed_segment_set_are_refused():
    champion = make_side()
    flipped = copy.deepcopy(champion)
    flipped.pred.loc[(flipped.pred["horizon"] == 1) & (flipped.pred.index == 0), "alert_flag"] = 1.0
    assert "alert_flags_identical" in failed_names(judge(champion, flipped))
    fingerprint = copy.deepcopy(champion)
    fingerprint.artifacts.loc[0, "model_version"] = "other"
    assert "model_fingerprints_identical" in failed_names(judge(champion, fingerprint))
    fewer = copy.deepcopy(champion)
    fewer.pred = fewer.pred.iloc[1:]
    assert "same_segments_and_horizons" in failed_names(judge(champion, fewer))


# ------------------------------------------------------------------ stability: a later month


def later(side, **kwargs):
    return moved(side, kwargs.pop("horizon", 1), kwargs.pop("pp", 0.0), origin=202508, **kwargs)


def test_a_later_refit_that_moves_a_little_is_promoted():
    champion = make_side()
    challenger = later(later(champion, horizon=1, pp=0.3), horizon=3, pp=0.2)
    decision = judge(champion, challenger)
    assert decision.status == "promote" and {c.scope for c in decision.checks} == {
        "sanity",
        "stability",
    }


def test_the_weighted_mean_change_is_limited_to_six_tenths_of_a_point():
    champion = make_side()
    assert judge(champion, later(champion, pp=0.55)).status == "promote"
    refused = judge(champion, later(champion, pp=0.65))
    assert refused.status == "hold" and "weighted_mean_change_pp@1" in failed_names(refused)
    one = next(c for c in refused.failed if c.check == "weighted_mean_change_pp")
    assert one.value == pytest.approx(0.65) and one.limit == 0.6


def test_no_big_segment_may_move_more_than_five_points_but_a_small_one_may():
    champion = make_side()
    assert judge(champion, later(champion, pp=4.9, indexes=[0])).status == "promote"
    refused = judge(champion, later(champion, pp=5.1, indexes=[0]))
    assert failed_names(refused) == {"largest_big_segment_change_pp@1"}  # the mean stayed small
    small = make_side(visits=50)  # every segment under 100 visits: the big-segment rule is silent
    assert judge(small, later(small, pp=5.1, indexes=[0])).status == "promote"


def test_at_least_nine_in_ten_segments_must_be_kept():
    champion = make_side()
    challenger = later(champion)
    drop = challenger.pred[
        (challenger.pred["horizon"] == 1) & (challenger.pred["specialty_name"] == "SPEC0")
    ].index
    challenger.pred = challenger.pred.drop(drop[:1])  # 11 of 12 kept: 91.7%
    assert judge(champion, challenger).status == "promote"
    challenger.pred = challenger.pred.drop(
        challenger.pred[
            (challenger.pred["horizon"] == 1) & (challenger.pred["specialty_name"] == "SPEC1")
        ].index[:1]
    )  # 10 of 12 kept: 83.3%
    refused = judge(champion, challenger)
    assert refused.status == "hold" and "segments_kept@1" in failed_names(refused)


def test_the_rule_horizon_is_exempt_from_the_stability_limits_because_it_follows_the_data():
    champion = make_side()
    assert judge(champion, later(champion, horizon=6, pp=10.0)).status == "promote"


def test_a_challenger_that_starts_from_an_earlier_month_is_refused():
    champion = make_side(origin=202508)
    challenger = make_side(origin=202507)
    refused = judge(champion, challenger)
    assert refused.status == "hold" and "origin_moves_forward" in failed_names(refused)


# ------------------------------------------------------------------ the decision and its record


def test_a_written_reason_is_the_only_way_through_and_it_is_recorded():
    champion = make_side()
    bad = moved(champion, 1, 1e-3)
    assert judge(champion, bad).status == "hold"
    for blank in (None, "", "   "):
        assert judge(champion, bad, blank).status == "hold"
    overridden = judge(champion, bad, "  re-fit after the library upgrade, forecasts reviewed  ")
    assert overridden.status == "override"
    assert overridden.override_reason == "re-fit after the library upgrade, forecasts reviewed"
    assert overridden.failed  # the failure is still on the record


def test_with_no_champion_a_broken_refit_is_still_refused_as_there_is_nothing_to_fall_back_to():
    broken = make_side()
    broken.pred.loc[0, "point_share"] = np.nan
    assert judge(None, broken).status == "hold"


def test_the_log_frame_has_one_row_per_check_stable_numbers_and_no_timestamps():
    champion = make_side()
    decision = judge(champion, moved(champion, 1, 0.123456789e-3))
    frame = pr.decision_frame(decision)
    assert list(frame.columns) == pr.LOG_COLUMNS and len(frame) == len(decision.checks)
    assert set(frame["passed"]) <= {0, 1} and set(frame["decision"]) == {"hold"}
    shown = frame[frame["check"] == "forecasts_identical"].iloc[0]
    assert shown["value"] == pytest.approx(0.123457e-3, rel=1e-5) and shown["limit"] == 1e-6
    assert shown["champion_origin"] == 202507 and shown["challenger_data_version"] == "28b9a7bd3234"
    again = pr.decision_frame(judge(champion, moved(champion, 1, 0.123456789e-3)))
    pd.testing.assert_frame_equal(frame, again)  # the same inputs give the same table
    assert not [c for c in frame.columns if "time" in c or "date" in c]


def test_the_summary_names_each_failed_check_with_its_horizon():
    champion = make_side()
    text = pr.summary(judge(champion, later(champion, pp=0.9)))
    assert text["decision"] == "hold" and "stability:weighted_mean_change_pp@h1" in text["failed"]
    assert text["champion_origin"] == 202507 and text["challenger_origin"] == 202508


# ------------------------------------------------------------------ the stage, on real files


def write_world(engine, side: Side):
    side.pred.to_sql("pred_batch", engine, index=False, if_exists="replace")
    side.artifacts.to_sql("model_artifacts", engine, index=False, if_exists="replace")


def file_with(path, side):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    write_world(engine, side)
    engine.dispose()
    return path


@pytest.fixture(autouse=True)
def map_with_three_segment_models(monkeypatch):
    monkeypatch.setattr(pr, "expected_model_ids", lambda: set(EXPECTED))


def test_read_side_returns_the_forecasts_the_models_and_the_month_or_nothing(tmp_path):
    side = pr.read_side(file_with(tmp_path / "w.db", make_side()))
    assert side.origin == 202507 and side.data_version == "28b9a7bd3234"
    assert len(side.pred) == 36 and set(side.artifacts["model_id"]) >= EXPECTED
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert pr.read_side(empty) is None  # an older warehouse with no models


def test_the_stage_writes_the_log_to_the_staged_database_and_is_repeatable(tmp_path):
    published = file_with(tmp_path / "published.db", make_side())
    staged = tmp_path / "staged.db"
    engine = create_engine(f"sqlite:///{staged.as_posix()}")
    write_world(engine, make_side())
    first = pr.run_promotion(engine, published)
    one = pd.read_sql("SELECT * FROM promotion_log", engine)
    second = pr.run_promotion(engine, published)  # replaced, not appended
    two = pd.read_sql("SELECT * FROM promotion_log", engine)
    engine.dispose()
    assert first == second and first["decision"] == "promote" and first["failed"] == []
    pd.testing.assert_frame_equal(one, two)


def test_the_stage_with_no_published_database_or_no_predictions_does_not_block(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'staged.db').as_posix()}")
    write_world(engine, make_side())
    assert pr.run_promotion(engine, None)["decision"] == "no_champion"
    assert pr.run_promotion(engine, tmp_path / "missing.db")["decision"] == "no_champion"
    blank = create_engine(f"sqlite:///{(tmp_path / 'blank.db').as_posix()}")
    with blank.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (a INT)")
    assert pr.run_promotion(blank, None)["decision"] == "skipped"
    engine.dispose()
    blank.dispose()


def test_a_refused_refit_raises_with_every_failed_check_its_number_and_the_way_through(tmp_path):
    published = file_with(tmp_path / "published.db", make_side())
    engine = create_engine(f"sqlite:///{(tmp_path / 'staged.db').as_posix()}")
    write_world(engine, moved(make_side(), 1, 1e-3))
    with pytest.raises(PromotionHeld) as caught:
        pr.run_promotion(engine, published)
    message = str(caught.value)
    assert (
        "reproducibility:forecasts_identical" in message and "against a limit of 1e-06" in message
    )
    assert "keep serving" in message and "--accept-challenger" in message
    engine.dispose()


# ------------------------------------------------------------------ inside the whole publish run


def scoring_with(side):
    def fn(engine):
        write_world(engine, side)
        return {"pred_batch": len(side.pred)}

    return fn


def publish_with(tmp_path, side, **extra):
    return _publish(tmp_path, MONTHS_2, scoring_fn=scoring_with(side), **extra)


def stage(record, name):
    return next(s for s in record["stages"] if s["stage"] == name)


def test_the_first_run_has_no_champion_and_is_published_with_its_decision_in_the_record(tmp_path):
    record = publish_with(tmp_path, make_side())
    assert record["status"] == "ok" and record["promotion"]["decision"] == "no_champion"
    names = [s["stage"] for s in record["stages"]]
    assert names.index("scoring") < names.index("promotion") < names.index("swap")
    published = sqlite3.connect(_db(tmp_path))
    assert published.execute("SELECT COUNT(*) FROM promotion_log").fetchone()[0] == 6


def test_the_same_models_refit_again_are_promoted_and_swapped_in(tmp_path):
    publish_with(tmp_path, make_side())
    record = publish_with(tmp_path, make_side())
    assert record["status"] == "ok" and record["promotion"]["decision"] == "promote"
    log = pd.read_sql("SELECT * FROM promotion_log", sqlite3.connect(_db(tmp_path)))
    assert set(log["decision"]) == {"promote"} and set(log["scope"]) == {
        "sanity",
        "reproducibility",
    }


def test_a_refused_refit_stops_before_the_swap_and_leaves_the_champion_untouched(tmp_path):
    publish_with(tmp_path, make_side())
    before = _db(tmp_path).read_bytes()
    record = publish_with(tmp_path, moved(make_side(), 1, 1e-3))
    assert record["status"] == "failed" and "were not promoted" in record["error"]
    assert [s["stage"] for s in record["stages"] if s["status"] == "failed"] == ["promotion"]
    assert "swap" not in [s["stage"] for s in record["stages"]]
    assert _db(tmp_path).read_bytes() == before  # the champion is still what is published
    assert not list((tmp_path / "published").glob(".staging-*"))
    assert _log(tmp_path)[-1]["error"] == record["error"]  # and the reason is in the run log


def test_a_written_reason_lets_the_same_refit_through_and_is_in_the_run_record_and_the_table(
    tmp_path,
):
    publish_with(tmp_path, make_side())
    record = publish_with(
        tmp_path, moved(make_side(), 1, 1e-3), accept_challenger="reviewed: library upgrade"
    )
    assert record["status"] == "ok" and record["promotion"]["decision"] == "override"
    assert record["promotion"]["override_reason"] == "reviewed: library upgrade"
    assert "reproducibility:forecasts_identical" in record["promotion"]["failed"]
    log = pd.read_sql("SELECT * FROM promotion_log", sqlite3.connect(_db(tmp_path)))
    assert set(log["decision"]) == {"override"}
    assert set(log["override_reason"].dropna()) == {"reviewed: library upgrade"}


def test_a_later_month_is_judged_on_stability_and_a_big_jump_is_held(tmp_path):
    base = make_side()
    publish_with(tmp_path, base)
    small = publish_with(tmp_path, later(base, pp=0.2))
    assert small["status"] == "ok" and small["promotion"]["decision"] == "promote"
    jump = publish_with(tmp_path, later(later(base, pp=0.2), pp=1.0))  # origin 202508 again
    assert jump["status"] == "failed"  # the same month, not a later one, and it moved


def test_without_a_scoring_stage_there_is_nothing_to_judge_and_the_stage_is_skipped(tmp_path):
    record = _publish(tmp_path, MONTHS_2)
    assert stage(record, "promotion")["status"] == "skipped" and record["promotion"] == {}


def test_the_promotion_stage_can_be_turned_off_and_a_crash_in_it_stops_the_run(tmp_path):
    record = publish_with(tmp_path, make_side(), promotion_fn=None)
    assert stage(record, "promotion")["status"] == "skipped"

    def boom(*_args):
        raise RuntimeError("gate broke")

    publish_with(tmp_path / "second", make_side())
    crashed = publish_with(tmp_path / "second", make_side(), promotion_fn=boom)
    assert crashed["status"] == "failed" and "gate broke" in crashed["error"]
