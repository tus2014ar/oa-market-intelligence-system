# Load test and limits (DL-127)

How much traffic the API takes, what it does when it has too much, and what the limits are based on. The tool is **Locust** (`loadtest/locustfile.py`; `pip install -r requirements-loadtest.txt`, kept out of the pinned runtime packages), and `loadtest/summarise.py` turns its results into a table and checks them against budgets.

**Status: run locally and against the live function's `/health`; the keyed mix has not been run against the live function.** Read "What was and was not measured" before quoting a number.

## Budgets

A run passes if, for the whole run: the 95th-percentile response time is at most **500 ms** and at most **1%** of requests fail. A cold start (a new copy of the function starting) is expected to take a few seconds and is not held to the 500 ms budget.

## What the test does

`loadtest/locustfile.py` has two modes (environment variable `LOAD_MODE`):

- `mix` (default): a realistic mix of every endpoint (status, share, segments, three forecast views, model results, findings, the model list, and the live predictions and alerts for random segments and horizons), with the key in `LOAD_API_KEY`.
- `health`: only `/health`, which needs no key and is never rate-limited.

Safety: any target that is not this machine is refused unless `LOAD_ALLOW_REMOTE=yes`; the key is read from the environment, sent only in the `X-API-Key` header and never printed or written (Locust's files hold names and timings only; a test checks the locustfile has no `print`).

## Results on this machine, before and after the cache

The real API app (one uvicorn process), a throwaway key, the rate limit raised so it does not distort the measure, Locust on the same laptop, three stages of 45 seconds (5, 20 and 50 concurrent users), the `mix` mode. Zero failures in every run.

| Users | Version | Requests/s | Median | 95th pct | 99th pct |
|---|---|---|---|---|---|
| 5 | before | 23.5 | 27 ms | 78 ms | 130 ms |
| 5 | after | 24.4 | 15 ms | 84 ms | 150 ms |
| 20 | before | 47.2 | 150 ms | **770 ms** (over budget) | 1,100 ms |
| 20 | after | **91.8** | 21 ms | **110 ms** | 210 ms |
| 50 | before | 27.8 | 1,300 ms | **3,500 ms** (over budget) | 4,800 ms |
| 50 | after | **193.1** | 51 ms | **190 ms** | 240 ms |

**What the "before" run found.** One process saturated at about 45 requests a second with 20 users, and at 50 users throughput fell (to 28) while the 95th percentile reached 3.5 seconds: CPU contention, because every request recomputed its answer. In-process, `/health` rebuilt the status block on every call (about 9 ms), `/v1/model-results` took about 32 ms, `/v1/forecasts/national` about 11 ms. The answers cannot change until the service switches to a new release.

**The fix (DL-127).** A small cache (`api/cache.py`), used by every data endpoint, `/health`, the model list and the predictions: bounded to 512 entries (oldest dropped first, so a client sending many different requests cannot make it grow), private copies in and out, never an error, thread-safe, and keyed with a *generation* that rises whenever the service switches to a new release, so an answer from old data is never returned for new data. In-process, a cached answer costs about 2 to 3 ms including the test client's own overhead (`/v1/model-results` 31.6 ms to 2.4 ms). Predictions are cached per segment and horizon (about 340 combinations), so they still compute on first use: that is why `/v1/predict/segment` is the slowest endpoint at 5 users.

**How the "after" numbers nearly went wrong.** The first "after" run was against a server that had not been restarted (the new process failed to bind its port and the old one kept answering), so it measured the old code. The in-process timings disagreed with it, which exposed the mistake; the numbers above are from a rerun on a server confirmed to be the new process (its first call took 2.6 s, the next three 11 to 12 ms).

## The live function, unauthenticated

`/health` of the deployed function, 5 users for 60 seconds, run from this machine over the internet: **1,574 requests, 0 failures, median 43 ms, 95th percentile 51 ms, 99th 300 ms, 27 requests/s**; the fastest was 36 ms (mostly the network round trip) and the slowest **3,350 ms**, which is a new copy of the function starting (5 users make the function scale out to up to 5 copies). So a cold start costs about **3.3 seconds** and warm requests about a few milliseconds of server time.

This measured the *deployed* version, which does not yet have the cache; the cache reaches AWS with the next deploy.

## What the numbers say about the limits

- **Cost.** The free allowance is 1,000,000 requests a month. This test used 1,574 (0.16%). A sustained 27 requests a second would use the whole month's allowance in about 10 hours, so the protection against abuse is not capacity, it is the per-key rate limit, the free plan's ceiling and the $5 budget alert.
- **Rate limit.** The default of 60 requests a minute per key per running copy is a policy, not a capacity limit: one copy answers well over 100 requests a second once warm, so 60 a minute is about 0.5% of what a copy can do. It stays at 60. Because each copy counts separately, with N copies running the most one key can make is N times 60 a minute.
- **Concurrency.** A fresh function scales to as many copies as there are concurrent requests (5 users made up to 5). The account's concurrency ceiling has not been read, so no reservation is set; set `ReservedConcurrency` only if the Lambda dashboard shows the account allows it (see `deployment.md`).
- **Timeouts.** A cold start of about 3.3 seconds sits well inside the website panel's 15-second wait, the canary's 6 tries of 10 seconds and the deploy workflow's 12 tries of 5 seconds; the function's own timeout is 30 seconds.

## What was and was not measured

- **Measured:** the application's throughput and latency on one laptop, before and after the cache; the live function's `/health` warm latency, cold start and scale-out over the internet; zero failures throughout.
- **Not measured:** the keyed endpoints on the live function (that needs your key and a temporary rate-limit raise, below); the predictions' cost on Lambda's CPU (Lambda gives about 0.6 of a CPU at 1,024 MB, so each request there may take longer than on a laptop); other memory sizes (Lambda power tuning); a long soak run; the rate limiter's behaviour across several copies.
- **Caveats:** Locust shared the laptop with the server, so the local figures are a floor on capacity; each stage ran once (single runs vary by several percent); the prediction cache is cold at the start of a run.

## How to run it

**On this machine.** Start the API with a throwaway key and a raised rate limit, confirm that *your* process owns the port, then run the stages:

```bash
API_KEYS=local-load-key API_RATE_PER_MINUTE=1000000 PYTHONPATH=src \
  python -m uvicorn oa_market_intelligence.api.main:create_app --factory --port 8010 --no-access-log
LOAD_API_KEY=local-load-key locust -f loadtest/locustfile.py --headless -u 20 -r 10 -t 45s \
  --csv loadtest/results/local_u20 --host http://127.0.0.1:8010
python loadtest/summarise.py loadtest/results/local_u20
```

**Live `/health`, no key** (small and safe):

```bash
LOAD_MODE=health LOAD_ALLOW_REMOTE=yes locust -f loadtest/locustfile.py --headless -u 5 -r 5 -t 60s \
  --csv loadtest/results/live_health --host https://<the function address>
```

**Live, keyed mix (optional; you run it, not me).** The default limit of 60 a minute would answer most of a load test with `429`, so raise it for the test and put it back:
1. In CloudFormation, update the stack `oa-market-api` and set **RatePerMinute** to `100000` (keep **Use previous value** on **ApiKeys**; use **Replace existing template** only if the template changed).
2. In your own PowerShell window, type the key without echo and run a short test, for example 5 users for 60 seconds:

```powershell
$secure = Read-Host "API key" -AsSecureString
$env:LOAD_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
$env:LOAD_ALLOW_REMOTE = "yes"
locust -f loadtest/locustfile.py --headless -u 5 -r 5 -t 60s --csv loadtest/results/live_mix --host https://<the function address>
python loadtest/summarise.py loadtest/results/live_mix
```

3. Set **RatePerMinute** back to `60`. A 60-second run of 5 users makes a few thousand requests, a fraction of a percent of the free allowance.

Do not paste the key anywhere, and do not commit anything from `loadtest/results/` (it is git-ignored).

## Tested

`tests/test_api_cache.py` (13: the cache itself, and in the API: hits return the same body, case and spaces share one entry, errors are never kept, the size is bounded under 700 different requests, nothing from old data after a switch, one cache per app), `tests/test_loadtest.py` (7: the table, the budgets on both sides of their edges, the command's exit status), and the API suites, including those where the service switches to a new release, all pass with the cache.
