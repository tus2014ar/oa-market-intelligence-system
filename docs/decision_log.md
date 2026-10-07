# Decision Log

Every significant decision on this project, why it was made, what was rejected, and where it is recorded. Entries are chronological within each part. Add a new entry whenever a decision is made or changed; do not edit an old one, mark it **Superseded** and link the new one.

**Status values:** **Decided** (in force), **Superseded**, **Proposed** (suggested, not yet approved), **Open** (a question waiting on a decision).

## Index

| Part | Entries |
|---|---|
| A. Scope and design (Sep 2026) | DL-01 to DL-10 |
| B. Pipeline and data rules (Sep 2026) | DL-11 to DL-18 |
| C. Analysis, features and evaluation (Sep to Oct 2026) | DL-19 to DL-27 |
| D. How we work | PR-01 to PR-03 |
| E. Direction from here (proposed) | PD-01 to PD-08 |
| F. Open questions | Q-01 to Q-05 |

---

## A. Scope and design

**DL-01 · 6 Sep 2026 · Predict a direction (Up / Flat / Down), not an exact value.** Decided.
*Why:* the brand manager acts on "it is slipping", and a three-way label tolerates noise better than a point forecast. *Rejected:* a numeric share forecast as the main target. *Recorded in:* PROPOSAL (Objective 2) and §18.2.

**DL-02 · 6 Sep 2026 · RA is analysed, not modeled.** Decided; confirmed with data on 1 Oct 2026.
*Why:* about 1,250 RA visits in six years, 75% of them one product (Ilaris), far too few to learn from. Notebook 3 found Zilretta alone has about 110 times RA's monthly volume. *Recorded in:* PROPOSAL §6.2, notebook 3 (#24).

**DL-03 · 6 Sep 2026 · Persistence is the mandatory baseline.** Decided. A model must beat "next month repeats last month". *Recorded in:* PROPOSAL §6.1.

