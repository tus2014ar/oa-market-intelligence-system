"""The Model results tab, as tested functions: the headline for each task, the round 2 tests in
plain words and what the adopted segment model relies on (DL-100).

Everything here reads stored results (the scoreboard from `model_scores`,
`modelling_round2_results`,
its post-hoc check and the SHAP summary) and computes nothing new. Round 2 was found after seeing
round 1 and is exploratory: a pass is provisional and nothing from it is adopted, and the wording
here says so wherever round 2 appears.
"""

from __future__ import annotations

import pandas as pd

TASKS = ("All", "National share", "Segment share", "Decline alert")
TEST_NAMES = {
    "E1a": "Bias correction factor",
    "E1b": "Rolling 36-month window",
    "E2": "Lasting-decline top 10",
    "E3": "Ranking winners and losers",
}
COMPARATOR_NAMES = {
    "recent_spike": 'the one-line "just spiked" rule',
    "random": "a random order",
    "small_segment": "segment size alone",
    "round1_alert": "the first alert",
}
PROVISIONAL = (
    "Round 2 was designed after seeing round 1's results, on the same test months. A pass is "
    "provisional: it is checked again on months after July 2025 and adopted only if it holds. "
    "Nothing from round 2 is used by the site yet."
)


def for_task(board: pd.DataFrame, task: str) -> pd.DataFrame:
    """The scoreboard rows of one task ("All" keeps every row)."""
    return board if task == "All" else board[board["task"] == task].reset_index(drop=True)


def segment_gains(board: pd.DataFrame) -> dict[int, float]:
    """The segment model's gain over the simple rule by horizon, as a fraction of the rule's
    score, for the horizons where a model is the one served."""
    rows = board[board["task"] == "Segment share"]
    out = {}
    for r in rows.itertuples():
        if r.chosen_model != r.simple_rule:
            out[int(r.horizon)] = (r.simple_rule_score - r.chosen_model_score) / r.simple_rule_score
    return out


def headline_cards(board: pd.DataFrame, bias: pd.DataFrame, posthoc: pd.DataFrame, round2: dict):
    """Four (label, value, caption) cards: the national share, the segment share, the decline
    alert and round 2."""
    national = board[board["task"] == "National share"]
    beaten = int((national["chosen_model"] != national["simple_rule"]).sum())
    gains = segment_gains(board)
    biases = dict(zip(bias["horizon"], bias["forecast_above_actual_pct"], strict=True))
    gain_text = " / ".join(f"+{100 * g:.1f}%" for g in gains.values()) or "none"
    horizons = " and ".join(str(h) for h in gains)
    high = " and ".join(f"{biases[h]:.1f}%" for h in gains if h in biases)
    reliable = int(posthoc["reliable"].sum())
    return [
        (
            "National share",
            "No model wins" if beaten == 0 else f"{beaten} of {len(national)}",
            f'"Same as last month" is the forecast at {len(national)} horizons: no model beat it.',
        ),
        (
            "Segment share",
            gain_text,
            f"Small gain over the simple rule at {horizons} months, and the forecast runs "
            f"{high} high.",
        ),
        (
            "Decline alert",
            f"{reliable} of {len(posthoc)}",
            'horizons where it reliably beats a one-line "just spiked" rule (1 month only).',
        ),
        (
            "Round 2",
            f"{round2['tests_passed']} of {round2['meta']['tests']}",
            "tests passed. All provisional, none adopted.",
        ),
    ]


def round2_rows(round2: dict, posthoc: dict) -> list[dict]:
    """The seven round 2 tests, each with its name, the horizon, whether it passed, the result
    and a plain-words reading."""
    spearman_flipped = posthoc["e3_main"]["mean_spearman"]["segment_reversal"]
    rows = []
    for line in round2["summary"]:
        test, horizon, passed = line["test"], line["horizon"], bool(line["passes"])
        h = f"{horizon} month{'s' if horizon != 1 else ''}"
        if test.startswith("E1"):
            before, after = 100 * line["bias_before"], 100 * line["bias_after"]
            limit = 2.5 if horizon == 1 else 5.0
            result = f"{before:.1f}% to {after:.1f}% too high"
            if passed:
                plain = (
                    "Most of the upward bias is removed and accuracy is not lost "
                    f"(limit {limit:g}%)."
                )
            else:
                plain = (
                    "Accuracy is slightly better but the forecast is still above the "
                    f"{limit:g}% limit."
                )
        elif test == "E2":
            main = round2["e2"][str(horizon)]["scenarios"]["main"]
            rule = main["precision_at_10"][line["best_comparator"]]
            result = (
                f"{100 * line['precision_at_10']:.0f}% of the top 10 right "
                f"(chance {100 * line['base_rate']:.0f}%)"
            )
            plain = (
                f"About {line['precision_at_10'] / line['base_rate']:.1f} times chance, but "
                f"{COMPARATOR_NAMES.get(line['best_comparator'], line['best_comparator'])} gets "
                f"{100 * rule:.0f}%: the model adds nothing."
            )
        else:
            result = f"rank correlation {line['mean_spearman']:+.2f}"
            plain = (
                "Orders segments by expected change well, but mostly because recent risers fall "
                f"back: reversing each segment's last move alone gives {spearman_flipped:+.2f}."
            )
        rows.append(
            {
                "test": test,
                "name": TEST_NAMES[test],
                "horizon": horizon,
                "horizon_label": h,
                "passes": passed,
                "result": result,
                "plain": plain,
            }
        )
    return rows


def round2_summary(round2: dict) -> dict:
    """How many round 2 tests passed, and which."""
    passed = [f"{x['test']} ({x['horizon']} mo)" for x in round2["summary"] if x["passes"]]
    return {
        "passed": int(round2["tests_passed"]),
        "total": int(round2["meta"]["tests"]),
        "which": passed,
        "status": round2["meta"]["status"],
    }


def shap_rows(shap: dict, horizon: int, top: int = 9) -> dict:
    """What the adopted segment model relies on at a horizon: each kind of information's share of
    the movement it causes in the forecasts (from the stored SHAP summary), with an example."""
    block = shap["horizons"][str(horizon)]
    groups = sorted(
        block["group_share_of_movement_pct"].items(), key=lambda kv: kv[1], reverse=True
    )[:top]
    example = block["example"]
    segment = example["segment"]
    return {
        "groups": groups,
        "typical_pct": block["typical_segment_pct"],
        "segments": block["segments"],
        "origin_month_id": shap["origin_month_id"],
        "example": {
            "name": f"{segment['specialty_name'].title()}, ages "
            f"{segment['age_band'].title().replace(' To ', ' to ')}, {segment['gender'].lower()}",
            "visits": example["visits_last_month"],
            "last_pct": example["last_month_pct"],
            "forecast_pct": example["forecast_pct"],
        },
        "checks_pass": bool(block["matches_stored_forecasts"] and block["contributions_add_up"]),
    }


def alert_cards(posthoc: pd.DataFrame) -> list[tuple[str, float, float, float, bool]]:
    """For each horizon: the registered rule's score, the corrected rule's, the alert's and whether
    the alert's gain over the corrected rule is reliable (PR-AUC, higher is better)."""
    return [
        (
            f"{int(r.horizon)} month{'s' if r.horizon != 1 else ''}",
            float(r.registered_simple_rule),
            float(r.corrected_simple_rule),
            float(r.model),
            bool(r.reliable),
        )
        for r in posthoc.itertuples()
    ]
