"""The Context tab, as tested functions: company sales set beside IQVIA visits, what was tested to
explain the gap, Medicare adoption by state and the events timeline (DL-102).

The numbers come from the committed public-data subset (`data/published/external_subset.db`: the
company's filings, CMS Medicare billing and the stored verdicts of the pre-registered analyses),
which is a different source from the IQVIA visits. Nothing here is fitted; the verdicts are
read as stored. Nothing here says why the share moved: the tab sets the sources side by side and
reports what was tested.
"""

from __future__ import annotations

import pandas as pd

BASE_YEAR = 2021  # the Medicare peak and the last year before the visits and sales part ways
GRID_CAP = 4.0  # percent: the map's colour scale stops here
SMALL_PROVIDERS = 100  # visible providers under which a state's rate has a wide interval
GAP_CHECKS = ("D1", "D2", "D3", "D4")
TILE_GRID = {
    "AK": (0, 0), "ME": (0, 11),
    "VT": (1, 10), "NH": (1, 11),
    "WA": (2, 1), "ID": (2, 2), "MT": (2, 3), "ND": (2, 4), "MN": (2, 5), "IL": (2, 6),
    "WI": (2, 7), "MI": (2, 8), "NY": (2, 9), "RI": (2, 10), "MA": (2, 11),
    "OR": (3, 1), "NV": (3, 2), "WY": (3, 3), "SD": (3, 4), "IA": (3, 5), "IN": (3, 6),
    "OH": (3, 7), "PA": (3, 8), "NJ": (3, 9), "CT": (3, 10),
    "CA": (4, 1), "UT": (4, 2), "CO": (4, 3), "NE": (4, 4), "MO": (4, 5), "KY": (4, 6),
    "WV": (4, 7), "VA": (4, 8), "MD": (4, 9), "DE": (4, 10),
    "AZ": (5, 2), "NM": (5, 3), "KS": (5, 4), "AR": (5, 5), "TN": (5, 6), "NC": (5, 7),
    "SC": (5, 8), "DC": (5, 9),
    "OK": (6, 4), "LA": (6, 5), "MS": (6, 6), "AL": (6, 7), "GA": (6, 8),
    "HI": (7, 0), "TX": (7, 4), "FL": (7, 9),
}  # fmt: skip


# ------------------------------------------------------------------ company sales and visits


def _quarter_start(quarter_id: str) -> pd.Timestamp:
    year, quarter = int(quarter_id[:4]), int(quarter_id[-1])
    return pd.Timestamp(year=year, month=3 * (quarter - 1) + 1, day=1)


def annual(company: pd.DataFrame) -> pd.DataFrame:
    """Net sales and IQVIA Zilretta visits by calendar year, for complete years only (four
    quarters), with each year's change from the year before in percent."""
    frame = company.assign(year=company["quarter_id"].str[:4].astype(int))
    grouped = frame.groupby("year").agg(
        net_sales_usd=("net_sales_usd", "sum"),
        visits=("iqvia_zilretta_visits", "sum"),
        quarters=("quarter_id", "count"),
    )
    out = grouped[grouped["quarters"] == 4].reset_index()
    out["sales_change_pct"] = 100 * out["net_sales_usd"].pct_change()
    out["visits_change_pct"] = 100 * out["visits"].pct_change()
    return out


