"""A tiny hand-built Gold database for tests (see tests/test_serving_queries.py).

The first two months are fixed so expected numbers stay checkable by hand; any further
months repeat the second month's pattern.
"""

import calendar

from oa_market_intelligence.warehouse.schema import (
    create_schema,
    dim_demographics,
    dim_month,
    dim_specialty,
    gold_segment_adoption,
    gold_visit_share_monthly,
)

FIRST_TWO = (201908, 201909)


def _month_row(month_id):
    year, month = divmod(month_id, 100)
    return dict(
        month_id=month_id, calendar_date=f"{year}-{month:02d}-01", year=year,
        quarter=(month - 1) // 3 + 1, month_number=month, month_name=calendar.month_name[month],
    )


def build_tiny_gold(engine, month_ids=FIRST_TWO):
    """Create the schema on `engine` and fill it for the given months."""
    create_schema(engine)
    later = [m for m in month_ids if m not in FIRST_TWO]
    with engine.begin() as conn:
        if month_ids:
            conn.execute(dim_month.insert(), [_month_row(m) for m in month_ids])
        conn.execute(
            dim_specialty.insert(),
            [dict(specialty_id=1, specialty_name="A"), dict(specialty_id=2, specialty_name="B")],
        )
        conn.execute(
            dim_demographics.insert(),
            [
                dict(demographic_id=1, age_band="65 TO 74", gender="FEMALE"),
                dict(demographic_id=2, age_band="40 TO 59", gender="MALE"),
            ],
        )
        shares = [
            dict(month_id=m, branded_injectable_visits=20, generic_corticosteroid_visits=160,
                 nsaid_otc_visits=20, visit_share=0.10, direction_label="Flat")
            for m in month_ids
        ]
        if 201908 in month_ids:
            shares[0] = dict(month_id=201908, branded_injectable_visits=10,
                             generic_corticosteroid_visits=80, nsaid_otc_visits=10,
                             visit_share=0.10, direction_label=None)
        if shares:
            conn.execute(gold_visit_share_monthly.insert(), shares)

        segments = []
        if 201908 in month_ids:
            segments += [
                dict(month_id=201908, specialty_id=1, demographic_id=1,
                     branded_injectable_visits=8, total_category_visits=40,
                     segment_visit_share=0.2),
                dict(month_id=201908, specialty_id=2, demographic_id=2,
                     branded_injectable_visits=2, total_category_visits=60,
                     segment_visit_share=2 / 60),
            ]
        for m in [x for x in month_ids if x in FIRST_TWO[1:]] + later:
            segments += [
                dict(month_id=m, specialty_id=1, demographic_id=1,
                     branded_injectable_visits=20, total_category_visits=100,
                     segment_visit_share=0.2),
                dict(month_id=m, specialty_id=2, demographic_id=2,
                     branded_injectable_visits=0, total_category_visits=100,
                     segment_visit_share=0.0),
            ]
        if segments:
            conn.execute(gold_segment_adoption.insert(), segments)
    return engine
