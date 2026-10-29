# The site (DL-85)

A Streamlit page over the published warehouse. It is built and tested; **it is not deployed** (the hosting steps are phase 4 of the serving layer). The Claude question box is phase 3 and is not on the page yet.

```bash
pip install -r requirements.txt && pip install -r app/requirements.txt && pip install --no-deps -e .
streamlit run app/streamlit_app.py
```

## What it shows

| Tab | Content | Source |
|---|---|---|
| Overview | latest share, the peak, the change since it, the next-month forecast with its range; what to use each forecast for and what not to trust; four caveats | `gold_visit_share_monthly`, `pred_batch`, `data/reference/serving_models.json` |
| Market trend | the monthly share with the peak, the turn and the March to July 2024 data-capture dip marked, and a table by year | `gold_visit_share_monthly` |
| Segments | Zilretta's share by specialty, age band or gender (groups under 1,000 visits left out), and specialties over time | `gold_segment_adoption` and the dimensions |
| Forecasts | the national forecast with its 90% range; the segment forecasts rolled up by specialty with the bias recorded in testing; the 1-month decline alert list with its precision | `pred_batch`, `model_scores` |
| Model results | for each task and horizon, the simple rule, the model the registered rule chose, whether it is adopted (DL-79) and what that means in plain words; the forecast bias; the post-hoc caveat on the alert | `model_scores`, `modelling_posthoc.json` |
| Data status | the last publish run: stages, freshness, drift, versions, input extracts | `run_log.jsonl` |
| Findings | the 17 findings with evidence and business impact | `data/reference/findings.json` (equal to the README table, tested) |

A banner under the title says when the latest month of data is stale, which it is while there is one static extract (the data end in July 2025).

## How it is built
- `src/oa_market_intelligence/serving/data.py` reads the warehouse **read-only** and returns aggregates (months, specialties, specialty x age band x gender segments); no function takes SQL text and none returns patient-level rows (tested).
- `serving/views.py` holds every frame and every sentence the page draws, so the wording of each headline is tested; numbers in sentences (the bias, the coverage of the range) are computed from the tables, not typed.
- `app/streamlit_app.py` only lays it out. Nothing is fitted while a page loads.
- The page reads the committed `data/published/warehouse.db`; when the monthly workflow commits a new one, the site shows it.

## Tests and the CI job
`tests/test_serving_views.py` (by hand on small frames), `tests/test_serving_data.py` (the real warehouse: read-only, aggregates only, findings equal the README) and `tests/test_site_app.py` (the page run headlessly with Streamlit's test runner: no exception, every tab, the headline numbers and the caveats). The app test needs Streamlit, so it runs in the CI job `site`, which installs `app/requirements.txt`.
