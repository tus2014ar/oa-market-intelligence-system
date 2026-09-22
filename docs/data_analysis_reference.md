# Data Analysis & FDA-Integration Reference

This document combines two internal analyses: a full audit of the four raw NMTA files (structure, data-quality findings, complete product taxonomy, feature-engineering priorities, and model recommendations), and a dedicated strategy for combining that data with the openFDA dataset. It complements, rather than replaces, [`docs/data_dictionary.md`](data_dictionary.md) (field-level structure) and `PROPOSAL.md` (business framing, §10, §18) — overlapping findings are cross-referenced rather than repeated in full.

---

## 1. Dataset Structure Recap

Four Excel files, forming two pairs (OA / RA), each with a large raw monthly pivot extract and a small Brand/Generic reference table:

| File | Size | Role |
|---|---|---|
| `Team1_M15_19_OA.xlsx` | 26.5 MB | Raw OA data — full monthly pivot, every product × specialty × age × gender combination, Aug 2019–Jul 2025 |
| `Branded Generic - OA.xlsx` | 39 KB | OA product reference table — one row per (manufacturer, product, tag), totals only (not monthly) |
| `Team1_M04_RA.xlsx` | 200 KB | Raw RA data — same structure as OA, far smaller due to RA's data sparsity |
| `Branded Generic - RA.xlsx` | 24 KB | RA product reference table — 18 rows, 15 distinct products |

Full field-level structure (row hierarchy, column-header encoding, the Place-of-Service secondary sheet, the OA/RA header-segment difference) is documented in [`data_dictionary.md`](data_dictionary.md) §1–§4 and is not repeated here.

**Data coverage confirmed**: all four files, all eight worksheets (two per file) were directly opened and inspected — no hidden sheets exist in any of the four files. **All four** workbooks' second sheet is a Place-of-Service-by-month table (an earlier version of this note wrongly called the reference files' second sheet empty). They are not interchangeable: the pivot workbooks' labels carry a year, the reference workbooks' are truncated (`Sep...`) and cover Sep 2019–Aug 2025, and the RA pivot workbook's sheet totals only 126 visits — see `data_dictionary.md` §3.

---

## 2. Key Data-Quality Findings (summary)

These are documented in full in `PROPOSAL.md` §10 and `data_dictionary.md` §7; summarized here for context before the taxonomy and feature sections below:

| Finding | Consequence |
|---|---|
| Main pivot's own `Grand Total` (5,308,627) ≠ Place-of-Service total (7,189,004) | ~1.88M OA visits (~26%) are diagnosis-only, no product recorded. The visit-share formula uses the ~5.3M product-linked figure, not the 7.19M headline figure. (An earlier draft attributed 5,323,282 to the pivot; that is the *reference file's* printed total — see `data_dictionary.md` §2.4 for all five figures.) |
| Pivot `Patient Visits` is a distinct count at every level | A visit involving two products appears in both product rows, so the pivot's product rows sum to 5,544,840 — more than its Grand Total. Product-level shares are shares of *product-visits*, and that must be stated wherever they are reported. |
| Zilretta split across two manufacturer labels (11,235 + 123,898 = 135,133) | Group by Product name only, never (Manufacturer, Product), or true share is understated ~8%. |
| Brand/Generic tag reflects patent status, not drug class | Real NSAIDs (aspirin, ibuprofen) are tagged `OTHER`. The treatment-category taxonomy must be built by product-name review, not read off this column. |
| RA reference table has zero generic-tagged products (all 18 rows `BRAND`) | Confirms, from the data itself, that RA's competitive structure (biologic vs. biosimilar) doesn't map to OA's branded-vs-generic formula. |
| Reference file's row-level total (5,561,131) ≠ its own printed Grand Total (5,323,282) | Not an error: `Patient Visits` is a distinct count, so a visit involving two products sits in both rows and rows sum to *at least* the Grand Total (same overlap as the pivot's 5,544,840 vs. 5,308,627, and RA's 1,287 vs. 1,283). Previously listed as an open item for the instructor; explained in `data_dictionary.md` §4. |

