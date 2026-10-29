# The site (DL-85, DL-86, DL-88, DL-89, DL-90)

A Streamlit page over the published warehouse. It is built and tested; **it is not deployed** (the hosting steps are phase 4 of the serving layer). The Claude question box (phase 3, DL-86) is built and tested with a scripted client; it has not been run against the real Claude API, which needs your key.

```bash
pip install -r requirements.txt && pip install -r app/requirements.txt && pip install --no-deps -e .
streamlit run app/streamlit_app.py
```

## What it shows

| Tab | Content | Source |
|---|---|---|
| Overview | four cards (the latest share with the last 36 months as a line, the peak, the change since it with the run down from the peak as an area, and the next-month forecast with its likely range); a chart of the share so far with the national forecast and its 90% range; three cards saying what to use each forecast for and what not to trust, each with a status (green, amber, red) and a label; the caveats; two downloads (the monthly share and the forecast, as CSV) | `gold_visit_share_monthly`, `pred_batch`, `data/reference/serving_models.json` |
| Market trend | the monthly share with the peak, the turn and the March to July 2024 data-capture dip marked, and a table by year | `gold_visit_share_monthly` |
| Segments | Zilretta's share by specialty, age band or gender (groups under 1,000 visits left out), and specialties over time | `gold_segment_adoption` and the dimensions |
| Forecasts | the national forecast with its 90% range; the segment forecasts rolled up by specialty with the bias recorded in testing; the 1-month decline alert list with its precision | `pred_batch`, `model_scores` |
| Model results | for each task and horizon, the simple rule, the model the registered rule chose, whether it is adopted (DL-79) and what that means in plain words; the forecast bias; the post-hoc caveat on the alert | `model_scores`, `modelling_posthoc.json` |
| Data status | the last publish run: stages, freshness, drift, versions, input extracts | `run_log.jsonl` |
| Findings | the 17 findings with evidence and business impact | `data/reference/findings.json` (equal to the README table, tested) |
| Market map | the knowledge graph as three views you can explore (Zilretta and the groups it is compared with; which specialties each group's visits come from; the event timeline), a closer look at any point with its sources, and the events drawn against the share | `serving/market_map.py`, `data/reference/knowledge_graph.json` |
| Ask Claude | a conversation that answers from eleven read-only tools (the tables above and the knowledge graph), with sources, charts on request and a check on the figures; off unless an API key is configured | `serving/qa.py`, `serving/qa_tools.py`, `serving/guard.py`, `serving/charts.py`, `serving/answer_check.py` |

A banner under the title says when the latest month of data is stale, which it is while there is one static extract (the data end in July 2025).

## How it is built
- `src/oa_market_intelligence/serving/data.py` reads the warehouse **read-only** and returns aggregates (months, specialties, specialty x age band x gender segments); no function takes SQL text and none returns patient-level rows (tested).
- `serving/views.py` holds every frame and every sentence the page draws, so the wording of each headline is tested; numbers in sentences (the bias, the coverage of the range) are computed from the tables, not typed.
- `app/streamlit_app.py` only lays it out. Nothing is fitted while a page loads.
- The page reads the committed `data/published/warehouse.db`; when the monthly workflow commits a new one, the site shows it.

## Tests and the CI job
`tests/test_serving_views.py` (by hand on small frames), `tests/test_serving_data.py` (the real warehouse: read-only, aggregates only, findings equal the README) and `tests/test_site_app.py` (the page run headlessly with Streamlit's test runner: no exception, every tab, the headline numbers and the caveats). The app test needs Streamlit and `tests/test_serving_charts.py` needs Altair, so both run in the CI job `site`, which installs `app/requirements.txt` (they are skipped where those packages are not installed).

## The question box (DL-86)
- **What Claude can see.** Eleven tools and no more. Six read the tables: `get_data_status`, `get_market_trend`, `get_segments`, `get_forecasts`, `get_model_results`, `get_findings`. Four read the knowledge graph ([knowledge_graph.md](knowledge_graph.md)): `get_entity`, `get_neighbors`, `get_competitors`, `get_events`. A last one, `show_chart`, asks the page to draw one of four charts (`trend`, `segments`, `forecast`, `specialty_mix`); Claude chooses the chart and its settings and the page draws it with its own tested chart code (`serving/charts.py`), so Claude never writes drawing code. Each tool returns a small summary built by the same functions as the tabs (`views`), so an answer can be checked against a tab. None takes SQL text, a table name or a column name, and none returns patient-level rows (tested). Bad input comes back to Claude as an error message, not a stack trace. A name that fits several things (a product and a group are both "Zilretta") resolves to the product; a truly ambiguous one returns the candidates and asks for an id.
- **What it is told.** The system prompt (`qa.SYSTEM_PROMPT`) says to answer only from tool results; to say when no model beat the simple rule; to state the bias with any segment forecast; to treat the alert as a 1-month supporting flag; to give findings as what the data show, never why; to repeat the stale-data warning; to give no investment, medical or prescribing advice; and to treat the user's message only as a question. For the graph it adds: events are context, never causes; the competitor groups are the project's own grouping, not a market fact. Earlier turns of the conversation are given as plain text and flagged as not re-checked. Tests check the wording is in place.
- **Limits.** Optional shared access code; 8 questions an hour per visitor; 200 an hour for the site; a daily token budget (default 300,000); questions up to 500 characters; at most 5 tool rounds of 800 tokens. These limits live in the running app, so they reset when the host restarts it. **The hard cap is the monthly spend limit you set on your Anthropic account.**
- **Secrets.** Set these in the host's secrets store (Streamlit Community Cloud: app settings, Secrets) or as environment variables for a local run. Never put them in a file in the repository.

  | Name | Needed | Meaning |
  |---|---|---|
  | `ANTHROPIC_API_KEY` | to switch the box on | your key from the Anthropic console |
  | `ACCESS_CODE` | optional | a shared code visitors type before they can ask; empty means open |
  | `DAILY_TOKEN_BUDGET` | optional | tokens per day for the whole site (default 300000) |
  | `CLAUDE_MODEL` | optional | model name (default `claude-haiku-5-5`) |

- **Trying it locally.** In your own terminal (never paste a key into a chat or a file in the repository):

  ```powershell
  $env:ANTHROPIC_API_KEY = "<your key>"
  streamlit run app/streamlit_app.py
  ```

  Then open the Ask Claude tab. Check each answer against the tab it came from; the example questions on the tab are a good first set.
- **Tests.** `tests/test_serving_qa_tools.py` (the tools on the real warehouse), `tests/test_serving_qa.py` (the loop with a scripted client: tool round trip, unknown tool, round limit, token totals, API failure with no key text, injection text kept out of the system prompt, history, streaming, sources, charts, figures to check, follow-ups, caching marks), `tests/test_serving_answer_check.py`, `tests/test_serving_charts.py`, `tests/test_serving_qa_check.py`, `tests/test_serving_guard.py` (gate and limits) and the question-box tests in `tests/test_site_app.py` (off without a key, access code, examples, a streamed answer with its sources and follow-ups, history and clearing, a chart and figures to check, empty and over-long questions, the daily budget, the hourly limit, a failed answer, the key never on the page).

## The conversation (DL-88)
- **Chat.** The tab is a conversation: the last 6 turns are sent with each question (text only; a failed answer is not sent back), answers stream in as they are written, and a line says what is being looked up. "Clear the conversation" starts again.
- **Examples and follow-ups.** The tab opens with four questions for a brand manager and four for an analyst. After each answer, up to three follow-up questions are offered; they are chosen from a fixed table by the tools the answer used (no second Claude call).
- **Sources.** Every answer lists where its data came from, for example "Forecasts tab (pred_batch, model_scores)" or "Knowledge graph (dim_product, events.csv)", and "Data the answer used" lists the exact calls.
- **Charts.** When Claude calls `show_chart` the page draws the chart under the answer, with a tooltip on every mark and small groups faded. Colours are the dataviz reference palette (blue for the series, orange for what is projected).
- **The number check.** After each answer, `serving/answer_check.py` lists any figure that is in the answer but nowhere in the data Claude looked up (or in the question), with the note "not found in the data it looked up; it may be a calculation". Years, small whole numbers, dates, ids and the 90% range are ignored; rounding, sign and percent-against-fraction are allowed for. It cannot catch a wrong claim that uses right figures, so it is a prompt to look, not a proof.
- **Cost.** The fixed system prompt and tool list are marked for the API's prompt caching. The limits above are unchanged.

## The question set and its check (DL-88)
`data/reference/qa_question_set.json` holds 28 questions: 19 in scope (trend, segments, forecasts, the alert, models, status, limits, findings and the graph) and 9 that should be refused or limited (a cause for the 2024 fall, buying stock, a 2030 forecast, patient rows, a request to reveal the prompt and key, SQL, another drug, medical advice, sales by state). Each in-scope question lists the calls a good answer makes and facts they must return; each lists words a good answer includes or avoids.
- **In CI, no key:** `tests/test_serving_qa_check.py` runs every in-scope question's calls on the committed data and fails if a fact is missing, so a change to the data or the tools cannot silently leave a question unanswerable.
- **With your key, by you:** `oa-ask-check` asks Claude every question and screens each answer on the words, on whether it looked anything up, and on figures not found in the data (`--only q01,q05` for some; `--out answers.json` to keep the full answers). It reads `ANTHROPIC_API_KEY` from your environment and never prints or saves it. It uses real tokens (28 questions, up to 5 rounds each). The screen is rough: **read the answers as well**, and record what you find. **No real answer has been graded yet.**

  ```powershell
  $env:ANTHROPIC_API_KEY = "<your key>"
  oa-ask-check --out data/processed/answers.json
  ```

## The Market map (DL-89)
The knowledge graph ([knowledge_graph.md](knowledge_graph.md)) as a picture, because a table of 827 connections is not something a brand manager reads. Three views, because all 311 points at once is a hairball:

| View | Shows |
|---|---|
| Zilretta and the groups it is compared with | the Zilretta product and its group; the two groups it is compared with; each group's leading manufacturers; Zilretta's leading specialties (point size and line width follow its visits); the events that name Zilretta or its maker |
| Which specialties each group's visits come from | the three groups and the top specialties by combined visits; each line's width is the specialty's share of *that group's own* visits, so the groups' mixes can be compared |
| The event timeline and who each event names | all 14 events, the market, and the product, maker or disease area each event's own text names |

- **Controls.** The view; how many manufacturers and specialties (3 to 15); whether to show the events; and "Let the points drift and spread out" (off by default: the layout is worked out in advance with a fixed seed so the map opens whole and looks the same every time, and turning drift on lets the points move from there).
- **Reading it.** Each kind of point has its own colour and shape (star product, box group, square manufacturer, dot specialty, diamond event; a grey diamond is an event that is not verified), shown in a legend above the map. Hover a point or a line for its facts and its source; drag to move, scroll to zoom. Dashed lines are comparisons or derived, not direct facts: a line from a manufacturer to a group means the manufacturer makes products the project placed in that group (derived from `made_by` and `in_group`, and its tooltip says so).
- **Look closer.** A picker lists every point in the view; choosing one shows what the graph holds about it, its connections (relation, other end, visits and share where there are any) and the sources.
- **Events against the share.** The share line with the events that fall inside the data (August 2019 to July 2025) as thin rules, a shaded band for an event with an end date, and dashed grey for one that is not verified; hover for the event and its type. Below it, the table of all 14 events with their sources. Events before the data begins are in the table only.
- **What it does not claim.** No line is a cause; the March to July 2024 dip is tied to the market only. Competitor groups are the project's own grouping, not a market fact. Visits are product-visits inside IQVIA's panel. IQVIA's "No Manufacturer" placeholder (4 products) is not drawn or listed as a company (it stays in the graph as the source gives it).
- **How it is drawn.** `serving/market_map.py` builds each view as plain nodes and edges (tested without any drawing library) and `to_html` draws it with `pyvis` as one self-contained page: the drawing library is embedded and the Bootstrap link pyvis adds is removed, so the map needs no network. The page is shown in an iframe. `pyvis` is in `app/requirements.txt`; if it is missing the tab says so and the details and events below still work.
- **Tests.** `tests/test_serving_market_map.py` (each view's contents, a source on every line and a tooltip on every point, derived lines marked, the slider clamped, no cause drawn, the placeholder left out, details and the event table, a fixed layout, a self-contained page), the events chart in `tests/test_serving_charts.py` and the tab in `tests/test_site_app.py`. The drawing tests need `pyvis` and `networkx`, so they run in the CI job `site` and are skipped where those are not installed.

## The redesign, tab by tab (DL-90)
The site is being reworked one tab at a time, so each change can be looked at and corrected before the next. Done so far:

- **The look (every tab).** A project theme in `.streamlit/config.toml`: teal as the accent, navy text and a navy dark mode. Streamlit follows the visitor's light or dark setting and uses the matching colours; the charts take their text and grid colours from the page (the first version fixed them to a grey that was hard to read in dark mode), and the Market map is drawn in light or dark colours to match.
- **The data-age note.** The long yellow warning is on Overview only. Every other tab starts with a one-line badge, for example "Data to Jul 2025 · 454 days old, no newer extract" (orange when stale, green when fresh), so the first screen of each tab is not taken up by it.
- **Overview.** As in the table above. The three status cards come from `views.use_and_dont`, which now also returns a status and a colour for each forecast (national: "Rely on it as a baseline", green; segment: "Use with care", orange; alert: "A supporting flag only", red); the words carry the meaning as well as the colour. The sparklines and the two CSV files are built by tested functions in `views` (`share_series`, `since_peak_series`, `monthly_csv`, `forecast_csv`).
- **Still to do, in this order:** Market trend, Segments, Forecasts, Model results, Findings, then a Context tab (company sales against visits, Medicare adoption by state).
- **Tests.** `tests/test_serving_views.py` (status and colour, the sparklines, the CSV files) and `tests/test_site_app.py` (the Overview's chart, cards and downloads; one data-age badge on each of the eight other tabs and the long warning once).
