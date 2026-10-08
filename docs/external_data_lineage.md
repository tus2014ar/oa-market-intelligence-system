# External data lineage (DL-59)

Every column of the external database `data/processed/external.db`, with the source file and original column it comes from and the rule applied. The schema is `src/oa_market_intelligence/external/schema.py`; a test fails if any column is missing here or documented here but absent from the schema. Protocol: [`external_data_protocol.md`](external_data_protocol.md). Raw files are described in the profiling notebook [`10_external_raw_data_profile`](../notebooks/10_external_raw_data_profile.ipynb).

Tables marked *local only* stay on this machine; all others are copied into the committed published database. Joins to the IQVIA warehouse use natural keys (month `YYYYMM`, specialty name, two-letter state), not foreign keys.

## `dim_state`

Grain: one row per state, DC or territory.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `dim_state.state_code` | Hand-built reference (protocol) | Census state list | two-letter code |
| `dim_state.state_name` | Hand-built reference (protocol) | Census state list | as is |
| `dim_state.state_fips` | Hand-built reference (protocol) | Census state list | two-digit FIPS code; the key the CMS geography files use |
| `dim_state.is_us_state_or_dc` | Hand-built reference (protocol) | Census state list | 1 for the 50 states and DC, else 0 |

## `dim_year`

Grain: one row per calendar year.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `dim_year.year` | Derived by the loader | years present in the loaded facts | integer year |

## `dim_quarter`

Grain: one row per calendar quarter.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `dim_quarter.quarter_id` | Derived by the loader | calendar | text such as 2021Q2 |
| `dim_quarter.year` | Derived by the loader | calendar | year of the quarter |
| `dim_quarter.quarter` | Derived by the loader | calendar | 1 to 4 |
| `dim_quarter.first_month_id` | Derived by the loader | calendar | YYYYMM of the quarter's first month; joins dim_month in the IQVIA warehouse |
| `dim_quarter.last_month_id` | Derived by the loader | calendar | YYYYMM of the quarter's last month |

## `dim_hcpcs_code`

Grain: one row per approved billing code.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `dim_hcpcs_code.hcpcs_code` | external/codes.py | ALL_CODES | the 27 approved codes, as text |
| `dim_hcpcs_code.code_group` | external/codes.py | CODE_GROUPS | A primary, B hyaluronic context, C procedures, D IV steroids |
| `dim_hcpcs_code.drug_family` | external/codes.py | DRUG_FAMILY | drug family; methylprednisolone acetate is one family |
| `dim_hcpcs_code.is_cpt` | external/codes.py | code format | 1 for the two CPT procedure codes (20610, 20611), else 0 |
| `dim_hcpcs_code.short_description` | Part B by Geography and Service (CMS) | HCPCS_Desc | latest wording from the committed profiling output of the Part B geography file; J-codes only, NULL for CPT codes (AMA copyright), enforced by a constraint |
| `dim_hcpcs_code.first_year_seen` | Part B by Geography and Service (CMS) | year with a row for the code | earliest year with a row in the Part B geography profile (data/reference/external_profile) |
| `dim_hcpcs_code.last_year_seen` | Part B by Geography and Service (CMS) | year with a row for the code | latest year with a row in the Part B geography profile |

## `dim_event`

Grain: one row per dated event.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `dim_event.event_id` | Derived by the loader | sequence | autoincrement |
| `dim_event.event_date` | Hand-built reference (protocol) | events reference file | ISO date of the event or its start |
| `dim_event.event_end_date` | Hand-built reference (protocol) | events reference file | ISO end date where the event has a window; otherwise empty |
| `dim_event.event_type` | Hand-built reference (protocol) | events reference file | regulatory, payment, corporate, data, guideline |
| `dim_event.description` | Hand-built reference (protocol) | events reference file | plain-language description |
| `dim_event.source` | Hand-built reference (protocol) | events reference file | document or filing the date comes from |
| `dim_event.verified` | Hand-built reference (protocol) | events reference file | 1 if confirmed from a primary document, else 0 |

## `bridge_specialty_crosswalk`