def indexed_quarters(company: pd.DataFrame, base_year: int = BASE_YEAR) -> pd.DataFrame:
    """Each quarter's net sales and visits as a percentage of the average quarter of the base year
    (100 = that average), long form for the chart: `quarter_id`, `date`, `series`, `index`."""
    frame = company.assign(
        year=company["quarter_id"].str[:4].astype(int),
        date=company["quarter_id"].map(_quarter_start),
    )
    base = frame[frame["year"] == base_year]
    if len(base) != 4:
        raise ValueError(f"the base year {base_year} needs four quarters")
    sales_base, visits_base = base["net_sales_usd"].mean(), base["iqvia_zilretta_visits"].mean()
    parts = []
    for label, column, denominator in (
        ("Company net sales", "net_sales_usd", sales_base),
        ("IQVIA Zilretta visits", "iqvia_zilretta_visits", visits_base),
    ):
        parts.append(
            pd.DataFrame(
                {
                    "quarter_id": frame["quarter_id"],
                    "date": frame["date"],
                    "series": label,
                    "index": 100 * frame[column] / denominator,
                    "value": frame[column],
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def headline(years: pd.DataFrame, base_year: int = BASE_YEAR) -> dict:
    """Net sales and visits in the latest complete year against the base year, and that year's
    change from the year before, all in percent."""
    last = years.iloc[-1]
    base = years[years["year"] == base_year].iloc[0]
    return {
        "year": int(last["year"]),
        "base_year": base_year,
        "sales_vs_base_pct": float(100 * (last["net_sales_usd"] / base["net_sales_usd"] - 1)),
        "visits_vs_base_pct": float(100 * (last["visits"] / base["visits"] - 1)),
        "sales_change_pct": float(last["sales_change_pct"]),
        "visits_change_pct": float(last["visits_change_pct"]),
    }


def decline_caveat(findings: list[dict]) -> str:
    """The stored finding 1's own "what it means" (the size of the decline is not a market fact),
    so the tab quotes the project's wording and does not rephrase it."""
    for item in findings:
        if int(item["id"]) == 1:
            return item["impact"]
    return (
        "The size of the IQVIA decline must not be quoted as a market fact: ask IQVIA whether "
        "panel coverage changed before using it."
    )


# ------------------------------------------------------------------ what was tested


def _latest(verdicts: pd.DataFrame, check_id: str) -> pd.DataFrame:
    rows = verdicts[verdicts["check_id"] == check_id]
    return rows[rows["run_id"] == rows["run_id"].max()] if len(rows) else rows


def _value(rows: pd.DataFrame, metric: str) -> float | None:
    part = rows[rows["metric"] == metric]
    return None if part.empty or pd.isna(part["value"].iloc[0]) else float(part["value"].iloc[0])


def _verdict(rows: pd.DataFrame) -> str:
    own = rows[rows["metric"] == "verdict"]
    return str((own if len(own) else rows)["verdict"].iloc[0])


def gap_checks(verdicts: pd.DataFrame) -> list[dict]:
    """The four checks of why sales and visits part ways (written before any code ran), each with
    its question, its stored verdict in plain words and the number behind it. Read from the latest
    stored run of each check."""
    out = []
    for check in GAP_CHECKS:
        rows = _latest(verdicts, check)
        if rows.empty:
            continue
        verdict = _verdict(rows)
        if check == "D1":
            gap = _value(rows, "gap_65_vs_medicare")
            question = "Is it the population (age, payer mix)?"
            result = {"not_supported": "Not supported"}.get(verdict, verdict.replace("_", " "))
            number = (
                "n/a"
                if gap is None
                else f"visits for ages 65+ and Medicare patients part by {abs(100 * gap):.1f} "
                "points (15 allowed)"
            )
        elif check == "D2":
            shift = _value(rows, "facility_share_shift")
            question = "Did Zilretta move to a setting IQVIA covers poorly?"
            result = {"not_supported": "Not supported"}.get(verdict, verdict.replace("_", " "))
            number = (
                "n/a"
                if shift is None
                else f"hospital-outpatient share of Medicare billing moved {100 * shift:.2f} points"
            )
        elif check == "D3":
            per_visit = _value(rows, "sales_per_visit_change")
            services = _value(rows, "services_per_patient_change")
            payment = _value(rows, "payment_per_service_change")
            question = "Is each visit worth more?"
            result = {"supported": "Supported on direction only"}.get(verdict, verdict)
            number = "n/a"
            if None not in (per_visit, services, payment):
                medicare = (1 + services) * (1 + payment) - 1
                number = (
                    f"sales per IQVIA visit {100 * per_visit:+.0f}%, but value per Medicare "
                    f"patient {100 * medicare:+.0f}%"
                )
        else:
            top = _value(rows, "top2_share_of_fall")
            question = "Is the fall concentrated?"
            result = {"supported": "Concentrated"}.get(verdict, verdict.replace("_", " "))
            number = (
                "n/a" if top is None else f"the two largest groups are {100 * top:.0f}% of the fall"
            )
        out.append(
            {
                "check": check,
                "question": question,
                "result": result,
                "number": number,
                "verdict": verdict,
            }
        )
    return out


def overall_gap(verdicts: pd.DataFrame) -> str:
    """The stored overall verdict of the gap checks, in plain words."""
    rows = _latest(verdicts, "GAP")
    if rows.empty:
        return ""
    if str(rows["verdict"].iloc[0]) == "supported":
        return (
            "One check holds, on direction only (value per visit); none of the four accounts "
            "for the size of the gap."
        )
    return "No check holds."


# ------------------------------------------------------------------ Medicare adoption by state


def national_adoption(states: pd.DataFrame) -> pd.DataFrame:
    """The share of visible Medicare providers who billed Zilretta, pooled over the states and DC,
    by year, in percent."""
    grouped = states.groupby("year")[["n_zilretta_providers", "n_visible_providers"]].sum()
    out = (100 * grouped["n_zilretta_providers"] / grouped["n_visible_providers"]).rename("pct")
    return out.reset_index()


def state_year(states: pd.DataFrame, year: int) -> pd.DataFrame:
    """One row per state (and DC) for a year: the adoption rate and its interval in percent, the
    providers behind it, whether it is a small state and the headroom (visible providers times the
    national rate minus the state's, so a positive number means below the national rate)."""
    part = states[states["year"] == year].copy()
    for column in ("adoption_rate", "ci_low", "ci_high"):
        part[column.replace("adoption_rate", "adoption") + "_pct"] = 100 * part[column]
    part["small"] = part["n_visible_providers"] < SMALL_PROVIDERS
    part["row"] = part["state_code"].map(lambda s: TILE_GRID[s][0])
    part["col"] = part["state_code"].map(lambda s: TILE_GRID[s][1])
    return part.reset_index(drop=True)


def state_detail(states: pd.DataFrame, code: str, year: int | None = None) -> dict | None:
    """One state on its own: its adoption by year with the interval and the national rate beside it,
    and, for `year` (the latest by default), the providers behind the rate, how far it sits from the
    national rate, its rank among the states shown that year, its headroom, its Medicare Advantage
    share and its arthritis prevalence. None for a code the data does not have."""
    rows = states[states["state_code"] == code].sort_values("year")
    if rows.empty:
        return None
    national = national_adoption(states).set_index("year")["pct"]
    by_year = rows[
        [
            "year",
            "n_visible_providers",
            "n_zilretta_providers",
            "adoption_rate",
            "ci_low",
            "ci_high",
            "headroom",
        ]
    ].copy()
    for column in ("adoption_rate", "ci_low", "ci_high"):
        by_year[column.replace("adoption_rate", "adoption") + "_pct"] = 100 * by_year[column]
    by_year["national_pct"] = by_year["year"].map(national)
    last = rows.iloc[-1] if year is None else rows[rows["year"] == year].iloc[0]
    shown = states[(states["year"] == last["year"]) & (states["reported"] == 1)]
    rank = int((shown["adoption_rate"] > last["adoption_rate"]).sum()) + 1
    adoption = 100 * float(last["adoption_rate"])
    reference = float(national[last["year"]])
    return {
        "name": str(last["state_name"]),
        "code": code,
        "year": int(last["year"]),
        "by_year": by_year.reset_index(drop=True),
        "adoption_pct": adoption,
        "national_pct": reference,
        "gap_pp": adoption - reference,
        "providers": int(last["n_visible_providers"]),
        "zilretta_providers": int(last["n_zilretta_providers"]),
        "headroom": None if pd.isna(last["headroom"]) else float(last["headroom"]),
        "rank": rank,
        "of": int(len(shown)),
        "ma_pct": None
        if pd.isna(last["ma_participation_rate"])
        else 100 * float(last["ma_participation_rate"]),
        "arthritis_pct": None
        if pd.isna(last["arthritis_prevalence_pct"])
        else float(last["arthritis_prevalence_pct"]),
        "small": int(last["n_visible_providers"]) < SMALL_PROVIDERS,
    }


def state_sentence(d: dict) -> str:
    """A state in one plain sentence: its rate against the national rate and its rank."""
    side = "below" if d["gap_pp"] < 0 else "above"
    return (
        f"{d['name']}: {d['adoption_pct']:.2f}% of {d['providers']:,} visible Medicare providers "
        f"billed Zilretta in {d['year']}, {abs(d['gap_pp']):.2f} points {side} the national "
        f"{d['national_pct']:.2f}%; {d['rank']} of {d['of']} states by rate."
    )


def rank_stability(states: pd.DataFrame, first: int = 2022, last: int = 2024) -> float:
    """The rank correlation of the states' adoption between two years (1 means they keep their
    order); the project's own gate for using the state ranking was 0.7."""
    wide = states[states["year"].isin([first, last])].pivot(
        index="state_code", columns="year", values="adoption_rate"
    )
    wide = wide.dropna()
    return float(wide[first].rank().corr(wide[last].rank()))


def below_national(view: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """The states furthest below the national rate in provider terms: the largest positive headroom
    first. Only reported states with a headroom figure are listed."""
    shown = view[(view["reported"] == 1) & view["headroom"].notna() & (view["headroom"] > 0)]
    return shown.sort_values(["headroom", "state_code"], ascending=[False, True]).head(n)


# ------------------------------------------------------------------ the events


def _date_text(start: str, end: str | None) -> str:
    first = pd.Timestamp(start)
    if end is None or pd.isna(end):
        return f"{first.day} {first.strftime('%b %Y')}"
    last = pd.Timestamp(end)
    return f"{first.day} {first.strftime('%b %Y')} to {last.day} {last.strftime('%b %Y')}"


def event_rows(events: pd.DataFrame) -> list[dict]:
    """The events oldest first, each with a readable date (or date range), its type, its text, its
    source and whether it was verified against that source."""
    ordered = events.sort_values(["event_date", "event_id"])
    return [
        {
            "date": _date_text(r.event_date, r.event_end_date),
            "type": str(r.event_type).replace("_", " "),
            "description": str(r.description),
            "source": str(r.source),
            "verified": bool(r.verified),
        }
        for r in ordered.itertuples()
    ]


def csv_sales_visits(years: pd.DataFrame) -> bytes:
    """The annual company sales and visits as a CSV."""
    out = pd.DataFrame(
        {
            "year": years["year"],
            "net_sales_usd": years["net_sales_usd"].astype(int),
            "iqvia_zilretta_visits": years["visits"].astype(int),
            "sales_change_pct": years["sales_change_pct"].round(1),
            "visits_change_pct": years["visits_change_pct"].round(1),
        }
    )
    return out.to_csv(index=False).encode("utf-8")


def tiles_complete(codes: set[str]) -> bool:
    """True when the map has a tile for every state in `codes` and no two tiles share a cell."""
    cells = list(TILE_GRID.values())
    return codes <= set(TILE_GRID) and len(cells) == len(set(cells))
