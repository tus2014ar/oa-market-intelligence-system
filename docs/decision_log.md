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
| G. Final deliverable and plan (Oct 2026) | DL-28 to DL-48, PD-09 to PD-12, Q-06 to Q-08 |

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

These follow from DL-26. Nothing here has been built or run. **Part G supersedes the emphasis of PD-01:** the course deliverable is a deployed data and MLOps pipeline with a public analytics website, so the segment model and the share forecast become the trained models inside that pipeline (DL-32), not stand-alone analyses.

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
| Q-06 | Single shared access code, or open access, for the public site? | PD-11, DL-30 |
| Q-07 | Hosting platform, and the Claude API budget and monthly spend cap? | DL-33 (by 14 Oct) |
| Q-08 | Is the segment model, the share forecast, or both the trained models shown on the site? (Plan assumes both, with the monthly classifier as an informational challenger.) | DL-32 |

## G. Final deliverable and plan (Oct 2026)

What the course requires and the answers given on 6 Oct 2026. Plan: [`final_deliverable_plan.md`](final_deliverable_plan.md).

**DL-28 · 6 Oct 2026 · The final deliverable is due at the end of October 2026.** Decided. About three and a half weeks. The plan is time-boxed, with a cut list (see the plan).

**DL-29 · 6 Oct 2026 · The final deliverable is a deployed data pipeline and MLOps pipeline, with a public analytics website that has a Claude API question box on the modeling and the analytics.** Decided (course requirement).
*Why this changes priorities:* the work is graded as a working system, not a model score. The riskiest unknowns are now hosting, secrets, storage and the Claude API, not modeling, so the plan builds a thin deployed slice first and then widens it. *Recorded in:* `docs/final_deliverable_plan.md`.

**DL-30 · 6 Oct 2026 · Public display of aggregate IQVIA-derived tables on the website is approved.** Decided (your confirmation). Aggregate tables only, never raw rows, as for the Claude API (DL-07). *Not decided:* whether access is behind a single access code; the plan assumes one shared code for cost control (see Q-06).

**DL-31 · 6 Oct 2026 · The system must be robust and adaptable to new data, but a new-data arrival is not demonstrated live.** Decided.
*Why:* a live demo of arrival adds risk for little value. Robustness is proven by automated tests instead: a simulated new month (run on data up to June, then July), a malformed file, and an outside-service failure. A failed run keeps the last good data live and shows why on the site.

**DL-32 · 6 Oct 2026 · The pipeline must train and serve real trained models (this is an ML pipeline project).** Decided.
*Why and how:* each monthly run trains models, evaluates them out of time against the baselines with confidence intervals (the evaluation protocol), records them, and a pre-specified rule decides, per task, what serves. Where no trained model beats its baseline, the site says so plainly and serves the better of the two. This keeps the null result on monthly direction (DL-26) honest instead of hiding it. Trained models planned: the segment model (random forest and gradient boosting against the specialty-track-record baseline), the share-level forecast (ETS or SARIMA against "same as last month"), and the monthly direction classifier kept as an informational challenger. *Rejected:* promoting a model by default, or a model that cannot be checked out of time.

**DL-33 · 6 Oct 2026 · Hosting platform and budgets are deferred to a decision point.** Decided. To be settled together before the deployed slice starts (target: by 14 Oct 2026). The Claude API budget, the per-user limits and the monthly spend cap are part of that decision.

**PD-09 · Build a deployed thin slice first, then widen.** Proposed. A deployed page with one table, a Claude question box scoped to that table, and a workflow that publishes a new database file and makes the site pick it up. Then harden the rerun, widen the site, add Q&A guardrails, and add the MLOps layer.

**PD-10 · A safe rerun.** Proposed. Build the new database beside the old one and swap it in only if every check passes. On failure, keep the last good data live, show a banner with the reason, and write a run log. Retry outside-service calls.

**PD-11 · Claude Q&A with scoped tools and guardrails.** Proposed. A fixed set of read-only functions over the summary tables (never raw rows and never open SQL), an access code, per-user rate limits, a monthly spend cap, and a test set of questions with expected answers. Plain Claude tool use first; MCP packaging only if time allows.

**PD-12 · Scope cuts, in order, if time runs short.** Proposed. Accounts and sign-up (use one access code), then RAG over methodology documents, then the knowledge graph, then SHAP, then drift tooling beyond a simple custom check, then XGBoost. Not cut: a deployed site, the Claude Q&A box, the safe rerun, and at least one trained model served with its honest evaluation.

