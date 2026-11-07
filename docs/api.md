# The API (DL-116, DL-117)

A read-only HTTP API over the published warehouse. It serves the same figures as the website's pages and the AI Question Box, because every endpoint wraps the same tested lookups (`serving/qa_tools.py`), which are built from the same functions as the pages. It reads aggregates only, opens the database read-only, and fits nothing. The monthly job still fits the models and stores their forecasts; since DL-117 it also stores the fitted models as readable numbers, and the API applies those numbers when you ask (`/v1/predict/*`), so a prediction made live must equal the stored forecast, and each response says whether it does.

**Status: live on AWS Lambda since 6 Nov 2026 (DL-118); the address is not published.** It also runs locally, in Docker and, as a Lambda image, in the Lambda emulator. What was checked on the live function, and what was not: [`deployment.md`](deployment.md), "The AWS version". The AWS free plan ends on 6 May 2027 or when its credits run out, so the live API is temporary unless the account is upgraded.

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
| `/health` | Whether the service is up, the newest month of data, the data version (a short hash of the input extracts, the same on every forecast of a run) and whether a key is needed. No key required. `data_source` says whether the data came from the service's image or from the current release in the data bucket (DL-124). The deploy workflow and the daily canary compare the data version with the repository's. |
| `/v1/status` | The headline numbers, how fresh the data are, and the limits of the analysis. |
| `/v1/share/monthly` | Zilretta's share of visits by month. Optional `start_month_id`, `end_month_id` (YYYYMM). |
| `/v1/share/yearly` | The same by calendar year; the first and last years are part years. |
| `/v1/segments` | Share by `by=specialty`, `age_band` or `gender`, groups of at least 1,000 visits; `top` (1 to 25). |
| `/v1/forecasts/national` | The national share 1, 3 and 6 months ahead with its 90% range. |
| `/v1/forecasts/specialty` | Segment forecasts rolled up to specialties, with the bias recorded in testing. |
| `/v1/forecasts/alerts` | The 1-month decline-alert list: a supporting flag, never a reason to act alone. |
| `/v1/model-results` | How each forecast scored against a simple rule, and what to use each for. |
| `/v1/findings` | The 17 findings with evidence and impact; optional `keyword`. |
| `/v1/models` | The saved models: which task and horizon, logistic regression or a rule, the month and rows it was fitted on, the number of parameters, a fingerprint of the saved numbers and the bias recorded in testing. |
| `/v1/predict/segments` | The segments the live endpoints can predict for (a specialty, an age band and a gender). |
| `/v1/predict/segment` | Runs the adopted model for one segment now: `specialty`, `age_band`, `gender` and `horizon` (1, 3 or 6; default 1). Returns the forecast in percent, last month's value for comparison, the change in points, the visits behind it, a `small` flag under 100 visits, the known bias, the stored batch forecast and `matches_stored`. |
| `/v1/predict/alert` | Runs the 1-month decline alert for one segment now: the score, the cut-off, whether it is flagged, or why the segment is not scored (under 100 visits, or no share last month). Compares with the stored score. |

Example, with a key:

```bash
curl -H "X-API-Key: choose-a-key" "http://localhost:8000/v1/share/monthly?start_month_id=202501"
```

A live prediction (names are matched without regard to case):

```bash
curl -H "X-API-Key: choose-a-key" "http://localhost:8000/v1/predict/segment?specialty=RHEUMATOLOGY&age_band=65%20TO%2074&gender=FEMALE&horizon=3"
```

## Live predictions (DL-117)

**What runs.** The models adopted in DL-79: the segment logistic regression at 1 and 3 months, the rule "same as last month" at 6 months, and the 1-month decline-alert logistic regression. The national forecast is a rule and stays a stored forecast (`/v1/forecasts/national`).

**How.** The monthly job saves each fitted logistic regression in the warehouse file as the numbers that define it (`model_artifacts`: imputing medians, scaling, specialty categories, coefficients, intercept, plus a SHA-256 fingerprint) and the inputs of every segment at the last month (`pred_inputs`). Numbers rather than a pickled object, so they can be read and compared, need no scikit-learn in the API image (NumPy only) and cannot break when a library changes. They are in the same file as `pred_batch`, so a forecast, its model and its inputs always come from the same run. Before a model is saved the job applies the saved numbers to the rows it scored and stops if they differ from the fitted model by more than 1e-9.