Grain: one row per IQVIA group and Medicare specialty name.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `bridge_specialty_crosswalk.iqvia_group` | Hand-built reference (protocol) | approved crosswalk (protocol) | IQVIA specialty group name |
| `bridge_specialty_crosswalk.medicare_name` | Hand-built reference (protocol) | approved crosswalk (protocol) | Medicare specialty name; NONE for the excluded group |
| `bridge_specialty_crosswalk.relationship` | Hand-built reference (protocol) | approved crosswalk (protocol) | exact, combined, weak or excluded |
| `bridge_specialty_crosswalk.note` | Hand-built reference (protocol) | approved crosswalk (protocol) | free text, for example 'weak: different definitions' |

## `bridge_drug_family`

Grain: one row per drug family and IQVIA product name.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `bridge_drug_family.drug_family` | external/codes.py | DRUG_FAMILY | family name |
| `bridge_drug_family.iqvia_product_name` | Hand-built reference (protocol) | data/reference/product_taxonomy.csv | IQVIA product names belonging to the family |

## `bridge_taxonomy_specialty`

Grain: one row per provider taxonomy code.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `bridge_taxonomy_specialty.taxonomy_code` | NUCC taxonomy 26.1 | Code | ten-character taxonomy code |
| `bridge_taxonomy_specialty.specialty_group` | Hand-built reference (protocol) | mapping built from the NUCC classification and specialization | an IQVIA group or OTHER; approximate for Sports Medicine, Pain Medicine and Osteopathic Medicine |
| `bridge_taxonomy_specialty.medicare_name` | Hand-built reference (protocol) | mapping built from the NUCC classification | matching Medicare specialty name, if any |

## `src_nucc_taxonomy` *(local only)*

Grain: one row per NUCC code (local only: AMA copyright).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `src_nucc_taxonomy.code` | NUCC taxonomy 26.1 | Code | as is |
| `src_nucc_taxonomy.grouping` | NUCC taxonomy 26.1 | Grouping | as is |
| `src_nucc_taxonomy.classification` | NUCC taxonomy 26.1 | Classification | as is |
| `src_nucc_taxonomy.specialization` | NUCC taxonomy 26.1 | Specialization | as is, blank when none |
| `src_nucc_taxonomy.display_name` | NUCC taxonomy 26.1 | Display Name | as is |

## `bronze_external_files`

Grain: one row per downloaded file.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `bronze_external_files.file_id` | Derived by the loader | sequence | autoincrement |
| `bronze_external_files.dataset` | Download manifest | dataset | as is |
| `bronze_external_files.relative_path` | Download manifest | file | path under data/raw/New Datasets |
| `bronze_external_files.source_url` | Download manifest | url | download link as recorded |
| `bronze_external_files.bytes` | Download manifest | bytes | size when downloaded |
| `bronze_external_files.sha256` | Download manifest | sha256 | checksum when downloaded |
| `bronze_external_files.status` | Download manifest | status | downloaded, downloaded (filtered) or manual |
| `bronze_external_files.downloaded_at` | Download manifest | at | timestamp |
| `bronze_external_files.rows_loaded` | Derived by the loader | loader result | rows loaded into a fact table from this file |
| `bronze_external_files.loaded_at` | Derived by the loader | loader result | timestamp of the load |

## `external_load_runs` *(local only)*

Grain: one row per loader run (local only).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `external_load_runs.run_id` | Derived by the loader | sequence | autoincrement |
| `external_load_runs.source` | Derived by the loader | loader | source name |
| `external_load_runs.data_year` | Derived by the loader | loader | year loaded, if the source is yearly |
| `external_load_runs.rows_read` | Derived by the loader | loader | rows read from the file |
| `external_load_runs.rows_loaded` | Derived by the loader | loader | rows written after rules R1 to R13 |
| `external_load_runs.status` | Derived by the loader | loader | ok or failed |
| `external_load_runs.started_at` | Derived by the loader | loader | timestamp |
| `external_load_runs.finished_at` | Derived by the loader | loader | timestamp |
| `external_load_runs.note` | Derived by the loader | loader | free text, for example rows dropped by a rule |

## `fact_ext_partb_provider` *(local only)*

