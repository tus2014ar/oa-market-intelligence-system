"""How big an accuracy gain could a small test set detect? (Q2: "no signal" or "too little data")

The comparison of two classifiers on the same months is McNemar's exact test on the months where
exactly one of them is right. To ask what a test of `n` months could detect, simulate paired
outcomes for a reference classifier (persistence) and a hypothetical better one whose accuracy is
higher by `delta`, with the rate at which the two disagree in correctness (`discordance`) taken
from real data, and count how often the exact test comes out significant.

With P(only B right) = (discordance + delta) / 2 and P(only A right) = (discordance - delta) / 2,
B's accuracy exceeds A's by exactly `delta`. A gain larger than the discordance is impossible.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import binom


def mcnemar_power(
    *,
    n: int,
    accuracy: float,
    discordance: float,
    deltas,
    n_sim: int = 5000,
    seed: int = 0,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """Power of the exact McNemar test for each accuracy gain in `deltas`.

    A row is `feasible` when the implied probabilities are all non-negative; infeasible gains
    get NaN power. Power counts significant results in which the better classifier wins."""
    rng = np.random.default_rng(seed)
    rows = []
    for delta in deltas:
        only_b = (discordance + delta) / 2
        only_a = (discordance - delta) / 2
        both_right = accuracy - only_a
        both_wrong = 1 - accuracy - only_b
        probabilities = np.array([both_right, only_a, only_b, both_wrong])
        feasible = bool((probabilities >= -1e-12).all() and delta <= discordance + 1e-12)
        if not feasible:
            rows.append({"delta": float(delta), "power": float("nan"), "feasible": False})
            continue
        counts = rng.multinomial(n, np.clip(probabilities, 0, None) / probabilities.clip(0).sum(),
                                 size=n_sim)
        a_only, b_only = counts[:, 1], counts[:, 2]
        discordant = a_only + b_only
        smaller = np.minimum(a_only, b_only)
        with np.errstate(invalid="ignore"):
            p_value = np.where(discordant > 0,
                               np.minimum(1.0, 2 * binom.cdf(smaller, discordant, 0.5)), 1.0)
        significant = (p_value < alpha) & (b_only > a_only)
        rows.append({"delta": float(delta), "power": float(significant.mean()), "feasible": True})
    return pd.DataFrame(rows)


def smallest_detectable_gain(table: pd.DataFrame, *, power: float = 0.8) -> float | None:
    """The first (smallest) feasible gain whose power reaches `power`, or None."""
    reached = table[table["feasible"] & (table["power"] >= power)]
    return float(reached["delta"].iloc[0]) if len(reached) else None
