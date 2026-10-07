"""The model panel for the website: what each model scored and which one serves.

The serving rule is fixed in advance (decision log DL-32) so a model is never promoted by
default.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.features.monthly import (
    compute_monthly_features,
    load_monthly_gold,
    model_ready_monthly,
)
from oa_market_intelligence.modeling.baselines import (
    MajorityClassBaseline,
    PersistenceBaseline,
    SeasonalBaseline,
    StratifiedRandomBaseline,
)
from oa_market_intelligence.modeling.evaluation import (
    DEFAULT_MIN_TRAIN,
    compare_models,
    score_table,
    simulate_scores,
)
from oa_market_intelligence.modeling.models import make_logistic_regression

BASELINE_NAMES = ("always-majority", "persistence", "seasonal")
CHANCE_METRICS = ["accuracy", "balanced_accuracy", "macro_f1", "recall_down", "recall_up"]


def serving_decision(scores: pd.DataFrame, *, chance_p95: float) -> dict:
    """Which model serves: a trained model only if its balanced accuracy is strictly above
    both the 95th percentile of random guessing and every simple baseline's; otherwise the
    best baseline. `scores` is indexed by model name with a `balanced_accuracy` column and
    holds the three baselines plus any trained models (not the random-guess baseline)."""
    missing = [b for b in BASELINE_NAMES if b not in scores.index]
    if missing:
        raise ValueError(f"missing baseline(s) {missing}: cannot judge a trained model")

    balanced = scores["balanced_accuracy"]
    best_baseline = balanced[list(BASELINE_NAMES)].idxmax()
    trained = [name for name in scores.index if name not in BASELINE_NAMES]
    if not trained:
        return {
            "promoted": False,
            "serving": best_baseline,
            "reason": f"No trained model to judge; the best baseline ({best_baseline}) serves.",
        }

    best_model = balanced[trained].idxmax()
    model_score = balanced[best_model]
    baseline_score = balanced[best_baseline]
    if model_score <= chance_p95:
        reason = (
            f"{best_model} (balanced accuracy {model_score:.3f}) is not above the range chance "
            f"produces (random guessing, 95th percentile {chance_p95:.3f}), so it is not promoted; "
            f"the best baseline ({best_baseline}) serves."
        )
    elif model_score <= baseline_score:
        reason = (
            f"{best_model} (balanced accuracy {model_score:.3f}) does not beat the best "
            f"baseline, {best_baseline} ({baseline_score:.3f}), so it is not promoted."
        )
    else:
        return {
            "promoted": True,
            "serving": best_model,
            "reason": (
                f"{best_model} (balanced accuracy {model_score:.3f}) beats chance "
                f"({chance_p95:.3f}) and every baseline (best: {best_baseline}, "
                f"{baseline_score:.3f}), so it serves."
            ),
        }
    return {"promoted": False, "serving": best_baseline, "reason": reason}


def model_panel(
    engine: Engine, *, chance_runs: int = 300, min_train: int = DEFAULT_MIN_TRAIN
) -> dict:
    """Walk-forward results for the baselines and the logistic regression, the chance band,
    and the serving decision, for the monthly direction task."""
    ready = model_ready_monthly(compute_monthly_features(load_monthly_gold(engine)))
    models = {
        "always-majority": MajorityClassBaseline,
        "persistence": PersistenceBaseline,
        "seasonal": SeasonalBaseline,
        "logistic": make_logistic_regression,
    }
    predictions = compare_models(ready, models, min_train=min_train)
    scores = score_table(predictions)

    chance_runs_scores = simulate_scores(
        ready,
        lambda seed: (lambda: StratifiedRandomBaseline(seed)),
        seeds=range(chance_runs),
        min_train=min_train,
    )
    chance = pd.DataFrame(
        {
            "mean": chance_runs_scores[CHANCE_METRICS].mean(),
            "p05": chance_runs_scores[CHANCE_METRICS].quantile(0.05),
            "p95": chance_runs_scores[CHANCE_METRICS].quantile(0.95),
        }
    )
    decision = serving_decision(scores, chance_p95=float(chance.loc["balanced_accuracy", "p95"]))
    return {
        "scores": scores,
        "chance": chance,
        "decision": decision,
        "n_test": int(len(predictions)),
        "label_counts": predictions["y_true"].value_counts().to_dict(),
    }
