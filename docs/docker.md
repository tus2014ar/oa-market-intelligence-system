# Docker (DL-116)

Four images, one per kind of work (the fourth, `Dockerfile.lambda`, is the API for AWS Lambda: see [`deployment.md`](deployment.md), "The AWS version"). None of them holds a secret: keys and limits are passed when a container starts.

| Image | File | What it runs | Port |
|---|---|---|---|
| `oa-market-intelligence` | `Dockerfile` | the monthly pipeline and the modelling commands (a job, not a service); the data is mounted, not baked in | none |
| `oa-site` | `Dockerfile.site` | the website (Streamlit), with the published data inside the image | 8501 |
| `oa-api` | `Dockerfile.api` | the read-only API (FastAPI under uvicorn), with the published data inside the image | 8000 |

The site and API images each install only their own small requirements file (`app/requirements.txt`, `api/requirements.txt`), run as an unprivileged user (uid 10001), have a health check, and take the published data (about 15 MB) and `data/reference/`. `.dockerignore` keeps the 23 GB of raw and local data, notebooks, documents and any `secrets.toml` or `.env` file out of the build.

## The whole stack on one machine

```bash
docker compose up --build
```

- the website: http://localhost:8501
- the API: http://localhost:8000 (documentation at `/docs`)

Settings come from your shell and are never stored in `docker-compose.yml`: `ANTHROPIC_API_KEY`, `ACCESS_CODE`, `DAILY_TOKEN_BUDGET`, `CLAUDE_MODEL` for the AI Question Box, and `API_KEYS`, `API_RATE_PER_MINUTE` for the API. For example, in PowerShell:

```bash
$env:API_KEYS = "choose-a-key"; docker compose up --build
```

The monthly pipeline is a job: `docker compose run --rm pipeline` (it mounts `./data`).

## One image at a time

```bash
docker build -f Dockerfile.site -t oa-site .
docker run --rm -p 8501:8501 oa-site
```

```bash
docker build -f Dockerfile.api -t oa-api .
docker run --rm -p 8000:8000 -e API_KEYS=choose-a-key oa-api
```

## Checked on a machine before it went to CI (6 Nov 2026, Docker 29.9.2)

All three images built and ran. The API container answered `/health` (`api-key`), refused `/v1/status` without the key (`401`), returned data with it, served `/docs`, ran as uid 10001 and reported `healthy`; its log held one JSON line per request and no key. The website container answered its health check with `ok`, served the Overview in a browser (title, four cards, chart, plain-words box) and held only the theme file and 16 MB of published data. `docker compose up --build` started both with one command. The pipeline image still built and ran its commands after the `.dockerignore` change. Sizes: pipeline 1.53 GB (the full modelling stack), website 1.03 GB, API 523 MB.

## What CI checks (job `docker-build`)

It builds all three images and runs them: the pipeline image's commands; the API image with a key (`/health` says `api-key`, `/v1/status` is `401` without the key and returns data with it, and the process is not root); the website image (its health check answers `ok`, and the process is not root).

## What this is, and is not

- This makes the project reproducible on any machine with Docker and gives CI a real check of every way it runs.
- The live website does **not** run from these images: it runs on Streamlit Community Cloud from the source. Running the images in the cloud (AWS) is planned and not done, and nothing here says it is deployed.
- Not done yet: scanning the images for known vulnerabilities, pushing them to a registry, and pinning the base image by digest.

## The Lambda image (DL-118)

`Dockerfile.lambda` builds the same API on AWS's Lambda base image (`public.ecr.aws/lambda/python:3.12`) with the Mangum adapter, and runs `oa_market_intelligence.api.lambda_handler.handler`. Checked on a machine through the runtime emulator with a read-only filesystem; CI (`docker-build`) builds it and calls it with a function-URL event (health, `401` without a key, the model list with one). The image is about 1.1 GB uncompressed. It is not pushed anywhere or scanned yet except by the registry's scan on push once deployed; the base image is not pinned by digest.