Grain: year x NPI x code x setting; individual and organisation rows (local only).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_partb_provider.year` | Part B by Provider and Service (CMS) | file year | year of the release |
| `fact_ext_partb_provider.npi` | Part B by Provider and Service (CMS) | Rndrng_NPI | text |
| `fact_ext_partb_provider.hcpcs_code` | Part B by Provider and Service (CMS) | HCPCS_Cd | text, only the approved codes |
| `fact_ext_partb_provider.setting` | Part B by Provider and Service (CMS) | Place_Of_Srvc | O office, F facility |
| `fact_ext_partb_provider.specialty_cms` | Part B by Provider and Service (CMS) | Rndrng_Prvdr_Type | as is; mapped to IQVIA groups through the crosswalk |
| `fact_ext_partb_provider.entity_type` | Part B by Provider and Service (CMS) | Rndrng_Prvdr_Ent_Cd | I individual, O organisation; organisations are excluded from provider counts |
| `fact_ext_partb_provider.state_code` | Part B by Provider and Service (CMS) | Rndrng_Prvdr_State_Abrvtn | two-letter code as is |
| `fact_ext_partb_provider.medicare_participating` | Part B by Provider and Service (CMS) | Rndrng_Prvdr_Mdcr_Prtcptg_Ind | Y or N |
| `fact_ext_partb_provider.benes` | Part B by Provider and Service (CMS) | Tot_Benes | patients; CMS suppresses cells of 10 or fewer, so the minimum is 11 |
| `fact_ext_partb_provider.services` | Part B by Provider and Service (CMS) | Tot_Srvcs | number as is (billing units, never compared across codes) |
| `fact_ext_partb_provider.bene_day_services` | Part B by Provider and Service (CMS) | Tot_Bene_Day_Srvcs | number as is |
| `fact_ext_partb_provider.avg_submitted_charge` | Part B by Provider and Service (CMS) | Avg_Sbmtd_Chrg | number as is (average submitted charge per service) |
| `fact_ext_partb_provider.avg_allowed_amt` | Part B by Provider and Service (CMS) | Avg_Mdcr_Alowd_Amt | number as is (average Medicare allowed amount per service) |
| `fact_ext_partb_provider.avg_payment_amt` | Part B by Provider and Service (CMS) | Avg_Mdcr_Pymt_Amt | number as is (average Medicare payment per service) |
| `fact_ext_partb_provider.avg_standardized_amt` | Part B by Provider and Service (CMS) | Avg_Mdcr_Stdzd_Amt | number as is (average standardised payment per service) |

## `fact_ext_partb_geo`

Grain: year x geography level x geography code x code x setting.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_partb_geo.year` | Part B by Geography and Service (CMS) | file year | year of the release |
| `fact_ext_partb_geo.geo_level` | Part B by Geography and Service (CMS) | Rndrng_Prvdr_Geo_Lvl | National or State |
| `fact_ext_partb_geo.geo_code` | Part B by Geography and Service (CMS) | Rndrng_Prvdr_Geo_Cd | FIPS code for a state (join dim_state.state_fips); US for national; UNKNOWN for the blank-code placeholder |
| `fact_ext_partb_geo.hcpcs_code` | Part B by Geography and Service (CMS) | HCPCS_Cd | text, only the approved codes |
| `fact_ext_partb_geo.setting` | Part B by Geography and Service (CMS) | Place_Of_Srvc | O office, F facility |
| `fact_ext_partb_geo.geo_desc` | Part B by Geography and Service (CMS) | Rndrng_Prvdr_Geo_Desc | state name as is |
| `fact_ext_partb_geo.drug_indicator` | Part B by Geography and Service (CMS) | HCPCS_Drug_Ind | Y or N |
| `fact_ext_partb_geo.n_providers` | Part B by Geography and Service (CMS) | Tot_Rndrng_Prvdrs | number of rendering providers in the cell |
| `fact_ext_partb_geo.benes` | Part B by Geography and Service (CMS) | Tot_Benes | patients; suppressed below 11 |
| `fact_ext_partb_geo.services` | Part B by Geography and Service (CMS) | Tot_Srvcs | number as is |
| `fact_ext_partb_geo.bene_day_services` | Part B by Geography and Service (CMS) | Tot_Bene_Day_Srvcs | number as is |
| `fact_ext_partb_geo.avg_submitted_charge` | Part B by Geography and Service (CMS) | Avg_Sbmtd_Chrg | number as is (average submitted charge per service) |
| `fact_ext_partb_geo.avg_allowed_amt` | Part B by Geography and Service (CMS) | Avg_Mdcr_Alowd_Amt | number as is (average Medicare allowed amount per service) |
| `fact_ext_partb_geo.avg_payment_amt` | Part B by Geography and Service (CMS) | Avg_Mdcr_Pymt_Amt | number as is (average Medicare payment per service) |
| `fact_ext_partb_geo.avg_standardized_amt` | Part B by Geography and Service (CMS) | Avg_Mdcr_Stdzd_Amt | number as is (average standardised payment per service) |

