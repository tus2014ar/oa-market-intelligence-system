# Silver & Gold Layer Data Dictionary

This document explains what each column in the Silver (star schema) and Gold (serving) tables *means* and where its value comes from — field-by-field business meaning, not SQL structure. For the *structure* (types, constraints, keys), see [`database_schema.md`](database_schema.md). For the *raw source* fields these are ultimately derived from, see [`data_dictionary.md`](data_dictionary.md) (Bronze layer). Three documents, three purposes:

| Document | Answers |
|---|---|
| `data_dictionary.md` | "What does the raw Excel/JSON file actually contain?" (Bronze) |
| `database_schema.md` | "What are the exact tables, types, and constraints?" (DDL) |
| `silver_gold_data_dictionary.md` (this doc) | "What does each column *mean*, and how is its value derived?" |

---

## 1. Silver Layer (Star Schema)

### 1.1 `dim_month`

| Column | Meaning | Values / Range | Derived From |
|---|---|---|---|
| `month_id` | Deterministic identifier for a calendar month, used as the join key everywhere else in the schema | Integer, `YYYYMM` format (e.g., `201908` = August 2019) | Computed from the raw pivot's Month row value (`data_dictionary.md` §2.1) |
| `calendar_date` | The month, as a real date, for date-math and charting | ISO date, first of month (`'2019-08-01'`) | Same source as `month_id`, reformatted |
| `year` | Calendar year | 2019–2025 (grows by one each Jan) | Derived from `calendar_date` |
| `quarter` | Calendar quarter | 1–4 | Derived from `calendar_date` |
| `month_number` | Month of year | 1–12 | Derived from `calendar_date` |
| `month_name` | Human-readable month name, for display | `"August"`, etc. | Derived from `calendar_date` |

### 1.2 `dim_product`

| Column | Meaning | Values / Range | Derived From |
|---|---|---|---|
| `product_id` | Surrogate key for the product, stable across monthly refreshes (§6 of `database_schema.md`) | Autoincrement integer | Assigned on first sight of a new `product_name` |
| `product_name` | The drug/product name as it appears in the source data | e.g., `"ZILRETTA"` | Raw pivot Product row (`data_dictionary.md` §2.1) |
| `manufacturer` | Manufacturer name — **informational display only**, never used for grouping or share calculations, because a single product can carry multiple manufacturer labels over time (the Zilretta case, §10 of `PROPOSAL.md`) | e.g., `"PACIRA PHARM"`, `"No Manufacturer"` | Raw pivot Manufacturer row, most recent value seen |
| `brand_generic_tag` | The raw patent/ownership tag — explicitly **not** a therapeutic classification (§10 of `PROPOSAL.md`) | `BRAND`, `GENERIC`, `BRANDED GENERIC`, `OTHER` | Brand/Generic reference file (`data_dictionary.md` §4) |
| `disease_area` | Which disease scope this product belongs to | `OA` or `RA` | Determined by which pivot file/sheet the product came from |
| `treatment_category` | The actual competitive-category classification the visit-share formula depends on (§18.1 of `PROPOSAL.md`) — this is the field the raw `brand_generic_tag` cannot substitute for | `branded_injectable`, `generic_corticosteroid`, `nsaid_otc`, `opioid_other`, `unclassified` | Looked up from `data/reference/product_taxonomy.csv` (§18.10); `unclassified` + an alert if the product isn't in that file yet |
| `fda_approval_date` | The product's original FDA approval date, if retrieved | ISO date, nullable | openFDA, Method A lookup (§6.3 of `data_analysis_reference.md`) — only populated for branded products actually queried (§18.6 of `PROPOSAL.md`) |

### 1.3 `dim_specialty`

| Column | Meaning | Values / Range | Derived From |
|---|---|---|---|
| `specialty_id` | Surrogate key for a medical specialty | Autoincrement integer | Assigned on first sight of a new `specialty_name` |
| `specialty_name` | The prescribing specialty recorded for a visit | 50 distinct values for OA, 19 for RA (e.g., `"ORTHOPEDIC SURGERY"`, `"RHEUMATOLOGY"`) | Raw pivot column-header first segment (`data_dictionary.md` §2.2) |

### 1.4 `dim_demographics`

| Column | Meaning | Values / Range | Derived From |
|---|---|---|---|
| `demographic_id` | Surrogate key for one (age band, gender) combination | Autoincrement integer | Assigned on first sight of a new pair |
| `age_band` | Patient age bracket | `00 TO 02` … `85 +`, plus `UNSPECIFIED` (OA only) | Raw pivot column-header segment |
| `gender` | Patient gender as recorded | `MALE`, `FEMALE`, `UNSPECIFIED` | Raw pivot column-header segment |

### 1.5 `fact_product_visits`

| Column | Meaning | Values / Range | Derived From |
|---|---|---|---|
| `month_id`, `product_id`, `specialty_id`, `demographic_id` | The four dimensions that together identify one observed slice of data | FKs to the dimension tables above | — |
| `patient_visits` | The number of patient visits recorded for this exact (month, product, specialty, age, gender) combination | Non-negative integer | Raw pivot cell value (`data_dictionary.md` §2.4) — this is the one genuinely *observed* number in the whole schema; everything else is a lookup, a key, or a later computation |

