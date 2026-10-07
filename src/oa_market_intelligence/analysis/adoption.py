"""Q3: does Zilretta's adoption vary by specialty, and is that pattern stable?

A binomial model on the counts, Zilretta visits out of category visits for every
segment-month, with a fixed effect for each month (which absorbs the market-wide level) plus
specialty, age band and gender. Small segments carry less weight instead of being dropped.

The headline numbers are **adjusted shares**: Zilretta's share if every visit in the data had
been in a given specialty, keeping each visit's month, age band and gender (marginal
standardisation). Intervals come from two bootstraps, one that resamples months and one that
resamples whole segments, and the wider of the two is reported, because visits are not
independent. The specialty term is tested with a quasi-likelihood F test, because the data is
more variable than a plain binomial allows.

The model is fitted with a small sparse solver (iteratively reweighted least squares) because
the bootstraps refit it thousands of times; the tests check it against statsmodels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import f as f_dist
from scipy.stats import spearmanr
from sqlalchemy import Engine

RARE_LABEL = "RARE (grouped)"
SPECIALTY_THRESHOLD = 0.005  # keeps Pain Medicine and Anesthesiology, which the questions name
TERMS = ("specialty", "age_band", "gender")
_FACTORS = {"specialty": "specialty", "age_band": "age_band", "gender": "gender"}
_MAX_COEFFICIENT = 25.0  # a level with no Zilretta visits drives its coefficient to minus infinity


def load_adoption_data(engine: Engine) -> pd.DataFrame:
    """Zilretta and category visits per month, specialty, age band and gender."""
    query = (
        "SELECT g.month_id, s.specialty_name AS specialty, d.age_band, d.gender, "
        "SUM(g.branded_injectable_visits) AS zilretta, "
        "SUM(g.total_category_visits) AS category "
        "FROM gold_segment_adoption g "
        "JOIN dim_specialty s ON s.specialty_id = g.specialty_id "
        "JOIN dim_demographics d ON d.demographic_id = g.demographic_id "
        "GROUP BY g.month_id, s.specialty_name, d.age_band, d.gender "
        "ORDER BY g.month_id, s.specialty_name, d.age_band, d.gender"
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def group_specialties(df: pd.DataFrame, threshold: float = SPECIALTY_THRESHOLD) -> pd.DataFrame:
    """Specialties under `threshold` of all category visits become one RARE group."""
    share = df.groupby("specialty")["category"].sum() / df["category"].sum()
    common = share[share >= threshold].index
    out = df.copy()
    out["specialty"] = out["specialty"].where(out["specialty"].isin(common), RARE_LABEL)
    return out


@dataclass(frozen=True)
class Design:
    X: sparse.csr_matrix
    columns: list[str]
    blocks: dict[str, list[int]]  # term -> column indices (month included)
    specialty_column: dict[str, int | None]  # specialty -> its column (None for the reference)
    reference: dict[str, str]


@dataclass(frozen=True)
class Fit:
    beta: np.ndarray
    deviance: float
    pearson_chi2: float
    df_resid: int
    converged: bool
    iterations: int


def build_design(df: pd.DataFrame, terms: tuple[str, ...] = TERMS) -> Design:
    """One column per month, then (levels - 1) columns per term; the reference level of each
    term is its largest by category visits."""
    n = len(df)
    columns: list[str] = []
    blocks: dict[str, list[int]] = {"month": []}
    reference: dict[str, str] = {}
    specialty_column: dict[str, int | None] = {}
    rows, cols = [], []

    def add(name: str, mask: np.ndarray, term: str) -> int:
        index = len(columns)
        columns.append(name)
        blocks.setdefault(term, []).append(index)
        hit = np.flatnonzero(mask)
        rows.append(hit)
        cols.append(np.full(len(hit), index))
        return index

    for month in sorted(df["month_id"].unique()):
        add(f"month={month}", (df["month_id"] == month).to_numpy(), "month")
    for term in terms:
        volume = df.groupby(_FACTORS[term])["category"].sum().sort_values(ascending=False)
        reference[term] = volume.index[0]
        for level in sorted(volume.index):
            if level == reference[term]:
                if term == "specialty":
                    specialty_column[level] = None
                continue
            index = add(f"{term}={level}", (df[_FACTORS[term]] == level).to_numpy(), term)
            if term == "specialty":
                specialty_column[level] = index
    row_index = np.concatenate(rows) if rows else np.array([], dtype=int)
    col_index = np.concatenate(cols) if cols else np.array([], dtype=int)
    X = sparse.csr_matrix(
        (np.ones(len(row_index)), (row_index, col_index)), shape=(n, len(columns))
    )
    return Design(X=X, columns=columns, blocks=blocks, specialty_column=specialty_column,
                  reference=reference)


def _deviance_and_pearson(z, t, mu, w) -> tuple[float, float]:
    with np.errstate(divide="ignore", invalid="ignore"):
        term1 = np.where(z > 0, z * np.log(z / (t * mu)), 0.0)
        term2 = np.where(t - z > 0, (t - z) * np.log((t - z) / (t * (1 - mu))), 0.0)
        pearson = (z - t * mu) ** 2 / (t * mu * (1 - mu))
    return float(2 * np.sum(w * (term1 + term2))), float(np.sum(w * pearson))


def fit_binomial(
    design: Design,
    df: pd.DataFrame,
    *,
    weights: np.ndarray | None = None,
    start: np.ndarray | None = None,
    max_iter: int = 100,
    tol: float = 1e-11,
) -> Fit:
    """Maximum-likelihood fit by iteratively reweighted least squares. `weights` are row
    multiplicities (for the bootstraps); columns with no weight are dropped from the fit."""
    z = df["zilretta"].to_numpy(float)
    t = df["category"].to_numpy(float)
    w = np.ones(len(df)) if weights is None else np.asarray(weights, float)
    X = design.X
    active = np.flatnonzero(np.asarray(X.T @ (w * t)).ravel() > 0)
    Xa = X[:, active]
    beta_a = np.zeros(len(active)) if start is None else np.asarray(start)[active].copy()
    mu = np.clip((z + 0.5) / (t + 1.0), 1e-9, 1 - 1e-9) if start is None else None
    eta = np.log(mu / (1 - mu)) if mu is not None else Xa @ beta_a
    previous = np.inf
    converged = False
    iterations = 0
    for iterations in range(1, max_iter + 1):
        mu = np.clip(1 / (1 + np.exp(-eta)), 1e-12, 1 - 1e-12)
        variance = mu * (1 - mu)
        weight = w * t * variance
        working = eta + (z / t - mu) / variance
        gram = (Xa.T @ sparse.diags(weight) @ Xa).toarray()
        rhs = np.asarray(Xa.T @ (weight * working)).ravel()
        beta_a = np.linalg.solve(gram + 1e-10 * np.eye(len(active)), rhs)
        eta = Xa @ beta_a
        mu = np.clip(1 / (1 + np.exp(-eta)), 1e-12, 1 - 1e-12)
        deviance, _ = _deviance_and_pearson(z, t, mu, w)
        if np.max(np.abs(beta_a)) > _MAX_COEFFICIENT:
            break  # a level with no events: report as not converged
        if abs(previous - deviance) <= tol * (abs(deviance) + 0.1):
            converged = True
            break
        previous = deviance
    beta = np.zeros(X.shape[1])
    beta[active] = beta_a
    deviance, pearson = _deviance_and_pearson(z, t, mu, w)
    return Fit(
        beta=beta,
        deviance=deviance,
        pearson_chi2=pearson,
        df_resid=int(np.sum(w > 0)) - len(active),
        converged=converged,
        iterations=iterations,
    )


def adjusted_shares(
    design: Design, df: pd.DataFrame, fit: Fit, weights: np.ndarray | None = None
) -> pd.Series:
    """Zilretta's share if every (weighted) visit had been in each specialty in turn."""
    w = np.ones(len(df)) if weights is None else np.asarray(weights, float)
    t = df["category"].to_numpy(float)
    spec_columns = [c for c in design.specialty_column.values() if c is not None]
    base_beta = fit.beta.copy()
    base_beta[spec_columns] = 0.0
    base_eta = design.X @ base_beta
    out = {}
    for specialty, column in design.specialty_column.items():
        shift = 0.0 if column is None else fit.beta[column]
        p = 1 / (1 + np.exp(-(base_eta + shift)))
        out[specialty] = float(np.sum(w * t * p) / np.sum(w * t))
    return pd.Series(out)


