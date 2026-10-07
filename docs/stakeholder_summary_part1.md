# What the data says about Zilretta's share: Part 1 summary

**For:** the OA and RA Injectable Brand Manager. **Data:** IQVIA NMTA patient-visit extract, osteoarthritis (OA), Aug 2019 to Jul 2025, 72 months. **Status:** draft for the project owner's review, 7 Oct 2026. **Scope:** Part 1 answers the trend question (Q1) and the segment question (Q3). Prediction and monitoring (Q2, Q4) are reported separately.

**How to read the numbers.** *Share* means Zilretta's visits as a fraction of visits for Zilretta, generic corticosteroid injections and NSAIDs together. *pp* means percentage points: a move from 3.0% to 2.0% is 1 pp. *90% range* means the range we would expect the true figure to fall in 9 times out of 10, given the data; it is wider than a simple calculation would give, because visits are not independent of each other.

## The short version

1. **Zilretta's share rose until early 2022 and has fallen since.** It peaked at 3.38% in December 2021 and averaged 1.88% in the year to July 2025. The turn is statistically clear and does not depend on method choices.
2. **The fall happened inside specialties, not because of who is prescribing.** Zilretta lost share within the four largest specialties (about 90% of visits) and most smaller ones. The change in the mix of specialties (for example, more visits from physician assistants and fewer from orthopedic surgeons) did not cause it.
3. **Adoption differs a lot by specialty, and the pattern is stable.** Physical Medicine & Rehab and Sports Medicine use Zilretta well above the market-wide share. Orthopedic Surgery, the largest specialty at 43% of visits, sits below it. Family Practice, Internal Medicine and Rheumatology are lower still.
4. **We cannot say why.** The data has no payer, geography, price, promotion or competitor-activity information. Everything here describes *what* changed and *where*, not *why*.

## Q1: How has share shifted, and where is the inflection point?

**What we found.**

| Period (Aug to Jul) | Average share |
|---|---|
| 2019–20 | 2.19% |
| 2020–21 | 2.72% |
| **2021–22 (peak year)** | **2.99%** |
| 2022–23 | 2.75% |
| 2023–24 | 2.22% |
| 2024–25 | 1.88% |

- **The turn:** a statistical test finds one change in direction, at about **March 2022** (90% range: May 2021 to July 2022). Before it, share rose about 0.5 pp a year; after it, it fell about 0.4 pp a year.
- **Generic corticosteroids** (about 90% of category visits) and **NSAIDs** (about 7%) show no reliable change in direction over the same period.
- **Not linked to an FDA event we can see.** We looked up the FDA approval date of every branded product in the share formula. None was approved between Aug 2019 and Jul 2025. The only approval in the window (Anjeso, February 2020) is outside the formula and has 2 visits in the whole dataset. Zilretta's own approval (October 2017) is before our data starts, so its market entry cannot be observed.
- **Not caused by COVID or the 2024 disruption.** The turn is not near either (COVID: Mar–May 2020; a volume dip: Mar–Jul 2024), and it is unchanged when those months, or the suspected mis-coded pediatrics prescriber, are removed.

**What explains the fall: the mix of specialties, or Zilretta's share inside them?** From the peak year to the latest year, share fell 1.11 pp:

| | Effect on share |
|---|---|
| Change in the mix of specialties | **+0.08 pp** (slightly favourable) |
| Change in Zilretta's share *within* specialties | **−1.20 pp** |

