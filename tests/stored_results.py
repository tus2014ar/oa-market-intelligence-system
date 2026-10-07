"""Small stored Phase 4 results with the same shape `serving/results.py` produces.

The numbers are made up and tiny; they exist so the site and its display helpers can be tested
without fitting any model.
"""

MONTHS = [202001 + i for i in range(12)]


def stored_results(*, review: bool = True) -> dict:
    specialties = [
        {"specialty": "Pain Medicine", "adjusted_share": 0.06, "low": 0.05, "high": 0.07,
         "visit_share_pct": 20.0, "position": "clearly above"},
        {"specialty": "Orthopedic Surgery", "adjusted_share": 0.025, "low": 0.02, "high": 0.03,
         "visit_share_pct": 40.0, "position": "not clearly different"},
        {"specialty": "Family Practice", "adjusted_share": 0.01, "low": 0.008, "high": 0.012,
         "visit_share_pct": 40.0, "position": "clearly below"},
    ]
    findings = {
        "months": MONTHS,
        "series_pct": [2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 2.8, 2.6, 2.4, 2.2, 2.0, 1.8],
        "trend": {
            "fitted": [2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 2.8, 2.6, 2.4, 2.2, 2.0, 1.8],
            "n_breaks": 1, "break_months": [202006], "intervals": [[202004, 202008]],
            "p_values": [0.001, 0.4],
            "segments": [
                {"from": 202001, "to": 202005, "mean_pct": 2.4, "slope_pp_per_year": 2.4},
                {"from": 202006, "to": 202012, "mean_pct": 2.4, "slope_pp_per_year": -2.4},
            ],
            "events_overlap": [[]], "block_sensitivity": [], "other_categories": {},
        },
        "decomposition": {
            "windows": [[202001, 202012], [202101, 202112]],
            "chain": [{"comparison": "1 to 2", "share_a": 0.03, "share_b": 0.02,
                       "total": -0.01, "mix": 0.001, "rate": -0.011}],
            "bootstrap": {"first_to_last": {
                "total": {"estimate": -0.01, "low": -0.012, "high": -0.008},
                "mix": {"estimate": 0.001, "low": 0.0, "high": 0.002},
                "rate": {"estimate": -0.011, "low": -0.013, "high": -0.009}}},
            "focus": "1 to 2", "top_specialties": [], "granularity": {},
        },
        "adoption": {
            "overall_share": 0.025, "table": specialties,
            "deviance": {"specialty": 0.5, "age_band": 0.4, "gender": 0.01, "dispersion": 1.5},
            "interval_ratio_median": 2.0,
            "stability": {"spearman": 0.8, "low": 0.6, "high": 0.9, "pearson_logit": 0.9,
                          "same_side_fraction": 0.9,
                          "table": [{"specialty": "Family Practice", "first_half": 0.01,
                                     "second_half": 0.03, "flipped": True}],
                          "overall_first_half": 0.025, "overall_second_half": 0.02},
        },
        "robustness": {"verdicts": {"T1": {"label": "robust", "failed": []},
                                    "D1": {"label": "robust", "failed": []},
                                    "A1": {"label": "robust", "failed": []},
                                    "A3": {"label": "fragile", "failed": ["x"]}},
                       "runs": {}},
        "precision": "fast",
    }
    segment = {
        "n_test_months": 10, "n_test_rows": 100, "n_prediction_rows": 200,
        "trained": ["logistic"],
        "scores": {"last_month": {"log_loss": 0.113, "mae": 0.015},
                   "logistic": {"log_loss": 0.1127, "mae": 0.011}},
        "best_baseline": "last_month", "summaries": {},
        "decision": {"promoted": ["logistic"], "serving": "logistic", "tie_break": "simplest"},
        "wins": {}, "calibration": {"table": [], "slope": 1.1, "intercept": 0.0,
                                    "mean_pred": 0.0265, "mean_obs": 0.0248},
        "by_size": [{"bucket": "<50", "improvement": 0.0077}], "by_specialty": [],
        "importance": {}, "a2": {}, "precision": "fast",
    }
    rows = [{"month_id": m, "actual": 2.0, "point": 2.0, "lo80": 1.7, "hi80": 2.3,
             "lo90": 1.6, "hi90": 2.4} for m in MONTHS]
    forecast = {
        "n_test_months": 12, "scores": {"last_month": {"mae": 0.125, "cover90": 0.95}},
        "best_baseline": "last_month", "summaries": {},
        "decision": {"promoted": [], "serving": "last_month"}, "fallbacks": {},
        "next_month": {"month_id": 202101, "model": "last_month", "point": 1.8, "lo80": 1.5,
                       "hi80": 2.1, "lo90": 1.4, "hi90": 2.2, "fallback": False},
        "history": rows, "block_sensitivity": [], "precision": "fast",
    }
    direction = {
        "n_test": 35, "label_counts": {"Flat": 25, "Down": 5, "Up": 5},
        "scores": {"rf": {"accuracy": 0.5, "balanced_accuracy": 0.33, "macro_f1": 0.3,
                          "recall_down": 0.2, "recall_up": 0.2},
                   "gbm": {"accuracy": 0.55, "balanced_accuracy": 0.4, "macro_f1": 0.35,
                           "recall_down": 0.3, "recall_up": 0.3}},
        "chance": {"mean": 0.33, "p95": 0.45, "p05": 0.22},
        "decision": {"serving": "seasonal", "promoted": [], "reason": "No model cleared the bar."},
        "versus_persistence": {}, "in_sample": {},
        "power": {"persistence_accuracy": 0.63, "discordance": 0.3, "table": [],
                  "smallest_detectable_gain": 0.3},
        "predictions": [], "precision": "fast",
    }
    monitoring = {
        "direction": {"model": "seasonal", "match_rate": 0.66, "rate_low": 0.5, "rate_high": 0.8,
                      "threshold": 0.33, "random_line": 0.17, "latest_rolling": 0.5,
                      "flag": False, "windows_at_or_below": 0, "n_windows": 2,
                      "rolling": [{"month_id": 202011, "accuracy": 0.5},
                                  {"month_id": 202012, "accuracy": 0.67}]},
        "forecast": {"rule": "R90", "level": "90", "k": 3, "n": 6,
                     "misses_in_window": 3 if review else 0, "alarm": review,
                     "recent": [{"month_id": m, "outside": review} for m in MONTHS[-6:]],
                     "backtest_misses": 2, "backtest_months": 12, "backtest_alarm_windows": 0,
                     "n_windows": 7, "false_alarm_rate": 0.004},
        "detection": [{"scenario": "volatility x2", "rule": "R90", "detection_rate": 0.5}],
        "status": "review" if review else "ok",
        "banner": ("At least 3 of the last 6 months fell outside the forecast's 90% interval."
                   if review else None),
        "precision": "fast",
    }
    return {"findings": findings, "segment_model": segment, "forecast": forecast,
            "direction": direction, "monitoring": monitoring}
