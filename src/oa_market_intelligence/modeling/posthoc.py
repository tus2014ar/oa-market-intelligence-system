"""A post-hoc check of the T3 result (docs/modelling_results.md). Not part of the plan's rules.

The registered "recent drop" baseline scores a segment higher when its latest share is *below* its
3-month mean. The real run showed that this points the wrong way: a share that spiked above its
recent average tends to fall back, so the baseline scores below no skill. This module repeats the
T3 comparison with that one baseline's sign flipped (nothing else changed) to see how much of the
registered gain is only that regression to the mean. It does not change any registered result, and
it was added after the results were seen.

    PYTHONPATH=src python -m oa_market_intelligence.modeling.posthoc --run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from oa_market_intelligence.features.groups import columns_for
from oa_market_intelligence.modeling.alerts import (
    add_alert_label,
    ap_bootstrap,
    average_precision,
    recent_drop_score,
    walk_alert_method,
    walk_forward_alerts,
)
from oa_market_intelligence.modeling.horizons import HORIZONS, pair_rows, promotion_percentile
from oa_market_intelligence.modeling.run_modelling import DEFAULT_DB, clean, load_inputs

POSTHOC_JSON = Path(__file__).resolve().parents[3] / "data" / "reference" / "modelling_posthoc.json"
OTHER_PERCENTILE = 2.5


def _flipped(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, float]:
    """The recent-drop score with its sign flipped; alerts on every row (the cut-off is not used
    by PR-AUC)."""
    return -recent_drop_score(test), float("-inf")


def _registered(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, float]:
    return recent_drop_score(test), float("-inf")


def flipped_baseline_check(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    horizon: int,
    *,
    extra_columns: tuple[str, ...] = (),
    n_boot: int = 2000,
    seed: int = 0,
) -> dict:
    """PR-AUC of the registered recent-drop baseline, of the same score with its sign flipped, and
    of the T3 logistic regression, with the logistic's paired gain over the flipped baseline."""
    paired = add_alert_label(pair_rows(rows, outcomes, horizon))
    logistic = walk_alert_method(paired, horizon, "logistic", extra_columns)
    registered = walk_forward_alerts(paired, _registered, horizon)
    flipped = walk_forward_alerts(paired, _flipped, horizon)
    draws = ap_bootstrap({"logistic": logistic, "flipped": flipped}, n_boot=n_boot, seed=seed)
    gain = draws["logistic"] - draws["flipped"]
    own = promotion_percentile(horizon)
    return {
        "horizon": horizon,
        "decline_share": float(logistic["label"].mean()),
        "pr_auc_registered_recent_drop": average_precision(
            registered["label"], registered["score"]
        ),
        "pr_auc_flipped_recent_drop": average_precision(flipped["label"], flipped["score"]),
        "pr_auc_logistic": average_precision(logistic["label"], logistic["score"]),
        "gain_over_flipped": average_precision(logistic["label"], logistic["score"])
        - average_precision(flipped["label"], flipped["score"]),
        "lower_at_registered_percentile": float(np.percentile(gain, own)),
        "registered_percentile": own,
        "lower_at_2_5": float(np.percentile(gain, OTHER_PERCENTILE)),
        "share_positive": float((gain > 0).mean()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=POSTHOC_JSON)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--n-boot", type=int, default=2000)
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("choose --run")
    data = load_inputs(create_engine(f"sqlite:///{args.db.as_posix()}"))
    g4 = columns_for(("G4",), target="T2")
    results = {
        "note": "post hoc, added after the results were seen; outside the plan's rules",
        "horizons": {
            str(h): flipped_baseline_check(
                data["rows"], data["outcomes"], h, extra_columns=g4, n_boot=args.n_boot
            )
            for h in HORIZONS
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(clean(results), indent=1, sort_keys=True, allow_nan=False) + "\n", "utf-8"
    )
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
