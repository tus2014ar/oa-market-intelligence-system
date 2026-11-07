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

The original rule was **no credit card anywhere except Anthropic**, because most cloud accounts that take a card have no hard spending cap. On 6 Nov 2026 you chose to add AWS, on its **Free plan** (opened 6 Nov 2026; the welcome email says it ends on 6 May 2027 or when the credits are used, and that no charges apply within the free-plan limits unless you upgrade). The site and the Streamlit host are unchanged. See "The AWS version" below.

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

## The AWS version (DL-118)

**Status: live on AWS Lambda since 6 Nov 2026** (region us-east-1, account on the AWS Free plan). The address is deliberately not published in this repository (decision of 6 Nov 2026): `/health` and `/docs` answer anyone who has the address, and every data call needs an API key. It is shown in the CloudFormation stack's Outputs. The Streamlit site stays where it is and does not use this.

**What it is.** The API (`docs/api.md`) as an AWS Lambda container function with a public function URL. The URL needs no AWS sign-in; the API's own `X-API-Key` check protects it. The files: `Dockerfile.lambda` (the image), `src/oa_market_intelligence/api/lambda_handler.py` (the Mangum adapter), `infra/aws/bootstrap.yml` (image repository and the GitHub deploy role), `infra/aws/api.yml` (the function, its URL and its logs) and `.github/workflows/deploy-aws.yml` (a manual workflow, main branch only).

**What happened on 6 Nov 2026.** The base stack `oa-market-api-bootstrap` was created in the account (image repository, GitHub sign-in link, deploy role). The first run of `deploy-aws` then **failed at the AWS sign-in step** ("Not authorized to perform sts:AssumeRoleWithWebIdentity"): the role trusted GitHub's token by the repository's plain name, but GitHub names this repository by immutable IDs (`use_immutable_subject: true`). The template was changed to trust the ID form (parameter `GitHubSubjectPrefix`, DL-119), the stack was updated, and the re-run signed in, built the image and pushed it (one image, 260 MB compressed, tagged with the commit). The function stack `oa-market-api` was then created from that image (`CREATE_COMPLETE`).

**Checked on the live function (6 Nov 2026).** Without a key, from the internet: `/health` returned `200` (`"auth": "api-key"`, data through Jul 2025, 72 months), `/docs` returned `200`, `/v1/models` returned `401`, a `POST` returned `405`. With the key, in the interactive docs: `/v1/models` listed the four saved models with the same version fingerprints and known biases as the stored forecasts, `/v1/predict/segments` listed 113 segments, and `/v1/predict/segment` for rheumatology, 65 to 74, female, 3 months returned a forecast of 1.7326% with `matches_stored: true` (the stored forecast is 1.7326%). **Not checked on the live function:** the other segments and the alert endpoint (they are covered by the tests and the emulator run below), behaviour under load, the time of a cold start.

**What was checked before that.** The image was built and called on a machine exactly as Lambda calls it (the Lambda runtime emulator, a read-only filesystem, function-URL events): health, the model list, a live prediction equal to the stored forecast, an alert, `401` for a missing or wrong key, `404`, `405` and the docs all answered correctly, and the logs hold no key. Both templates pass `cfn-lint`. Ten tests (`tests/test_aws_infra.py`) pin down the safety properties; CI builds and calls the Lambda image too. **Not checked:** the templates have not been run in an AWS account, the workflow has not run, and the free-plan terms for the image repository and function URLs were not read from AWS's own pages.

**Safety design.** No access key exists anywhere: GitHub signs in with a short-lived token (OIDC) and the role accepts it only from the `main` branch of this repository. The role can push to one repository and update one function, nothing else (no IAM, no CloudFormation). The function runs as a role that can only write its own logs. The API key is typed by you into a hidden CloudFormation parameter and is stored on the function; it is not in the repository, the image or the workflow. Images are scanned on push, tags cannot be overwritten, and only the last 3 images are kept. Logs expire after 14 days. The workflow runs only when you start it.

