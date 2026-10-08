"""Run the gap diagnosis (protocol section 9) on the loaded data and store D1 to D4 verdicts."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.gap import (
    annual_sales,
    combined_value_change,
    d1_result,
    d2_result,
    d3_result,
    d4_result,
    grid_verdicts,
    hospital_share,
    iqvia_metrics,
    medicare_metrics,
    overall_gap,
    verdicts_flip,
)
from oa_market_intelligence.external.analysis.run_6a import load_provider_years
from oa_market_intelligence.external.analysis.store import write_verdicts
from oa_market_intelligence.external.analysis.turn import _at_least
from oa_market_intelligence.external.loaders.reference import crosswalk_frame

RULES = {
    "D1": (
        "supported if (a) the IQVIA 65+ Zilretta visit change is within 15 points of the change "
        "in Medicare Zilretta patients per 1,000 Original Medicare beneficiaries and (b) the "
        "IQVIA under-65 change is at least 15 points lower than the 65+ change"
    ),
    "D2": "supported if the facility share of Medicare Zilretta services rose by 10 points or more",
    "D3": (
        "supported if company sales per IQVIA visit rose by 25% or more and either Medicare "
        "services per patient or payment per service rose by 10% or more"
    ),
    "D4": (
        "supported (concentrated) if the two specialty groups with the largest falls account for "
        "at least 50% of the net fall in IQVIA Zilretta visits"
    ),
    "GAP": "supported if at least one of D1 to D3 is supported; otherwise not supported",
}
UNTESTABLE = (
    "Company-side causes (inventory, gross-to-net adjustments, channel mix) need data we do not "
    "hold and are untestable here."
)


def _read(engine: Engine, query: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)


def load_context(external: Engine, iqvia: Engine, py: pd.DataFrame | None = None) -> dict:
    visits = _read(
        iqvia,
        "SELECT dm.year AS year, dd.age_band AS age_band, ds.specialty_name AS specialty_name, "
        "SUM(f.patient_visits) AS visits FROM fact_product_visits f "
        "JOIN dim_month dm ON f.month_id = dm.month_id "
        "JOIN dim_demographics dd ON f.demographic_id = dd.demographic_id "
        "JOIN dim_specialty ds ON f.specialty_id = ds.specialty_id "
        "WHERE f.product_id = (SELECT product_id FROM dim_product WHERE product_name = 'ZILRETTA') "
        "GROUP BY 1, 2, 3",
    )
    pos = _read(
        iqvia,
        "SELECT dm.year AS year, p.place_of_service AS place_of_service, "
        "SUM(p.patient_visits) AS visits FROM fact_place_of_service_visits p "
        "JOIN dim_month dm ON p.month_id = dm.month_id GROUP BY 1, 2",
    )
    partb = _read(
        external,
        "SELECT year, setting, benes, services, avg_payment_amt FROM fact_ext_partb_geo "
        "WHERE geo_level = 'National' AND hcpcs_code = 'J3304'",
    )
    geovar = _read(
        external,
        "SELECT year, benes_original_medicare FROM fact_ext_geo_variation "
        "WHERE geo_level = 'National' AND age_level = 'All'",
    )
    revenue = _read(
        external,
        "SELECT period_end, period_type, net_sales_usd FROM fact_ext_company_revenue "
        "WHERE product = 'Zilretta'",
    )
    crosswalk = crosswalk_frame()
    groups = sorted(set(crosswalk.loc[crosswalk["relationship"] != "excluded", "iqvia_group"]))
    py = load_provider_years(external) if py is None else py
    providers = (
        py[py["group"].isin(groups)]
        .groupby(["group", "year"])["zilretta"]
        .sum()
        .unstack(fill_value=0)
    )
    return {
        "iqvia": iqvia_metrics(visits, groups),
        "hospital": hospital_share(pos),
        "medicare_both": medicare_metrics(partb, geovar),
        "medicare_office": medicare_metrics(partb, geovar, settings=("O",)),
        "sales": annual_sales(revenue),
        "providers": providers,
        "groups": groups,
    }


def evaluate(
    ctx: dict,
    base: int = config.GAP_BASE_YEAR,
    last: int = config.GAP_LAST_YEAR,
    *,
    window: float = config.D1_WINDOW,
    shift: float = config.D2_SHIFT,
    d3: tuple[float, float] = (config.D3_SALES, config.D3_MEDICARE),
    top2: float = config.D4_TOP2,
    office_only: bool = False,
) -> dict:
    iq = ctx["iqvia"]
    both = ctx["medicare_both"].set_index("year")
    med = (ctx["medicare_office"] if office_only else ctx["medicare_both"]).set_index("year")

    def pair(series) -> tuple[float, float]:
        return float(series[base]), float(series[last])

    groups = {name: pair(iq["by_group"].loc[name]) for name in iq["by_group"].index}
    return {
        "D1": d1_result(
            pair(iq["age65"]),
            pair(iq["under65"]),
            pair(med["benes_per_1000_ffs"]),
            window=window,
        ),
        "D2": d2_result(
            pair(both["facility_services"]), pair(both["office_services"]), shift=shift
        ),
        "D3": d3_result(
            sales=pair(ctx["sales"]),
            visits=pair(iq["total"]),
            services=pair(med["services"]),
            benes=pair(med["benes"]),
            payment=pair(med["payment_per_service"]),
            sales_threshold=d3[0],
            medicare_threshold=d3[1],
        ),
        "D4": d4_result(groups, threshold=top2),
    }


def sensitivity(ctx: dict) -> dict:
    """The verdict of each check across its grid, with 2022 as the base and Medicare office only."""
    grids = {
        "D1": grid_verdicts(lambda w: evaluate(ctx, window=w)["D1"]["verdict"], config.D1_GRID),
        "D2": grid_verdicts(lambda s: evaluate(ctx, shift=s)["D2"]["verdict"], config.D2_GRID),
        "D3": grid_verdicts(lambda t: evaluate(ctx, d3=t)["D3"]["verdict"], config.D3_GRID),
        "D4": grid_verdicts(lambda t: evaluate(ctx, top2=t)["D4"]["verdict"], config.D4_GRID),
    }
    alt_base = evaluate(ctx, base=config.GAP_ALT_BASE_YEAR)
    office = evaluate(ctx, office_only=True)
    return {
        check: {
            "grid": grids[check],
            "flips": verdicts_flip(grids[check]),
            "alt_base": alt_base[check]["verdict"],
            "office_only": office[check]["verdict"] if check in ("D1", "D3") else None,
        }
        for check in ("D1", "D2", "D3", "D4")
    }


def _sens_note(info: dict) -> str:
    grid = ", ".join(f"{setting}: {verdict}" for setting, verdict in info["grid"])
    text_ = f"Sensitivity (grid, primary in the middle): {grid}."
    text_ += " The verdict changes across the grid." if info["flips"] else " It does not change."
    text_ += f" Base year 2022: {info['alt_base']}."
    if info["office_only"] is not None:
        text_ += f" Medicare office setting only: {info['office_only']}."
    return text_


def _pct(value: float) -> str:
    return f"{value:+.1%}"


def build_rows(ctx: dict, result: dict, sens: dict) -> list[dict]:
    base, last = config.GAP_BASE_YEAR, config.GAP_LAST_YEAR
    d1, d2, d3, d4 = result["D1"], result["D2"], result["D3"], result["D4"]
    rows = []

    def add(check, metric, value, threshold, verdict, note):
        rows.append(
            {
                "check_id": check,
                "metric": metric,
                "value": value,
                "threshold": threshold,
                "rule": RULES[check],
                "verdict": verdict,
                "note": note,
            }
        )

    frame = (
        f"{base} to {last}. The unspecified IQVIA age band is in neither age group; Medicare "
        "patients seen in both settings are counted twice."
    )
    add(
        "D1",
        "gap_65_vs_medicare",
        d1["gap"],
        f"abs <= {config.D1_WINDOW}",
        "supported" if d1["part_a"] else "not_supported",
        f"IQVIA 65+ visits {_pct(d1['chg_65'])}; Medicare patients per 1,000 Original Medicare "
        f"beneficiaries {_pct(d1['chg_medicare'])}. {frame}",
    )
    add(
        "D1",
        "under65_minus_65",
        d1["under65_minus_65"],
        f"<= {-config.D1_WINDOW}",
        "supported" if d1["part_b"] else "not_supported",
        f"IQVIA under-65 visits {_pct(d1['chg_under65'])} against 65+ {_pct(d1['chg_65'])}. "
        f"{frame}",
    )
    add("D1", "verdict", None, None, d1["verdict"], _sens_note(sens["D1"]))

    ctxh = ctx["hospital"]
    add(
        "D2",
        "facility_share_shift",
        d2["shift"],
        f">= {config.D2_SHIFT}",
        d2["verdict"],
        f"Facility share of Medicare Zilretta services {d2['share_base']:.1%} to "
        f"{d2['share_last']:.1%}. Context, no rule: IQVIA hospital share of all OA visits (the "
        f"whole market, not Zilretta) {ctxh[base]:.1%} to {ctxh[last]:.1%}. "
        + _sens_note(sens["D2"]),
    )

    sales_up = _at_least(d3["sales_per_visit"], config.D3_SALES)
    add(
        "D3",
        "sales_per_visit_change",
        d3["sales_per_visit"],
        f">= {config.D3_SALES}",
        "supported" if sales_up else "not_supported",
        f"Net sales ${ctx['sales'][base] / 1e6:.1f}M to ${ctx['sales'][last] / 1e6:.1f}M over "
        f"IQVIA Zilretta visits {int(ctx['iqvia']['total'][base]):,} to "
        f"{int(ctx['iqvia']['total'][last]):,}.",
    )
    for metric, key, label in (
        ("services_per_patient_change", "services_per_patient", "Medicare services per patient"),
        ("payment_per_service_change", "payment_per_service", "Medicare payment per service"),
    ):
        add(
            "D3",
            metric,
            d3[key],
            f">= {config.D3_MEDICARE}",
            "supported" if _at_least(d3[key], config.D3_MEDICARE) else "not_supported",
            f"{label} {_pct(d3[key])}.",
        )
    size = combined_value_change(d3["services_per_patient"], d3["payment_per_service"])
    note = (
        "Size check, added after the results were seen and descriptive only: Medicare services "
        "per patient and payment per service together moved value per Medicare patient by "
        f"{_pct(size)}, against {_pct(d3['sales_per_visit'])} for sales per IQVIA visit. "
    ) + _sens_note(sens["D3"])
    if d3["unexplained"]:
        note = "Sales per visit rose but neither Medicare measure did: unexplained. " + note
    add("D3", "verdict", None, None, d3["verdict"], note)

    falls = sorted(d4["falls"].items(), key=lambda item: -item[1])
    listing = "; ".join(f"{name} {int(fall):+,}" for name, fall in falls)
    providers = ctx["providers"]
    medicare_note = "; ".join(
        f"{name}: {int(providers.loc[name, base])} to {int(providers.loc[name, last])}"
        for name in d4["top2"]
        if name in providers.index
    )
    share = d4["top2_share"]
    add(
        "D4",
        "top2_share_of_fall",
        share,
        f">= {config.D4_TOP2}",
        d4["verdict"],
        f"Falls in IQVIA Zilretta visits {base} to {last} (visits lost): {listing}. Net fall "
        f"{int(d4['net_fall']):,}; top two: {', '.join(d4['top2'])}. Context, no rule: Medicare "
        f"Zilretta providers in those groups ({medicare_note}). " + _sens_note(sens["D4"]),
    )

    overall = overall_gap([d1["verdict"], d2["verdict"], d3["verdict"]])
    add(
        "GAP",
        "any_of_D1_D3",
        None,
        None,
        overall,
        f"D1 {d1['verdict']}, D2 {d2['verdict']}, D3 {d3['verdict']}. D3 is a rule on direction "
        f"only: the Medicare measures moved value per patient by {_pct(size)}, against "
        f"{_pct(d3['sales_per_visit'])} for sales per IQVIA visit, so no tested explanation "
        f"accounts for the size of the gap. D4 describes where the fall sits and does not "
        f"count. {UNTESTABLE}",
    )
    return rows


def run_gap(external: Engine, iqvia: Engine, run_id: str, py: pd.DataFrame | None = None) -> dict:
    ctx = load_context(external, iqvia, py)
    result = evaluate(ctx)
    sens = sensitivity(ctx)
    rows = build_rows(ctx, result, sens)
    write_verdicts(external, run_id, rows)
    return {"ctx": ctx, "result": result, "sensitivity": sens, "rows": rows}
