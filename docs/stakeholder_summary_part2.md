# What the models can and cannot tell you: Part 2 summary

**For:** the OA and RA Injectable Brand Manager. **Data:** IQVIA NMTA patient-visit extract, osteoarthritis (OA), Aug 2019 to Jul 2025, 72 months. **Status:** draft for the project owner's review, 8 Oct 2026. **Scope:** Part 1 ([`stakeholder_summary_part1.md`](stakeholder_summary_part1.md)) explained what changed and where. This part asks what the prediction work adds (business questions 2 and 4, and a use of the segment results).

**How to read the numbers.** *Share* means Zilretta's visits as a fraction of visits for Zilretta, generic corticosteroid injections and NSAIDs together. *pp* means percentage points. *Held-out months* are months a model never saw while it was being built: each month is predicted using only earlier months.

## The short version

1. **A simple model gives a better estimate of a segment's current share than "same as last month", mostly for small segments.** The gain is real and consistent but modest. For large segments it adds almost nothing.
2. **Next month's overall share is best estimated by this month's.** Five alternatives were tried; none is better. Expect to be within about 0.12 pp (about 5% of the level).
3. **We found no reliable way to predict whether next month's share will move Up, Flat or Down.** Three model families failed. The data cannot rule out a modest effect; it could only have shown a very large one.
4. **Monitoring rules exist but are limited.** One catches big jumps in volatility with almost no false alarms; neither would have noticed the slow decline since 2022.

## 1. Estimating a segment's share (useful, modestly)

A segment is a combination of specialty, age band and gender. Its Zilretta share in any one month is noisy, especially when it has few visits. A model that blends the segment's last three months with its longer history (and, a little, its specialty's level) gives a better estimate of the next month's share than just repeating last month.

| | Error vs "same as last month" |
|---|---|
| Segments with **under 50** visits last month | about **36% lower** |
| Segments with 50 to 300 visits | about 18% to 29% lower |
| Segments with **over 300** visits | about 9% lower (log-loss gain close to zero) |
| All segments, weighted by visits | about 12% lower |

It beat "same as last month" in 44 of 46 held-out months on the main score and in every specialty, and the result held when the known data problems were removed. Three different model types gave the same answer, so this reflects the information in the history, not a particular algorithm.

**How you could use it.** For a field-force target list or a segment review, use these estimates in place of the raw latest-month share, so a thin segment's bad or good month is not mistaken for a trend.

**Two cautions.**
- **It runs about 7% high** in the period tested (a falling market): about 2.65% predicted against 2.48% observed. It would run low in a rising market. Use it to rank and compare segments, not to forecast the level.
- It estimates a segment's share; it does not explain what moves it.

## 2. Forecasting next month's overall share (no better option than "same as last month")

We tested last month's value, the same month last year, two exponential-smoothing models and a regression on recent months. None beat repeating last month; their errors were 18% to 27% higher. This is what you would expect for a smooth series: this month is already a very good guide to the next.

| | |
|---|---|
| Forecast | Last month's value |
| Typical error | about **0.12 pp** (about 5% of the level) |
| 90% range | about **0.64 pp** wide (roughly ±0.32 pp) |

The range contained the actual value 96% of the time in the held-out months (slightly conservative), and missed twice in 48 months (January and March 2023).

## 3. Can we predict Up, Flat or Down? (no reliable signal found)

**What was asked.** Whether a model can say, a month ahead, whether the share will move up, down or stay flat. Of the 72 months of data, the first 13 cannot be labelled (the label compares a month's change with the previous 12 months' changes), leaving 59 labelled months, and **35 months were held out for testing** (25 Flat, 6 Down, 4 Up).

**What we found.** Four simple rules and three model types (logistic regression, random forest and gradient boosting) were compared. None beat the simple "seasonal" rule, none beat persistence, and none was better than random guessing could produce (balanced accuracy 0.33 to 0.41 against a chance ceiling of 0.46). All three models memorise the months they train on (0.86 to 1.00 accuracy there) and learn nothing that carries over (0.33 to 0.41 on new months).

**What this does and does not mean.** "No signal found" can mean "there is none" or "there is some, but 35 months cannot show it". We measured which: a test this size would detect an improvement over persistence **only if it were about 30 percentage points** (from 63% to about 93% right). A 10-point improvement would be caught about 1 time in 10. So the honest statement is: *a large, usable direction forecast does not exist in this data, and a modest one cannot be ruled out.*

**How you could use it.** Don't plan around a model's direction call. The site serves a simple seasonal rule for this reason, and says so.

## 4. Monitoring: when should a flag be raised?

**Direction.** The served rule matched the actual direction in 66% of the held-out months (plausible range 49% to 77%). Always predicting "Flat" would match 71%, so match rate alone misleads; the project judges on balanced accuracy. A sensible review flag is a **rolling six-month match rate of 33% (2 of 6) or below**, the level chance alone produces about 1 time in 20 if performance is stable. Because the rule has little skill to begin with, a flag means "worse than its own history", not "was ever good".

**Forecast.** An alarm that fires when at least 3 of the last 6 months fall outside the 90% range has almost no false alarms (about 0.4% of windows). But it is **insensitive**:

| What changes | Caught within 6 months |
|---|---|
| Month-to-month noise doubles | about 54% of the time |
| Month-to-month noise rises by half as much | about 14% |
| Share drifts 0.3 pp a month | about 24% |
| Share drifts 0.2 pp a month or less | **never** |

The real decline since 2022 runs at about 0.03 pp a month, so this alarm would not have seen it. A forecast that repeats last month also adapts to a sudden level shift after one month, so the alarm cannot see those. A monitor for slow drift would be a natural addition; it has not been built.

## What this does not tell you

- **Why share moves.** The data has no payer, geography, price, promotion or competitor-activity information. The models use only history.
- **Anything about RA.** About 1,283 RA visits in six years are too few to model or monitor.
- **That prediction is impossible.** It says what this data cannot resolve.

## What would make it more useful

- **Not more of the same inputs.** We tested seven extra feature families (a segment's activity history, its specialty's recent trend, a seasonal gap, price, company sales, event timing, Medicare adoption) under rules written before the run. Two lowered the segment error by about 0.06%, which is too small to matter, so nothing changed; none helped the overall share forecast ([`feature_results.md`](feature_results.md)).
- **Extra inputs** that could explain moves history cannot: payer or formulary changes, geography, price, and prescription volume.
- **A longer history,** so a modest effect becomes detectable.

## Where each number comes from

| Finding | Evidence | Decisions |
|---|---|---|
| Segment share estimates | [`notebooks/08_segment_share_prediction.ipynb`](../notebooks/08_segment_share_prediction.ipynb), [`model_card_segment_share.md`](model_card_segment_share.md) | DL-48 to DL-51 |
| Overall share forecast and the alarm | [`notebooks/09_forecast_monitoring_and_direction.ipynb`](../notebooks/09_forecast_monitoring_and_direction.ipynb), [`model_card_share_forecast.md`](model_card_share_forecast.md) | DL-52, DL-53, DL-55 |
| Direction and the power statement | the same notebook, [`model_card_logistic_regression.md`](model_card_logistic_regression.md) | DL-54, DL-56 |
