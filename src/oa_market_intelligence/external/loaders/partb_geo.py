"""Medicare Part B by Geography and Service, 2019 to 2024 (DL-59, step 4b).

Keeps the approved billing codes only. National rows get the code `US`; the blank-code placeholder
state row gets `UNKNOWN`; state rows keep the CMS (FIPS) code, which joins `dim_state.state_fips`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.codes import ALL_CODES
from oa_market_intelligence.external.common import (
    finish_partition,
    now,
    to_int,
    to_number,
    verified_entry,
)
from oa_market_intelligence.external.profile import iter_csv

FOLDER = "Medicare Physician & Other Practitioners - by Geography and Service"
SOURCE = "partb_geo"


def geo_code(level: pd.Series, code: pd.Series) -> pd.Series:
    """National -> US; a state row with no code -> UNKNOWN; otherwise the CMS code as is."""
    code = code.astype(str).str.strip()
    return code.where(code.ne(""), "UNKNOWN").where(level.ne("National"), "US")


def clean_partb_geo(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    frame = frame[frame["HCPCS_Cd"].isin(ALL_CODES)]
    out = pd.DataFrame(
        {
            "year": year,
            "geo_level": frame["Rndrng_Prvdr_Geo_Lvl"],
            "geo_code": geo_code(frame["Rndrng_Prvdr_Geo_Lvl"], frame["Rndrng_Prvdr_Geo_Cd"]),
            "hcpcs_code": frame["HCPCS_Cd"],
            "setting": frame["Place_Of_Srvc"],
            "geo_desc": frame["Rndrng_Prvdr_Geo_Desc"],
            "drug_indicator": frame["HCPCS_Drug_Ind"],
            "n_providers": to_int(frame["Tot_Rndrng_Prvdrs"], name="Tot_Rndrng_Prvdrs"),
            "benes": to_int(frame["Tot_Benes"], name="Tot_Benes"),
            "services": to_number(frame["Tot_Srvcs"], name="Tot_Srvcs"),
            "bene_day_services": to_int(frame["Tot_Bene_Day_Srvcs"], name="Tot_Bene_Day_Srvcs"),
            "avg_submitted_charge": to_number(frame["Avg_Sbmtd_Chrg"], name="Avg_Sbmtd_Chrg"),
            "avg_allowed_amt": to_number(frame["Avg_Mdcr_Alowd_Amt"], name="Avg_Mdcr_Alowd_Amt"),
            "avg_payment_amt": to_number(frame["Avg_Mdcr_Pymt_Amt"], name="Avg_Mdcr_Pymt_Amt"),
            "avg_standardized_amt": to_number(
                frame["Avg_Mdcr_Stdzd_Amt"], name="Avg_Mdcr_Stdzd_Amt"
            ),
        }
    )
    return out.reset_index(drop=True)


def files(raw_root: Path) -> list[tuple[int, Path]]:
    found = []
    for path in sorted((Path(raw_root) / FOLDER).rglob("*_Geo.csv")):
        match = re.search(r"_D(\d{2})_Geo", path.name)
        if match:
            found.append((2000 + int(match.group(1)), path))
    return found


def load_partb_geo(engine: Engine, raw_root: Path) -> dict[int, int]:
    loaded = {}
    for year, path in files(raw_root):
        started = now()
        verified_entry(raw_root, path)
        read, parts = 0, []
        for chunk in iter_csv(path):
            read += len(chunk)
            parts.append(clean_partb_geo(chunk, year))
        frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        loaded[year] = finish_partition(
            engine,
            raw_root,
            source=SOURCE,
            year=year,
            path=path,
            table="fact_ext_partb_geo",
            frame=frame,
            rows_read=read,
            where={"year": year},
            started_at=started,
        )
    return loaded
