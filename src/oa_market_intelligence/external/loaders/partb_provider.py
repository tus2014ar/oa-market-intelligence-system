"""Medicare Part B by Provider and Service, 2019 to 2024 (DL-59, step 4c).

2019 to 2023 come from the filtered API pulls (already limited to the approved codes); 2024 is
filtered from the full 3 GB file in chunks. Provider names and street addresses are never read:
only the NPI, specialty, state, setting and volumes.
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

FOLDER = "Medicare Physician & Other Practitioners - by Provider and Service"
SOURCE = "partb_provider"
USECOLS = [
    "Rndrng_NPI",
    "Rndrng_Prvdr_Ent_Cd",
    "Rndrng_Prvdr_State_Abrvtn",
    "Rndrng_Prvdr_Type",
    "Rndrng_Prvdr_Mdcr_Prtcptg_Ind",
    "HCPCS_Cd",
    "Place_Of_Srvc",
    "Tot_Benes",
    "Tot_Srvcs",
    "Tot_Bene_Day_Srvcs",
    "Avg_Sbmtd_Chrg",
    "Avg_Mdcr_Alowd_Amt",
    "Avg_Mdcr_Pymt_Amt",
    "Avg_Mdcr_Stdzd_Amt",
]


def clean_partb_provider(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    frame = frame[frame["HCPCS_Cd"].isin(ALL_CODES)]
    out = pd.DataFrame(
        {
            "year": year,
            "npi": frame["Rndrng_NPI"],
            "hcpcs_code": frame["HCPCS_Cd"],
            "setting": frame["Place_Of_Srvc"],
            "specialty_cms": frame["Rndrng_Prvdr_Type"],
            "entity_type": frame["Rndrng_Prvdr_Ent_Cd"],
            "state_code": frame["Rndrng_Prvdr_State_Abrvtn"],
            "medicare_participating": frame["Rndrng_Prvdr_Mdcr_Prtcptg_Ind"],
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
    """The source file for each year: the filtered pull where there is one, else the full file."""
    folder = Path(raw_root) / FOLDER
    found: dict[int, Path] = {}
    for path in sorted(folder.rglob("PHY_R*_Prov_Svc.csv")):
        match = re.search(r"_D(\d{2})_Prov_Svc", path.name)
        if match:
            found[2000 + int(match.group(1))] = path
    for path in sorted(folder.glob("*/PartB_ProviderService_*_approved_codes.csv")):
        match = re.search(r"PartB_ProviderService_(\d{4})_", path.name)
        if match:
            found[int(match.group(1))] = path  # the filtered pull is preferred for its year
    return sorted(found.items())


def load_partb_provider(engine: Engine, raw_root: Path) -> dict[int, int]:
    loaded = {}
    for year, path in files(raw_root):
        started = now()
        verified_entry(raw_root, path)
        read, parts = 0, []
        for chunk in iter_csv(path, usecols=USECOLS):
            read += len(chunk)
            parts.append(clean_partb_provider(chunk, year))
        frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        loaded[year] = finish_partition(
            engine,
            raw_root,
            source=SOURCE,
            year=year,
            path=path,
            table="fact_ext_partb_provider",
            frame=frame,
            rows_read=read,
            where={"year": year},
            started_at=started,
        )
    return loaded