---

## 3. Complete Product Taxonomy

Computed directly from `Branded Generic - OA.xlsx` and `Branded Generic - RA.xlsx` (not retyped by hand). Manufacturer-split entries (e.g., Zilretta) are combined into a single product-level total.

**This is the seed of a maintained artifact, not a one-off document.** The full tables below become the first snapshot of `data/reference/product_taxonomy.csv`, the version-controlled mapping file the pipeline actually reads at runtime (`product_name → treatment_category`). A product not yet in that file gets flagged, not guessed at — see `PROPOSAL.md` §18.10 for the full pipeline behavior on a new, never-before-seen product.

### 3.1 OA — Category Summary (145 products, 5,561,131 visits by row-level sum — an over-count by design, see §2)

| Category | # Products | Total Visits |
|---|---|---|
| A. Branded injectable — no generic exists | 1 | 135,133 |
| B. Injectable corticosteroids (brand + generic) | 52 | 4,914,533 |
| C. Opioid / other injectable analgesics | 37 | 112,742 |
| D. NSAIDs and OTC oral analgesics | 54 | 398,722 |
| E. Unclassified / negligible-volume long tail | 1 | 1 |

#### A. Branded injectable — no generic exists

| Product | Total Visits | Brand/Generic Tag |
|---|---|---|
| ZILRETTA | 135,133 | BRANDED GENERIC |

#### B. Injectable corticosteroids (brand + generic)

Kenalog and Depo-Medrol are tagged `BRANDED GENERIC`/`BRAND` — the same tags as Zilretta — but both have real generic equivalents elsewhere in this table and belong here as generic-corticosteroid-equivalent competitors, not as branded peers to Zilretta (§2).

| Product | Total Visits | Tag | | Product | Total Visits | Tag |
|---|---|---|---|---|---|---|
| KENALOG | 1,777,924 | BRANDED GENERIC | | DEX COMBO | 135 | BRANDED GENERIC |
| DEPO-MEDROL | 1,092,125 | BRAND | | CELESTONE PHOS | 90 | BRAND |
| TRIAMCINOLONE ACTN | 850,238 | GENERIC | | READYSHARP BETAMET | 47 | BRANDED GENERIC |
| BETAMETH ACE/SOD PHOS | 398,973 | GENERIC | | ARISTOSPAN | 46 | BRAND |
| METHYLPRED ACE | 392,757 | GENERIC | | MULTI-SPECIALTY | 43 | BRANDED GENERIC |
| DEXAMETHASONE | 167,304 | BRAND | | HEXATRIONE | 41 | BRANDED GENERIC |
| CELESTONE | 111,038 | BRAND | | BETA 1 | 41 | BRANDED GENERIC |
| DEXAMETH S PH | 106,279 | GENERIC | | PREDNISONE | 32 | BRAND |
| SOLU-MEDROL | 10,145 | BRAND | | HYDROCORTISONE VAL | 18 | GENERIC |
| METHYLPREDNISOLONE | 1,775 | GENERIC | | DECADRON | 14 | BRAND |
| PRO-C-DURE 5 | 1,418 | BRANDED GENERIC | | DYURAL-80 | 14 | BRANDED GENERIC |
| PHYS EZ USE JOINT | 835 | BRANDED GENERIC | | A-METHAPRED | 13 | BRANDED GENERIC |
| METHYLPRED SOD SUC | 749 | GENERIC | | A-HYDROCORT | 9 | BRANDED GENERIC |
| SARAPIN | 691 | BRANDED GENERIC | | HYDROCORTISONE | 7 | BRAND/GENERIC |
| TRIAMCINOLONE DIAC | 685 | GENERIC | | DEXAMETHASONE INTN | 7 | BRANDED GENERIC |
| BETAMETHASONE COMBO | 599 | BRANDED GENERIC | | DEXLIDO-M | 7 | BRANDED GENERIC |
| PRO-C-DURE 6 | 231 | BRANDED GENERIC | | MEDROL | 6 | BRAND |
| SOLU-CORTEF | 169 | BRAND | | BETAMETHASONE | 3 | GENERIC |
| | | | | BETAMETH DIP AUG | 3 | GENERIC |
| | | | | PREDNISOLONE S PH | 3 | BRAND |
| | | | | ARZE-JECT-A | 2 | BRANDED GENERIC |
| | | | | CLOBETASOL PROP | 2 | GENERIC |
| | | | | READYSHARP DEXAMET | 2 | BRANDED GENERIC |
| | | | | HYDROCORTONE | 2 | BRAND |
| | | | | CORTEF | 2 | BRAND |
| | | | | MARBETA-L | 1 | BRANDED GENERIC |
| | | | | MEDROLOAN SUIK | 1 | BRANDED GENERIC |
| | | | | PREDNISOLONE | 1 | GENERIC |
| | | | | ARISTOCORT | 1 | BRAND |
| | | | | LIDOLOG | 1 | BRANDED GENERIC |
| | | | | BAYCADRON | 1 | BRANDED GENERIC |
| | | | | TAC-3 | 1 | BRANDED GENERIC |
| | | | | DOUBLEDEX | 1 | BRANDED GENERIC |
| | | | | BETALOAN SUIK | 1 | BRANDED GENERIC |

