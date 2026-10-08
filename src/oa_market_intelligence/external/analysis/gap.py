"""Gap diagnosis (protocol section 9): why do company sales and IQVIA visits diverge?

Four descriptive checks on two years (2021 and 2024): D1 population, D2 setting, D3 value per
visit, D4 concentration by specialty. Pure functions on small inputs so each rule can be tested
on a planted case. Changes are fractions (0.15 is 15 percentage points when two changes are
compared). Nothing here says what caused anything.
"""

from __future__ import annotations

import pandas as pd

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.turn import _at_least

UNSPECIFIED_AGE = "UNSPECIFIED"


def change(base: float, last: float) -> float:
    if base == 0:
        raise ZeroDivisionError("the base value is zero, so a change is undefined")
    return float(last) / float(base) - 1


def _verdict(met: bool) -> str:
    return "supported" if met else "not_supported"


# ---------------------------------------------------------------- D1 population


def d1_result(
    iqvia_65: tuple[float, float],
    iqvia_u65: tuple[float, float],
    medicare: tuple[float, float],
    *,
    window: float = config.D1_WINDOW,
) -> dict:
    """Supported if (a) the IQVIA 65+ change is within `window` of the Medicare change and (b) the
    IQVIA under-65 change is at least `window` lower than the 65+ change. Each argument is a
    (base year, last year) pair; Medicare is patients per 1,000 Original Medicare beneficiaries."""
    chg_65, chg_u65, chg_med = change(*iqvia_65), change(*iqvia_u65), change(*medicare)
    gap = chg_65 - chg_med
    under_minus_65 = chg_u65 - chg_65
    part_a = _at_least(window, abs(gap))
    part_b = _at_least(-under_minus_65, window)
    return {
        "chg_65": chg_65,
        "chg_under65": chg_u65,
        "chg_medicare": chg_med,
        "gap": gap,
        "under65_minus_65": under_minus_65,
        "part_a": part_a,
        "part_b": part_b,
        "verdict": _verdict(part_a and part_b),
    }


# ---------------------------------------------------------------- D2 setting


def d2_result(
    facility: tuple[float, float], office: tuple[float, float], *, shift: float = config.D2_SHIFT
) -> dict:
    """Supported if the facility share of Medicare Zilretta services rose by `shift`."""
    share_base = facility[0] / (facility[0] + office[0])
    share_last = facility[1] / (facility[1] + office[1])
    moved = share_last - share_base
    return {
        "share_base": share_base,
        "share_last": share_last,
        "shift": moved,
        "verdict": _verdict(_at_least(moved, shift)),
    }


# ---------------------------------------------------------------- D3 value per visit


def d3_result(
    *,
    sales: tuple[float, float],
    visits: tuple[float, float],
    services: tuple[float, float],
    benes: tuple[float, float],
    payment: tuple[float, float],
    sales_threshold: float = config.D3_SALES,
    medicare_threshold: float = config.D3_MEDICARE,
) -> dict:
    """Supported if sales per IQVIA visit rose by `sales_threshold` and either Medicare services
    per patient or payment per service rose by `medicare_threshold`."""
    per_visit = change(sales[0] / visits[0], sales[1] / visits[1])
    per_patient = change(services[0] / benes[0], services[1] / benes[1])
    per_service = change(*payment)
    sales_up = _at_least(per_visit, sales_threshold)
    medicare_up = _at_least(per_patient, medicare_threshold) or _at_least(
        per_service, medicare_threshold
    )
    return {
        "sales_per_visit": per_visit,
        "services_per_patient": per_patient,
        "payment_per_service": per_service,
        "verdict": _verdict(sales_up and medicare_up),
        "unexplained": bool(sales_up and not medicare_up),
    }


def combined_value_change(services_per_patient: float, payment_per_service: float) -> float:
    """Change in Medicare value per patient: services per patient times payment per service. A
    descriptive size check added after the results were seen; it does not change any verdict."""
    return (1 + services_per_patient) * (1 + payment_per_service) - 1


# ---------------------------------------------------------------- D4 concentration


