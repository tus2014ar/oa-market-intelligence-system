"""Read-only access to what the site shows: the published warehouse and the committed reference
files. Every function returns aggregates (months, specialties, segments of specialty x age band x
gender); none takes SQL text and none returns patient-visit-level rows."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "published" / "warehouse.db"
RUN_LOG = REPO_ROOT / "data" / "published" / "run_log.jsonl"
REFERENCE = REPO_ROOT / "data" / "reference"
FINDINGS_JSON = REFERENCE / "findings.json"
POSTHOC_JSON = REFERENCE / "modelling_posthoc.json"
SERVING_JSON = REFERENCE / "serving_models.json"
GROUPINGS = {"specialty": "s.specialty_name", "age_band": "d.age_band", "gender": "d.gender"}


def open_engine(db_path: Path | str = DB_PATH) -> Engine:
    """A read-only engine: the site can read the warehouse and cannot change it."""
    return create_engine(f"sqlite:///file:{Path(db_path).as_posix()}?mode=ro&uri=true")


def _read(engine: Engine, query: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def market_monthly(engine: Engine) -> pd.DataFrame:
    """One row per month: Zilretta's share of the three-class visit total and the three counts."""
    frame = _read(
        engine,
        "SELECT month_id, branded_injectable_visits AS zilretta, "
        "generic_corticosteroid_visits AS corticosteroid, nsaid_otc_visits AS nsaid_otc, "
        "visit_share FROM gold_visit_share_monthly ORDER BY month_id",
    )
    frame.insert(1, "month", pd.to_datetime(frame["month_id"].astype(str), format="%Y%m"))
    return frame


def segments(engine: Engine, by: str = "specialty", min_visits: int = 1000) -> pd.DataFrame:
    """Zilretta's visits and share of the three-class total for each group (a specialty, an age
    band or a gender), over all months, for groups with at least `min_visits` category visits."""
    if by not in GROUPINGS:
        raise ValueError(f"by must be one of {sorted(GROUPINGS)}, got {by!r}")
    column = GROUPINGS[by]
    table = _read(
        engine,
        f"SELECT {column} AS grp, SUM(g.branded_injectable_visits) AS zilretta_visits, "
        "SUM(g.total_category_visits) AS category_visits "
        "FROM gold_segment_adoption g "
        "JOIN dim_specialty s ON s.specialty_id = g.specialty_id "
        "JOIN dim_demographics d ON d.demographic_id = g.demographic_id "
        f"GROUP BY {column} HAVING SUM(g.total_category_visits) >= {int(min_visits)} "
        "ORDER BY category_visits DESC",
    )
    table = table.rename(columns={"grp": "group"})
    table["share"] = table["zilretta_visits"] / table["category_visits"]
    return table


def specialty_by_year(engine: Engine) -> pd.DataFrame:
    """Zilretta's share by specialty and calendar year (all age bands and genders together)."""
    table = _read(
        engine,
        "SELECT s.specialty_name AS specialty, m.year AS year, "
        "SUM(g.branded_injectable_visits) AS zilretta_visits, "
        "SUM(g.total_category_visits) AS category_visits "
        "FROM gold_segment_adoption g "
        "JOIN dim_specialty s ON s.specialty_id = g.specialty_id "
        "JOIN dim_month m ON m.month_id = g.month_id "
        "GROUP BY s.specialty_name, m.year",
    )
    table["share"] = table["zilretta_visits"] / table["category_visits"]
    return table


def predictions(engine: Engine) -> pd.DataFrame:
    """The monthly predictions (`pred_batch`): national and segment forecasts."""
    return _read(engine, "SELECT * FROM pred_batch")


def model_scores(engine: Engine) -> pd.DataFrame:
    return _read(engine, "SELECT * FROM model_scores")


def backtest(engine: Engine) -> pd.DataFrame:
    return _read(engine, "SELECT * FROM backtest_batch")


def last_run(log_path: Path | str = RUN_LOG) -> dict:
    """The most recent publish run from the run log (empty if there is none)."""
    path = Path(log_path)
    lines = (
        [ln for ln in path.read_text("utf-8").splitlines() if ln.strip()] if path.exists() else []
    )
    return json.loads(lines[-1]) if lines else {}


def _json(path: Path | str) -> dict:
    return json.loads(Path(path).read_text("utf-8"))


def findings(path: Path | str = FINDINGS_JSON) -> list[dict]:
    return _json(path)["findings"]


def posthoc(path: Path | str = POSTHOC_JSON) -> dict:
    return _json(path)


def serving_map(path: Path | str = SERVING_JSON) -> dict:
    return _json(path)
