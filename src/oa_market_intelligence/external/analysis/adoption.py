"""Provider adoption of Zilretta, the cluster bootstrap, E1 (specialty ranking) and the E2a rule.

Definitions (protocol section 2): a *visible provider* is an individual (entity type I) who billed
at least one primary-set code in the year; a *Zilretta provider* billed J3304. The adoption rate of
a group is Zilretta provider-years divided by visible provider-years. Medicare hides cells of ten
or fewer patients, so adoption means "among visible providers".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

from oa_market_intelligence.analysis.adoption import (
    adjusted_shares,
    build_design,
    fit_binomial,
    group_specialties,
)
from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.codes import CODE_GROUPS, ZILRETTA_CODE


def provider_years(frame: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """One row per visible provider and year: year, npi, specialty_cms, group, state_code, zilretta.

    `frame` has year, npi, hcpcs_code, specialty_cms, entity_type, state_code (one row per
    provider, code and setting). `group` is the approved IQVIA specialty group, or missing."""
    rows = frame[frame["entity_type"].eq("I") & frame["hcpcs_code"].isin(CODE_GROUPS["A_primary"])]
    rows = rows.assign(is_zilretta=rows["hcpcs_code"].eq(ZILRETTA_CODE))
    one = rows.groupby(["year", "npi"], as_index=False).agg(
        specialty_cms=("specialty_cms", "first"),
        state_code=("state_code", "first"),
        zilretta=("is_zilretta", "max"),
    )
    names = crosswalk[crosswalk["relationship"] != "excluded"].set_index("medicare_name")
    one["group"] = one["specialty_cms"].map(names["iqvia_group"])
    return one[["year", "npi", "specialty_cms", "group", "state_code", "zilretta"]]


def rates(py: pd.DataFrame, by: str) -> pd.DataFrame:
    """Provider-years, distinct providers, Zilretta provider-years and the rate, by `by`."""
    part = py.dropna(subset=[by])
    out = part.groupby(by).agg(
        n_provider_years=("npi", "size"),
        n_providers=("npi", "nunique"),
        n_zilretta=("zilretta", "sum"),
    )
    out["n_zilretta"] = out["n_zilretta"].astype(int)
    out["rate"] = out["n_zilretta"] / out["n_provider_years"]
    return out.reset_index()


def _clusters(py: pd.DataFrame, by: str, key) -> tuple[np.ndarray, np.ndarray]:
    """Per provider within one group: how many years they are visible and how many with Zilretta."""
    part = py[py[by] == key]
    grouped = part.groupby("npi").agg(years=("year", "size"), zil=("zilretta", "sum"))
    return grouped["years"].to_numpy(float), grouped["zil"].to_numpy(float)


def _draw_rates(
    years: np.ndarray, zil: np.ndarray, n_boot: int, rng, batch: int = 100
) -> np.ndarray:
    """Cluster bootstrap: resample providers with their years, return the pooled rate per draw."""
    m = len(years)
    probs = np.full(m, 1.0 / m)
    out = np.empty(n_boot)
    for start in range(0, n_boot, batch):
        size = min(batch, n_boot - start)
        counts = rng.multinomial(m, probs, size=size)
        out[start : start + size] = (counts @ zil) / (counts @ years)
    return out


def bootstrap_group_draws(
    py: pd.DataFrame, groups: list, *, n_boot: int, seed: int, by: str = "group"
) -> np.ndarray:
    """Bootstrap rates for every group: an (n_boot x n_groups) matrix, one stream per group."""
    streams = np.random.SeedSequence(seed).spawn(len(groups))
    columns = []
    for key, stream in zip(groups, streams, strict=True):
        years, zil = _clusters(py, by, key)
        columns.append(_draw_rates(years, zil, n_boot, np.random.default_rng(stream)))
    return np.column_stack(columns)


def cluster_bootstrap_ci(
    py: pd.DataFrame,
    by: str,
    *,
    n_boot: int = config.N_BOOT,
    seed: int = config.SEED,
    level: float = config.CI_LEVEL,
) -> pd.DataFrame:
    """`rates` with a percentile interval from resampling providers within each group."""
    table = rates(py, by)
    keys = list(table[by])
    draws = bootstrap_group_draws(py, keys, n_boot=n_boot, seed=seed, by=by)
    tail = (1 - level) / 2 * 100
    table["low"] = np.percentile(draws, tail, axis=0)
    table["high"] = np.percentile(draws, 100 - tail, axis=0)
    return table


def spearman(x, y) -> float:
    return float(spearmanr(x, y).statistic)


def rank_draws(draws: np.ndarray, fixed: np.ndarray) -> np.ndarray:
    """Spearman correlation of each row of `draws` with `fixed` (average ranks for ties)."""
    ranks = rankdata(draws, axis=1)
    centred = ranks - ranks.mean(axis=1, keepdims=True)
    target = rankdata(fixed)
    target = target - target.mean()
    denominator = np.sqrt((centred**2).sum(axis=1) * (target**2).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        return (centred @ target) / denominator


def _top(series: pd.Series, k: int) -> list:
    return list(series.sort_index().sort_values(ascending=False, kind="stable").index[:k])


def top_k_overlap(a: pd.Series, b: pd.Series, k: int) -> int:
    return len(set(_top(a, k)) & set(_top(b, k)))


def e1_verdict(rho: float, overlap: int) -> str:
    """agrees if the correlation and the top-3 overlap both pass, partial if one does."""
    if rho is None or np.isnan(rho):
        raise ValueError("the rank correlation is undefined, so there is no verdict")
    passed = int(rho >= config.E1_RHO_MIN) + int(overlap >= config.E1_TOP_MIN)
    return {2: "agrees", 1: "partial", 0: "disagrees"}[passed]


def e2a_verdict(rate_by_year: dict[int, float]) -> tuple[str, int]:
    """consistent if the peak year is 2021 or 2022 and the last year is below the peak."""
    peak = max(rate_by_year, key=lambda y: rate_by_year[y])
    last = rate_by_year[config.E2A_LAST_YEAR]
    ok = peak in config.E2A_PEAK_YEARS and last < rate_by_year[peak]
    return ("consistent" if ok else "inconsistent"), int(peak)


def e1_result(
    py: pd.DataFrame,
    iqvia: pd.Series,
    *,
    n_boot: int = config.N_BOOT,
    seed: int = config.SEED,
    drop: list[str] | None = None,
) -> dict:
    """E1: rank agreement between Medicare adoption (pooled provider-years in `py`) and IQVIA's
    adjusted shares by specialty group. The interval resamples Medicare providers; IQVIA's
    values are held fixed (protocol)."""
    groups = sorted(set(py["group"].dropna()) & set(iqvia.index) - set(drop or []))
    table = rates(py[py["group"].isin(groups)], "group").set_index("group").reindex(groups)
    medicare = table["rate"]
    iq = iqvia.reindex(groups)
    rho = spearman(medicare, iq)
    draws = bootstrap_group_draws(py, groups, n_boot=n_boot, seed=seed)
    rho_draws = rank_draws(draws, iq.to_numpy())
    tail = (1 - config.CI_LEVEL) / 2 * 100
    overlap = top_k_overlap(medicare, iq, config.E1_TOP_K)
    return {
        "rho": rho,
        "rho_low": float(np.nanpercentile(rho_draws, tail)),
        "rho_high": float(np.nanpercentile(rho_draws, 100 - tail)),
        "overlap": overlap,
        "top3_medicare": _top(medicare, config.E1_TOP_K),
        "top3_iqvia": _top(iq, config.E1_TOP_K),
        "verdict": e1_verdict(rho, overlap),
        "n_groups": len(groups),
        "groups": groups,
    }


def iqvia_adjusted_specialty_shares(
    df: pd.DataFrame, *, first_month: int, last_month: int, ages: tuple[str, ...] | None = None
) -> pd.Series:
    """IQVIA's adjusted Zilretta share by specialty group over a month window, with the model of
    notebook 07. The RARE grouping is decided on all the data (as in the notebook), then the
    window and age bands are applied."""
    grouped = group_specialties(df)
    window = grouped[(grouped["month_id"] >= first_month) & (grouped["month_id"] <= last_month)]
    if ages is not None:
        window = window[window["age_band"].isin(ages)]
    window = window.reset_index(drop=True)
    design = build_design(window)
    return adjusted_shares(design, window, fit_binomial(design, window))