#### C. Opioid / other injectable analgesics

Pain-control agents used around procedures — not part of the branded-injectable vs. generic-corticosteroid competitive story; excluded from the visit-share taxonomy entirely (§18.1 of `PROPOSAL.md`).

| Product | Visits | Tag | | Product | Visits | Tag |
|---|---|---|---|---|---|---|
| KETOROLAC TROMETH | 97,801 | BRAND | | DILAUDID | 6 | BRAND |
| DICLOFENAC POT | 11,005 | GENERIC | | TRAMADOL HCL ER | 6 | GENERIC |
| FENTANYL CIT | 2,875 | BRAND | | DURAMORPH PF | 4 | BRANDED GENERIC |
| NALBUPHINE HCL | 240 | BRAND | | DARVON | 4 | BRAND |
| LIDOCAINE | 115 | GENERIC | | POD-CARE 100C | 3 | BRANDED GENERIC |
| HYDROMORPHONE HCL | 111 | GENERIC | | ANJESO | 2 | BRANDED GENERIC |
| MORPHINE SULF | 101 | GENERIC | | DYLOJECT | 2 | BRAND |
| P-CARE K40 | 80 | BRANDED GENERIC | | OXYCODONE HCL | 2 | BRANDED GENERIC |
| LOFENA | 79 | BRANDED GENERIC | | OFIRMEV | 2 | BRANDED GENERIC |
| DEMEROL | 67 | BRAND | | DURACLON | 1 | BRANDED GENERIC |
| CLONIDINE HCL | 61 | BRAND | | DILAUDID SYRINGE | 1 | BRANDED GENERIC |
| TORADOL | 24 | BRAND | | OXYCODONE/APAP | 1 | GENERIC |
| BUPRENORPHINE HCL | 21 | GENERIC | | READYSHARP ANES+KETO | 1 | BRANDED GENERIC |
| DARVON-N | 19 | BRAND | | | | |
| INFUMORPH | 16 | BRANDED GENERIC | | | | |
| BUTORPHANOL TART | 13 | BRAND | | | | |
| DARVOCET-N 50 | 12 | BRAND | | | | |
| TRAMADOL HCL | 12 | GENERIC | | | | |
| MEPERIDINE HCL | 12 | BRAND | | | | |
| HYCD/APAP | 12 | GENERIC | | | | |
| PRIALT | 9 | BRAND | | | | |
| DARVOCET-N 100 | 8 | BRAND | | | | |
| POD-CARE 100K | 7 | BRANDED GENERIC | | | | |
| MORPHINE SULF C-JECT | 7 | GENERIC | | | | |

#### D. NSAIDs and OTC oral analgesics

The "NSAID" leg of the three-way visit-share denominator (`PROPOSAL.md` §18.1).

