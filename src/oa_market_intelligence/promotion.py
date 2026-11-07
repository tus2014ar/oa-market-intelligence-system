"""The promotion gate (DL-126): may the refitted models replace the ones in service?

Every monthly run refits the adopted models. Before the new warehouse is swapped in, the refit (the
**challenger**, in the staged database) is compared with the models now in service (the
**champion**, in the published database). A failed check stops the run before the swap: nothing new
is published and the champion keeps serving, unless a person runs it again with a written reason
(`oa-publish --accept-challenger "reason"`), which is recorded.

What the gate can and cannot do. It guards against a change in *behaviour*: broken output, a code or
library change that alters results without the data changing, a forecast that jumps by more than
the models have ever moved between months. It does not decide which model is *better*: that
needs outcomes for new months and is the pre-registered modelling test (DL-79), which stays a human
step. Realized skill is reported by the monitoring stage.

The checks, and where their tolerances come from (measured on the real data, 7 Nov 2026):

1. **Sanity.** Every forecast is finite and between 0 and 1, the keys are unique, the models of
   the adoption map are present, each saved model matches its recorded SHA-256 and has finite
   coefficients.
2. **Reproducibility** (the challenger starts from the same month as the champion, i.e. the data
   did not change). The forecasts must be the same to 1e-6 percentage points (two builds on
   different machines differed by 3e-11), the coefficients to 1e-9 (they differed by 7e-13), the
   alert flags identical, and the model fingerprints identical. Anything more means the code or the
   libraries changed what the models do.
3. **Stability** (the challenger starts from a later month). For the fitted 1- and 3-month models:
   the volume-weighted mean change in forecast at most 0.6 points (over 18 months of real refits
   the largest was 0.29), no segment of 100 or more visits moving more than 5 points (the largest
   was 2.5), and at least 90% of the champion's segments kept (the lowest was 94%). The 6-month
   model is a rule that simply follows last month's value, so it is exempt.

Nothing here talks to a network. It reads two SQLite files and writes one table, `promotion_log`
(deterministic: no timestamps), to the staged database.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

LOG_TABLE = "promotion_log"
KEY = ["horizon", "specialty_name", "age_band", "gender"]

# ---- tolerances (see the module note for the evidence behind each)
REPRO_FORECAST_PP = 1e-6
REPRO_COEFFICIENT = 1e-9
STABLE_WEIGHTED_MEAN_PP = 0.6
STABLE_BIG_SEGMENT_PP = 5.0
STABLE_KEPT_FRACTION = 0.90
BIG_SEGMENT_VISITS = 100

LOG_COLUMNS = [
    "decision", "scope", "check", "horizon", "value", "limit", "passed", "detail",
    "champion_origin", "challenger_origin", "champion_data_version", "challenger_data_version",
    "override_reason",
]  # fmt: skip


class PromotionHeld(RuntimeError):
    """The gate refused the challenger. The message names every failed check."""


@dataclass(frozen=True)
class Check:
    scope: str
    check: str
    passed: bool
    value: float | None = None
    limit: float | None = None
    horizon: int | None = None
    detail: str = ""


@dataclass
class Side:
    """What the gate reads from one warehouse."""

    pred: pd.DataFrame
    artifacts: pd.DataFrame
    origin: int
    data_version: str


@dataclass
class Decision:
    status: str  # 'no_champion', 'promote', 'hold', 'override'
    checks: list[Check] = field(default_factory=list)
    champion_origin: int | None = None
    challenger_origin: int | None = None
    champion_data_version: str | None = None
    challenger_data_version: str | None = None
    override_reason: str | None = None

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


# --------------------------------------------------------------------------- reading


def _connect(path: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def read_side(path: Path | str) -> Side | None:
    """The forecasts and saved models of a warehouse, or None if it has none (an older file)."""
    con = _connect(path)
    try:
        if not {"pred_batch", "model_artifacts"} <= _tables(con):
            return None
        pred = pd.read_sql("SELECT * FROM pred_batch WHERE level = 'segment'", con)
        artifacts = pd.read_sql("SELECT * FROM model_artifacts", con)
        versions = [r[0] for r in con.execute("SELECT DISTINCT data_version FROM pred_batch")]
        month = con.execute("SELECT MAX(run_month_id) FROM pred_batch").fetchone()[0]
    finally:
        con.close()
    if pred.empty or artifacts.empty or month is None:
        return None
    version = str(versions[0]) if len(versions) == 1 else "mixed"
    return Side(pred, artifacts, int(month), version)


# --------------------------------------------------------------------------- the checks


def _finite(series: pd.Series) -> bool:
    return bool(np.isfinite(pd.to_numeric(series, errors="coerce")).all())


def sanity_checks(side: Side, expected_ids: set[str] | None = None) -> list[Check]:
    pred, art = side.pred, side.artifacts
    checks = []
    share = pd.to_numeric(pred["point_share"], errors="coerce")
    ok = _finite(pred["point_share"]) and bool(((share >= 0) & (share <= 1)).all())
    checks.append(
        Check("sanity", "forecasts_finite_and_between_0_and_1", ok, detail=f"{len(pred)} rows")
    )
    score = pd.to_numeric(pred["alert_score"], errors="coerce").dropna()
    checks.append(
        Check("sanity", "alert_scores_between_0_and_1", bool(((score >= 0) & (score <= 1)).all()),
              detail=f"{len(score)} scores")
    )  # fmt: skip
    dup = int(pred.duplicated(KEY).sum())
    checks.append(Check("sanity", "no_duplicate_segment_keys", dup == 0, float(dup), 0.0))
    ids = set(art["model_id"])
    missing = sorted((expected_ids or set()) - ids)
    checks.append(
        Check("sanity", "models_of_the_adoption_map_present", not missing,
              detail="missing: " + ", ".join(missing) if missing else f"{len(ids)} models")
    )  # fmt: skip
    bad_sha, bad_coef = [], []
    for row in art.to_dict("records"):
        text = str(row["parameters"])
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != row["artifact_sha256"]:
            bad_sha.append(row["model_id"])
        try:
            params = json.loads(text) if text else {}
        except ValueError:
            bad_sha.append(row["model_id"])
            continue
        values = [*params.get("coef", []), params.get("intercept", 0.0)]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            bad_coef.append(row["model_id"])
    checks.append(
        Check("sanity", "saved_numbers_match_their_checksum", not bad_sha,
              detail="mismatch: " + ", ".join(sorted(set(bad_sha))) if bad_sha else "")
    )  # fmt: skip
    checks.append(
        Check("sanity", "coefficients_finite", not bad_coef,
              detail="not finite: " + ", ".join(bad_coef) if bad_coef else "")
    )  # fmt: skip
    return checks


def _fitted_horizons(art: pd.DataFrame) -> list[int]:
    """The segment horizons served by a fitted (logistic) model; a rule is not refit."""
    rows = art[(art["task"] == "segment") & (art["model"] == "logistic")]
    return sorted(int(h) for h in rows["horizon"])


def _coef(art: pd.DataFrame, model_id: str):
    row = art[art["model_id"] == model_id]
    if row.empty or not row["parameters"].iloc[0]:
        return None
    params = json.loads(row["parameters"].iloc[0])
    if "coef" not in params:
        return None
    return np.array([*params["coef"], params["intercept"]], dtype=float)


def reproducibility_checks(champion: Side, challenger: Side) -> list[Check]:
    """Same month, so the same data: the refit must be the same models."""
    checks = []
    merged = champion.pred.merge(
        challenger.pred, on=KEY, how="outer", suffixes=("_c", "_n"), indicator=True
    )
    only = int((merged["_merge"] != "both").sum())
    checks.append(
        Check("reproducibility", "same_segments_and_horizons", only == 0, float(only), 0.0)
    )
    both = merged[merged["_merge"] == "both"]
    delta = 100 * (both["point_share_n"] - both["point_share_c"]).abs()
    worst = float(delta.max()) if len(delta) else 0.0
    checks.append(
        Check("reproducibility", "forecasts_identical", worst <= REPRO_FORECAST_PP, worst,
              REPRO_FORECAST_PP, detail="largest difference in percentage points")
    )  # fmt: skip
    a = both["alert_flag_c"].fillna(-1)
    b = both["alert_flag_n"].fillna(-1)
    flips = int((a != b).sum())
    checks.append(Check("reproducibility", "alert_flags_identical", flips == 0, float(flips), 0.0))
    ca = champion.artifacts.set_index("model_id")
    cn = challenger.artifacts.set_index("model_id")
    versions = [
        m
        for m in ca.index
        if m in cn.index and ca.loc[m, "model_version"] != cn.loc[m, "model_version"]
    ]
    checks.append(
        Check("reproducibility", "model_fingerprints_identical", not versions,
              detail="differ: " + ", ".join(versions) if versions else "")
    )  # fmt: skip
    gap = 0.0
    for model_id in ca.index:
        x, y = _coef(champion.artifacts, model_id), _coef(challenger.artifacts, model_id)
        if x is None or y is None:
            continue
        gap = max(gap, float(np.abs(x - y).max())) if len(x) == len(y) else float("inf")
    ok = gap <= REPRO_COEFFICIENT
    checks.append(Check("reproducibility", "coefficients_identical", ok, gap, REPRO_COEFFICIENT))
    return checks


def stability_checks(champion: Side, challenger: Side) -> list[Check]:
    """A later month: the fitted models may move, but not by more than they ever have."""
    forward = challenger.origin > champion.origin
    checks = [
        Check("stability", "origin_moves_forward", forward, float(challenger.origin),
              float(champion.origin), detail="challenger month against champion month")
    ]  # fmt: skip
    for h in _fitted_horizons(challenger.artifacts):
        old = champion.pred[champion.pred["horizon"] == h]
        new = challenger.pred[challenger.pred["horizon"] == h]
        common = old.merge(new, on=KEY[1:], suffixes=("_c", "_n"))
        kept = len(common) / len(old) if len(old) else 0.0
        checks.append(
            Check("stability", "segments_kept", kept >= STABLE_KEPT_FRACTION, kept,
                  STABLE_KEPT_FRACTION, h, f"{len(common)} of {len(old)}")
        )  # fmt: skip
        if common.empty:
            continue
        delta = 100 * (common["point_share_n"] - common["point_share_c"]).abs()
        weights = common["origin_visits_n"].astype(float)
        mean = float((delta * weights).sum() / weights.sum()) if weights.sum() else 0.0
        checks.append(
            Check("stability", "weighted_mean_change_pp", mean <= STABLE_WEIGHTED_MEAN_PP, mean,
                  STABLE_WEIGHTED_MEAN_PP, h, "volume-weighted mean absolute change")
        )  # fmt: skip
        big = delta[common["origin_visits_n"] >= BIG_SEGMENT_VISITS]
        top = float(big.max()) if len(big) else 0.0
        checks.append(
            Check("stability", "largest_big_segment_change_pp", top <= STABLE_BIG_SEGMENT_PP, top,
                  STABLE_BIG_SEGMENT_PP, h, f"segments of {BIG_SEGMENT_VISITS}+ visits")
        )  # fmt: skip
    return checks


def evaluate(
    champion: Side | None,
    challenger: Side,
    *,
    expected_ids: set[str] | None = None,
    override_reason: str | None = None,
) -> Decision:
    """The decision for a challenger. With no champion only the sanity checks apply."""
    checks = sanity_checks(challenger, expected_ids)
    decision = Decision(
        "no_champion" if champion is None else "promote",
        checks,
        champion.origin if champion else None,
        challenger.origin,
        champion.data_version if champion else None,
        challenger.data_version,
    )
    if champion is not None and challenger.origin == champion.origin:
        checks.extend(reproducibility_checks(champion, challenger))
    elif champion is not None:
        checks.extend(stability_checks(champion, challenger))
    if decision.failed:
        reason = (override_reason or "").strip()
        decision.status = "override" if reason else "hold"
        decision.override_reason = reason or None
    return decision


# --------------------------------------------------------------------------- the log and the stage


def _six(value: float | None) -> float | None:
    """A value to 6 significant figures, so the log is stable and readable."""
    if value is None:
        return None
    if not math.isfinite(value):
        return value
    return float(f"{value:.6g}")


def decision_frame(decision: Decision) -> pd.DataFrame:
    rows = []
    for c in decision.checks:
        rows.append(
            {
                "decision": decision.status, "scope": c.scope, "check": c.check,
                "horizon": c.horizon, "value": _six(c.value), "limit": _six(c.limit),
                "passed": int(c.passed), "detail": c.detail,
                "champion_origin": decision.champion_origin,
                "challenger_origin": decision.challenger_origin,
                "champion_data_version": decision.champion_data_version,
                "challenger_data_version": decision.challenger_data_version,
                "override_reason": decision.override_reason,
            }
        )  # fmt: skip
    return pd.DataFrame(rows, columns=LOG_COLUMNS)


def summary(decision: Decision) -> dict:
    return {
        "decision": decision.status,
        "checks": len(decision.checks),
        "failed": [
            f"{c.scope}:{c.check}" + (f"@h{c.horizon}" if c.horizon else "")
            for c in decision.failed
        ],
        "champion_origin": decision.champion_origin,
        "challenger_origin": decision.challenger_origin,
        "override_reason": decision.override_reason,
    }


def expected_model_ids() -> set[str]:
    """The models the adoption map says must exist (the alert only when it is a fitted model)."""
    from oa_market_intelligence.modeling.scoring import load_serving_map

    serving = load_serving_map()
    return {f"segment:{h}" for h in serving.get("segment", {})}


def run_promotion(
    engine, previous_db: Path | str | None, override_reason: str | None = None
) -> dict:
    """The `promotion` stage: judge the staged database against the published one, write the log
    to the staged database and either return the summary or raise `PromotionHeld`."""
    challenger = read_side(engine.url.database)  # the staged file
    if challenger is None:
        return {"decision": "skipped", "reason": "the staged database has no predictions to judge"}
    champion = read_side(previous_db) if previous_db and Path(previous_db).exists() else None
    decision = evaluate(
        champion, challenger, expected_ids=expected_model_ids(), override_reason=override_reason
    )
    frame = decision_frame(decision)
    with engine.begin() as conn:
        frame.to_sql(LOG_TABLE, conn, if_exists="replace", index=False)
    result = summary(decision)
    if decision.status == "hold":
        raise PromotionHeld(
            "the refitted models were not promoted: "
            + "; ".join(_describe(c) for c in decision.failed)
            + ". The models in service keep serving. To publish anyway, run again with "
            "--accept-challenger and a reason."
        )
    return result


def _describe(check: Check) -> str:
    """A failed check in one phrase, with its number and its limit when it has them."""
    name = f"{check.scope}:{check.check}" + (f"@h{check.horizon}" if check.horizon else "")
    if check.value is not None and check.limit is not None:
        return f"{name} ({_six(check.value)} against a limit of {_six(check.limit)})"
    return f"{name} ({check.detail})" if check.detail else name
