"""Modelling round 2: the one combined run of all seven registered tests
(docs/modelling_round2_plan.md, section 11).

    oa-model-round2 --run

E1a and E1b at 1 and 3 months, E2 at 1 and 3 months, E3 at 3 months, each at the round's one
percentile (0.05 / 7). The run reads the warehouse and the stored round 1 scores; it writes only
`data/reference/modelling_round2_results.json`. It writes nothing to the warehouse, is not part of
`oa-publish` and does not touch `serving_models.json`. A pass is provisional, never adoption.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

from oa_market_intelligence.features.groups import columns_for
from oa_market_intelligence.modeling.round2 import e1_bias, e2_alert, e3_ranking
from oa_market_intelligence.modeling.run_modelling import (
    DEFAULT_DB,
    RESULTS_JSON,
    clean,
    load_inputs,
)

ROOT = Path(__file__).resolve().parents[4]
ROUND2_JSON = ROOT / "data" / "reference" / "modelling_round2_results.json"
TESTS = (
    ("E1a", 1),
    ("E1a", 3),
    ("E1b", 1),
    ("E1b", 3),
    ("E2", 1),
    ("E2", 3),
    ("E3", 3),
)


def stored_logistic_log_loss(stored: dict, horizon: int) -> float:
    """Round 1's pooled log-loss of the logistic regression at a horizon (main scenario)."""
    return float(stored["T2"][str(horizon)]["scenarios"]["main"]["models"]["logistic"]["log_loss"])


def summarise(results: dict) -> list[dict]:
    """The seven formal tests, one line each: what was tested and whether it passed."""
    lines = []
    for name, horizon in TESTS:
        if name.startswith("E1"):
            main = results["e1"][str(horizon)]["scenarios"]["main"]
            variant = main["variants"][name.lower()]
            lines.append(
                {
                    "test": name,
                    "horizon": horizon,
                    "passes": variant["passes"],
                    "checks": {
                        key: variant[key]
                        for key in (
                            "bias_within_limit",
                            "not_less_accurate",
                            "still_beats_baseline",
                        )
                    },
                    "bias_before": main["bias"]["raw"],
                    "bias_after": main["bias"][name.lower()],
                }
            )
        elif name == "E2":
            main = results["e2"][str(horizon)]["scenarios"]["main"]
            lines.append(
                {
                    "test": name,
                    "horizon": horizon,
                    "passes": main["passes"],
                    "checks": {
                        "lift_at_least_2": main["lift_at_least_2"],
                        "beats_best_comparator": main["beats_best_comparator"],
                    },
                    "precision_at_10": main["precision_at_10"]["model"],
                    "base_rate": main["base_rate"],
                    "best_comparator": main["best_comparator"],
                }
            )
        else:
            main = results["e3"]["scenarios"]["main"]
            lines.append(
                {
                    "test": name,
                    "horizon": horizon,
                    "passes": main["passes"],
                    "checks": {"beats_best_comparator": main["beats_best_comparator"]},
                    "mean_spearman": main["mean_spearman"]["model"],
                    "best_comparator": main["best_comparator"],
                }
            )
    return lines


def run_round2(
    rows: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    extra_columns: tuple[str, ...] = (),
    stored: dict | None = None,
    scenarios: tuple[str, ...] = e1_bias.SCENARIOS,
    n_boot: int = 2000,
    seed: int = 0,
    n_jobs: int = 1,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """All seven tests. `stored` is round 1's results, used to check that the uncorrected walks are
    round 1's own (the run stops if they are not)."""

    def guard(horizon: int) -> float | None:
        return stored_logistic_log_loss(stored, horizon) if stored else None

    results: dict = {
        "meta": {
            "plan": "docs/modelling_round2_plan.md (DL-92)",
            "tests": len(TESTS),
            "percentile": e1_bias.ROUND_PERCENTILE,
            "n_boot": n_boot,
            "seed": seed,
            "scenarios": list(scenarios),
            "g4_columns": list(extra_columns),
            "status": "exploratory and provisional until confirmed on months after July 2025",
        },
        "e1": {},
        "e2": {},
    }
    for horizon in e1_bias.HORIZONS:
        run = e1_bias.run_e1(
            rows,
            outcomes,
            horizon,
            extra_columns=extra_columns,
            scenarios=scenarios,
            stored_log_loss=guard(horizon),
            n_boot=n_boot,
            seed=seed,
            n_jobs=n_jobs,
            progress=progress,
        )
        results["e1"][str(horizon)] = run
    for horizon in e2_alert.HORIZONS:
        results["e2"][str(horizon)] = e2_alert.run_e2(
            rows,
            outcomes,
            horizon,
            extra_columns=extra_columns,
            scenarios=scenarios,
            n_boot=n_boot,
            seed=seed,
            progress=progress,
        )
    results["e3"] = e3_ranking.run_e3(
        rows,
        outcomes,
        extra_columns=extra_columns,
        scenarios=scenarios,
        stored_log_loss=guard(e3_ranking.HORIZON),
        n_boot=n_boot,
        seed=seed,
        progress=progress,
    )
    results["summary"] = summarise(results)
    results["tests_passed"] = sum(1 for line in results["summary"] if line["passes"])
    return results


def write_results(results: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(clean(results), indent=1, sort_keys=True, allow_nan=False) + "\n", "utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--stored", type=Path, default=RESULTS_JSON)
    parser.add_argument("--out", type=Path, default=ROUND2_JSON)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--n-jobs", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("choose --run")
    data = load_inputs(create_engine(f"sqlite:///{args.db.as_posix()}"))
    stored = json.loads(args.stored.read_text("utf-8"))
    results = run_round2(
        data["rows"],
        data["outcomes"],
        extra_columns=columns_for(("G4",), target="T2"),
        stored=stored,
        n_boot=args.n_boot,
        n_jobs=args.n_jobs,
        progress=lambda message: print(message, flush=True),
    )
    write_results(results, args.out)
    for line in results["summary"]:
        verdict = "PASS" if line["passes"] else "no pass"
        print(f"{line['test']} h={line['horizon']}: {verdict}")
    print(f"{results['tests_passed']} of {len(TESTS)} tests passed; written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