def _segment_ids(df: pd.DataFrame) -> pd.Series:
    return df["specialty"] + "|" + df["age_band"] + "|" + df["gender"]


def bootstrap_adjusted_shares(
    df: pd.DataFrame,
    *,
    by: str = "month",
    n_boot: int = 1000,
    seed: int = 0,
    level: float = 0.90,
) -> dict:
    """Intervals for the adjusted share of each specialty from refitting the model on
    resampled months (`by='month'`) or resampled whole segments (`by='segment'`)."""
    if by not in ("month", "segment"):
        raise ValueError("by must be 'month' or 'segment'")
    design = build_design(df)
    estimate_fit = fit_binomial(design, df)
    estimate = adjusted_shares(design, df, estimate_fit)
    keys = df["month_id"] if by == "month" else _segment_ids(df)
    codes, unique = pd.factorize(keys)
    rng = np.random.default_rng(seed)
    draws, failed = [], 0
    for _ in range(n_boot):
        counts = np.bincount(rng.integers(0, len(unique), len(unique)), minlength=len(unique))
        weights = counts[codes].astype(float)
        fit = fit_binomial(design, df, weights=weights, start=estimate_fit.beta)
        if not fit.converged:
            failed += 1
            continue
        draws.append(adjusted_shares(design, df, fit, weights).reindex(estimate.index).to_numpy())
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(np.array(draws), [tail, 100 - tail], axis=0)
    table = pd.DataFrame({"estimate": estimate.to_numpy(), "low": low, "high": high},
                         index=estimate.index)
    return {"table": table, "n_failed": failed, "n_boot": n_boot}


