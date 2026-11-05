"""A post-hoc check of the E3 result (docs/modelling_round2_results.md); not part of the plan.

The registered E3 comparators continue a trend. The run showed that trends in this data reverse:
the segment's own and the specialty's own 3-month change both correlate *negatively* with the next
3 months' change, so the registered best comparator is a random order. This module repeats the E3
comparison with those two scores' signs flipped ("the trend reverses", the same idea as round 1's
post-hoc "just spiked" check) to see how much of the model's ranking is only mean reversion. It
changes no registered result and was added after the results were seen; it is not one of the
seven tests and has no bar of its own.

    PYTHONPATH=src python -m oa_market_intelligence.modeling.round2.posthoc --run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from oa_market_intelligence.features.groups import columns_for
from oa_market_intelligence.modeling.round2 import e3_ranking as e3
from oa_market_intelligence.modeling.round2.e1_bias import ROUND_PERCENTILE
from oa_market_intelligence.modeling.round2.e2_alert import mean_bootstrap
from oa_market_intelligence.modeling.round2.run import stored_logistic_log_loss
from oa_market_intelligence.modeling.run_modelling import (
    DEFAULT_DB,
    RESULTS_JSON,
    clean,
    load_inputs,
)

ROOT = Path(__file__).resolve().parents[4]
POSTHOC_JSON = ROOT / "data" / "reference" / "modelling_round2_posthoc.json"
FLIPPED = {"segment_reversal": "segment_trend", "specialty_reversal": "specialty_trend"}


def flipped_check(frame: pd.DataFrame, *, n_boot: int = 2000, seed: int = 0) -> dict:
    """The model against the two flipped trend scores on the registered rows: mean rank
    correlation, top-fifth hit rate and the paired gain (resampling whole test origins)."""
    frame = frame.copy()
    for new, old in FLIPPED.items():
        frame[new] = -frame[old]
    methods = ("model", *FLIPPED)
    spearman = {m: e3.monthly_spearman(frame, m) for m in methods}
    hits = {m: e3.top_fifth_hit(frame, m) for m in methods}
    draws = mean_bootstrap(spearman, n_boot=n_boot, seed=seed)
    versus = {}
    for name in FLIPPED:
        gain = draws["model"] - draws[name]
        low = np.percentile(gain, ROUND_PERCENTILE)
        versus[name] = {
            "estimate": float(spearman["model"].mean() - spearman[name].mean()),
            "lower_at_round_percentile": float(low),
            "lower_at_2_5": float(np.percentile(gain, 2.5)),
            "share_positive": float((gain > 0).mean()),
        }
    return {
        "n_test_months": int(len(spearman["model"])),
        "mean_spearman": {m: float(s.mean()) for m, s in spearman.items()},
        "top_fifth_hit_rate": {m: float(s.mean()) for m, s in hits.items()},
        "chance_hit_rate": e3.chance_hit(frame),
        "model_versus_flipped": versus,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--stored", type=Path, default=RESULTS_JSON)
    parser.add_argument("--out", type=Path, default=POSTHOC_JSON)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--n-boot", type=int, default=2000)
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("choose --run")
    data = load_inputs(create_engine(f"sqlite:///{args.db.as_posix()}"))
    stored = json.loads(args.stored.read_text("utf-8"))
    run = e3.run_e3(
        data["rows"],
        data["outcomes"],
        extra_columns=columns_for(("G4",), target="T2"),
        scenarios=("main",),
        stored_log_loss=stored_logistic_log_loss(stored, e3.HORIZON),
        n_boot=10,
    )
    result = {
        "note": "post hoc, added after the results were seen; outside the plan's rules",
        "e3_main": flipped_check(run["_frame"], n_boot=args.n_boot),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(clean(result), indent=1, sort_keys=True, allow_nan=False) + "\n", "utf-8"
    )
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
