"""Medicare Part D by Geography and Drug, 2019 to 2024 (DL-59, step 4b).

Keeps only the fixed NSAID and oral-steroid generic names. Blank cells are suppressed values and
load as missing, never as zero; the suppression flags are kept as written.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.codes import classify_partd_drug
from oa_market_intelligence.external.common import (
    finish_partition,
    now,
    to_int,
    to_number,
    verified_entry,
)
from oa_market_intelligence.external.loaders.partb_geo import geo_code
from oa_market_intelligence.external.profile import iter_csv

FOLDER = "Medicare Part D Prescribers - by Geography and Drug"
SOURCE = "partd_geo"


def clean_partd_geo(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    family = frame["Gnrc_Name"].map(classify_partd_drug)
    frame, family = frame[family.notna()], family[family.notna()]
    out = pd.DataFrame(
        {
            "year": year,
            "geo_level": frame["Prscrbr_Geo_Lvl"],
            "geo_code": geo_code(frame["Prscrbr_Geo_Lvl"], frame["Prscrbr_Geo_Cd"]),
            "brand_name": frame["Brnd_Name"],
            "generic_name": frame["Gnrc_Name"],
            "drug_family": family,
            "n_prescribers": to_int(frame["Tot_Prscrbrs"], name="Tot_Prscrbrs"),
            "claims": to_number(frame["Tot_Clms"], name="Tot_Clms"),
            "fills_30day": to_number(frame["Tot_30day_Fills"], name="Tot_30day_Fills"),
            "total_drug_cost": to_number(frame["Tot_Drug_Cst"], name="Tot_Drug_Cst"),
            "benes": to_int(frame["Tot_Benes"], name="Tot_Benes"),
            "ge65_claims": to_number(frame["GE65_Tot_Clms"], name="GE65_Tot_Clms"),
            "ge65_fills_30day": to_number(
                frame["GE65_Tot_30day_Fills"], name="GE65_Tot_30day_Fills"
            ),
            "ge65_drug_cost": to_number(frame["GE65_Tot_Drug_Cst"], name="GE65_Tot_Drug_Cst"),
            "ge65_benes": to_int(frame["GE65_Tot_Benes"], name="GE65_Tot_Benes"),
            "ge65_suppression_flag": frame["GE65_Sprsn_Flag"].replace("", None),
            "ge65_bene_suppression_flag": frame["GE65_Bene_Sprsn_Flag"].replace("", None),
        }
    )
    return out.reset_index(drop=True)


def files(raw_root: Path) -> list[tuple[int, Path]]:
    found = []
    for path in sorted((Path(raw_root) / FOLDER).rglob("*_Geo*.csv")):
        match = re.search(r"_DY(\d{2})_Geo", path.name)
        if match:
            found.append((2000 + int(match.group(1)), path))
    return found


def load_partd_geo(engine: Engine, raw_root: Path) -> dict[int, int]:
    loaded = {}
    for year, path in files(raw_root):
        started = now()
        verified_entry(raw_root, path)
        read, parts = 0, []
        for chunk in iter_csv(path):
            read += len(chunk)
            parts.append(clean_partd_geo(chunk, year))
        frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        loaded[year] = finish_partition(
            engine,
            raw_root,
            source=SOURCE,
            year=year,
            path=path,
            table="fact_ext_partd_geo",
            frame=frame,
            rows_read=read,
            where={"year": year},
            started_at=started,
        )
    return loaded