**DL-34 · 6 Oct 2026 · Version 1 of the public site goes live by 15 Oct 2026; it is then improved day by day until the end-of-October deadline.** Decided (your direction). DL-28 still sets the hard deadline.
*Why:* the project is to go on the resume and into job applications now, and stakeholder feedback should shape the improvements. *Consequence:* version 1 is deliberately small (status, trend, segment table, honest model panel, Claude question box, safe rerun). Everything else in the plan is added after it is live.

**DL-35 · 6 Oct 2026 · The site must be public on the internet, on free hosting first, and reachable at the Cloudflare domain you own.** Decided (your direction). Refines DL-33.
*How:* a free host first (Streamlit Community Cloud is the first choice; the app is plain Streamlit and can move to Hugging Face Spaces or Render), and a redirect or CNAME from the Cloudflare domain. The only unavoidable cost is Claude API usage, controlled by a spend cap you set in the Anthropic console (DL-37). *Free-tier trade-off:* the app sleeps when idle and takes some seconds to wake. *Still open:* the exact host, confirmed when the deployment is created (Q-07).

**DL-36 · 6 Oct 2026 · The site reads one self-contained database file, `data/published/warehouse.db`, which the monthly workflow rebuilds and commits.** Decided; built on branch `feat/serving-layer-and-app`.
*Why:* the model panel is stored inside the same file as the tables, so the site can never show new tables with an old model verdict, and the page loads without recomputing anything (the evaluation takes about 30 seconds). The publish step (`oa_market_intelligence.publish`) builds in a staging file, checks it (not empty, no fewer months, not moving backwards), runs the model stage, and swaps the file in atomically. A failed run leaves the last good file live and still writes a run log. Tests cover a crash in the pipeline, a crash in the model stage, a shrinking rebuild, an empty rebuild and a new month. This implements PD-10.
*Rejected:* rebuilding at app start-up (slow first visit, depends on an outside service). *Trade-off:* each monthly commit adds about 7 MB to the repository; revisit with release assets or object storage if that grows.

**DL-37 · 6 Oct 2026 · Version 1 defaults for the Claude question box.** Decided as defaults; change them through Q-06 and Q-07.
*Defaults:* answers come only from four read-only tools that return aggregate summaries (no SQL, no rows); the default model is Claude Haiku 4.5 (cheap and fast; configurable by a host secret); at most 8 questions per visitor per hour, 200 per hour for the whole site, and 300,000 tokens per day; an optional shared access code (off unless a secret is set). The API key lives only in the host's secrets, never in the repository. *Your action:* create the API key with a monthly spend limit in the Anthropic console; the in-app limits are a second line of defence, not a replacement.

**DL-38 · 7 Oct 2026 · Phase 4 is redone inference first, prediction second, and mapped to the four business questions in the course project document.** Decided (your direction: the first pass was not enough and the results must be reliable for stakeholders).
*What:* Q1 trend and change points plus a decomposition of the share decline into specialty-mix and within-specialty adoption effects; Q3 a binomial model of specialty, age and gender effects with intervals and a stability check; predictive Task A (segment High/Low) and Task B (share forecast with intervals) tested against the strongest simple baselines; Q4 monitoring; Q2 closed with a statement of what the test could detect. *Why:* the data is six years of aggregated counts for one drug, which supports careful inference with honest uncertainty far better than precise prediction; the first plan also left Q4 uncovered and treated Q1 only descriptively. Full plan: [`phase4_modeling_plan.md`](phase4_modeling_plan.md). *Rejected:* more variants of the direction classifier (more tries, not more evidence); making ML models the headline result.

**DL-39 · 7 Oct 2026 · Q3 uses a binomial model with month fixed effects, and Task A uses a three-label scheme (High, Low, undetermined) from Wilson intervals, adopted only if it passes a pre-modeling audit.** Decided; the label scheme is conditional on the audit gate in Step 6, with a stated fallback (two labels, segments with at least 100 visits).
*Why:* "above the market share" flips by chance for small segments, so part of persistence's 0.77 balanced accuracy is stickiness of noise and part of any model's error is unpredictable chance. Using the counts directly (binomial) and labelling only segments that are clearly above or below removes that noise. *Rejected:* the simple two-label rule as the primary label, and the top third of segments each month (forces a fixed 33% High). Changes the earlier default for Q-04.