| Product | Visits | Tag | | Product | Visits | Tag |
|---|---|---|---|---|---|---|
| ASPIRIN | 152,528 | GENERIC/OTHER | | TYLENOL 8 HOUR | 59 | OTHER |
| ACETAMINOPHEN | 132,041 | GENERIC/OTHER | | ASPIRIN BUFFERED | 38 | OTHER |
| ASPIRIN (OTC) | 104,898 | GENERIC/OTHER | | ASA/APAP/CAF | 34 | OTHER |
| NAPROXEN SOD (RX) | 2,256 | GENERIC | | M-PAP | 23 | OTHER |
| MAPAP ARTHRITIS PAIN | 1,691 | OTHER | | THERAPY BAYER ASP | 20 | OTHER |
| BAYER ASP REGIMEN | 906 | OTHER | | FENOPROFEN CA | 19 | GENERIC |
| TRI-BUFFERED ASPIRIN | 494 | OTHER | | ALEVE | 14 | OTHER |
| IBUPROFEN (OTC) | 488 | OTHER | | BUFFERIN | 12 | OTHER |
| PHARBETOL | 359 | OTHER | | DOLOBID | 12 | BRANDED GENERIC |
| DIFLUNISAL | 358 | GENERIC | | PAIN RELIEVER PLUS | 11 | OTHER |
| ECOTRIN | 342 | OTHER | | PAIN RELIEF | 11 | OTHER |
| ZORVOLEX | 279 | BRAND | | ADVIL | 9 | OTHER |
| BAYER ASPIRIN | 275 | OTHER | | VAZALORE | 9 | OTHER |
| IBUPROFEN | 250 | GENERIC/OTHER | | ACETAMINOPHEN CHILD | 8 | OTHER |
| SALSALATE | 238 | GENERIC | | EXCEDRIN EX STR | 7 | OTHER |
| NAPROXEN SOD | 211 | GENERIC/OTHER | | SILAPAP | 7 | OTHER |
| TYLENOL EXTRA STRG | 198 | OTHER | | ST. JOSEPH ASPIRIN | 6 | OTHER |
| TYLENOL REGULAR | 157 | OTHER | | EXCEDRIN MIGRAINE | 4 | OTHER |
| ZIPSOR | 141 | BRANDED GENERIC | | IBUPROFEN (OTC) CHILD | 4 | OTHER |
| IBUPROFEN (RX) | 103 | GENERIC | | Q-PAP | 3 | OTHER |
| TYLENOL ARTHRITIS | 102 | OTHER | | ANACIN | 2 | OTHER |
| NAPROXEN SOD (OTC) | 82 | GENERIC/OTHER | | HALFPRIN | 2 | OTHER |
| | | | | MOTRIN IB | 2 | OTHER |
| | | | | EXCEDRIN TEN HDCHE | 1 | OTHER |
| | | | | APAP X | 1 | OTHER |
| | | | | APAP/CAF | 1 | OTHER |
| | | | | ASCRIPTIN | 1 | OTHER |
| | | | | EXCEDRIN ASP/FREE | 1 | OTHER |
| | | | | DOANS X-STR | 1 | OTHER |
| | | | | BIOFREEZE W/ILEX | 1 | OTHER |
| | | | | CHILDRENS MAPAP | 1 | OTHER |
| | | | | FEVERALL JUNIOR | 1 | OTHER |

#### E. Unclassified / negligible-volume long tail

| Product | Total Visits | Tag |
|---|---|---|
| MLK PROCEDUR F2 | 1 | BRANDED GENERIC |

### 3.2 RA — Full Product List (15 products)

