# Protocol: external public data (DL-57, DL-59)

**Status: approved before any analysis ran.** (Section 8 was added afterwards, to record the implementation choices made while running step 6; it changes no rule.) Everything in sections 1 to 6 is fixed here. Any change made after results are seen is recorded as a deviation (DL-60 onward) with the reason. Raw-data profiling (step 0) is described in [`notebooks/10_external_raw_data_profile.ipynb`](../notebooks/10_external_raw_data_profile.ipynb) and its outputs in [`data/reference/external_profile/`](../data/reference/external_profile/).

## 0. Purpose and scope

IQVIA shows visits, but not geography, price, promotion or company results. Public sources can add independent checks and new decision tools:

- **Independent check:** does Medicare billing agree with IQVIA about which specialties use Zilretta most, and about when its use turned (Q1 and Q3)?
- **New decision tool:** where (by state) is Zilretta under-used compared with how many providers inject steroids?
- **Candidate explanations** for the early-2022 turn, tested against fixed rules, never claimed as causes.

**Out of scope:** causal claims; forecasts from annual data; any change to the website or Claude question box (the serving layer is deferred to the final phase); new software libraries.

### Disclosures (stated up front)

1. **E2 is a weakened test.** While checking downloads we saw the number of Medicare provider rows billing Zilretta by year, and the 2024 national totals. The E2 rule was written after that glimpse, so E2 is reported as "consistent / inconsistent, not a clean test". The weight sits on E1, E3 and the E4 hypotheses written before their data was examined.
2. **H3 is already decided** by an existing result (the detected break is March 2022). It is reported as such; the new Medicare facility-versus-office view is description only.
3. **Medicare data is not the IQVIA population.** It covers Original Medicare (mostly age 65 and over), has no diagnosis, and counts claims. We compare rankings and directions, never levels.
4. **Open Payments files are the 2026 republication of every year**, so older years include later corrections.
5. **H2 (price) is also not a fully clean test.** While loading and checking the price files we saw several Zilretta payment-limit values by chance (for example about $16.99 per mg in 2022Q3 and about $19.16 in 2026), so the H2 rule was fixed after a glimpse of its data. It stays as written and is reported with this label.
6. **Company sales are national and all-payer.** Zilretta net sales (Flexion to the third quarter of 2021, Pacira from 2022) include commercial and other payers, not only Medicare or the IQVIA sample. The fourth quarter of each year is derived (the year minus the first nine months). The 2021 fourth quarter spans the 19 November 2021 acquisition and is derived from Pacira's stated full-year figure of $102.7 million (rounded to $0.1 million, so good to about plus or minus $0.05 million) minus Flexion's first nine months; it is flagged and kept in E2b.

## 1. Data used

| Source | Coverage | Used for | Lineage |
|---|---|---|---|
| Medicare Part B by Geography and Service | 2019 to 2024 | state-level provider and patient counts for approved codes | fact_ext_partb_geo |
| Medicare Part B by Provider and Service | 2019 to 2023 (filtered API pull), 2024 (filtered from the full file) | provider-level adoption by specialty and state | fact_ext_partb_provider |
| Medicare Part D by Geography and Drug | 2019 to 2024 | NSAID and oral steroid context by state | fact_ext_partd_geo |
| Part B payment limit (ASP) files | 2019Q3 to 2025Q4 (26 quarters) | price series (H2) | fact_ext_asp_price |
| Open Payments, general payments | program years 2019 to 2025 | monthly promotion series (H1) | fact_ext_openpay_month |
| Medicare Geographic Variation, and Medicare Advantage Geographic Variation | 2014 to 2024, 2016 to 2023 | Advantage share by state (context and adjustment) | fact_ext_geo_variation |
| NPPES provider registry, with the NUCC taxonomy list (version 26.1) | single snapshot, September 2026 | provider counts by specialty and state (denominator, flagged as a snapshot) | fact_ext_provider_counts |
| CDC PLACES | 2025 release | arthritis prevalence by state (context) | fact_ext_arthritis_prevalence |
| SEC filings (Flexion, Pacira, Bioventus, Anika), 86 documents | 2019 to 2026 | Zilretta and competitor revenue, transcribed by hand | fact_ext_company_revenue |
| FDA, CMS and event documents | various | dated event table | dim_event |

