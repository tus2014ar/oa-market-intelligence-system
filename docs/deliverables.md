# Final deliverables

These six items are the locked, final deliverables of the project, for the stakeholders (the OA/RA Injectable Brand Manager and the course audience). Nothing else is promised. The audience is non-technical, so every item is written in plain language, with the method and evidence kept behind it.

This page records what each deliverable is, what it contains, where it comes from and what state it is in today. It does not build anything. Each item is built and checked one at a time after this page is merged.

## Summary

| # | Deliverable | Format | Built from | State today |
|---|---|---|---|---|
| 1 | Database on Datasette | One read-only SQLite file, browsed in Datasette | `data/published/warehouse.db` and `data/published/external_subset.db` | Exists locally, but is out of date: it was built before the modelling tables (`model_scores`, `backtest_batch`, `pred_batch`) existed. Needs a rebuild |
| 2 | Stakeholder demo | One self-contained HTML page plus a launcher that also starts Datasette | The stakeholder database and committed reference files | Exists locally. Needs the modelling results, the decline-alert (T3) slide and removal of the open "Questions for you" box |
| 3 | Analytics website | Streamlit site, seven tabs | `app/streamlit_app.py`, the published warehouse | Built and merged (DL-85, PR #84). Not hosted. Delivered as run-it-locally steps plus a screenshot set |
| 4 | Project zip and steps to run it on another machine | One zip and a one-page run guide | `git archive` of the tagged commit, plus the files in items 1, 2, 5 and 6 | Not started |
| 5 | Project report | Word document (.docx) | The README, `docs/`, the model cards and the stored results | Not started |
| 6 | Full project presentation | PowerPoint (.pptx) | The same sources, in the order of the project's seven stages | Not started. Three topic decks exist locally and are inputs, not the deliverable |

## 1. Database on Datasette

- **What it is.** The project's data in one file that a person can browse and query in a web page, with no coding. Datasette opens it read-only, so nothing in it can be changed.
- **Contents.** The IQVIA warehouse (15 tables), the model results and forecasts (3 tables), the 23 publishable public-data tables (CMS, CDC, price, company filings, payments), the full feature tables with their dictionary, the competitor view, and a table catalog that describes every table in one line.
- **Left out on purpose.** Provider-level Medicare rows, the copyrighted taxonomy text, county-level prevalence and the load log. These are local-only and are not in the committed inputs.
- **Ready-made queries.** Share by month, year and specialty; competitors by year; company sales against visits; state headroom; specialty triangulation; promotion against share; price ratio; the verdicts; feature groups; data quality; ingested files. To add: the latest forecasts, the model scoreboard and the decline-alert list.
- **How to open it.** `datasette data/processed/stakeholder_database.db --metadata <metadata file>`, then open http://127.0.0.1:8001.
- **Check before it is locked.** Table count and row counts match the catalog; the model tables are present; the file opens read-only; every ready-made query returns rows.

## 2. Stakeholder demo

- **What it is.** A walk-through page a stakeholder can open without help: a data explorer with tabs, including the entity-relationship diagram, the modelling plan and the project links.
- **Two parts.** `stakeholder_data_explorer.html` (a single file with its data inside it) and `run_stakeholder_demo.bat`, which starts Datasette on port 8001 and the page on port 8002 and opens the browser.
- **Still to do.** Add a modelling-results tab and the decline-alert slide; remove the "Questions for you" box, because the decisions it asked for were made (DL-79); point the launcher at the rebuilt database; make the launcher use paths relative to its own folder so it runs on any machine.
- **Check before it is locked.** Opens from a clean folder; every tab renders; the numbers on it equal the warehouse; no open question or "awaiting" wording remains.

## 3. Analytics website

- **What it is.** The Streamlit site with seven tabs: Overview, Market trend, Segments, Forecasts, Model results, Data status, Findings. It reads the published warehouse in read-only mode and shows aggregates only (months, specialties, age and gender groups), never visit-level rows.
- **What it states plainly.** The latest share (1.88% in Jul 2025), the peak (3.38% in Dec 2021) and the 44% fall since the peak; that the segment forecasts run 5.5% too high at 1 month and 10.9% at 3 months; that the national forecast is "same as last month" because no model beat it; that the decline alert is a 1-month flag only; and that payer, regional and prescription-volume data are not part of this analysis.
- **Not included.** The Claude question box (phase 3 of the serving layer) is not part of this release. The site has no free-text box and no API key.
- **How it is delivered.** Not hosted. The stakeholders get the run steps in item 4 and a screenshot set of all seven tabs, so the site can be reviewed without installing anything. A hosted link is a later, separate step and needs the owner's own account.
- **Check before it is locked.** The site's test file passes; the site starts in a fresh virtual environment from `app/requirements.txt` alone; the screenshots are taken from that run.

## 4. Project zip and steps to run it on another machine

- **What it is.** One zip that a person can unpack on a different computer and run, with a one-page guide.
- **Contents.** A clean export of the tagged commit (code, tests, docs, notebooks, the committed published warehouse and public-data subset, and the four IQVIA extracts in `data/raw/`), plus the stakeholder database, the demo page, the report and the deck.
- **About the IQVIA extracts.** They are already in the public repository, committed with the course instructor's approval under Penn State's data licence. The zip carries the same four files and nothing more. The 24 GB of raw public downloads on the developer's machine are not in the zip; the committed 6 MB public-data subset replaces them.
- **Steps the guide gives, in order.** Install Python; create a virtual environment; install the pinned packages; run the tests; start the site (`streamlit run app/streamlit_app.py`); start Datasette on the stakeholder database; optionally rebuild the warehouse with `oa-publish --public-data committed`, or run the Docker image.
- **What the guide says it cannot do.** It does not fetch new IQVIA data (none has arrived since Jul 2025, so the site shows a stale-data banner), and it does not run the Claude question box.
- **Check before it is locked.** The zip is unpacked into an empty folder on a different path, a new virtual environment is made, and the tests, the site and Datasette all start from the guide alone. The zip's checksum is recorded.

## 5. Project report (.docx)

- **Audience and length.** Non-technical stakeholders; written so a brand manager can act on it. A short summary first, then the detail.
- **Outline.** (1) Summary and the decision it supports. (2) The business question and the data. (3) What was found: the 17 findings, each with evidence and impact. (4) How the data was cleaned and checked. (5) The forecasting work: what was tested, what won, what did not, and why. (6) What to use and what not to use (the use-and-do-not-use table). (7) Limits: no Rx volume, regional or approval-status data; the size of the IQVIA decline is unverified because company sales rose while visits fell. (8) How the system runs each month, and how it is kept honest (tests, monitoring, runbook). (9) What is built and what is not (nothing is hosted). (10) Appendix: model cards, the decision log, the data dictionary.
- **Rules.** Null and mixed results are stated as they are. No figure is written that is not in the warehouse or the stored results. No claim of deployment or of a knowledge graph.
- **Check before it is locked.** Every number is traced to `modelling_results.json`, the warehouse or `findings.json`; the file passes validation and opens in Word.

## 6. Full project presentation (.pptx)

- **Audience and length.** Non-technical stakeholders; plain words, one idea per slide, a visual on every slide.
- **Storyline, in the order of the project's seven stages.** The question and why it matters; the data and its limits; cleaning and the warehouse; what the data shows (the findings); features; the models and their honest results (including the decline-alert result and the bias the segment forecast carries); hardening (tests, monthly job, monitoring); the site and the database; what to use, what not to use; limits and next steps.
- **Reuse.** The three topic decks (feature engineering, modelling plan, model evaluation metrics) are the source for their sections; the new deck joins them into one story and ends with the use-and-do-not-use slide.
- **Check before it is locked.** Passes the PPTX validator; no text overflows a slide; the figures match the report; speaker notes carry the plain-language explanation.

## How the lock works

1. This page is merged first, so the scope cannot drift.
2. Each deliverable is built and checked against its own check list above, in this order: database, demo, website screenshots, report, deck, zip. The zip is last because it holds the others.
3. Each file carries the date and the commit it was built from. A manifest lists every deliverable with its checksum.
4. Once all six pass, the commit is tagged `stakeholder-v1`. After the tag, a change means a new tag, not an edit.
5. The deck and the report are kept local and are not committed to the public repository. The zip and the manifest are the way they reach the stakeholders.

## Not in this release

- Hosting the site or any public link.
- The Claude question box.
- A knowledge graph.
- New IQVIA data, prescription volume, regional or approval-status data.
