"""Display helpers for the site: turn the stored Phase 4 results into small frames and sentences.

The Streamlit page only draws what these functions return, so the wording of every headline is
tested here. Every function takes the stored result dicts (`None` when the database has none)
and never fits anything.
"""

from __future__ import annotations

import pandas as pd


def month_label(month_id: int) -> str:
    return pd.Timestamp(year=int(month_id) // 100, month=int(month_id) % 100, day=1).strftime(
        "%b %Y"
    )


def _month_start(month_id: int) -> pd.Timestamp:
    return pd.Timestamp(year=int(month_id) // 100, month=int(month_id) % 100, day=1)


# ---------------------------------------------------------------- frames


def trend_frame(findings: dict) -> pd.DataFrame:
    """Observed monthly share (percent) and the fitted piecewise-linear trend."""
    months = findings["months"]
    return pd.DataFrame(
        {
            "month": [_month_start(m) for m in months],
            "month_id": months,
            "share_pct": findings["series_pct"],
            "fitted_pct": findings["trend"]["fitted"],
        }
    )


def adoption_frame(findings: dict) -> pd.DataFrame:
    """Adjusted Zilretta share by specialty with its interval, in percent, largest first."""
    frame = pd.DataFrame(findings["adoption"]["table"])
    for column in ("adjusted_share", "low", "high"):
        frame[column] = frame[column] * 100
    return frame.sort_values("adjusted_share", ascending=False).reset_index(drop=True)


def decomposition_frame(findings: dict) -> pd.DataFrame:
    """Year-over-year change in share split into mix and rate, in percentage points."""
    frame = pd.DataFrame(findings["decomposition"]["chain"])
    for column in ("total", "mix", "rate"):
        frame[column] = frame[column] * 100
    return frame[["comparison", "total", "mix", "rate"]]


def forecast_frame(forecast: dict) -> pd.DataFrame:
    """Walk-forward forecasts against actuals, with the 80% and 90% ranges, in percent."""
    frame = pd.DataFrame(forecast["history"])
    frame["month"] = [_month_start(m) for m in frame["month_id"]]
    return frame


def rolling_frame(monitoring: dict) -> pd.DataFrame:
    frame = pd.DataFrame(monitoring["direction"]["rolling"])
    frame["month"] = [_month_start(m) for m in frame["month_id"]]
    return frame


# ---------------------------------------------------------------- sentences


def trend_headline(findings: dict) -> str:
    trend = findings["trend"]
    breaks, segments = trend["break_months"], trend["segments"]
    if not breaks:
        return "The test found no clear change in the direction of the share trend."
    slopes = [segment["slope_pp_per_year"] for segment in segments]
    when = ", ".join(month_label(m) for m in breaks)
    if len(breaks) == 1 and slopes[0] > 0 > slopes[-1]:
        return f"Zilretta's visit share rose until about {when} and has fallen since."
    if len(breaks) == 1 and slopes[0] < 0 < slopes[-1]:
        return f"Zilretta's visit share fell until about {when} and has risen since."
    return f"The share trend changed direction around {when}."


def decomposition_headline(findings: dict) -> str | None:
    parts = findings["decomposition"]
    pairs, windows = parts["bootstrap"], parts["windows"]
    label = "third_to_last" if "third_to_last" in pairs else "first_to_last"
    earlier = windows[2 if label == "third_to_last" else 0]
    entry = pairs[label]
    total, mix, rate = (entry[k]["estimate"] * 100 for k in ("total", "mix", "rate"))
    where = "share within specialties" if abs(rate) >= abs(mix) else "a shift in specialty mix"
    return (
        f"Between the 12 months to {month_label(earlier[-1])} and the 12 months to "
        f"{month_label(windows[-1][-1])} the share changed by {total:+.2f} percentage points; "
        f"most of that comes from {where} ({rate:+.2f} points) rather than from where visits "
        f"happen ({mix:+.2f} points)."
    )


def specialty_headline(findings: dict) -> str:
    table = findings["adoption"]["table"]
    above = sum(row["position"] == "clearly above" for row in table)
    below = sum(row["position"] == "clearly below" for row in table)
    stability = findings["adoption"]["stability"]
    return (
        f"After adjusting for age and gender, {above} specialties use Zilretta clearly more than "
        f"the overall share and {below} clearly less; the ranking of specialties is "
        f"{'stable' if stability['spearman'] >= 0.7 else 'not stable'} between the first and "
        f"second half of the period (rank correlation {stability['spearman']:.2f})."
    )


def robustness_headline(findings: dict) -> str:
    verdicts = findings["robustness"]["verdicts"]
    robust = sorted(key for key, value in verdicts.items() if value["label"] == "robust")
    fragile = sorted(key for key, value in verdicts.items() if value["label"] != "robust")
    if not fragile:
        return "Every finding held up under all the robustness checks."
    return (
        f"{len(robust)} of {len(verdicts)} findings held up under the robustness checks; "
        f"not robust: {', '.join(fragile)}."
    )


def forecast_headline(forecast: dict) -> str:
    nxt = forecast["next_month"]
    return (
        f"{month_label(nxt['month_id'])}: Zilretta's visit share is most likely near "
        f"{nxt['point']:.2f}% (80% range {nxt['lo80']:.2f}% to {nxt['hi80']:.2f}%, "
        f"90% range {nxt['lo90']:.2f}% to {nxt['hi90']:.2f}%)."
    )


def forecast_caveat(forecast: dict) -> str:
    decision = forecast["decision"]
    if decision["promoted"]:
        return f"A trained model ({decision['serving']}) beat the baselines on held-out months."
    return (
        f"No trained model beat the {forecast['best_baseline'].replace('_', ' ')} baseline on "
        "the held-out months, so this is a reference range built from that baseline, not a "
        "prediction that the share will change."
    )


def model_serving_lines(results: dict) -> list[dict]:
    """One row per modelling task: what is served and whether anything trained was promoted."""
    rows = []
    for task, key in (("Segment share, next month", "segment_model"),
                      ("Overall share forecast", "forecast")):
        stored = results.get(key)
        if stored is None:
            continue
        decision = stored["decision"]
        rows.append(
            {
                "task": task,
                "serving": decision["serving"].replace("_", " "),
                "trained model promoted": "yes" if decision["promoted"] else "no",
                "test months": stored["n_test_months"],
            }
        )
    direction = results.get("direction")
    if direction is not None:
        decision = direction["decision"]
        rows.append(
            {
                "task": "Direction (Up/Flat/Down)",
                "serving": decision["serving"].replace("_", " "),
                "trained model promoted": "yes" if decision.get("promoted") else "no",
                "test months": direction["n_test"],
            }
        )
    return rows
