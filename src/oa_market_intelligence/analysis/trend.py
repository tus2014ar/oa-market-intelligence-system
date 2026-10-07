"""Q1: how has visit share shifted, and where does the trend bend?

A piecewise-linear fit with up to two breakpoints (each segment has its own intercept and
slope, at least `min_segment` months long). The number of breaks is chosen by sequential
tests calibrated by a block bootstrap (BIC was tried first and found far too liberal: with an
unknown break month it reports breaks that are not there), and the break months get
intervals from a second block bootstrap of the residuals. Breaks are compared
with a short list of events that was fixed before the analysis was run.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.features.monthly import COVID_SHOCK_MONTHS, DIP_2024_MONTHS

# Fixed before the analysis. FDA approval dates were looked up on 7 Oct 2026 for every branded
# product in the share formula (data/reference/openfda_competitive_set_approvals_2026-10-07.csv):
# none has an original approval inside the data window (Aug 2019 to Jul 2025), so there is no
# approval event to add. The unbranded generics (39% of visits) cannot be looked up by name.
EVENTS = {
    "COVID shock": COVID_SHOCK_MONTHS,
    "Mar to Jul 2024 volume dip": DIP_2024_MONTHS,
}
CATEGORIES = ["branded_injectable", "generic_corticosteroid", "nsaid_otc"]
DEFAULT_MIN_SEGMENT = 12
DEFAULT_MAX_BREAKS = 2
DEFAULT_BLOCK_LENGTH = 6
_FLOOR = 1e-12


@dataclass(frozen=True, eq=False)
class BreakFit:
    breaks: tuple[int, ...]  # index of the first observation of each new segment
    sse: float
    bic: float
    fitted: np.ndarray
    p_values: tuple[float, ...] = ()

    @property
    def n_breaks(self) -> int:
        return len(self.breaks)


def _cost_matrix(y: np.ndarray, min_segment: int) -> np.ndarray:
    """cost[i, j] = squared error of the best straight line through y[i:j] (inf if too short)."""
    n = len(y)
    x = np.arange(n) - (n - 1) / 2  # centred, for numerical stability
    cum = {
        name: np.concatenate([[0.0], np.cumsum(values)])
        for name, values in {"x": x, "y": y, "xx": x * x, "xy": x * y, "yy": y * y}.items()
    }
    idx = np.arange(n + 1)
    i, j = np.meshgrid(idx, idx, indexing="ij")
    count = (j - i).astype(float)
    valid = count >= min_segment
    safe = np.where(valid, count, 1.0)

    def window(name):
        return cum[name][j] - cum[name][i]

    sx, sy = window("x"), window("y")
    sxx_c = window("xx") - sx * sx / safe
    sxy_c = window("xy") - sx * sy / safe
    syy_c = window("yy") - sy * sy / safe
    with np.errstate(divide="ignore", invalid="ignore"):
        sse = syy_c - np.where(sxx_c > 0, sxy_c * sxy_c / sxx_c, 0.0)
    return np.where(valid, np.maximum(sse, 0.0), np.inf)


def _segment_fit(y: np.ndarray, edges: list[int]) -> np.ndarray:
    fitted = np.empty(len(y))
    for start, stop in zip(edges, edges[1:]):
        x = np.arange(start, stop)
        slope, intercept = np.polyfit(x, y[start:stop], 1)
        fitted[start:stop] = intercept + slope * x
    return fitted


def _best_breaks(cost: np.ndarray, n_breaks: int, n: int) -> tuple[tuple[int, ...], float]:
    """Dynamic programme over break positions: lowest total error with `n_breaks` breaks."""
    best = cost[0, :].copy()  # one segment ending at j
    back: list[np.ndarray] = []
    for _ in range(n_breaks):
        total = best[:, None] + cost  # total[i, j]: best up to i, then a segment i..j
        choice = np.argmin(total, axis=0)
        best = total[choice, np.arange(total.shape[1])]
        back.append(choice)
    breaks: list[int] = []
    j = n
    for choice in reversed(back):
        j = int(choice[j])
        breaks.append(j)
    return tuple(sorted(breaks)), float(best[n])


def _bic(sse: float, n: int, n_breaks: int) -> float:
    parameters = 2 * (n_breaks + 1) + n_breaks  # intercept and slope per segment, break months
    return n * np.log(max(sse / n, _FLOOR)) + parameters * np.log(n)


def fit_breaks(y, *, n_breaks: int, min_segment: int = DEFAULT_MIN_SEGMENT) -> BreakFit:
    """The best piecewise-linear fit with exactly `n_breaks` breaks."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < (n_breaks + 1) * min_segment:
        raise ValueError("series too short for that many segments")
    cost = _cost_matrix(y, min_segment)
    breaks, sse = _best_breaks(cost, n_breaks, n)
    fitted = _segment_fit(y, [0, *breaks, n])
    return BreakFit(breaks=breaks, sse=sse, bic=_bic(sse, n, n_breaks), fitted=fitted)


def _resample_residuals(residuals: np.ndarray, rng, block_length: int) -> np.ndarray:
    """Moving-block resample, which keeps the month-to-month dependence of the residuals."""
    n = len(residuals)
    blocks: list[np.ndarray] = []
    while sum(len(block) for block in blocks) < n:
        start = int(rng.integers(0, n - block_length + 1))
        blocks.append(residuals[start : start + block_length])
    return np.concatenate(blocks)[:n]


