"""E3: where is Zilretta under-used? State adoption, a rank-stability gate and headroom.

A state-year is reported only with at least `E3_MIN_PROVIDERS` visible providers. State ranks are
usable only if the 2022 and 2024 ranks correlate at `E3_RANK_RHO_MIN`; otherwise nothing is ranked
and no headroom is produced (protocol E3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.adoption import (
    cluster_bootstrap_ci,
    spearman,
    top_k_overlap,
)


def _us_states(states: pd.DataFrame) -> list[str]:
    return sorted(states.loc[states["is_us_state_or_dc"] == 1, "state_code"])


def state_table(
    py: pd.DataFrame,
    states: pd.DataFrame,
    *,
    n_boot: int = config.N_BOOT,
    seed: int = config.SEED,
) -> pd.DataFrame:
    """One row per state (or DC) and year: counts always, rate and interval only if reported."""
    us = _us_states(states)
    py = py[py["state_code"].isin(us)]
    pieces = []
    for year in sorted(py["year"].unique()):
        this = py[py["year"] == year]
        counts = (
            this.groupby("state_code")
            .agg(n_visible_providers=("npi", "size"), n_zilretta_providers=("zilretta", "sum"))
            .reindex(us, fill_value=0)
        )
        counts["n_zilretta_providers"] = counts["n_zilretta_providers"].astype(int)
        counts["reported"] = (counts["n_visible_providers"] >= config.E3_MIN_PROVIDERS).astype(int)
        shown = list(counts.index[counts["reported"] == 1])
        counts["adoption_rate"] = np.nan
        counts["ci_low"] = np.nan
        counts["ci_high"] = np.nan
        if shown:
            ci = cluster_bootstrap_ci(
                this[this["state_code"].isin(shown)], "state_code", n_boot=n_boot, seed=seed
            ).set_index("state_code")
            counts.loc[shown, "adoption_rate"] = ci["rate"]
            counts.loc[shown, "ci_low"] = ci["low"]
            counts.loc[shown, "ci_high"] = ci["high"]
        pieces.append(counts.reset_index().assign(year=int(year)))
    table = pd.concat(pieces, ignore_index=True)
    return table[
        [
            "year",
            "state_code",
            "n_visible_providers",
            "n_zilretta_providers",
            "adoption_rate",
            "ci_low",
            "ci_high",
            "reported",
        ]
    ]


def _paired_columns(py: pd.DataFrame, state: str, years: tuple[int, int]):
    """Per provider in a state: visible and Zilretta flags in each of the two years."""
    part = py[(py["state_code"] == state) & py["year"].isin(years)]
    first = part[part["year"] == years[0]].set_index("npi")["zilretta"]
    second = part[part["year"] == years[1]].set_index("npi")["zilretta"]
    npis = first.index.union(second.index)
    vis1 = npis.isin(first.index).astype(float)
    vis2 = npis.isin(second.index).astype(float)
    zil1 = first.reindex(npis).fillna(False).astype(float).to_numpy()
    zil2 = second.reindex(npis).fillna(False).astype(float).to_numpy()
    return vis1, zil1, vis2, zil2


def _row_spearman(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ra = rankdata(a, axis=1)
    rb = rankdata(b, axis=1)
    ra = ra - ra.mean(axis=1, keepdims=True)
    rb = rb - rb.mean(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (ra * rb).sum(axis=1) / np.sqrt((ra**2).sum(axis=1) * (rb**2).sum(axis=1))


def paired_rank_draws(
    py: pd.DataFrame, states: list[str], years: tuple[int, int], *, n_boot: int, seed: int
) -> np.ndarray:
    """Spearman correlation between the two years' state rates in each bootstrap draw.

    Providers are resampled within state and keep both of their years, so the repeat visibility of
    the same people is respected."""
    streams = np.random.SeedSequence(seed).spawn(len(states))
    first = np.empty((n_boot, len(states)))
    second = np.empty((n_boot, len(states)))
    for j, (state, stream) in enumerate(zip(states, streams, strict=True)):
        vis1, zil1, vis2, zil2 = _paired_columns(py, state, years)
        m = len(vis1)
        counts = np.random.default_rng(stream).multinomial(m, np.full(m, 1.0 / m), size=n_boot)
        with np.errstate(invalid="ignore", divide="ignore"):
            first[:, j] = (counts @ zil1) / (counts @ vis1)
            second[:, j] = (counts @ zil2) / (counts @ vis2)
    return _row_spearman(first, second)


def rank_stability(
    py: pd.DataFrame,
    states: pd.DataFrame,
    *,
    n_boot: int = config.N_BOOT,
    seed: int = config.SEED,
    years: tuple[int, int] = config.E3_RANK_YEARS,
) -> dict:
    """Do states keep their rank between the two years? Only states with enough providers in both
    years are compared. `usable` requires rho >= `E3_RANK_RHO_MIN`."""
    table = state_table(py, states, n_boot=1, seed=seed)
    wide = table[table["year"].isin(years)].pivot(
        index="state_code", columns="year", values=["n_visible_providers", "reported"]
    )
    both = wide["reported"][list(years)].fillna(0).eq(1).all(axis=1)
    kept = sorted(both.index[both])
    rates = {
        y: py[(py["year"] == y) & py["state_code"].isin(kept)]
        .groupby("state_code")["zilretta"]
        .mean()
        .reindex(kept)
        for y in years
    }
    rho = spearman(rates[years[0]], rates[years[1]])
    draws = paired_rank_draws(py, kept, years, n_boot=n_boot, seed=seed)
    tail = (1 - config.CI_LEVEL) / 2 * 100
    return {
        "rho": rho,
        "rho_low": float(np.nanpercentile(draws, tail)),
        "rho_high": float(np.nanpercentile(draws, 100 - tail)),
        "verdict": "usable" if rho >= config.E3_RANK_RHO_MIN else "unstable",
        "n_states": len(kept),
        "states": kept,
        "years": list(years),
    }


def add_headroom(
    table: pd.DataFrame, usable: bool, national: dict[int, float] | None = None
) -> pd.DataFrame:
    """Headroom = visible providers x (national adoption - state adoption), reported rows only,
    and only if the state ranks are usable. A negative value means the state is above national.

    `national` maps year to the national rate; by default it is pooled over every row of `table`."""
    out = table.copy()
    out["headroom"] = np.nan
    if not usable:
        return out
    if national is None:
        pooled = table.groupby("year")[["n_zilretta_providers", "n_visible_providers"]].sum()
        national = (pooled["n_zilretta_providers"] / pooled["n_visible_providers"]).to_dict()
    gap = out["year"].map(national) - out["adoption_rate"]
    shown = out["reported"] == 1
    out.loc[shown, "headroom"] = out.loc[shown, "n_visible_providers"] * gap[shown]
    return out


def ma_adjusted_sensitivity(table: pd.DataFrame, year: int, top_k: int = 5) -> dict:
    """Advantage-adjusted sensitivity (protocol E3). Fee-for-service claims see fewer patients in
    states with high Advantage enrolment, so adoption is regressed on the Advantage share across
    reported states and the residual (added back to the national rate) is the adjusted rate.
    Reports how much the headroom ranking moves."""
    part = table[(table["year"] == year) & (table["reported"] == 1)].dropna(
        subset=["ma_participation_rate", "headroom"]
    )
    x = part["ma_participation_rate"].to_numpy(float)
    y = part["adoption_rate"].to_numpy(float)
    slope, intercept = np.polyfit(x, y, 1)
    national = part["n_zilretta_providers"].sum() / part["n_visible_providers"].sum()
    adjusted_rate = national + (y - (intercept + slope * x))
    adjusted = pd.Series(
        part["n_visible_providers"].to_numpy(float) * (national - adjusted_rate),
        index=part["state_code"].to_numpy(),
    )
    original = pd.Series(part["headroom"].to_numpy(float), index=part["state_code"].to_numpy())
    return {
        "year": year,
        "n_states": len(part),
        "slope": float(slope),
        "rho": spearman(original, adjusted),
        "top_overlap": top_k_overlap(original, adjusted, top_k),
        "top_k": top_k,
        "corr_with_ma": spearman(part["adoption_rate"], part["ma_participation_rate"]),
    }


def ma_by_state_year(geovar: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    """Advantage participation by state and year, all ages, joined to states by FIPS code."""
    rows = geovar[(geovar["geo_level"] == "State") & (geovar["age_level"] == "All")]
    fips = states.set_index("state_fips")["state_code"]
    out = rows.assign(state_code=rows["geo_code"].map(fips)).dropna(subset=["state_code"])
    return out[["year", "state_code", "ma_participation_rate"]].reset_index(drop=True)


def arthritis_by_state(counties: pd.DataFrame) -> pd.DataFrame:
    """Population-weighted county arthritis prevalence per state; counties without a value or a
    population are skipped."""
    valid = counties.dropna(subset=["prevalence_pct", "total_population"])
    weighted = valid.assign(
        w=valid["total_population"], v=valid["prevalence_pct"] * valid["total_population"]
    )
    grouped = weighted.groupby("state_code")[["v", "w"]].sum()
    return (
        (grouped["v"] / grouped["w"])
        .rename("arthritis_prevalence_pct")
        .reset_index()[["state_code", "arthritis_prevalence_pct"]]
    )


def add_context(table: pd.DataFrame, ma: pd.DataFrame, arthritis: pd.DataFrame) -> pd.DataFrame:
    """Join Advantage share (by year and state) and arthritis prevalence (by state)."""
    base = table.drop(
        columns=["ma_participation_rate", "arthritis_prevalence_pct"], errors="ignore"
    )
    return base.merge(ma, on=["year", "state_code"], how="left").merge(
        arthritis, on="state_code", how="left"
    )