## `fact_ext_partd_geo`

Grain: year x geography level x geography code x brand x generic.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_partd_geo.year` | Part D by Geography and Drug (CMS) | file year | year of the release |
| `fact_ext_partd_geo.geo_level` | Part D by Geography and Drug (CMS) | Prscrbr_Geo_Lvl | National or State |
| `fact_ext_partd_geo.geo_code` | Part D by Geography and Drug (CMS) | Prscrbr_Geo_Cd | FIPS code; US for national; UNKNOWN for the blank-code placeholder |
| `fact_ext_partd_geo.brand_name` | Part D by Geography and Drug (CMS) | Brnd_Name | as is |
| `fact_ext_partd_geo.generic_name` | Part D by Geography and Drug (CMS) | Gnrc_Name | as is; the stable key for the fixed drug lists |
| `fact_ext_partd_geo.drug_family` | Hand-built reference (protocol) | fixed lists (protocol) | nsaid or oral_steroid, from the generic name |
| `fact_ext_partd_geo.n_prescribers` | Part D by Geography and Drug (CMS) | Tot_Prscrbrs | number as is |
| `fact_ext_partd_geo.claims` | Part D by Geography and Drug (CMS) | Tot_Clms | number as is |
| `fact_ext_partd_geo.fills_30day` | Part D by Geography and Drug (CMS) | Tot_30day_Fills | 30-day standardised fills |
| `fact_ext_partd_geo.total_drug_cost` | Part D by Geography and Drug (CMS) | Tot_Drug_Cst | dollars as is |
| `fact_ext_partd_geo.benes` | Part D by Geography and Drug (CMS) | Tot_Benes | patients; blank when suppressed |
| `fact_ext_partd_geo.ge65_claims` | Part D by Geography and Drug (CMS) | GE65_Tot_Clms | claims for age 65 and over; blank when suppressed |
| `fact_ext_partd_geo.ge65_fills_30day` | Part D by Geography and Drug (CMS) | GE65_Tot_30day_Fills | as is |
| `fact_ext_partd_geo.ge65_drug_cost` | Part D by Geography and Drug (CMS) | GE65_Tot_Drug_Cst | as is |
| `fact_ext_partd_geo.ge65_benes` | Part D by Geography and Drug (CMS) | GE65_Tot_Benes | blank when suppressed |
| `fact_ext_partd_geo.ge65_suppression_flag` | Part D by Geography and Drug (CMS) | GE65_Sprsn_Flag | as is |
| `fact_ext_partd_geo.ge65_bene_suppression_flag` | Part D by Geography and Drug (CMS) | GE65_Bene_Sprsn_Flag | as is |

## `fact_ext_asp_price`

Grain: quarter x billing code.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_asp_price.quarter_id` | Part B payment limit (ASP) files (CMS) | file name | quarter from the file's month and year, for example 2021Q2 |
| `fact_ext_asp_price.hcpcs_code` | Part B payment limit (ASP) files (CMS) | HCPCS Code | text; header found by name (five layouts) |
| `fact_ext_asp_price.short_description` | Part B payment limit (ASP) files (CMS) | Short Description | as is |
| `fact_ext_asp_price.dosage` | Part B payment limit (ASP) files (CMS) | HCPCS Code Dosage | for example '1 MG' or '10 MG'; the unit the limit refers to |
| `fact_ext_asp_price.payment_limit` | Part B payment limit (ASP) files (CMS) | Payment Limit | dollars per dosage unit; blank becomes NULL |
| `fact_ext_asp_price.coinsurance_pct` | Part B payment limit (ASP) files (CMS) | Co-insurance Percentage | present only in 2023Q2 onward; otherwise NULL |
| `fact_ext_asp_price.notes` | Part B payment limit (ASP) files (CMS) | Notes | as is |
| `fact_ext_asp_price.source_file` | Download manifest | file | file name the row came from |
| `fact_ext_asp_price.source_release` | Part B payment limit (ASP) files (CMS) | file name | the 'updated' date in the file name, where present |

