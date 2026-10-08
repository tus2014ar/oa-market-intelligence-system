"""Medicare Geographic Variation, 2014 to 2024 (DL-59, step 4b).

National and state rows only. `*` means suppressed and loads as missing; the placeholder state rows
("Territory", "ZZ") have no geography code and are dropped. County rows are not loaded.
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

FOLDER = "Medicare Geographic Variation"
SOURCE = "geovar"


def clean_geovar(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame[frame["BENE_GEO_LVL"].isin(["National", "State"])]
    placeholder = frame["BENE_GEO_LVL"].eq("State") & frame["BENE_GEO_CD"].str.strip().eq("")
    frame = frame[~placeholder]
    code = frame["BENE_GEO_CD"].str.strip().where(frame["BENE_GEO_LVL"].ne("National"), "US")
    out = pd.DataFrame(
        {
            "year": to_int(frame["YEAR"], name="YEAR"),
            "geo_level": frame["BENE_GEO_LVL"],
            "geo_code": code,
            "age_level": frame["BENE_AGE_LVL"],
            "geo_desc": frame["BENE_GEO_DESC"],
            "benes_total": to_int(frame["BENES_TOTAL_CNT"], name="BENES_TOTAL_CNT"),
            "benes_ffs_ab": to_int(frame["BENES_WTH_PTAPTB_CNT"], name="BENES_WTH_PTAPTB_CNT"),
            "benes_original_medicare": to_int(frame["BENES_OM_CNT"], name="BENES_OM_CNT"),
            "benes_ma": to_int(frame["BENES_MA_CNT"], name="BENES_MA_CNT"),
            "ma_participation_rate": to_number(frame["MA_PRTCPTN_RATE"], name="MA_PRTCPTN_RATE"),
            "avg_age": to_number(frame["BENE_AVG_AGE"], name="BENE_AVG_AGE"),
        }
    )
    return out.reset_index(drop=True)


def load_geovar(engine: Engine, raw_root: Path) -> int:
    path = next((Path(raw_root) / FOLDER).glob("*.csv"))
    started = now()
    verified_entry(raw_root, path)
    chunks = list(iter_csv(path))
    read = sum(len(c) for c in chunks)
    frame = pd.concat([clean_geovar(c) for c in chunks], ignore_index=True)
    # one file holds every year, so the whole table is replaced
    return finish_partition(
        engine,
        raw_root,
        source=SOURCE,
        year=None,
        path=path,
        table="fact_ext_geo_variation",
        frame=frame,
        rows_read=read,
        where={},
        started_at=started,
    )