**Set-up, once** (sign in as `tushar-admin`, region `us-east-1`; the repository's files are on your computer):

1. **The base stack.** In the console, open **CloudFormation**, choose **Create stack**, **With new resources**, **Upload a template file**, and pick `infra/aws/bootstrap.yml`. Name it `oa-market-api-bootstrap`, keep the defaults, and on the last page tick the box that says CloudFormation might create IAM resources with custom names. Submit. When the status is `CREATE_COMPLETE`, open **Outputs** and copy `DeployRoleArn`.
2. **Two GitHub variables** (not secrets): in the repository, **Settings**, **Secrets and variables**, **Actions**, **Variables**, add `AWS_REGION` = `us-east-1` and `AWS_DEPLOY_ROLE_ARN` = the role ARN.
3. **Push the first image.** Merge the branch to `main` first (the workflow and the role both require `main`). Then **Actions**, **deploy-aws**, **Run workflow**, branch `main`. It builds the image and pushes it; the run summary prints the image address and says the function does not exist yet.
4. **The function stack.** Back in CloudFormation, **Create stack**, upload `infra/aws/api.yml`, name it `oa-market-api`. Paste the image address as `ImageUri`. For `ApiKeys`, type a long random value of your own (at least 16 characters; save it in your password manager) and do not paste it anywhere else. Leave the rest, tick the IAM acknowledgement and submit. Under **Outputs**, `ApiUrl` is the public address.
5. **Check it.** Open `ApiUrl` + `health` in a browser (it answers without a key), then `ApiUrl` + `docs`. A request to `/v1/models` without the key must say `401`; with the header `X-API-Key: <your key>` it must list four models.
6. **Account limits.** In **Lambda**, **Dashboard**, read the account's concurrent-execution limit. If it is above 10, you may set `ReservedConcurrency` on the stack to cap how many copies run at once; if it is 10 or less, leave it at 0 (AWS does not allow a reservation then).

**If the sign-in step fails with "Not authorized to perform sts:AssumeRoleWithWebIdentity"** the role does not recognise GitHub's token. Read how GitHub names this repository with `gh api repos/OWNER/REPO/actions/oidc/customization/sub` (the answer holds `sub_claim_prefix`, for example `repo:tus2014ar@30377559/oa-market-intelligence-system@1373160617`; these IDs are not secrets) and make `GitHubSubjectPrefix` in `bootstrap.yml` equal to it. To apply a changed `bootstrap.yml`: in CloudFormation open the stack `oa-market-api-bootstrap`, choose **Update stack**, **Make a direct update**, **Replace existing template**, **Upload a template file**, pick the file, then **Next** through to **Submit** (tick the IAM acknowledgement if it asks). Wait for `UPDATE_COMPLETE`, then run the workflow again.

**Updating later, and the loop (DL-121).** The deploy workflow (`deploy-aws`) now starts three ways, only from `main`:
1. **After the Monthly Pipeline finishes successfully.** It reads the data version in the repository, asks the live API for its own (`/health`), and stops without building anything if they are the same. If they differ, it builds, pushes, switches the function and checks again.
2. **By hand** (Actions, deploy-aws, Run workflow): builds and deploys this commit, for a code change.
3. **By hand with `rollback_to`**: the full commit SHA of an earlier deploy. It checks that the image is still in the registry (the last 3 are kept), switches the function to it and does no build. A rollback is not held to the repository's data version; the summary shows which version is live.

Every deploy ends by calling `/health` through the function's address: the API must answer, and (except for a rollback) must serve the data version in the repository, or the run fails and GitHub emails you. The function's address is masked in the logs and never printed. The monthly job pushes with GitHub's built-in token, and GitHub does not start workflows from such a push, which is why the deploy follows the pipeline's completion instead of a push to `main`. This path is built and tested for its logic and its permissions; it has **not yet run** on a real publish (the first one will show whether AWS accepts the sign-in from a pipeline-triggered run), and no new IQVIA extract is expected, so the data path is exercised by a manual re-run of the Monthly Pipeline, not by new data.

**The daily canary** (`.github/workflows/canary.yml`). Once a day, and on demand, it calls `/health` (the API must answer ok and still ask for a key), calls `/v1/models` without a key (must be `401`) and compares the live data version with the repository's. A failure turns the run red, which GitHub emails. One-time set-up: in the repository, **Settings**, **Secrets and variables**, **Actions**, **Secrets**, **New repository secret**, name `API_URL`, value the function's address (the `ApiUrl` output of the stack). It is a secret so that GitHub masks it in the public logs; until it is set, the canary fails and says so. GitHub emails a failed scheduled run to the person who last changed its schedule, so keep Actions email notifications on. It does not check the Streamlit site, which can sleep when idle.

**Monitoring** (`infra/aws/monitoring.yml`, the stack `oa-market-api-monitoring`). It watches the function with five alarms that email you through one alert topic: the function crashing, throttling, the API answering a 5xx (Lambda's own error count misses these because the API catches them, so they are counted from the access log), slow runs (95th percentile over `SlowMilliseconds` for 15 minutes), and many rejected keys in five minutes (someone guessing). A quiet function is not an alarm. It also makes a dashboard, `oa-market-api`, under CloudWatch, Dashboards (requests and outcomes, run-time percentiles, crashes and throttles, copies running at once). Set-up: CloudFormation, **Create stack**, upload `infra/aws/monitoring.yml`, name `oa-market-api-monitoring`, type your own email in **AlertEmail** (it is not stored in the repository), submit, then **open the email AWS sends and confirm the subscription**, or no alert arrives. To test the path, open **SNS**, **Topics**, choose the topic named in the stack's Outputs (`AlertTopicArn`), choose **Publish message**, send a short test message, and check that the email arrives. **Not verified:** the stack has not been created in the account; the log-metric patterns read the API's JSON access line and were checked against its format, not against a live CloudWatch; whether CloudWatch alarms, metrics and SNS email are within your free plan was not read from AWS's pages.

**Taking it down.** Delete the stack `oa-market-api` first, then empty the image repository (images, not the repository) and delete `oa-market-api-bootstrap`. The free plan ends on 6 May 2027 or when the credits are used, so the live AWS address is temporary unless you move to a paid plan; the site, the docs and the screenshots do not depend on it.

**Limits to know.** The rate limit counts requests in memory for each running copy, so on Lambda it is per copy and approximate. The cost protection is the budget alert, the optional concurrency limit and the free plan's own ceiling, not that counter. After a period of idleness the first request starts a new copy that loads the warehouse; that start-up time was not measured. The image is built for x86_64 and is not pinned to a base-image digest.

**Try the Lambda image on your machine** (Docker running):

```bash
docker build -f Dockerfile.lambda -t oa-api-lambda .
docker run --rm --read-only --tmpfs /tmp -p 9000:8080 -e API_KEYS=choose-a-key oa-api-lambda
```

then send it a function-URL event, for example (this prints `"statusCode": 200`):

```bash
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{"version":"2.0","routeKey":"$default","rawPath":"/health","rawQueryString":"","headers":{},"requestContext":{"http":{"method":"GET","path":"/health","sourceIp":"203.0.113.9"}},"isBase64Encoded":false}'
```

## Switching the live panel on (DL-122)

The Forecasts page has a panel that asks the AWS API to run the model (`site.md`, "The live model panel"). It stays switched off until the site's Secrets hold the API's address and a key. Use a **separate key for the website**, so either side can be changed without touching the other.

1. **Add a second key to the API.** In CloudFormation open the stack `oa-market-api`, choose **Update stack**, **Make a direct update**, **Use existing template**, then in **ApiKeys** type your current key, a comma, and a new random key of at least 32 characters, with no spaces (`current-key,website-key`). Save the website key in your password manager. Submit and wait for `UPDATE_COMPLETE`. Both keys work from then on.
2. **Give the site its two secrets.** In Streamlit Community Cloud open the app, **Settings**, **Secrets**, and add two lines (the address is the stack's `ApiUrl` output; type the values yourself, never into a chat or a file in the repository): `LIVE_API_URL = "https://…/"` and `LIVE_API_KEY = "the website key"`. Save; the app restarts.
3. **Check.** Open the Forecasts page, find "Run the model live", press **Run the model**. It should show the live forecast and "Same as the forecast above". If it says the service did not answer, check the address; if it says the key was refused, check the key.
4. **Switching it off, or changing the key.** Delete the two secrets to switch the panel off. To replace the website key, change it in both places (the stack's **ApiKeys** and the Secrets); removing the old key from **ApiKeys** stops it working.

Until step 2 is done the live site shows "The live model check is switched off on this copy of the site." The panel calls the API only when a visitor presses the button; the API's own rate limit counts the website as one client (its key).

## What this does not do

- It does not give an uptime guarantee, a staging copy, infrastructure as code, a model registry or automated alerts. Those are listed as optional later work in the decision log (DL-113).
- It does not host Datasette or the stakeholder database. They stay local, for stakeholders only: they hold far more than the site shows.
- The free-tier terms of the host (memory limit, how long an idle app sleeps, the number of apps) were not re-checked from the host's own page when this was written. Check them when you create the app.