## `fact_ext_openpay_month`

Grain: month x product x recipient type.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_openpay_month.month_id` | Open Payments general payments (CMS) | Date_of_Payment | YYYYMM of the payment date; the date must fall inside Program_Year, else the record is excluded |
| `fact_ext_openpay_month.product` | Open Payments general payments (CMS) | Name_of_Drug_or_Biological_..._1 to 5 | Zilretta or a normalised hyaluronic product; matched in any of the five slots, never by payer |
| `fact_ext_openpay_month.recipient_type` | Open Payments general payments (CMS) | Covered_Recipient_Type | physician, non_physician_practitioner or teaching_hospital |
| `fact_ext_openpay_month.n_records` | Open Payments general payments (CMS) | Record_ID | count of payment records |
| `fact_ext_openpay_month.n_distinct_recipients` | Open Payments general payments (CMS) | Covered_Recipient_NPI | distinct recipients with at least one payment in the month |
| `fact_ext_openpay_month.total_amount_usd` | Open Payments general payments (CMS) | Total_Amount_of_Payment_USDollars | sum of dollars |
| `fact_ext_openpay_month.n_payments_counted` | Open Payments general payments (CMS) | Number_of_Payments_Included_in_Total_Amount | sum; zero-count records are flagged |
| `fact_ext_openpay_month.n_flagged_records` | Open Payments general payments (CMS) | Total_Amount_of_Payment_USDollars, Number_of_Payments_Included_in_Total_Amount | records kept but flagged: zero dollars or zero payments counted (R8). Records dated outside their program year (for example year 0002) are dropped and tallied in the run log |

## `fact_ext_openpay_nature`

Grain: month x product x recipient type x nature of payment.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_openpay_nature.month_id` | Open Payments general payments (CMS) | Date_of_Payment | YYYYMM, as above |
| `fact_ext_openpay_nature.product` | Open Payments general payments (CMS) | Name_of_Drug_or_Biological_..._1 to 5 | as above |
| `fact_ext_openpay_nature.recipient_type` | Open Payments general payments (CMS) | Covered_Recipient_Type | as above |
| `fact_ext_openpay_nature.nature_of_payment` | Open Payments general payments (CMS) | Nature_of_Payment_or_Transfer_of_Value | as is (16 values) |
| `fact_ext_openpay_nature.n_records` | Open Payments general payments (CMS) | Record_ID | count of payment records |
| `fact_ext_openpay_nature.total_amount_usd` | Open Payments general payments (CMS) | Total_Amount_of_Payment_USDollars | sum of dollars |

## `fact_ext_company_revenue`