def d4_result(groups: dict[str, tuple[float, float]], *, threshold: float = config.D4_TOP2) -> dict:
    """Concentrated if the two groups with the largest falls account for at least `threshold` of
    the net fall in IQVIA visits. With no net fall the share is undefined and the check fails."""
    falls = {name: base - last for name, (base, last) in groups.items()}
    net = sum(falls.values())
    ordered = sorted(falls, key=lambda name: (-falls[name], name))
    top2 = ordered[:2]
    if net <= 0:
        return {
            "falls": falls,
            "net_fall": net,
            "top2": top2,
            "top2_share": None,
            "verdict": "not_supported",
        }
    share = sum(falls[name] for name in top2) / net
    return {
        "falls": falls,
        "net_fall": net,
        "top2": top2,
        "top2_share": share,
        "verdict": _verdict(_at_least(share, threshold)),
    }


def overall_gap(verdicts: list[str]) -> str:
    """Supported if at least one of D1 to D3 is supported."""
    return "supported" if "supported" in verdicts else "not_supported"


# ---------------------------------------------------------------- sensitivity


def grid_verdicts(verdict_at, grid) -> list[tuple]:
    """The verdict at each setting of a threshold grid: [(setting, verdict), ...]."""
    return [(setting, verdict_at(setting)) for setting in grid]


def verdicts_flip(verdicts: list[tuple]) -> bool:
    return len({verdict for _, verdict in verdicts}) > 1


# ---------------------------------------------------------------- metric builders


def medicare_metrics(
    partb: pd.DataFrame, geovar: pd.DataFrame, settings: tuple[str, ...] = ("F", "O")
) -> pd.DataFrame:
    """Per year, from the national J3304 rows: patients, services, payment per service (weighted
    by services), the two settings' services, and patients per 1,000 Original Medicare
    beneficiaries. A patient seen in both settings is counted twice (stated limit)."""
    rows = partb[partb["setting"].isin(settings)].copy()
    rows["pay_x_services"] = rows["avg_payment_amt"] * rows["services"]
    by_year = rows.groupby("year").agg(
        benes=("benes", "sum"), services=("services", "sum"), pay=("pay_x_services", "sum")
    )
    by_year["payment_per_service"] = by_year["pay"] / by_year["services"]
    by_year["facility_services"] = rows[rows["setting"] == "F"].groupby("year")["services"].sum()
    by_year["office_services"] = rows[rows["setting"] == "O"].groupby("year")["services"].sum()
    by_year[["facility_services", "office_services"]] = by_year[
        ["facility_services", "office_services"]
    ].fillna(0)
    ffs = geovar.set_index("year")["benes_original_medicare"]
    by_year["benes_per_1000_ffs"] = by_year["benes"] / by_year.index.map(ffs) * 1000
    return by_year.drop(columns="pay").reset_index()


def iqvia_metrics(visits: pd.DataFrame, groups: list[str]) -> dict:
    """Zilretta visits by year: all ages, 65 and over, under 65 (the unspecified age band is in
    neither age group) and by specialty group (every specialty outside `groups` is "other").
    `visits` has year, age_band, specialty_name and visits."""
    frame = visits.copy()
    age65 = frame["age_band"].isin(config.IQVIA_65_PLUS)
    under65 = ~age65 & (frame["age_band"] != UNSPECIFIED_AGE)
    frame["group"] = frame["specialty_name"].where(frame["specialty_name"].isin(groups), "other")
    by_group = frame.pivot_table(
        index="group", columns="year", values="visits", aggfunc="sum", fill_value=0
    )
    for name in [*groups, "other"]:
        if name not in by_group.index:
            by_group.loc[name] = 0
    return {
        "total": frame.groupby("year")["visits"].sum(),
        "age65": frame[age65].groupby("year")["visits"].sum(),
        "under65": frame[under65].groupby("year")["visits"].sum(),
        "by_group": by_group,
    }


def hospital_share(pos: pd.DataFrame) -> dict[int, float]:
    """Hospital visits over all visits per year, from the market-wide place-of-service table."""
    total = pos.groupby("year")["visits"].sum()
    hospital = pos[pos["place_of_service"] == "HOSPITAL"].groupby("year")["visits"].sum()
    return {int(year): float(hospital.get(year, 0) / total[year]) for year in total.index}


def annual_sales(revenue: pd.DataFrame) -> dict[int, float]:
    """Company net sales by calendar year, from the year rows only."""
    rows = revenue[revenue["period_type"] == "year"]
    return {
        int(end[:4]): float(value)
        for end, value in zip(rows["period_end"], rows["net_sales_usd"], strict=True)
    }