**What it proves.** On the published warehouse every segment at every horizon, and every alert score and flag, is predicted live and equals the stored value (worst difference about 5e-7, the rounding of the stored shares); the tests run all of them.

**What it is not.** Not a what-if tool: it predicts for the segments and the month in the data, with the inputs the job saved, and a segment it has no row for is `404`, not a guess. A horizon with no saved model (2, 4, 5 and so on) is `422` and lists the horizons served. The caveats of the batch forecasts apply unchanged: the segment forecasts ran high in testing (`known_bias_pct`), the alert is a supporting flag (about half its flags were real declines), and small segments are noisy. If the warehouse has no saved models the live endpoints answer `503` and the others keep working.

## Access and limits

- **Keys.** If `API_KEYS` (comma-separated) is set, every `/v1` endpoint needs an `X-API-Key` header with one of them; a missing or wrong key is `401`. If it is not set the API is open, which `/health` reports as `"auth": "open"`. Keys are compared in constant time and never appear in a response or a log.
- **Rate.** Each client (a key, or an address when the API is open) may make `API_RATE_PER_MINUTE` requests a minute (default 60); beyond that is `429` with `Retry-After: 60`. `/health` is never limited. The count is kept in memory, so it resets when the service restarts.
- **Errors.** A value the data cannot answer is `422` with a `detail` that names the problem; a range with no months is `404`. Only `GET` is allowed.
- **CORS.** A browser page on another origin may read (`GET`); the allowed origins are `*` unless `API_CORS_ORIGINS` (comma-separated) is set.
- **Logs.** One JSON line per request (`method`, `path`, `status`, `ms`), no key and no query text.

## Where the data come from (DL-124)

The service's image carries a copy of the published warehouse. With the environment variable `OA_DATA_BUCKET` set (see [`deployment.md`](deployment.md), "The data release"), it also reads a pointer (`latest.json`) in a private S3 bucket when it starts and again at most every `OA_DATA_REFRESH_SECONDS` (default 300), and switches to the warehouse the pointer names, so a new month of data reaches the API with no image rebuild. A file is used only after its size, its SHA-256, its readability, its data version and its month all match the pointer, and a pointer may only name keys of one fixed shape. Anything that goes wrong (no pointer yet, no access, a damaged or half-written file, a release this code cannot load) keeps what the service is already serving; at start-up the image's copy is the fallback. With no bucket set, nothing changes. The status page describes the data it serves using the run record the release carries.

## Performance (DL-127)

Warm, on one laptop with a cache of answers (kept until the service switches to new data, bounded and never holding an error), the API took 193 requests a second from 50 concurrent users with a 95th percentile of 190 ms and no failures; on the live function a warm `/health` answers in about 43 ms from this machine and a cold start takes about 3.3 seconds. Method, budgets, what was and was not measured, and what the limits rest on: [`loadtest.md`](loadtest.md).

## What it is not

- Not a training service. Models are fitted only by the monthly job; the API applies the saved ones and cannot change them.
- Not a source of patient-level, provider-level or payer data: none exists in it, and a test checks that no field name suggests it.
- Not an explanation of why a share moves. Read the `limits` in `/v1/status` before quoting a number.

## Tests

`tests/test_api.py` (40 tests, run on the committed warehouse; CI job `api`, in an environment with only `api/requirements.txt`): every endpoint returns what the lookup returns and the headline figures equal the website's; ranges and groupings; the caveats travel with the data; only aggregates are served (field names checked) and lists are capped; every bad value is a clean `422` and an empty range a `404`; keys (each of two works, wrong and missing are `401`, the key is never echoed, a wrong key does not use up a rate); the rate (per client, `/health` never counted, from the environment); the interactive documentation describes every endpoint with its typed response and its `401`, `422` and `429`; one JSON log line per request with no key; CORS reads but does not write; and the service loads the warehouse itself when given no snapshot. The live endpoints add: the model list, the segment list, every segment at every horizon and every alert predicted live and equal to the stored value, the caveats on a prediction, case-insensitive names, `404` for an unknown segment, `422` for an unserved or out-of-range horizon, keys and docs applying to the new paths, and `503` when no models are saved. The saved-model code has its own tests: `tests/test_predictor.py` (the arithmetic on small models worked out by hand; no scikit-learn) and `tests/test_model_artifacts.py` (the saved numbers reproduce the batch forecasts, are checked against the fitted model, and are identical on a rerun).