def _improvement(sse_smaller: float, sse_larger: float, n: int) -> float:
    return n * float(np.log(max(sse_smaller, _FLOOR) / max(sse_larger, _FLOOR)))


def break_tests(
    y,
    *,
    max_breaks: int = DEFAULT_MAX_BREAKS,
    min_segment: int = DEFAULT_MIN_SEGMENT,
    n_null: int = 500,
    block_length: int = DEFAULT_BLOCK_LENGTH,
    seed: int = 0,
    alpha: float = 0.05,
) -> list[dict]:
    """Sequential tests: is one more break needed?

    Test k compares the best fit with k breaks against the best with k-1. Its p-value is the
    share of simulated series, built from the k-1 fit plus block-resampled residuals (so
    there is no break beyond k-1 by construction), whose improvement is at least as large as
    the observed one. Searching over every possible break month makes the raw improvement
    look better than it is; simulating the same search under "no extra break" corrects that.
    Testing stops at the first test that is not significant."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    results: list[dict] = []
    smaller = fit_breaks(y, n_breaks=0, min_segment=min_segment)
    for k in range(1, max_breaks + 1):
        if n < (k + 1) * min_segment:
            break
        larger = fit_breaks(y, n_breaks=k, min_segment=min_segment)
        observed = _improvement(smaller.sse, larger.sse, n)
        residuals = y - smaller.fitted
        rng = np.random.default_rng([seed, k])
        at_least = 0
        for _ in range(n_null):
            draw = smaller.fitted + _resample_residuals(residuals, rng, block_length)
            cost = _cost_matrix(draw, min_segment)
            _, sse_small = _best_breaks(cost, k - 1, n)
            _, sse_large = _best_breaks(cost, k, n)
            at_least += _improvement(sse_small, sse_large, n) >= observed
        p_value = (1 + at_least) / (1 + n_null)
        results.append({"breaks": k, "improvement": observed, "p_value": p_value})
        if p_value >= alpha:
            break
        smaller = larger
    return results


def select_breaks(
    y,
    *,
    max_breaks: int = DEFAULT_MAX_BREAKS,
    min_segment: int = DEFAULT_MIN_SEGMENT,
    n_null: int = 500,
    block_length: int = DEFAULT_BLOCK_LENGTH,
    seed: int = 0,
    alpha: float = 0.05,
) -> BreakFit:
    """The fit with as many breaks as the sequential tests support (possibly none)."""
    tests = break_tests(
        y,
        max_breaks=max_breaks,
        min_segment=min_segment,
        n_null=n_null,
        block_length=block_length,
        seed=seed,
        alpha=alpha,
    )
    chosen = sum(1 for test in tests if test["p_value"] < alpha)
    fit = fit_breaks(y, n_breaks=chosen, min_segment=min_segment)
    return BreakFit(
        breaks=fit.breaks,
        sse=fit.sse,
        bic=fit.bic,
        fitted=fit.fitted,
        p_values=tuple(test["p_value"] for test in tests),
    )


def bootstrap_break_locations(
    y,
    n_breaks: int,
    *,
    min_segment: int = DEFAULT_MIN_SEGMENT,
    n_boot: int = 1000,
    block_length: int = DEFAULT_BLOCK_LENGTH,
    seed: int = 0,
    level: float = 0.90,
) -> list[tuple[int, int]]:
    """A `level` interval for each break position, holding the number of breaks fixed.

    Residuals of the fit are resampled in blocks, added back to the fitted line, and the break
    search is repeated. Returns [] when there are no breaks."""
    if n_breaks == 0:
        return []
    y = np.asarray(y, dtype=float)
    base = fit_breaks(y, n_breaks=n_breaks, min_segment=min_segment)
    residuals = y - base.fitted
    rng = np.random.default_rng(seed)
    n = len(y)
    positions = []
    for _ in range(n_boot):
        draw = base.fitted + _resample_residuals(residuals, rng, block_length)
        breaks, _ = _best_breaks(_cost_matrix(draw, min_segment), n_breaks, n)
        positions.append(breaks)
    positions = np.array(positions)
    tail = (1 - level) / 2 * 100
    return [
        tuple(int(round(v)) for v in np.percentile(positions[:, c], [tail, 100 - tail]))
        for c in range(n_breaks)
    ]


def _month_number(month_id: int) -> int:
    return (month_id // 100) * 12 + month_id % 100


def events_overlapping(interval: tuple[int, int]) -> list[str]:
    """Names of the pre-specified events whose month window overlaps an interval of month ids."""
    low, high = (_month_number(m) for m in interval)
    return [
        name
        for name, (start, end) in EVENTS.items()
        if low <= _month_number(end) and _month_number(start) <= high
    ]


def category_shares(engine: Engine) -> pd.DataFrame:
    """Monthly share of the three-category total for each treatment category."""
    with engine.connect() as conn:
        counts = pd.read_sql(
            "SELECT month_id, branded_injectable_visits AS branded_injectable, "
            "generic_corticosteroid_visits AS generic_corticosteroid, "
            "nsaid_otc_visits AS nsaid_otc FROM gold_visit_share_monthly ORDER BY month_id",
            conn,
        )
    total = counts[CATEGORIES].sum(axis=1)
    shares = counts.copy()
    shares[CATEGORIES] = counts[CATEGORIES].div(total, axis=0)
    shares.insert(1, "month", pd.to_datetime(shares["month_id"].astype(str), format="%Y%m"))
    return shares
