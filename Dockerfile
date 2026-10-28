# The project image: Python 3.12 (the supported baseline, the same as CI) with the pinned packages
# and the code. The data is not baked in: mount the repo's data/ folder when running, e.g.
#
#   docker build -t oa-market-intelligence .
#   docker run --rm -v "$PWD/data:/app/data" oa-market-intelligence oa-publish
#   docker run --rm -v "$PWD/data:/app/data" oa-market-intelligence oa-model --counts-only
#
# The package is installed in editable mode so that the code finds data/ beside it (/app/data).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# The pinned packages first, so this layer is rebuilt only when requirements.txt changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps -e .

COPY tests ./tests

CMD ["oa-publish", "--help"]
