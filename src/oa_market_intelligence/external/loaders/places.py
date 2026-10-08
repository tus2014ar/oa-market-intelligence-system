"""CDC PLACES county data, 2025 release: arthritis prevalence (DL-59, step 4c).

ARTHRITIS only, age-adjusted prevalence only (comparable across counties with different age
mixes, approved at the 4c plan), the latest data year available for each location, and counties
only (the national summary row is dropped).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.common import (
    finish_partition,
    now,
    to_int,
    to_number,
    verified_entry,
)
from oa_market_intelligence.external.profile import iter_csv

FOLDER = "CDC PLACES"
SOURCE = "places"
MEASURE = "ARTHRITIS"
VALUE_TYPE = "Age-adjusted prevalence"


def clean_places(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame[(frame["MeasureId"] == MEASURE) & (frame["Data_Value_Type"] == VALUE_TYPE)]
    # the file also carries one national summary row (state "US", no county name): not a county
    frame = frame[frame["StateAbbr"].str.strip().ne("US")]
    frame = frame.sort_values("Year", ascending=False).drop_duplicates("LocationID")
    out = pd.DataFrame(
        {
            "data_year": to_int(frame["Year"], name="Year"),
            "location_id": frame["LocationID"],
            "measure_id": frame["MeasureId"],
            "value_type": frame["Data_Value_Type"],
            "state_code": frame["StateAbbr"],
            "county_name": frame["LocationName"],
            "prevalence_pct": to_number(frame["Data_Value"], name="Data_Value"),
            "ci_low_pct": to_number(frame["Low_Confidence_Limit"], name="Low_Confidence_Limit"),
            "ci_high_pct": to_number(frame["High_Confidence_Limit"], name="High_Confidence_Limit"),
            "total_population": to_int(frame["TotalPopulation"], name="TotalPopulation"),
            "footnote": frame["Data_Value_Footnote"].replace("", None),
        }
    )
    return out.sort_values("location_id").reset_index(drop=True)


def load_places(engine: Engine, raw_root: Path) -> int:
    path = next((Path(raw_root) / FOLDER).glob("*.csv"))
    started = now()
    verified_entry(raw_root, path)
    chunks = list(iter_csv(path))
    read = sum(len(c) for c in chunks)
    frame = clean_places(pd.concat(chunks, ignore_index=True))
    return finish_partition(
        engine,
        raw_root,
        source=SOURCE,
        year=None,
        path=path,
        table="fact_ext_arthritis_prevalence",
        frame=frame,
        rows_read=read,
        where={},
        started_at=started,
    )
