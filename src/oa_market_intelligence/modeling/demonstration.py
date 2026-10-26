"""The batch demonstration (docs/modelling_plan.md, section 7): descriptive, not a test.

Data are stopped at a cut-off month and the serving model of each task predicts the six following
months as one batch, to be shown beside what happened. For the month k steps after the cut-off the
model *type* is the serving model of the nearest evaluated horizon (k = 1 and 2 use h = 1, 3 and 4
use h = 3, 5 and 6 use h = 6) and is fitted directly for horizon k, using only rows whose outcome
month is at or before the cut-off.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oa_market_intelligence.modeling.horizons import (
    assert_pairing,
    month_id_from_index,
    month_index,
    pair_rows,
)
from oa_market_intelligence.modeling.segment_baselines import BASELINES
from oa_market_intelligence.modeling.segment_models import TunedPredictor
from oa_market_intelligence.modeling.t1_horizon import check_series, make_forecasters

CUTOFFS = (202212, 202312)
STEPS = range(1, 7)


def evaluated_horizon(step: int, horizons: tuple[int, ...] = (1, 3, 6)) -> int:
    """The evaluated horizon whose serving model type is used for a step (ties go to the
    shorter horizon)."""
    return min(horizons, key=lambda h: (abs(h - step), h))


def national_batch(
    series: pd.Series, cutoff: int, serving_by_horizon: dict[int, str]
) -> pd.DataFrame:
    """The next six months of the national share from data through `cutoff`: the point forecast,
    the 90% range and the actual value (percentage points)."""
    check_series(series)
    history = series[series.index <= cutoff]
    if history.index[-1] != cutoff:
        raise ValueError(f"the series has no value for the cut-off {cutoff}")
    origin = month_index(cutoff)
    forecasters = make_forecasters()
    rows = []
    for step in STEPS:
        target = int(month_id_from_index(origin + step))
        name = serving_by_horizon[evaluated_horizon(step)]
        out = forecasters[name](history, target, step)
        rows.append(
            {"cutoff": cutoff, "step": step, "month_id": target, "model": name,
             "actual": float(series.get(target, np.nan)), "point": out["point"],
             "lo90": out["lo90"], "hi90": out["hi90"]}
        )
    return pd.DataFrame(rows)


def segment_batch(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    cutoff: int,
    serving_by_horizon: dict[int, str],
    *,
    extra_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    """The next six months of the segment shares from data through `cutoff`, summarised by
    specialty: visit-weighted predicted and actual share. The model sees only rows whose outcome
    month is at or before the cut-off and the features of the cut-off origin."""
    origin = int(month_index(cutoff))
    frames = []
    for step in STEPS:
        name = serving_by_horizon[evaluated_horizon(step)]
        paired = pair_rows(rows, outcomes, step)
        assert_pairing(paired, step)
        outcome = month_index(paired["month_id"].to_numpy())
        train = paired[outcome <= origin]
        test = paired[paired["origin_id"].to_numpy() == cutoff]
        if train.empty or test.empty:
            continue
        visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
        if name in BASELINES:
            p = BASELINES[name](train, visible)
        else:
            p = TunedPredictor(name, extra_columns=extra_columns)(train, visible)
        frame = pd.DataFrame(
            {
                "specialty_name": test["specialty_name"].to_numpy(),
                "visits": test["y_visits"].to_numpy(float),
                "predicted_visits": test["y_visits"].to_numpy(float) * np.asarray(p, float),
                "actual_visits": np.rint(
                    test["y_share"].to_numpy(float) * test["y_visits"].to_numpy(float)
                ),
            }
        )
        grouped = frame.groupby("specialty_name").sum().reset_index()
        grouped["predicted_share"] = grouped["predicted_visits"] / grouped["visits"]
        grouped["actual_share"] = grouped["actual_visits"] / grouped["visits"]
        grouped.insert(0, "month_id", int(month_id_from_index(origin + step)))
        grouped.insert(0, "step", step)
        grouped.insert(0, "cutoff", cutoff)
        grouped["model"] = name
        frames.append(grouped)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