**DL-40 · 7 Oct 2026 · Models: a small core, tested carefully.** Decided.
*Core:* logistic regression and gradient boosting (scikit-learn) for Task A; damped ETS and "same as last month" for Task B. *If time:* random forest, seasonal ETS, ridge. *Last, first cut:* XGBoost. Inference uses a decomposition, change points and a binomial model. *Why:* reliability does not come from the number of models; a few models with pre-specified grids, month-level intervals and strong baselines are stronger than many tuned loosely. Resolves Q-05 in favour of ETS. *Rejected:* deep learning (too little data), a model per specialty (17 specialties), SARIMA (72 months).

**DL-41 · 7 Oct 2026 · The test protocol and the serving rule for Phase 4 are fixed before the analyses run.** Decided.
*What:* features use month t−1 or earlier only; tuning happens inside the training window with grids fixed in the plan; test months never influence a choice; confidence intervals resample whole months (rows within a month are not independent); a trained model is promoted only if the lower end (1.67th percentile, Bonferroni for three trained models) of its paired improvement over the best baseline is above zero, and for Task A its balanced accuracy is above 0.5. Extra variants are reported as exploratory. Every headline conclusion is re-run without the known data problems (PEDIATRICS from Oct 2024, the COVID months, the Mar to Jul 2024 dip) and labelled robust or fragile. *Why:* this is what makes a positive result believable and a negative one honest. It extends DL-32 and the evaluation protocol.

**DL-42 · 7 Oct 2026 · The v1 site is deployed in parallel with the Phase 4 redo; it does not wait for it.** Decided as the default.
*Why:* the site already shows the honest current panel, and the new results appear on it at the next publish with no site change beyond the Model results tab. Refines the sequencing in PD-09. The schedule for Phase 4 itself is left open until you set your dates.

**DL-43 · 7 Oct 2026 · Q4 monitoring has two parts, Q2 is closed with a power statement, and Q1's FDA question is stated as untestable as worded.** Decided.
*Q4:* (a) the direction model's month-by-month match rate with a review threshold from a block bootstrap, as the question is worded (wide, with 35 test months); (b) a forecast-interval alarm, flagging when at least 3 of the last 6 months fall outside the 90% interval (about 1.6% false alarms if the intervals are calibrated), which is the monitor to rely on. Backtest predictions fill the empty `predicted_direction` / `actual_direction` Gold columns, labelled as backtest. *Q2:* a simulation states the smallest improvement over persistence that 35 test months could have detected, so "no signal" is not confused with "too little data". *Q1:* Zilretta's approval (Oct 2017) predates the data (Aug 2019), so its entry cannot be observed. RA stays descriptive (about 1,283 visits). Task B is promoted from lowest priority because it feeds Q4.

**DL-44 · 7 Oct 2026 · Change-point selection uses bootstrap-calibrated sequential tests, not BIC.** Decided; replaces the BIC rule written in the Phase 4 plan (Step 2) before it was ever run on real data.
*Why:* writing the tests first showed BIC is far too liberal when the break month is unknown. On a plain trend with noise and no break it still chose one or more breaks in about 73% of resamples, and it added a spurious second break to a series with one true shift. Searching over every possible break month inflates the apparent improvement. *What replaced it:* each additional break is kept only if its improvement in fit exceeds what noise produces in 95% of 1,000 block-bootstrap simulations of the search. *Evidence it works:* in tests the false-positive rate on no-break series is within the 5% target for independent and for dependent noise, a second break is wrongly added in about 3% of series with one true break, and the one true break is located exactly. *Rejected:* a heavier BIC penalty (an arbitrary constant, not a calibrated error rate).

**DL-45 · 7 Oct 2026 · FDA approval dates were looked up for every branded product in the share formula; none was approved inside the data window.** Decided (your direction: run the lookup before Step 3).
*What:* 70 branded and branded-generic products (the three share-formula categories plus the opioid-tagged ones, to check Anjeso) were queried against the public openFDA Drugs@FDA API with the existing client (earliest original-approval date per exact brand name); no errors. 33 had a date. In the share formula the dates run from 1951 (Hydrocortone) to Oct 2017 (Zilretta); the newest NSAID brands are Zipsor (2009) and Zorvolex (2013). The only approval inside Aug 2019 to Jul 2025 is Anjeso (20 Feb 2020), which is tagged `opioid_other`, outside the formula, and has 2 visits in the whole dataset. *Coverage:* products with a date are 60.8% of the competitive set's visits; branded products with no date found are 0.08% (compounded kits and truncated names); the other 39.1% are unbranded generics, which cannot be looked up by brand name. *Meaning for Q1:* no branded entrant appears in the window, so an FDA brand-entry event does not explain the 2022 turn in Zilretta's share. This does not rule out new generic approvals, label changes or formulary shifts, which the original-approval lookup cannot see. *Snapshot:* `data/reference/openfda_competitive_set_approvals_2026-10-07.csv`. The warehouse is unchanged.