The mix really did move: Orthopedic Surgery fell from 45% to 35% of visits, and Physician Assistants rose from 26% to 38%. But these two moves nearly cancel out for Zilretta. What happened is a broad decline inside the big specialties (Zilretta's share fell in the four largest specialties and in most smaller ones; it rose in two, Physical Medicine & Rehab, from 5.11% to 7.39%, and Family Practice, from 0.27% to 1.40%):

| Specialty | Zilretta share, peak year | Latest year |
|---|---|---|
| Orthopedic Surgery | 2.52% | 1.53% |
| Physician Assistants | 3.19% | 1.88% |
| Nurse Practitioners | 4.56% | 2.44% |
| Osteopathic Medicine | 3.70% | 1.66% |
| Sports Medicine | 5.70% | 2.52% |

**How sure are we?** *High* that the share turned around early 2022 and that the fall is within specialties, mainly the four largest. *Moderate* on the exact date (the range is 14 months wide). *None* on the cause.

## Q3: Does adoption vary by specialty?

**Yes, strongly.** Specialty accounts for about half of the explainable variation in adoption (age band accounts for about as much; gender, almost none). To compare fairly, the figures below are *adjusted shares*: Zilretta's share if every specialty had the same mix of months, ages and genders. The overall share is 2.49%.

**Clearly above the market-wide share**

| Specialty | Adjusted share (90% range) | Share of visits |
|---|---|---|
| Physical Medicine & Rehab | 5.34% (4.73–5.93) | 1.8% |
| Sports Medicine | 4.52% (4.03–5.04) | 2.8% |
| Nurse Practitioner | 3.18% (2.92–3.46) | 8.0% |
| Osteopathic Medicine | 2.89% (2.64–3.13) | 7.6% |

**Clearly below it**

| Specialty | Adjusted share (90% range) | Share of visits |
|---|---|---|
| **Orthopedic Surgery** | **2.17% (1.98–2.33)** | **42.8%** |
| Rheumatology | 1.67% (1.41–2.01) | 1.8% |
| Family Practice | 0.62% (0.53–0.71) | 2.9% |
| Internal Medicine | 0.45% (0.32–0.60) | 1.2% |

**Not clearly different from the market-wide share:** Physician Assistants 2.65% (2.44–2.86), Pain Medicine 2.44% (2.22–2.69) and Anesthesiology 3.08% (2.48–3.87).

**Is the pattern stable?** We fitted the first and last three years separately. The ranking agrees (rank correlation 0.78), and the eight specialties above keep their side in both halves. Two do not: Anesthesiology and Pain Medicine moved from above to below the overall share, so we would not feature them. Overall share fell between the halves (2.66% to 2.31%), and most specialties fell with it. Physical Medicine & Rehab is the exception, rising from 3.99% to 6.66%.

**How sure are we?** *High* for the eight clear positions: they hold in both halves and under every robustness check. *Low* for Anesthesiology and Pain Medicine, and for the exact ordering in the middle of the table.

## Did we check these findings are not artefacts?

Yes. We re-ran everything on the baseline and again without (a) the suspected mis-coded pediatrics prescriber, (b) the COVID months and (c) the Mar–Jul 2024 volume dip. The pass/fail tests were written down before the re-run. **All five headline findings held every time:** the turn in early 2022, the fall being within specialties, the specialty differences, the eight clear positions, and the stable pattern.

Two refinements. The net fall from the first year to the latest year shrinks from 0.32 pp to 0.22 pp without the COVID months, because April 2020 was unusually high; the peak-to-latest fall (1.11 pp), the more decision-relevant figure, is unaffected. And the pediatrics anomaly inflates the grouped "other specialties" figure (1.70% to 1.08% without it) but changes none of the named specialties.

## What this does not tell you

- **Why share fell.** Payer coverage, pricing, supply, sales-force activity and competitors' behaviour are not in the data. Describing where share fell is not the same as explaining it.
- **What will happen next.** Nothing here is a forecast. Whether next month's direction can be predicted is a separate question, reported separately, and so far the answer is no.
- **Individual physicians, or segments not in the data.** Findings are about specialties and groups of visits, not about any prescriber.
- **RA.** There are about 1,283 RA visits in six years, too few for the analysis above.
- **Causal advice.** A specialty sitting below the market-wide share is a *lead to investigate*, not proof of an opportunity: the gap may reflect patient mix, access or practice setting that the data cannot see.

## Leads worth following up

- **Orthopedic Surgery** is the largest specialty by visits and sits below the market-wide share. It is where the most volume is, and where share lags most in absolute terms.
- **The broad decline inside the large specialties** points to something that affected all of them at once, such as access, coverage or competing options, rather than to one specialty's behaviour. Information on payers or formulary changes would be the natural next data to add.
- **Physical Medicine & Rehab and Sports Medicine** show above-average adoption that is stable over time, and Physical Medicine & Rehab is rising while most other specialties fall. Worth understanding what differs in how they use Zilretta.

## Where each number comes from

| Finding | Analysis | Code |
|---|---|---|
| Share by year, turn at March 2022, range | Calibrated change-point test with bootstrap range | `analysis/trend.py` |
| FDA approval dates | openFDA lookup of 70 branded products, 7 Oct 2026 | `data/reference/openfda_competitive_set_approvals_2026-10-07.csv` |
| Mix versus within-specialty effects | Two-term decomposition over six 12-month windows | `analysis/decomposition.py` |
| Adjusted shares, ranges, stability | Binomial model on visit counts, two bootstraps, first-versus-last-half fit | `analysis/adoption.py` |
| Robustness | Re-run on three exclusions against pre-set rules | `analysis/sensitivity.py` |

Decisions and the reasoning behind each method are in `docs/decision_log.md` (DL-44 to DL-47); the plan is `docs/phase4_modeling_plan.md`.
