"""Q1: why did Zilretta's share change? How much is the mix of who is treated, and how much
is Zilretta's own share within each group?

In any window, Zilretta's share of the category total is the sum over groups g of
w_g x r_g, where w_g is the group's share of category visits (the mix) and r_g is Zilretta's
share within the group (the rate). Between two windows a and b the standard two-term
(Kitagawa) decomposition splits the change in total share exactly:

    mix_g  = (w_b - w_a) x (r_a + r_b) / 2
    rate_g = (r_b - r_a) x (w_a + w_b) / 2

and the sum over groups of mix_g + rate_g equals the change in total share. A mix effect
says the mix moved, not why; the split also depends on how finely the groups are cut, so
the analysis is repeated at several levels of detail.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import Engine

GROUPINGS = {
    "specialty": "s.specialty_name",
    "age_gender": "d.age_band || ' | ' || d.gender",
    "segment": "s.specialty_name || ' | ' || d.age_band || ' | ' || d.gender",
}
WINDOW_LENGTH = 12


def group_counts(engine: Engine, *, by: str = "specialty") -> pd.DataFrame:
    """Zilretta and category visits per month and group."""
    if by not in GROUPINGS:
        raise ValueError(f"by must be one of {sorted(GROUPINGS)}, got {by!r}")
    query = (
        f"SELECT g.month_id, {GROUPINGS[by]} AS grp, "
        "SUM(g.branded_injectable_visits) AS zilretta, "
        "SUM(g.total_category_visits) AS category "
        "FROM gold_segment_adoption g "
        "JOIN dim_specialty s ON s.specialty_id = g.specialty_id "
        "JOIN dim_demographics d ON d.demographic_id = g.demographic_id "
        "GROUP BY g.month_id, grp ORDER BY g.month_id, grp"
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn).rename(columns={"grp": "group"})


def year_windows(month_ids) -> list[list[int]]:
    """Consecutive blocks of 12 months, in order."""
    months = sorted(int(m) for m in month_ids)
    if len(months) % WINDOW_LENGTH:
        raise ValueError(f"the number of months must be a multiple of {WINDOW_LENGTH}")
    return [months[i : i + WINDOW_LENGTH] for i in range(0, len(months), WINDOW_LENGTH)]


def _effects(za, ta, zb, tb) -> dict:
    """Mix and rate effects per group from Zilretta (z) and category (t) visit totals in
    windows a and b. A group missing from one window takes its other window's rate, so its
    whole contribution is mix (it has zero weight where it is missing)."""
    total_a, total_b = ta.sum(), tb.sum()
    wa, wb = ta / total_a, tb / total_b
    with np.errstate(divide="ignore", invalid="ignore"):
        ra = np.where(ta > 0, za / ta, np.nan)
        rb = np.where(tb > 0, zb / tb, np.nan)
    ra = np.where(np.isnan(ra), np.where(np.isnan(rb), 0.0, rb), ra)
    rb = np.where(np.isnan(rb), ra, rb)
    mix = (wb - wa) * (ra + rb) / 2
    rate = (rb - ra) * (wa + wb) / 2
    return {
        "wa": wa, "wb": wb, "ra": ra, "rb": rb, "mix": mix, "rate": rate,
        "share_a": za.sum() / total_a, "share_b": zb.sum() / total_b,
    }


def _window_totals(counts: pd.DataFrame, groups: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    totals = counts.groupby("group")[["zilretta", "category"]].sum().reindex(groups, fill_value=0)
    return totals["zilretta"].to_numpy(float), totals["category"].to_numpy(float)


def decompose(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Decompose the change in total share from window `a` to window `b`.

    `a` and `b` have columns group, zilretta, category (any number of months)."""
    groups = pd.Index(sorted(set(a["group"]) | set(b["group"])))
    za, ta = _window_totals(a, groups)
    zb, tb = _window_totals(b, groups)
    e = _effects(za, ta, zb, tb)
    by_group = pd.DataFrame(
        {
            "group": groups,
            "w_a": e["wa"], "w_b": e["wb"], "r_a": e["ra"], "r_b": e["rb"],
            "mix": e["mix"], "rate": e["rate"], "total": e["mix"] + e["rate"],
        }
    )
    return {
        "share_a": float(e["share_a"]),
        "share_b": float(e["share_b"]),
        "total": float(e["share_b"] - e["share_a"]),
        "mix": float(e["mix"].sum()),
        "rate": float(e["rate"].sum()),
        "by_group": by_group,
    }


def decompose_chain(counts: pd.DataFrame, windows: list[list[int]]) -> pd.DataFrame:
    """Each consecutive pair of windows, then the first against the last."""
    pairs = [(i, i + 1) for i in range(len(windows) - 1)]
    if len(windows) > 2:
        pairs.append((0, len(windows) - 1))
    rows = []
    for i, j in pairs:
        result = decompose(
            counts[counts["month_id"].isin(windows[i])],
            counts[counts["month_id"].isin(windows[j])],
        )
        rows.append(
            {
                "comparison": f"{i + 1} to {j + 1}",
                "share_a": result["share_a"],
                "share_b": result["share_b"],
                "total": result["total"],
                "mix": result["mix"],
                "rate": result["rate"],
            }
        )
    return pd.DataFrame(rows)


def bootstrap_decomposition(
    counts: pd.DataFrame,
    months_a: list[int],
    months_b: list[int],
    *,
    n_boot: int = 1000,
    seed: int = 0,
    level: float = 0.90,
) -> dict:
    """Uncertainty from resampling the months inside each window with replacement.

    Resampling months treats a window's months as exchangeable, which ignores the share's
    drift within the window, so the intervals are, if anything, a little wide."""
    groups = pd.Index(sorted(counts["group"].unique()))

    def matrices(months):
        wide = counts[counts["month_id"].isin(months)].pivot_table(
            index="month_id", columns="group", values=["zilretta", "category"],
            aggfunc="sum", fill_value=0,
        )
        z = wide["zilretta"].reindex(columns=groups, fill_value=0).to_numpy(float)
        t = wide["category"].reindex(columns=groups, fill_value=0).to_numpy(float)
        return z, t

    za_m, ta_m = matrices(months_a)
    zb_m, tb_m = matrices(months_b)

    def run(ia, ib):
        e = _effects(za_m[ia].sum(0), ta_m[ia].sum(0), zb_m[ib].sum(0), tb_m[ib].sum(0))
        return e["share_b"] - e["share_a"], e["mix"].sum(), e["rate"].sum()

    point = run(np.arange(len(za_m)), np.arange(len(zb_m)))
    rng = np.random.default_rng(seed)
    draws = np.array(
        [
            run(rng.integers(0, len(za_m), len(za_m)), rng.integers(0, len(zb_m), len(zb_m)))
            for _ in range(n_boot)
        ]
    )
    tail = (1 - level) / 2 * 100
    out = {}
    for k, name in enumerate(("total", "mix", "rate")):
        low, high = np.percentile(draws[:, k], [tail, 100 - tail])
        out[name] = {"estimate": float(point[k]), "low": float(low), "high": float(high)}
    return out