**DL-46 · 7 Oct 2026 · Step 4 details: 0.5% specialty grouping, quasi-F test, and a segment bootstrap in place of cluster-robust standard errors.** Decided.
*Grouping:* a 1% cut would have merged Pain Medicine (0.77% of visits) into the rare group even though Q3 names it, so specialties under 0.5% of category visits are grouped (11 stay separate). *Test:* the full model's Pearson dispersion is about 1.5, so a plain likelihood-ratio test would overstate significance; the specialty term is tested with a quasi-likelihood F test scaled by that dispersion. *Intervals:* instead of cluster-robust standard errors as the cross-check, a second bootstrap that resamples whole segments is run on the same quantity (the adjusted share), and the wider interval is reported per specialty. *Solver:* a sparse iteratively-reweighted least-squares fit (about 100 times faster than statsmodels for the bootstraps), verified against statsmodels in a test.

**DL-47 · 7 Oct 2026 · Part 1 of Phase 4 (Q1 and Q3 inference) passed its sensitivity checks: all five headline conclusions are robust.** Decided (result recorded).
*Rules* were fixed in the plan before the run and applied to a baseline and to three exclusions (PEDIATRICS, the COVID months, the Mar to Jul 2024 dip), at 300 bootstrap draws (100 for stability). *Verdicts:* **T1** a trend break in early 2022 holds (Jan to Mar 2022 in every run, intervals from May or June 2021 to June or August 2022); **D1** the decline is within-specialty, with the rate effect 4 to 18 times the mix effect; **A1** specialty carries 50 to 52% of explainable variation; **A2** all eight clearly-above or clearly-below specialties keep their side; **A3** split-half rank correlation 0.78 at baseline, 0.85 to 0.88 without the COVID or dip months. *Noted:* the PEDIATRICS anomaly inflates the grouped "other specialties" share (1.70% to 1.08% once removed) and leaves every named specialty unchanged; the net year 1 to 6 decline shrinks from 0.317 to 0.221 percentage points without the COVID months, while the larger peak-to-latest decline (1.114 points) is unaffected. *Limit:* the exclusions are small perturbations, so "robust" means not driven by these known problems, not robust to every perturbation. Bootstrap fits that did not converge (levels with no events in a resample) are excluded and were not counted in this run.

**DL-48 · 7 Oct 2026 · The Task A label audit failed for the interval scheme, so the pre-specified two-label fallback is used, with a last-month volume rule.** Decided (applying the plan's own gate).
*Audit (8,450 prediction rows, 70 months, 150 segments):* the 95% interval scheme leaves 2,355 scored rows (28%), below the 3,000 gate; the High share (62%) and Low share (38%) pass. A 90% interval would scrape through (3,009), and is **not** adopted, because the gate and fallback were fixed before the data was seen. The scored rows are also almost trivially persistent (93% accuracy, 164 label changes in 70 months), because only clearly-above or clearly-below segments are labelled. *Fallback:* two labels, segments with at least 100 category visits last month: 5,436 rows, 47% High, persistence 0.82 balanced accuracy, 979 label changes (18%), median 14 a month. *Clarification:* the plan said "segments with at least 100 visits" without a month; last month's volume is used because it is known at prediction time (using the outcome month's volume gives nearly the same rows, 5,245, and persistence 0.82). *Checks:* feature leakage tests (including a deliberately injected leak that must be caught), a rewrite-the-future check on the real table at three cut months (maximum difference 0), and five rows recomputed independently from the raw counts. *Consequence:* the bar to beat is persistence at 0.82, with about 979 label changes to learn from, so power to show a gain is limited; see the Step 6 report.

## Known loose ends (not decisions)

- README files the classifier under "Phase 3"; the course roadmap and deck list modeling as Phase 4.
- PROPOSAL executive summary says 5.32M product-linked visits where the verified figure is 5.31M.
- PROPOSAL lines dated "as of Phase 1" and "Week 2" are stale.
- The deck and script need refreshing before any further presentation (see PR-02).
