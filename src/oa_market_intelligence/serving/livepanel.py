"""The logic and the wording behind the Forecasts page's "Run the model live" panel (DL-122).

The page draws; this module decides. It picks the options a visitor can choose, compares the
live answer with the forecast the page already shows, compares the data version the website holds
with the one the live service reports, and writes the sentences. It has no Streamlit, no network
and no clock, so every rule here is tested exactly.
"""

from __future__ import annotations

import pandas as pd

# The live service rounds its forecast to 4 decimals of a percent and calls two forecasts equal
# when they are within this many percentage points (the same rule as the API's `matches_stored`).
TOLERANCE_PP = 1e-3

LEAD = (
    "Pick a segment and the website asks the model service on AWS to run the saved model now. "
    "The answer should equal the forecast above; this shows that it does."
)
BIAS_NOTE = (
    "Segment forecasts ran high in testing, so read this as optimistic and as a lead to look into, "
    "not as proof. At 6 months the model is 'same as last month'."
)
SWITCHED_OFF = (
    "The live model check is switched off on this copy of the site. The forecasts above are the "
    "monthly job's stored output."
)
UNUSABLE = (
    "The live model service is set up on this site, but its address or key is not usable. The "
    "address must start with https:// and the key must not be empty. The site owner can fix both "
    "in the app's settings."
)


def setting_detail(address_found: bool, key_found: bool, address_https: bool) -> str:
    """Which of the two settings the site can read, as plain words (never a value)."""
    address = (
        "not found" if not address_found else ("found" if address_https else "found, no https://")
    )
    return f"Address: {address}. Key: {'found' if key_found else 'not found'}."


UNAVAILABLE_TAIL = "The forecast shown above is unchanged."


def specialties(view: pd.DataFrame) -> list[str]:
    """The specialties that have a forecast, the busiest first."""
    ordered = view.sort_values("origin_visits", ascending=False)
    return list(dict.fromkeys(ordered["specialty"]))


def age_bands(view: pd.DataFrame, specialty: str) -> list[str]:
    """The age bands that have a forecast for the specialty, the busiest first."""
    part = view[view["specialty"] == specialty].sort_values("origin_visits", ascending=False)
    return list(dict.fromkeys(part["age_band"]))


def genders(view: pd.DataFrame, specialty: str, age_band: str) -> list[str]:
    """The genders that have a forecast for the specialty and age band, the busiest first."""
    part = view[(view["specialty"] == specialty) & (view["age_band"] == age_band)]
    return list(dict.fromkeys(part.sort_values("origin_visits", ascending=False)["gender"]))


def stored_forecast(view: pd.DataFrame, specialty: str, age_band: str, gender: str) -> float | None:
    """The forecast the page shows for the segment, in percent, or None if it has none."""
    row = view[
        (view["specialty"] == specialty)
        & (view["age_band"] == age_band)
        & (view["gender"] == gender)
    ]
    return None if row.empty else float(row["forecast_pct"].iloc[0])


def compare(live_pct: float, stored_pct: float | None) -> dict:
    """Whether the live forecast equals the one on the page, and by how many points they differ."""
    if stored_pct is None:
        return {"status": "no_stored", "gap_pp": None}
    gap = abs(float(live_pct) - float(stored_pct))
    return {"status": "match" if gap < TOLERANCE_PP else "differs", "gap_pp": gap}


def data_versions(local: str | None, live: str | None) -> str:
    """'same', 'different' or 'unknown' for the website's data version against the service's."""
    if not local or not live or "unknown" in (local, live) or "mixed" in (local, live):
        return "unknown"
    return "same" if local == live else "different"


def local_data_version(pred: pd.DataFrame) -> str:
    """The data version the stored forecasts on the site carry."""
    if "data_version" not in pred.columns:
        return "unknown"
    versions = pred["data_version"].dropna().astype(str).unique()
    return str(versions[0]) if len(versions) == 1 else ("mixed" if len(versions) else "unknown")


def verdict(comparison: dict, versions: str) -> tuple[str, str]:
    """A level ('ok' or 'warn') and one sentence about how the live answer compares."""
    status = comparison["status"]
    if status == "match":
        text = "Same as the forecast above, to the 4 decimals the service reports."
        if versions == "different":
            return "warn", text + " The website and the service hold different data versions."
        return "ok", text
    if status == "differs":
        gap = comparison["gap_pp"]
        why = (
            " The website and the service hold different data versions, which would explain it."
            if versions == "different"
            else ""
        )
        return "warn", f"Different from the forecast above by {gap:.4f} points.{why}"
    return "warn", "The page has no stored forecast for this segment to compare with."


def unavailable(message: str) -> str:
    """What to say when the live service cannot answer."""
    return f"{message} {UNAVAILABLE_TAIL}"


def result_rows(live: dict, horizon_label: str) -> list[tuple[str, str]]:
    """The labelled values the panel lists for a live answer."""
    bias = live.get("known_bias_pct")
    rows = [
        (
            f"Live forecast, {horizon_label} ({live['target_month']})",
            f"{live['forecast_pct']:.2f}%",
        ),
        ("Last month's share", f"{live['last_month_pct']:.2f}%"),
        ("Change", f"{live['change_pp']:+.2f} points"),
        (
            "Visits last month",
            f"{int(live['origin_visits']):,}" + (" (small, noisy)" if live["small"] else ""),
        ),
        ("Model", f"{live['model']} ({live['model_version']})"),
    ]
    if bias is not None:
        rows.append(("Ran high in testing by", f"{bias:.1f}%"))
    return rows
