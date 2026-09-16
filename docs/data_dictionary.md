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

**A naming caveat, confirmed directly**: file names are not a reliable signal of disease scope — `Team1_M15_19_OA.xlsx` has previously been saved to disk under an `..._RA.xlsx` name after an Excel re-save, with identical, correct internal content. The loader identifies OA vs. RA by **sheet name and header content** (`M15_19_OA_*` vs. `M04_RA_*`, and the presence/absence of an ICD-10 segment in the value-column headers — see §2.3), never by file name.

---

## 2. Main Patient-Visit Pivot Sheets

Applies to `M15_19_OA_PAT_VISIT` (OA) and `M04_RA_PAT_VISIT` (RA).

### 2.1 Row Structure — a 3-level nested pivot, not a flat table

Column A alternates between three row types, in this repeating pattern:

1. **Month row**: column A holds a `datetime` value (e.g., `2019-08-01`); all value columns are blank.
2. **Manufacturer row**: column A holds a manufacturer name (string, or the literal `"No Manufacturer"`); all value columns are blank.
3. **Product row(s)**: column A holds a product name (string); value columns hold visit counts for that product, under whichever manufacturer row precedes it.

A given Month block contains many Manufacturer/Product row pairs before the next Month row appears. **Parsing requires forward-filling the Month value down through the Manufacturer and Product rows beneath it**, and pairing each Product row with the nearest Manufacturer row above it — there is no explicit foreign key linking them, only row order.

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

Visit counts are non-negative integers on Product rows only; `None`/blank elsewhere (Month rows, Manufacturer rows, and any Specialty×Age×Gender combination with zero visits for that product that month).

---

## 3. Place-of-Service-by-Month Sheets (secondary sheet in every workbook)

Applies to the `*1`-suffixed sheet in all four workbooks (e.g., `M15_19_OA_PAT_VISIT1`). This is a separate, flat summary table, not a continuation of the main pivot — it starts at row 29 in every file (rows 1–28 are blank, a leftover artifact of the Excel PivotTable/slicer export), and has its own header row.

| Column | Type | Description |
|---|---|---|
| Month | string, `"Mon YYYY"` (e.g., `"Aug 2019"`) — note: **string here, not `datetime`** as in the main sheet | Calendar month |
| HOSPITAL | integer or blank | Visit count, hospital setting |
| OFFICE | integer or blank | Visit count, office setting |
| OTHER | integer or blank | Visit count, other/unspecified setting |
| TELEHEALTH | integer or blank | Visit count, telehealth setting |

**Confirmed inconsistency**: not every file's secondary sheet has all four columns — `Team1_M04_RA.xlsx`'s secondary sheet only has `OFFICE` and `OTHER` (RA has effectively zero hospital/telehealth visits, consistent with its known sparsity, §6.2/§10), while the other three files have all four. The loader must read columns by header name, not fixed position, and tolerate a missing column as "no visits in that setting," not an error.

This is the real source of the "Place of Service" field described in §3.1 of the proposal — 72 monthly rows per file, spanning Aug 2019–Jul 2025, matching the stated 6-year data window exactly.

---

## 4. Brand/Generic Reference Sheets

Applies to `M15_19_OA_BRANDED_GENERIC` (in `Branded Generic - OA.xlsx`) and `M04_RA_PAT_VISIT` (in `Branded Generic - RA.xlsx` — note this file reuses the same sheet-name convention as the main RA pivot despite holding entirely different, non-pivoted content; distinguish by row/column shape, not sheet name, for this file).

A flat table, one row per (Manufacturer, Product):

| Column | Type | Description |
|---|---|---|
| Manufacturer | string | Manufacturer name, or `"No Manufacturer"` |
| Product Sum | string | Product name (labeled `"Product Sum"` in the OA file's header — a leftover Excel pivot-field label, not a numeric sum) |
| Brand/Generic | string, one of `BRAND`, `GENERIC`, `BRANDED GENERIC` | Patent/ownership tag — reflects patent status, not therapeutic class (§10, §18.1: this tag alone is not sufficient to build the treatment-category taxonomy) |
| One column per ICD-10 code | integer or blank | e.g., `"M16 - OSTEOARTHRITIS OF HIP\nPatient Visits"`, `"M17 - OSTEOARTHRITIS OF KNEE\nPatient Visits"` — this is where OA's ICD-10-code-level breakdown actually lives, unlike the main pivot sheet (§2.2) |

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
