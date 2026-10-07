"""Read-only aggregate queries over the Gold tables, for the website and the Q&A tools.

Every function takes a SQLAlchemy engine and returns a small summary (a dict or a
DataFrame). None accepts SQL text, and none returns patient-visit-level rows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import Engine

GROUPINGS = {
    "specialty": "s.specialty_name",
    "age_band": "d.age_band",
    "gender": "d.gender",
}

_WILSON_Z = 1.96


def data_status(engine: Engine) -> dict:
    """What the site is showing: the months covered and the headline totals."""
    with engine.connect() as conn:
        months = pd.read_sql(
            "SELECT MIN(month_id) AS first_month_id, MAX(month_id) AS last_month_id, "
            "COUNT(*) AS n_months, SUM(branded_injectable_visits) AS zilretta_visits, "
            "SUM(branded_injectable_visits + generic_corticosteroid_visits + nsaid_otc_visits) "
            "AS category_visits FROM gold_visit_share_monthly",
            conn,
        ).iloc[0]
    return {key: int(months[key]) for key in months.index}


def market_trend(engine: Engine) -> pd.DataFrame:
    """One row per month: the visit share, its direction label and the three category counts."""
    with engine.connect() as conn:
        trend = pd.read_sql(
            "SELECT month_id, visit_share, direction_label, branded_injectable_visits, "
            "generic_corticosteroid_visits, nsaid_otc_visits "
            "FROM gold_visit_share_monthly ORDER BY month_id",
            conn,
        )
    trend.insert(1, "month", pd.to_datetime(trend["month_id"].astype(str), format="%Y%m"))
    return trend


def _wilson(successes: pd.Series, trials: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Wilson score interval for a proportion. Visits are distinct counts that are not
    independent of each other, so this understates the true uncertainty."""
    p = successes / trials
    z2 = _WILSON_Z**2
    denominator = 1 + z2 / trials
    centre = (p + z2 / (2 * trials)) / denominator
    half = _WILSON_Z * np.sqrt(p * (1 - p) / trials + z2 / (4 * trials**2)) / denominator
    return (centre - half).clip(lower=0.0), (centre + half).clip(upper=1.0)


def segment_table(engine: Engine, *, by: str = "specialty", min_visits: int = 0) -> pd.DataFrame:
    """Zilretta's observed share of the competitive set for each group, against what the
    market-wide share of the same months would predict for that group's volume.

    `ratio` above 1 means the group uses Zilretta more than the market-wide share would
    suggest; below 1, less. It is a lead to investigate, not proof of an opportunity: a
    group can differ for reasons the data cannot see (payer, geography, practice mix)."""
    if by not in GROUPINGS:
        raise ValueError(f"by must be one of {sorted(GROUPINGS)}, got {by!r}")
    column = GROUPINGS[by]
    query = (
        f"SELECT {column} AS grp, "
        "SUM(g.branded_injectable_visits) AS zilretta_visits, "
        "SUM(g.total_category_visits) AS category_visits, "
        "SUM(g.total_category_visits * m.visit_share) AS expected_visits "
        "FROM gold_segment_adoption g "
        "JOIN dim_specialty s ON s.specialty_id = g.specialty_id "
        "JOIN dim_demographics d ON d.demographic_id = g.demographic_id "
        "JOIN gold_visit_share_monthly m ON m.month_id = g.month_id "
        f"GROUP BY {column} HAVING SUM(g.total_category_visits) >= {int(min_visits)} "
        "ORDER BY category_visits DESC"
    )
    with engine.connect() as conn:
        table = pd.read_sql(query, conn)
    table = table.rename(columns={"grp": "group"})
    table["observed_share"] = table["zilretta_visits"] / table["category_visits"]
    table["expected_share"] = table["expected_visits"] / table["category_visits"]
    table["ratio"] = table["observed_share"] / table["expected_share"]
    table["ci_low"], table["ci_high"] = _wilson(table["zilretta_visits"], table["category_visits"])
    return table.drop(columns="expected_visits").reset_index(drop=True)