| Product | Visits | What it actually is |
|---|---|---|
| ILARIS | 963 | Canakinumab — IL-1 blocker, autoinflammatory syndromes |
| ACTEMRA | 89 | Tocilizumab — IL-6 blocker, RA biologic |
| INFLECTRA | 89 | Infliximab biosimilar — TNF blocker |
| KINERET | 85 | Anakinra — IL-1 blocker |
| REMICADE | 39 | Infliximab (brand) — TNF blocker |
| ARCALYST | 6 | Rilonacept — IL-1 blocker |
| RENFLEXIS | 4 | Infliximab biosimilar |
| OPDIVO | 3 | Nivolumab — oncology checkpoint inhibitor, **not an RA drug** |
| YERVOY | 3 | Ipilimumab — oncology checkpoint inhibitor, **not an RA drug** |
| KEYTRUDA | 1 | Pembrolizumab — oncology checkpoint inhibitor, **not an RA drug** |
| ORENCIA | 1 | Abatacept — RA biologic |
| RITUXAN | 1 | Rituximab (brand) — used in both RA and lymphoma |
| SAPHNELO | 1 | Anifrolumab — a lupus drug, not RA-specific |
| STELARA | 1 | Ustekinumab — mainly psoriasis / psoriatic arthritis |
| TRUXIMA | 1 | Rituximab biosimilar |

**Finding**: Opdivo, Yervoy, and Keytruda are cancer immunotherapy drugs, not RA treatments. They almost certainly appear here because checkpoint-inhibitor cancer drugs can cause inflammatory joint symptoms as a side effect, and that side-effect visit got coded under the M04 diagnosis. Concrete confirmation that some of RA's data isn't RA-treatment data at all (§6.2 of `PROPOSAL.md`).

---

## 4. Full Feature Inventory

| Field | Source | Type | Values / Range |
|---|---|---|---|
| Month | Row hierarchy (raw pivot) | Date (monthly) | 72 values: Aug 2019–Jul 2025 |
| Manufacturer | Row hierarchy (raw pivot) | Categorical | ~169 distinct values; needs name-standardization |
| Product | Row hierarchy (raw pivot) | Categorical | ~145 distinct products (OA); 15 (RA) |
| Brand/Generic tag | Lookup file | Categorical | `BRAND`, `GENERIC`, `BRANDED GENERIC`, `OTHER` |
| Specialty | Column header (raw pivot) | Categorical | 50 values |
| Age band | Column header (raw pivot) | Categorical/ordinal | 10 bands: `00-02` through `85+`, plus `UNSPECIFIED` |
| Gender | Column header (raw pivot) | Categorical | `MALE`, `FEMALE`, `UNSPECIFIED` |
| Place of Service | Second sheet (raw pivot) | Categorical | `HOSPITAL`, `OFFICE`, `TELEHEALTH`, `OTHER` — market-level only, not per-product |
| Patient Visits | Cell value (raw pivot) | Numeric (count) | The base metric everything else is computed from |
| ICD-10 scope | File-level (implicit) | Categorical | M15–M19 (OA) or M04 (RA) — fixed per file |
| FDA approval date | External: openFDA (not in these files) | Date | Retrieved per branded product, §6 below |

### 4.1 Feature Priority for the Core Classifier

Ranked for predicting next month's Up/Down/Flat direction of branded-injectable visit share in OA. `[RAW FIELD]` exists today as a column; `[ENGINEERED]` does not exist anywhere in the raw files and must be built during feature engineering.

**Raw fields:**

| Field | Priority | Why |
|---|---|---|
| `[RAW FIELD]` Patient Visits (via Product + Brand/Generic tag) | **High** | The base ingredient everything else is built from — once products are mapped to a treatment category, summing by category and month produces the visit-share series itself. |
| `[RAW FIELD]` Place of Service | Medium | Usable as monthly market-level context; useful for flagging anomalies (e.g., the April 2020 COVID-driven Office→Telehealth shift). |
| `[RAW FIELD]` Specialty | Medium | Main value is as the input to the engineered "specialty-mix shift" feature; also the key field for the Objective 3 stretch goal. |
| `[RAW FIELD]` Age band and Gender | Low | 30 age×gender combinations against only 72 months of data mostly produces noise for the core monthly classifier; more useful for Objective 3. |
| `[RAW FIELD]` Manufacturer | Low / use with caution | Given the Zilretta manufacturer-of-record split, clean or ignore in favor of Product name for any grouping. |

**Engineered features (none exist in the raw files today):**

