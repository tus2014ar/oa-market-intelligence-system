"""Run E4 (H1 to H4), the E5 trigger check and E2b on the loaded data (step 6c)."""

from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import Engine, text

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.store import replace_gold, write_verdicts
from oa_market_intelligence.external.analysis.turn import (
    H4_CATEGORIES,
    block_permutation_lags,
    e2b_table,
    e2b_verdict,
    e5_trigger,
    h1_verdict,
    h2_verdict,
    h3_verdict,
    h4_result,
    price_per_mg,
    quarterly_sales,
    quarterly_visits,
)

PHYSICIAN, PRACTITIONER = "physician", "non_physician_practitioner"
ACQUISITION = "Pacira closed its acquisition of Flexion on 19 November 2021"

RULE_H1 = (
    "supported if in any month from Nov 2020 to Jul 2022 the 6-month mean of physicians paid is "
    "at least 25% below the previous 6-month mean"
)
RULE_LAG = (
    "association only: Pearson r of first differences, promotion leading IQVIA share by 0 to 6 "
    "months, block permutation (blocks of 6, 2,000 permutations), Bonferroni across 7 lags"
)
RULE_H2 = (
    "supported if the J3304 per-mg payment limit relative to J3301 per-mg changes by at least "
    "10% between 2020Q2 and 2021Q2"
)
RULE_H3 = (
    "supported only if the detected break is within 6 months of April 2021 (end of pass-through "
    "status); decided by the existing result"
)
RULE_H4 = (
    "consistent with a data-capture artefact if all three IQVIA categories are at least 10% "
    "below the same months of 2023 over March to July 2024"
)
RULE_E2B = (
    "consistent if the year-over-year direction of Zilretta net sales and IQVIA Zilretta visits "
    "agrees in at least 70% of quarters from Q3 2020 to Q2 2025 (flat = change under 1%)"
)
RULE_E5 = "runs only if H1 shows a Bonferroni-significant lag; otherwise skipped and recorded"


def _read(engine: Engine, query: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)


def load_promotion(external: Engine) -> pd.DataFrame:
    """Zilretta Open Payments by month: distinct physicians and practitioners paid, and totals
    over every recipient type."""
    rows = _read(
        external,
        "SELECT month_id, recipient_type, n_records, n_distinct_recipients, total_amount_usd, "
        "n_flagged_records FROM fact_ext_openpay_month WHERE product = 'Zilretta'",
    )
    people = rows.pivot_table(
        index="month_id", columns="recipient_type", values="n_distinct_recipients", aggfunc="sum"
    )
    totals = rows.groupby("month_id")[["n_records", "total_amount_usd", "n_flagged_records"]].sum()
    out = totals.join(people[[PHYSICIAN]].rename(columns={PHYSICIAN: "n_physicians_paid"}))
    if PRACTITIONER in people:
        out = out.join(
            people[[PRACTITIONER]].rename(columns={PRACTITIONER: "n_practitioners_paid"})
        )
    else:
        out["n_practitioners_paid"] = float("nan")
    months = range(out.index.min(), out.index.max() + 1)
    valid = [m for m in months if 1 <= m % 100 <= 12]
    out = out.reindex(valid)
    out[["n_physicians_paid", "n_records", "n_flagged_records"]] = out[
        ["n_physicians_paid", "n_records", "n_flagged_records"]
    ].fillna(0)
    out["total_amount_usd"] = out["total_amount_usd"].fillna(0.0)
    return out.rename_axis("month_id").reset_index()


def load_iqvia_monthly(iqvia: Engine) -> pd.DataFrame:
    return _read(
        iqvia,
        "SELECT month_id, branded_injectable_visits, generic_corticosteroid_visits, "
        "nsaid_otc_visits, visit_share FROM gold_visit_share_monthly ORDER BY month_id",
    )


def stored_break_months(iqvia: Engine) -> list[int]:
    payload = _read(iqvia, "SELECT payload FROM serving_results WHERE key = 'findings'")
    return [int(m) for m in json.loads(payload["payload"].iloc[0])["trend"]["break_months"]]


