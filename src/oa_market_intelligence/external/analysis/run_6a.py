"""Run E1 (specialty ranking) and E2a (national adoption trend) on the loaded data (step 6a)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

from oa_market_intelligence.analysis.adoption import load_adoption_data
from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.adoption import (
    cluster_bootstrap_ci,
    e1_result,
    e2a_verdict,
    iqvia_adjusted_specialty_shares,
    provider_years,
)
from oa_market_intelligence.external.analysis.store import replace_gold, write_verdicts
from oa_market_intelligence.external.loaders.reference import crosswalk_frame

WEAK_LINK = "OSTEOPATHIC MEDICINE"
RULE_E1 = (
    "agrees if Spearman rho >= 0.6 and at least 2 of the top 3 groups are shared; partial if "
    "exactly one holds; otherwise disagrees"
)


def load_provider_years(external: Engine) -> pd.DataFrame:
    query = (
        "SELECT year, npi, hcpcs_code, specialty_cms, entity_type, state_code "
        "FROM fact_ext_partb_provider WHERE entity_type = 'I'"
    )
    with external.connect() as conn:
        frame = pd.read_sql(text(query), conn)
    return provider_years(frame, crosswalk_frame())


def _e1_rows(label: str, out: dict, note: str) -> list[dict]:
    interval = (
        f"95% interval {out['rho_low']:.2f} to {out['rho_high']:.2f} ({out['n_groups']} groups)"
    )
    overlap_pass = out["overlap"] >= config.E1_TOP_MIN
    return [
        {
            "check_id": "E1",
            "metric": f"{label}rho",
            "value": out["rho"],
            "threshold": f">= {config.E1_RHO_MIN}",
            "rule": RULE_E1,
            "verdict": "agrees" if out["rho"] >= config.E1_RHO_MIN else "disagrees",
            "note": f"{interval}. {note}",
        },
        {
            "check_id": "E1",
            "metric": f"{label}top3_overlap",
            "value": float(out["overlap"]),
            "threshold": f">= {config.E1_TOP_MIN} of {config.E1_TOP_K}",
            "rule": RULE_E1,
            "verdict": "agrees" if overlap_pass else "disagrees",
            "note": f"Medicare top 3: {', '.join(out['top3_medicare'])}; IQVIA top 3: "
            f"{', '.join(out['top3_iqvia'])}. {note}",
        },
        {
            "check_id": "E1",
            "metric": f"{label}verdict",
            "value": None,
            "threshold": None,
            "rule": RULE_E1,
            "verdict": out["verdict"],
            "note": note,
        },
    ]


def run_e1(py: pd.DataFrame, iqvia_df: pd.DataFrame) -> dict:
    window = py[py["year"].between(*config.E1_YEARS)]
    kwargs = {"first_month": config.E1_FIRST_MONTH, "last_month": config.E1_LAST_MONTH}
    iqvia_all = iqvia_adjusted_specialty_shares(iqvia_df, **kwargs)
    iqvia_65 = iqvia_adjusted_specialty_shares(iqvia_df, ages=config.IQVIA_65_PLUS, **kwargs)
    return {
        "window": window,
        "iqvia_all": iqvia_all,
        "iqvia_65": iqvia_65,
        "all_ages": e1_result(window, iqvia_all),
        "ages_65": e1_result(window, iqvia_65),
        "without_weak_link": e1_result(window, iqvia_all, drop=[WEAK_LINK]),
    }


def triangulation_table(py: pd.DataFrame, e1: dict) -> pd.DataFrame:
    crosswalk = crosswalk_frame()
    relation = crosswalk.drop_duplicates("iqvia_group").set_index("iqvia_group")["relationship"]
    groups = e1["all_ages"]["groups"]
    pieces = []
    for year in range(config.E1_YEARS[0], config.E1_YEARS[1] + 1):
        pieces.append(
            cluster_bootstrap_ci(py[py["year"] == year], "group").assign(period=str(year))
        )
    pooled = cluster_bootstrap_ci(e1["window"], "group").assign(period="pooled_2020_2024")
    table = pd.concat([*pieces, pooled], ignore_index=True)
    table = table[table["group"].isin(groups)].rename(columns={"group": "specialty_group"})
    table["relationship"] = table["specialty_group"].map(relation)
    table["iqvia_adjusted_share"] = None
    table["iqvia_share_65plus"] = None
    is_pooled = table["period"] == "pooled_2020_2024"
    table.loc[is_pooled, "iqvia_adjusted_share"] = table.loc[is_pooled, "specialty_group"].map(
        e1["iqvia_all"]
    )
    table.loc[is_pooled, "iqvia_share_65plus"] = table.loc[is_pooled, "specialty_group"].map(
        e1["iqvia_65"]
    )
    table = table.rename(
        columns={
            "n_provider_years": "n_visible_providers",
            "n_zilretta": "n_zilretta_providers",
            "rate": "medicare_adoption_rate",
            "low": "medicare_ci_low",
            "high": "medicare_ci_high",
        }
    )
    table["iqvia_ci_low"] = None
    table["iqvia_ci_high"] = None
    return table[
        [
            "period",
            "specialty_group",
            "relationship",
            "n_visible_providers",
            "n_zilretta_providers",
            "medicare_adoption_rate",
            "medicare_ci_low",
            "medicare_ci_high",
            "iqvia_adjusted_share",
            "iqvia_ci_low",
            "iqvia_ci_high",
            "iqvia_share_65plus",
        ]
    ]


def run_e2a(py: pd.DataFrame) -> dict:
    py = py.assign(all="all")
    by_year = cluster_bootstrap_ci(py, "year")
    rate_by_year = {int(y): float(r) for y, r in zip(by_year["year"], by_year["rate"], strict=True)}
    verdict, peak = e2a_verdict(rate_by_year)
    return {"by_year": by_year, "verdict": verdict, "peak_year": peak, "rates": rate_by_year}


def run_6a(external: Engine, iqvia: Engine, run_id: str) -> dict:
    py = load_provider_years(external)
    e1 = run_e1(py, load_adoption_data(iqvia))
    e2a = run_e2a(py)
    replace_gold(external, "gold_ext_specialty_triangulation", triangulation_table(py, e1))
    rows = []
    rows += _e1_rows(
        "", e1["all_ages"], "All ages, IQVIA adjusted shares recomputed for 2020 to 2024."
    )
    rows += _e1_rows("age65_", e1["ages_65"], "IQVIA restricted to patients aged 65 and over.")
    rows += _e1_rows(
        "no_osteopathic_",
        e1["without_weak_link"],
        "Sensitivity: the weak crosswalk match (osteopathic medicine) removed.",
    )
    rates = ", ".join(f"{y}: {r:.4f}" for y, r in e2a["rates"].items())
    rows.append(
        {
            "check_id": "E2a",
            "metric": "peak_year",
            "value": float(e2a["peak_year"]),
            "threshold": "2021 or 2022, with 2024 below the peak",
            "rule": "consistent if national Medicare adoption peaks in 2021 or 2022 and 2024 is "
            "below the peak",
            "verdict": e2a["verdict"],
            "note": f"WEAKENED TEST (protocol disclosure 1). Adoption by year: {rates}.",
        }
    )
    write_verdicts(external, run_id, rows)
    return {"py": py, "e1": e1, "e2a": e2a}