| Feature | Priority | Built from |
|---|---|---|
| Lagged visit share (t-1, t-2, t-3) | **High** — single strongest expected predictor | The computed monthly visit-share series, shifted back 1–3 months. Highly autocorrelated, which is exactly why the persistence baseline is a genuinely hard bar. |
| Rolling averages / momentum (3-mo, 6-mo) | High | Rolling-window average or slope on the visit-share series — captures whether a gain is accelerating or decelerating. |
| Competitive context | High | Product + Brand/Generic tag (count of no-generic-equivalent branded products active that month) **plus** FDA approval dates from openFDA — a signal the visit data structurally cannot contain alone (§6 below). |
| Seasonality indicators (month-of-year/quarter) | Medium | Built from Month. The Place-of-Service sheet already shows a concrete shock: Office visits fell from ~90,000/month to 39,487 in April 2020 while Telehealth jumped from near-zero to 1,016 — handle explicitly as a flagged outlier, not smoothed away generically. |
| Specialty-mix shift | Medium | Specialty + Patient Visits — a change in which specialties are prescribing can lead the aggregate share number. |
| FDA approval event flags | Medium | Requires openFDA (§6) — a 0/1 indicator for "a relevant approval occurred in the last N months," supporting Objective 1's inflection-point analysis. |

---

## 5. Recommended Modeling Approach

Ties the proposal's stated model progression (§9) to what the data itself actually supports:

| Step | Model | Role | Why it fits this data |
|---|---|---|---|
| 1 | Persistence baseline | Mandatory bar | Monthly visit-share is highly autocorrelated — a genuinely strong competitor, not a token comparison. |
| 2 | Logistic regression | First real model | Simple, interpretable; performs well with a modest number of engineered features; low overfitting risk on a small (72-month) dataset. |
| 3 | Random forest | Main candidate | Handles non-linear interactions (e.g., share only drops when a competitor launches *and* it's a slow season) logistic regression can't capture. Pairs well with SHAP. |
| 4 | Gradient boosting (XGBoost/LightGBM) | Main candidate | Typically the strongest tabular-data performer; same SHAP-compatibility, often better accuracy if tuned (Optuna). |

**Honest caveat on dataset size**: only 72 months of data total. The backtesting design (§18.3) uses the first 24 months for initial training, leaving ~48 monthly predictions to evaluate on — a small evaluation set for a 3-class classifier. Differences between candidate models may end up small, and statistical significance (McNemar's test, §18.4) may be hard to establish. That's an expected outcome, not a flaw in the plan — McNemar's test was chosen specifically to handle this rather than relying on a raw accuracy comparison. **Practical implication**: a heavily complex model (deep learning, large ensembles) is unlikely to help here and more likely to overfit; a well-regularized random forest or gradient boosting model with a small number of carefully engineered features is the more defensible choice.

**RA track**: should not receive its own Up/Down/Flat classifier — confirmed directly by the raw file (only ~1,283 visits, zero generic-tagged products), not just assumed. RA remains descriptive/exploratory: trend charts, branded-vs-biosimilar share decomposition, side-by-side comparison against OA (§6.2 of `PROPOSAL.md`).

### 5.1 Time-Series Decomposition & a Classical Forecasting Baseline (validation check, not a competing track)

With 72 months of data, it would be a real gap not to apply classical time-series analysis — but it's added here as a **validation check on the classification approach above, not a second modeling track**. Two additions, both scoped deliberately small:

- **Decomposition as EDA**: trend/seasonality/residual decomposition and ACF/PACF plots on the `visit_share` series. This confirms — with real numbers, not just visual inspection — the two claims the feature engineering in §4.1 already depends on: that the series is strongly autocorrelated (why lagged visit share is the top-priority engineered feature) and seasonal (why the April 2020 Office→Telehealth shock needs an explicit flag rather than a generic seasonality term).
- **One classical model as an additional baseline**: SARIMA or Holt-Winters/ETS, forecasting `visit_share` directly, with Up/Down/Flat derived from the forecasted change. Evaluated through the exact same expanding-window backtest (§18.3) and McNemar's test (§18.4) as every other candidate — not a separate evaluation methodology. This answers, honestly, whether the tree-based classifiers are earning their added complexity: if a well-understood SARIMA/ETS model gets most of the way there, that's a finding worth reporting, not a result to bury.

**Deliberately not done**: a full second modeling track, or anything beyond one classical model (no LSTM/deep sequence models). The same small-dataset caution already stated above applies with even more force to a heavily parameterized SARIMA grid search — 72 points is a short series to tune `(p,d,q)(P,D,Q,s=12)` on without overfitting.

---

## 6. Combining the NMTA Data with openFDA

### 6.1 Why they can't be merged the way the four xlsx files were merged with each other

The four Excel files share the same grain — a monthly visit count for a specific product — so they reshape into one consistent table. The openFDA JSON is a fundamentally different kind of data:

| | xlsx visit data | openFDA `drugsfda.json` |
|---|---|---|
| Grain (one row =) | Month × Product × Specialty × Age × Gender | One FDA application (a regulatory filing) |
| Has a time series? | Yes — 72 monthly snapshots | No — each fact (e.g., an approval date) is a single, fixed point in time |
| Scope | ~145 OA + 15 RA products actually seen in the data | 29,329 applications, every FDA-regulated drug ever filed, all therapeutic areas |
| Join key available | Product name (`Product Sum` column) | Product name (`brand_name`, inside `products[]`) |

A full row-for-row merge doesn't make sense — there's no month column on the FDA side to align with the 72 months on the visit side. What's possible and useful instead is a **lookup/enrichment join**: attach a small number of static facts about each product (its approval date, and things derived from it) onto the existing monthly visit rows.

### 6.2 The real obstacles (all manageable — none structural)

- **Scale mismatch**: only a fraction of 29,329 applications are actually relevant. Filtering, not a blocker — **corrected scope**: applying §18.6's actual rule (every product tagged `BRAND` or `BRANDED GENERIC` in our own reference tables) gives **87 products** (`ingestion/openfda_client.branded_products()`), not the ~20 this section originally estimated. The ~20 figure was the handful checked by hand early on (Zilretta, Kenalog, Depo-Medrol, the RA biologics); most of the other ~67 are opioid/anesthetic brand names (Category C, already excluded from the visit-share formula, §18.1) or the RA oncology-miscoded products (§3.2) — real products, just not ones Objective 1's inflection-point analysis needs a date for. Which subset the Silver builder actually queries (all 87, or only the categories the classifier's features use) is an open decision, not yet made.
- **Name spelling differences**: xlsx `Product Sum` values (e.g., `ZILRETTA`) vs. JSON `brand_name` values usually match after simple normalization (uppercase, trim) — verified for Zilretta and Kenalog against the live API while building `ingestion/openfda_client.py`.
- **Multiple applications per product**: some products (e.g., Kenalog) have several FDA applications over the decades. Fix: take the earliest ORIG/approved date across all matching applications, the approach already used when checked manually (§18.5 of `PROPOSAL.md`).
- **Manufacturer-of-record changes (the Zilretta case)**: join on Product name only, never Manufacturer, for the same reason already established for the visit-share taxonomy itself (§2).

### 6.3 Three Ways to Combine Them

Not competing alternatives — the proposal's architecture calls for using more than one, for different consumers (the classifier vs. the natural-language query layer).

**Method A — Simple key-based lookup join** (the foundation). Build a small derived table from the JSON: product name → earliest approval date. Exactly what `PROPOSAL.md` §18.7 already calls for (a simple lookup table, not the full knowledge graph, for Core Objectives 1–2).
```
fda_lookup = {"ZILRETTA": "2017-10-06", "KENALOG": "1974-01-29", ...}  # both verified live
oa_long["fda_approval_date"] = oa_long["product"].map(fda_lookup)
```
This alone creates no new predictive feature — a static date sitting in a column isn't something a monthly classifier can use directly. It's the input to Method B.

**Method B — Derived time-aware features** (the part that creates value). Once every relevant product has an approval date attached, compute features that change month to month relative to it:
- `months_since_launch` — current month minus approval date, for the branded injectable.
- `is_post_launch` flag — 0/1, whether the product had launched by that month.
- `competitor_count_on_market(t)` — count of branded, no-generic-equivalent products approved on or before month *t*; requires joining across all branded products, not just Zilretta.
- `months_since_last_competitor_event` — time since the most recent competitor approval or major label supplement.

This is the "competitive context" feature (§4.1) — a signal that cannot exist in the visit data alone, because visit counts have no concept of a regulatory calendar.

**Method C — Knowledge graph representation**. Rather than (or alongside) a flat joined table, `PROPOSAL.md` §4.2 models this as nodes and edges: `Product --approved_on--> Date`, `Product --competes_with--> Product`, `Product --entered_market_on--> Event`. A different way of combining the same two sources — not a merged table, but a graph traversable for questions a flat join answers awkwardly, such as "how many competing branded products were active in March 2023." This is also the representation the §19.3 RAG/MCP query layer draws on for natural-language questions spanning both datasets.

**In practice**: Methods A and B feed the classifier (Objective 2); Method C feeds the natural-language Q&A layer (§5, §19.3). Both are called for, for different consumers of the same underlying join.

### 6.4 What's Actually Gained by Combining Them

- **Objective 1 becomes answerable at all.** Without FDA data, "is there an inflection point tied to approval" has no answer — visit data alone has no concept of a launch date. This join is literally how §18.5's finding (Zilretta's approval predates the data window) was produced.
- **A genuinely new predictive feature for Objective 2.** "Competitor count" or "months since last competitor approval" is information the visit data structurally cannot contain by itself — external context, not a transformation of what's already there.
- **Grounded, citable answers for the Q&A layer (§19.3).** An answer that cites a real FDA event date is stronger than a vague trend description with no external anchor.
- **Turns a one-off manual analysis into a repeatable pipeline step.** The approval-date lookups done by hand so far need to become an automated join that runs on every monthly refresh, not a finding that only exists in a chat transcript.

### 6.5 Honest Caveat on How Much This Will Actually Move the Classifier

Every branded product's approval date checked so far — Zilretta and all its OA competitors, plus all the RA biologics — falls before the 6-year data window (Aug 2019–Jul 2025), except two very low-volume products (Anjeso, 2 total visits; Saphnelo, 1 total visit). This means `is_post_launch` and `months_since_launch` will show essentially no variation across the current 72 months — every month is already post-launch for every product with meaningful volume.

This doesn't make the join not worth doing. It's still required for Objective 1's honest writeup, for `competitor_count` (which does still change over time, since new competitors could theoretically enter), and for future data (if NMTA history is extended backward, or a new competitor launches during a future monthly refresh). Going in with the correct expectation matters, though: this join won't produce a dramatic new predictive signal in the *current* dataset — its main value is completeness, correctness, and positioning the pipeline for data the project doesn't have yet.

---

## 7. From Raw Files to a Model-Ready Table

The concrete reshape path (implemented by the ingestion pipeline, §9.4 of `PROPOSAL.md`):

1. **Un-pivot** the wide crosstab into long format: one row per (Month, Manufacturer, Product, Specialty, Age, Gender) with a single `patient_visits` column.
2. **Fix Manufacturer/Product naming issues** (the Zilretta case) — group by Product name only for share calculations, not by (Manufacturer, Product).
3. **Map each product to a treatment category** (branded injectable / generic corticosteroid / NSAID) using the Brand/Generic tag plus manual review (§3 above), since `OTHER` mixes in real NSAIDs like ibuprofen.
4. **Aggregate up** to Month × treatment-category totals, compute visit share per §18.1's formula, then label Up/Down/Flat per §18.2's threshold.
5. **Join in** Place-of-Service (market-level monthly context) and FDA approval-date flags (via openFDA, §6) as additional columns.

The result is a clean table with about 72 rows (one per month) and a manageable set of engineered columns — what's actually fed to logistic regression, random forest, and gradient boosting.
