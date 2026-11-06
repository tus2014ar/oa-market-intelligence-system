# The API (DL-116)

A read-only HTTP API over the published warehouse. It serves the same figures as the website's pages and the AI Question Box, because every endpoint wraps the same tested lookups (`serving/qa_tools.py`), which are built from the same functions as the pages. It reads aggregates only, opens the database read-only, and fits nothing: the monthly models stay batch jobs and the API serves their stored output.

**Status: built and tested; not deployed.** It runs locally and in Docker. A cloud deployment (AWS) is planned and not done.

## Run it

```bash
pip install -r api/requirements.txt
PYTHONPATH=src uvicorn oa_market_intelligence.api.main:create_app --factory --port 8000
```

or in Docker (see [`docker.md`](docker.md)):

```bash
docker build -f Dockerfile.api -t oa-api .
docker run --rm -p 8000:8000 -e API_KEYS=choose-a-key oa-api
```

Then open http://localhost:8000/docs for the interactive documentation (every endpoint, its parameters, its response fields and its errors), or http://localhost:8000/openapi.json for the machine-readable description.

## Endpoints (all `GET`, version 1)

| Path | Returns |
|---|---|
| `/health` | Whether the service is up, the newest month of data, and whether a key is needed. No key required. |
| `/v1/status` | The headline numbers, how fresh the data are, and the limits of the analysis. |
| `/v1/share/monthly` | Zilretta's share of visits by month. Optional `start_month_id`, `end_month_id` (YYYYMM). |
| `/v1/share/yearly` | The same by calendar year; the first and last years are part years. |
| `/v1/segments` | Share by `by=specialty`, `age_band` or `gender`, groups of at least 1,000 visits; `top` (1 to 25). |
| `/v1/forecasts/national` | The national share 1, 3 and 6 months ahead with its 90% range. |
| `/v1/forecasts/specialty` | Segment forecasts rolled up to specialties, with the bias recorded in testing. |
| `/v1/forecasts/alerts` | The 1-month decline-alert list: a supporting flag, never a reason to act alone. |
| `/v1/model-results` | How each forecast scored against a simple rule, and what to use each for. |
| `/v1/findings` | The 17 findings with evidence and impact; optional `keyword`. |

Example, with a key:

```bash
curl -H "X-API-Key: choose-a-key" "http://localhost:8000/v1/share/monthly?start_month_id=202501"
```

## Access and limits

- **Keys.** If `API_KEYS` (comma-separated) is set, every `/v1` endpoint needs an `X-API-Key` header with one of them; a missing or wrong key is `401`. If it is not set the API is open, which `/health` reports as `"auth": "open"`. Keys are compared in constant time and never appear in a response or a log.
- **Rate.** Each client (a key, or an address when the API is open) may make `API_RATE_PER_MINUTE` requests a minute (default 60); beyond that is `429` with `Retry-After: 60`. `/health` is never limited. The count is kept in memory, so it resets when the service restarts.
- **Errors.** A value the data cannot answer is `422` with a `detail` that names the problem; a range with no months is `404`. Only `GET` is allowed.
- **CORS.** A browser page on another origin may read (`GET`); the allowed origins are `*` unless `API_CORS_ORIGINS` (comma-separated) is set.
- **Logs.** One JSON line per request (`method`, `path`, `status`, `ms`), no key and no query text.

## What it is not

- Not a model server. The forecasts are the monthly batch job's stored output (`pred_batch`); there is no score-on-demand endpoint.
- Not a source of patient-level, provider-level or payer data: none exists in it, and a test checks that no field name suggests it.
- Not an explanation of why a share moves. Read the `limits` in `/v1/status` before quoting a number.

## Tests

`tests/test_api.py` (26 tests, run on the committed warehouse; CI job `api`, in an environment with only `api/requirements.txt`): every endpoint returns what the lookup returns and the headline figures equal the website's; ranges and groupings; the caveats travel with the data; only aggregates are served (field names checked) and lists are capped; every bad value is a clean `422` and an empty range a `404`; keys (each of two works, wrong and missing are `401`, the key is never echoed, a wrong key does not use up a rate); the rate (per client, `/health` never counted, from the environment); the interactive documentation describes every endpoint with its typed response and its `401`, `422` and `429`; one JSON log line per request with no key; CORS reads but does not write; and the service loads the warehouse itself when given no snapshot.
