# Deployment (DL-113)

**Status: live, at https://oa-market-intelligence-system-gc4jn4n6qqdf8mnaliabff.streamlit.app** (Streamlit Community Cloud, free tier, public, from the `main` branch). It was checked page by page from the public address in a browser that is not signed in to anything, on 6 Nov 2026 (step 5 below): all 11 pages loaded without an error, the data-age banner read "Data to Jul 2025", a chart click on Segments opened Segment detail for the group with a way back, and the AI Question Box showed its switched-off view with a disabled chat box and no key text. **The AI Question Box is off** until you add the key (see "Turning the box on"). This page is the plan and the go-live steps.

## What is deployed, and where

| Part | Where | Cost |
|---|---|---|
| **The website** (11 pages) | Streamlit Community Cloud, from this public GitHub repo, branch `main` | free, no card |
| **The data** (`data/published/warehouse.db`, `external_subset.db`, the files in `data/reference/`) | in the repo; the site reads them from its own copy | free |
| **The forecasts** | the monthly GitHub Actions job writes them into `warehouse.db` (the `pred_batch` table); there is no model server | free (public repo) |
| **The AI Question Box** | the host's secrets hold the Anthropic key; Claude runs at Anthropic | the only paid part, capped by you |
| **Your own domain** (optional) | a Cloudflare redirect to the app's address | free |

The rule for the project: **no credit card anywhere except Anthropic.** Every cloud account that takes a card has no hard spending cap, so none is used.

## Decisions

- Public: anyone with the link can open the site, with no sign-in. Public display of aggregate IQVIA-derived tables was approved (DL-30) and the stakeholder approved the public-display note.
- One production app from `main`. No staging app for now: every change already passes CI before it reaches `main`.
- **The AI Question Box starts switched off** and is switched on after the key test (see "Turning the box on").
- The token limits are yours to set (see "Limits").

## What has been checked before going live

- The repo is public and its default branch is `main`.
- The site reads only committed files: the two databases (about 15 MB together) and `data/reference/`.
- No secret is in the repo or its history (the only match for a key pattern is the placeholder `sk-ant-...` in an old setup example). `.streamlit/secrets.toml` is git-ignored.
- **A clean Python 3.13 environment with only `app/requirements.txt` installed** runs all 11 pages with no error, and serves a healthy built-in health check (`/_stcore/health` returns `ok`). Re-run it any time with `scripts/check_host_env.py` (how is in its first lines).
- CI runs the site's tests on Python 3.12 on every change.

## Go-live steps

Steps 1 to 4 are yours: I never create accounts or enter credentials.

1. **Create the host account.** Go to Streamlit Community Cloud (share.streamlit.io) and sign in with your GitHub account. It is free and asks for no card. Allow it to read your repositories.
2. **Create the app.** New app, then:
   - Repository: `tus2014ar/oa-market-intelligence-system`
   - Branch: `main`
   - Main file path: `app/streamlit_app.py`
   - Advanced settings, Python version: 3.13 (the version checked clean above; 3.12 is what CI uses and also works)
   - App URL: choose a short name
   - Secrets: leave empty for now, so the AI Question Box stays off
3. **Deploy and wait.** The first build installs `app/requirements.txt` and takes a few minutes. If the build fails, open the log from the app's menu and send me the last lines.
4. **Make sure it is public.** In the app's sharing settings, it must be open to everyone, not restricted to named viewers.
5. **Check it from the public address** (I can do this with you): open every page, in this order: Overview, Market trend, Segments, Segment detail, Forecasts, Model results, Data status, Findings, Context, Market map, AI Question Box. Check that each loads without an error, that the data-age banner reads "Data to Jul 2025", that the colours and navigation on the right look as on your computer, that one chart click and one table click work, and that the AI Question Box shows "not switched on" with its preview. **Only after this check is the site "deployed".**

## After launch

- **First visit is slow.** The free host puts an idle app to sleep, and the next visitor wakes it, which takes some seconds.
- **No uptime guarantee.** The free tier has no service-level promise. Treat the target as best effort.
- **Your domain.** Streamlit Community Cloud, as far as I know, has no custom-domain setting. The plan is a Cloudflare redirect rule from your domain to the app's address; verify this when you set it up.
- **Uptime and alerts.** A free uptime monitor on the app's address, and GitHub's own email when the monthly workflow fails. Both are small additions after launch.
- **Updates.** Merging to `main` redeploys the site. The monthly workflow commits a new `warehouse.db` to `main`, which redeploys it too.
- **Rolling back.** Revert the commit on `main` (`git revert`). The host redeploys the earlier version. The monthly publish keeps the last good database if a run fails.

## Turning the AI Question Box on

1. **Run the key test yourself** with your own key, in your own terminal (the steps are in `docs/site.md`, section "Trying it locally", and `oa-ask-check`). Grade the answers, and I will report the tokens each question used.
2. **Set the limits**, both of them:
   - In the Anthropic console, an account spend limit. This is the hard stop.
   - In the host's secrets, `DAILY_TOKEN_BUDGET`. When it is used up the box says so until the next day; the rest of the site keeps working. It lives in memory, so it resets when the app restarts, which is why the account limit is the real guarantee.
3. **Add the secrets.** In the app's settings, Secrets, paste (with your own values; never put these in the repo or in a chat):

   ```toml
   ANTHROPIC_API_KEY = "your key"
   DAILY_TOKEN_BUDGET = "300000"
   # ACCESS_CODE = "a shared code"   # optional: only people with the code can ask
   # CLAUDE_MODEL = "claude-haiku-5-5"   # optional: the default
   ```

4. **Check.** The app restarts, the "not switched on" notice is gone and the chat box is enabled. Ask one question and check its sources and figures.
5. **Switching it off again:** delete `ANTHROPIC_API_KEY` from the secrets.

Each visitor can ask 8 questions an hour and the whole site 200 an hour; questions are capped at 500 characters and nothing is saved from them. If strangers use up the day's budget too early, add an `ACCESS_CODE`.

## What this does not do

- It does not give an uptime guarantee, a staging copy, infrastructure as code, a model registry or automated alerts. Those are listed as optional later work in the decision log (DL-113).
- It does not host Datasette or the stakeholder database. They stay local, for stakeholders only: they hold far more than the site shows.
- The free-tier terms of the host (memory limit, how long an idle app sleeps, the number of apps) were not re-checked from the host's own page when this was written. Check them when you create the app.
