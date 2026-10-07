# Deploying the public site

Goal (DL-34, DL-35): a public website on the internet, on free hosting, reachable at your Cloudflare domain, with the Claude question box working. Secrets are typed into the hosting dashboard, never into the repository or into chat.

## How it fits together

```
data/raw/*.xlsx ──► python -m oa_market_intelligence.publish ──► data/published/warehouse.db
   (committed)        (staging build, checks, model stage,          (tables + model panel in one
                       atomic swap, run log)                          file, committed to main)
                                                                          │
                                       push to main redeploys ◄───────────┘
                                                  │
                                    app/streamlit_app.py (Streamlit Community Cloud)
                                                  │
                          visitors ─► tables, charts, model panel, Ask Claude box
```

The monthly workflow (`.github/workflows/monthly_pipeline.yml`) runs the publish step on the 10th of each month, or on demand from the Actions tab. A failed run turns red, commits nothing, and the site keeps serving the last good file.

## One-time setup

### 1. Publish the first database

Either run it locally and commit the result:

```bash
PYTHONPATH=src python -m oa_market_intelligence.publish
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

- Open the site in a private window: every tab loads; the Model results tab shows the verdict without waiting.
- Ask the three example questions. Check each answer quotes numbers that match the tables.
- Ask something the tools cannot answer ("what was Zilretta's revenue?") and confirm the box says it cannot.
- With an access code set, confirm a wrong code is refused.
- Confirm no secret appears anywhere in the page, the repository, or the run log.
