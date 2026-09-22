# Data Dictionary

This document describes the exact structure of every raw data source used by the OA & RA Market Intelligence System, field by field, as verified by direct inspection of the real files (not assumed from a schema description). It exists so the ingestion pipeline (§9, Phase 1) has one authoritative reference for column meaning, rather than each module re-discovering the structure independently.

See [`docs/PROPOSAL.md`](PROPOSAL.md) for the business context; this document covers only field-level structure.

---

## 1. Source File Inventory

| File | Sheet(s) | Shape (main sheet) | Purpose |
|---|---|---|---|
| `Team1_M15_19_OA.xlsx` | `M15_19_OA_PAT_VISIT` (main), `M15_19_OA_PAT_VISIT1` (secondary) | 14,157 rows × 648 cols | OA patient-visit pivot (Month × Manufacturer × Product × Specialty × Age × Gender), plus a Place-of-Service-by-month summary |
| `Team1_M04_RA.xlsx` | `M04_RA_PAT_VISIT` (main), `M04_RA_PAT_VISIT1` (secondary) | 664 rows × 89 cols | Same structure as above, for RA |
| `Branded Generic - OA.xlsx` | `M15_19_OA_BRANDED_GENERIC` (main), `M15_19_OA_BRANDED_GENERIC1` (secondary) | 419 rows × 5 cols | OA product reference table: Manufacturer, Product, Brand/Generic tag, per-ICD-10-code visit totals; plus a Place-of-Service-by-month summary |
| `Branded Generic - RA.xlsx` | `M04_RA_PAT_VISIT` (main), `M04_RA_PAT_VISIT1` (secondary) | 20 rows × 4 cols | Same structure as above, for RA |
| `drug-drugsfda-0001-of-0001.json` | n/a (single JSON array) | 29,329 records | openFDA Drugs@FDA bulk export — application/sponsor/submission history per drug |

---

## 2. Main Patient-Visit Pivot Sheets

Applies to `M15_19_OA_PAT_VISIT` (OA) and `M04_RA_PAT_VISIT` (RA).

### 2.1 Row Structure — a 3-level nested pivot, not a flat table

Column A alternates between three row types, in this repeating pattern:

1. **Month row**: column A holds a `datetime` value (e.g., `2019-08-01`); the value columns hold that month's **subtotal**.
2. **Manufacturer row**: column A holds a manufacturer name (string, or the literal `"No Manufacturer"`); the value columns hold that manufacturer's **subtotal** for the month.
3. **Product row(s)**: column A holds a product name (string); value columns hold visit counts for that product, under whichever manufacturer row precedes it.

A final row with the label `Grand Total` closes the sheet. Match it exactly — one manufacturer is literally named `PHYS TOTAL CARE`, so a substring match on "total" would misfire.

**The hierarchy is marked by the column-A cell's indent, not by any value**: Month and `Grand Total` rows have indent 1, Manufacturer rows indent 3, Product rows indent 5 (cell fill just alternates for banding and carries no meaning). Values alone cannot separate Manufacturer rows from Product rows — a small manufacturer's row can look exactly like a product's. There is no explicit foreign key linking a Product to its Manufacturer or Month, only row order plus indent, so **parsing requires carrying the current Month and Manufacturer down through the rows beneath them**.

**Because subtotal rows sit alongside detail rows, naively summing the value columns over every row multiple-counts visits.** Use only Product rows for product-level analysis.

**Patient Visits is a distinct count at every level, not a sum.** A visit that involved two of one manufacturer's products counts once in the Manufacturer row but once in *each* of the two Product rows. So a Manufacturer subtotal can be *less than* the sum of its Product rows (verified on the real data: e.g., FRESENIUS KABI USA, Aug 2019, is 16 visits short across 12 columns, each exactly 1 over). The relationships that do hold, verified on every row of both the OA and RA sheets: each Product row is ≤ its Manufacturer row, each Manufacturer row is ≤ the sum of its Product rows, and the `Grand Total` row equals the sum of the 72 Month rows (months are disjoint).

### 2.2 Column Structure — compound headers, one header row