def price_table(external: Engine) -> pd.DataFrame:
    asp = _read(
        external,
        "SELECT quarter_id, hcpcs_code, payment_limit FROM fact_ext_asp_price "
        "WHERE hcpcs_code IN ('J3304', 'J3301')",
    )
    wide = asp.pivot(index="quarter_id", columns="hcpcs_code", values="payment_limit")
    out = pd.DataFrame(
        {
            "j3304_limit_per_mg": price_per_mg(wide["J3304"], 1),
            "j3301_limit_per_mg": price_per_mg(wide["J3301"], config.H2_J3301_MG_PER_UNIT),
        }
    )
    out["price_ratio"] = out["j3304_limit_per_mg"] / out["j3301_limit_per_mg"]
    return out.rename_axis("quarter_id").reset_index()


def run_h1(promotion: pd.DataFrame, iqvia_monthly: pd.DataFrame) -> dict:
    physicians = promotion.set_index("month_id")["n_physicians_paid"]
    share = iqvia_monthly.set_index("month_id")["visit_share"]
    # practitioners were reported from January 2021 only, so the sensitivity series starts there
    both = (
        promotion.set_index("month_id")[["n_physicians_paid", "n_practitioners_paid"]]
        .loc[202101:]
        .sum(axis=1)
    )
    return {
        "decline": h1_verdict(physicians),
        "decline_with_practitioners": h1_verdict(both, window=(202112, config.H1_WINDOW[1])),
        "lags": block_permutation_lags(physicians, share),
        "lags_with_practitioners": block_permutation_lags(both, share),
    }


def _lag_rows(prefix: str, lags: pd.DataFrame, note: str) -> list[dict]:
    rows = []
    for row in lags.itertuples():
        rows.append(
            {
                "check_id": "H1",
                "metric": f"{prefix}lag{row.lag}_r",
                "value": row.r,
                "threshold": f"p < {row.alpha:.4f}",
                "rule": RULE_LAG,
                "verdict": "supported" if row.significant else "not_supported",
                "note": f"p = {row.p_value:.4f}. {note}",
            }
        )
    return rows


