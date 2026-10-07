# Deploying the public site

Goal (DL-34, DL-35): a public website on the internet, on free hosting, reachable at your Cloudflare domain, with the Claude question box working. Secrets are typed into the hosting dashboard, never into the repository or into chat.

## How it fits together

```
data/raw/*.xlsx ──► python -m oa_market_intelligence.publish ──► data/published/warehouse.db
   (committed)        (staging build, checks, Phase 4 results,      (tables + model panel + stored
                       atomic swap, run log)                          results in one file, on main)
                                                                          │
                                       push to main redeploys ◄───────────┘
                                                  │
                                    app/streamlit_app.py (Streamlit Community Cloud)
                                                  │
                          visitors ─► trend, specialties, forecast & monitoring, model results, Ask Claude
```

The monthly workflow (`.github/workflows/monthly_pipeline.yml`) runs the publish step on the 10th of each month, or on demand from the Actions tab. A failed run turns red, commits nothing, and the site keeps serving the last good file.

**What the publish step computes (DL-58).** After rebuilding and checking the tables it computes, once, every number the site and the Claude tools show: the trend and change-point analysis, the mix-versus-rate decomposition, adjusted specialty shares and robustness checks, the segment-share model, the next-month forecast with ranges, the direction panel with its power table, and the monitoring status. They are stored in the same database file (tables `serving_model_panel` and `serving_results`), so one file is one consistent version of the site and nothing is fitted while a visitor waits. If any stage fails, the run fails whole and the last good file stays live. With `--precision full` (the default, used by the monthly workflow) the draws match the notebooks and a run takes roughly 40 minutes of model fitting; `--precision fast` uses small draws for quick checks and is recorded inside the stored results (a full build is what gets committed to `main`).

## One-time setup

### 1. Publish the first database

Either run it locally and commit the result:

```bash
PYTHONPATH=src python -m oa_market_intelligence.publish            # full precision, about 40 minutes
PYTHONPATH=src python -m oa_market_intelligence.publish --precision fast --published-dir scratch/  # quick check, not for committing
```

then commit `data/published/`, or merge the branch and run the **Monthly Pipeline** workflow once from the Actions tab (Run workflow). Check `data/published/run_log.jsonl` says `"status": "ok"`.

### 2. Create the Claude API key with a spend limit

In the Anthropic console create an API key for this project and set a **monthly spend limit** on it before the site goes public. The in-app limits (per visitor, per site, daily tokens) are a second line of defence only.

### 3. Create the app on Streamlit Community Cloud

1. Sign in at share.streamlit.io with GitHub and choose **Create app**.
2. Repository `tus2014ar/oa-market-intelligence-system`, branch `main`, main file `app/streamlit_app.py`. Python 3.12 if asked. (It installs `app/requirements.txt`, the small list for the site, not the notebook-heavy root `requirements.txt`.)
3. Under **Advanced settings → Secrets**, paste (with your own values):

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
# optional
ACCESS_CODE = "choose-a-code"      # leave out to keep the box open to everyone
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
DAILY_TOKEN_BUDGET = "300000"
```

4. Deploy. You get a `https://<name>.streamlit.app` address. Free apps sleep when idle and wake in a few seconds.

### 4. Point your Cloudflare domain at it

Streamlit's free tier does not serve custom domains directly, so version 1 uses a redirect:

1. In Cloudflare DNS add a proxied record for the subdomain you want, for example `oa` (type `AAAA`, value `100::`, proxy on). The address is a placeholder; the redirect rule answers before it is used.
2. **Rules → Redirect Rules → Create rule**: when hostname equals `oa.<yourdomain>`, redirect (dynamic or static) to `https://<name>.streamlit.app`, status 302.

Visitors typing your domain land on the app; the address bar then shows the `streamlit.app` address. A true custom domain with no redirect needs a paid or self-run host (for example Render with the domain on it), which can come later.

## Checks before sharing the link

- Open the site in a private window: every tab loads without waiting (Overview, Market trend, Segments, Forecast & monitoring, Model results); the Market trend tab shows the change around March 2022, and the Segments tab shows Physical Medicine & Rehab with the highest adjusted share (5.34 percent in the notebooks).
- Check the stored results say `"precision": "full"` (not `"fast"`) in `serving_results`, and that the Forecast & monitoring tab shows the same status as the run log's `monitoring_status`.
- Ask the four example questions. Check each answer quotes numbers that match the tables, says the forecast is a reference range and not a prediction of change, and never gives a cause for the share moving.
- Ask something the tools cannot answer ("what was Zilretta's revenue?") and confirm the box says it cannot.
- With an access code set, confirm a wrong code is refused.
- Confirm no secret appears anywhere in the page, the repository, or the run log.