def combine_intervals(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """The wider of two interval tables for every specialty."""
    b = b.reindex(a.index)
    return pd.DataFrame(
        {
            "estimate": a["estimate"],
            "low": np.minimum(a["low"], b["low"]),
            "high": np.maximum(a["high"], b["high"]),
        }
    )


def deviance_shares(df: pd.DataFrame) -> dict:
    """How much of the explainable variation each term carries, and a test for specialty.

    Each term's share is the rise in deviance when that term alone is dropped from the full
    model, as a fraction of the gap between the month-only and the full model (so the three
    need not sum to one when the terms overlap). The specialty test is a quasi-likelihood F
    test, scaled by the full model's dispersion because the data varies more than binomial."""
    full_design = build_design(df)
    full = fit_binomial(full_design, df)
    month_only = fit_binomial(build_design(df, terms=()), df)
    gap = month_only.deviance - full.deviance
    dispersion = full.pearson_chi2 / full.df_resid
    out: dict = {
        "deviance_full": full.deviance,
        "deviance_month_only": month_only.deviance,
        "dispersion": dispersion,
    }
    for term in TERMS:
        reduced_terms = tuple(t for t in TERMS if t != term)
        reduced = fit_binomial(build_design(df, terms=reduced_terms), df)
        delta = reduced.deviance - full.deviance
        degrees = len(full_design.blocks[term])
        out[term] = {"delta_deviance": delta, "df": degrees, "share": delta / gap}
    spec = out["specialty"]
    f_stat = (spec["delta_deviance"] / spec["df"]) / dispersion
    out["specialty_F"] = f_stat
    out["specialty_p"] = float(f_dist.sf(f_stat, spec["df"], full.df_resid))
    return out


def split_half_stability(df: pd.DataFrame, *, n_boot: int = 200, seed: int = 0) -> dict:
    """Is the specialty pattern the same in the first and second half of the months?

    Fits the model separately to each half and compares the adjusted shares of the specialties
    present in both (the RARE group is left out): rank correlation, correlation on the logit
    scale, and the fraction of specialties on the same side of their half's overall share.
    The interval for the rank correlation resamples months within each half."""
    months = sorted(df["month_id"].unique())
    cut = len(months) // 2
    halves = [months[:cut], months[cut:]]

    def compare(frames):
        shares = [_fit_half(frame) for frame in frames]
        common = [s for s in shares[0].index if s in shares[1].index and s != RARE_LABEL]
        a, b = shares[0][common], shares[1][common]
        rho = float(spearmanr(a, b)[0])
        pearson = float(np.corrcoef(np.log(a / (1 - a)), np.log(b / (1 - b)))[0, 1])
        overall = [frame["zilretta"].sum() / frame["category"].sum() for frame in frames]
        same_side = float(np.mean((a.to_numpy() > overall[0]) == (b.to_numpy() > overall[1])))
        return rho, pearson, same_side, pd.DataFrame({"first_half": a, "second_half": b})

    frames = [df[df["month_id"].isin(h)].reset_index(drop=True) for h in halves]
    rho, pearson, same_side, table = compare(frames)

    rng = np.random.default_rng(seed)
    rhos = []
    for _ in range(n_boot):
        resampled = []
        for h in halves:
            drawn = rng.choice(h, size=len(h), replace=True)
            counts = pd.Series(drawn).value_counts()
            parts = [df[df["month_id"] == m].assign(month_id=f"{m}-{k}")
                     for m, c in counts.items() for k in range(c)]
            resampled.append(pd.concat(parts, ignore_index=True))
        try:
            rhos.append(compare(resampled)[0])
        except (np.linalg.LinAlgError, ValueError):
            continue
    low, high = np.percentile([r for r in rhos if not np.isnan(r)], [5, 95])
    return {
        "spearman": rho,
        "spearman_low": float(low),
        "spearman_high": float(high),
        "pearson_logit": pearson,
        "same_side_fraction": same_side,
        "table": table,
    }


def _fit_half(frame: pd.DataFrame) -> pd.Series:
    design = build_design(frame)
    return adjusted_shares(design, frame, fit_binomial(design, frame))