def build_rows(
    h1: dict, price: pd.DataFrame, break_months: list[int], iqvia_monthly: pd.DataFrame, e2b: dict
) -> list[dict]:
    rows: list[dict] = []
    d = h1["decline"]
    rows.append(
        {
            "check_id": "H1",
            "metric": "worst_6mo_change",
            "value": d["worst_change"],
            "threshold": f"<= {config.H1_DECLINE}",
            "rule": RULE_H1,
            "verdict": d["verdict"],
            "note": f"Worst month {d['worst_month']}; {d['n_months_meeting']} of "
            f"{d['n_months_tested']} window months meet the rule, the first being "
            f"{d['first_month_meeting']}. Association only: {ACQUISITION}, so a change of "
            "payer-of-record or reporting practice cannot be separated from a real pull-back.",
        }
    )
    s = h1["decline_with_practitioners"]
    rows.append(
        {
            "check_id": "H1",
            "metric": "sens_practitioners_worst_6mo_change",
            "value": s["worst_change"],
            "threshold": f"<= {config.H1_DECLINE}",
            "rule": RULE_H1 + " (physicians plus practitioners)",
            "verdict": s["verdict"],
            "note": "Sensitivity. Practitioners are reported from January 2021, so only months "
            f"from December 2021 can be tested ({s['n_months_tested']} of them).",
        }
    )
    rows += _lag_rows("", h1["lags"], "Physicians, IQVIA monthly visit share.")
    rows += _lag_rows(
        "sens_practitioners_",
        h1["lags_with_practitioners"],
        "Sensitivity: physicians plus practitioners, from January 2021.",
    )

    ratio = price.set_index("quarter_id")["price_ratio"].to_dict()
    h2 = h2_verdict(ratio)
    rows.append(
        {
            "check_id": "H2",
            "metric": "price_ratio_change",
            "value": h2["change"],
            "threshold": f"abs >= {config.H2_CHANGE}",
            "rule": RULE_H2,
            "verdict": h2["verdict"],
            "note": f"Ratio {h2['from']:.1f} in {config.H2_FROM} to {h2['to']:.1f} in "
            f"{config.H2_TO}. NOT A CLEAN TEST (protocol disclosure 5). The ratio moves because "
            "the J3301 comparator's limit fell; the J3304 limit itself fell by less.",
        }
    )

    if break_months:
        h3 = h3_verdict(break_months[0])
        rows.append(
            {
                "check_id": "H3",
                "metric": "break_distance_months",
                "value": float(h3["distance_months"]),
                "threshold": f"abs <= {config.H3_WINDOW_MONTHS}",
                "rule": RULE_H3,
                "verdict": h3["verdict"],
                "note": f"Stored break month {h3['break_month']} against April 2021.",
            }
        )
    else:
        rows.append(
            {
                "check_id": "H3",
                "metric": "break_distance_months",
                "value": None,
                "threshold": None,
                "rule": RULE_H3,
                "verdict": "not_run",
                "note": "No break stored in the IQVIA findings.",
            }
        )

    h4 = h4_result(iqvia_monthly)
    for name in H4_CATEGORIES:
        change = h4["changes"][name]
        rows.append(
            {
                "check_id": "H4",
                "metric": f"{name}_change",
                "value": change,
                "threshold": f"<= {-config.H4_DROP}",
                "rule": RULE_H4,
                "verdict": "supported" if change <= -config.H4_DROP + 1e-9 else "not_supported",
                "note": "March to July 2024 against March to July 2023.",
            }
        )
    spread = ", ".join(f"{k.replace('_visits', '')} {v:+.0%}" for k, v in h4["changes"].items())
    rows.append(
        {
            "check_id": "H4",
            "metric": "all_three_below",
            "value": None,
            "threshold": None,
            "rule": RULE_H4,
            "verdict": h4["verdict"],
            "note": f"{spread}. The rule is met when all three fall, but the sizes differ a "
            "great deal, so a capture problem alone does not explain the whole branded fall.",
        }
    )

    verdict = e2b["verdict"]
    rows.append(
        {
            "check_id": "E2b",
            "metric": "direction_agreement",
            "value": verdict["agreement"],
            "threshold": f">= {config.E2B_AGREEMENT_MIN}",
            "rule": RULE_E2B,
            "verdict": verdict["verdict"],
            "note": f"{verdict['n_agree']} of {verdict['n_quarters']} quarters agree. WEAKENED "
            "TEST (protocol disclosure 1). Sales are national and all-payer; the third quarter "
            "of 2020 is compared on August and September only (IQVIA starts August 2019).",
        }
    )

    triggered, lags = e5_trigger(h1["lags"])
    rows.append(
        {
            "check_id": "E5",
            "metric": "trigger",
            "value": float(len(lags)),
            "threshold": ">= 1 significant lag",
            "rule": RULE_E5,
            "verdict": "not_run",
            "note": (
                f"Triggered at lag(s) {lags}; the forecast comparison is not yet run."
                if triggered
                else "Skipped: no lag is significant after the Bonferroni correction."
            ),
        }
    )
    return rows


def run_6c(external: Engine, iqvia: Engine, run_id: str) -> dict:
    promotion = load_promotion(external)
    iqvia_monthly = load_iqvia_monthly(iqvia)
    h1 = run_h1(promotion, iqvia_monthly)
    price = price_table(external)

    revenue = _read(
        external,
        "SELECT period_end, company, period_type, net_sales_usd, derived "
        "FROM fact_ext_company_revenue WHERE product = 'Zilretta'",
    )
    visits = quarterly_visits(iqvia_monthly.set_index("month_id")["branded_injectable_visits"])
    table = e2b_table(quarterly_sales(revenue), visits)
    e2b = {"table": table, "verdict": e2b_verdict(table)}

    shares = iqvia_monthly.set_index("month_id")["visit_share"] * 100
    promo_gold = promotion.assign(iqvia_share_pct=promotion["month_id"].map(shares))
    replace_gold(external, "gold_ext_promotion_monthly", promo_gold)
    replace_gold(external, "gold_ext_price_quarterly", price)
    replace_gold(external, "gold_ext_company_vs_visits", table)

    rows = build_rows(h1, price, stored_break_months(iqvia), iqvia_monthly, e2b)
    write_verdicts(external, run_id, rows)
    return {"h1": h1, "price": price, "e2b": e2b, "rows": rows}
