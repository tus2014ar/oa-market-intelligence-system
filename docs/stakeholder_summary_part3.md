# What public data adds: Part 3 summary

**For:** the OA and RA Injectable Brand Manager. **Data:** public Medicare, CDC, price, company-filing and payment-disclosure data, 2019 to 2025, set against the IQVIA NMTA extract used in Parts 1 and 2. **Status:** draft for the project owner's review, 8 Oct 2026. **Scope:** whether independent sources agree with the IQVIA findings, where Zilretta is under-used by state, and what lines up with the early-2022 turn. Evidence and rules: [`external_data_results.md`](external_data_results.md), [`external_data_protocol.md`](external_data_protocol.md), notebooks 11 and 12.

**How to read the numbers.** *Adoption* here means the share of Medicare-billing injectors (individual providers who billed at least one injectable-steroid code in the year) who billed for Zilretta that year. Medicare hides very small counts, so it is "among providers we can see". It is a count of providers, not of patients or visits, and it covers Original Medicare only. All tests were written down, with their pass or fail lines, before the data was analysed. *Association* means two things move together; it does not mean one causes the other.

## The short version

1. **The independent sources do not confirm the IQVIA specialty ranking.** Medicare puts sports medicine and orthopedics first; IQVIA puts physical medicine and rehabilitation first. The sources measure different populations, so read this as "the ranking depends on who you count", not "one is wrong".
2. **Company sales did not fall when IQVIA visits did.** Zilretta's reported net sales kept rising while IQVIA Zilretta visits fell by about half from their 2022 peak; most of that fall is in 2023 and 2024. Medicare adoption eased only by about a sixth. We tested why: the age mix and the care setting do not explain it, and the value-per-visit effect is far too small. **Do not quote the size of the IQVIA decline as a market fact until IQVIA is asked about panel coverage.**
3. **A stable list of states with room to grow exists.** Michigan, Arizona, Florida, Minnesota and Indiana have the most. The list is stable between 2022 and 2024. It is a place to look, not a forecast of what a campaign would win.
4. **Promotion fell before the turn, but nothing ties it to the share month by month.** The fall coincides with the Flexion acquisition, so a real pull-back cannot be separated from a change in reporting.

## 1. Do the specialties rank the same way? No

| | Medicare adoption (2020 to 2024) | IQVIA adjusted share |
|---|---|---|
| Sports medicine | 6.1% (first) | 4.8% |
| Orthopedic surgery | 4.1% | 2.3% |
| Physician assistants | 2.4% | 2.8% |
| Rheumatology | 2.3% | 1.7% |
| Physical medicine and rehabilitation | 1.7% (fifth) | **5.5% (first)** |
| Nurse practitioners | 0.8% | 3.3% |

The rank correlation is 0.35 against the 0.6 we required, and one of the top three is shared (two were required). It does not change when IQVIA is limited to patients aged 65 and over. **Use:** do not present a single specialty order to the field force; say which source it comes from. Medicare shows who bills in Original Medicare; IQVIA shows visits in its own panel.

## 2. Sales and visits disagree (the finding to take seriously)

Company net sales and IQVIA Zilretta visits moved the same way in only 9 of 20 quarters (45%; 70% was required). Through 2021 they mostly rose together; sales then kept rising (about $105 million in 2022, $117 million in 2025) while IQVIA visits fell by about half from their 2022 peak. **By full years the gap opens in 2023 (IQVIA visits -14%, sales +5%) and is largest in 2024 (-36% against +6%).**

**What we tested (a follow-up, rules written before the run):**

| Idea | Result | In plain words |
|---|---|---|
| The populations differ (age or payer mix) | **No** | Even IQVIA's visits for patients aged 65 and over fell by a third, while Medicare's Zilretta patients per 1,000 beneficiaries were flat |
| Zilretta moved to a care setting IQVIA covers poorly | **No** | In Medicare it is almost entirely an office product, and that did not change |
| Each visit is worth more | **Only a small part** | Value per Medicare patient rose about 10%; the gap needs about 94%. The rule passes on direction, but not with 2022 as the base year |
| The fall sits in a few specialties | **Yes** | Orthopedic surgery and physician assistants account for about two thirds of IQVIA's fall. Medicare providers fell in orthopedic surgery but *rose* for physician assistants, so that is where the sources diverge most |

**What is still open (untested).** IQVIA's panel may cover fewer Zilretta settings or specialties than before. 2024 is also the year of the early-2024 IQVIA dip, when corticosteroid and NSAID visits fell too (-18% and -8%) and a claims-clearinghouse outage is recorded; Zilretta fell about twice as much, so that cannot be the whole story. And company-side causes (inventory, gross-to-net adjustments, channel mix) need data we do not hold. **Use:** treat "Zilretta's share has fallen" as a statement about IQVIA's panel until another source supports the size of the fall. Ask IQVIA whether panel coverage changed, especially for physician assistants and orthopedic surgery and during 2024.

*A caution on this test:* it is weakened (we had seen some Medicare counts before writing its rule), and company sales are national and all-payer.

## 3. Where is there room? A stable state list

Adoption by state is steady between 2022 and 2024 (rank correlation 0.92), so the ranking passes our rule for use. The list below is ordered by *headroom*: how many more providers would be billing Zilretta if the state matched the national rate.

| State (2024) | Visible providers | Billing Zilretta | Adoption |
|---|---|---|---|
| Michigan | 1,935 | 3 | 0.2% |
| Arizona | 1,915 | 5 | 0.3% |
| Florida | 5,331 | 59 | 1.1% |
| Minnesota | 1,066 | 0 | 0.0% |
| Indiana | 1,569 | 12 | 0.8% |

Highest adoption: Delaware, New Hampshire, Massachusetts and Illinois. **Cautions.** The same providers appear in both years, which makes states look steadier than independent samples would. We have not found out why Michigan or Minnesota are near zero; local Medicare coverage rules are one possibility and are **untested**. Medicare Advantage share explains almost none of the ranking (removing it barely changes the order). **Use:** a starting list for a territory or education review, after checking coverage rules, not a forecast.

## 4. What lined up with the early-2022 turn?

| Idea | Verdict | In plain words |
|---|---|---|
| Promotion fell (payments to physicians) | **Supported** | The number of physicians paid fell about 81% in the sharpest six months, starting in November 2021, four months before the turn. No month-by-month link to the share. Same month as the Flexion acquisition |
| The relative price rose | Supported, **not a clean test** | The ratio to triamcinolone rose 12%, but because the comparator's price fell, not because Zilretta's rose |
| The end of pass-through status (March 2021) | **Not supported** | The turn is 11 months later |
| The 2024 dip is a data-capture problem | **Partly** | All three categories fell, but by very different amounts (Zilretta -47%, steroids -25%, NSAID -16%), and one month fails the test |

Adding promotion to the forecast was **skipped** because the lag test found nothing.

## What this does not tell you

- It does not say what caused the turn. Every item above is an association at best.
- Medicare is a different population from IQVIA, with annual data and no diagnosis; only rankings and directions are compared, never levels.
- Several tests were weakened or are not clean, and are labelled.

## What would help most

1. From IQVIA or the instructor: whether the panel's coverage of Zilretta settings changed after 2021, and any prescription-volume or regional data.
2. A look at Medicare coverage rules in the near-zero states.
3. Pacira's own account of the 2022 to 2025 sales mix, if the brand team has it.