Not used for any rule: Part D by provider (2024) and Medicaid drug utilisation (2024); both were downloaded and are optional context only.

**Verified dates** (from primary documents): FDA approval 6 October 2017 (signed letter; the package cover sheet's "14 December 2017" is treated as a cover-sheet discrepancy); OPPS pass-through status 1 April 2018 to 31 March 2021 (CMS Transmittal 3988, Table 5; Transmittal 10666, Table 11). Pass-through is a hospital outpatient payment rule; office-based payment is unaffected.

## 2. Definitions

- **Provider adoption rate** (year, group) = distinct individual providers (entity code I) billing J3304 in any setting ÷ distinct individual providers billing at least one primary-set code (set A). Groups are the approved specialties or states.
- **Visibility bias:** CMS suppresses any figure from 10 or fewer patients (the observed minimum is 11), so low-volume providers do not appear. Adoption means "among visible providers".
- **Patients** (Tot_Benes) are a secondary measure and are never added across codes (one patient can receive several). **Billing units are never compared** (1 mg, 10 mg and 40 mg codes).
- **Code sets** (fixed in `external/codes.py`, tested):
  - **A primary:** J3304, J3301, J1010, J1020, J1030, J1040, J0702, J1100.
  - **B hyaluronic context** (never part of a share): J7318, J7320 to J7329, J7331, J7332.
  - **C joint-injection procedures** (denominator, analysis only): 20610, 20611.
  - **D IV steroids** (sensitivity only): J2920, J2930, J1720 (J2919 has no rows before 2024 and is dropped from D).
  - Methylprednisolone acetate is **one drug family** across J1010 and J1020 to J1040.
- **Years:** full calendar years 2020 to 2024 for comparisons with IQVIA (IQVIA covers August 2019 to July 2025).

### Specialty crosswalk (approved)

| IQVIA group | Medicare name(s) | Note |
|---|---|---|
| ANESTHESIOLOGY | Anesthesiology | nurse anesthetists excluded |
| FAMILY PRACTICE | Family Practice | exact |
| INTERNAL MEDICINE | Internal Medicine | exact |
| NURSE PRACTITIONER | Nurse Practitioner | exact |
| ORTHOPEDIC SURGERY | Orthopedic Surgery | IQVIA spine-surgery group excluded |
| OSTEOPATHIC MEDICINE | Osteopathic Manipulative Medicine | **weak link, flagged in results** |
| PAIN MEDICINE | Interventional Pain Management + Pain Management | two names combined |
| PHYSICAL MEDICINE & REHAB | Physical Medicine and Rehabilitation | name differs |
| PHYSICIAN ASSISTANT | Physician Assistant | exact |
| RHEUMATOLOGY | Rheumatology | exact |
| SPORTS MEDICINE | Sports Medicine | exact |
| RARE (grouped) | none | excluded |

### Fixed name lists

- **Part D NSAIDs** (context only, no pass or fail): single-ingredient celecoxib, diclofenac (oral forms), diflunisal, etodolac, fenoprofen, flurbiprofen, ibuprofen, indomethacin, ketoprofen, ketorolac (oral), mefenamic acid, meloxicam, nabumetone, naproxen, oxaprozin, piroxicam, salsalate, sulindac, tolmetin. Combination products are excluded.
- **Part D oral steroids:** prednisone, prednisolone, methylprednisolone, dexamethasone, base generic names only. Topical, eye and injectable forms and hydrocortisone are excluded.
- **Open Payments products:** Zilretta is matched case-insensitively in any of the five product slots. Hyaluronic products are normalised to Durolane, Euflexxa, Gelsyn-3, GenVisc 850, Gel-One, Hyalgan, Hymovis, Monovisc, Orthovisc, Supartz FX, Synvisc (including Synvisc-One), Triluron, Trivisc and Visco-3. Excluded false matches: `Triamcinolone-Moxifloxacin PF`, the generic "hyaluronic acid other" bucket, and `Methylprednisolone sodium succinate`. Kenalog never appears as a product name, so promotion of the generic comparator cannot be measured.

## 3. Questions, rules and verdicts

All resampling uses fixed seed 0 and 2,000 draws. Verdict labels are assigned by the rule, not by judgement.

### E1. Does Medicare's specialty ranking agree with IQVIA's (Q3)?
- **Medicare side:** provider adoption rate by approved specialty, pooled 2020 to 2024, with intervals from resampling providers within specialty.
- **IQVIA side:** the adjusted specialty shares of notebook 07; a second view uses IQVIA's 65-and-over patients only.
- **Rule:** Spearman rank correlation across the 11 matched groups, **and** the overlap of the top 3 groups.
- **Verdict:** *agrees* if ρ ≥ 0.6 and at least 2 of the top 3 are shared; *partial* if exactly one holds; *disagrees* if neither. With 11 groups the correlation has wide uncertainty and the result states it.

### E2. Does the Medicare and company-revenue picture match the IQVIA turn (Q1)? (weakened, see disclosures)
- **(a)** Medicare provider adoption by year 2019 to 2024: *consistent* if the peak year is 2021 or 2022 and 2024 is below the peak.
- **(b)** Quarterly Zilretta net sales (Flexion to Q3 2021, Pacira from Q4 2021) against IQVIA quarterly Zilretta visits, Q3 2020 to Q2 2025: *consistent* if the year-over-year direction agrees in at least 70% of quarters.

### E3. Where is Zilretta under-used (geography)?
- **Measure:** state provider adoption rate by year; a state-year is reported only with at least 30 visible providers; intervals from resampling providers within state.
- **Stability gate:** state ranks are **usable only if the 2022 and 2024 state ranks correlate at ρ ≥ 0.7**; otherwise the result is "unstable" and no targeting list is produced.
- **Headroom (if usable):** visible providers × (national adoption − state adoption), shown with Medicare Advantage share and arthritis prevalence as context, and an Advantage-adjusted sensitivity run.

### E4. What lines up with the 2022 turn? (exploratory, association only)
- **H1 promotion:** monthly distinct physicians with at least one payment tied to Zilretta (payment date inside its program year). *Supported* if, in any month from November 2020 to July 2022, the 6-month mean is at least 25% below the previous 6-month mean. Lagged correlations (0 to 6 months, first differences, block permutation, Bonferroni) are reported alongside. Physicians only; nurse practitioners and assistants as a sensitivity run.
- **H2 price:** the J3304 payment limit per mg relative to J3301 per mg (J3301 is quoted per 10 mg). *Supported* if the ratio changes by at least 10% between 2020Q2 and 2021Q2.
- **H3 pass-through end (31 March 2021):** decided by the existing result: the detected break is March 2022, outside ±6 months of April 2021, so *not supported as a direct driver*. The Medicare facility-versus-office trend is description only.
- **H4 the March to July 2024 dip:** *consistent with a data-capture artefact* if all three IQVIA categories are at least 10% below the same months of 2023 (the claims-clearinghouse outage began 21 February 2024).

### E5 (conditional) and E6
- **E5:** only if H1 shows a Bonferroni-significant lag, lagged promotion is added to the share forecast and judged by the existing serving rule (promote only if the 1.67th percentile of paired improvement is above zero). Otherwise it is skipped and the reason recorded.
- **E6:** descriptive tables only: Zilretta against the hyaluronic injectables (Medicare patients and provider counts by year) and company revenue. No pass or fail.

## 4. Warehouse design (an extension; existing tables are never altered)

A **fact constellation**: more fact tables sharing conformed dimensions where they genuinely match.

- **Dimensions:** `dim_state`, `dim_hcpcs_code` (code, drug family, group, valid years), `dim_year`, `dim_quarter` (linked to `dim_month` through a month-quarter-year hierarchy), `dim_event`, `bridge_specialty_crosswalk`, `bridge_drug_family`.
- **Facts** (declared grain): `fact_ext_partb_provider` (year × NPI × code × setting), `fact_ext_partb_geo` (year × state × code × setting), `fact_ext_partd_geo` (year × state × drug), `fact_ext_asp_price` (quarter × code), `fact_ext_openpay_month` (month × specialty × state × product), `fact_ext_company_revenue` (quarter × company × product), `fact_ext_geo_variation` (year × state), `fact_ext_provider_counts` (snapshot × specialty × state), `fact_ext_arthritis_prevalence` (year × state).
- **Gold:** `gold_ext_specialty_triangulation`, `gold_ext_state_adoption`, `gold_ext_promotion_monthly`, `gold_ext_price_quarterly`, `gold_ext_company_vs_visits`.
- **Two tiers:** NPI-level facts live only in the local, git-ignored warehouse; the committed published database receives only the small dimensions and Gold tables.
- **Implementation (approved at step 3):** the external tables live in their own local SQLite file, `data/processed/external.db`, with their own schema module (`external/schema.py`), so the IQVIA warehouse cannot be altered by an external load. The two sides join by natural keys (month `YYYYMM`, specialty name, two-letter state). 27 tables, 214 columns; 4 stay local (the NPI-level fact, county prevalence, the NUCC text and the run log). The geographic-variation fact holds national and state rows only.
- **Bronze catalogue:** `bronze_external_files`, loaded from the download manifest (file, source link, checksum, size, row count).
- **Idempotent loads** per source and year, in their own command (`python -m oa_market_intelligence.external`) with a run log, separate from the monthly publish.
- **Lineage:** [`external_data_lineage.md`](external_data_lineage.md) maps every column to its source file and original column; a test fails if any new column is missing from it.

## 5. Ingestion rules (from profiling)

R1 read everything as text and cast in Silver with explicit rules; R2 suppressed small cells are unknown, not zero; R3 in Geographic Variation `*` loads as NULL and placeholder rows ("Territory", "ZZ", no geography code) are dropped; R4 organisation rows (entity code O) are excluded from provider counts; R5 a code-by-year validity table (J1010 and J2919 from 2024; J7331 and J7332 from 2020); R6 office and facility settings are loaded separately; R7 price files are read by column name (five layouts) with the header row located; R8 Open Payments is filtered by product name in any of five slots, never by payer, payment dates must fall inside their program year (one 2024 row is dated year 0002), zero-count and zero-dollar rows are flagged, physicians are separated from practitioners and teaching hospitals; R9 hyaluronic names go through the normalisation map; R10 PLACES uses one data year and one value type per measure (age-adjusted prevalence, approved at step 4c); R11 the registry's state field is cleaned to two letters, blank-entity rows are excluded, and it is treated as one 2026 snapshot; R12 company revenue is transcribed by hand with filing, accession number and page; R13 reconciliation, below.

### Reconciliation targets (tests fail if loading drifts from these)
- Part B provider rows after filtering: 185,295 (2019), 156,538 (2020), 163,580 (2021), 164,243 (2022), 162,299 (2023), 164,940 (2024). J3304 rows 2019 to 2023: 912, 1,088, 1,294, 1,262, 1,181.
- Open Payments 2025 rows mentioning Zilretta: 3,275.
- 26 price quarters, Zilretta's code present in all.
- Every manifest entry verified against its file on disk: 148 files downloaded by script plus 23 files the owner downloaded by hand, recorded afterwards with size and checksum (171 entries).

## 6. Build order and acceptance

Tests first, one pull request per step, your acceptance between steps:
1. Raw profiling (PR #44).
2. This protocol and DL-59.
3. Schemas, lineage skeleton and tests.
4. Readers, then Open Payments.
5. Reference tables (company revenue, events).
6. Analyses E1 to E4 in notebook 11; E5 only if triggered.
7. Results documents, stakeholder summary, README and plan updates.

**Acceptance:** (a) reconciliation tests pass; (b) the lineage test passes; (c) every analysis ends in a verdict label by the rule above; (d) negative and weakened results are reported in the same place as positive ones; (e) the full test suite and CI are green.

## 7. Risks, stated now

- The specialty crosswalk is hand-built; the osteopathic match is weak and flagged.
- With 11 groups the E1 correlation has low power.
- Medicare and IQVIA differ in population, unit and diagnosis information.
- The registry is a single 2026 snapshot, so provider denominators describe today, not the past.
- A code set that shifts over time (J1010) and new products appearing mid-period (for example Trivisc) are handled in the validity table, not assumed away.
- AMA copyright covers code descriptions and the taxonomy list: raw copies stay local, and the repository holds only derived tables and our own mapping.

## 8. Implementation choices made while running step 6 (recorded afterwards, DL-60)

The rules above were not changed. Where the text left a detail open, the choice made in code is listed here, with whether it was made before or after related numbers had been seen.

- **H4, pooled or month by month.** The rule says "all three categories at least 10% below the same months of 2023". The code pools March to July (sum of 2024 against sum of 2023, per category). This was chosen *after* the monthly IQVIA counts had been looked at while exploring, so it is not a clean choice. Read one month at a time, the rule fails in July 2024 (NSAID and OTC visits down only 2%). Both readings are stored (`all_three_below`, `months_all_three_below`); the verdict uses the pooled one.
- **E2b, the first quarter.** IQVIA starts in August 2019, so its third quarter of 2019 has two months. The third quarter of 2020 is compared with the year before on August and September only. Company sales use full quarters. Decided before E2b was run.
- **E2b, fourth quarters.** Fourth-quarter sales are derived (the year minus nine months) and used, including the fourth quarter of 2021 that spans the acquisition (see disclosure 6). An earlier plan was to treat that quarter as partial and leave it out; it was kept after the derivation was verified.
- **H1, practitioners.** Nurse practitioners and assistants appear in Open Payments only from January 2021. The sensitivity run (physicians plus practitioners) therefore starts then, and only months from December 2021 can be tested against the 12-month look-back.
- **H1, the lag test.** Promotion is the number of distinct physicians with a Zilretta payment in the month; the comparison series is IQVIA's monthly visit share. The correlation is Pearson on first differences, promotion leading share by 0 to 6 months; blocks of 6 consecutive months of the promotion changes are shuffled (2,000 permutations, seed 0); the p-value is two-sided, (1 + count of permuted correlations at least as large in absolute size) / 2,001; Bonferroni level 0.05 / 7.
- **H2, units.** The J3304 limit is per 1 mg and the J3301 limit is per 10 mg, so J3301 is divided by 10 before the ratio is formed. The change is judged in either direction.
- **H3.** The stored break (March 2022, the only one in the published findings) is compared with April 2021.
- **E3, national rate.** The national rate used for headroom is pooled over all visible provider-years in the 50 states and DC, including state-years below the 30-provider reporting line (all states reach 30 in every year in the real data, so this made no difference).
- **E3, the gate.** The rank correlation is taken between each state's 2022 and 2024 adoption rate among states with at least 30 visible providers in both years; the verdict uses the point estimate, the paired bootstrap interval is information.
- **E3, Advantage-adjusted sensitivity.** The protocol names the run without defining it. The code regresses 2024 state adoption on the state's all-ages Advantage participation rate (Geographic Variation file, same year), adds the residual to the national rate, recomputes headroom, and reports the rank correlation with the unadjusted headroom (stable if 0.7 or more, the same line as the gate). Arthritis prevalence is the 2023 county estimate, population-weighted to state level, with no year dimension.
- **E1, extra readings.** Limiting IQVIA to ages 65 and over and dropping the osteopathic group are sensitivity runs added to E1; the E1 verdict comes from the all-ages, all-groups run.
- **Provider attributes.** A provider's state and specialty in a year are taken from their first row for that year.