### 1.6 `fact_place_of_service_visits`

| Column | Meaning | Values / Range | Derived From |
|---|---|---|---|
| `month_id` | The month | FK to `dim_month` | — |
| `disease_area` | Which market this row describes | `OA` or `RA` | Which raw file the secondary sheet came from |
| `place_of_service` | Where the visit occurred — market-level, not tied to any specific product | `HOSPITAL`, `OFFICE`, `OTHER`, `TELEHEALTH` | Raw Place-of-Service secondary sheet (`data_dictionary.md` §3) |
| `patient_visits` | Visit count for that month/setting, across the entire OA or RA market | Non-negative integer | Same secondary sheet |

---

## 2. Gold Layer (Serving Tables)

### 2.1 `gold_visit_share_monthly` — feeds Objective 2 (the core classifier)

| Column | Meaning | Values / Range | Computed From |
|---|---|---|---|
| `month_id` | The month this row describes — one row per month, the table's entire grain | FK to `dim_month` | — |
| `branded_injectable_visits` | Total visits that month for the one no-generic-equivalent branded injectable (Zilretta) | Non-negative integer | `SUM(patient_visits)` from `fact_product_visits` joined to `dim_product` where `treatment_category = 'branded_injectable'` |
| `generic_corticosteroid_visits` | Total visits that month across all 52 generic-equivalent corticosteroid products | Non-negative integer | Same aggregation, `treatment_category = 'generic_corticosteroid'` |
| `nsaid_otc_visits` | Total visits that month across all 54 NSAID/OTC products | Non-negative integer | Same aggregation, `treatment_category = 'nsaid_otc'` |
| `visit_share` | The core target metric: branded injectable's share of the three-category total | Ratio, 0.0–1.0 | §18.1 formula: `branded / (branded + generic_corticosteroid + nsaid_otc)` |
| `direction_label` | Ground-truth classification label — how `visit_share` moved vs. the prior month | `Up`, `Down`, `Flat` | §18.2 threshold (±1.0 percentage point) applied to month-over-month `visit_share` change |
| `visit_share_lag_1/2/3` | `visit_share` from 1, 2, 3 months prior — the single highest-priority engineered feature (§4.1 of `data_analysis_reference.md`) | Ratio, 0.0–1.0 | Window function over this same table, ordered by `month_id` |
| `visit_share_roll_3mo`, `_roll_6mo` | Rolling average of `visit_share` over the trailing 3 or 6 months — captures acceleration/deceleration | Ratio, 0.0–1.0 | Rolling window over this same table |
| `months_since_launch` | Months elapsed since the branded injectable's FDA approval date | Integer (currently large/constant — §6.5 caveat: approval predates the data window) | `month_id` minus `dim_product.fda_approval_date` (Method B, §6.3) |
| `is_post_launch` | Whether the product had already launched by this month | `0` or `1` | Derived from `months_since_launch` |
| `competitor_count_on_market` | Count of no-generic-equivalent branded products with an approval date on or before this month | Non-negative integer | Joins across all `dim_product` rows tagged as no-generic-equivalent branded, filtered by `fda_approval_date <= calendar_date` |
| `months_since_last_competitor_event` | Time since the most recent competitor approval or major label event | Integer | Derived from the competitor set's approval dates |
| `hospital_visits`, `office_visits`, `other_visits`, `telehealth_visits` | Market-level visit counts by care setting that month — context, not per-product | Non-negative integer | `fact_place_of_service_visits` for `disease_area = 'OA'`, that month |
| `predicted_direction` | The model's prediction for *next* month's `direction_label`, made using data available through this month | `Up`, `Down`, `Flat`, nullable | Written by the modeling stage after a prediction run (§17.2) |
| `prediction_probability` | Model's confidence in `predicted_direction` | 0.0–1.0, nullable | Model output |
| `model_version` | Which registered model version produced this prediction, for auditability | Text (MLflow registry version), nullable | Model output |
| `actual_direction` | What `direction_label` actually turned out to be, filled in retroactively once that month's real data lands | `Up`, `Down`, `Flat`, nullable | Copied from this table's own `direction_label` for the corresponding month, once available — used by monitoring (§17.4) to check predictions against reality |

### 2.2 `gold_segment_adoption` — feeds Objective 3 (stretch: segment-level adoption)

| Column | Meaning | Values / Range | Computed From |
|---|---|---|---|
| `month_id`, `specialty_id`, `demographic_id` | The segment this row describes — one row per (month, specialty, age/gender) combination | FKs | — |
| `branded_injectable_visits` | Visits to the branded injectable within this specific segment that month | Non-negative integer | `SUM(patient_visits)` from `fact_product_visits`, filtered to this segment and `treatment_category = 'branded_injectable'` |
| `total_category_visits` | Total visits across all three treatment categories within this segment that month | Non-negative integer | Same aggregation, all three categories summed |
| `segment_visit_share` | This segment's version of the core `visit_share` metric | Ratio, 0.0–1.0 | `branded_injectable_visits / total_category_visits` |
| `adoption_label` | Whether this segment is a High- or Low-adoption segment for the branded injectable | `High`, `Low`, nullable | Written by the Objective 3 model once it runs — not computed by a fixed rule, since "high" vs. "low" is a classification output |
