"""The API on AWS Lambda (DL-118): the same app, behind a Lambda function URL.

Mangum turns a function-URL request into the ASGI call FastAPI expects and the response back, so
nothing in the API changes. The app is built once per cold start (it loads the snapshot and the
saved models from the read-only warehouse in the image) and reused by every request after.

Keys and the rate come from the function's environment (`API_KEYS`, `API_RATE_PER_MINUTE`), never
from the image. The rate count is kept in memory by each running copy, so on Lambda it is per copy
and approximate; the real protection against cost is the budget alert, the concurrency limit and
the account's free-plan ceiling (docs/deployment.md).
"""

from mangum import Mangum

from oa_market_intelligence.api.main import create_app

handler = Mangum(create_app(), lifespan="auto")
