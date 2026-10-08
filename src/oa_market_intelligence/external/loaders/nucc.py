"""NUCC provider taxonomy 26.1 and the specialty bridge (DL-59, step 4c).

The raw NUCC text (AMA copyright) is loaded only into the local `src_nucc_taxonomy` table. The
bridge that is published holds our own mapping: taxonomy code, one of the approved IQVIA specialty
groups (or OTHER) and the matching Medicare specialty name. The mapping is approximate for Sports
Medicine, Pain Medicine and Osteopathic Medicine, and feeds context columns only, never a pass or
fail rule.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.common import (
    finish_partition,
    now,
    replace_table,
    verified_entry,
)
from oa_market_intelligence.external.profile import iter_csv

FOLDER = "NUCC Taxonomy"
SOURCE = "nucc"
PAIN = ("Pain Medicine", "Interventional Pain Medicine")

MEDICARE_NAME = {
    "ANESTHESIOLOGY": "Anesthesiology",
    "FAMILY PRACTICE": "Family Practice",
    "INTERNAL MEDICINE": "Internal Medicine",
    "NURSE PRACTITIONER": "Nurse Practitioner",
    "ORTHOPEDIC SURGERY": "Orthopedic Surgery",
    "OSTEOPATHIC MEDICINE": "Osteopathic Manipulative Medicine",
    "PHYSICAL MEDICINE & REHAB": "Physical Medicine and Rehabilitation",
    "PHYSICIAN ASSISTANT": "Physician Assistant",
    "RHEUMATOLOGY": "Rheumatology",
    "SPORTS MEDICINE": "Sports Medicine",
}


def classify_taxonomy(classification: str, specialization: str) -> str:
    """The approved IQVIA specialty group for a NUCC classification and specialization, or OTHER.

    Rules are applied in order, so a pain or sports specialization wins over its classification."""
    c, s = classification.strip(), specialization.strip()
    if c == "Podiatrist":
        return "OTHER"
    if s in PAIN or c == "Pain Medicine":
        return "PAIN MEDICINE"
    if s == "Sports Medicine" or c == "Neuromusculoskeletal Medicine, Sports Medicine":
        return "SPORTS MEDICINE"
    if c == "Anesthesiology" and not s:
        return "ANESTHESIOLOGY"
    if c == "Family Medicine" and not s:
        return "FAMILY PRACTICE"
    if c == "Internal Medicine":
        return {"": "INTERNAL MEDICINE", "Rheumatology": "RHEUMATOLOGY"}.get(s, "OTHER")
    if c == "Nurse Practitioner":
        return "NURSE PRACTITIONER"
    if c == "Physician Assistant":
        return "PHYSICIAN ASSISTANT"
    if c == "Orthopaedic Surgery":
        return "OTHER" if s == "Orthopaedic Surgery of the Spine" else "ORTHOPEDIC SURGERY"
    if c == "Neuromusculoskeletal Medicine & OMM":
        return "OSTEOPATHIC MEDICINE"
    if c == "Physical Medicine & Rehabilitation":
        return "PHYSICAL MEDICINE & REHAB"
    return "OTHER"


def taxonomy_bridge(nucc: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for code, classification, specialization in zip(
        nucc["Code"], nucc["Classification"], nucc["Specialization"], strict=True
    ):
        group = classify_taxonomy(classification, specialization)
        if group == "PAIN MEDICINE":
            medicare = (
                "Interventional Pain Management"
                if specialization.strip() == "Interventional Pain Medicine"
                else "Pain Management"
            )
        else:
            medicare = MEDICARE_NAME.get(group)
        rows.append({"taxonomy_code": code, "specialty_group": group, "medicare_name": medicare})
    return pd.DataFrame(rows)


def load_nucc(engine: Engine, raw_root: Path) -> int:
    path = next((Path(raw_root) / FOLDER).glob("*.csv"))
    started = now()
    verified_entry(raw_root, path)
    nucc = pd.concat(list(iter_csv(path)), ignore_index=True)
    source = pd.DataFrame(
        {
            "code": nucc["Code"],
            "grouping": nucc["Grouping"],
            "classification": nucc["Classification"],
            "specialization": nucc["Specialization"],
            "display_name": nucc["Display Name"],
        }
    )
    replace_table(engine, "bridge_taxonomy_specialty", taxonomy_bridge(nucc))
    return finish_partition(
        engine,
        raw_root,
        source=SOURCE,
        year=None,
        path=path,
        table="src_nucc_taxonomy",
        frame=source,
        rows_read=len(nucc),
        where={},
        started_at=started,
    )
