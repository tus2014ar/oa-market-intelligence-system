# The promotion gate (DL-126)

Every monthly run refits the adopted models. The gate decides whether the refit may replace the models in service. It is the `promotion` stage of the publish run, between `scoring` and `monitoring`, and a failed check stops the run **before the swap**: nothing new is published, the models in service keep serving, and the run log says why.

**Status: live.** It ran on the real warehouse locally and then in the Monthly Pipeline on GitHub's machine (7 Nov 2026): **promote, 11 checks, 0 failed**, both months 202507, the whole stage taking 0.04 seconds. The run committed its record and published the release to S3, and the live API stayed current (the deploy that followed found it already serving the repository's data version). Only the no-new-data case (reproducibility) has run for real; the stability checks have not judged a real new month.

## What it compares

The **challenger** is the refit in the staged database. The **champion** is the models in the currently published database (`pred_batch` and `model_artifacts`). With no published database there is no champion, and only the sanity checks apply.

## The checks, and where each tolerance comes from

The tolerances were fixed before any run, from two measurements on the real data (7 Nov 2026).

| Check | When | Limit | Evidence |
|---|---|---|---|
| **Sanity** | always | every forecast finite and between 0 and 1; alert scores between 0 and 1; no duplicate keys; the models of the adoption map present; each saved model equal to its recorded SHA-256 with finite coefficients | structural |
| **Reproducibility** | the challenger starts from the same month as the champion (the data did not change) | forecasts equal to **1e-6** percentage points; coefficients to **1e-9**; alert flags, model fingerprints and the set of segments identical | two builds of identical data on different machines differed by **2.8e-11** points and **6.5e-13** (floating-point round-off): the limits leave four orders of magnitude of margin |
| **Stability** | the challenger starts from a later month | for the fitted 1- and 3-month models: volume-weighted mean change at most **0.6** points; no segment of 100 or more visits moving more than **5** points; at least **90%** of the champion's segments kept; the month must move forward | refitting at 18 consecutive past months moved forecasts by at most 0.29 points weighted, 2.5 points for one large segment, and kept at least 94% of segments: the limits are about twice the largest ever seen |

The 6-month model is a rule (last month's value), not a fit, so it is exempt from the stability limits (it moves with the data by design) but is held to reproducibility.

## What it does not do

It does **not** decide which model is better. That needs outcomes for months that have not arrived and is the pre-registered modelling test (DL-79, `oa-model --run`), which stays a human step. The gate guards against a change in *behaviour*: broken output, a code or library change that alters results while the data did not change, or a forecast that jumps further than the models ever have between months. Realized skill is reported by the `monitoring` stage.

With no new IQVIA extract, every run compares a refit of the same data, so the gate confirms "the same models" (the reproducibility checks). Its stability checks matter when a new month arrives. They were checked on the history of real refits, not on a real new month.

## When it refuses

The run stops at `promotion`, the published database is untouched, the failure is the run's `error` (each failed check with its number and its limit) and the job turns red. Read the run summary, then either fix the cause or, if you have reviewed the forecasts and accept them, publish anyway with a written reason:

- locally: `oa-publish --accept-challenger "reviewed: what and why"`;
- on GitHub: **Actions**, **Monthly Pipeline**, **Run workflow**, and type the reason in the `accept_challenger_reason` box.

A blank reason does nothing. The reason is stored in the run record and in every row of `promotion_log`, with the failed checks still marked failed, so an override can never pass for a clean promotion.

**The cost of this design:** a refused refit also holds back that month's data, because the whole warehouse is swapped in as one file. The alternative (publish the data, keep the champion's numbers, apply them to the new inputs) was considered and not built: it changes how the API chooses its forecast month and adds a path to test.

## The record

- `promotion_log` (in the warehouse): one row per check with its value, limit, result and detail, the decision, the two months and data versions compared and any override reason. Deterministic: no timestamps, numbers to 6 significant figures.
- The run record has a `promotion` entry (decision, number of checks, failed checks, override reason) and the job summary prints a "Promotion gate" line.
- The MLflow store gets a **Promotion decisions** experiment with the decision as one run: every check's value and limit as metrics (`docs/tracking.md`).

## Tested

`tests/test_promotion.py` (35 tests on small made-up worlds, each tolerance on both sides of its edge; the stage on real SQLite files; and inside the whole publish run: first release, the same models again, a refused refit leaving the champion's bytes untouched, an override recorded in the run and the table, a crash in the gate stopping the run), the existing publish, end-to-end and published-warehouse tests (updated for the new stage), and the tracking tests. On the real warehouse the gate passed with the numbers above.