**DL-04 · 16 to 17 Sep 2026 · SQLite through SQLAlchemy; local-first development.** Decided.
*Why:* one file, no server, enough for about 150 thousand rows, easy in CI. DVC remote and cloud hosting wait until there is something to demo. *Recorded in:* PROPOSAL §8, §19.6 (#4, #7).

**DL-05 · Sep 2026 · Bronze / Silver / Gold layers with a star schema.** Decided. Four dimension tables, two fact tables (place of service has its own grain), two Gold tables. *Recorded in:* `docs/database_schema.md`.

**DL-06 · 16 Sep 2026 · Zilretta's launch date is October 2017**, confirmed from openFDA, so there is no launch inflection in the data window and "months since launch" is a clock feature. *Recorded in:* PROPOSAL §18.5 (#2).

**DL-07 · 17 Sep 2026 · Commit the real data and a hand-built product taxonomy.** Decided.
*Why:* the brand/generic tag is a patent label, not a drug class (ibuprofen is tagged "other"), so every product was reviewed and mapped. Unknown products are flagged, never guessed. The instructor approved committing the extracts and sending aggregate (never raw) data to the Claude API. *Recorded in:* README, PROPOSAL §3.1, §18.10 (#8, #27).

**DL-08 · Sep 2026 · Group by product name only; manufacturer is display-only and is not standardised.** Decided.
*Why:* Zilretta's manufacturer changed mid-window, and grouping by manufacturer would understate its share by about 8%. Name standardisation was weighed and left out because the field is never used for grouping. *Recorded in:* PROPOSAL §10, `docs/data_dictionary.md`.

**DL-09 · Sep 2026 · Visit share = Zilretta ÷ (Zilretta + generic corticosteroid + NSAID visits), per month, OA only, from product-level pivot counts.** Decided.
*Why:* those three are the competing choices for the decision. Opioids are excluded. The pivot, not the reference file, is the source because the two count differently. Visit share is a share of *product-visits* (counts are distinct per row, so product rows sum to about 4.5% more than the grand total). *Recorded in:* PROPOSAL §18.1, `docs/data_analysis_reference.md`.

**DL-10 · 17 Sep 2026 · No formal fairness audit applies.** Decided. The data is aggregated visit counts with no individuals. *Recorded in:* PROPOSAL §14 (#9).

---

## B. Pipeline and data rules

**DL-11 · 21 Sep 2026 · A strict Pandera validation gate sits before Silver.** Decided. Allowed-value lists per disease area (OA and RA differ), no unexpected columns, positive counts. Bad data is rejected, never repaired. *Recorded in:* `ingestion/validation.py` (#15).

**DL-12 · 21 Sep 2026 · OA versus RA is read from file content (header format, codes), never from the file name.** Decided, and cross-checked against the sheet name. *Recorded in:* `nmta_loader.py`, `reference_loader.py` (#11, #13).

**DL-13 · 21 Sep 2026 · A blank pivot cell means zero visits and is dropped; a blank cell in the reference table is kept as 0.** Decided.
*Why:* in the pivot, absence is the information (95% blank); in the reference table a row's existence is the information. *Recorded in:* `nmta_loader.py`, `reference_loader.py`.

**DL-14 · 21 Sep 2026 · Dimension tables are upserted; fact and Gold tables are fully rebuilt.** Decided. Upsert keeps surrogate keys stable (the segment table's key depends on them); full refresh is safe where nothing points to the row IDs. *Recorded in:* `build_silver.py` (#17).

**DL-15 · 21 Sep 2026 · Sum visits across manufacturer to reach the fact table's grain.** Decided. A real slice had 13 manufacturers; total visits were verified unchanged (5,546,090 before and after). *Recorded in:* `build_silver.py`, `docs/database_schema.md`.

**DL-16 · 21 Sep 2026 · A product's single manufacturer and brand/generic tag are resolved by most visits; a product absent from the taxonomy is flagged `unclassified`, distinct from one reviewed and deliberately left `unclassified`.** Decided (membership is checked before mapping). *Recorded in:* `build_silver.py` (#17).

**DL-17 · 21 Sep 2026 · The parser verifies the pivot's own arithmetic and stops loudly on failure.** Decided. Product ≤ manufacturer subtotal ≤ sum of products; grand total equals the sum of the 72 months. Inequalities, not equality, because counts are distinct. *Recorded in:* `nmta_loader.py` (#11).

**DL-18 · 21 Sep 2026 · One CLI command runs the pipeline; GitHub Actions runs lint and tests on every push and a monthly scheduled run that uploads `warehouse.db` as a build artifact.** Decided, as a stand-in until a DVC remote exists. *Recorded in:* `pipeline.py`, `.github/workflows/` (#20).

---

## C. Analysis, features and evaluation

**DL-19 · 30 Sep 2026 · The Up/Down/Flat threshold is each month's change measured in standard deviations of the previous 12 months' changes (±1), replacing the fixed ±1.0 percentage points.** Decided.
*Why:* monthly changes have a standard deviation of only 0.19 points, so the fixed rule labelled all 71 months Flat. The new rule is leak-free (past data only) and gives 7 Up / 43 Flat / 9 Down over 59 labelled months. *Rejected:* whole-period standard deviation (uses future data), other fixed thresholds. *Recorded in:* PROPOSAL §18.2, `build_gold.py` (#23).

**DL-20 · 3 Oct 2026 · Rolling averages cover prior months only.** Decided.
*Why:* the original included the current month, so `roll_3mo` correlated 0.95 with the target (more than last month's value, 0.90) by construction. Fixed to `shift(1)` windows; the correlation is now 0.88. A test enforces it. *Recorded in:* `build_gold.py` (#26).

**DL-21 · 3 Oct 2026 · Notebooks, docs and deliverables are reconciled to the live warehouse.** Decided. A full reconciliation (raw re-parse, DB recompute, notebook re-execution) matched the data exactly and found stale narrative, which was corrected. *Recorded in:* #25.

**DL-22 · 5 Oct 2026 · Model features are computed on demand by a `features/` module from the Gold tables, not stored as new Gold columns.** Decided (your choice).
*Why:* matches the PROPOSAL §9 architecture, leaves the Gold schema stable, and keeps modeling choices (warm-up rows, constant columns) out of the warehouse. *Rejected:* adding about 20 columns to Gold. Features obey one rule, only information from before the predicted month, enforced by a test that rewrites every later month. *Recorded in:* `docs/feature_dictionary.md` (#28).

**DL-23 · 6 Oct 2026 · Evaluation protocol: expanding walk-forward, 24-month minimum, one month ahead, fresh model per fold.** Decided (from PROPOSAL §18.3, implemented).
*Why:* random splits would leak the future. Primary metric is recall on Down; accuracy is reported but misleading (always-Flat scores 71%); McNemar's exact test compares models. *Finding that changed the plan:* only 59 months are model-ready, so there are **35** test months (25 Flat, 6 Down, 4 Up), not the proposal's ~48, and significance tests have very little power. *Recorded in:* `docs/evaluation_protocol.md`, PROPOSAL §18.3 note (#29).

**DL-24 · 6 Oct 2026 · Baselines are random-by-class-mix, always-majority, persistence and a seasonal rule, and every score gets a bootstrap confidence interval.** Decided.
*Why:* the chance band (1,000 random runs: balanced accuracy 0.21 to 0.46) is the honest yardstick; persistence and the seasonal rule both sit inside it. Resamples where a class score is undefined are excluded, not scored zero. *Recorded in:* `docs/evaluation_protocol.md` (#30).

**DL-25 · 6 Oct 2026 · A model's settings are fixed in code before any result is seen; every other variant is exploratory and reported in full.** Decided.
*Why:* with 35 test months, choosing after looking fits the test set by accident. The primary logistic regression: standardised features, L2 with `C = 0.1`, balanced class weights, all 22 features. A test pins these. Tuning, if any, happens only inside training windows. *Recorded in:* `modeling/models.py`, `docs/model_card_logistic_regression.md` (#31, open).

**DL-26 · 6 Oct 2026 · The monthly direction classifier does not beat the baselines or chance, and is treated as a null result.** Decided (finding).
*Why:* balanced accuracy 0.37, recall on Down 1 of 6, accuracy 40% against 71% for always-Flat, and clear overfitting (about 0.86 on training months, 0.37 on unseen ones). The best of about a dozen exploratory variants (0.47) clears the chance ceiling by a hair and is consistent with luck, so none is a finding. *Not done:* tuning further to rescue it; adding random forest or gradient boosting to the monthly question (they would memorise 59 rows more easily). *Recorded in:* notebook 5, model card (#31, open).

**DL-27 · 6 Oct 2026 · The model card and notebooks report negative results plainly.** Decided. A clear negative result with the right checks behind it is a legitimate finding. *Recorded in:* `docs/model_card_logistic_regression.md`.

---

## D. How we work

**PR-01 · Branch, pull request, CI, and merge only when you say "merge it".** Decided. Small documentation fixes may be committed directly when you ask. CI is checked before declaring a PR ready; auto-merge is not enabled (and is off for the repo).

**PR-02 · The slide deck and presentation script stay out of the repo until the whole project is done.** Decided. Both are currently stale: they carry the old test count (152), the old rolling-average correlation (0.95) and a wrong symbol for the specialty effect (η, not ρ, on slide 33), and predate the modeling results.

**PR-03 · Both team members stay credited in the README and PROPOSAL; Saakshaat Saini helps with documentation.** Decided (your call). Tushar does the code and analysis.

---

## E. Direction from here (proposed, awaiting approval)

These follow from DL-26. Nothing here has been built or run.

**PD-01 · Change what the project delivers.** Proposed.
(1) A **segment opportunity model**: where Zilretta adoption is below or above what a group's profile would predict. (2) A **share-level forecast with honest intervals**, judged against "same as last month". (3) A **surprise alert**: flag a month that moved more than normal, after it happens. (4) The monthly Up/Down/Flat classifier stays a documented null result.
*Why:* the data has signal for (1) (specialty track record correlates 0.61 with segment share; specialty has η = 0.67) and for (2) (share level is highly autocorrelated), not for predicting surprises. This answers the brand manager's real questions without claiming more than the data supports.

**PD-02 · Segment model design.** Proposed. Target: each segment's Zilretta share, weighted by volume. Models in order: market-wide share, the specialty's track record alone, ridge regression, a shallow random forest, scikit-learn's gradient boosting. Hyperparameters fixed in advance; the tree models count only if they beat the track-record baseline.

**PD-03 · Split for the segment model.** Proposed. First 24 months train only; the next 36 months are a walk-forward development zone (predict all ~120 segments of month *t* from months before it); the **last 12 months (Aug 2024 to Jul 2025) are a final holdout evaluated once**. This fixes the monthly test's weakness that the same months were used to explore and to judge.

**PD-04 · Features for the segment model.** Proposed. Keep the specialty track record and age. Treat gender, season and segment size (all η of 0.08 or less) as pre-specified candidates to drop. Add candidates written down in advance: a trailing 6 or 12-month track record, specialty × age history, segment typical volume. Judged on the development zone only; a feature stays only if it beats the track-record baseline. The 22 monthly features are market-level and mostly irrelevant here.

**PD-05 · Tree models come from scikit-learn** (random forest, `HistGradientBoosting`), not XGBoost, to avoid a new dependency for no gain at this size, unless XGBoost is wanted for its own sake.

**PD-06 · Uncertainty and robustness.** Proposed. Bootstrap by whole months (time dependence) and also by specialty (the same specialties repeat). Report results with and without the Pediatrics segments (mis-coded from Oct 2024) and note that the holdout includes the 2024 volume dip.

**PD-07 · A gap is a lead, not a proven opportunity.** Proposed as a standing rule for everything shown to the brand manager. A specialty can sit below expectation for reasons the data cannot see (payer, geography, practice mix); ages are estimated from band midpoints; the 2024 dip and the Pediatrics anomaly are unresolved.

**PD-08 · Guardrails for the rest of the work.** Proposed. No further tuning on the monthly question. At most **one** pre-specified reframing of the monthly question ("Down versus not Down"), reported whatever it shows. No picking a variant after seeing test scores. Any change to a pre-specified choice gets its own log entry first.

---

## F. Open questions

| # | Question | Needed for |
|---|---|---|
| Q-01 | Approve PD-01 to PD-08, including the 12-month final holdout? | Starting the segment model |
| Q-02 | Run the one pre-specified monthly reframing ("Down versus not Down")? | PD-08 |
| Q-03 | Is XGBoost wanted (for the resume) rather than scikit-learn's boosting? | PD-05 |
| Q-04 | If the High/Low adoption label is wanted, what fixed threshold (for example above or below the market-wide share)? | PD-02 |
| Q-05 | ETS or SARIMA for the share-level forecast? | PD-01 (2) |

## Known loose ends (not decisions)

- README files the classifier under "Phase 3"; the course roadmap and deck list modeling as Phase 4.
- PROPOSAL executive summary says 5.32M product-linked visits where the verified figure is 5.31M.
- PROPOSAL lines dated "as of Phase 1" and "Week 2" are stale.
- The deck and script need refreshing before any further presentation (see PR-02).