Grain: period end x company x product.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_company_revenue.period_end` | SEC 10-K and 10-Q filings (hand-transcribed) | income statement period | ISO date of the period end |
| `fact_ext_company_revenue.company` | SEC 10-K and 10-Q filings (hand-transcribed) | filer | Flexion Therapeutics or Pacira BioSciences (Zilretta); Bioventus or Anika (competitors) |
| `fact_ext_company_revenue.product` | SEC 10-K and 10-Q filings (hand-transcribed) | revenue table row | Zilretta, or the competitor product line |
| `fact_ext_company_revenue.period_type` | SEC 10-K and 10-Q filings (hand-transcribed) | income statement period | quarter, nine_months or year |
| `fact_ext_company_revenue.fiscal_label` | SEC 10-K and 10-Q filings (hand-transcribed) | income statement period | for example 2021Q3 or FY2022 |
| `fact_ext_company_revenue.net_sales_usd` | SEC 10-K and 10-Q filings (hand-transcribed) | net product sales | dollars, transcribed by hand |
| `fact_ext_company_revenue.source_accession` | SEC 10-K and 10-Q filings (hand-transcribed) | filing accession number | for example 0001564590-21-012050 |
| `fact_ext_company_revenue.source_form` | SEC 10-K and 10-Q filings (hand-transcribed) | form type | 10-K or 10-Q |
| `fact_ext_company_revenue.source_page` | SEC 10-K and 10-Q filings (hand-transcribed) | page or section | where the figure appears |
| `fact_ext_company_revenue.derived` | Hand-built reference (protocol) | calculation | 1 when computed (fourth quarter = year minus nine months), else 0 |
| `fact_ext_company_revenue.note` | SEC 10-K and 10-Q filings (hand-transcribed) | transcriber note | free text |

## `fact_ext_geo_variation`

Grain: year x geography level x geography code x age level.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_geo_variation.year` | Medicare Geographic Variation (CMS) | YEAR | as is |
| `fact_ext_geo_variation.geo_level` | Medicare Geographic Variation (CMS) | BENE_GEO_LVL | National or State; county rows are not loaded |
| `fact_ext_geo_variation.geo_code` | Medicare Geographic Variation (CMS) | BENE_GEO_CD | FIPS code for a state; placeholder rows with no code are dropped (R3) |
| `fact_ext_geo_variation.age_level` | Medicare Geographic Variation (CMS) | BENE_AGE_LVL | All, <65 or >=65 |
| `fact_ext_geo_variation.geo_desc` | Medicare Geographic Variation (CMS) | BENE_GEO_DESC | two-letter state abbreviation or National |
| `fact_ext_geo_variation.benes_total` | Medicare Geographic Variation (CMS) | BENES_TOTAL_CNT | number; * becomes NULL |
| `fact_ext_geo_variation.benes_ffs_ab` | Medicare Geographic Variation (CMS) | BENES_WTH_PTAPTB_CNT | number; * becomes NULL |
| `fact_ext_geo_variation.benes_original_medicare` | Medicare Geographic Variation (CMS) | BENES_OM_CNT | number; * becomes NULL |
| `fact_ext_geo_variation.benes_ma` | Medicare Geographic Variation (CMS) | BENES_MA_CNT | number; * becomes NULL |
| `fact_ext_geo_variation.ma_participation_rate` | Medicare Geographic Variation (CMS) | MA_PRTCPTN_RATE | 0 to 1; * means suppressed and becomes NULL |
| `fact_ext_geo_variation.avg_age` | Medicare Geographic Variation (CMS) | BENE_AVG_AGE | number; * becomes NULL |

## `fact_ext_provider_counts`

