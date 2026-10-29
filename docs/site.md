# The site (DL-85, DL-86, DL-88)

A Streamlit page over the published warehouse. It is built and tested; **it is not deployed** (the hosting steps are phase 4 of the serving layer). The Claude question box (phase 3, DL-86) is built and tested with a scripted client; it has not been run against the real Claude API, which needs your key.

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