Row 1 holds the header for every value column (column A's header is literally `"Month"`). Each value-column header is a single string with embedded newlines (`\n`), concatenating multiple dimensions:

- **OA** (`M15_19_OA_PAT_VISIT`): 4 segments — `{SPECIALTY}\n{AGE_BAND}\n{GENDER}\n{"Patient Visits"}`. Example: `"ADDICTION MEDICINE\n40 TO 59\nFEMALE\nPatient Visits"`.
- **RA** (`M04_RA_PAT_VISIT`): 5 segments — `{SPECIALTY}\n{AGE_BAND}\n{GENDER}\n{ICD10_LABEL}\n{"Patient Visits"}`. Example: `"ALLERGY\n00 TO 02\nMALE\nM04 - AUTOINFLAMMATORY SYNDROMES\nPatient Visits"`.

**This 4-vs-5-segment difference between OA and RA is real and confirmed** (verified across all 647 OA value columns and all 88 RA value columns — segment count is consistent within each sheet, never mixed). The parser must branch on sheet identity, not assume one shared header format. OA's ICD-10 scope (M15–M19, five codes) is not split out per column here; RA's single code (M04) is embedded in every column even though it never varies. ICD-10-level breakdown for OA lives instead in the Brand/Generic reference file (§3).

### 2.3 Confirmed Value Ranges (for schema validation, §3.4)

| Dimension | OA values | RA values |
|---|---|---|
| Specialty | 50 distinct values | 19 distinct values |
| Age Band | `00 TO 02`, `03 TO 09`, `10 TO 19`, `20 TO 39`, `40 TO 59`, `60 TO 64`, `65 TO 74`, `75 TO 84`, `85 +`, `UNSPECIFIED` | Same list, minus `UNSPECIFIED` |
| Gender | `FEMALE`, `MALE`, `UNSPECIFIED` | `FEMALE`, `MALE` |
| ICD-10 label (RA only) | n/a | `M04 - AUTOINFLAMMATORY SYNDROMES` (constant) |

Only combinations with at least one recorded visit appear as columns — the pivot is sparse, not a full Cartesian product (OA: 647 of a theoretical 1,500 combinations present; RA: 88 of a theoretical 342). Validation should check that observed values are a **subset** of the lists above, not that all combinations exist.

### 2.4 Cell Values

Visit counts are positive integers (no zeros and no negatives appear anywhere in either sheet); a **blank cell means zero visits**. Month and Manufacturer rows carry subtotals (§2.1), so only Product-row cells are detail-level observations. The parser (`ingestion/nmta_loader.py`) drops blank cells and emits one long-format row per non-blank Product-row cell — 240,773 rows for OA and 1,021 for RA.

**Totals differ by source and should not be treated as interchangeable** (OA):

| Figure | Value | What it is |
|---|---|---|
| Main pivot `Grand Total` row | 5,308,627 | Distinct visits, all products (this sheet's own total) |
| Sum of the pivot's Product rows | 5,544,840 | Over-counts: a visit involving two products is counted in each |
| Reference file, row-level sum | 5,561,131 | `Branded Generic - OA.xlsx`, summed by hand; exceeds the printed total because rows overlap (§4) |
| Reference file, printed Grand Total | 5,323,282 | The same file's own stated total |
| Place-of-Service total | 7,189,004 | All OA visits, with or without a product recorded |

The same disagreement shows at product level: Zilretta is 135,119 visits in the pivot (11,403 under `No Manufacturer`, 123,716 under `PACIRA PHARM`) but 135,133 in the reference file (11,235 + 123,898). Which figure a downstream metric uses must be a stated choice; for product-level visit share it is decided: the pivot (`PROPOSAL.md` §18.1).

---

## 3. Place-of-Service-by-Month Sheets (secondary sheet in every workbook)

Applies to the `*1`-suffixed sheet in all four workbooks (e.g., `M15_19_OA_PAT_VISIT1`). This is a separate, flat summary table, not a continuation of the main pivot — it starts at row 29 in every file (rows 1–28 are blank, a leftover artifact of the Excel PivotTable/slicer export), and has its own header row.

| Column | Type | Description |
|---|---|---|
| Month | string, `"Mon YYYY"` (e.g., `"Aug 2019"`) in the two `Team1_*` workbooks — note: **string here, not `datetime`** as in the main sheet. In the two `Branded Generic` workbooks it is truncated to `"Sep..."` with **no year** (see below). | Calendar month |
| HOSPITAL | integer or blank | Visit count, hospital setting |
| OFFICE | integer or blank | Visit count, office setting |
| OTHER | integer or blank | Visit count, other/unspecified setting |
| TELEHEALTH | integer or blank | Visit count, telehealth setting |

**Confirmed inconsistency**: not every file's secondary sheet has all four columns — `Team1_M04_RA.xlsx`'s secondary sheet only has `OFFICE` and `OTHER` (RA has effectively zero hospital/telehealth visits, consistent with its known sparsity, §6.2/§10), while the other three files have all four. The loader must read columns by header name, not fixed position, and tolerate a missing column as "no visits in that setting," not an error.

This is the real source of the "Place of Service" field described in §3.1 of the proposal. The loader is `ingestion/place_of_service_loader.py`. Which of the four workbooks it can and should read is not uniform, and three findings from the real data (verified, not assumed) decide it:

1. **OA, main workbook (`Team1_M15_19_OA.xlsx`) — clean.** 72 rows, `Aug 2019`–`Jul 2025`, all four settings, total **7,189,004** (the documented all-OA-visits figure). April 2020 shows the COVID shock: Office 39,487 (down from 68,746 in March), Telehealth 1,016 (up from 275).
2. **The `Branded Generic` workbooks carry a different, year-less window.** Their month labels are truncated to `Sep...`, `Oct...`, … with no year, so the loader refuses them rather than guess. Comparing against the OA main sheet shows the rows line up **one month later**, i.e. Sep 2019–Aug 2025 rather than Aug 2019–Jul 2025: 60 of the 71 overlapping months are identical, and the last 11 (Sep 2024–Jul 2025) are slightly *higher* in the Branded Generic file (e.g., Sep 2024 Office 83,983 vs. 83,849). That looks like the same extract re-run later, with recent months restated upward as late data arrived — so **the most recent months of any single extract are provisional**. OA reference total: 7,187,677.
3. **RA is internally inconsistent across sources.** The RA main workbook's Place-of-Service sheet totals only **126 visits** (58 months, Office and Other only), while that same workbook's pivot has a Grand Total of **1,246** and the RA Branded Generic workbook's sheet has **1,283** (72 months, all four settings). The 126-visit sheet appears to be a filtered subset, not the whole RA market. The "~1,283 RA visits" figure quoted elsewhere in this project comes from the Branded Generic sheet, not from this main-workbook sheet. Which RA source (if any) feeds `fact_place_of_service_visits` is an open decision.

---

## 4. Brand/Generic Reference Sheets

Applies to `M15_19_OA_BRANDED_GENERIC` (in `Branded Generic - OA.xlsx`) and `M04_RA_PAT_VISIT` (in `Branded Generic - RA.xlsx` — note this file reuses the same sheet-name convention as the main RA pivot despite holding entirely different, non-pivoted content; distinguish by row/column shape, not sheet name, for this file).

A flat table, one row per **(Manufacturer, Product, Brand/Generic tag)** — the tag is part of the key, not just an attribute. The loader is `ingestion/reference_loader.py`; it finds the sheet by the shape of its header (`Manufacturer`, `Product Sum`, `Brand/Generic`, then ICD-10 columns), never by name.

| Column | Type | Description |
|---|---|---|
| Manufacturer | string | Manufacturer name, or `"No Manufacturer"` |
| Product Sum | string | Product name (labeled `"Product Sum"` in the OA file's header — a leftover Excel pivot-field label, not a numeric sum) |
| Brand/Generic | string, one of `BRAND`, `GENERIC`, `BRANDED GENERIC`, `OTHER` | Patent/ownership tag — reflects patent status, not therapeutic class (§10, §18.1: this tag alone is not sufficient to build the treatment-category taxonomy) |
| One column per ICD-10 code | integer or blank | OA has two: `"M16 - OSTEOARTHRITIS OF HIP\nPatient Visits"` and `"M17 - OSTEOARTHRITIS OF KNEE\nPatient Visits"` (no M15, M18 or M19 column exists); RA has one, `M04`. This is where OA's ICD-10-code-level breakdown actually lives, unlike the main pivot sheet (§2.2). A blank cell means zero. |

A final `Grand Total` row closes the sheet. **Real-data facts** (verified, and asserted in `tests/test_reference_loader.py`):

- **OA:** 417 data rows = 145 distinct products × 168 distinct manufacturers, tags `GENERIC` 170, `OTHER` 123, `BRAND` 80, `BRANDED GENERIC` 44. **RA:** 18 rows = 15 distinct products, 12 manufacturers, every row `BRAND`.
- **A product can carry more than one tag.** 7 OA products do (ACETAMINOPHEN, ASPIRIN, ASPIRIN (OTC), HYDROCORTISONE, IBUPROFEN, NAPROXEN SOD, NAPROXEN SOD (OTC)), because the same manufacturer's product is occasionally tagged differently on a stray row. The stray tags are tiny: ACETAMINOPHEN is 131,976 visits tagged `OTHER` against 65 tagged `GENERIC`. A single-valued `dim_product.brand_generic_tag` therefore needs a stated resolution rule (see `database_schema.md`).
- **Row sums exceed the printed Grand Total, and that is not an error.** `Patient Visits` is a distinct count here too (§2.1): a visit involving two products appears in both rows. OA rows sum to 567,927 (M16) and 4,993,204 (M17) against printed totals of 525,661 and 4,797,621 — 1.08× and 1.04×; RA rows sum to 1,287 against 1,283. The loader checks the inequalities that must hold — each row ≤ the Grand Total ≤ the sum of the rows — not equality. This is the explanation for the "5,561,131 row-level vs. 5,323,282 printed" gap noted in §2.4.
- **The product list matches the committed taxonomy exactly:** the 145 OA and 15 RA products here are precisely the products in `data/reference/product_taxonomy.csv`.

---

## 5. openFDA Fields Used

From `drug-drugsfda-0001-of-0001.json` (or the live `api.fda.gov/drug/drugsfda.json` endpoint, §5, §18.6). Only the fields actually consumed by this project:

| Field (JSON path) | Description |
|---|---|
| `results[].application_number` | FDA application ID (e.g., `NDA208845`) |
| `results[].sponsor_name` | Manufacturer/sponsor of record at approval |
| `results[].products[].brand_name` | Brand name — used to match against our Manufacturer/Product reference tables (§4) |
| `results[].submissions[].submission_type` | `"ORIG"` identifies the original approval submission, as opposed to later supplements |
| `results[].submissions[].submission_status_date` | Date (format `YYYYMMDD`) — the field used to determine actual approval date (§18.5) |

---

## 6. Derived / Computed Fields

Not present in any raw source — computed by the pipeline. Full formulas are in `PROPOSAL.md`, referenced here rather than duplicated:

| Field | Defined in |
|---|---|
| `visit_share` | §18.1 |
| Direction label (Up/Down/Flat) | §18.2 |
| Treatment-category taxonomy (branded injectable / generic corticosteroid / NSAID) | §10, §18.1 |
| Lag/seasonal features, KG-derived competitive-context features | §9.4 (Feature Engineering component) |

---

## 7. Known Data-Quality Notes Specific to Field Parsing

These are parsing-level findings from direct inspection, in addition to the business-level data-quality issues already documented in §10 of the proposal (internal total discrepancy, manufacturer-of-record split, RA miscoding, etc.):

- OA and RA main pivot sheets use a **different number of `\n`-separated header segments** (4 vs. 5, §2.2) — a single shared parser must branch on this, not assume one format.
- The Place-of-Service secondary sheet's **Month column is a string** (`"Aug 2019"`), while the main sheet's Month column is a real `datetime` — these must be normalized to the same type before joining.
- The Place-of-Service secondary sheet **does not always have all four setting columns** (§3) — read by header name, default missing columns to zero/blank rather than erroring.
- `Branded Generic - RA.xlsx`'s reference sheet reuses the sheet name `M04_RA_PAT_VISIT`, identical to the main RA pivot file's sheet name, despite completely different content and shape — sheet name alone cannot distinguish a pivot sheet from a reference sheet; row/column shape must be checked too.
