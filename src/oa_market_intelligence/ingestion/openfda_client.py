"""Client for looking up FDA approval dates via the openFDA Drugs@FDA API.

Method A of the combining strategy (docs/data_analysis_reference.md §6.3): for a given
product name, take the earliest original-approval (ORIG) submission date across every
application that lists it as a product. Retrieval is scoped to branded products this
project's own data actually tags, per docs/PROPOSAL.md §18.6 — never an externally
guessed list. Targeted live queries, not the 125 MB bulk file.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable

import pandas as pd
import requests

OPENFDA_URL = "https://api.fda.gov/drug/drugsfda.json"
ORIG_SUBMISSION_TYPE = "ORIG"
BRANDED_TAGS = frozenset({"BRAND", "BRANDED GENERIC"})

FetchFn = Callable[[str], dict]


class OpenFDAError(RuntimeError):
    """openFDA returned something this client cannot interpret."""


def _default_fetch(brand_name: str) -> dict:
    """Query openFDA for every application listing `brand_name` as a product.

    openFDA returns HTTP 404 with a JSON error body for zero matches (confirmed live,
    not documented behavior worth assuming) rather than HTTP 200 with an empty list.
    """
    response = requests.get(
        OPENFDA_URL,
        params={"search": f'products.brand_name:"{brand_name}"', "limit": 100},
        timeout=30,
    )
    if response.status_code == 404:
        return {"results": []}
    response.raise_for_status()
    return response.json()


def _parse_orig_date(raw: str, brand_name: str) -> date:
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        raise OpenFDAError(
            f"Unrecognized submission_status_date {raw!r} for {brand_name!r}"
        ) from None


def earliest_approval_date(brand_name: str, fetch: FetchFn = _default_fetch) -> date | None:
    """Return the earliest ORIG submission date for `brand_name`, or None if not found.

    An application matches only if `brand_name` is exactly one of its own products
    (case-insensitive) — openFDA's search can surface applications where the term
    appears on a different, unrelated product. An application with several ORIG
    submissions (re-filings) or no `submissions` field at all (seen live: NDA011602)
    is handled, not assumed away.
    """
    payload = fetch(brand_name)
    target = brand_name.strip().upper()
    dates = []
    for application in payload.get("results", []):
        products = application.get("products", [])
        names = {(p.get("brand_name") or "").strip().upper() for p in products}
        if target not in names:
            continue
        for submission in application.get("submissions", []):
            if submission.get("submission_type") != ORIG_SUBMISSION_TYPE:
                continue
            raw = submission.get("submission_status_date")
            if raw:
                dates.append(_parse_orig_date(raw, brand_name))
    return min(dates) if dates else None


def branded_products(reference_table: pd.DataFrame) -> list[str]:
    """Products this project's own data tags BRAND or BRANDED GENERIC on any row.

    A product with more than one tag across rows (docs/data_dictionary.md §4 — 7 OA
    products, e.g. ACETAMINOPHEN) is included if any row is branded: that is a lower bar
    than resolving dim_product.brand_generic_tag to one value (an open decision,
    database_schema.md), and an FDA lookup costs nothing to run on an extra product.
    """
    is_branded = reference_table["brand_generic_tag"].isin(BRANDED_TAGS)
    return sorted(reference_table.loc[is_branded, "product"].unique())


def build_approval_date_lookup(
    product_names: list[str], fetch: FetchFn = _default_fetch
) -> pd.DataFrame:
    """Look up every name in `product_names`, one openFDA query each.

    fda_approval_date is cast to datetime64 (pandas' usual date representation,
    matching every other date column in this project) rather than left as the object
    dtype a plain list of date/None values would otherwise produce.
    """
    records = [(name, earliest_approval_date(name, fetch=fetch)) for name in product_names]
    frame = pd.DataFrame(records, columns=["product", "fda_approval_date"])
    frame["fda_approval_date"] = pd.to_datetime(frame["fda_approval_date"])
    return frame