Grain: snapshot x state x primary taxonomy code.

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_provider_counts.snapshot_date` | NPPES registry, Sept 2026 (CMS) | file name | date of the monthly file (September 2026) |
| `fact_ext_provider_counts.state_code` | NPPES registry, Sept 2026 (CMS) | Provider Business Practice Location Address State Name | cleaned to a two-letter state (R11); unmapped values are dropped |
| `fact_ext_provider_counts.taxonomy_code` | NPPES registry, Sept 2026 (CMS) | Healthcare Provider Taxonomy Code_1 | primary taxonomy code as is |
| `fact_ext_provider_counts.n_individual_providers` | NPPES registry, Sept 2026 (CMS) | Entity Type Code, NPI Deactivation Date | count of individual providers (entity type 1) with no deactivation date, a taxonomy code and a state that maps to two letters; every excluded row is tallied in the run log |

## `fact_ext_arthritis_prevalence` *(local only)*

Grain: data year x location x measure x value type (county; local only).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `fact_ext_arthritis_prevalence.data_year` | CDC PLACES county data, 2025 release | Year | latest data year for each location (the arthritis rows hold only 2023) |
| `fact_ext_arthritis_prevalence.location_id` | CDC PLACES county data, 2025 release | LocationID | county FIPS code |
| `fact_ext_arthritis_prevalence.measure_id` | CDC PLACES county data, 2025 release | MeasureId | ARTHRITIS only |
| `fact_ext_arthritis_prevalence.value_type` | CDC PLACES county data, 2025 release | Data_Value_Type | Age-adjusted prevalence only (approved at step 4c) |
| `fact_ext_arthritis_prevalence.state_code` | CDC PLACES county data, 2025 release | StateAbbr | as is |
| `fact_ext_arthritis_prevalence.county_name` | CDC PLACES county data, 2025 release | LocationName | as is |
| `fact_ext_arthritis_prevalence.prevalence_pct` | CDC PLACES county data, 2025 release | Data_Value | percent of adults; blank becomes NULL |
| `fact_ext_arthritis_prevalence.ci_low_pct` | CDC PLACES county data, 2025 release | Low_Confidence_Limit | percent |
| `fact_ext_arthritis_prevalence.ci_high_pct` | CDC PLACES county data, 2025 release | High_Confidence_Limit | percent |
| `fact_ext_arthritis_prevalence.total_population` | CDC PLACES county data, 2025 release | TotalPopulation | number |
| `fact_ext_arthritis_prevalence.footnote` | CDC PLACES county data, 2025 release | Data_Value_Footnote | as is, blank when none |

## `gold_ext_specialty_triangulation`

Grain: period x IQVIA specialty group (E1).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `gold_ext_specialty_triangulation.period` | Analysis step | protocol E1 | a year 2020 to 2024 or pooled_2020_2024 |
| `gold_ext_specialty_triangulation.specialty_group` | bridge_specialty_crosswalk | iqvia_group | the 11 matched groups |
| `gold_ext_specialty_triangulation.relationship` | bridge_specialty_crosswalk | relationship | exact, combined or weak |
| `gold_ext_specialty_triangulation.n_visible_providers` | fact_ext_partb_provider | npi | distinct individual providers billing any set A code |
| `gold_ext_specialty_triangulation.n_zilretta_providers` | fact_ext_partb_provider | npi | distinct individual providers billing J3304 |
| `gold_ext_specialty_triangulation.medicare_adoption_rate` | Analysis step | protocol definition | Zilretta providers divided by visible providers, 0 to 1 |
| `gold_ext_specialty_triangulation.medicare_ci_low` | Analysis step | provider bootstrap | lower bound, 2,000 draws, seed 0 |
| `gold_ext_specialty_triangulation.medicare_ci_high` | Analysis step | provider bootstrap | upper bound |
| `gold_ext_specialty_triangulation.iqvia_adjusted_share` | IQVIA warehouse | notebook 07 adjusted shares | adjusted Zilretta share by specialty |
| `gold_ext_specialty_triangulation.iqvia_ci_low` | IQVIA warehouse | notebook 07 intervals | lower bound |
| `gold_ext_specialty_triangulation.iqvia_ci_high` | IQVIA warehouse | notebook 07 intervals | upper bound |
| `gold_ext_specialty_triangulation.iqvia_share_65plus` | IQVIA warehouse | fact_product_visits, age band | Zilretta share among patients aged 65 and over |

## `gold_ext_state_adoption`

Grain: year x state (E3).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `gold_ext_state_adoption.year` | Analysis step | protocol E3 | 2019 to 2024 |
| `gold_ext_state_adoption.state_code` | dim_state | state_code | joined from FIPS |
| `gold_ext_state_adoption.n_visible_providers` | fact_ext_partb_provider | npi | distinct individual providers billing any set A code in the state |
| `gold_ext_state_adoption.n_zilretta_providers` | fact_ext_partb_provider | npi | distinct individual providers billing J3304 |
| `gold_ext_state_adoption.adoption_rate` | Analysis step | protocol definition | Zilretta providers divided by visible providers, 0 to 1 |
| `gold_ext_state_adoption.ci_low` | Analysis step | provider bootstrap | lower bound |
| `gold_ext_state_adoption.ci_high` | Analysis step | provider bootstrap | upper bound |
| `gold_ext_state_adoption.reported` | Analysis step | protocol E3 | 1 only if at least 30 visible providers |
| `gold_ext_state_adoption.ma_participation_rate` | fact_ext_geo_variation | ma_participation_rate | state Medicare Advantage share, context |
| `gold_ext_state_adoption.arthritis_prevalence_pct` | fact_ext_arthritis_prevalence | prevalence_pct | population-weighted state value, context |
| `gold_ext_state_adoption.headroom` | Analysis step | protocol E3 | visible providers times (national minus state adoption); only when ranks are usable |

## `gold_ext_promotion_monthly`

Grain: month (H1).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `gold_ext_promotion_monthly.month_id` | fact_ext_openpay_month | month_id | YYYYMM |
| `gold_ext_promotion_monthly.n_physicians_paid` | fact_ext_openpay_month | n_distinct_recipients | physicians with a Zilretta-related payment |
| `gold_ext_promotion_monthly.n_practitioners_paid` | fact_ext_openpay_month | n_distinct_recipients | non-physician practitioners, sensitivity |
| `gold_ext_promotion_monthly.total_amount_usd` | fact_ext_openpay_month | total_amount_usd | all recipient types |
| `gold_ext_promotion_monthly.n_records` | fact_ext_openpay_month | n_records | all recipient types |
| `gold_ext_promotion_monthly.n_flagged_records` | fact_ext_openpay_month | n_flagged_records | records kept but flagged by R8 |
| `gold_ext_promotion_monthly.iqvia_share_pct` | IQVIA warehouse | gold_visit_share_monthly | monthly Zilretta share in percent |

## `gold_ext_price_quarterly`

Grain: quarter (H2).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `gold_ext_price_quarterly.quarter_id` | fact_ext_asp_price | quarter_id | as is |
| `gold_ext_price_quarterly.j3304_limit_per_mg` | fact_ext_asp_price | payment_limit | J3304 limit divided by its dosage (1 mg) |
| `gold_ext_price_quarterly.j3301_limit_per_mg` | fact_ext_asp_price | payment_limit | J3301 limit divided by its dosage (10 mg) |
| `gold_ext_price_quarterly.price_ratio` | Analysis step | protocol H2 | J3304 per mg divided by J3301 per mg |

## `gold_ext_company_vs_visits`

Grain: quarter (E2b).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `gold_ext_company_vs_visits.quarter_id` | fact_ext_company_revenue | period_end | quarter of the period end |
| `gold_ext_company_vs_visits.company` | fact_ext_company_revenue | company | Flexion before Q4 2021, Pacira after |
| `gold_ext_company_vs_visits.net_sales_usd` | fact_ext_company_revenue | net_sales_usd | Zilretta net product sales |
| `gold_ext_company_vs_visits.iqvia_zilretta_visits` | IQVIA warehouse | gold_visit_share_monthly | Zilretta visits summed over the quarter |
| `gold_ext_company_vs_visits.yoy_sales_direction` | Analysis step | protocol E2b | up, down or flat against the same quarter a year earlier |
| `gold_ext_company_vs_visits.yoy_visits_direction` | Analysis step | protocol E2b | up, down or flat against the same quarter a year earlier |
| `gold_ext_company_vs_visits.directions_agree` | Analysis step | protocol E2b | 1 if the two directions agree |

## `gold_ext_verdicts`

Grain: run x check x metric (one row per rule).

| Column | Source | Original column | Rule |
|---|---|---|---|
| `gold_ext_verdicts.run_id` | Analysis step | run | identifier of the analysis run |
| `gold_ext_verdicts.check_id` | Analysis step | protocol | E1, E2a, E2b, E3, H1, H2, H3 or H4 |
| `gold_ext_verdicts.metric` | Analysis step | protocol | the quantity the rule uses, for example spearman_rho |
| `gold_ext_verdicts.value` | Analysis step | computed | the measured value |
| `gold_ext_verdicts.threshold` | Analysis step | protocol | the pre-set threshold as text |
| `gold_ext_verdicts.rule` | Analysis step | protocol | the rule that assigns the verdict |
| `gold_ext_verdicts.verdict` | Analysis step | protocol | agrees, partial, disagrees, consistent, inconsistent, supported, not_supported, usable, unstable or not_run |
| `gold_ext_verdicts.note` | Analysis step | analysis | free text, including weakened-test labels |
| `gold_ext_verdicts.created_at` | Analysis step | run | timestamp |
