"""Run E3 (state adoption, rank-stability gate, headroom) on the loaded data (step 6b)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.run_6a import load_provider_years
from oa_market_intelligence.external.analysis.states import (
    add_context,
    add_headroom,
    arthritis_by_state,
    ma_adjusted_sensitivity,
    ma_by_state_year,
    rank_stability,
    state_table,
)
from oa_market_intelligence.external.analysis.store import replace_gold, write_verdicts
from oa_market_intelligence.external.loaders.reference import states_frame

RULE_GATE = (
    "state ranks are usable only if the 2022 and 2024 state adoption ranks correlate at "
    "Spearman rho >= 0.7 (states with at least 30 visible providers in both years); otherwise "
    "unstable and no headroom list is produced"
)


def _read(external: Engine, query: str) -> pd.DataFrame:
    with external.connect() as conn:
        return pd.read_sql(text(query), conn)


def run_e3(py: pd.DataFrame, external: Engine) -> dict:
    states = states_frame()
    table = state_table(py, states)
    stability = rank_stability(py, states)
    usable = stability["verdict"] == "usable"
    geovar = _read(
        external,
        "SELECT year, geo_level, geo_code, age_level, ma_participation_rate "
        "FROM fact_ext_geo_variation WHERE geo_level = 'State'",
    )
    counties = _read(
        external,
        "SELECT state_code, prevalence_pct, total_population FROM fact_ext_arthritis_prevalence",
    )
    table = add_context(table, ma_by_state_year(geovar, states), arthritis_by_state(counties))
    pooled = table.groupby("year")[["n_zilretta_providers", "n_visible_providers"]].sum()
    national = (pooled["n_zilretta_providers"] / pooled["n_visible_providers"]).to_dict()
    table = add_headroom(table, usable, national)
    sensitivity = None
    if usable:
        sensitivity = ma_adjusted_sensitivity(table, config.E3_RANK_YEARS[1])
    return {"table": table, "stability": stability, "usable": usable, "sensitivity": sensitivity}


def _verdict_rows(e3: dict) -> list[dict]:
    s = e3["stability"]
    interval = f"95% interval {s['rho_low']:.2f} to {s['rho_high']:.2f}"
    rows = [
        {
            "check_id": "E3",
            "metric": "rank_rho_2022_2024",
            "value": s["rho"],
            "threshold": f">= {config.E3_RANK_RHO_MIN}",
            "rule": RULE_GATE,
            "verdict": s["verdict"],
            "note": f"{interval}; {s['n_states']} states with at least {config.E3_MIN_PROVIDERS} "
            "visible providers in both years.",
        }
    ]
    sens = e3["sensitivity"]
    if sens is None:
        rows.append(
            {
                "check_id": "E3",
                "metric": "ma_adjusted_rank_rho",
                "value": None,
                "threshold": None,
                "rule": "Advantage-adjusted sensitivity of the headroom ranking (only if usable)",
                "verdict": "not_run",
                "note": "State ranks are unstable, so no headroom ranking exists to adjust.",
            }
        )
    else:
        rows.append(
            {
                "check_id": "E3",
                "metric": "ma_adjusted_rank_rho",
                "value": sens["rho"],
                "threshold": f">= {config.E3_RANK_RHO_MIN}",
                "rule": "Advantage-adjusted sensitivity: headroom ranking after removing the "
                "part of adoption explained by the Advantage share; stable if rho >= 0.7",
                "verdict": "usable" if sens["rho"] >= config.E3_RANK_RHO_MIN else "unstable",
                "note": f"{sens['year']}, {sens['n_states']} states; top {sens['top_k']} shared: "
                f"{sens['top_overlap']}; rank correlation of adoption with Advantage share "
                f"{sens['corr_with_ma']:.2f}.",
            }
        )
    return rows


def run_6b(external: Engine, run_id: str, py: pd.DataFrame | None = None) -> dict:
    py = load_provider_years(external) if py is None else py
    e3 = run_e3(py, external)
    replace_gold(external, "gold_ext_state_adoption", e3["table"])
    write_verdicts(external, run_id, _verdict_rows(e3))
    return e3
